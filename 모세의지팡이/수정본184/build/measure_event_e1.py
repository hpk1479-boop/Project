"""One reference replay per preserved actual capture, including test strategies."""
import importlib.util,sys,socket,shutil
import numpy as np
from event_e1_common import *
sys.path.insert(0,str(ROOT/'Part1/program'))
from event_engine import EventEngine,IngressSequencer,Subscriptions,Signal,Kind
from event_engine.model import Input
from event_engine.replay import replay
from event_engine.capture_io import capture_bundles
from event_engine.staff_adapter import StaffIngressAdapter

def denied(*args,**kwargs):raise AssertionError('Network forbidden during E1 measurement')
socket.socket.connect=denied;socket.socket.connect_ex=denied;socket.socket.sendto=denied

class Probe:
    name='actual_probe'
    def subscriptions(self):return Subscriptions(timeframes=('1m','5m'),facts=('ATR14_GENERAL',),kinds=(Kind.MARKET_BUNDLE,))
    def on_event(self,event,board,state,emit):
        symbol=event.payload['symbol'];state['calls']=state.get('calls',0)+1
        emit(Signal(symbol,'bar_state',{'close':float(board.snapshot(symbol,'1m').values[-1,3]),
            'text':'E1 시험 알림','direction':'LONG','recipients':(0,)}))

if (OUT/'reference_measurement.json').exists():
    backup=OUT/'reference_measurement_development.json'
    if not backup.exists():shutil.copy2(OUT/'reference_measurement.json',backup)
folder=OUT/'reference_measurement_final';folder.mkdir(exist_ok=False)
source=folder/'THE STAFF OF MOSES.py';shutil.copy2(ROOT/'Part1/program'/source.name,source)
spec=importlib.util.spec_from_file_location('event_e1_staff_measure',source)
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
results=[]
for case in ['final_mt5_sigma3_v2','btc_weekend_previous_sigma3_v2']:
    info=read(ROOT/'검증결과/staff_s8'/case/'result.json')
    path=ROOT/'검증결과/staff_s8'/case/Path(info['retained_export']).name
    clock=[0.]
    cache=m.StaffPipeCache('',health_session='E1',monotonic=lambda:clock[0],gap_journal=folder/(case+'_gaps.jsonl'))
    collector=IngressSequencer();adapter=StaffIngressAdapter(cache,collector)
    # Decode input once; no timing claim for the file loader or STAFF receiver.
    owner=object();collector.bind(owner);inputs=[]
    for observed,raw in capture_bundles(path):
        clock[0]=observed/1000;adapter.publish(raw)
        while len(collector):inputs.append(collector.take_input(owner))
    engine=EventEngine(IngressSequencer(),[Probe()],collect_timings=True)
    replay(engine,inputs)
    # Same decoded observation flow also passes the live boundary via STAFF.
    # Signals from a direct ingress feed must be identical to replay without
    # rerunning the timed engine; this second run is parity, not a performance sample.
    live=EventEngine(IngressSequencer(),[Probe()])
    for item in inputs:
        live.ingress.post(item.kind,source=item.source,source_seq=item.source_seq,source_time=item.source_time,payload=item.payload)
        live.run()
    signature=lambda e:[{'source_time':v.source_time,'signal_id':v.payload['signal_id'],'content':dict(v.payload['content'])} for v in e.signals]
    assert signature(engine)==signature(live)
    ns=np.asarray(engine.metrics.bundle_ns,dtype=float)
    result={'case':case,'symbol':info['symbol'],'capture':str(path.relative_to(ROOT)),
        'engine_source_hashes':{p.name:sha(p) for p in (ROOT/'Part1/program/event_engine').glob('*.py')},
        'samples':1,'bundles':len(ns),'mean_ms':float(ns.mean()/1e6),'p99_ms':float(np.percentile(ns,99)/1e6),
        'max_ms':float(ns.max()/1e6),'scope':'Board commit + Fact invalidation/calculation + test strategy + SIGNAL processing; input file/STAFF decode excluded',
        'signal_count':len(engine.signals),'live_replay_equal':True,'first_source_time':inputs[0].source_time,
        'last_source_time':inputs[-1].source_time,'source_hashes':{p.name:sha(p) for p in path.glob('pipe_*.bin')}}
    write(folder/(case+'_signals.json'),signature(engine));results.append(result);print(result,flush=True)
write(OUT/'reference_measurement.json',results)
