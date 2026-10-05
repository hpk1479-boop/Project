"""Streaming workers, deterministic commands, monthly overlap, disk-backed alerts."""
from pathlib import Path
import concurrent.futures
import csv
import datetime as dt
import hashlib
import json
import logging
import os
import sys
import time
import uuid
from .settings import PROGRAM,digest,file_hash,milliseconds,months,work_periods,overlap_start,warehouse_path,relative_path
from .system import deny_network,physical_cores,peak_memory,process_memory,write_progress,current_processor
from .warehouse import Warehouse,ResultWriter,FIELDS

def code_hash():
    root=PROGRAM.parents[1]
    paths=list(PROGRAM.rglob('*.py'))+list((root/'Part2/event_backtest').glob('*.py'))+[root/'Part2/generic_backtest/native_mt5.py']
    return digest({str(p.relative_to(root)):file_hash(p) for p in sorted(paths) if '__pycache__' not in p.parts})

def resolve_captures(catalog,s,*,ea_build_hash=None):
    mode='BAR' if s['mode']=='BAR' else 'TIMER'
    end=s['end']
    candidates=catalog.available(s['symbol'],mode)
    from .build_plan import current_build,schema_id
    ea_build_hash=ea_build_hash or current_build(catalog.root)
    candidates=[c for c in candidates if c['schema_id']==schema_id() and c.get('reconstruction_verified') and not c.get('history_missing') and int(c.get('timer_ms',1000))==int(s['timer_ms'])
                and (not ea_build_hash or c['ea_build_hash']==ea_build_hash)]
    from .calendar import warm_start
    try:start=warm_start(s['start'],s['overlap_trading_days'],candidates)
    except ValueError:start=overlap_start(s['start'],s['overlap_trading_days'])
    chosen={}
    for c in candidates:
        if c['end']<=start or c['start']>=end or int(c.get('timer_ms',1000))!=int(s['timer_ms']):continue
        if ea_build_hash and c['ea_build_hash']!=ea_build_hash:continue
        key=(c['start'],c['end'])
        if key not in chosen:chosen[key]=c
    from .catalog_plan import coverage
    return coverage(list(chosen.values()),start,end)

def runtime_config(s):
    sys.path.insert(0,str(PROGRAM))
    from event_composer_domain import load_config
    config=load_config(str(PROGRAM/'config.txt'))
    config.update(STAFF_ALLOWED_SYMBOLS=s['symbol'],TARGET_SYMBOLS=s['symbol'],TELEGRAM_TOKEN='OFFLINE',
                  TELEGRAM_CHAT_ID='BACKTEST',GEMINI_API_KEY='',GEMINI_FALLBACK_ENABLED='false',ECONOMY_ENABLED='false')
    return config

