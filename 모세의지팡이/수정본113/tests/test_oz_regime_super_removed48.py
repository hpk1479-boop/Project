"""Revision48 correction: removed OZ strategies cannot return through source state.

Uses synthetic in-memory state and transport-free hosts only. No network or MT5.
"""
from __future__ import annotations
import copy
import importlib.util
import inspect
import json
import threading
from pathlib import Path
from types import SimpleNamespace
import pytest
import oz_profiles
from oz_profile_loader import load_saved_oz, LegacyUnsupportedProfile
import monitor_OZ
from durable_protocol import identity
from domain_memory import memory_scope
from event_composer_domain import ComposerManager
from event_application import load_strategy_inputs
from oz_engine import common
from oz_engine.runtime import OZRuntime, SourceClock
from oz_engine.controllers import OZWatchController, ExternalLiquidityController
from test_oz_profiles48 import CONFIG, profile, view, seed, prohibit_network
from test_special5_profiles48 import Host, frame

REMOVED=('REGIME','SUPER','BREAKER_REGIME','BREAKER_SUPER','REGIME_SUPER','BREAKER_REGIME_SUPER')
ROOT=Path(__file__).resolve().parents[1]


def test_removed_evaluators_and_injection_points_are_deleted():
    from oz_engine.profile import OZProfile
    from event_engine.oz_processor import OZProcessor
    assert importlib.util.find_spec('special5_source_condition') is None
    for obj in (common, monitor_OZ):
        for name in ('regime_filter','super_filter','REGIME_COLUMNS','SUPER_COLUMNS'):
            assert not hasattr(obj,name)
    for name in ('_source_family_gate','use_regime','use_super','_super_fact'):
        assert not hasattr(OZProfile,name)
    assert 'source_band' not in inspect.signature(OZRuntime).parameters
    assert 'source_band' not in inspect.signature(OZProcessor).parameters
    assert not hasattr(OZRuntime,'_source_condition')
    assert not hasattr(OZRuntime,'_evaluate_source_band')


@pytest.mark.parametrize('tm',REMOVED)
@pytest.mark.parametrize('vm',('NORMAL','BLIND'))
def test_removed_checkpoint_is_quarantined_never_reinterpreted(tm,vm,caplog):
    key='oz_observed_'+identity('XAUUSD+',vm,tm)[:24]+'.json'
    raw=json.dumps({'version':1,'symbol':'XAUUSD+','profile':[vm,tm],'observed':{'sentinel':123}})
    runtime=OZRuntime(monitor_OZ,CONFIG,{key:raw})
    assert not runtime.profiles
    files=runtime.export_files()
    assert key not in files
    rejected=json.loads(files['oz_rejected_profile_checkpoints.json'])
    assert rejected[key]['original']==raw
    assert key in caplog.text and '격리' in caplog.text
    clone=copy.deepcopy(runtime)
    assert clone.export_files()==files
    assert OZRuntime(monitor_OZ,CONFIG,files).export_files()==files


def test_source_band_checkpoint_discard_preserves_valid_breaker_state():
    p=profile('NORMAL','BREAKER');seed(p,view())
    valid=json.dumps({'version':1,'profile':['NORMAL','BREAKER'],'observed':p.export_event_state()})
    removed_key='special5_source_band_legacy.json'
    removed=json.dumps({'version':1,'profile':['NORMAL','BREAKER'],
                        'strategy_condition':'SPECIAL5_UPPER_REGIME_BAND','observed':p.export_event_state()})
    source={p.checkpoint_name:valid,removed_key:removed}
    runtime=OZRuntime(monitor_OZ,CONFIG,source)
    restored=runtime._profile('XAUUSD+','NORMAL','BREAKER')
    assert restored.candidates['1m','LONG'].true_b0_price==99.
    assert source[removed_key]==removed
    files=runtime.export_files()
    assert removed_key not in files
    assert json.loads(files['oz_rejected_profile_checkpoints.json'])[removed_key]['original']==removed
    assert not hasattr(runtime,'source_conditions')


