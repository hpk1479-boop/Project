"""Additional exact baseline/final WATCH-plan, OHLC and TREND evidence.

Two independent package roots must run this file in separate interpreters.
Supplied finite TREND/OZ fixture frames are not broker raw or native terminal proof.
"""
from pathlib import Path
import argparse,sys,json,math,struct,time
p=argparse.ArgumentParser();p.add_argument('--root',required=True);p.add_argument('--out',required=True)
a=p.parse_args();sys.path.insert(0,a.root)
from generic_backtest.watch.compiler import compile_watch
from generic_backtest.watch.engines.inputs import exact_digest
from generic_backtest.watch.engines.trend import HistoricalTrendEngine
from generic_backtest.watch.engines.trend_math import TREND_METRIC_FIELDS
from oz_fixtures import Sequence
from generic_backtest.market import GenericMarketCore
from generic_backtest.calendar import CalendarRegistry
from generic_backtest.contracts import InstrumentSpec
from pit.models import TickRecord
report={'scope':'SYNTHETIC_CONTRACT_REGRESSION','plans':[],'ohlc':{},'trend':{}}
commands=[]
for tf in (1,3,6,15):
    commands += [f'{tf}분봉 마감 알려줘',f'{tf}분 올존 알려줘',f'15분 추세 상승이고 {tf}분 올존 알려줘',
                 f'{tf}분 6 HMA 17 HMA 골든크로스 알려줘',f'{tf}분 6 HMA 17 HMA 데드크로스 알려줘']
commands += ['주문 실행','__import__', '1분봉 마감; 삭제','1분 RSI 14 20 30 아무조건']
for text in commands:
    try:
        plan=compile_watch(text,'TEST');r={'text':text,'status':'COMPILED','digest':exact_digest(plan),'plan':plan}
    except Exception as exc:r={'text':text,'status':'REJECTED','error':str(exc)}
    report['plans'].append(r)
Path(a.out).write_text(json.dumps(report,ensure_ascii=False,indent=2))
# 1d, 30-second ticks. Compare all immutable bar fields and current quote/token.
start=1736726400
core=GenericMarketCore(InstrumentSpec('TEST','fixture'),CalendarRegistry({'kind':'UTC_GRID_RESEARCH_V1','explicit_research_choice':True}),start*10**9,{tf:4 for tf in ('1m','3m','6m','15m')},'identity-check')
bits=lambda x:struct.unpack('<Q',struct.pack('<d',x))[0]
digests=[]
for i in range(2880):
    s=start+i*30;v=100+math.sin(i*.27)*2
    tick=TickRecord('identity-check',i+1,s,bits(v),bits(v+.1),bits(v),1,s*1000,2,bits(1.))
    view,change=core.step(tick)
    digests.append(exact_digest((view,change)))
report['ohlc']={'observations':len(digests),'timeframes':['1m','3m','6m','15m'],'digests':digests,'sequence_digest':exact_digest(digests)}
Path(a.out).write_text(json.dumps(report,ensure_ascii=False,indent=2))
engines={tf:HistoricalTrendEngine('TEST',tf) for tf in ('1m','3m','6m')}
digests=[];positive=0;event_count=0;directions={};total=0.
for frames,token,_,_ in Sequence(count=240,rows=650,tfs=tuple(engines),scenario='equal'):
    values=[]
    for tf,engine in engines.items():
        t=time.perf_counter();result,events=engine.observe(frames[tf],token)
        metrics=engine.metrics(frames[tf],token,tuple(sorted(TREND_METRIC_FIELDS)))
        total+=time.perf_counter()-t
        if result:
            positive+=1;direction=result['trend'];directions[direction]=directions.get(direction,0)+1
        event_count+=len(events)
        values.append((tf,result,events,metrics,engine.current,engine.last_direction,engine.guard.last_token,engine.guard.signature))
    digests.append(exact_digest(values))
report['trend']={'observations':len(digests),'tf_evaluations':len(digests)*len(engines),'nonempty':positive,'events':event_count,'directions':directions,
                  'digests':digests,'sequence_digest':exact_digest(digests),'observe_and_metrics_wall_s':total}
Path(a.out).write_text(json.dumps(report,ensure_ascii=False,indent=2))
print(json.dumps({'plans':len(report['plans']),'accepted':sum(x['status']=='COMPILED' for x in report['plans']),
                  'OHLC':report['ohlc']['observations'],'TREND':{k:v for k,v in report['trend'].items() if k!='digests'}},ensure_ascii=False))
