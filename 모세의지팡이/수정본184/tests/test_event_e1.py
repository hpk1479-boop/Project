"""E1-only strategies; never wired to production or Telegram."""
import io
import sys
import socket
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import threading
import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
PROGRAM = ROOT/'Part1/program'
sys.path.insert(0, str(PROGRAM))
from event_engine import (EventEngine, IngressSequencer, Kind, Resolution, Signal,
                          TimerRequest, Subscriptions, Boundary, FeedSnapshot)
from event_engine.model import Input
from event_engine.replay import replay, select_inputs
from event_engine.staff_adapter import StaffIngressAdapter
from event_engine.static_rules import violations
from event_engine.facts import owner_table
import indicator_facts as facts
import staff_schema as wire


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError('E1 tests forbid actual network/Telegram')
    monkeypatch.setattr(socket.socket, 'connect', denied)
    monkeypatch.setattr(socket.socket, 'connect_ex', denied)
    monkeypatch.setattr(socket, 'create_connection', denied)
    monkeypatch.setattr(socket.socket, 'sendto', denied)


def snapshot(value=100., bar=0, seq=1):
    t=np.arange(30, dtype='<i8')*60+1790033400+bar*60
    x=np.full((30,len(wire.PIPE_VALUE_COLUMNS)), value, dtype='<f8')
    x[:,0]=np.arange(30)+value; x[:,1]=x[:,0]+2; x[:,2]=x[:,0]-1; x[:,3]=x[:,0]+.5
    x[:,wire.PIPE_VALUE_COLUMNS.index('wonbi_upper')]=x[:,0]+3
    x[:,wire.PIPE_VALUE_COLUMNS.index('wonbi_lower')]=x[:,0]-3
    return FeedSnapshot(t, np.ones(30,dtype='<i8'), x, seq, 'session:1', {'PRICE':True})


def market(timestamp, *, symbol='XAUUSD+', value=100., bar=0, seq=1):
    return Input('staff',seq,timestamp,Kind.MARKET_BUNDLE,
                 {'symbol':symbol,'feeds':{'1m':snapshot(value,bar,seq),'5m':snapshot(value+5,bar,seq)}},0)


def post(engine, item):
    engine.ingress.post(item.kind, source=item.source, source_seq=item.source_seq,
                        source_time=item.source_time,payload=item.payload)


class Probe:
    name='probe'
    def __init__(self, resolution=Resolution.TICK):self.resolution=resolution;self.seen=[]
    def subscriptions(self):
        return Subscriptions(timeframes=('1m','5m'),facts=('ATR14_GENERAL',),resolution=self.resolution,
                             boundaries=(Boundary('1m','close',130.),))
    def on_event(self,event,board,state,emit):
        symbol=event.payload['symbol']
        for tf in ('1m','5m'):board.fact('ATR14_GENERAL',symbol,tf)
        self.seen.append((event.kind,event.source_time,board.observed[(symbol,'1m')],board.observed[(symbol,'5m')]))
        state['calls']=state.get('calls',0)+1
        emit(Signal(symbol,'probe',{'direction':'LONG','text':'시험 신호','recipients':(11,22),
             'value':float(board.snapshot(symbol,'1m').values[-1,3])}))


class ClockProbe:
    name='clock'
    def __init__(self):self.seen=[]
    def subscriptions(self):return Subscriptions(timeframes=('1m','5m'))
    def on_event(self,event,board,state,emit):
        symbol=event.payload['symbol']
        self.seen.append((event.kind,event.source_time,board.observed[(symbol,'1m')],event.payload.get('key')))
        if event.kind==Kind.MARKET_BUNDLE and symbol=='XAUUSD+' and not state.get('requested'):
            state['requested']=True
            for key,due in [('early_b',1500),('early_a',1500),('equal',2000)]:
                emit(TimerRequest(symbol,due,key))


class FailingProbe:
    name='fails'
    def subscriptions(self):return Subscriptions(kinds=(Kind.MARKET_BUNDLE,))
    def on_event(self,event,board,state,emit):raise ValueError('controlled failure')


