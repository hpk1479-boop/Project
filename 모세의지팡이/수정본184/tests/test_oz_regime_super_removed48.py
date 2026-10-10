"""Revision48 correction: removed OZ strategies cannot return through source state.

Uses synthetic in-memory state and transport-free hosts only. No network or MT5.
"""
from __future__ import annotations
import copy
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
from oz_engine.runtime import OZRuntime, SourceClock
from oz_engine.controllers import OZWatchController, ExternalLiquidityController
from test_oz_profiles48 import CONFIG, profile, view, seed, prohibit_network

REMOVED=('REGIME','SUPER','BREAKER_REGIME','BREAKER_SUPER','REGIME_SUPER','BREAKER_REGIME_SUPER')
ROOT=Path(__file__).resolve().parents[1]


# 수정본162: the check that only looked for traces of the removed evaluators was deleted;
# old saved state that names a removed profile is still quarantined or rejected (below).
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


@pytest.mark.parametrize('fields',[
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
