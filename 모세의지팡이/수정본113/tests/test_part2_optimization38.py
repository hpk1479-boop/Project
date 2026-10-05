"""Result-preserving optimizations: keys, live selection and ownership boundaries."""
from dataclasses import FrozenInstanceError
from pathlib import Path
from types import SimpleNamespace
import sys
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'Part1/program'), str(ROOT / 'Part2')]
from event_engine.model import Kind, Resolution, Subscriptions
from event_engine.subscription_cache import cached_subscriptions, _cached


@pytest.mark.parametrize('kwargs', [
    {}, {'symbols': ['XAUUSD+']}, {'timeframes': ['1m', '5m']},
    {'facts': ['ATR14_GENERAL']}, {'processor_states': ['OZ_STATE']},
    {'kinds': [Kind.MARKET_BUNDLE, Kind.SIGNAL]}, {'resolution': 0},
    {'boundaries': []}, {'boundaries': [{'future': 'unhashable descriptor'}]},
])
def test_subscription_constructor_equivalence(kwargs):
    assert cached_subscriptions(**kwargs) == Subscriptions(**kwargs)


def test_subscription_cache_bounded_and_frozen():
    _cached.cache_clear()
    first = cached_subscriptions(symbols=('XAUUSD+',))
    assert first is cached_subscriptions(symbols=['XAUUSD+'])
    with pytest.raises(FrozenInstanceError):
        first.symbols = ('NAS100',)
    for i in range(300):
        cached_subscriptions(symbols=(str(i),))
    assert _cached.cache_info().currsize == 256
    assert cached_subscriptions(symbols=('XAUUSD+',)) == first


@pytest.mark.parametrize('module,cls,args', [
    ('composition_consumer', 'CompositionConsumer', ('test', None, None)),
    ('fvg_state', 'FVGProcessor', (None, None)),
    ('oz_state', 'OZCandidateProcessor', (None, {})),
    ('oz_processor', 'OZProcessor', (None, None, {})),
    ('indicator_consumer', 'IndicatorConsumer', (None, None, None, None)),
    ('sweep_state', 'SweepProcessor', (None, None, None, None)),
    ('watch_consumer', 'WatchConditionConsumer', (None, None, {})),
])
def test_live_symbol_reassignment_updates_subscription(module, cls, args):
    from importlib import import_module
    obj = getattr(import_module('event_engine.' + module), cls)(*args, symbols=('XAUUSD+',))
    first = obj.subscriptions()
    assert obj.subscriptions() is first
    obj.symbols = ['NAS100']
    assert obj.subscriptions().symbols == ('NAS100',)
    assert first.symbols == ('XAUUSD+',)


def test_watch_selection_change_recomputes_full_key():
    from event_engine.composition_consumer import CompositionConsumer
    c = CompositionConsumer('COMPOSER', None, None)
    broad = c.subscriptions()
    assert broad.processor_states == ('OZ_STATE',)
    capabilities=frozenset({'WONBI','ATR','INDICATOR'})
    c.selection = SimpleNamespace(processors=frozenset(), capabilities=capabilities)
    narrow = c.subscriptions()
    assert narrow.processor_states == ()
    c.selection = SimpleNamespace(processors=frozenset({'OZ_STATE'}), capabilities=capabilities)
    assert c.subscriptions() == broad
    assert narrow.processor_states == ()


@pytest.mark.parametrize('family', ['TREND', 'SWEEP', 'FVG', 'MA', 'WONBI', 'PERCENTILE'])
def test_condition_scope_is_original_identity(family):
    from event_composer_domain import _cached_condition_scope
    from durable_protocol import fact_scope
    for symbol in ('XAUUSD+', 'NAS100', 'BTCUSD'):
        for tf in ('1m', '5m', '1h', '1d'):
            for wid in (None, '', 'watch-1'):
                event = {'strategy': family, 'symbol': symbol, 'source_tf': tf, 'watch_id': wid}
                assert _cached_condition_scope(family, symbol, tf, wid) == fact_scope(event)


@pytest.mark.parametrize('period,mult', [(14, 1), (14.0, 1.0), (14, 1.5), (21, 2.0)])
def test_sweep_id_matches_original_with_typed_numeric_keys(period, mult):
    from event_composer_domain import _cached_sweep_watch_id
    from watch_orchestrator import stable_id
    args = ('XAUUSD+', '5m', 'PDH,PDL', period, mult, '08:00-17:00', '13:00-22:00')
    assert _cached_sweep_watch_id(*args) == stable_id('CMP:SWEEP', *args)


def _manager_stub():
    from event_composer_domain import ComposerManager
    m = ComposerManager.__new__(ComposerManager)
    m.config = {'MAIN_LONDON': '08:00-17:00', 'MAIN_NEWYORK': '13:00-22:00'}
    m._source_bindings = {}
    m.source_status = {}
    spec = SimpleNamespace(symbol='XAUUSD+', sweep_levels=('PDH', 'PDL'),
                           sweep_atr_period=14, sweep_atr_mult=1.0)
    cond = SimpleNamespace(kind='SWEEP', tf='5m', side='')
    return m, spec, cond