def new_engine(*strategies,**kwargs):return EventEngine(IngressSequencer(),strategies,retain_events=True,**kwargs)


def signal_values(engine):
    return [(e.source_time,dict(e.payload)) for e in engine.signals]


def test_sequences_atomic_bundle_and_deep_immutability():
    probe=Probe();e=new_engine(probe)
    for i in range(5):post(e,market(1000+i*1000,value=100+i,seq=i+1))
    e.run()
    assert [x.engine_seq for x in e.events]==list(range(1,len(e.events)+1))
    assert all(source==a==b for kind,source,a,b in probe.seen)
    assert len(probe.seen)==5 and len(e.signals)==5
    data=e.events[0].payload
    with pytest.raises(TypeError):data['symbol']='bad'
    with pytest.raises(ValueError):data['feeds']['1m'].values.setflags(write=True)
    with pytest.raises(ValueError):data['feeds']['1m'].values[0,0]=0
    assert e.events[0].payload['feeds']['1m'].values[0,0]==100
    assert e.events[-1].engine_seq > e.events[0].source_seq


def test_timer_before_after_ties_symbol_clock_and_prequeued_inputs():
    p=ClockProbe();e=new_engine(p)
    for item in [market(1000),market(9000,symbol='BTCUSD'),market(2000),market(2000,seq=3)]:post(e,item)
    e.run()
    timers=[x for x in p.seen if x[0]==Kind.TIMER]
    assert timers==[(Kind.TIMER,1500,1000,'early_b'),(Kind.TIMER,1500,1000,'early_a'),(Kind.TIMER,2000,2000,'equal')]
    assert [x.engine_seq for x in e.events]==list(range(1,len(e.events)+1))
    kinds=[(x.kind,x.source_time,x.payload.get('symbol')) for x in e.events]
    assert kinds.index((Kind.MARKET_BUNDLE,9000,'BTCUSD')) < kinds.index((Kind.TIMER,1500,'XAUUSD+'))
    assert kinds.index((Kind.TIMER,1500,'XAUUSD+')) < kinds.index((Kind.MARKET_BUNDLE,2000,'XAUUSD+'))


def test_atr_bits_shared_once_and_precise_invalidation():
    a=Probe();b=Probe();b.name='second';e=new_engine(a,b)
    first=market(1000);post(e,first);e.run()
    snap=first.payload['feeds']['1m']
    df=pd.DataFrame(snap.values,columns=wire.PIPE_VALUE_COLUMNS)
    expected=facts.add_atr14_feature(df.copy())['atr_14'].to_numpy()
    actual=e.board.view(e.processor_state,a).fact('ATR14_GENERAL','XAUUSD+','1m')
    assert expected.tobytes()==actual.tobytes()
    assert e.facts.computed[('XAUUSD+','1m','ATR14_GENERAL')]==1
    second=market(2000,seq=2);post(e,second);e.run()
    assert e.facts.computed[('XAUUSD+','1m','ATR14_GENERAL')]==1
    row=snap.values.copy();row[-1,1]+=9
    post(e,Input('staff',3,3000,Kind.MARKET_BUNDLE,{'symbol':'XAUUSD+','feeds':{'1m':FeedSnapshot(snap.time,snap.volume,row,3)}},0));e.run()
    assert e.facts.computed[('XAUUSD+','1m','ATR14_GENERAL')]==2
    assert e.facts.computed[('XAUUSD+','5m','ATR14_GENERAL')]==1
    post(e,market(4000,symbol='BTCUSD'));e.run()
    assert e.facts.computed[('XAUUSD+','1m','ATR14_GENERAL')]==2
    assert any(r['name']=='ATR14_GENERAL' and r['owner']=='indicator_facts' for r in owner_table([a,b]))


