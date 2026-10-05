"""Revision42 Fact optimization contracts; all market inputs are synthetic."""
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from types import MappingProxyType
import sys

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'Part1/program'))
import indicator_facts as registry
import event_engine.facts as module
from event_engine.model import FeedSnapshot
from staff_schema import PIPE_VALUE_COLUMNS

KEY = ('FACT42', '1m')
COLS = {name: i for i, name in enumerate(PIPE_VALUE_COLUMNS)}


def snapshot(*, rows=650, seq=1, epoch='42:1', shift=0, changes=(), volume_changes=()):
    time = np.arange(rows, dtype='<i8') * 60 + 1756684800 + shift
    values = np.full((rows, len(COLS)), 100., dtype='<f8')
    values[:, COLS['open']] = 100 + np.arange(rows) * .01
    values[:, COLS['high']] = values[:, COLS['open']] + 2
    values[:, COLS['low']] = values[:, COLS['open']] - 2
    values[:, COLS['close']] = values[:, COLS['open']] + .5
    values[:, COLS['wonbi_upper']] = values[:, COLS['open']] + 3
    values[:, COLS['wonbi_lower']] = values[:, COLS['open']] - 3
    volume = np.arange(rows, dtype='<i8') + 1
    for row, col, value in changes:
        values[row, COLS[col]] = value
    for row, value in volume_changes:
        volume[row] = value
    return FeedSnapshot(time, volume, values, seq, epoch)


def registered(monkeypatch, name, deps=('@high',), *, axis='forming_bar',
               owner='fact42_owner', shared=True, compute=None, array_compute=None):
    if compute is None:
        compute = lambda frame, *args: frame.df['high']
    spec = registry.FactSpec(name, tuple(deps), compute, owner=owner,
                            invalidation=axis, shared=shared, array_compute=array_compute)
    monkeypatch.setitem(registry.FACTS, name, spec)
    return spec


@pytest.fixture
def axes(monkeypatch):
    for name, raw, axis in (
        ('F42_FORM_HIGH', 'high', 'forming_bar'),
        ('F42_FORM_HIGH_2', 'high', 'forming_bar'),
        ('F42_CLOSE_HIGH', 'high', 'bar_close'),
        ('F42_CLOSE_CLOSE', 'close', 'bar_close'),
        ('F42_FORM_VOLUME', 'volume', 'forming_bar'),
    ):
        registered(monkeypatch, name, ('@' + raw,), axis=axis,
                   compute=lambda frame, *args, raw=raw: frame.df[raw])
    return ('F42_FORM_HIGH', 'F42_FORM_HIGH_2', 'F42_CLOSE_HIGH',
            'F42_CLOSE_CLOSE', 'F42_FORM_VOLUME')


def raw_dependencies(name, stack=()):
    if name in stack:
        raise ValueError('Fact dependency cycle: ' + name)
    result = set()
    for dep in registry.FACTS[name].deps:
        if dep.startswith('@'):
            result.add(dep[1:])
        else:
            result.update(raw_dependencies(dep, stack + (name,)))
    return frozenset(result | {'time'})


def raw_array(snap, name):
    if name in ('time', 'volume'):
        return getattr(snap, name)
    return snap.values[:, PIPE_VALUE_COLUMNS.index(name)]


CASES = [
    ('unchanged', {}, {}),
    ('forming_high', {}, {'changes': [(-1, 'high', 999.)]}),
    ('forming_close', {}, {'changes': [(-1, 'close', 999.)]}),
    ('closed_high', {}, {'changes': [(-2, 'high', 999.)]}),
    ('old_correction', {}, {'changes': [(10, 'high', 999.)]}),
    ('new_nan', {}, {'changes': [(10, 'high', np.nan)]}),
    ('same_nan', {'changes': [(10, 'high', np.nan)]}, {'changes': [(10, 'high', np.nan)]}),
    ('same_inf', {'changes': [(10, 'high', np.inf)]}, {'changes': [(10, 'high', np.inf)]}),
    ('different_inf', {'changes': [(10, 'high', np.inf)]}, {'changes': [(10, 'high', -np.inf)]}),
    ('signed_zero', {'changes': [(10, 'high', 0.)]}, {'changes': [(10, 'high', -0.)]}),
    ('epoch', {}, {'epoch': '42:2'}),
    ('new_bar', {}, {'shift': 60}),
    ('shorter', {}, {'rows': 649}),
    ('volume', {}, {'volume_changes': [(-1, 9999)]}),
    ('irrelevant_column', {}, {'changes': [(10, 'ema_20', 999.)]}),
    ('single_row_closed_empty', {'rows': 1}, {'rows': 1, 'changes': [(-1, 'high', 999.)]}),
]


