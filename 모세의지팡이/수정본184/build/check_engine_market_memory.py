"""Fourteen synthetic market days through LIVE STAFF/engine, all strategies."""
from pathlib import Path
import gc,io,json,logging,os,sys,threading,time
ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'검증결과/engine_optimization/market_memory'
sys.path[:0]=[str(ROOT/'Part1/program'),str(ROOT/'Part2')]

def main():
    OUT.mkdir(parents=True,exist_ok=True);os.environ['MOSES_LOG_DIRECTORY']=str(OUT/'logs')
    from event_backtest.system import deny_network,process_memory
    deny_network();logging.disable(logging.CRITICAL)
    from event_application import create_event_engine
    from event_host import load_staff
    from event_pipe_host import PipeReceiver
    from event_engine.staff_adapter import StaffIngressAdapter
    from event_engine.domain_support import plain
    from event_composer_domain import load_config
    from part1_host.synthetic import SyntheticMarket,TIMEFRAMES
    from part1_host.wire_v2 import Publisher
    start=1790035200;market=SyntheticMarket('XAUUSD+',start,start+14*86400,history_days=30,seed=0)
    config=load_config(str(ROOT/'Part1/program/config.txt'))
    config.update(TELEGRAM_TOKEN='OFFLINE',TELEGRAM_CHAT_ID='offline',GEMINI_API_KEY='',STAFF_ALLOWED_SYMBOLS='XAUUSD+',TARGET_SYMBOLS='XAUUSD+')
    engine=create_event_engine(config,symbols=('XAUUSD+',),selection=['ALL'],backtest=False)
    engine.retain_signals=False;counts={'signals':0,'notifications':0}
    def sink(event):
        counts['signals']+=1
        counts['notifications']+=int(event.payload.get('content',{}).get('type')=='NOTIFICATION')
    engine.signal_sink=sink
    staff=load_staff();clock=[0.];cache=staff.StaffPipeCache('',monotonic=lambda:clock[0],gap_journal=OUT/'gaps.jsonl')
    adapter=StaffIngressAdapter(cache,engine.ingress);receiver=PipeReceiver(staff,cache,adapter,('XAUUSD+',),threading.Event())
    publisher=Publisher('XAUUSD+');samples=[];began=time.perf_counter()
    for day in range(14):
        for minute in range(0,1440,15):
            second=start+day*86400+minute*60
            raw=publisher.bundle([(tf,market.payload(tf,second)) for tf in TIMEFRAMES]);clock[0]+=.001
            receiver.receive(io.BytesIO(raw).read,lambda x:None,source_time=second*1000);engine.run()
            if engine.error_log:raise RuntimeError(str(plain(engine.error_log)))
        kernel=engine.strategy_state['COMPOSER']['kernels']['XAUUSD+'];m=kernel.manager
        gc.collect()
        sample={'day':day+1,**counts,'rss_bytes':process_memory(peak=False),
            'receipts':len(m._receipts._records),'fact_scopes':len(m._fact_revisions._records),
            'signatures':len(m._signature_records._records),'notification_dedup':len(kernel.notifier.deliveries),
            'watch_links':len(m._watch_message_links),'active_children':len(m._active_children),
            'records_bytes':sum(len(r.export_json().encode()) for r in (m._receipts,m._fact_revisions,m._signature_records))}
        samples.append(sample);print(sample,flush=True)
    (OUT/'result.json').write_text(json.dumps({'samples':samples,'source_days':14,'bundles':14*96,'interval_seconds':900,
        'network_blocked':True,'errors':plain(engine.error_log),'elapsed_seconds':time.perf_counter()-began,
        'scope':'all strategies; real PipeReceiver/STAFF; no notification transport; source-time market sampled each15min'},ensure_ascii=False,indent=2),encoding='utf-8')

if __name__=='__main__':main()