def test_strategy_error_isolation_reset_reenable_and_no_transport():
    bad=FailingProbe();good=Probe();logs=[];e=new_engine(bad,good,logger=logs.append)
    for i in range(8):post(e,market((i+1)*1000,seq=i+1))
    e.run()
    assert e.disabled=={'fails'} and e.status=='DEGRADED'
    assert len(logs)==5 and len([x for x in e.events if x.kind==Kind.STRATEGY_ERROR])==5
    assert len(good.seen)==8
    alerts=[x for x in e.signals if x.payload['condition_key']=='DEGRADED:fails']
    assert len(alerts)==1 and '이 전략의 조건은 현재 보장할 수 없음' in alerts[0].payload['content']['message']
    e.ingress.post(Kind.COMMAND,source='test',source_seq=1,source_time=9000,
                   payload={'command':'ENABLE_STRATEGY','strategy':'fails'})
    e.run();assert not e.disabled and e.failures['fails']==0
    original=bad.on_event
    bad.on_event=lambda *args:None
    post(e,market(10000));e.run();assert e.failures['fails']==0
    bad.on_event=original
    post(e,market(11000));e.run();assert e.failures['fails']==1


def test_checkpoint_processor_and_strategy_state_resume():
    class Processor:
        name='shared'
        def subscriptions(self):return Subscriptions(kinds=(Kind.MARKET_BUNDLE,))
        def on_event(self,event,board,state):state['count']=state.get('count',0)+1
    p=ClockProbe();e=new_engine(p,processors=[Processor()]);post(e,market(1000));e.run()
    checkpoint=e.checkpoint();clone=new_engine(ClockProbe(),processors=[Processor()]);clone.restore(checkpoint)
    for engine in (e,clone):post(engine,market(2000));engine.run()
    assert e.strategy_state==clone.strategy_state and e.processor_state==clone.processor_state
    assert e.processor_state=={'shared':{'count':2}}
    assert clone.strategies[0].seen==p.seen[1:]
    assert clone.events[0].engine_seq==checkpoint['seq']+1
    checkpoint['strategies']['clock']['requested']=False
    assert clone.strategy_state['clock']['requested'] is True


def test_three_replay_resolutions_and_approximation():
    items=[market(1000+i*1000,value=100+(i>1),bar=int(i>=4),seq=i+1) for i in range(6)]
    counts=[]
    for resolution in Resolution:
        e=new_engine(Probe());result=replay(e,items,resolution);counts.append(result['input_count'])
        assert result['approximate']==(resolution<Resolution.TICK)
    assert counts[0]<counts[1]<=counts[2]==6
    exact=new_engine(Probe(Resolution.CONDITION));tick=new_engine(Probe(Resolution.CONDITION))
    # E1 sample emits per observation, so lower resolution is deliberately not
    # claimed equivalent to all-tick signals; boundary transition test below.
    assert not replay(exact,items,Resolution.CONDITION)['approximate']


@pytest.fixture
def staff_cache(tmp_path):
    # Load the actual STAFF source in an isolated path so its log setup cannot
    # mutate the production source tree or any frozen baseline.
    source=tmp_path/'THE STAFF OF MOSES.py';source.write_bytes((PROGRAM/source.name).read_bytes())
    spec=importlib.util.spec_from_file_location('e1_staff_test',source)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module.StaffPipeCache('',health_session='E1',monotonic=lambda:0,gap_journal=tmp_path/'gaps.jsonl')


def test_pipe_staff_engine_and_replay_signals_identical(staff_cache,tmp_path):
    live=new_engine(Probe());adapter=StaffIngressAdapter(staff_cache,live.ingress)
    captured=[]
    for i in range(6):
        item=market((i+1)*1000,value=100+i,seq=i+1)
        frames=[wire.pack_v2('XAUUSD+',tf,s.time,s.volume,s.values,seq=i+1) for tf,s in item.payload['feeds'].items()]
        packet=wire.pack_bundle('XAUUSD+',frames,seq=i+1,sent_at_ms=item.source_time)
        stream=io.BytesIO(packet);adapter.receive_one(stream.read)
        live.run()
        captured.extend(Input(e.source,e.source_seq,e.source_time,e.kind,e.payload,0)
                        for e in live.events if e.kind==Kind.MARKET_BUNDLE and e.source_seq==i+1)
    backtest=new_engine(Probe());replay(backtest,captured)
    assert signal_values(live)==signal_values(backtest) and len(live.signals)==6
    with pytest.raises(EOFError):adapter.receive_one(io.BytesIO(packet[:-2]).read)


