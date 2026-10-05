"""One public path: Recipe registration, LIVE/replay, state and simple cost."""
import copy
import importlib.util
import json
import sys
import time
import threading
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
PROGRAM = ROOT / 'Part1/program'
sys.path.insert(0, str(PROGRAM))
from event_application import create_event_engine, register_strategy_loader, unregister_strategy_loader
from event_engine import Kind, FeedSnapshot
from staff_schema import PIPE_VALUE_COLUMNS
from strategy_recipe.contract import execution_plan
from strategy_recipe.registry import list_presets, load_plugins, entries, dependencies
from strategy_recipe.port import IntentPort
from strategy_recipe.runtime import IntentMachine


def test_all_presets_use_rule_inputs_without_numbered_executors():
    plugins = load_plugins({'SYMBOLS':'TEST'})
    assert tuple(plugins) == list_presets()
    for name, plugin in plugins.items():
        units = plugin.recipe['strategy_intent'].get('branches') or [plugin.recipe['strategy_intent']]
        for unit in units:
            machine = IntentMachine(unit, 'TEST', 'LONG')
            yes = [{'ready':True,'matched':True,'event':False} for step in unit['steps']]
            no = [{'ready':True,'matched':False,'event':False} for step in unit['steps']]
            assert machine.advance(10, no) == []
            actions = []
            # Sequenced steps remain discrete and strictly ordered. Other
            # presets may consume their declared current states together.
            if unit['order_mode'] == 'SEQUENTIAL':
                for index in range(len(yes)):
                    observed = copy.deepcopy(no)
                    observed[index] = dict(yes[index],event=True,token=index,at=11+index)
                    actions += machine.advance(11+index,observed)
            else:
                actions = machine.advance(11, yes, after_observations=yes[:len(unit.get('after_conditions', []))],
                    lifecycle_context={'snapshots':{'anchor':100.,'atr':2.}})
            assert 'ARM' in actions, name
            assert machine.advance(12, yes, cancelled=True) == ['CANCEL']


def snapshot(sequence, above):
    values=np.full((240,len(PIPE_VALUE_COLUMNS)),100.,dtype='<f8')
    for column in ('high',):values[:,PIPE_VALUE_COLUMNS.index(column)]=101.
    values[:,PIPE_VALUE_COLUMNS.index('low')]=99.
    values[:,PIPE_VALUE_COLUMNS.index('ema_50')]=110. if above else 90.
    for index,name in enumerate(PIPE_VALUE_COLUMNS):
        if name.endswith(('_lower_out','_upper_out')):values[:,index]=np.nan
    stamps=np.arange(240,dtype='<i8')*60+1790800000
    return FeedSnapshot(stamps,np.ones(240,dtype='<i8'),values,sequence,'rule-test',{})


def test_live_replay_and_host_checkpoint_use_same_common_port(tmp_path):
    raw={'direction':'LONG','symbols':['TEST'],'steps':[{'kind':'MA_STATE','tfs':['1m'],
        'ma_family':'EMA','fast_period':50,'slow_period':200,'side':'ABOVE'}],
        'order_mode':'SIMULTANEOUS','persistent':True,'final':{'kind':'NOTIFY'}}
    plan=execution_plan(raw)['meaning']
    def register(manager):IntentPort(manager,plan,'User rule','USER_RULE').install()
    plugin=SimpleNamespace(__name__='USER_RULE',register=register,OZ_DECLARATIONS=())
    register_strategy_loader('USER_RULE',lambda:plugin,dependencies=dependencies(plan))
    try:
        live=create_event_engine({'SYMBOLS':'TEST','TELEGRAM_CHAT_ID':'TEST'},symbols=['TEST'],selection=['USER_RULE'])
        replay=create_event_engine({'SYMBOLS':'TEST','TELEGRAM_CHAT_ID':'TEST'},symbols=['TEST'],selection=['USER_RULE'],backtest=True)
        for seq,above in enumerate((False,True,True,False,True),1):
            for engine in (live,replay):
                engine.ingress.post(Kind.MARKET_BUNDLE,source='staff',source_seq=seq,
                    source_time=(1790814400+seq)*1000,payload={'symbol':'TEST','feeds':{'1m':snapshot(seq,above)}})
                engine.run()
            assert not live.error_log and not replay.error_log
            assert [s.payload for s in live.signals] == [s.payload for s in replay.signals]
        notices=[s.payload['content'] for s in live.signals if s.payload['content'].get('type')=='NOTIFICATION']
        assert len(notices)==2
        assert all(s['signal_strategy']=='USER_RULE' for s in notices)
        from event_startup import export_engine_state
        saved=export_engine_state(live)
        state=json.loads(saved['event_composer_memory.json'])['TEST']
        assert 'USER_RULE' in json.loads(state['strategy_recipe_state.json'])
        # Move the same Recipe registry to a new project path; relative roots
        # remain sufficient and no original warehouse access is involved.
        from strategy_recipe import registry
        moved=tmp_path/'moved-project/settings';moved.mkdir(parents=True)
        target=moved/'strategy_registry.json';target.write_bytes(registry.REGISTRY.read_bytes())
        old=registry.REGISTRY
        try:
            registry.REGISTRY=target
            assert registry.list_presets()==tuple(entries())
        finally:registry.REGISTRY=old
    finally:unregister_strategy_loader('USER_RULE')