@pytest.mark.parametrize('label,before,after', CASES, ids=[x[0] for x in CASES])
def test_step1_invalidation_matches_exact_contract(axes, label, before, after):
    prior, current = snapshot(**before), snapshot(seq=2, **after)
    values = {name: object() for name in axes}
    expected = {}
    if prior.source_epoch == current.source_epoch:
        for name, value in values.items():
            cut = slice(None, -1) if registry.FACTS[name].invalidation == 'bar_close' else slice(None)
            if all(np.array_equal(raw_array(prior, col)[cut], raw_array(current, col)[cut], equal_nan=True)
                   for col in raw_dependencies(name)):
                expected[name] = value
    cache = module.EventFacts()
    cache._entries[KEY] = (prior, values)
    cache.invalidate({KEY: current})
    actual = cache._entries[KEY][1]
    assert list(actual) == list(expected), label
    assert all(actual[name] is value for name, value in expected.items()), label


def test_step1_comparison_count_is_unique_per_axis_and_column(axes, monkeypatch):
    prior = snapshot()
    cache = module.EventFacts()
    cache._entries[KEY] = (prior, {name: object() for name in axes})
    original = np.array_equal
    calls = []
    def counted(a, b, *, equal_nan=False):
        assert equal_nan is True
        calls.append(len(a))
        return original(a, b, equal_nan=equal_nan)
    with monkeypatch.context() as patch:
        patch.setattr(module.np, 'array_equal', counted)
        cache.invalidate({KEY: snapshot(seq=2)})
    pairs = {(registry.FACTS[name].invalidation, col) for name in axes for col in raw_dependencies(name)}
    assert len(calls) == len(pairs) == 6
    assert calls.count(650) == 3 and calls.count(649) == 3


def test_step1_comparisons_do_not_leak_to_other_keys_or_updates(axes):
    cache = module.EventFacts()
    prior = snapshot()
    other = ('OTHER42', '5m')
    for key in (KEY, other):
        cache._entries[key] = (prior, {name: object() for name in axes})
    changed = snapshot(changes=[(-1, 'high', 999.)])
    cache.invalidate({KEY: snapshot(seq=2), other: changed})
    assert 'F42_FORM_HIGH' in cache._entries[KEY][1]
    assert 'F42_FORM_HIGH' not in cache._entries[other][1]
    assert 'F42_CLOSE_HIGH' in cache._entries[other][1]
    cache.invalidate({KEY: changed})
    assert 'F42_FORM_HIGH' not in cache._entries[KEY][1]
    assert 'F42_CLOSE_HIGH' in cache._entries[KEY][1]


def assert_value_equal(actual, expected):
    if isinstance(expected, Mapping):
        assert list(actual) == list(expected)
        for key in expected:
            assert_value_equal(actual[key], expected[key])
    elif isinstance(expected, np.ndarray):
        np.testing.assert_array_equal(actual, expected)
    else:
        assert actual == expected