def test_config_changes_and_replacement_binding_are_never_cached():
    from durable_protocol import fact_scope
    from watch_orchestrator import stable_id
    m, spec, cond = _manager_stub()
    original = m._sweep_subscription_payload(spec, cond)
    original['levels'].append('caller mutation')
    for key, value in [('LONDON', '09:00-16:00'), ('NEWYORK', '14:00-20:00'),
                       ('LONDON', ''), ('MAIN_LONDON', '06:00-14:00'),
                       ('NEWYORK', ''), ('MAIN_NEWYORK', '11:00-19:00')]:
        m.config[key] = value
        payload = m._sweep_subscription_payload(spec, cond)
        assert payload['levels'] == ['PDH', 'PDL']
        expected = stable_id('CMP:SWEEP', spec.symbol, cond.tf, 'PDH,PDL', 14, 1.0,
                             payload['session_london'], payload['session_newyork'])
        assert payload['watch_id'] == expected
        scope, missing = m._condition_source_binding(spec, cond)
        assert scope == fact_scope({'strategy': 'SWEEP', 'symbol': spec.symbol,
                                   'source_tf': cond.tf, 'watch_id': expected})
        binding = {'sources': {'5m': 'epoch-1'}, 'indicators': ['PRICE']}
        m._source_bindings[scope] = binding
        assert m._condition_source_binding(spec, cond)[1] is binding
        replacement = {'sources': {'5m': 'epoch-2'}}
        m._source_bindings[scope] = replacement
        assert m._condition_source_binding(spec, cond)[1] is replacement
        m._source_bindings.clear()
        assert m._condition_source_binding(spec, cond)[1] == {}


@pytest.mark.parametrize('attribute,value', [('symbol', 'NAS100'), ('sweep_levels', ('PDL',)),
                                             ('sweep_atr_period', 21), ('sweep_atr_mult', 2.0)])
def test_changed_spec_changes_sweep_key(attribute, value):
    m, spec, cond = _manager_stub()
    original = m._condition_source_binding(spec, cond)[0]
    setattr(spec, attribute, value)
    assert m._condition_source_binding(spec, cond)[0] != original


def test_current_health_epoch_and_indicators_still_control_usability():
    m, spec, cond = _manager_stub()
    scope, _ = m._condition_source_binding(spec, cond)
    m._source_bindings[scope] = {'sources': {'5m': 'epoch-1'}, 'indicators': ['PRICE']}
    health = {'5m': {'status': 'FRESH', 'source_epoch': 'epoch-1',
                    'age_seconds': 0.0, 'stale_seconds': 30.0, 'indicators': {'PRICE': True}}}
    m.staff = SimpleNamespace(health=lambda *args: health)
    assert m._condition_source_usable(spec, cond)
    for key, value in [('status', 'STALE'), ('source_epoch', 'epoch-2'),
                       ('age_seconds', 31.0), ('indicators', {'PRICE': False})]:
        saved = health['5m'][key]
        health['5m'][key] = value
        assert not m._condition_source_usable(spec, cond)
        health['5m'][key] = saved
        assert m._condition_source_usable(spec, cond)
    m._source_bindings[scope]['sources']['5m'] = ''
    assert not m._condition_source_usable(spec, cond)
    assert m.source_status[scope] == 'UNKNOWN'


def test_identity_cache_memory_is_bounded():
    from event_composer_domain import _cached_sweep_watch_id, _cached_condition_scope
    _cached_condition_scope.cache_clear()
    _cached_sweep_watch_id.cache_clear()
    for i in range(4200):
        _cached_condition_scope('SWEEP', str(i), '1m', 'w')
        _cached_sweep_watch_id(str(i), '1m', 'PDH', 14, 1.0, '', '')
    assert _cached_condition_scope.cache_info().currsize == 4096
    assert _cached_sweep_watch_id.cache_info().currsize == 4096


@pytest.mark.parametrize('family', ['FVG', 'SWEEP', 'OZ', 'TREND', 'WATCH'])
@pytest.mark.parametrize('sealed', [False, True])
def test_prepared_facts_match_normal_emission_and_isolate_mutations(family, sealed):
    from dataclasses import dataclass
    import numpy as np
    import pandas as pd
    from event_engine.model import freeze
    from event_engine.domain_support import emit_facts, plain
    @dataclass
    class Descriptor:
        value: int
    raw = {'symbol': 'XAUUSD+', 'kind': 'FACT', 'source_tf': '1m',
           'nested': {'values': [np.int64(3), np.float64(1.25), Descriptor(4)],
                      'set': {'z', 'a'}, 'timestamp': pd.Timestamp('2026-09-01')},
           'event_id': 'fixed-id'}
    prepared = plain(raw)
    events = freeze([prepared]) if sealed else [prepared]
    before, after = [], []
    emit_facts(before.append, 'fallback', family, events)
    emit_facts(after.append, 'fallback', family, events, prepared=True)
    assert before == after
    raw['nested']['values'][0] = 900
    prepared['nested']['values'][1] = 900
    assert after[0].content['event']['nested']['values'][:2] == (3, 1.25)
    with pytest.raises(TypeError):
        after[0].content['event']['nested']['values'][0] = 99
    if sealed:
        assert after[0].content['event'] is events[0]


