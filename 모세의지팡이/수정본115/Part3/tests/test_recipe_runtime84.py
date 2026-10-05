"""Focused common-rule checks: user inputs, boundaries and shared replay state."""
import copy
import json
import sys
import ast
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

PROGRAM = Path(__file__).resolve().parents[2] / 'Part1' / 'program'
sys.path.insert(0, str(PROGRAM))
from strategy_recipe.runtime import IntentMachine
from strategy_recipe.port import IntentPort
from staff_schema import PIPE_VALUE_COLUMNS


def meaning(steps=None, **extras):
    result = dict(symbols=['TEST'], direction='LONG', order_mode='SIMULTANEOUS',
        steps=steps or [{'kind':'BAR_CLOSE', 'tfs':['30m']}],
        final={'kind':'OZ', 'tfs':['1m'], 'validation_mode':'NORMAL', 'trigger_mode':'OZ'})
    result.update(extras)
    return result


def event(token, at=10, **fields):
    return dict(ready=True, matched=True, event=True, token=token, at=at, **fields)


class Board:
    def __init__(self, feeds, now=3900):
        self.feeds = {}; self._frame_cache = {}; self.health = {}; self.observed = {}
        self.source_time = now * 1000
        self.atrs = {}
        self.publication = 0
        for tf, rows in feeds.items(): self.replace(tf, rows)

    @property
    def publication_token(self): return self.publication

    def replace(self, tf, rows):
        values = np.full((len(rows), len(PIPE_VALUE_COLUMNS)), 100., dtype=float)
        for name in PIPE_VALUE_COLUMNS:
            if name.endswith(('_lower_out','_upper_out')): values[:,PIPE_VALUE_COLUMNS.index(name)] = np.nan
        times = []
        for i, row in enumerate(rows):
            times.append(row['time'])
            for name, value in row.items():
                if name != 'time': values[i, PIPE_VALUE_COLUMNS.index(name)] = value
        self.feeds['TEST',tf] = SimpleNamespace(time=np.array(times, dtype=np.int64),
            values=values, volume=np.ones(len(rows)), source_epoch='feed', seq=len(rows))
        self.observed['TEST',tf] = self.source_time
        self._frame_cache = {}
        self.publication += 1

    def snapshot(self, symbol, tf): return self.feeds[symbol,tf]
    def fact(self, name, symbol, tf):
        assert name == 'ATR14_GENERAL'
        return np.full(len(self.feeds[symbol,tf].time), self.atrs.get(tf, 2.))


class API:
    official_chat_id = None
    def __init__(self, board, now=3900):
        self.board = board; self.now = now; self.resources = {}; self.armed = {}; self.cancelled = []; self.deliveries = []
    def market_context(self): return self.board, 'TEST', self.now
    def shared_resource(self, name, factory): return self.resources.setdefault(name, factory())
    def time_allowed(self, filters): return True
    def ensure_oz_watch(self, payload, **kwargs): self.armed[payload['watch_id']] = copy.deepcopy(payload)
    def cancel_oz_watches(self, ids):
        self.cancelled.extend(ids)
        for watch_id in ids: self.armed.pop(watch_id, None)
    def deliver_oz_event_core(self, event): self.deliveries.append(event); return {'delivered':True}
    def register_subscription_provider(self, owner, provider): pass
    def register_fact_observer(self, owner, observer): pass
    def register_strategy_state_provider(self, owner, provider):
        if getattr(self,'saved',None): provider.restore(self.saved)
    def register_oz_handler(self, owner, handler): pass
    def register_watch_handler(self, owner, handler): pass
    def snapshot_oz_watches(self, *, symbol, watch_id_prefix):
        return [(wid,payload) for wid,payload in self.armed.items()
            if wid.startswith(watch_id_prefix) and payload.get('symbol') == symbol]


def make_port(plan, board, now=3900):
    api = API(board, now)
    return IntentPort(SimpleNamespace(special_api=api), plan, 'Custom', 'CUSTOM'), api


def rows(stamps, **fields): return [dict(time=t, **fields) for t in stamps]


def test_seconds_expiry_boundary_and_frozen_snapshot():
    plan = meaning(lifecycle={'expires':{'seconds':360}, 'snapshots':{'atr':{'tf':'1m','field':'ATR14','bar_state':'CLOSED'}}})
    machine = IntentMachine(plan, 'TEST', 'LONG')
    original = {'atr':2.}
    assert machine.advance(100, [event('A',100)], lifecycle_context={'snapshots':original}) == ['ARM']
    original['atr'] = 99.
    assert machine.snapshots == {'atr':2.}
    assert machine.final_allowed(459, [event('A')])
    assert not machine.final_allowed(460, [event('A')])
    assert machine.advance(460, [event('A')]) == ['CANCEL']