def test_step1_cached_actual_indicators_equal_fresh_full_calculation():
    cache = module.EventFacts({'WONBI_SIGMA': 2.})
    variants = [{}, {}, {'changes': [(-1, 'close', 120.)]},
                {'changes': [(17, 'high', 160.)]}, {'shift': 60},
                {'shift': 120}, {'epoch': '42:2'}, {'rows': 649},
                {'changes': [(20, 'high', np.nan)]}]
    for seq, kw in enumerate(variants, 1):
        current = snapshot(seq=seq, **kw)
        cache.invalidate({KEY: current})
        fresh = module.EventFacts({'WONBI_SIGMA': 2.})
        fresh.invalidate({KEY: current})
        for name in ('ATR14_GENERAL', 'WONBI_BANDS'):
            actual = cache.get(*KEY, current, name, 'test42')
            expected = fresh.get(*KEY, current, name, 'test42')
            assert_value_equal(actual, expected)
        df = pd.DataFrame(current.values, columns=PIPE_VALUE_COLUMNS)
        expected_atr = registry.add_atr14_feature(df.copy())['atr_14'].to_numpy()
        np.testing.assert_array_equal(cache.get(*KEY, current, 'ATR14_GENERAL', 'test42'), expected_atr)


def test_step2_all_registered_raw_dependencies_and_time_are_preserved():
    for name in registry.FACTS:
        assert module.dependencies(name) == raw_dependencies(name)
        assert 'time' in module.dependencies(name)


def test_step2_unchanged_plan_does_not_repeat_recursive_resolution(monkeypatch):
    module._dependency_plan.cache_clear()
    expected = module.dependencies('ATR14_GENERAL')
    def denied(*args):
        raise AssertionError('unchanged dependency DAG traversed again')
    monkeypatch.setattr(module, '_resolve_dependencies', denied)
    for _ in range(20):
        assert module.dependencies('ATR14_GENERAL') is expected


def test_step2_transitive_replace_delete_and_reregister_are_seen(monkeypatch):
    leaf = registered(monkeypatch, 'F42_LEAF')
    registered(monkeypatch, 'F42_MIDDLE', ('F42_LEAF',))
    registered(monkeypatch, 'F42_ROOT', ('F42_MIDDLE',))
    assert module.dependencies('F42_ROOT') == frozenset(('time', 'high'))
    monkeypatch.setitem(registry.FACTS, 'F42_LEAF', replace(leaf, deps=('@low',)))
    assert module.dependencies('F42_ROOT') == frozenset(('time', 'low'))
    monkeypatch.delitem(registry.FACTS, 'F42_LEAF')
    with pytest.raises(KeyError, match='F42_LEAF'):
        module.dependencies('F42_ROOT')
    monkeypatch.setitem(registry.FACTS, 'F42_LEAF', replace(leaf, deps=('@close',)))
    assert module.dependencies('F42_ROOT') == frozenset(('time', 'close'))


def test_step2_root_replacement_and_registry_replacement_are_seen(monkeypatch):
    spec = registered(monkeypatch, 'F42_REPLACE')
    assert module.dependencies(spec.name) == frozenset(('time', 'high'))
    monkeypatch.setitem(registry.FACTS, spec.name, replace(spec, deps=('@volume',)))
    assert module.dependencies(spec.name) == frozenset(('time', 'volume'))
    replacement = dict(registry.FACTS)
    replacement[spec.name] = replace(spec, deps=('@low',))
    monkeypatch.setattr(module, 'FACTS', replacement)
    assert module.dependencies(spec.name) == frozenset(('time', 'low'))


def test_step2_cycles_unknown_names_and_explicit_stack_are_not_hidden(monkeypatch):
    a = registered(monkeypatch, 'F42_A')
    registered(monkeypatch, 'F42_B', ('F42_A',))
    module.dependencies('F42_B')
    monkeypatch.setitem(registry.FACTS, 'F42_A', replace(a, deps=('F42_B',)))
    with pytest.raises(ValueError, match='Fact dependency cycle'):
        module.dependencies('F42_B')
    with pytest.raises(KeyError, match='F42_MISSING'):
        module.dependencies('F42_MISSING')
    with pytest.raises(ValueError, match='Fact dependency cycle'):
        module.dependencies('ATR14_GENERAL', ('tr',))


def test_step2_plan_cache_is_bounded(monkeypatch):
    module._dependency_plan.cache_clear()
    for i in range(270):
        name = 'F42_BOUND_' + str(i)
        registered(monkeypatch, name)
        assert module.dependencies(name) == frozenset(('time', 'high'))
    assert module._dependency_plan.cache_info().currsize <= 256


