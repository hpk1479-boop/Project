"""Shared market observations are judged independently; output is a policy."""
from pathlib import Path
from types import SimpleNamespace as NS
import socket,sys,threading
import pytest
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'Part1/program'),str(ROOT/'Part2')]
from event_composer_domain import ComposerManager,SpecialPluginAPI
from event_composition import NotificationPort
from event_engine.model import Event,Kind,Signal
from composer_oz_dispatch import dispatch
from manager_KIM import SignalOutput

@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def deny(*a,**k):raise AssertionError('actual network prohibited')
    monkeypatch.setattr(socket,'create_connection',deny)
    for name in ('connect','connect_ex','sendto'):monkeypatch.setattr(socket.socket,name,deny)

def manager(ids=('PIPELINE_1','PIPELINE_2','PIPELINE_6')):
    m=ComposerManager.__new__(ComposerManager)
    m._lock=threading.RLock();m._delivery_context=threading.local()
    m._config_chain_active={};m._special_oz_event_handlers={}
    m._active_children={'shared':{'source_spec_id':ids[0],'source_spec_ids':list(ids)}}
    m.special_api=SpecialPluginAPI(m);m._save_active_children_state_locked=lambda:None
    m.commands=[];m._push=m.commands.append
    m._filter_config_chain_deadline_event=lambda e:(e,False)
    m.watch_orchestrator=NS(filter_deadline_event=lambda e:(e,False))
    m.seen=[]
    def core(e):
        m.seen.append(dict(e));m.special_api.cancel_oz_watches(e['watch_ids'])
        return {'ok':True,'delivered':True}
    m._handle_oz_event_core=core
    return m

def event():return {'kind':'FINAL_ALERT','strategy':'OZ','symbol':'XAUUSD+','direction':'LONG',
    'watch_ids':['shared'],'source_spec_ids':['PIPELINE_1'],'event_id':'old-shared-id',
    'market_event_id':'market-one','message':'OZ','source_tf':'1m'}

def test_each_special_evaluated_before_shared_child_removed():
    m=manager();seen=[]
    for name in ('PIPELINE_1','PIPELINE_2','PIPELINE_6'):
        def handle(e,name=name):
            assert 'shared' in m._active_children
            assert e['source_spec_ids']==[name]
            seen.append(name)
            return m._handle_oz_event_core(e)
        m._special_oz_event_handlers[name]=NS(handle_oz_event=handle)
    r=dispatch(m,event())
    assert r['ok'] and seen==['PIPELINE_1','PIPELINE_2','PIPELINE_6']
    assert len({e['event_id'] for e in m.seen})==3
    assert [e['signal_strategy'] for e in m.seen]==['SPECIAL1','SPECIAL2','SPECIAL6']
    assert not m._active_children and len(m.commands)==1

@pytest.mark.parametrize('blocked',['time_filter','special5_internal'])
def test_suppression_never_blocks_other_special(blocked):
    m=manager(('PIPELINE_5','PIPELINE_2'))
    m._special_oz_event_handlers['PIPELINE_5']=NS(handle_oz_event=lambda e:{'ok':True,'delivered':True,'suppressed':blocked})
    assert dispatch(m,event())['ok']
    assert [e['signal_strategy'] for e in m.seen]==['SPECIAL2']

def test_exception_isolated_and_failed_shared_child_retained():
    m=manager()
    def fail(e):raise ValueError('forced exception')
    m._special_oz_event_handlers['PIPELINE_1']=NS(handle_oz_event=fail)
    result=dispatch(m,event())
    assert not result['ok'] and result['strategy_results']['PIPELINE_1']['error']
    assert [e['signal_strategy'] for e in m.seen]==['SPECIAL2','SPECIAL6']
    assert 'shared' in m._active_children

def test_plugin_replacement_defers_shared_cleanup_until_other_decisions():
    m=manager()
    def replace(e):
        m.special_api.replace_oz_watches([],source_spec_id='PIPELINE_1')
        return {'ok':True,'delivered':True,'suppressed':True}
    m._special_oz_event_handlers['PIPELINE_1']=NS(handle_oz_event=replace)
    def other(e):
        assert 'shared' in m._active_children
        return m._handle_oz_event_core(e)
    for name in ('PIPELINE_2','PIPELINE_6'):m._special_oz_event_handlers[name]=NS(handle_oz_event=other)
    assert dispatch(m,event())['ok']
    assert not m._active_children and len(m.commands)==1

def test_signal_identity_does_not_depend_on_other_registered_strategies():
    all_=manager();solo=manager(('PIPELINE_2',))
    dispatch(all_,event());dispatch(solo,event())
    assert next(e['event_id'] for e in all_.seen if e['signal_strategy']=='SPECIAL2')==solo.seen[0]['event_id']

def test_notification_context_uses_strategy_before_output_filter():
    m=manager();k=NS(config={'TELEGRAM_CHAT_ID':'offline'},timestamp=1000,current_event=event(),manager=m,messages=[])
    port=NotificationPort(k)
    def core(e):
        port.send('same text',event_id=e['event_id'])
        return {'ok':True,'delivered':True}
    m._handle_oz_event_core=core
    dispatch(m,event());dispatch(m,event())
    assert len(k.messages)==3
    assert {x['signal_strategy'] for x in k.messages}=={'SPECIAL1','SPECIAL2','SPECIAL6'}

def notification(strategy,market='one',chat='offline'):
    return Event(1,0,'test',1,1000,Kind.SIGNAL,{'strategy':strategy,'symbol':'XAUUSD+',
        'signal_id':strategy+market,'content':{'type':'NOTIFICATION','market_event_id':market,
        'message':'OZ','recipients':[chat]}})