def test_bars_expire_on_first_excess_closed_bar_and_cancel_input():
    plan = meaning(lifecycle={'expires':{'bars':1,'tf':'FINAL'}})
    machine = IntentMachine(plan, 'TEST', 'LONG')
    assert machine.advance(100, [event('A',100)]) == ['ARM']
    assert machine.advance(160, [event('A')], lifecycle_context={'closed_bars':1}) == []
    assert machine.active
    assert machine.advance(220, [event('A')], lifecycle_context={'closed_bars':2}) == ['CANCEL']
    assert not machine.active
    assert machine.advance(300, [event('B',300)]) == ['ARM']
    assert machine.advance(301, [event('B')], cancelled=True) == ['CANCEL']


def test_notify_waits_for_after_condition_without_early_consumption():
    plan = meaning(after_conditions=[{'kind':'MA_PRICE_TOUCH','tfs':['1m']}])
    plan['final'] = {'kind':'NOTIFY'}
    machine = IntentMachine(plan, 'TEST', 'LONG')
    missed = dict(ready=True, matched=False, event=True, token='touch', at=11)
    assert machine.advance(10, [event('setup')], after_observations=[missed]) == []
    assert machine.active
    assert machine.advance(11, [event('setup')], after_observations=[event('touch',11)]) == ['NOTIFY']
    assert not machine.active


def test_live_and_replay_restore_have_same_rule_outputs():
    plan = meaning(order_mode='SEQUENTIAL')
    plan['steps'] = [{'kind':'MA_CROSS','tfs':['5m']}, {'kind':'MA_PRICE_TOUCH','tfs':['1m']}]
    live = IntentMachine(plan, 'TEST', 'LONG')
    replay = IntentMachine(plan, 'TEST', 'LONG')
    no = dict(ready=True,matched=False,event=True,token=None,at=10)
    assert live.advance(10, [event('cross'),no]) == []
    replay.restore(live.checkpoint())
    for at, observations in [(10,[event('cross'),event('touch',10)]),(11,[event('cross'),event('touch',11)]),
            (12,[event('cross'),event('touch',11)])]:
        assert live.advance(at,observations) == replay.advance(at,observations)
    assert live.active and replay.active


def test_first_bar_observation_is_baseline_and_next_bar_is_event():
    board = Board({'30m':rows([0,1800,3600])})
    port, api = make_port(meaning(), board)
    port.poll(); assert not api.armed
    board.replace('30m',rows([1800,3600,5400])); api.now = 5400
    port.poll(); assert len(api.armed) == 1


def test_snapshot_excursion_cancel_and_new_bar_rearm():
    life = {'restart_on':[{'kind':'BAR_CLOSE','tfs':['30m']}], 'expires':{'seconds':360},
        'snapshots':{'anchor':{'tf':'30m','field':'close','bar_state':'CLOSED'},
            'atr':{'tf':'1m','field':'ATR14','bar_state':'CLOSED'}},
        'excursion':{'tf':'1m','anchor':'anchor','snapshot':'atr','multiplier':1,'direction':'FAVORABLE'}}
    board = Board({'30m':rows([0,1800,3600]), '1m':rows([3480,3540,3600],high=100.5,low=99.5)})
    port, api = make_port(meaning(lifecycle=life),board,3600)
    port.poll(); assert not api.armed
    board.replace('30m',rows([1800,3600,5400])); board.replace('1m',rows([5280,5340,5400],high=100.5,low=99.5)); api.now = 5400
    port.poll(); assert len(api.armed) == 1
    assert port.machines[0].snapshots['atr'] == 2.
    board.atrs['1m'] = 90.
    board.replace('1m',rows([5280,5340,5400],high=102.,low=99.5)); api.now = 5401
    port.poll(); assert not api.armed and api.cancelled
    assert not port.machines[0].active


def test_final_tf_siblings_first_success_close_only_their_group():
    plan = meaning(lifecycle={'first_success':True,'replace':{'scope':'SYMBOL_DIRECTION'}})
    plan['final']['tfs'] = ['1m','2m']
    board = Board({'30m':rows([0,1800,3600])})
    port, api = make_port(plan, board)
    port.poll(); board.replace('30m',rows([1800,3600,5400])); api.now = 5400; port.poll()
    assert len(api.armed) == 2
    selected = port.machines[0]
    port.handle_oz_event({'source_spec_id':port._sid(selected),'source_tf':'1m','symbol':'TEST','event_time':5401})
    assert len(api.deliveries) == 1
    assert not api.armed and all(not m.active for m in port.machines)