def test_ingress_concurrent_producers_no_drop_and_pressure():
    e=new_engine(queue_warning=2)
    def producer(n):
        for i in range(50):
            e.ingress.post(Kind.COMMAND,source=str(n),source_seq=i,source_time=i,payload={'value':i})
    threads=[threading.Thread(target=producer,args=(i,)) for i in range(4)]
    for t in threads:t.start()
    for t in threads:t.join()
    e.run()
    assert len([x for x in e.events if x.kind==Kind.COMMAND])==200
    assert [x.engine_seq for x in e.events]==list(range(1,len(e.events)+1))
    assert e.metrics.peak_queue==200
    assert any(x.kind==Kind.FEED_HEALTH and x.payload['warning']=='ENGINE_BACKLOG' for x in e.events)
    with pytest.raises(RuntimeError):e.ingress.number(object(),market(1000))


@pytest.mark.parametrize('code',[
    'import time as t\nx=t.time()', 'from datetime import datetime as d\nx=d.now()',
    'from time import monotonic as clock\nx=clock()', 'import random\nx=random.random()',
    'import numpy as np\nx=np.random.rand()', 'import socket\nx=socket.socket()',
    'open("x","w")', 'from pathlib import Path\nx=Path("x").read_text()',
    'board.fact("FVG_WILDER_ATR", "X", "1m")', 'engine._process(event)',
    'import requests as r\nr.get("http://example.invalid")', 'eval("1+1")',
    'from strategy_FVG import FVG_WILDER_ATR as atr', 'import strategy_FVG as f\nf._wilder_atr(h,l,c,14)'])
def test_static_checker_negative_controls(code):
    assert violations(code,module='sample_strategy',registry=facts.FACTS)


def test_production_core_static_contract_and_event_default():
    exempt={'metrics.py','capture_io.py','staff_adapter.py','static_rules.py','__init__.py'}
    for path in (PROGRAM/'event_engine').glob('*.py'):
        if path.name not in exempt:
            assert not violations(path.read_text('utf-8'),module=path.stem,registry=facts.FACTS,framework=True),path.name
    assert not violations((PROGRAM/'indicator_facts.py').read_text('utf-8'),module='indicator_facts',framework=True)
    # E3 deliberately replaces the disconnected polling default.
    supervisor=(PROGRAM.parent/'live_control.py').read_text('utf-8')
    assert 'PROGRAMS = [("EVENT ENGINE", "event_host.py", 0.0)]' in supervisor
    assert 'from event_engine' in (PROGRAM/'event_host.py').read_text('utf-8')
    ea=(PROGRAM/'MT5/THE_STAFF_OF_MOSES.mq5').read_text('utf-8')
    assert 'input int    STAFF_TIMER_MS          = 1000;' in ea


