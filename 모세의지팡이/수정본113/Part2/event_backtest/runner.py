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
from .settings import PROGRAM,digest,file_hash,milliseconds,months,work_periods,overlap_start,warehouse_path,relative_path,COMMAND_CONTINUOUS_REASON
from .system import deny_network,physical_cores,peak_memory,process_memory,write_progress,current_processor
from .warehouse import Warehouse,ResultWriter,FIELDS
from .cancellation import requested, file_check, coverage, utc_ms
from .build_compat import compatible_hashes,compatible_group
from .instrumentation import measure_consumer

_STOP_CHECK_SECONDS=0.5
_CPU_SAMPLE_SECONDS=0.5

def worker_count(s,cores=None,sequential=False):
    """Physical-core default; explicit settings retain precedence."""
    workers=1 if sequential else int(cores or s.get('cores') or physical_cores())
    if workers<1:raise ValueError('cores must be positive')
    return workers


def execution_plan(s,cores=None,sequential=False):
    """Keep all command-dependent state in one engine, across recording pieces."""
    from .partition import plan_periods
    requested_workers=worker_count(s,cores)
    continuous=bool(s.get('commands'))
    if s.get('_available_periods'):
        # Never keep WATCH/strategy state across an unrecorded interval.
        periods=[]
        for period in s['_available_periods']:
            if continuous or sequential:periods.append((period['start'],period['end']))
            else:
                chunks,_=plan_periods(period['start'],period['end'],requested_workers,s.get('work_size','AUTO'),s.get('adaptive_chunks',True))
                periods.extend(chunks)
        partition='AVAILABLE_PERIODS'
        workers=1 if continuous or sequential else min(requested_workers,len(periods))
    elif continuous or sequential:
        periods=[(s['start'],s['end'])]
        partition='SEQUENTIAL' if sequential else 'COMMAND_CONTINUOUS'
        workers=1
    else:
        periods,partition=plan_periods(s['start'],s['end'],requested_workers,s.get('work_size','AUTO'),s.get('adaptive_chunks',True))
        workers=min(requested_workers,len(periods))
    details={'cores':workers,'requested_cores':requested_workers,
             'requested_work_size':s.get('work_size','AUTO'),'requested_sequential':bool(sequential),
             'partition':partition,'execution_policy':'AVAILABLE_INDEPENDENT' if s.get('_available_periods') else 'COMMAND_CONTINUOUS' if continuous else 'SEQUENTIAL' if sequential else 'INDEPENDENT_CHUNKS',
             'execution_reason':'데이터 누락 구간을 넘어 상태를 이어 붙이지 않습니다.' if s.get('_available_periods') else COMMAND_CONTINUOUS_REASON if continuous else '', 'planned_chunks':len(periods)}
    return periods,details

def code_hash():
    root=PROGRAM.parents[1]
    paths=list(PROGRAM.rglob('*.py'))+list((root/'Part2/event_backtest').glob('*.py'))+[root/'Part2/generic_backtest/native_mt5.py',root/'settings/strategy_registry.json']
    return digest({str(p.relative_to(root)):file_hash(p) for p in sorted(paths) if '__pycache__' not in p.parts})


def capture_build_provenance(captures,current_ea_hash):
    """Preserve each recorded hash while distinguishing approved compatibility."""
    actual={c.get('ea_build_hash','') for c in captures}
    approved=compatible_hashes(current_ea_hash)
    return {'ea_builds':sorted(actual),
            'current_ea_build_hash':current_ea_hash,
            'capture_ea_build_hashes':{c.get('capture_id',c['path']):c.get('ea_build_hash','') for c in captures},
            'ea_build_compatibility_applied':any(h!=current_ea_hash and h in approved for h in actual),
            'mixed_unapproved_ea_builds':not compatible_group(actual)}