@pytest.mark.parametrize('policy,expected',[('ALL_STRATEGIES',3),('MARKET_REPRESENTATIVE',1)])
def test_manager_policy_and_strategy_specific_dedup_restore(policy,expected):
    sent=[]
    def transport(data):
        sent.append(data);return NS(status_code=200,json=lambda:{'ok':True,'result':{'message_id':len(sent)}})
    config={'OZ_OUTPUT_POLICY':policy}
    output=SignalOutput(config,transport=transport)
    for name in ('SPECIAL1','SPECIAL2','SPECIAL6'):
        output.accept(notification(name));output.accept(notification(name))
    assert len(sent)==expected
    restored=SignalOutput(config,transport=transport,receipts=output.receipts)
    for name in ('SPECIAL1','SPECIAL2','SPECIAL6'):restored.accept(notification(name))
    assert len(sent)==expected
    restored.accept(notification('SPECIAL1',chat='other'))
    restored.accept(notification('SPECIAL1',market='two'))
    assert len(sent)==expected+2

def test_default_output_keeps_distinct_conditions_in_same_special():
    sent=[]
    def transport(data):
        sent.append(data);return NS(status_code=200,json=lambda:{'ok':True,'result':{'message_id':len(sent)}})
    output=SignalOutput({},transport=transport)
    first=notification('SPECIAL1');second=Event(2,0,'test',2,1000,Kind.SIGNAL,
        {**dict(first.payload),'signal_id':'second-condition'})
    output.accept(first);output.accept(second);output.accept(second)
    assert len(sent)==2

def test_failed_representative_does_not_block_next_strategy():
    sent=[]
    def transport(data):
        sent.append(data)
        return NS(status_code=400,json=lambda:{'ok':False}) if len(sent)==1 else NS(status_code=200,json=lambda:{'ok':True,'result':{'message_id':2}})
    output=SignalOutput({'OZ_OUTPUT_POLICY':'MARKET_REPRESENTATIVE'},transport=transport)
    output.accept(notification('SPECIAL1'));output.accept(notification('SPECIAL2'))
    assert len(sent)==2 and len(output.receipts)==1

def test_backtest_collects_every_strategy_before_output_arbitration(tmp_path):
    from event_backtest.warehouse import ResultWriter
    import csv
    writer=ResultWriter(tmp_path/'alerts.csv',{'run_id':'test'},0,2000)
    output=SignalOutput({'OZ_OUTPUT_POLICY':'MARKET_REPRESENTATIVE'},transport=lambda data:NS(status_code=200,json=lambda:{'ok':True,'result':{'message_id':1}}))
    for name in ('SPECIAL1','SPECIAL2','SPECIAL6'):
        e=notification(name);writer.accept(e);output.accept(e)
    writer.close()
    rows=list(csv.DictReader((tmp_path/'alerts.csv').open(encoding='utf-8')))
    assert len(rows)==3 and {r['strategy'] for r in rows}=={'SPECIAL1','SPECIAL2','SPECIAL6'}
    assert len(output.receipts)==1

@pytest.mark.parametrize('allowed',[True,False])
@pytest.mark.parametrize('first_external',[True,False])
def test_non_external_watch_never_skips_external_qualification(allowed,first_external):
    from oz_engine.controllers import OZWatchController
    controller=OZWatchController.__new__(OZWatchController)
    controller._lock=threading.RLock();calls=[]
    external=NS(direction='LONG',external=True);plain=NS(direction='LONG',external=False)
    controller._profile_watches_locked=lambda *a,**kw:[external,plain] if first_external else [plain,external]
    controller._external_id_for_watch_locked=lambda w:'sweep' if w.external else None
    controller.external=NS(spec=lambda wid:NS(source_tf='5m'),
        state=lambda wid,d:NS(status='ACTIVE',direction=d),
        validate_true_b0=lambda wid,d,price:calls.append((wid,d,price)) or allowed)
    assert controller.validate_external_true_b0('XAUUSD+','5m','LONG','NORMAL','BREAKER',100.)
    assert calls==[('sweep','LONG',100.)]
    controller._profile_watches_locked=lambda *a,**kw:[external]
    assert controller.validate_external_true_b0('XAUUSD+','5m','LONG','NORMAL','BREAKER',100.) is allowed

def test_explicit_owners_are_not_discarded_as_duplicate_profile():
    from oz_engine.controllers import OZWatchController,ExternalLiquidityController
    from oz_engine.runtime import SourceClock
    clock=SourceClock({});sender=NS(send=lambda *a,**kw:True)
    c=OZWatchController(sender,ExternalLiquidityController(clock,sweep_registry={}),clock)
    for name in ('PIPELINE_1','PIPELINE_6'):
        c.add_manual(['1m'],symbol='XAUUSD+',direction='LONG',persistent=True,
            validation_mode='NORMAL',trigger_mode='BREAKER',watch_id=name,source_spec_id=name)
    assert set(c._watches)=={'PIPELINE_1','PIPELINE_6'}
    assert set(c._matching('XAUUSD+','1m','LONG','NORMAL','BREAKER'))==set(c._watches)
    c.add_manual(['1m'],symbol='XAUUSD+',direction='LONG',persistent=True,
        validation_mode='NORMAL',trigger_mode='BREAKER',watch_id='PIPELINE_1',source_spec_id='PIPELINE_1')
    assert len(c._watches)==2
