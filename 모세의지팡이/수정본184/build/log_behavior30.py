"""Offline real-consumer validation; old host remains an independent full copy."""
from pathlib import Path
import argparse,collections,gzip,hashlib,io,json,logging,sys,time,threading,types,os
ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'검증결과/log_restore'

def main():
    p=argparse.ArgumentParser();p.add_argument('mode',choices=('live','replay'));p.add_argument('case',choices=('synthetic240','day','timer239','filtered_day'))
    p.add_argument('--revision',default='30',choices=('22','30'));p.add_argument('--label',default='');a=p.parse_args()
    program=ROOT/'Part1/program' if a.revision=='30' else OUT/'hosts/revision22/Part1/program'
    sys.path[:0]=[str(program),str(ROOT/'Part2')]
    from event_backtest.system import deny_network
    deny_network();logging.disable(logging.CRITICAL)
    from event_backtest.settings import settings,milliseconds
    from event_backtest.bridge import CaptureInputs
    from event_engine.capture_io import capture_bundles
    from event_engine.staff_adapter import StaffIngressAdapter
    from event_pipe_host import PipeReceiver
    from event_engine.model import Kind,Resolution
    from event_engine.replay import select_inputs
    from event_engine.domain_support import plain
    from event_application import create_event_engine
    from event_host import EventHost,load_staff
    from event_composer_domain import load_config
    from command_interpreter import CommandInterpreter
    from staff_golden import scenario
    out=OUT/'behavior'/f'{a.revision}_{a.case}_{a.mode}{a.label}';out.mkdir(parents=True,exist_ok=True)
    os.environ['MOSES_LOG_DIRECTORY']=str(out/'logs')
    if (out/'result.json').exists():raise FileExistsError('completed behavior evidence exists')
    hashes=lambda:{x.relative_to(program).as_posix():hashlib.sha256(x.read_bytes()).hexdigest() for x in program.rglob('*.py') if '__pycache__' not in x.parts}
    before=hashes();config=load_config(str(program/'config.txt'));symbol='XAUUSD+'
    config.update(STAFF_ALLOWED_SYMBOLS=symbol,TARGET_SYMBOLS=symbol,TELEGRAM_TOKEN='OFFLINE',TELEGRAM_CHAT_ID='OFFICIAL',GEMINI_API_KEY='',GEMINI_FALLBACK_ENABLED='false',ECONOMY_ENABLED='false')
    engine=create_event_engine(config,symbols=(symbol,),enabled_specials=tuple(f'SPECIAL{i}' for i in range(1,8)),trigger_overrides={'SPECIAL7':'무지성 올존'},selection=['ALL'],backtest=True)
    from module_diagnostics import configure
    diagnostics=configure(out)
    engine.diagnostics=diagnostics
    engine.retain_signals=False
    def transport(data):return types.SimpleNamespace(status_code=200,json=lambda:{'ok':True,'result':{'message_id':1}})
    host=EventHost(engine,config,CommandInterpreter(config,program/'command_aliases.json'),transport=transport)
    counts=collections.Counter();digest=hashlib.sha256();alerts=[];bundle=0
    handle=gzip.open(out/'signals.jsonl.gz','wt',encoding='utf-8',compresslevel=1)
    def accept(event):
        payload=plain(event.payload);record={'time':event.source_time,**payload}
        line=json.dumps(record,ensure_ascii=False,sort_keys=True,separators=(',',':'));digest.update(line.encode());handle.write(line+'\n')
        content=payload['content'];family=content.get('family') or content.get('type') or payload['strategy'];counts[family]+=1
        if content.get('type')=='NOTIFICATION':alerts.append(record)
        host._signal(event)
    engine.signal_sink=accept
    start=scenario.START*1000 if a.case=='synthetic240' else milliseconds('2026-09-25')
    for i,(chat,text) in enumerate(scenario.WATCHES):engine.ingress.post(Kind.COMMAND,source='commands',source_seq=i,source_time=start-1,payload={'symbol':symbol,'chat_id':chat,'text':text})
    engine.run();host.drain_outputs();assert not engine.error_log,engine.error_log
    clock=[0.];staff=load_staff();cache=staff.StaffPipeCache('',health_session='SCHEMA',monotonic=lambda:clock[0],gap_journal=out/'gaps.jsonl')
    if a.case=='synthetic240':
        from part1_host.wire_v2 import Publisher
        from part1_host.synthetic import TIMEFRAMES
        from event_backtest.bridge import Collector
        collector=Collector();adapter=StaffIngressAdapter(cache,collector);receiver=PipeReceiver(staff,cache,adapter,(symbol,),threading.Event(),diagnostics=diagnostics)
        market=scenario.market();publisher=Publisher(symbol)
        def source():
            for second in range(scenario.START,scenario.START+240):
                raw=publisher.bundle([(tf,market.payload(tf,second)) for tf in TIMEFRAMES]);clock[0]+=.001
                if a.mode=='live':receiver.receive(io.BytesIO(raw).read,lambda x:None,source_time=second*1000)
                else:adapter.publish(raw,source_time=second*1000)
                pending=collector.pending;collector.pending=[];yield from pending
        inputs=source()
    else:
        if a.revision=='22':
            info=json.loads((ROOT/'검증결과/part2_connection/day_BAR.json').read_text('utf-8'))[0];path=Path(settings()['warehouse'])/info['path']
        else:path=OUT/'inputs/TIMER' if a.case in ('timer239','filtered_day') else ROOT/'검증결과/schema_cleanup/captures/day_bar_a'
        end=start+86400000
        if a.case=='timer239':
            start=next(iter(capture_bundles(path)))[0];end=start+239000
        inputs=CaptureInputs(staff,cache,[path],transport=a.mode,clock=clock,start_ms=start,end_ms=end)
        if a.case=='filtered_day':inputs=select_inputs(inputs,tuple(c.subscriptions() for c in engine.strategies+engine.processors),Resolution.BAR_CLOSE)
    began=last=time.perf_counter()
    try:
        for item in inputs:
            if item.kind==Kind.MARKET_BUNDLE:bundle+=1
            engine.ingress.post(item.kind,source=item.source,source_seq=item.source_seq,source_time=item.source_time,payload=item.payload)
            engine.run();host.drain_outputs()
            if engine.error_log:raise RuntimeError(str(plain(engine.error_log)))
            if time.perf_counter()-last>20:
                print(a.revision,a.case,a.mode,bundle,sum(counts.values()),flush=True);last=time.perf_counter()
        result={'revision':a.revision,'mode':a.mode,'case':a.case,'bundles':bundle,'signals':sum(counts.values()),'signal_sha256':digest.hexdigest(),
            'counts':dict(counts),'alerts':alerts,'deliveries':host.output.results,'errors':plain(engine.error_log),
            'source_hashes':before,'source_unchanged':before==hashes(),'wonbi_sigma':float(config.get('WONBI_SIGMA',3)),
            'seconds':time.perf_counter()-began,'purpose':'전체 감시 진단; 선택 실행 백테스트 시간 아님'}
        result['module_logs']={k:len(v) for k,v in diagnostics.buffers.items()}
        result['module_states']=diagnostics.snapshot()['modules']
        diagnostics.close()
        (out/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        assert result['source_unchanged'];print('DONE',a.revision,a.case,a.mode,bundle,result['signals'],len(alerts),flush=True)
    finally:handle.close()

if __name__=='__main__':main()