def test_separate_price_tf_and_metric_use_existing_fact_layer():
    board = Board({'15m':rows(range(0,64*900,900),close=80.,hma_50=100.),
        '1m':rows(range(0,64*60,60),close=110.)})
    port, _ = make_port(meaning(),board)
    step = {'kind':'MA_PRICE_STATE','tfs':['15m'],'price_tf':'1m','ma_family':'HMA','slow_period':50,'side':'ABOVE'}
    assert port.observe(board,'TEST',step,'LONG',3900)['matched']
    metric = {'kind':'TREND_METRIC','tfs':['1m'],'metric':'price','metric_operator':'GT','metric_value':100.}
    assert port.observe(board,'TEST',metric,'LONG',3900)['matched']
    assert len(port.metric_frames) == 1


def test_percentile_first_publication_does_not_invent_return_event():
    out = dict(RSI_val=10.,RSI_db=20.,RSI_ub=80.,RSI_lower_out=10.)
    board = Board({'1m':rows([0,60,120],**out)})
    port, _ = make_port(meaning(),board)
    step = {'kind':'PERCENTILE_OUT_IN','tfs':['1m'],'families':['RSI'],'family_combine':'ANY','side':'LOWER'}
    assert not port.observe(board,'TEST',step,'LONG',120)['matched']
    board.replace('1m',rows([0,60,120],RSI_val=25.,RSI_db=20.,RSI_ub=80.))
    assert port.observe(board,'TEST',step,'LONG',121)['matched']
    # Same input publication is shared, never consumed separately by siblings.
    assert port.observe(board,'TEST',dict(step),'LONG',121)['matched']


def test_checkpoint_json_roundtrip_preserves_active_scope_and_deadline():
    board = Board({'30m':rows([0,1800,3600])})
    plan = meaning(lifecycle={'expires':{'seconds':360}})
    port, api = make_port(plan, board)
    port.poll(); board.replace('30m',rows([1800,3600,5400])); api.now = 5400; port.poll()
    restored, _ = make_port(plan,board,5400)
    assert restored.restore(json.loads(json.dumps(port.checkpoint())))
    assert restored.deadlines() == (5760,)
    assert restored.machines[0].active


def test_middle_scope_invalidation_keeps_parent_and_waits_for_replacement():
    steps = [{'kind':'OZ_ALERT','tfs':['15m'],'capture':'parent'},
        {'kind':'OZ_ALERT','tfs':['3m'],'capture':'middle'},
        {'kind':'OZ_ALERT','tfs':['1m'],'capture':'child'}]
    machine = IntentMachine(meaning(steps, order_mode='SEQUENTIAL',lifecycle={'invalidate_refs':True}), 'TEST','LONG')
    no = dict(ready=True,matched=False,event=True,token=None,at=0)
    a = event('A',10,resource={'kind':'OZ','id':'A','at':10})
    b = event('B',11,resource={'kind':'OZ','id':'B','at':11})
    c = event('C',12,resource={'kind':'OZ','id':'C','at':12})
    assert machine.advance(10,[a,no,no]) == []
    assert machine.advance(11,[a,b,no]) == []
    assert machine.advance(12,[a,b,c]) == ['ARM']
    assert machine.advance(13,[a,b,c],lifecycle_context={'invalid_refs':['middle']}) == ['CANCEL']
    assert machine.stage == 1 and set(machine.captures) == {'parent'}
    assert machine.advance(14,[a,b,c]) == []
    b2 = event('B2',15,resource={'kind':'OZ','id':'B2','at':15})
    c2 = event('C2',16,resource={'kind':'OZ','id':'C2','at':16})
    assert machine.advance(15,[a,b2,c]) == []
    assert machine.advance(16,[a,b2,c2]) == ['ARM']
    assert machine.advance(17,[a,b2,c2],lifecycle_context={'invalid_refs':['parent']}) == ['CANCEL']
    assert machine.stage == 0 and not machine.captures


def test_fvg_reference_matches_captured_zone_not_newer_unrelated_zone():
    board = Board({'5m':rows([0,300,600],low=104.,high=106.,close=105.)})
    port, _ = make_port(meaning(),board,600)
    facts = [{'zone_id':'old','fvg_side':'BULL','zone_bot':90.,'zone_top':95.,'touched_now':False},
        {'zone_id':'new','fvg_side':'BULL','zone_bot':104.,'zone_top':106.,'touched_now':True}]
    port._fact_snapshot = lambda *args, **kwargs: facts
    machine = port.machines[0]
    machine.captures['selected'] = {'kind':'FVG','id':'old','tf':'5m','lower':90.,'upper':95.}
    step = {'kind':'FVG_TOUCH','tfs':['5m'],'side':'BULL','ref':'selected'}
    assert not port.observe(board,'TEST',step,'LONG',600,machine)['matched']
    facts[0]['touched_now'] = True
    board.publication += 1
    assert port.observe(board,'TEST',step,'LONG',601,machine)['matched']