def test_step2_column_table_preserves_values_views_and_unknown_error():
    current = snapshot()
    for name in ('time', 'volume') + tuple(PIPE_VALUE_COLUMNS):
        actual = module.array(current, name)
        expected = raw_array(current, name)
        np.testing.assert_array_equal(actual, expected)
        assert np.shares_memory(actual, expected)
    with pytest.raises(ValueError):
        module.array(current, 'F42_UNKNOWN_COLUMN')


def test_step2_mixed_axes_are_still_rejected(monkeypatch):
    registered(monkeypatch, 'F42_CLOSED', axis='bar_close')
    registered(monkeypatch, 'F42_MIXED', ('F42_CLOSED',))
    current = snapshot()
    cache = module.EventFacts()
    cache.invalidate({KEY: current})
    with pytest.raises(ValueError, match='Fact dependency axes differ'):
        cache.get(*KEY, current, 'F42_MIXED', 'test42')


def test_step3_cached_private_fact_still_checks_current_requester(monkeypatch):
    registered(monkeypatch, 'F42_PRIVATE', shared=False, owner='owner42')
    current = snapshot()
    cache = module.EventFacts()
    cache.invalidate({KEY: current})
    cache.get(*KEY, current, 'F42_PRIVATE', 'owner42')
    with pytest.raises(ValueError, match='private Fact belongs to owner42'):
        cache.get(*KEY, current, 'F42_PRIVATE', 'intruder42')
    assert cache.computed[(*KEY, 'F42_PRIVATE')] == 1


def test_step3_cached_parent_still_checks_changed_child_permission(monkeypatch):
    child = registered(monkeypatch, 'F42_AUTH_CHILD')
    registered(monkeypatch, 'F42_AUTH_PARENT', ('F42_AUTH_CHILD',),
               owner='parent42', compute=lambda f, child: child)
    current = snapshot()
    cache = module.EventFacts()
    cache.invalidate({KEY: current})
    cache.get(*KEY, current, 'F42_AUTH_PARENT', 'test42')
    monkeypatch.setitem(registry.FACTS, child.name, replace(child, owner='other42', shared=False))
    with pytest.raises(ValueError, match='private Fact belongs to other42'):
        cache.get(*KEY, current, 'F42_AUTH_PARENT', 'test42')


def test_step3_public_array_header_and_bytes_stay_isolated():
    current = snapshot()
    cache = module.EventFacts()
    cache.invalidate({KEY: current})
    one = cache.get(*KEY, current, 'ATR14_GENERAL', 'test42')
    expected = one.copy()
    one.shape = (65, 10)
    two = cache.get(*KEY, current, 'ATR14_GENERAL', 'test42')
    assert two.shape == (650,)
    assert one is not two and np.shares_memory(one, two)
    np.testing.assert_array_equal(two, expected)
    with pytest.raises(ValueError):
        two.setflags(write=True)
    with pytest.raises(ValueError):
        two[0] = 0
    assert cache.computed[(*KEY, 'ATR14_GENERAL')] == 1


@pytest.mark.parametrize('kind', ['series', 'frame', 'array', 'map', 'proxy', 'list', 'tuple', 'scalar', 'none'])
def test_step3_cached_return_types_keep_freeze_contract(monkeypatch, kind):
    values = {'series': lambda: pd.Series([1., 2.]),
              'frame': lambda: pd.DataFrame({'x': [1., 2.]}),
              'array': lambda: np.array([1., 2.]),
              'map': lambda: {'nested': [1., 2.]},
              'proxy': lambda: MappingProxyType({'nested': [1., 2.]}),
              'list': lambda: [1., 2.], 'tuple': lambda: (1., 2.),
              'scalar': lambda: 2., 'none': lambda: None}
    name = 'F42_RETURN_' + kind
    registered(monkeypatch, name, compute=lambda f, *args: values[kind]())
    current = snapshot()
    cache = module.EventFacts()
    cache.invalidate({KEY: current})
    one = cache.get(*KEY, current, name, 'test42')
    two = cache.get(*KEY, current, name, 'test42')
    assert type(one) is type(two)
    assert_value_equal(one, two)
    assert cache.computed[(*KEY, name)] == 1
    if isinstance(two, np.ndarray):
        assert not two.flags.writeable
    if isinstance(two, Mapping):
        with pytest.raises(TypeError):
            two['injected'] = 5