def resolve_captures(catalog,s,*,ea_build_hash=None):
    mode='BAR' if s['mode']=='BAR' else 'TIMER'
    end=s['end']
    candidates=catalog.available(s['symbol'],mode)
    from .build_plan import current_build,schema_id
    ea_build_hash=ea_build_hash or current_build(catalog.root)
    approved_builds=compatible_hashes(ea_build_hash)
    candidates=[c for c in candidates if c['schema_id']==schema_id() and c.get('reconstruction_verified') and not c.get('history_missing') and int(c.get('timer_ms',1000))==int(s['timer_ms'])
                and c['ea_build_hash'] in approved_builds]
    candidates.sort(key=lambda c:c['ea_build_hash']!=ea_build_hash)
    from .calendar import warm_start
    try:start=warm_start(s['start'],s['overlap_trading_days'],candidates)
    except ValueError:start=overlap_start(s['start'],s['overlap_trading_days'])
    chosen={}
    for c in candidates:
        if c['end']<=start or c['start']>=end or int(c.get('timer_ms',1000))!=int(s['timer_ms']):continue
        key=(c['start'],c['end'])
        if key not in chosen:chosen[key]=c
    from .catalog_plan import coverage
    return coverage(list(chosen.values()),start,end)

def runtime_config(s):
    sys.path.insert(0,str(PROGRAM))
    from event_composer_domain import load_config
    config=load_config(str(PROGRAM/'config.txt'))
    config.update(SYMBOLS=s['symbol'],TELEGRAM_TOKEN='OFFLINE',
                  TELEGRAM_CHAT_ID='BACKTEST',GEMINI_API_KEY='',GEMINI_FALLBACK_ENABLED='false',ECONOMY_ENABLED='false')
    return config

def applied_strategy_settings(s,config):
    """Report registry defaults/overrides; no source-file or market evaluation."""
    if str(PROGRAM) not in sys.path:sys.path.insert(0,str(PROGRAM))
    from special_settings_model import SESSIONS
    from strategy_recipe.registry import list_presets, default_settings
    available=list_presets('Part2')
    selected=list(available) if s['strategies']==['ALL'] else [name for name in s['strategies'] if name in available]
    applied={}
    for name in selected:
        default_trigger,default_time=default_settings(name)
        time_inputs=s.get('special_time_filters',{})
        applied[name]={'trigger':s.get('triggers',{}).get(name) or default_trigger,
                       'final_alert_time_filters':time_inputs.get(name,default_time),
                       'time_source':'override' if name in time_inputs else 'preset_default',
                       'time_basis':'KST','session_config':{key:config.get(key,'') for key in SESSIONS}}
    return applied


