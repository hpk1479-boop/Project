"""Revision25 semantics: lazy work, resident records and canonical ingress."""
from pathlib import Path
from types import SimpleNamespace
import io,json,socket,sys
import numpy as np
import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'Part1/program'),str(ROOT/'Part2')]
from event_engine import EventEngine,IngressSequencer,FeedSnapshot,Kind,Subscriptions,Signal
import staff_schema as wire

@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def deny(*a,**k):raise AssertionError('real network is forbidden')
    for name in ('connect','connect_ex','sendto'):monkeypatch.setattr(socket.socket,name,deny)
    monkeypatch.setattr(socket,'create_connection',deny)

def snapshot(seq=1):
    times=np.arange(30,dtype='<i8')*60+1756684800
    values=np.full((30,len(wire.PIPE_VALUE_COLUMNS)),10.,dtype='<f8')
    values[:,0]=np.arange(30)+100;values[:,1]=values[:,0]+2;values[:,2]=values[:,0]-2;values[:,3]=values[:,0]+1
    return FeedSnapshot(times,np.ones(30,dtype='<i8'),values,seq,'test:1',{'PRICE':True,'WONBI':True})

def test_part3_is_not_an_execution_dependency():
    import ast
    # Part3 is now a sibling web UI, but neither runtime may import it.
    for source in (ROOT/'Part1/program', ROOT/'Part2/event_backtest'):
        for path in source.rglob('*.py'):
            tree=ast.parse(path.read_bytes())
            assert not any(
                isinstance(node,ast.Import) and any(alias.name=='Part3' or alias.name.startswith('Part3.')
                    for alias in node.names) or
                isinstance(node,ast.ImportFrom) and node.module and
                (node.module=='Part3' or node.module.startswith('Part3.'))
                for node in ast.walk(tree)),path
    tree=ast.parse((ROOT/'Part2/validation_suite/test_staff_s1.py').read_bytes())
    assert not any(isinstance(node,ast.Constant) and node.value=='Part3' for node in ast.walk(tree))
    assert "'part3'" in (ROOT/'Part2/validation_suite/test_staff_s1.py').read_text('utf-8')

def test_unused_facts_are_not_computed_and_used_fact_is_shared():
    class Probe:
        name='probe'
        def subscriptions(self):return Subscriptions(facts=('ATR14_GENERAL','WONBI_BANDS'),kinds=(Kind.MARKET_BUNDLE,))
        def on_event(self,event,board,state,emit):
            if event.source_seq==2:
                first=board.fact('ATR14_GENERAL','XAUUSD+','1m')
                second=board.fact('ATR14_GENERAL','XAUUSD+','1m')
                assert first.tobytes()==second.tobytes()
                emit(Signal('XAUUSD+','ok',{'value':float(first[-1])}))
    engine=EventEngine(IngressSequencer(),[Probe()])
    for seq in (1,2):
        engine.ingress.post(Kind.MARKET_BUNDLE,source='test',source_seq=seq,source_time=seq*1000,
            payload={'symbol':'XAUUSD+','feeds':{'1m':snapshot(seq),'1h':snapshot(seq)}})
        engine.run()
        if seq==1:assert not engine.facts.computed
    assert engine.facts.computed['XAUUSD+','1m','ATR14_GENERAL']==1
    assert not any(key[1]=='1h' or key[2]=='WONBI_BANDS' for key in engine.facts.computed)
    assert not engine.error_log and len(engine.signals)==1

def test_resident_records_retention_checkpoint_restore_and_no_dispatch_json(monkeypatch):
    from durable_protocol import Records
    from domain_memory import memory_scope
    data={}
    with memory_scope(data):
        r=Records(Path('event_receipts.json'),resident=True,retention_seconds=3*86400)
        r.advance_time(100.)
        r.put('one',{'status':'done','reply':{'ok':True}})
        with monkeypatch.context() as guard:
            def deny(*a,**k):raise AssertionError('whole JSON serialized during dispatch')
            guard.setattr(json,'loads',deny);guard.setattr(json,'dumps',deny)
            for i in range(10):r.put(str(i),{'value':i});assert r.get(str(i))=={'value':i}
        assert not data
        r.advance_time(100.+3*86400)
        assert r.get('one')['reply']['ok']  # exact deadline still retained
        saved=r.export_json()
        data['event_receipts.json']=saved
        restored=Records(Path('event_receipts.json'),resident=True,retention_seconds=3*86400)
        assert restored.all()==r.all()
        restored.advance_time(100.+3*86400+.001)
        assert restored.all()=={}
        assert r.get('one') is not None  # independent checkpoint

def test_resident_records_live_size_plateaus():
    from durable_protocol import Records
    from domain_memory import memory_scope
    sizes=[]
    with memory_scope({}):
        r=Records(Path('event_receipts.json'),resident=True,retention_seconds=3*86400)
        for day in range(12):
            r.advance_time(day*86400.)
            for n in range(100):r.put(f'{day}:{n}',{'status':'done','reply':{'ok':True}})
            sizes.append(len(r.all()))
    assert sizes[3:]==[400]*9