def test_oz_resource_projection_uses_existing_cycle_identity():
    from oz_engine.runtime import OZRuntime
    runtime=OZRuntime.__new__(OZRuntime)
    candidate=SimpleNamespace(true_b0_time=100,alerted=True)
    profile=SimpleNamespace(candidates={('1h','LONG'):candidate})
    runtime.ready_profiles={('TEST','NORMAL','BREAKER')}
    runtime.profiles={('TEST','NORMAL','BREAKER'):profile}
    key=('TEST','1h','NORMAL','BREAKER','LONG')
    assert runtime.resource_status('TEST',[('1h','NORMAL','BREAKER')])[key]==100
    profile.candidates['1h','LONG']=None
    assert runtime.resource_status('TEST',[('1h','NORMAL','BREAKER')])[key] is None
    runtime.ready_profiles.clear()
    assert runtime.resource_status('TEST',[('1h','NORMAL','BREAKER')])=={}


def test_simple_strategy_does_not_run_lifecycle_queries():
    plan=execution_plan({'direction':'LONG','symbols':['TEST'],
        'steps':[{'kind':'MA_PRICE_TOUCH','tfs':['1m'],'ma_family':'SMA','slow_period':23}],
        'order_mode':'SEQUENTIAL','final':{'kind':'NOTIFY'}})['meaning']
    machine=IntentMachine(plan,'TEST','LONG')
    began=time.perf_counter()
    observation={'ready':True,'matched':False,'event':True,'token':None}
    for index in range(10000):machine.advance(index,[observation])
    elapsed=time.perf_counter()-began
    assert not machine.snapshots and machine.closed_bars==0
    # Diagnostic duration, not a machine-specific arbitrary performance gate.
    proof=ROOT/'검증결과/simple_rule_cost84.json'
    proof.write_text(json.dumps({'observations':10000,'elapsed_seconds':elapsed,
        'snapshot_queries':0,'bar_lifecycle_queries':0},indent=2),encoding='utf-8')


def test_common_time_filters_boundaries_and_independent_overrides():
    from special_time_slot import session_filters_allowed
    from domain_clock import event_scope
    api=SimpleNamespace(config_get=lambda key,default='':'0900-1100',time_allowed=lambda filters:True)
    from datetime import datetime, timezone
    start=datetime(2026,10,2,0,0,tzinfo=timezone.utc).timestamp()*1000
    selected={'MAIN_ASIA':{'enabled':True,'start':'09:00','end':'11:00'}}
    disabled={'MAIN_ASIA':{'enabled':False,'start':'09:00','end':'11:00'}}
    # Existing time slots are minute based and include the ending minute.
    for seconds, expected in ((-1,False),(0,True),(7200,True),(7259,True),(7260,False)):
        with event_scope(int(start+seconds*1000),'time-test',{}):
            assert session_filters_allowed(api,selected) is expected
            assert session_filters_allowed(api,disabled)
            assert session_filters_allowed(api,{})
            assert session_filters_allowed(api,0)
    with event_scope(int(start+15*3600000),'time-test',{}):
        assert session_filters_allowed(api,{'MAIN_ASIA':'2300-0200'})


def test_oz_declarations_bound_recipe_sources_and_normal_confirmation():
    from event_oz_selection import OZSelection
    from strategy_recipe.registry import oz_declarations
    from oz_engine.common import TF_MAP
    for plugin in load_plugins({'SYMBOLS':'TEST'}).values():
        declared=oz_declarations(plugin.recipe['strategy_intent'])
        selection=OZSelection(declared)
        for tf,vm,tm in declared:
            assert tf in selection.feeds
            if vm=='NORMAL':assert set(TF_MAP[tf]) <= selection.feeds


def test_shared_dispatch_uses_arbitrary_owners_and_keeps_failed_inputs():
    from composer_oz_dispatch import dispatch
    context=threading.local()
    children={'shared':{'source_spec_ids':['ALPHA','BETA']}}
    seen=[];cancelled=[]
    def handler(event):
        seen.append(event)
        return {'ok':event['source_spec_id']=='ALPHA','delivered':True}
    manager=SimpleNamespace(_special_oz_source_ids=lambda event:['ALPHA','BETA'],
        _active_children=children,_config_chain_active={},_delivery_context=context,
        _special_oz_event_handlers={name:SimpleNamespace(handle_oz_event=handler) for name in ('ALPHA','BETA')},
        _filter_config_chain_deadline_event=lambda event:(event,False),
        watch_orchestrator=SimpleNamespace(filter_deadline_event=lambda event:(event,False)),
        special_api=SimpleNamespace(cancel_oz_watches=lambda ids:cancelled.extend(ids)))
    event={'kind':'FINAL_ALERT','watch_ids':['shared'],'event_id':'market','symbol':'TEST'}
    result=dispatch(manager,event)
    assert not result['ok'] and 'shared' in children and not cancelled
    assert {item['signal_strategy'] for item in seen}=={'ALPHA','BETA'}
    assert len({item['event_id'] for item in seen})==2


def test_startup_prunes_removed_owners_and_keeps_registered_recipe_watches():
    from event_composer_domain import ComposerManager
    manager=ComposerManager.__new__(ComposerManager)
    manager.official_specs={}
    manager._special_oz_event_handlers={'USER_RULE:BRANCH:0':object()}
    manager._save_active_children_state_locked=lambda:None
    manager._active_children={
        'current':{'watch_owner':'KIM','source_spec_id':'USER_RULE:BRANCH:0','request_chat_id':'PUBLIC'},
        'removed':{'watch_owner':'KIM','source_spec_id':'REMOVED_RULE','request_chat_id':'PUBLIC'},
        'personal':{'source_spec_id':'PERSONAL_RULE','request_chat_id':'PERSONAL'},
    }
    cancellations=manager._prune_stale_official_children_locked()
    assert set(manager._active_children)=={'current','personal'}
    assert [item['watch_id'] for item in cancellations]==['removed']
