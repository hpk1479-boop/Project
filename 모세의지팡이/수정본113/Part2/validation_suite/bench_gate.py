"""Long synthetic prefix benchmark; this is NOT an actual-market raw benchmark."""
import argparse,json,time,sys,struct,math
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--root',required=True);p.add_argument('--out',required=True);args=p.parse_args()
sys.path.insert(0,args.root)
from generic_backtest.market import GenericMarketCore
from generic_backtest.calendar import CalendarRegistry
from generic_backtest.contracts import InstrumentSpec
from generic_backtest.session_filter import TradingSessionFilter,default_definition
from generic_backtest import evaluation
from pit.models import TickRecord
inst=InstrumentSpec('TEST','SYNTHETIC_NOT_BROKER')
cal=CalendarRegistry({'kind':'UTC_GRID_RESEARCH_V1','explicit_research_choice':True})
bits=lambda x:struct.unpack('<Q',struct.pack('<d',x))[0]
rows=[]
for days in (1,7):
    ticks=[TickRecord('synthetic_gate',i+1,s,bits(100+math.sin(i/7)),bits(100.1+math.sin(i/7)),bits(0.),1,s*1000,2,bits(1.))
           for i,s in enumerate(range(0,days*86400,30))]
    for tfs in (('1m','3m','15m'),('3m','6m','15m'),('6m','15m'),('15m',)):
        optimized=hasattr(evaluation,'TimeframeCloseGate')
        base=evaluation.evaluation_base_tf(tfs) if optimized else '1m'
        for on in (False,True):
            definition=default_definition();definition['enabled']=on
            sessions=TradingSessionFilter(definition)
            gate=evaluation.TimeframeCloseGate(inst,cal,base) if optimized else evaluation.OneMinuteCloseGate(inst,cal)
            core=GenericMarketCore(inst,cal,0,{tf:20 for tf in tfs},'benchmark')
            callbacks=[0];checksum=[0.]
            def callback(view):
                callbacks[0]+=1
                checksum[0]+=sum(view.bars(tf)[-1].close for tf in tfs)
            start=time.perf_counter()
            for tick in ticks:
                v,_=core.step(tick);close=gate.on_tick(tick)
                if close is not None:
                    if optimized:v=evaluation.close_evaluation_view(v,base,close)
                    if sessions.allows(v.token.now_ns):callback(v)
            elapsed=time.perf_counter()-start
            rows.append({'days':days,'required_timeframes':tfs,'base_tf':base,'session_filter':on,
                         'raw_ticks':len(ticks),'gate_closes':gate.count,'callback_count':callbacks[0],
                         'wall_seconds':elapsed,'checksum_non_comparable_across_gate_semantics':checksum[0]})
            print(rows[-1],flush=True)
Path(args.out).write_text(json.dumps({'input_kind':'DETERMINISTIC_SYNTHETIC_30_SECOND_TICKS_NOT_REAL_RAW',
    'scope':'market ingestion + gate + scalar checksum callback, NOT complete WATCH',
    'rows':rows},indent=2))