def test_wire_buffers_are_immutable_and_crc_is_enforced():
    s=snapshot();raw=wire.pack_bundle('XAUUSD+',[wire.pack_v2('XAUUSD+','1m',s.time,s.volume,s.values,seq=1)],seq=1,sent_at_ms=1000)
    mutable=bytearray(raw);packet=wire.decode_v2(mutable);before=packet.children[0].values.tobytes()
    mutable[100:108]=b'xxxxxxxx'
    assert packet.children[0].values.tobytes()==before
    with pytest.raises(ValueError):packet.children[0].values.setflags(write=True)
    corrupt=bytearray(raw);corrupt[-1]^=1
    with pytest.raises(wire.WireError,match='CRC'):wire.decode_v2(corrupt)
    for cut in (1,4,40):
        with pytest.raises(wire.WireError):wire.decode_v2(raw[:-cut])
    with pytest.raises(wire.WireError):wire.decode_v2(raw+b'x')

def test_bundle_remap_preserves_payload_and_validates_in_staff(tmp_path):
    from event_backtest.bridge import remap_bundle,Collector
    from event_engine.staff_adapter import StaffIngressAdapter
    from event_host import load_staff
    s=snapshot();frames=[wire.pack_v2('XAUUSD+',tf,s.time,s.volume,s.values,seq=7) for tf in ('1m','5m')]
    raw=wire.pack_bundle('XAUUSD+',frames,seq=9,sent_at_ms=1000);packet=wire.decode_v2(raw)
    assert remap_bundle(raw,packet,[7,7],9,1000) is raw
    changed=remap_bundle(raw,packet,[1,3],1,2000)
    expected=wire.pack_bundle('XAUUSD+',[wire.pack_v2('XAUUSD+',tf,s.time,s.volume,s.values,seq=seq) for tf,seq in [('1m',1),('5m',3)]],seq=1,sent_at_ms=2000)
    assert changed==expected
    staff=load_staff();collector=Collector();cache=staff.StaffPipeCache('',monotonic=lambda:0.,gap_journal=tmp_path/'gaps.jsonl')
    adapter=StaffIngressAdapter(cache,collector);adapter.publish(changed)
    assert collector.pending[-1].source_time==2000
    assert set(collector.pending[-1].payload['feeds'])=={'1m','5m'}

def test_fact_interest_includes_dynamic_requirements_without_copying_irrelevant_payload():
    from event_composition import CompositionKernel
    required={'TREND':{'trend':{'symbol':'XAUUSD+','source_tf':'1m'}},'FVG':{},'SWEEP':{}}
    manager=SimpleNamespace(_subscription_dirty=False,_engine_subscriptions=required,
        _desired_subscriptions_locked=lambda:{'TREND':{},'FVG':{'fvg':{'symbol':'XAUUSD+','source_tf':'5m'}},'SWEEP':{}})
    kernel=SimpleNamespace(symbol='XAUUSD+',manager=manager)
    event={'kind':'FACT_SNAPSHOT','strategy':'TREND','symbol':'XAUUSD+','source_tf':'1m'}
    assert CompositionKernel.wants_fact(kernel,event)
    assert not CompositionKernel.wants_fact(kernel,{**event,'source_tf':'5m'})
    assert not CompositionKernel.wants_fact(kernel,{**event,'symbol':'BTCUSD'})
    assert CompositionKernel.wants_fact(kernel,{**event,'kind':'TREND_STATE'})
    manager._subscription_dirty=True
    assert CompositionKernel.wants_fact(kernel,{**event,'strategy':'FVG','source_tf':'5m'})
    assert not CompositionKernel.wants_fact(kernel,event)

