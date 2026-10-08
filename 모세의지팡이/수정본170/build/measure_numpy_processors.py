"""Private whole-monitoring diagnostic replay; no public run or catalog writes.

The prior host is a complete independent Part1/program copy. Both revisions use
the identical preserved input reader and scenario. Results are project-relative.
"""
import argparse,csv,datetime as dt,hashlib,json,logging,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'검증결과/numpy_processors'

def main():
    parser=argparse.ArgumentParser();parser.add_argument('revision',choices=('21','22'));parser.add_argument('case',choices=('day','week'));args=parser.parse_args()
    program=(OUT/'host21/Part1/program') if args.revision=='21' else ROOT/'Part1/program'
    sys.path[:0]=[str(program),str(ROOT/'Part2')]
    from event_backtest.system import deny_network,peak_memory,write_progress
    deny_network();logging.disable(logging.CRITICAL)
    from event_backtest.settings import settings,milliseconds
    from event_backtest.bridge import CaptureInputs
    from event_backtest.warehouse import ResultWriter
    from event_engine.model import Kind,Resolution
    from event_engine.replay import select_inputs
    from event_application import create_event_engine
    from event_host import load_staff
    from event_composer_domain import load_config
    from staff_golden.scenario import WATCHES
    from event_engine.domain_support import plain
    import numpy as np
    warehouse=Path(settings()['warehouse'])
    if args.case=='day':
        capture=json.loads((ROOT/'검증결과/part2_connection/day_BAR.json').read_text('utf-8'))[0]
        start,end='2026-09-25','2026-09-26'
    else:
        capture=next(x for x in json.loads((ROOT/'검증결과/part2_connection/recorded_captures.json').read_text('utf-8')) if x['start']=='2025-09-01')
        start,end='2025-09-01','2025-09-08'
    path=warehouse/capture['path'];out=OUT/f'revision{args.revision}_{args.case}';out.mkdir(exist_ok=True)
    if (out/'result.json').exists():raise FileExistsError('completed diagnostic already exists')
    sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
    hashes={p.relative_to(program).as_posix():sha(p) for p in program.rglob('*.py') if '__pycache__' not in p.parts}
    config=load_config(str(program/'config.txt'))
    config.update(STAFF_ALLOWED_SYMBOLS='XAUUSD+',TARGET_SYMBOLS='XAUUSD+',TELEGRAM_TOKEN='OFFLINE',
        TELEGRAM_CHAT_ID='OFFICIAL',GEMINI_API_KEY='',GEMINI_FALLBACK_ENABLED='false',ECONOMY_ENABLED='false')
    engine=create_event_engine(config,symbols=('XAUUSD+',),enabled_specials=tuple(f'SPECIAL{i}' for i in range(1,8)),trigger_overrides={'SPECIAL7':'무지성 올존'})
    engine.retain_signals=False
    writer=ResultWriter(out/'alerts.csv',{'run_id':f'numpy{args.revision}_{args.case}'},milliseconds(start),milliseconds(end))
    processor={};signal_count=0;signal_hash=hashlib.sha256()
    def accept(event):
        nonlocal signal_count
        signal_count+=1
        signal_hash.update(json.dumps({'time':event.source_time,**plain(event.payload)},ensure_ascii=False,sort_keys=True).encode())
        writer.accept(event)
    engine.signal_sink=accept
    for consumer in (*engine.processors,*engine.strategies):
        original=consumer.on_event
        def measured(*a,_fn=original,_name=consumer.name,**kw):
            began=time.perf_counter_ns()
            try:
                if len(a)==4:
                    event,board,state,emit=a
                    def output(value):writer.note_emission(_name,event,value);emit(value)
                    return _fn(event,board,state,output,**kw)
                return _fn(*a,**kw)
            finally:
                item=processor.setdefault(_name,{'ns':0,'calls':0});item['ns']+=time.perf_counter_ns()-began;item['calls']+=1
        consumer.on_event=measured
    for i,(chat,text) in enumerate(WATCHES):
        engine.ingress.post(Kind.COMMAND,source='commands',source_seq=i,source_time=milliseconds(start)-1,
                            payload={'symbol':'XAUUSD+','chat_id':chat,'text':text})
    engine.run();assert not engine.error_log,engine.error_log
    processor.clear()
    clock=[0.];staff=load_staff();cache=staff.StaffPipeCache('',health_session='DIAGNOSTIC',monotonic=lambda:clock[0],gap_journal=out/'gaps.jsonl')
    inputs=CaptureInputs(staff,cache,[path],transport='replay',clock=clock,start_ms=milliseconds(start),end_ms=milliseconds(end))
    subs=tuple(c.subscriptions() for c in engine.strategies+engine.processors)
    times=[];began=time.perf_counter();cpu=time.process_time();last=began
    try:
        for item in select_inputs(inputs,subs,Resolution.BAR_CLOSE):
            t=time.perf_counter_ns()
            engine.ingress.post(item.kind,source=item.source,source_seq=item.source_seq,source_time=item.source_time,payload=item.payload)
            engine.run()
            if item.kind==Kind.MARKET_BUNDLE:times.append((time.perf_counter_ns()-t)/1e6)
            if engine.error_log:raise RuntimeError(str(engine.error_log[-1]))
            if writer.external_error:raise ValueError(writer.external_error)
            now=time.perf_counter()
            if now-last>=20:
                data={'bundles':len(times),'elapsed_seconds':now-began,'last_source_time':item.source_time,'max_memory_bytes':peak_memory()}
                write_progress(out/'progress.json',data)
                print(args.revision,args.case,'bundles',len(times),'seconds',round(now-began,1),flush=True);last=now
        elapsed=time.perf_counter()-began
        result=dict(revision=args.revision,case=args.case,start=start,end=end,symbol='XAUUSD+',mode='BAR',
            purpose='전체 감시 진단 재생; 선택 실행 백테스트 시간 아님',samples=1,bundles=len(times),
            input_inclusive_ms=elapsed*1000/len(times),engine_mean_ms=float(np.mean(times)),engine_p99_ms=float(np.percentile(times,99)),
            elapsed_seconds=elapsed,cpu_seconds=time.process_time()-cpu,max_memory_bytes=peak_memory(),
            processor_timings={n:dict(v,ms_per_bundle=v['ns']/1e6/len(times)) for n,v in processor.items()},
            signals=signal_count,signal_sha256=signal_hash.hexdigest(),notifications=writer.notifications,
            errors=plain(engine.error_log),capture_id=capture['capture_id'],tick_evidence=capture['tick_evidence'],
            code_hashes=hashes,commands=[{'chat_id':c,'text':t} for c,t in WATCHES])
        result['source_unchanged']=hashes=={p.relative_to(program).as_posix():sha(p) for p in program.rglob('*.py') if '__pycache__' not in p.parts}
        (out/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        assert result['source_unchanged']
        print('DONE',args.revision,args.case,'bundles',len(times),'engine_ms',result['engine_mean_ms'],'notifications',writer.notifications,flush=True)
    finally:writer.close()
if __name__=='__main__':main()