def test_default_unprepared_fact_conversion_is_preserved():
    import numpy as np
    from event_engine.domain_support import emit_facts
    results = []
    emit_facts(results.append, 'fallback', 'TEST', [{'kind': 'K', 'source_tf': '1m',
                                                   'values': {np.int64(4), np.int64(2)}}])
    assert results[0].symbol == 'fallback'
    assert results[0].condition_key == 'TEST:K:1m:0'
    assert results[0].content['event']['values'] == (2, 4)
    assert type(results[0].content['event']['values'][0]) is int


def test_prepared_path_skips_only_the_second_plain(monkeypatch):
    import event_engine.domain_support as module
    from oz_engine.runtime import Collector
    from event_engine.model import freeze
    collector = Collector()
    original = {'kind': 'K', 'values': [1, 2]}
    collector(original)
    original['values'][0] = 99
    assert collector.events[0]['values'] == [1, 2]
    def forbidden(*args):
        raise AssertionError('redundant plain conversion')
    monkeypatch.setattr(module, 'plain', forbidden)
    results = []
    module.emit_facts(results.append, 'XAUUSD+', 'OZ', collector.events, prepared=True)
    module.emit_facts(results.append, 'XAUUSD+', 'OZ', freeze(collector.events), prepared=True)
    assert results[0] == results[1]
    collector.events[0]['values'][0] = 88
    assert results[0].content['event']['values'][0] == 1


def test_fact_port_owns_first_conversion_and_sequence():
    import durable_protocol
    import numpy as np
    from event_engine.domain_support import FactPort, emit_facts
    state = {}
    port = FactPort(durable_protocol, 'FVG', state)
    event = {'kind': 'FVG_CREATED', 'symbol': 'XAUUSD+', 'source_tf': '1m',
             'event_id': 'event-1', 'nested': {'value': [np.int64(2)]}}
    port.send(event)
    event['nested']['value'][0] = 99
    assert port.events[0]['nested']['value'] == [2]
    assert type(port.events[0]['nested']['value'][0]) is int
    assert state['sequence'] == 1
    expected, actual = [], []
    emit_facts(expected.append, 'XAUUSD+', 'FVG', port.events)
    emit_facts(actual.append, 'XAUUSD+', 'FVG', port.events, prepared=True)
    assert expected == actual


def _wire_samples():
    import numpy as np
    import staff_schema as wire
    x = np.arange(6 * len(wire.PIPE_VALUE_COLUMNS), dtype='<f8').reshape(6, -1)
    # Preserve NaN payload bits, both signed zeros and extreme IEEE values.
    x.view('<u8')[0, :6] = [0, 2**63, 0x7ff8000000000001, 0x7ff8000000000002,
                            0x7ff0000000000000, 0xfff0000000000000]
    for seq, (times, values, kind) in enumerate([
        ([1,2,3,4,5,6], x, 1), ([2,3,4,5,6,7], x + 1, 1),
        ([7], x[-1:], 2), ([8], x[-1:] + 2, 2),
        ([], np.empty((0, x.shape[1])), 3), ([3,4,5,6,7,8], x, 1),
    ], 1):
        children = [wire.pack_v2('XAUUSD+', tf, times, np.arange(len(times)), values,
                                 seq=seq, kind=kind) for tf in ('1m', '5m')]
        yield wire.pack_bundle('XAUUSD+', children, seq=seq, sent_at_ms=seq * 1000)


@pytest.mark.parametrize('verify_crc', [False, True])
def test_delta_bytes_and_previous_outputs_stay_identical(verify_crc):
    from event_backtest.delta import DeltaCodec
    encoder, decoder = DeltaCodec(), DeltaCodec(verify_crc=verify_crc)
    decoded, expected = [], list(_wire_samples())
    for raw in expected:
        structure, bits = encoder.encode(raw)
        decoded.append(decoder.decode(structure, bits))
        assert decoded[-1] == raw
        assert type(decoded[-1]) is bytes
        assert decoded == expected[:len(decoded)]


@pytest.mark.parametrize('corruption', ['truncate', 'trailing', 'bits', 'crc'])
def test_delta_rejects_corrupt_input(corruption):
    from event_backtest.delta import DeltaCodec
    raw = next(_wire_samples())
    structure, bits = DeltaCodec().encode(raw)
    if corruption == 'truncate':
        structure = structure[:-1]
    elif corruption == 'trailing':
        structure += b'!'
    elif corruption == 'bits':
        bits = bits.copy()
        bits[0] ^= 1
    else:
        # The preserved outer CRC follows the two-byte prefix length and prefix.
        import struct
        offset = 2 + struct.unpack_from('<H', structure)[0]
        data = bytearray(structure)
        data[offset] ^= 1
        structure = bytes(data)
    with pytest.raises((ValueError, IndexError)):
        DeltaCodec().decode(structure, bits)