@pytest.mark.parametrize('fields',[
    {'special5_source_trigger_mode':'BREAKER_REGIME'},
    {'special5_source_trigger_mode':'SUPER'},
    {'special5_source_gate':'UPPER_REGIME_BAND'},
    {'watch_id':'OZARM:SP5:FINAL:5m:BREAKER_REGIME:old'},
    {'watch_id':'OZARM:SP5:HIGH_BREAKER_REGIME:5m'},
])
def test_removed_source_child_rejected_by_both_state_loaders(fields):
    base={'watch_id':'OZARM:SP5:FINAL:5m:BREAKER:old','action':'MANUAL_WATCH','timeframes':['1m'],
          'symbol':'XAUUSD+','validation_mode':'BLIND','trigger_mode':'OZ','persistent':True,
          'source_spec_id':'PIPELINE_5','issued_at':123}
    bad=dict(base,**fields)
    good=dict(base,watch_id='OZARM:SP5:FINAL:5m:BREAKER:good',special5_source_trigger_mode='BREAKER')
    raw={'version':3,'watches':[bad,good]}
    before=copy.deepcopy(raw)
    clock=SourceClock({})
    watch=OZWatchController(SimpleNamespace(send=lambda *a,**k:True),ExternalLiquidityController(clock,sweep_registry={}),clock,initial=raw)
    watch._load_state()
    assert set(watch._watches)=={good['watch_id']}
    assert watch.rejected_watches[0]['original']==bad
    manager=ComposerManager.__new__(ComposerManager)
    manager._lock=threading.RLock();manager._active_children={}
    manager._active_children_state_path=Path('children.json')
    files={'children.json':json.dumps(raw)}
    with memory_scope(files):
        manager._load_active_children_state()
        manager._save_active_children_state_locked()
    assert set(manager._active_children)=={good['watch_id']}
    assert manager._active_children[good['watch_id']]['special5_source_trigger_mode']=='BREAKER'
    assert manager._rejected_profile_children[0]['original']==bad
    assert json.loads(files['children.json'])['rejected_watches'][0]['original']==bad
    assert raw==before


@pytest.mark.parametrize('fields',[
    {'special5_source_trigger_mode':'BREAKER_REGIME'},
    {'special5_source_gate':'UPPER_REGIME_BAND'},
    {'watch_id':'OZARM:SP5:FINAL:5m:BREAKER_REGIME:old'},
])
def test_loader_excludes_old_children_before_special5_register(fields,caplog):
    sp5=load_strategy_inputs({})[2]['SPECIAL5'];host=Host()
    runtime=sp5.Special5Runtime(host.special_api,'XAUUSD+')
    ids=runtime.arm_final('LONG','5m')
    for wid in ids:
        item=host._active_children.pop(wid);item.update(fields)
        # A supplied legacy ID denotes one representative saved child.
        host._active_children[item['watch_id']]=item
    victims=set(host._active_children)
    restored={}
    for wid,row in host._active_children.items():
        with pytest.raises(LegacyUnsupportedProfile):load_saved_oz(row)
    host._active_children=restored
    host.pushes.clear()
    sp5.register(host)
    assert not any(w.startswith(sp5.FINAL_PREFIX) for w in host._active_children)
    assert len(host._active_children)==16
    assert not victims.intersection({x.get('watch_id') for x in host.pushes})
    assert not hasattr(sp5,'_cleanup_removed_source_watches')
    assert not host.notices


@pytest.mark.parametrize('legacy_id',[
    'OZARM:SP5:FINAL:5m:BREAKER_REGIME:old',
    'OZARM:SP5:FINAL:5m:BREAKER:already_cancelled',
])
def test_stale_removed_or_cancelled_child_event_cannot_deliver(legacy_id):
    sp5=load_strategy_inputs({})[2]['SPECIAL5'];host=Host();sp5.register(host)
    event={'kind':'FINAL_ALERT','symbol':'XAUUSD+','direction':'LONG','tf':'1m','watch_ids':[legacy_id]}
    result=host.oz_handlers[sp5.SPEC_ID].handle_oz_event(event)
    assert result['suppressed'] and not host.notices
    assert not any(w.startswith(sp5.FINAL_PREFIX) for w in host._active_children)


@pytest.mark.parametrize('location',('config','slot'))
@pytest.mark.parametrize('enabled',(False,True))
def test_part3_removed_alternative_rejected_even_when_disabled(location,enabled):
    from lab import catalog
    from lab.compiler import compile_recipe,prepare_manual
    recipe=catalog.load(5)
    if location=='config':recipe['config']['SOURCE_BAND_ENABLED']=enabled
    else:recipe['slots'][0]['params']['upper_regime_band']=enabled
    with pytest.raises(ValueError):compile_recipe(recipe,'Test_SPECIAL888.py')
    source=f'SOURCE_BAND_ENABLED={enabled!r}\ndef register(manager):pass\n'
    with pytest.raises(LegacyUnsupportedProfile):load_saved_oz(source,kind='source')


def test_current_special5_generated_source_has_no_replacement_condition():
    from lab import catalog
    from lab.compiler import compile_recipe
    recipe=catalog.load(5)
    source=compile_recipe(recipe,'Test_SPECIAL888.py')
    assert recipe['slots'][0]['params']=={'ema_filter':True}
    assert 'SOURCE_BAND_ENABLED' not in recipe['config']
    for token in ('SourceBandCondition','_source_family_gate','UPPER_REGIME_BAND','SOURCE_BAND_ENABLED','source_gate'):
        assert token not in source
    # Current UI selection labels for the replacement condition are also gone.
    ui=(ROOT/'Part3/web/app.js').read_text('utf-8')
    assert 'upper_regime_band:' not in ui and 'SOURCE_BAND_ENABLED:' not in ui
