"""Related E2 processor/consumer checks, with current direct decision function decisions."""
import importlib.util
import shutil
import sys
import socket
from pathlib import Path
from types import SimpleNamespace
import numpy as np
import pytest
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'Part1/program'))
from event_engine import EventEngine,IngressSequencer,Kind,FeedSnapshot
from event_engine.fvg_state import FVGProcessor,FVGConsumer
from event_engine.sweep_state import SweepProcessor,SweepConsumer,EventList
from event_engine.indicator_consumer import IndicatorConsumer
from event_engine.domain_support import FactPort,plain
from event_engine.frames import BoardFrames,SelectionPort
from domain_clock import event_scope,time as clock
from staff_schema import legacy_frame,PIPE_VALUE_COLUMNS


@pytest.fixture
def domains(tmp_path,monkeypatch):
    def deny(*a,**k):raise AssertionError('E2 network blocked')
    for name in ('connect','connect_ex','sendto'):monkeypatch.setattr(socket.socket,name,deny)
    loaded={}
    for label,root in [('old',ROOT),('new',ROOT)]:
        folder=tmp_path/label;folder.mkdir()
        for path in (root/'Part1/program').glob('*.py'):shutil.copyfile(path,folder/path.name)
        for stem in ('monitor_OZ','strategy_FVG','strategy_SWEEP','strategy_INDICATOR'):
            name='e2_domains_'+label+'_'+stem
            spec=importlib.util.spec_from_file_location(name,folder/(stem+'.py'))
            module=importlib.util.module_from_spec(spec);monkeypatch.setitem(sys.modules,name,module)
            spec.loader.exec_module(module);loaded[label,stem]=module
    import durable_protocol,staff_compat
    loaded['protocol']=durable_protocol;loaded['compat']=staff_compat
    return loaded


def snap(bar=0,seq=1):
    rng=np.random.default_rng(319)
    values=np.full((650,len(PIPE_VALUE_COLUMNS)),100.,dtype='<f8')
    values[:,0]=100+np.cumsum(rng.normal(0,.6,650))
    values[:,3]=values[:,0]+rng.normal(0,.5,650)
    values[:,1]=np.maximum(values[:,0],values[:,3])+.4
    values[:,2]=np.minimum(values[:,0],values[:,3])-.4
    values[-1,1]=120;values[-1,2]=60
    values[:,PIPE_VALUE_COLUMNS.index('wonbi_upper')]=103
    values[:,PIPE_VALUE_COLUMNS.index('wonbi_lower')]=97
    for name in PIPE_VALUE_COLUMNS:
        if name.endswith(('_lower_out','_upper_out')):values[:,PIPE_VALUE_COLUMNS.index(name)]=np.nan
    t=np.arange(650,dtype='<i8')*60+1790341740+bar*60
    return FeedSnapshot(t,np.ones(650,dtype='<i8'),values,seq,'E2',{})


def post(engine,bar,seq,tfs=('1m',)):
    feeds={tf:snap(bar,seq) for tf in tfs}
    engine.ingress.post(Kind.MARKET_BUNDLE,source='staff',source_seq=seq,
        source_time=(1790380680+bar*60)*1000,payload={'symbol':'XAUUSD+','feeds':feeds})
    engine.run();return feeds


def test_fvg_canonical_transitions_and_resume(domains):
    new=domains['new','strategy_FVG'];protocol=domains['protocol']
    # FVG create/touch/fill/expiry rules are checked directly in test_numpy_processors.py.
    factory=lambda:EventEngine(IngressSequencer(),[FVGConsumer()],
        [FVGProcessor(new,protocol,initial=(('XAUUSD+','1m'),))])
    engine=factory();resumed=None
    for i in range(8):
        post(engine,i,i+1)
        if i==3:resumed=factory();resumed.restore(engine.checkpoint())
        elif resumed is not None:
            post(resumed,i,i+1)
            # Resident runtime identity is not checkpoint behavior. Compare the
            # lifecycle state and all signals produced after the checkpoint.
            assert resumed.processor_state['FVG_STATE']['core']==engine.processor_state['FVG_STATE']['core']
            assert [plain(e.payload) for e in resumed.signals]==[plain(e.payload) for e in engine.signals[-len(resumed.signals):]]
    assert any(plain(e.payload['content']['event'])['kind']=='FVG_TOUCH' for e in engine.signals)
    assert not engine.error_log


def test_sweep_cleanup_advances_only_the_observed_symbol(domains):
    new=domains['new','strategy_SWEEP'];protocol=domains['protocol']
    engine=EventEngine(IngressSequencer(),[SweepConsumer()],
        [SweepProcessor(new,protocol,domains['compat'],domains['new','monitor_OZ'])])
    def detector(wid,symbol):
        spec=new.SweepSpec.from_payload({'watch_id':wid,'symbol':symbol,'source_tf':'1m','levels':['PDH']})
        return new.ExternalLiquidityDetector(spec)
    engine.processor_state['SWEEP_STATE']['core']={'detectors':{'x':detector('x','XAUUSD+'),'b':detector('b','BTCUSD')}}
    post(engine,0,1)
    assert set(engine.processor_state['SWEEP_STATE']['core']['detectors'])=={'b'}
    engine.ingress.post(Kind.MARKET_BUNDLE,source='staff',source_seq=1,source_time=1790380680000,
        payload={'symbol':'BTCUSD','feeds':{'1m':snap()}})
    engine.run()
    assert not engine.processor_state['SWEEP_STATE']['core']['detectors']


def test_indicator_source_time_and_resume(domains,monkeypatch):
    module=domains['new','strategy_INDICATOR'];protocol=domains['protocol']
    def factory():return EventEngine(IngressSequencer(),[IndicatorConsumer(module,protocol,
            domains['compat'],domains['new','monitor_OZ'],initial=(('XAUUSD+','1m'),))])
    engine=factory();post(engine,0,1)
    assert engine.signals and not engine.error_log
    resumed=factory();resumed.restore(engine.checkpoint())
    for i in range(1,5):post(engine,i,i+1);post(resumed,i,i+1)
    # Cached Fact arrays/runtime objects are reconstructible internals. State
    # transitions and externally emitted facts must survive the checkpoint.
    assert engine.strategy_state['INDICATOR']['core']==resumed.strategy_state['INDICATOR']['core']
    assert engine.strategy_state['INDICATOR']['pending']==resumed.strategy_state['INDICATOR']['pending']
    assert [plain(e.payload) for e in resumed.signals]==[plain(e.payload) for e in engine.signals[-len(resumed.signals):]]
    def denied():raise AssertionError('wall clock called in event scope')
    import time as real_time
    monkeypatch.setattr(real_time,'time',denied);monkeypatch.setattr(real_time,'monotonic',denied)
    with event_scope(1234000,'clock-test',{}):assert clock.time()==clock.monotonic()==1234