def test_step3_hit_has_no_compute_frame_or_resolving_allocations(monkeypatch):
    current = snapshot()
    cache = module.EventFacts()
    cache.invalidate({KEY: current})
    expected = cache.get(*KEY, current, 'ATR14_GENERAL', 'test42')
    def denied(*args, **kwargs):
        raise AssertionError('cache hit entered calculation preparation')
    monkeypatch.setattr(module, 'set', denied, raising=False)
    monkeypatch.setattr(module, 'SimpleNamespace', denied)
    monkeypatch.setattr(module, 'dependencies', denied)
    np.testing.assert_array_equal(cache.get(*KEY, current, 'ATR14_GENERAL', 'test42'), expected)


def test_step3_mismatched_snapshot_remove_clear_and_parameters():
    current = snapshot()
    cache = module.EventFacts({'WONBI_SIGMA': 2.})
    cache.invalidate({KEY: current})
    cache.get(*KEY, current, 'ATR14_GENERAL', 'test42')
    newer = snapshot(changes=[(-1, 'high', 999.)])
    actual = cache.get(*KEY, newer, 'ATR14_GENERAL', 'test42')
    fresh = module.EventFacts()
    expected = fresh.get(*KEY, newer, 'ATR14_GENERAL', 'test42')
    np.testing.assert_array_equal(actual, expected)
    assert cache._entries[KEY][0] is current
    with pytest.raises(TypeError):
        cache.parameters['WONBI_SIGMA'] = 3.
    cache.remove([KEY])
    assert KEY not in cache._entries and not cache._recurrences
    cache.invalidate({KEY: current})
    cache.get(*KEY, current, 'ATR14_GENERAL', 'test42')
    cache.clear()
    assert not cache._entries and not cache._recurrences


def test_integration_live_ingress_and_replay_use_same_fact_decisions():
    from event_engine import EventEngine, IngressSequencer, Kind, Subscriptions, Signal, Resolution
    from event_engine.model import Input
    from event_engine.replay import replay
    class Probe:
        name = 'fact42_probe'
        def subscriptions(self):
            return Subscriptions(timeframes=('1m',), facts=('ATR14_GENERAL', 'WONBI_BANDS'))
        def on_event(self, event, board, state, emit):
            atr = board.fact('ATR14_GENERAL', *KEY)
            again = board.fact('ATR14_GENERAL', *KEY)
            assert_value_equal(atr, again)
            bands = board.fact('WONBI_BANDS', *KEY)
            state['observations'] = state.get('observations', 0) + 1
            if atr[-1] > 4.1:
                emit(Signal(KEY[0], 'threshold', {'direction': 'LONG', 'atr': float(atr[-1]),
                                                'band': float(bands['wonbi_upper'][-1]), 'recipients': (11, 22)}))
    inputs = []
    variants = [{}, {}, {'changes': [(-1, 'high', 120.)]}, {},
                {'changes': [(10, 'high', 500.)]}, {'shift': 60},
                {'epoch': '42:2', 'changes': [(-1, 'high', 125.)]}]
    for seq, kw in enumerate(variants, 1):
        inputs.append(Input('synthetic42', seq, seq * 1000, Kind.MARKET_BUNDLE,
                            {'symbol': KEY[0], 'feeds': {'1m': snapshot(seq=seq, **kw)}}, 0))
    live = EventEngine(IngressSequencer(), [Probe()])
    for item in inputs:
        live.ingress.post(item.kind, source=item.source, source_seq=item.source_seq,
                          source_time=item.source_time, payload=item.payload)
        live.run()
    replayed = EventEngine(IngressSequencer(), [Probe()])
    result = replay(replayed, inputs, Resolution.TICK)
    def signals(engine):
        return [(event.source_time, dict(event.payload)) for event in engine.signals]
    assert result['input_count'] == len(inputs) and not result['approximate']
    assert not live.error_log and not replayed.error_log
    assert len(live.signals) == 2
    assert signals(live) == signals(replayed)
    assert live.strategy_state == replayed.strategy_state
    assert live.facts.computed == replayed.facts.computed