def test_native_windows_named_pipe_live_vs_replay(staff_cache):
    import ctypes
    import uuid
    from ctypes import wintypes
    k=ctypes.WinDLL('kernel32',use_last_error=True)
    k.CreateNamedPipeW.argtypes=[wintypes.LPCWSTR,wintypes.DWORD,wintypes.DWORD,wintypes.DWORD,
                                 wintypes.DWORD,wintypes.DWORD,wintypes.DWORD,ctypes.c_void_p]
    k.CreateNamedPipeW.restype=wintypes.HANDLE
    k.CreateFileW.argtypes=[wintypes.LPCWSTR,wintypes.DWORD,wintypes.DWORD,ctypes.c_void_p,
                           wintypes.DWORD,wintypes.DWORD,wintypes.HANDLE]
    k.CreateFileW.restype=wintypes.HANDLE
    for name in ['CloseHandle','DisconnectNamedPipe']:
        getattr(k,name).argtypes=[wintypes.HANDLE]
    k.ConnectNamedPipe.argtypes=[wintypes.HANDLE,ctypes.c_void_p]
    for name in ['ReadFile','WriteFile']:
        getattr(k,name).argtypes=[wintypes.HANDLE,ctypes.c_void_p,wintypes.DWORD,
                                  ctypes.POINTER(wintypes.DWORD),ctypes.c_void_p]
    name=r'\\.\pipe\event-e1-test-'+uuid.uuid4().hex
    handle=k.CreateNamedPipeW(name,1,0,1,65536,65536,1000,None)
    assert handle not in (None,ctypes.c_void_p(-1).value)
    item=market(1000)
    frames=[wire.pack_v2('XAUUSD+',tf,s.time,s.volume,s.values,seq=1) for tf,s in item.payload['feeds'].items()]
    raw=wire.pack_bundle('XAUUSD+',frames,seq=1,sent_at_ms=1000)
    failures=[];reader_finished=threading.Event()
    def writer():
        client=k.CreateFileW(name,0x40000000,0,None,3,0,None)
        try:
            assert client not in (None,ctypes.c_void_p(-1).value)
            sent=wintypes.DWORD()
            assert k.WriteFile(client,raw,len(raw),ctypes.byref(sent),None)
            assert sent.value==len(raw)
            # Keep the writer connected until the server has consumed the
            # bytes. Closing before ConnectNamedPipe can race into ERROR_NO_DATA.
            assert reader_finished.wait(10)
        except BaseException as exc:failures.append(exc)
        finally:k.CloseHandle(client)
    thread=threading.Thread(target=writer);thread.start()
    try:
        assert k.ConnectNamedPipe(handle,None) or ctypes.get_last_error()==535
        def reader(size):
            parts=[];remaining=size
            while remaining:
                buf=ctypes.create_string_buffer(remaining);read=wintypes.DWORD()
                assert k.ReadFile(handle,buf,remaining,ctypes.byref(read),None)
                assert read.value
                parts.append(buf.raw[:read.value]);remaining-=read.value
            return b''.join(parts)
        live=new_engine(Probe());StaffIngressAdapter(staff_cache,live.ingress).receive_one(reader);live.run()
        bt=new_engine(Probe());replay(bt,[Input(e.source,e.source_seq,e.source_time,e.kind,e.payload,0)
                                        for e in live.events if e.kind==Kind.MARKET_BUNDLE])
        assert signal_values(live)==signal_values(bt) and len(live.signals)==1
    finally:
        reader_finished.set()
        k.DisconnectNamedPipe(handle);k.CloseHandle(handle);thread.join(5)
    assert not thread.is_alive() and not failures


def test_condition_boundary_replay_matches_tick_notifications():
    class Edge:
        name='edge'
        def subscriptions(self):
            return Subscriptions(timeframes=('1m',),resolution=Resolution.CONDITION,
                                 boundaries=(Boundary('1m','close',130.),),kinds=(Kind.MARKET_BUNDLE,))
        def on_event(self,event,board,state,emit):
            sign=np.sign(board.snapshot(event.payload['symbol'],'1m').values[-1,3]-130)
            if state.get('sign')!=sign:
                emit(Signal(event.payload['symbol'],'cross',{'direction':int(sign),'text':'cross','recipients':(1,)}))
            state['sign']=sign
    inputs=[market(1000+i*1000,value=x,seq=i+1) for i,x in enumerate([100,100.1,101,101.1,100,100.2])]
    tick=new_engine(Edge());condition=new_engine(Edge())
    replay(tick,inputs,Resolution.TICK);result=replay(condition,inputs,Resolution.CONDITION)
    assert signal_values(tick)==signal_values(condition)
    assert len(tick.signals)==3 and result['input_count']==3 and not result['approximate']