def run_chunk(task):
    task_started=time.perf_counter()
    deny_network();sys.dont_write_bytecode=True;sys.path.insert(0,str(PROGRAM));logging.disable(logging.CRITICAL)
    out=Path(task['out'])
    from .system import restrict_writes
    restrict_writes(out)
    # A pool worker can pull several jobs. Switch its write boundary before
    # creating the next job directory, not after the old guard rejects it.
    out.mkdir(parents=True,exist_ok=True)
    os.environ['MOSES_LOG_DIRECTORY']=str(out/'logs')
    os.environ['TMP']=os.environ['TEMP']=str(out)
    from event_application import create_event_engine
    from event_engine.model import Kind,Resolution
    from event_engine.replay import select_inputs
    from event_host import load_staff
    from .bridge import CaptureInputs
    import numpy as np
    s=task['scenario'];config=task['config'];out=Path(task['out']);out.mkdir(parents=True,exist_ok=True)
    engine=create_event_engine(config,symbols=(s['symbol'],),selection=s['strategies'],trigger_overrides=s['triggers'],backtest=True,
                              oz_evaluation=s.get('oz_evaluation','selected'))
    engine.retain_signals=False
    writer=ResultWriter(out/'alerts.csv',{'run_id':task['run_id']},milliseconds(task['start']),milliseconds(task['end']))
    engine.signal_sink=writer.accept
    processor={}
    for consumer in (*engine.processors,*engine.strategies):
        original=consumer.on_event
        def measured(*args,_fn=original,_name=consumer.name,**kwargs):
            began=time.perf_counter_ns()
            try:
                if len(args)==4:
                    event,board,state,emit=args
                    def output(value):
                        writer.note_emission(_name,event,value);emit(value)
                    return _fn(event,board,state,output,**kwargs)
                return _fn(*args,**kwargs)
            finally:
                item=processor.setdefault(_name,{'ns':0,'calls':0});item['ns']+=time.perf_counter_ns()-began;item['calls']+=1
        consumer.on_event=measured
    start_ms=milliseconds(task['warm_start']);end_ms=milliseconds(task['end'])
    for ordinal,command in enumerate(s['commands']):
        engine.ingress.post(Kind.COMMAND,source='scenario',source_seq=ordinal,source_time=start_ms-1,
            payload={'symbol':s['symbol'],**command})
    engine.run()
    if writer.external_error:writer.close();raise ValueError(writer.external_error)
    if engine.error_log:writer.close();raise RuntimeError(str(engine.error_log))
    clock=[0.0];staff=load_staff();cache=staff.StaffPipeCache('',health_session='BACKTEST',monotonic=lambda:clock[0],gap_journal=out/'seq_gaps.jsonl')
    capture_paths=[warehouse_path(task['warehouse'],c['path']) if task.get('warehouse') else c['path'] for c in task['captures']]
    inputs=CaptureInputs(staff,cache,capture_paths,transport=task.get('transport','replay'),
                         clock=clock,start_ms=start_ms,end_ms=end_ms,capture_start=s.get('capture_start','keyframe'))
    resolution={'BAR':Resolution.BAR_CLOSE,'EVENT':Resolution.CONDITION,'TICK':Resolution.TICK}[s['mode']]
    subs=tuple(c.subscriptions() for c in engine.strategies+engine.processors)
    approximate=[c.name for c in engine.strategies+engine.processors if resolution<c.subscriptions().resolution]
    began=time.perf_counter();cpu=time.process_time();count=0;total_ns=0;hist={};last_progress=began
    month_metrics={};month_key=None;month_wall=began;month_cpu=cpu;month_count=0
    first_bundle_seconds=None;warmup_bundles=0;output_start_ms=milliseconds(task['start']);cpu_bundles={}
    try:
        for item in select_inputs(inputs,subs,resolution):
            observed_month=dt.datetime.fromtimestamp(item.source_time/1000,dt.timezone.utc).strftime('%Y-%m')
            if observed_month!=month_key:
                now_wall=time.perf_counter();now_cpu=time.process_time()
                if month_key is not None:
                    month_metrics[month_key]={'elapsed_seconds':now_wall-month_wall,'cpu_seconds':now_cpu-month_cpu,'bundles':count-month_count}
                month_key=observed_month;month_wall=now_wall;month_cpu=now_cpu;month_count=count
            t=time.perf_counter_ns()
            engine.ingress.post(item.kind,source=item.source,source_seq=item.source_seq,source_time=item.source_time,payload=item.payload)
            engine.run()
            if writer.external_error:raise ValueError(writer.external_error)
            if engine.error_log:raise RuntimeError(str(engine.error_log[-1]))
            if item.kind==Kind.MARKET_BUNDLE:
                elapsed=time.perf_counter_ns()-t;total_ns+=elapsed;count+=1
                if first_bundle_seconds is None:first_bundle_seconds=time.perf_counter()-task_started
                if item.source_time<output_start_ms:warmup_bundles+=1
                logical_cpu=current_processor();cpu_bundles[logical_cpu]=cpu_bundles.get(logical_cpu,0)+1
                # Bounded histogram, 0.1ms bins; exact average, p99 upper bound.
                key=(elapsed+99999)//100000;hist[key]=hist.get(key,0)+1
            now=time.perf_counter()
            if count==1 or now-last_progress>=5:
                progress={'pid':os.getpid(),'bundles':count,'time_ms':item.source_time,'percent':min(100,100*(item.source_time-start_ms)/(end_ms-start_ms)),
                          'elapsed_seconds':now-began,'max_memory_bytes':peak_memory()}
                write_progress(out/'progress.json',progress)
                last_progress=now
        accumulated=0;p99=0
        if month_key is not None:month_metrics[month_key]={'elapsed_seconds':time.perf_counter()-month_wall,'cpu_seconds':time.process_time()-month_cpu,'bundles':count-month_count}
        for value,amount in sorted(hist.items()):
            accumulated+=amount
            if accumulated>=count*.99:p99=value/10;break
        result={'bundles':count,'mean_ms':total_ns/max(1,count)/1e6,'p99_ms_upper_bound':p99,
                'pid':os.getpid(),'task_start':task['start'],'task_end':task['end'],'warm_start':task['warm_start'],
                'task_started':task_started,'task_finished':time.perf_counter(),
                'first_bundle_seconds':first_bundle_seconds,'warmup_bundles':warmup_bundles,'keyframe_seek':inputs.seek_info,
                'logical_cpu_bundle_samples':cpu_bundles,
                'wonbi_sigma':float(config.get('WONBI_SIGMA',3.0)),'strategies':list(engine.selection.names),
                'elapsed_seconds':time.perf_counter()-began,'cpu_seconds':time.process_time()-cpu,
                'max_memory_bytes':peak_memory(),'approximate':bool(approximate),'approximate_consumers':approximate,
                'calendar_month_measurements':month_metrics,
                'processor_timings':{n:{**v,'ms_per_bundle':v['ns']/max(1,count)/1e6} for n,v in processor.items()},
                'notifications':writer.notifications,'alerts_csv':relative_path(task.get('warehouse',PROGRAM.parents[1]),writer.path),
                'seams':[{**seam,'path':relative_path(task.get('warehouse',PROGRAM.parents[1]),seam['path'])} for seam in inputs.seams]}
        (out/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        return result
    finally:writer.close()

def run(s,warehouse,*,cores=None,sequential=False,captures=None,transport='replay',emit=lambda *a:None):
    requested_at=time.perf_counter()
    root=Path(warehouse);catalog=Warehouse(root);run_id=uuid.uuid4().hex
    out=root/'runs'/run_id;out.mkdir(parents=True,exist_ok=True)
    config=runtime_config(s)
    from event_selection import resolve
    resolve(s['strategies'],config)
    workers=1 if sequential else int(cores or s.get('cores') or physical_cores())
    if workers<1:raise ValueError('cores must be positive')
    if captures is None:
        captures,missing=resolve_captures(catalog,s)
        if missing:catalog.close();raise ValueError('누락된 녹화 조각: '+str(missing))
    from .history_check import require_complete,tick_warning
    try:require_complete(captures)
    except BaseException:catalog.close();raise
    for c in captures:
        if c.get('capture_id') and not catalog.find_capture(c['capture_id']):
            catalog.close();raise ValueError('녹화 무결성 오류: '+c['capture_id'])
    warnings=list(s['warnings'])
    if len({c.get('ea_build_hash') for c in captures})>1:warnings.append('서로 다른 EA 빌드의 녹화 조각이 섞였습니다.')
    for c in captures:
        warning=tick_warning(c,s['mode'])
        if warning:warnings.append(warning)
        if c.get('history_evidence_warning'):warnings.append(c['history_evidence_warning'])
        if c.get('empty') or c.get('records')==0:warnings.append(f"{c.get('start','')}~{c.get('end','')}: EA가 완료했으나 시장 관측이 0개인 조각입니다.")
    data={'scenario':{**s,'wonbi_sigma':float(config.get('WONBI_SIGMA',3.0))},'config_hash':digest(config),'code_hash':code_hash(),'cores':workers,
          'captures':[c.get('capture_id',c['path']) for c in captures],'warnings':warnings,
          'strategies':list(s['strategies']),'ea_builds':sorted({c.get('ea_build_hash','') for c in captures}),
          'schema_ids':sorted({c.get('schema_id',0) for c in captures}),'wonbi_sigma':float(config.get('WONBI_SIGMA',3))}
    periods=[(s['start'],s['end'])] if sequential else list(work_periods(s['start'],s['end'],s.get('work_size','MONTH')))
    tasks=[]
    from .calendar import warm_start
    try:
        for index,(start,end) in enumerate(periods):
            warm=warm_start(start,s['overlap_trading_days'],captures)
            pieces=[c for c in captures if c.get('end',end)>warm and c.get('start',start)<end]
            tasks.append({'scenario':s,'config':config,'start':start,'end':end,'warm_start':warm,'captures':pieces,
                          'run_id':run_id,'out':str(out/f'chunk_{index:03d}'),'transport':transport,'warehouse':str(root)})
        catalog.close();catalog=Warehouse(root,results=True)
        catalog.run(run_id,'RUNNING',data)
    finally:catalog.close()
    began=time.perf_counter();results=[];max_total_memory=0;first_progress=None
    try:
        with concurrent.futures.ProcessPoolExecutor(max_workers=min(workers,len(tasks))) as pool:
            futures=[pool.submit(run_chunk,t) for t in tasks]
            pending=set(futures)
            while pending:
                done,pending=concurrent.futures.wait(pending,timeout=2,return_when=concurrent.futures.FIRST_COMPLETED)
                for future in done:
                    results.append(future.result());emit('CHUNK_COMPLETE',{'done':len(results),'total':len(tasks),'run_id':run_id})
                memory=process_memory(peak=False);progress=[];sampled_pids=set()
                for task in tasks:
                    try:
                        row=json.loads((Path(task['out'])/'progress.json').read_text('utf-8'))
                        if row['pid'] not in sampled_pids:
                            memory+=process_memory(row['pid'],peak=False);sampled_pids.add(row['pid'])
                        progress.append(row)
                    except (OSError,ValueError,KeyError):pass
                max_total_memory=max(max_total_memory,memory)
                if progress and first_progress is None:first_progress=time.perf_counter()-requested_at
                if progress:emit('RUN_PROGRESS',{'run_id':run_id,'percent':sum(r['percent'] for r in progress)/len(tasks),
                    'elapsed_seconds':time.perf_counter()-began,'sampled_peak_total_memory_bytes':max_total_memory})
        catalog=Warehouse(root,results=True)
        for result in results:catalog.import_results(warehouse_path(root,result['alerts_csv']))
        # Streaming, deterministic chronological export. DuckDB may spill to disk.
        export=out/'alerts.csv'
        catalog.export_results(run_id,export)
        names={name for r in results for name in r['processor_timings']}
        for name in names:
            bundles=sum(r['bundles'] for r in results);ns=sum(r['processor_timings'].get(name,{}).get('ns',0) for r in results)
            catalog.db.execute('INSERT INTO timings VALUES (?,?,?,?,?)',[run_id,name,bundles,ns/max(1,bundles)/1e6,max(r['max_memory_bytes'] for r in results)])
        data.update(run_id=run_id,elapsed_seconds=time.perf_counter()-requested_at,replay_seconds=time.perf_counter()-began,
                    first_progress_seconds=first_progress,
                    logical_cpu_bundle_samples={str(cpu):sum(r.get('logical_cpu_bundle_samples',{}).get(cpu,0) for r in results)
                        for cpu in {key for r in results for key in r.get('logical_cpu_bundle_samples',{})}},
                    worker_distribution={str(pid):[{'start':r['task_start'],'end':r['task_end'],'bundles':r['bundles'],
                        'warmup_bundles':r['warmup_bundles'],'elapsed_seconds':r['elapsed_seconds']} for r in results if r.get('pid')==pid]
                        for pid in sorted({r['pid'] for r in results if 'pid' in r})},
                    verification_and_setup_seconds=began-requested_at,approximate=any(r['approximate'] for r in results),
                    chunks=results,alerts_csv=relative_path(root,export),max_worker_memory_bytes=max(r['max_memory_bytes'] for r in results),
                    concurrent_memory_upper_bound_bytes=sum(sorted((r['max_memory_bytes'] for r in results),reverse=True)[:workers]),
                    sampled_peak_total_memory_bytes=max_total_memory,memory_sampling_seconds=2)
        catalog.run(run_id,'COMPLETE',data);catalog.close()
        (out/'result.json').write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
        return data
    except BaseException as exc:
        # Tracebacks can contain workstation paths. Keep them out of the warehouse.
        import traceback
        diagnostics=out/'errors';diagnostics.mkdir(parents=True,exist_ok=True)
        details=traceback.format_exc().replace(str(PROGRAM.parents[1]),'<project>').replace(str(root),'<warehouse>')
        import re
        details=re.sub(r'File "[^"\n]+[\\/]([^\\/"\n]+)"',r'File "\1"',details)
        (diagnostics/(run_id+'.log')).write_text(details,encoding='utf-8')
        catalog=Warehouse(root,results=True);data.update(error=type(exc).__name__,error_reference=run_id,elapsed_seconds=time.perf_counter()-began)
        catalog.run(run_id,'FAILED',data);catalog.close();raise
