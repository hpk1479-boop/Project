"""Offline, complete internal signal trace before any schema change."""
import argparse,collections,gzip,hashlib,json,logging,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
EVIDENCE=ROOT/'검증결과/schema_cleanup'

def main():
    parser=argparse.ArgumentParser();parser.add_argument('revision',choices=('21','22','23'))
    parser.add_argument('--end',default='2025-09-08');parser.add_argument('--label',default='')
    args=parser.parse_args()
    program=(ROOT/'검증결과/numpy_processors/host21/Part1/program' if args.revision=='21' else
             EVIDENCE/'hosts/revision22/Part1/program' if args.revision=='22' else ROOT/'Part1/program')
    out=EVIDENCE/'phase0'/('revision'+args.revision+args.label);out.mkdir(parents=True,exist_ok=True)
    if (out/'signals.jsonl.gz').exists():raise FileExistsError('trace already exists')
    sys.path[:0]=[str(program),str(ROOT/'Part2')]
    from event_backtest.system import deny_network,peak_memory,write_progress
    deny_network();logging.disable(logging.CRITICAL)
    from event_backtest.settings import settings,milliseconds
    from event_backtest.bridge import CaptureInputs
    from event_backtest.warehouse import ResultWriter
    from event_engine.model import Kind,Resolution,Signal,signal_id
    from event_engine.replay import select_inputs
    from event_application import create_event_engine
    from event_host import load_staff
    from event_composer_domain import load_config
    from staff_golden.scenario import WATCHES
    from event_engine.domain_support import plain
    import numpy as np
    capture=next(x for x in json.loads((ROOT/'검증결과/part2_connection/recorded_captures.json').read_text('utf-8')) if x['start']=='2025-09-01')
    start,end=milliseconds('2025-09-01'),milliseconds(args.end)
    path=Path(settings()['warehouse'])/capture['path']
    sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
    hashes={p.relative_to(program).as_posix():sha(p) for p in program.rglob('*.py') if '__pycache__' not in p.parts}
    config=load_config(str(program/'config.txt'))
    config.update(STAFF_ALLOWED_SYMBOLS='XAUUSD+',TARGET_SYMBOLS='XAUUSD+',TELEGRAM_TOKEN='OFFLINE',
        TELEGRAM_CHAT_ID='OFFICIAL',GEMINI_API_KEY='',GEMINI_FALLBACK_ENABLED='false',ECONOMY_ENABLED='false')
    engine=create_event_engine(config,symbols=('XAUUSD+',),enabled_specials=tuple(f'SPECIAL{i}' for i in range(1,8)),trigger_overrides={'SPECIAL7':'무지성 올존'})
    engine.retain_signals=False
    writer=ResultWriter(out/'alerts.csv',{'run_id':'phase0_'+args.revision},start,end)
    count=collections.Counter();bundle=0
    signals=gzip.open(out/'signals.jsonl.gz','wt',encoding='utf-8',compresslevel=1)
    causes=gzip.open(out/'causes.jsonl.gz','wt',encoding='utf-8',compresslevel=1)
    def line(handle,value):handle.write(json.dumps(value,ensure_ascii=False,separators=(',',':'))+'\n')
    def accept(event):
        payload=plain(event.payload);content=payload['content'];raw=content.get('event',{})
        family=content.get('family') or content.get('type') or payload['strategy']
        subtype=raw.get('kind') or content.get('command',{}).get('action') or ''
        count[family+':'+str(subtype)]+=1
        line(signals,dict(bundle=bundle,engine_seq=event.engine_seq,time=event.source_time,payload=payload))
        writer.accept(event)
    engine.signal_sink=accept
    original_emit=engine._emit
    def traced_emit(owner,event,output):
        if isinstance(output,Signal):
            writer.note_emission(owner,event,output)
            line(causes,dict(bundle=bundle,parent_engine_seq=event.engine_seq,parent_kind=event.kind.value,
                parent_source=event.source,parent_signal_id=event.payload.get('signal_id'),
                parent_payload=plain(event.payload) if event.kind!=Kind.MARKET_BUNDLE else None,
                time=event.source_time,owner=owner,condition_key=output.condition_key,
                signal_id=signal_id(owner,output.symbol,event.source_time,output.condition_key)))
        return original_emit(owner,event,output)
    engine._emit=traced_emit
    for i,(chat,text) in enumerate(WATCHES):
        engine.ingress.post(Kind.COMMAND,source='commands',source_seq=i,source_time=start-1,
                            payload={'symbol':'XAUUSD+','chat_id':chat,'text':text})
    engine.run();assert not engine.error_log,engine.error_log
    clock=[0.];staff=load_staff();cache=staff.StaffPipeCache('',health_session='DIAGNOSTIC',monotonic=lambda:clock[0],gap_journal=out/'gaps.jsonl')
    inputs=CaptureInputs(staff,cache,[path],transport='replay',clock=clock,start_ms=start,end_ms=end)
    subs=tuple(c.subscriptions() for c in engine.strategies+engine.processors)
    began=last=time.perf_counter()
    try:
        for item in select_inputs(inputs,subs,Resolution.BAR_CLOSE):
            if item.kind==Kind.MARKET_BUNDLE:bundle+=1
            engine.ingress.post(item.kind,source=item.source,source_seq=item.source_seq,source_time=item.source_time,payload=item.payload)
            engine.run()
            if bundle==1 and not (out/'first_board.npz').exists():
                data={}
                for (symbol,tf),snapshot in engine.board._feeds.items():
                    for field in ('time','volume','values'):data[tf+'_'+field]=getattr(snapshot,field)
                np.savez_compressed(out/'first_board.npz',**data)
            if engine.error_log:raise RuntimeError(str(engine.error_log[-1]))
            if writer.external_error:raise ValueError(writer.external_error)
            now=time.perf_counter()
            if now-last>=20:
                signals.flush();causes.flush();writer.file.flush()
                progress=dict(bundles=bundle,seconds=now-began,time=item.source_time,signals=sum(count.values()),counts=dict(count),memory=peak_memory())
                write_progress(out/'progress.json',progress)
                print(args.revision,bundle,sum(count.values()),round(now-began,1),flush=True);last=now
        result=dict(revision=args.revision,bundles=bundle,signals=sum(count.values()),counts=dict(count),notifications=writer.notifications,
                    seconds=time.perf_counter()-began,errors=plain(engine.error_log),code_hashes=hashes,capture=capture['capture_id'])
        result['source_unchanged']=hashes=={p.relative_to(program).as_posix():sha(p) for p in program.rglob('*.py') if '__pycache__' not in p.parts}
        (out/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        assert result['source_unchanged'];print('DONE',args.revision,bundle,sum(count.values()),flush=True)
    finally:signals.close();causes.close();writer.close()

if __name__=='__main__':main()