def test_time_gate_reopens_an_oz_only_strategy_without_stuck_state():
    board = Board({'1m':rows([0,60,120])})
    plan = meaning(time_filters=['SESSION']); plan['steps'] = []
    port, api = make_port(plan,board,120)
    opened = [False]
    api.time_allowed = lambda filters: opened[0]
    port.poll(); assert not api.armed
    opened[0] = True; api.now = 121
    port.poll(); assert len(api.armed) == 1


def test_generated_backtest_loads_recipe_without_executing_saved_source(tmp_path, monkeypatch):
    root = PROGRAM.parents[1]
    monkeypatch.delenv('PART3_BT_REQUEST', raising=False)
    spec = importlib.util.spec_from_file_location('_backtest_recipe_probe84', root/'Part3'/'backtest.py')
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    raw = meaning([{'kind':'MA_STATE','tfs':['1m'],'ma_family':'EMA','fast_period':50,'slow_period':200,'side':'ABOVE'}])
    recipe = dict(schema_version=2, base='AI', name='Generated data', strategy_intent=raw)
    strategy = tmp_path/'Test_SPECIAL903.py'
    strategy.write_text('raise AssertionError("saved source must not execute")\nPART3_RECIPE = '+repr(recipe)+
        '\ndef register(manager):\n    raise AssertionError("old callback must not execute")\n', encoding='utf-8')
    registered = {}
    import event_application
    monkeypatch.setattr(event_application,'register_strategy_loader',lambda key,loader,dependencies:registered.update(
        key=key,loader=loader,dependencies=dependencies))
    installed = []
    class Port:
        def __init__(self,manager,plan,title,namespace): installed.append((manager,plan,title,namespace))
        def install(self): installed.append('installed')
    monkeypatch.setattr('strategy_recipe.port.IntentPort',Port)
    request = dict(project_root=str(root), strategy=str(strategy), job_dir=str(tmp_path))
    assert module.initialize(request) == 'Test_SPECIAL903'
    plugin = registered['loader']()
    assert plugin.OZ_DECLARATIONS == frozenset({('1m','NORMAL','OZ')})
    assert set(registered['dependencies']) == {'MA','OZ'}
    plugin.register('manager')
    assert installed[0][3] == 'Test_SPECIAL903' and installed[-1] == 'installed'
    assert '_resolved_direction' in installed[0][1]['steps'][0]
    recipe['base'] = 'SPECIAL4'
    strategy.write_text('PART3_COMPILE_MODE = "INHERITED"\nPART3_RECIPE = '+repr(recipe),encoding='utf-8')
    with pytest.raises(ValueError,match='INHERITED'): module.initialize(request)
    recipe['base'] = 'AI'; recipe['strategy_intent']['steps'][0]['kind'] = 'UNREGISTERED_RULE'
    strategy.write_text('PART3_RECIPE = '+repr(recipe),encoding='utf-8')
    with pytest.raises(ValueError,match='지원되지'): module.initialize(request)


def test_composer_history_keeps_bar_calendar_without_obsolete_hma_full_scan():
    import pandas as pd
    from event_engine.history import required_rows
    raw = pd.DataFrame({'time':pd.date_range('2020-01-01',periods=1000,freq='min')})
    assert required_rows(raw,'1m',[],role='composer') == 1000
    assert required_rows(raw,'1m',['HMA'],role='composer') < 1000
    # Arbitrary-period MA requests retain the same explicit finite lookback.
    assert required_rows(raw,'1m',['HMA270'],role='composer') == required_rows(raw,'1m',['HMA270'],role='default')


def test_install_reconciles_saved_oz_with_active_recipe_checkpoint():
    board = Board({'30m':rows([0,1800,3600])})
    plan = meaning(lifecycle={'expires':{'seconds':360}})
    active, original_api = make_port(plan,board,3600)
    active.poll(); board.replace('30m',rows([1800,3600,5400])); original_api.now = 5400; active.poll()
    restored, api = make_port(plan,board,5400)
    api.saved = active.checkpoint()
    api.armed['CUSTOM:BRANCH:999:OZ'] = {'symbol':'TEST'}
    api.armed['OTHER:BRANCH:0:OZ'] = {'symbol':'TEST'}
    restored.install()
    assert restored.machines[0].active
    assert 'CUSTOM:BRANCH:999:OZ' not in api.armed
    assert api.armed['CUSTOM:BRANCH:0:OZ']['watch_owner'] == 'KIM'
    assert 'OTHER:BRANCH:0:OZ' in api.armed
    changed = copy.deepcopy(plan); changed['lifecycle']['expires']['seconds'] = 400
    new_port = IntentPort(SimpleNamespace(special_api=api),changed,'Changed','CUSTOM')
    new_port.install()
    assert 'CUSTOM:BRANCH:0:OZ' not in api.armed
    assert 'OTHER:BRANCH:0:OZ' in api.armed
