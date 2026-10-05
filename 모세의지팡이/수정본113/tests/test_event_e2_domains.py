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
    old=domains['old','strategy_FVG'];new=domains['new','strategy_FVG'];protocol=domains['protocol']
    oracle=old.FVGEngine.__new__(old.FVGEngine)
    oracle._last_closed_time={};oracle._touch_state={};oracle._active_zones={}
    oracle._seen_created=set();oracle._initialized_keys=set()
    old_stream={};expected=[]
    factory=lambda:EventEngine(IngressSequencer(),[FVGConsumer()],
        [FVGProcessor(new,protocol,initial=(('XAUUSD+','1m'),))])
    engine=factory();resumed=None
    for i in range(8):
        oracle.manager=FactPort(protocol,'FVG',old_stream)
        raw=legacy_frame('XAUUSD+','1m',snap(i,i+1))
        oracle.process_watch_result(oracle.evaluate('XAUUSD+','1m',raw))
        expected.extend(oracle.manager.events)
        post(engine,i,i+1)
        assert [plain(e.payload['content']['event']) for e in engine.signals]==expected
        assert engine.processor_state['FVG_STATE']['core']['active_zones']==oracle._active_zones
        if i==3:resumed=factory();resumed.restore(engine.checkpoint())
        elif resumed is not None:
            post(resumed,i,i+1)
            # Resident runtime identity is not checkpoint behavior. Compare the
            # lifecycle state and all signals produced after the checkpoint.
            assert resumed.processor_state['FVG_STATE']['core']==engine.processor_state['FVG_STATE']['core']
            assert [plain(e.payload) for e in resumed.signals]==[plain(e.payload) for e in engine.signals[-len(resumed.signals):]]
    assert any(e['kind']=='FVG_TOUCH' for e in expected)
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


def test_sweep_canonical_transitions_and_resume(domains):
    old=domains['old','strategy_SWEEP'];new=domains['new','strategy_SWEEP'];protocol=domains['protocol']
    oz=domains['new','monitor_OZ'];compat=domains['compat']
    payload={'watch_id':'test','symbol':'XAUUSD+','source_tf':'1m','levels':['PDH','PDL']}
    factory=lambda:EventEngine(IngressSequencer(),[SweepConsumer()],
        [SweepProcessor(new,protocol,compat,oz,initial=(payload,))])
    engine=factory();oracle=old.SweepEngine.__new__(old.SweepEngine)
    oracle.detectors={};oracle._fingerprints={};oracle._restored_pending_sync=set()
    oracle._state_dirty=False;oracle._source_health={};oracle.events=EventList();oracle._level_cache=None
    stream={};expected=[];resumed=None
    for i in range(8):
        feeds=post(engine,i,i+1,('1m','1d','4h','8h','5m'))
        view=SimpleNamespace(feeds={('XAUUSD+',tf):s for tf,s in feeds.items()},
                             snapshot=lambda symbol,tf:feeds[tf])
        oracle.staff=SelectionPort(BoardFrames(view,compat,oz));oracle.manager=FactPort(protocol,'SWEEP',stream)
        oracle.run_spec_once(old.SweepSpec.from_payload(payload));expected.extend(oracle.manager.events)
        assert [plain(e.payload['content']['event']) for e in engine.signals]==expected
        current=engine.processor_state['SWEEP_STATE']['core']['detectors']['test']
        assert current.export_state()==oracle.detectors['test'].export_state()
        if i==3:resumed=factory();resumed.restore(engine.checkpoint())
        elif resumed is not None:
            post(resumed,i,i+1,('1m','1d','4h','8h','5m'))
            assert resumed.processor_state['SWEEP_STATE']['core']['detectors']['test'].export_state()==current.export_state()
    assert expected
    assert not engine.error_log


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