def _unstarted_chunk(task,out,began):
    """Persist an empty, explicit cancellation result without loading an engine."""
    writer=ResultWriter(out/'alerts.csv',{'run_id':task['run_id']},milliseconds(task['start']),milliseconds(task['end']))
    writer.close()
    result={'watch_dependencies':{},'cancelled':True,'not_started':True,
            'processed_start_ms':None,'processed_end_ms':None,'bundles':0,'mean_ms':0,'p99_ms_upper_bound':0,
            'pid':os.getpid(),'task_start':task['start'],'task_end':task['end'],'warm_start':task['warm_start'],
            'task_started':began,'task_finished':time.perf_counter(),'first_bundle_seconds':None,
            'warmup_bundles':0,'keyframe_seek':{},'logical_cpu_bundle_samples':{},
            'logical_cpu_sampling_seconds':_CPU_SAMPLE_SECONDS,'stop_check_seconds':_STOP_CHECK_SECONDS,
            'wonbi_sigma':float(task['config'].get('WONBI_SIGMA',3)),'strategies':list(task['scenario']['strategies']),
            'elapsed_seconds':time.perf_counter()-began,'cpu_seconds':0,'max_memory_bytes':peak_memory(),
            'approximate':False,'approximate_consumers':[],'calendar_month_measurements':{},'processor_timings':{},
            'notifications':0,'alerts':0,'alert_months':{},
            'alerts_csv':relative_path(task.get('warehouse',PROGRAM.parents[1]),writer.path),'seams':[]}
    write_progress(out/'progress.json',{'pid':os.getpid(),'bundles':0,'percent':0,
                                     'max_memory_bytes':result['max_memory_bytes'],'cancelled':True})
    (out/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    return result


def unprocessed_periods(tasks,results):
    """State the unprocessed suffix after the last observed market bundle."""
    finished={(row['task_start'],row['task_end']):row for row in results}
    remaining=[]
    for task in tasks:
        row=finished.get((task['start'],task['end']))
        if row is not None and not row.get('cancelled'):continue
        last=None if row is None else row.get('processed_end_ms')
        remaining.append({'start':task['start'] if last is None else utc_ms(last),
                          'end':task['end'],'start_exclusive':last is not None,
                          'reason':'NOT_STARTED' if row is None or row.get('not_started') else 'INTERRUPTED'})
    return remaining

def run_chunk(task):
    task_started=time.perf_counter()
    deny_network();sys.dont_write_bytecode=True;sys.path.insert(0,str(PROGRAM));logging.disable(logging.NOTSET)
    out=Path(task['out'])
    from .system import restrict_writes
    restrict_writes(out)
    # A pool worker can pull several jobs. Switch its write boundary before
    # creating the next job directory, not after the old guard rejects it.
    out.mkdir(parents=True,exist_ok=True)
    if (out.parent/'stop.request').exists():return _unstarted_chunk(task,out,task_started)
    os.environ['MOSES_LOG_DIRECTORY']=str(out/'logs')
    from module_diagnostics import configure
    diagnostics=configure(out,trace=False)
    os.environ['TMP']=os.environ['TEMP']=str(out)
    from event_application import create_event_engine
    from event_engine.model import Kind,Resolution
    from event_engine.replay import select_inputs
    from event_host import load_staff
    from .bridge import CaptureInputs
    import numpy as np
    s=task['scenario'];config=task['config'];out=Path(task['out']);out.mkdir(parents=True,exist_ok=True)
    engine=create_event_engine(config,symbols=(s['symbol'],),selection=s['strategies'],trigger_overrides=s['triggers'],backtest=True,
                              oz_evaluation=s.get('oz_evaluation','selected'),time_overrides=s.get('special_time_filters',{}))
    diagnostics.configure_specials(engine.selection.specials)
    engine.retain_signals=False
    engine._logger=lambda details:diagnostics.log(details["strategy"],logging.ERROR,"처리 오류: %s",details,exc_info=sys.exc_info())
    writer=ResultWriter(out/'alerts.csv',{'run_id':task['run_id']},milliseconds(task['start']),milliseconds(task['end']))
    registrations=[]
    def startup_signal(event):
        content=event.payload.get('content',{})
        if content.get('type')=='WATCH_COMMAND':registrations.append(content['command'])
        writer.accept(event)
    engine.signal_sink=startup_signal
    processor={}
    for consumer in (*engine.processors,*engine.strategies):
        consumer.on_event=measure_consumer(consumer.on_event,consumer.name,writer,processor)
    start_ms=milliseconds(task['warm_start']);end_ms=milliseconds(task['end'])
    for ordinal,command in enumerate(s['commands']):
        engine.ingress.post(Kind.COMMAND,source='scenario',source_seq=ordinal,source_time=start_ms-1,
            payload={'symbol':s['symbol'],**command})
    engine.run()
    if writer.external_error:writer.close();raise ValueError(writer.external_error)
    if engine.error_log:writer.close();raise RuntimeError(str(engine.error_log))
    from event_watch_selection import specialize
    watch_dependencies=specialize(engine,registrations)
    engine.signal_sink=writer.accept
    clock=[0.0];staff=load_staff();cache=staff.StaffPipeCache('',health_session='BACKTEST',monotonic=lambda:clock[0],gap_journal=out/'seq_gaps.jsonl')
    capture_paths=[warehouse_path(task['warehouse'],c['path']) if task.get('warehouse') else c['path'] for c in task['captures']]
    inputs=CaptureInputs(staff,cache,capture_paths,transport=task.get('transport','replay'),
                         clock=clock,start_ms=start_ms,end_ms=end_ms,capture_start=s.get('capture_start','keyframe'))
    resolution={'BAR':Resolution.BAR_CLOSE,'EVENT':Resolution.CONDITION,'TICK':Resolution.TICK}[s['mode']]
    subs=tuple(c.subscriptions() for c in engine.strategies+engine.processors)
    approximate=[c.name for c in engine.strategies+engine.processors if resolution<c.subscriptions().resolution]
    stop=file_check(Path(task['out']).parent/'stop.request')
    interrupted=False;processed_start=None;processed_end=None
    began=time.perf_counter();cpu=time.process_time();count=0;total_ns=0;hist={};last_progress=began
    next_stop_check=began;next_cpu_sample=began
    month_metrics={};month_key=None;month_start_ms=None;next_month_ms=None;month_wall=began;month_cpu=cpu;month_count=0
    first_bundle_seconds=None;warmup_bundles=0;output_start_ms=milliseconds(task['start']);cpu_bundles={}
    try:
        for item in select_inputs(inputs,subs,resolution):
            loop_now=time.perf_counter()
            if loop_now>=next_stop_check:
                next_stop_check=loop_now+_STOP_CHECK_SECONDS
                if requested(stop):interrupted=True;break
            # Keep both bounds so a backwards source-time jump is also classified
            # exactly as before. Calendar conversion is only needed at a boundary.
            if month_key is None or item.source_time<month_start_ms or item.source_time>=next_month_ms:
                month=dt.datetime.fromtimestamp(item.source_time/1000,dt.timezone.utc).replace(day=1,hour=0,minute=0,second=0,microsecond=0)
                following=month.replace(year=month.year+1,month=1) if month.month==12 else month.replace(month=month.month+1)
                month_start_ms=int(month.timestamp()*1000);next_month_ms=int(following.timestamp()*1000)
                now_wall=time.perf_counter();now_cpu=time.process_time()
                if month_key is not None:
                    month_metrics[month_key]={'elapsed_seconds':now_wall-month_wall,'cpu_seconds':now_cpu-month_cpu,'bundles':count-month_count}
                month_key=month.strftime('%Y-%m');month_wall=now_wall;month_cpu=now_cpu;month_count=count
            t=time.perf_counter_ns()
            engine.ingress.post(item.kind,source=item.source,source_seq=item.source_seq,source_time=item.source_time,payload=item.payload)
            engine.run()
            if writer.external_error:raise ValueError(writer.external_error)
            if engine.error_log:raise RuntimeError(str(engine.error_log[-1]))
            if item.kind==Kind.MARKET_BUNDLE:
                elapsed=time.perf_counter_ns()-t;total_ns+=elapsed;count+=1
                if first_bundle_seconds is None:first_bundle_seconds=time.perf_counter()-task_started
                if item.source_time<output_start_ms:warmup_bundles+=1
                else:
                    if processed_start is None:processed_start=item.source_time
                    processed_end=item.source_time
                # Bounded histogram, 0.1ms bins; exact average, p99 upper bound.
                key=(elapsed+99999)//100000;hist[key]=hist.get(key,0)+1
            now=time.perf_counter()
            if item.kind==Kind.MARKET_BUNDLE and now>=next_cpu_sample:
                logical_cpu=current_processor();cpu_bundles[logical_cpu]=cpu_bundles.get(logical_cpu,0)+1
                next_cpu_sample=now+_CPU_SAMPLE_SECONDS
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
        write_progress(out/'progress.json',{'pid':os.getpid(),'bundles':count,'percent':100 if not interrupted else (100*(processed_end-start_ms)/(end_ms-start_ms) if processed_end else 0),'max_memory_bytes':peak_memory()})
        result={'watch_dependencies':watch_dependencies,'cancelled':interrupted,'processed_start_ms':processed_start,'processed_end_ms':processed_end,'bundles':count,'mean_ms':total_ns/max(1,count)/1e6,'p99_ms_upper_bound':p99,
                'pid':os.getpid(),'task_start':task['start'],'task_end':task['end'],'warm_start':task['warm_start'],
                'task_started':task_started,'task_finished':time.perf_counter(),
                'first_bundle_seconds':first_bundle_seconds,'warmup_bundles':warmup_bundles,'keyframe_seek':inputs.seek_info,
                'logical_cpu_bundle_samples':cpu_bundles,'logical_cpu_sampling_seconds':_CPU_SAMPLE_SECONDS,'stop_check_seconds':_STOP_CHECK_SECONDS,
                'wonbi_sigma':float(config.get('WONBI_SIGMA',3.0)),'strategies':list(engine.selection.names),
                'elapsed_seconds':time.perf_counter()-began,'cpu_seconds':time.process_time()-cpu,
                'max_memory_bytes':peak_memory(),'approximate':bool(approximate),'approximate_consumers':approximate,
                'calendar_month_measurements':month_metrics,
                'processor_timings':{n:{**v,'ms_per_bundle':v['ns']/max(1,count)/1e6} for n,v in processor.items()},
                'notifications':writer.notifications,'alerts':len(writer.alert_ids),'alert_months':dict(sorted(writer.alert_months.items())),'alerts_csv':relative_path(task.get('warehouse',PROGRAM.parents[1]),writer.path),
                'seams':[{**seam,'path':relative_path(task.get('warehouse',PROGRAM.parents[1]),seam['path'])} for seam in inputs.seams]}
        (out/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        return result
    finally:writer.close()

def run(s,warehouse,*,cores=None,sequential=False,captures=None,transport='replay',emit=lambda *a:None,cancel=lambda:None):
    requested_at=time.perf_counter()
    root=Path(warehouse);catalog=Warehouse(root);run_id=s.get('_run_id') or uuid.uuid4().hex
    if len(run_id)!=32 or any(c not in '0123456789abcdef' for c in run_id):raise ValueError('invalid run ID')
    out=root/'runs'/run_id;out.mkdir(parents=True,exist_ok=True)
    config=runtime_config(s)
    from event_selection import resolve
    resolve(s['strategies'],config)
    periods,execution=execution_plan(s,cores,sequential)
    workers=execution['cores']
    if captures is None:
        captures,missing=resolve_captures(catalog,s)
        if missing:catalog.close();raise ValueError('누락된 녹화 조각: '+str(missing))
    from .history_check import require_complete,tick_warning
    try:require_complete(captures)
    except BaseException:catalog.close();raise
    for c in captures:
        if c.get('capture_id') and not catalog.find_capture(c['capture_id']):
            catalog.close();raise ValueError('녹화 무결성 오류: '+c['capture_id'])
    if s.get('result_mode')=='VIRTUAL_ENTRY':
        from .virtual_entry import pricing
        pricing(s,captures,config)
    warnings=list(s['warnings'])
    if s.get('period_adjustment'):warnings.append(s['period_adjustment']['message'])
    if len(s.get('_available_periods',[]))>1:warnings.append('데이터가 없는 기간을 제외하고 각 보유 구간을 독립적으로 재생했습니다.')
    if s.get('commands') and COMMAND_CONTINUOUS_REASON not in warnings:warnings.append(COMMAND_CONTINUOUS_REASON)
    from .build_plan import current_build
    build_provenance=capture_build_provenance(captures,current_build(root))
    if build_provenance.pop('mixed_unapproved_ea_builds'):warnings.append('서로 다른 EA 빌드의 녹화 조각이 섞였습니다.')
    for c in captures:
        warning=tick_warning(c,s['mode'])
        if warning:warnings.append(warning)
        if c.get('history_evidence_warning'):warnings.append(c['history_evidence_warning'])
        if c.get('empty') or c.get('records')==0:warnings.append(f"{c.get('start','')}~{c.get('end','')}: EA가 완료했으나 시장 관측이 0개인 조각입니다.")
    applied=applied_strategy_settings(s,config)
    data={'progress_log':relative_path(root,out/'progress.jsonl'),'result_path':relative_path(root,out/'result.json'),'result_mode':s.get('result_mode','ALERT_ONLY'),'scenario':{**s,'wonbi_sigma':float(config.get('WONBI_SIGMA',3.0))},'config_hash':digest(config),'code_hash':code_hash(),'cores':workers,
          'applied_special_settings':applied,
          'captures':[c.get('capture_id',c['path']) for c in captures],'warnings':warnings,
          'strategies':list(s['strategies']),'period_adjustment':s.get('period_adjustment'),
          'excluded_periods':s.get('excluded_periods',[]),'available_periods':s.get('_available_periods',[]),**build_provenance,
          'schema_ids':sorted({c.get('schema_id',0) for c in captures}),'wonbi_sigma':float(config.get('WONBI_SIGMA',3)),**execution}
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
    emit('RUN_START',{'run_id':run_id,**execution})
    began=time.perf_counter();results=[];max_total_memory=0;first_progress=None
    try:
        from .worker_schedule import plan_dispatch,BoundedTasks,ProgressRows,worker_pool
        dispatch_order,scheduling=plan_dispatch(tasks,workers)
        data['worker_scheduling']=scheduling
        with worker_pool(min(workers,len(tasks)),factory=concurrent.futures.ProcessPoolExecutor) as pool:
            queue=BoundedTasks(pool,run_chunk,tasks,min(workers,len(tasks)),dispatch_order)
            progress_rows=ProgressRows(tasks)
            def stop_submissions():
                if requested(cancel) or (out/'stop.request').exists():
                    (out/'stop.request').touch(exist_ok=True)
                    queue.stop()
                return queue.stopped
            if not stop_submissions():queue.fill()
            while queue.pending:
                if stop_submissions() and not queue.pending:break
                done,_=concurrent.futures.wait(queue.pending,timeout=2,return_when=concurrent.futures.FIRST_COMPLETED)
                completed=queue.collect(done)
                for _,result in completed:
                    results.append(result);emit('CHUNK_COMPLETE',{'done':len(results),'total':len(tasks),'run_id':run_id})
                # Cancellation may have arrived while waiting or in a callback.
                if not stop_submissions():queue.fill()
                memory=process_memory(peak=False);progress=[];sampled_pids=set()
                for row in progress_rows.read(queue.submitted,queue.completed):
                    try:
                        if row['pid'] not in sampled_pids:
                            memory+=process_memory(row['pid'],peak=False);sampled_pids.add(row['pid'])
                        progress.append(row)
                    except (OSError,ValueError,KeyError):pass
                max_total_memory=max(max_total_memory,memory)
                if progress and first_progress is None:first_progress=time.perf_counter()-requested_at
                if progress:emit('RUN_PROGRESS',{'run_id':run_id,'percent':sum(r['percent'] for r in progress)/len(tasks),
                    'elapsed_seconds':time.perf_counter()-began,'sampled_peak_total_memory_bytes':max_total_memory,**execution})
        scheduling['max_pending_observed']=queue.high_water
        scheduling['submitted_chunks']=len(queue.submitted)
        scheduling['cancelled_before_start']=len(queue.cancelled)
        # Dispatch/completion order never becomes alert import order.
        results.sort(key=lambda r:(r['task_start'],r['task_end']))
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
                    first_progress_seconds=first_progress,logical_cpu_sampling_seconds=_CPU_SAMPLE_SECONDS,
                    logical_cpu_bundle_samples={str(cpu):sum(r.get('logical_cpu_bundle_samples',{}).get(cpu,0) for r in results)
                        for cpu in {key for r in results for key in r.get('logical_cpu_bundle_samples',{})}},
                    worker_distribution={str(pid):[{'start':r['task_start'],'end':r['task_end'],'bundles':r['bundles'],
                        'warmup_bundles':r['warmup_bundles'],'elapsed_seconds':r['elapsed_seconds']} for r in results if r.get('pid')==pid]
                        for pid in sorted({r['pid'] for r in results if 'pid' in r})},
                    verification_and_setup_seconds=began-requested_at,approximate=any(r['approximate'] for r in results),
                    chunks=results,alerts_csv=relative_path(root,export),max_worker_memory_bytes=max((r['max_memory_bytes'] for r in results),default=0),
                    concurrent_memory_upper_bound_bytes=sum(sorted((r['max_memory_bytes'] for r in results),reverse=True)[:workers]),
                    sampled_peak_total_memory_bytes=max_total_memory,memory_sampling_seconds=2)
        interrupted=requested(cancel) or (out/'stop.request').exists() or any(r.get('cancelled') for r in results)
        data['processed_periods']=coverage(results)
        data['unprocessed_periods']=unprocessed_periods(tasks,results)
        from .alert_stats import summarize
        data['alert_statistics']=summarize(s['start'],s['end'],results,periods=s.get('_available_periods'))
        if s.get('result_mode')=='VIRTUAL_ENTRY' and not interrupted:
            from .virtual_entry import calculate
            data['virtual_entry']=calculate(export,captures,root,s,config,out,emit=emit,cancel=cancel)
            interrupted=data['virtual_entry'].get('cancelled',False)
        data.update(status='CANCELLED' if interrupted else 'COMPLETE',status_label='중단됨(부분 결과)' if interrupted else '완료')
        if interrupted and s.get('result_mode')=='VIRTUAL_ENTRY' and 'virtual_entry' not in data:
            data['virtual_entry']={'cancelled':True,'not_started':True,'summary':[],'processed_signals':0}
        catalog.run(run_id,data['status'],data);catalog.close()
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