def test_composer_numpy_wonbi_ma_and_heartbeat_liveness(monkeypatch):
    from event_engine.board import Board
    from event_engine.facts import EventFacts
    from event_engine.model import Event
    from event_composer_inputs import ComposerInputs
    # Use the ordinary engine's board to keep its declared Fact access rules.
    class Probe:
        name='probe'
        def subscriptions(self):return Subscriptions(facts=('WONBI_BANDS',),kinds=(Kind.MARKET_BUNDLE,))
        def on_event(self,event,board,state,emit):
            manager=SimpleNamespace(ma_state_facts={('XAUUSD+','1m','SMA17'):{'observed_at':0}},ma_price_state_facts={},ma_slope_state_facts={})
            kernel=SimpleNamespace(symbol='XAUUSD+',board=board,manager=manager,timestamp=1000)
            inputs=ComposerInputs()
            import pandas as pd
            def deny(*a,**kw):raise AssertionError('Composer built a DataFrame')
            with monkeypatch.context() as guard:
                guard.setattr(pd,'DataFrame',deny)
                wonbi=inputs.read(kernel,'XAUUSD+',['1m'],['WONBI'])['1m']
                assert wonbi.row(-1).get('wonbi_upper')==snapshot().values[-1,wire.PIPE_VALUE_COLUMNS.index('wonbi_upper')]
                ma=inputs.read(kernel,'XAUUSD+',['1m'],['MA'],ma_names={'1m':['SMA17']})['1m']
                from indicator_facts import ma_array
                np.testing.assert_array_equal(ma.column('SMA17'),ma_array(snapshot().values[:,0],'SMA',17))
                assert ma.row(-1).get('sma_17')==ma.column('SMA17')[-1]
                count=inputs.ma.computations;kernel.timestamp=2000
                assert inputs.read(kernel,'XAUUSD+',['1m'],['MA'],ma_names={'1m':['SMA17']})=={}
                assert inputs.ma.computations==count
                assert manager.ma_state_facts['XAUUSD+','1m','SMA17']['observed_at']==2.
    engine=EventEngine(IngressSequencer(),[Probe()])
    engine.ingress.post(Kind.MARKET_BUNDLE,source='test',source_seq=1,source_time=1000,payload={'symbol':'XAUUSD+','feeds':{'1m':snapshot()}})
    engine.run();assert not engine.error_log,engine.error_log

def test_notification_dedup_retention_and_logical_sequence():
    from event_composition import NotificationPort
    kernel=SimpleNamespace(config={'TELEGRAM_CHAT_ID':'offline'},timestamp=0,current_event=None,messages=[])
    port=NotificationPort(kernel);port.advance_time(10)
    assert port.send('hello',event_id='same');first=port.sequence
    assert port.send('hello',event_id='same') and port.sequence==first
    port.advance_time(10+3*86400)
    assert port.send('hello',event_id='same') and port.sequence==first
    port.advance_time(11+3*86400)
    assert not port.deliveries
    assert port.send('new',event_id='new') and port.sequence==first+1

def test_watch_without_composer_conditions_has_no_market_input_updates(monkeypatch):
    from event_application import create_event_engine
    engine=create_event_engine({'STAFF_ALLOWED_SYMBOLS':'XAUUSD+','TARGET_SYMBOLS':'XAUUSD+',
        'TELEGRAM_TOKEN':'OFFLINE','TELEGRAM_CHAT_ID':'offline'},symbols=('XAUUSD+',),selection=['WATCH'])
    def send(seq):
        engine.ingress.post(Kind.MARKET_BUNDLE,source='test',source_seq=seq,source_time=1756684800000+seq*1000,
            payload={'symbol':'XAUUSD+','feeds':{'1m':snapshot(seq)}});engine.run()
    send(1)
    kernel=engine.strategy_state['COMPOSER']['kernels']['XAUUSD+']
    def deny(*a,**k):raise AssertionError('Watch Composer evaluated unregistered market conditions')
    for name in ('_update_wonbi','_update_percentile','_update_ma_state','_update_local_chain_events','_evaluate_symbol_locked'):
        monkeypatch.setattr(kernel.manager,name,deny)
    send(2);assert not engine.error_log,engine.error_log

def test_staff_publication_rolls_back_whole_bundle_and_keeps_error_time(tmp_path):
    from event_host import load_staff
    from event_backtest.bridge import Collector
    from event_engine.staff_adapter import StaffIngressAdapter
    s=snapshot();staff=load_staff();collector=Collector()
    cache=staff.StaffPipeCache('',monotonic=lambda:0.,gap_journal=tmp_path/'gaps.jsonl')
    adapter=StaffIngressAdapter(cache,collector)
    initial=wire.pack_bundle('XAUUSD+',[wire.pack_v2('XAUUSD+',tf,s.time,s.volume,s.values,seq=1) for tf in ('1m','5m')],seq=1,sent_at_ms=1000)
    adapter.publish(initial)
    old=cache.snapshots_with_age('XAUUSD+',['1m','5m'])
    full=wire.pack_v2('XAUUSD+','1m',s.time,s.volume,s.values,seq=2)
    invalid=wire.pack_v2('XAUUSD+','5m',s.time[-1:]+60,s.volume[-1:],s.values[-1:],seq=2,kind=wire.WIRE_ROW)
    with pytest.raises(wire.WireError,match='new bar'):
        adapter.receive_one(io.BytesIO(wire.pack_bundle('XAUUSD+',[full,invalid],seq=2,sent_at_ms=2000)).read)
    after=cache.snapshots_with_age('XAUUSD+',['1m','5m'])
    assert all(after[tf][0] is old[tf][0] for tf in old)
    assert collector.pending[-1].kind==Kind.FEED_HEALTH and collector.pending[-1].source_time==2000
    assert sum(x.kind==Kind.MARKET_BUNDLE for x in collector.pending)==1