def test_health_gap_reconnect_and_duplicate_ingress(staff_cache):
    e=new_engine();a=StaffIngressAdapter(staff_cache,e.ingress);s=snapshot()
    def raw(seq):return wire.pack_bundle('BTCUSD',[wire.pack_v2('BTCUSD','1m',s.time,s.volume,s.values,seq=seq)],seq=seq,sent_at_ms=1000*seq)
    a.publish(raw(1));a.publish(raw(3));a.publish(raw(3));e.run()
    assert len([x for x in e.events if x.kind==Kind.MARKET_BUNDLE])==2
    gaps=[x for x in e.events if x.kind==Kind.FEED_GAP]
    assert len(gaps)==1 and gaps[0].payload['first_missing_seq']==2 and gaps[0].payload['last_missing_seq']==2
    a.reconnect(symbol='BTCUSD',source_time=4000)
    a.publish(raw(1),source_time=5000);e.run()
    assert e.board._feeds[('BTCUSD','1m')].seq==1
    assert any(x.kind==Kind.FEED_HEALTH and x.payload['status']=='RECONNECT' for x in e.events)


def test_fact_read_is_shared_and_processors_precede_consumers():
    order=[]
    class Processor:
        name='phase_processor'
        def subscriptions(self):return Subscriptions(kinds=(Kind.MARKET_BUNDLE,),facts=('ATR14_GENERAL',))
        def on_event(self,event,board,state):
            board.fact('ATR14_GENERAL','XAUUSD+','1m')
            assert e.facts.computed[('XAUUSD+','1m','ATR14_GENERAL')]==1
            state['ready']=True;order.append('processor')
    class Consumer(Probe):
        def subscriptions(self):
            return Subscriptions(timeframes=('1m',),facts=('ATR14_GENERAL',),processor_states=('phase_processor',))
        def on_event(self,event,board,state,emit):
            board.fact('ATR14_GENERAL','XAUUSD+','1m')
            assert e.facts.computed[('XAUUSD+','1m','ATR14_GENERAL')]==1
            assert board.processor('phase_processor')['ready']
            with pytest.raises(TypeError):board.processor('phase_processor')['ready']=False
            order.append('consumer')
    e=new_engine(Consumer(),processors=[Processor()]);post(e,market(1000));e.run()
    assert order==['processor','consumer']


def test_all_input_kinds_seq_and_startup_config():
    e=new_engine()
    for n,kind in enumerate([Kind.CONFIG,Kind.COMMAND,Kind.EXTERNAL_REPLY,Kind.FEED_HEALTH,Kind.FEED_GAP]):
        e.ingress.post(kind,source='test',source_seq=n,source_time=1000+n,payload={'data':n})
    e.run();assert [x.engine_seq for x in e.events]==[1,2,3,4,5]
    e.ingress.post(Kind.CONFIG,source='test',source_seq=6,source_time=2000,payload={})
    with pytest.raises(ValueError,match='startup-only'):e.run()


def test_fact_owner_is_module_not_spoofable_strategy_name(monkeypatch):
    name='E1_PRIVATE_TEST'
    monkeypatch.setitem(facts.FACTS,name,facts.FactSpec(name,('@high',),lambda f,_:f.df['high'],
                                                    owner='private_owner',shared=False))
    class Pretender:
        name='private_owner'
        def subscriptions(self):return Subscriptions(facts=(name,))
    with pytest.raises(ValueError,match='private Fact'):new_engine(Pretender())


def test_unknown_schema_keeps_staff_unavailable_semantics(staff_cache):
    e=new_engine();a=StaffIngressAdapter(staff_cache,e.ingress);s=snapshot()
    raw=wire.pack_v2('BTCUSD','1m',s.time,s.volume,s.values,seq=1)
    a.publish(raw,source_time=1000)
    header=list(wire.WIRE_HEADER.unpack(raw[:wire.WIRE_HEADER.size]));header[7]=0xABCDEF01
    bad=wire.WIRE_HEADER.pack(*header)+raw[wire.WIRE_HEADER.size:]
    with pytest.raises(wire.UnknownWireSchema):a.receive_one(io.BytesIO(bad).read,source_time=2000)
    e.run()
    assert staff_cache.health('BTCUSD',['1m'])['1m']['status']=='UNAVAILABLE'
    assert e.events[-1].kind==Kind.FEED_HEALTH and e.events[-1].payload['status']=='UNAVAILABLE'
