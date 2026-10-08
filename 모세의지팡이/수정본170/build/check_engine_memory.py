"""Twelve source days of fixed-scope synthetic LIVE events; no network or sleep."""
from pathlib import Path
import gc,json,logging,os,sys
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'검증결과/engine_optimization/memory'
sys.path[:0]=[str(ROOT/'Part1/program'),str(ROOT/'Part2')]

def main():
    OUT.mkdir(parents=True,exist_ok=True);os.environ['MOSES_LOG_DIRECTORY']=str(OUT/'logs')
    from event_backtest.system import deny_network,process_memory
    deny_network();logging.disable(logging.CRITICAL)
    from event_application import create_event_engine
    from event_engine.model import Kind
    from event_engine.domain_support import plain
    from event_composer_domain import load_config
    config=load_config(str(ROOT/'Part1/program/config.txt'))
    config.update(TELEGRAM_TOKEN='OFFLINE',TELEGRAM_CHAT_ID='offline',GEMINI_API_KEY='',STAFF_ALLOWED_SYMBOLS='XAUUSD+',TARGET_SYMBOLS='XAUUSD+')
    engine=create_event_engine(config,symbols=('XAUUSD+',),selection=['SPECIAL1'],backtest=False)
    engine.retain_signals=False;counts=[0];engine.signal_sink=lambda event:counts.__setitem__(0,counts[0]+1)
    samples=[];seq=0;start=1790035200
    for day in range(12):
        for minute in range(1440):
            seq+=1;t=start+day*86400+minute*60
            raw={'strategy':'TREND','kind':'TREND_STATE','symbol':'XAUUSD+','source_tf':'15m',
                 'trend':'UP' if minute%2 else 'DOWN','direction':'LONG' if minute%2 else 'SHORT',
                 'event_time':t,'bar_time':t,'event_id':f'memory:{seq}','fact_revision':[1,seq],
                 'hma50_slope':1. if minute%2 else -1.,'sma20_slope':1. if minute%2 else -1.}
            engine.ingress.post(Kind.SIGNAL,source='synthetic_fact_processor',source_seq=seq,source_time=t*1000,
                payload={'symbol':'XAUUSD+','strategy':'INDICATOR','signal_id':f'test:{seq}',
                         'content':{'type':'DOMAIN_FACT','family':'TREND','event':raw}})
            engine.run()
        if engine.error_log:raise RuntimeError(str(plain(engine.error_log)))
        kernel=engine.strategy_state['COMPOSER']['kernels']['XAUUSD+'];m=kernel.manager
        gc.collect()
        sample={'day':day+1,'input_events':seq,'receipts':len(m._receipts._records),
                'fact_scopes':len(m._fact_revisions._records),'signatures':len(m._signature_records._records),
                'notification_dedup':len(kernel.notifier.deliveries),
                'records_bytes':sum(len(r.export_json().encode()) for r in (m._receipts,m._fact_revisions,m._signature_records)),
                'rss_bytes':process_memory(peak=False)}
        samples.append(sample);print(sample,flush=True)
    assert len({s['receipts'] for s in samples[4:]})==1
    assert len({s['fact_scopes'] for s in samples[4:]})==1
    (OUT/'result.json').write_text(json.dumps({'samples':samples,'source_days':12,'network_blocked':True,
        'errors':plain(engine.error_log),'signals':counts[0],
        'scope':'fixed LIVE Composer registrations; minute source-time TREND transitions; no output retained',
        'plateau':True},ensure_ascii=False,indent=2),encoding='utf-8')

if __name__=='__main__':main()