def test_integration_part2_capture_live_pipe_and_replay_adapter_match(tmp_path, monkeypatch):
    """Actual CaptureInputs transports over synthetic Wire; no Windows/MT5/SQL."""
    import socket
    import struct
    sys.path.insert(0, str(ROOT / 'Part2'))
    from event_backtest.bridge import CaptureInputs
    from event_host import load_staff
    from event_engine import EventEngine, IngressSequencer, Kind, Subscriptions, Signal
    from event_engine.replay import replay
    import staff_schema as wire
    def denied(*args, **kwargs):
        raise AssertionError('network is forbidden in synthetic transport test')
    monkeypatch.setattr(socket.socket, 'connect', denied)
    symbol = 'XAUUSD+'
    variants = [{}, {}, {'changes': [(-1, 'high', 120.)]}, {},
                {'changes': [(10, 'high', 500.)]}, {'shift': 60},
                {'shift': 60, 'changes': [(-1, 'high', 125.)]}]
    snaps = [snapshot(seq=i, **kw) for i, kw in enumerate(variants, 1)]
    capture = tmp_path / 'synthetic_capture'
    capture.mkdir()
    with (capture / 'pipe_000.bin').open('wb') as stream:
        stream.write(struct.pack('<IIII', 0x4D535033, 2, len(PIPE_VALUE_COLUMNS), 650))
        for i, current in enumerate(snaps, 1):
            raw = wire.pack_v2(symbol, '1m', current.time, current.volume, current.values, seq=i)
            observed = int(snaps[0].time[-1]) * 1000 + i * 10000
            stream.write(struct.pack('<qiI', observed, 31, len(raw)))
            stream.write(raw)
    (capture / 'complete.txt').write_text('complete', encoding='ascii')
    (capture / 'manifest.tsv').write_text(
        'MSP3\nsymbol\t' + symbol + '\npipe_capture\tSTAFF_PIPE_V2\n'
        'pipe_observation_unit\tmilliseconds\npipe_feed\t0\t1m\tpipe_000.bin\t'
        + str(len(snaps)) + '\n', encoding='ascii')
    class Probe:
        name = 'wire42'
        def subscriptions(self):
            return Subscriptions(timeframes=('1m',), facts=('ATR14_GENERAL', 'WONBI_BANDS'))
        def on_event(self, event, board, state, emit):
            atr = board.fact('ATR14_GENERAL', symbol, '1m')
            repeated = board.fact('ATR14_GENERAL', symbol, '1m')
            np.testing.assert_array_equal(atr, repeated)
            bands = board.fact('WONBI_BANDS', symbol, '1m')
            state['seen'] = state.get('seen', 0) + 1
            if atr[-1] > 4.1:
                emit(Signal(symbol, 'wire42_threshold', {
                    'direction': 'LONG', 'atr': float(atr[-1]),
                    'band': float(bands['wonbi_upper'][-1]), 'recipients': ('42',)}))
    outputs = []
    for transport in ('live', 'replay'):
        staff = load_staff()
        clock = [0.]
        cache = staff.StaffPipeCache('', health_session='FACT42', monotonic=lambda: clock[0],
                                     gap_journal=tmp_path / (transport + '_gap.jsonl'))
        inputs = CaptureInputs(staff, cache, [capture], transport=transport, clock=clock)
        engine = EventEngine(IngressSequencer(), [Probe()])
        result = replay(engine, inputs)
        assert not engine.error_log
        assert result['input_count'] == len(snaps)
        assert len(engine.signals) == 2
        outputs.append(([(e.source_time, dict(e.payload)) for e in engine.signals],
                        engine.strategy_state, engine.facts.computed))
    assert outputs[0] == outputs[1]
