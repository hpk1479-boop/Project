"""Closed-history correction and immutable Wire publication regression tests."""
import importlib.util
import io
import copy
from datetime import datetime
from pathlib import Path
import sys
from types import SimpleNamespace

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'Part1/program'), str(ROOT / 'Part2')]
import staff_schema as wire
from event_engine.model import FeedSnapshot
from event_engine.market import MarketView, COLUMNS
from event_engine.fvg_structure import StructureStore
from event_engine.sweep_levels import LevelStore, external_levels

spec = importlib.util.spec_from_file_location('release_cache95_staff', ROOT / 'Part1/program/THE STAFF OF MOSES.py')
staff = importlib.util.module_from_spec(spec)
spec.loader.exec_module(staff)


def arrays(n=100, stride=60):
    values = np.full((n, len(COLUMNS)), 100., dtype='<f8')
    values[:, COLUMNS['high']] = 101.
    values[:, COLUMNS['low']] = 99.
    times = np.arange(n, dtype='<i8') * stride + 1790985600
    return times, np.ones(n, dtype='<i8'), values


class Stream:
    def __init__(self, tmp_path):
        self.cache = staff.StaffPipeCache('', health_session='cache95', monotonic=lambda: 0., gap_journal=tmp_path / 'gaps.jsonl')
        self.seq = 0

    def publish(self, tf, times=None, volume=None, values=None, kind=wire.WIRE_FULL):
        self.seq += 1
        packet = wire.pack_v2('TEST', tf, times, volume, values, seq=self.seq, kind=kind)
        raw = wire.pack_bundle('TEST', [packet], seq=self.seq, sent_at_ms=1791080000000 + self.seq)
        publication = self.cache.receive_publication(raw=raw, require_observation=True)
        snap = publication.feeds[tf]
        return MarketView(FeedSnapshot(snap.time, snap.volume, snap.values, snap.seq, snap.source_epoch, snap.indicator_validity))


@pytest.mark.parametrize('position', [-2, 20])
@pytest.mark.parametrize('column', ['high', 'low', 'close'])
def test_fvg_corrected_closed_dependency_matches_full_recompute(tmp_path, position, column):
    stream = Stream(tmp_path)
    times, volume, values = arrays()
    store = StructureStore()
    first = stream.publish('1m', times, volume, values)
    store.evaluate('TEST', '1m', first)
    values[position, COLUMNS[column]] += .25
    corrected = stream.publish('1m', times, volume, values)
    assert corrected.close_key() == first.close_key()
    assert store.evaluate('TEST', '1m', corrected) == StructureStore().evaluate('TEST', '1m', corrected)
    assert store.computations == 2


def test_audited_fvg_correction_creates_zone_at_same_closed_timestamp(tmp_path):
    stream = Stream(tmp_path)
    times, volume, values = arrays()
    store = StructureStore()
    assert store.evaluate('TEST', '1m', stream.publish('1m', times, volume, values))['zones'] == []
    for column, value in {'open': 102.5, 'high': 103., 'low': 102., 'close': 102.5}.items():
        values[-2, COLUMNS[column]] = value
    corrected = stream.publish('1m', times, volume, values)
    actual = store.evaluate('TEST', '1m', corrected)
    assert actual == StructureStore().evaluate('TEST', '1m', corrected)
    assert len(actual['zones']) == 1


@pytest.mark.parametrize('column,position', [('high', -1), ('low', -1), ('close', -1), ('open', -2), ('ema_20', 20)])
def test_fvg_forming_and_unrelated_changes_do_not_recompute_structure(tmp_path, column, position):
    stream = Stream(tmp_path)
    times, volume, values = arrays()
    store = StructureStore()
    store.evaluate('TEST', '1m', stream.publish('1m', times, volume, values))
    values[position, COLUMNS[column]] += .25
    current = stream.publish('1m', times, volume, values)
    assert store.evaluate('TEST', '1m', current) == StructureStore().evaluate('TEST', '1m', current)
    assert store.computations == 1


@pytest.mark.parametrize('tf,stride', [('1d', 86400), ('4h', 14400), ('8h', 28800), ('5m', 300)])
@pytest.mark.parametrize('position', [-2, 20])
@pytest.mark.parametrize('column', ['high', 'low'])
def test_sweep_closed_dependency_corrections_match_current_full_recompute(tmp_path, tf, stride, position, column):
    stream = Stream(tmp_path)
    times, volume, values = arrays(stride=stride)
    store = LevelStore()
    first = stream.publish(tf, times, volume, values)
    store.get({tf: first}, '1600-2100', '2200-0300', 'TEST')
    values[position, COLUMNS[column]] += 20 if column == 'high' else -20
    corrected = stream.publish(tf, times, volume, values)
    actual = store.get({tf: corrected}, '1600-2100', '2200-0300', 'TEST')
    assert actual == external_levels({tf: corrected}, '1600-2100', '2200-0300')
    assert store.computations == 2
    if tf == '1d' and position == -2 and column == 'high':
        assert next(item['price'] for item in actual if item['level_code'] == 'PDH') == 121.


@pytest.mark.parametrize('column,position', [('high', -1), ('low', -1), ('close', -2), ('open', 20), ('ema_20', 20)])
def test_sweep_forming_and_unrelated_prices_do_not_recompute_levels(tmp_path, column, position):
    stream = Stream(tmp_path)
    times, volume, values = arrays(stride=86400)
    store = LevelStore()
    store.get({'1d': stream.publish('1d', times, volume, values)}, symbol='TEST')
    values[position, COLUMNS[column]] += .25
    current = stream.publish('1d', times, volume, values)
    assert store.get({'1d': current}, symbol='TEST') == external_levels({'1d': current})
    assert store.computations == 1


def test_full_row_heartbeat_new_bar_preserve_readers_and_current_results(tmp_path):
    stream = Stream(tmp_path)
    times, volume, values = arrays()
    old = stream.publish('1m', times, volume, values)
    original = old.values.copy()
    fvg, sweep = StructureStore(), LevelStore()
    for kind in ('initial', 'row', 'heartbeat', 'new_bar', 'correction'):
        if kind == 'initial': current = old
        elif kind == 'row':
            values[-1, COLUMNS['high']] = 110.
            volume[-1] = 5
            current = stream.publish('1m', times[-1:], volume[-1:], values[-1:], wire.WIRE_ROW)
        elif kind == 'heartbeat': current = stream.publish('1m', kind=wire.WIRE_HEARTBEAT)
        else:
            if kind == 'new_bar': times += 60
            else: values[20, COLUMNS['high']] += 1
            current = stream.publish('1m', times, volume, values)
        assert fvg.evaluate('TEST', '1m', current) == StructureStore().evaluate('TEST', '1m', current)
        assert sweep.get({'1d': current}, symbol='TEST') == external_levels({'1d': current})
        assert fvg.computations == sweep.computations == {'initial': 1, 'row': 1, 'heartbeat': 1, 'new_bar': 2, 'correction': 3}[kind]
        with pytest.raises(ValueError): current.values.setflags(write=True)
        np.testing.assert_array_equal(old.values, original)


def test_changed_interior_time_and_daily_forming_week_boundary_invalidate(tmp_path):
    stream = Stream(tmp_path)
    times, volume, values = arrays(stride=86400)
    fvg, sweep = StructureStore(), LevelStore()
    current = stream.publish('1d', times, volume, values)
    fvg.evaluate('TEST', '1d', current)
    sweep.get({'1d': current}, symbol='TEST')
    times[20] += 1
    current = stream.publish('1d', times, volume, values)
    assert fvg.evaluate('TEST', '1d', current) == StructureStore().evaluate('TEST', '1d', current)
    assert sweep.get({'1d': current}, symbol='TEST') == external_levels({'1d': current})
    assert fvg.computations == sweep.computations == 2
    times[-1] += 7 * 86400
    current = stream.publish('1d', times, volume, values)
    assert sweep.get({'1d': current}, symbol='TEST') == external_levels({'1d': current})
    assert sweep.computations == 3
    assert fvg.evaluate('TEST', '1d', current) == StructureStore().evaluate('TEST', '1d', current)
    assert fvg.computations == 2


@pytest.mark.parametrize('tf', ['4h', '8h'])
def test_four_eight_hour_forming_timestamp_is_not_a_level_dependency(tmp_path, tf):
    stream = Stream(tmp_path)
    times, volume, values = arrays(stride=300)
    store = LevelStore()
    store.get({tf: stream.publish(tf, times, volume, values)}, '1600-2100', '2200-0300', 'TEST')
    times[-1] += 1
    current = stream.publish(tf, times, volume, values)
    assert store.get({tf: current}, '1600-2100', '2200-0300', 'TEST') == external_levels({tf: current}, '1600-2100', '2200-0300')
    assert store.computations == 1


def test_five_minute_forming_timestamp_selects_session_but_prices_stay_closed(tmp_path):
    stream = Stream(tmp_path)
    times, volume, values = arrays(stride=300)
    times += int(datetime.fromisoformat('2026-10-09T15:55:00+09:00').timestamp()) - times[-1]
    values[-1, COLUMNS['high']] = 1001.
    values[-1, COLUMNS['low']] = 1.
    store = LevelStore()
    first = stream.publish('5m', times, volume, values)
    assert store.get({'5m': first}, '1600-2100', '2200-0300', 'TEST') == []

    # Even with unchanged closed history, the forming clock selects the new session.
    times[-1] += 300
    current = stream.publish('5m', times, volume, values)
    assert current.close_key() == first.close_key()
    levels = store.get({'5m': current}, '1600-2100', '2200-0300', 'TEST')
    assert levels == external_levels({'5m': current}, '1600-2100', '2200-0300')
    assert {level['level_code']: level['price'] for level in levels} == {
        'PREV_SESSION_HIGH': 101., 'PREV_SESSION_LOW': 99.}
    assert all(':LONDON:' in level['id'] for level in levels)
    assert store.computations == 2

    values[-1, COLUMNS['high']] = 2001.
    current = stream.publish('5m', times, volume, values)
    assert store.get({'5m': current}, '1600-2100', '2200-0300', 'TEST') == levels
    assert store.computations == 2


def test_immutable_fast_path_checks_owner_offset_shape_dtype_and_strides():
    from event_engine.model import same_immutable_array
    base = np.frombuffer(np.arange(12, dtype='<i8').tobytes(), dtype='<i8').reshape(3, 4)
    assert same_immutable_array(base, base.view())
    assert same_immutable_array(base[:, 2], base[:, 2])
    assert not same_immutable_array(base, base.reshape(2, 6))
    assert not same_immutable_array(base[:2], base[1:])
    assert not same_immutable_array(base, base[:, ::-1])
    assert not same_immutable_array(base, base.view('<f8'))
    mutable = np.frombuffer(bytearray(base.tobytes()), dtype='<i8').reshape(base.shape)
    readonly = mutable.view()
    readonly.flags.writeable = False
    assert not same_immutable_array(readonly, readonly.view())
    owning = np.arange(12, dtype='<i8')
    owning.flags.writeable = False
    assert not same_immutable_array(owning, owning.view())


def test_restored_calculation_caches_recheck_corrected_history(tmp_path):
    stream = Stream(tmp_path)
    times, volume, values = arrays()
    first = stream.publish('1m', times, volume, values)
    fvg, sweep = StructureStore(), LevelStore()
    fvg.evaluate('TEST', '1m', first)
    sweep.get({'1d': first}, symbol='TEST')
    restored_fvg, restored_sweep = copy.deepcopy((fvg, sweep))
    for column, number in {'open': 102.5, 'high': 103., 'low': 102., 'close': 102.5}.items():
        values[-2, COLUMNS[column]] = number
    current = stream.publish('1m', times, volume, values)
    assert restored_fvg.evaluate('TEST', '1m', current) == StructureStore().evaluate('TEST', '1m', current)
    assert restored_sweep.get({'1d': current}, symbol='TEST') == external_levels({'1d': current})
    assert fvg.evaluate('TEST', '1m', current) == restored_fvg.evaluate('TEST', '1m', current)
    assert sweep.get({'1d': current}, symbol='TEST') == restored_sweep.get({'1d': current}, symbol='TEST')


def test_heartbeat_skips_fact_scans_but_full_correction_is_checked(tmp_path, monkeypatch):
    import event_engine.facts as module
    stream = Stream(tmp_path)
    times, volume, values = arrays(650)
    first = stream.publish('1m', times, volume, values).snapshot
    facts = module.EventFacts()
    key = ('TEST', '1m')
    names = ('ATR14_GENERAL', 'WONBI_BANDS')
    facts.invalidate({key: first})
    before = {name: facts.get(*key, first, name, 'release95') for name in names}
    heartbeat = stream.publish('1m', kind=wire.WIRE_HEARTBEAT).snapshot
    original = np.array_equal
    scans = []
    def measured(*args, **kwargs):
        scans.append(len(args[0]))
        return original(*args, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(module.np, 'array_equal', measured)
        facts.invalidate({key: heartbeat})
    assert scans == []
    for name in names:
        actual = facts.get(*key, heartbeat, name, 'release95')
        if isinstance(actual, np.ndarray): np.testing.assert_array_equal(actual, before[name])
        else:
            for field in actual: np.testing.assert_array_equal(actual[field], before[name][field])
    values[20, COLUMNS['high']] = 120
    corrected = stream.publish('1m', times, volume, values).snapshot
    with monkeypatch.context() as patch:
        patch.setattr(module.np, 'array_equal', measured)
        facts.invalidate({key: corrected})
    assert scans
    fresh = module.EventFacts()
    fresh.invalidate({key: corrected})
    np.testing.assert_array_equal(facts.get(*key, corrected, 'ATR14_GENERAL', 'release95'),
                                  fresh.get(*key, corrected, 'ATR14_GENERAL', 'release95'))
    assert facts.computed[(*key, 'ATR14_GENERAL')] == 2


@pytest.mark.parametrize('unavailable', [np.nan, np.inf, -np.inf, 1.7e308, -1.7e308])
def test_row_bytes_publication_preserves_normalization_and_old_readers(tmp_path, unavailable):
    stream = Stream(tmp_path)
    times, volume, values = arrays(650)
    values[:, COLUMNS['ema_50']] = np.nan
    first = stream.publish('1m', times, volume, values)
    expected = values.copy()
    row = values[-1:].copy()
    row[0, COLUMNS['ema_50']] = unavailable
    row[0, COLUMNS['close']] = 100.5
    expected[-1, COLUMNS['close']] = 100.5
    new_volume = volume[-1:].copy() + 10
    current = stream.publish('1m', times[-1:], new_volume, row, wire.WIRE_ROW)
    np.testing.assert_array_equal(current.values, expected)
    np.testing.assert_array_equal(first.values, values)
    assert current.volume[-1] == 11 and first.volume[-1] == 1
    assert current.snapshot.indicator_validity == first.snapshot.indicator_validity
    row[:] = 999
    new_volume[:] = 999
    np.testing.assert_array_equal(current.values, expected)
    assert current.volume[-1] == 11
    for array in (current.time, current.volume, current.values):
        with pytest.raises(ValueError): array.setflags(write=True)


def test_same_wire_full_row_heartbeat_and_correction_match_live_and_capture_replay(tmp_path):
    from event_application import create_event_engine, register_strategy_loader, unregister_strategy_loader
    from event_engine.staff_adapter import StaffIngressAdapter
    from event_engine.capture_io import FILE_HEADER, RECORD_HEADER
    from event_backtest.bridge import CaptureInputs
    from strategy_recipe.contract import execution_plan
    from strategy_recipe.port import IntentPort
    from strategy_recipe.registry import dependencies

    value = execution_plan({'symbols': ['TEST'], 'direction': 'LONG', 'order_mode': 'SIMULTANEOUS',
        'steps': [{'kind': 'FVG_STATE', 'tfs': ['1m'], 'state': 'EXISTS'},
                  {'kind': 'EXTERNAL_LIQUIDITY_TOUCH', 'tfs': ['1m'], 'level': 'PDH'}],
        'final': {'kind': 'NOTIFY'}})['meaning']
    name = 'CACHE95'
    plugin = SimpleNamespace(__name__=name, register=lambda manager: IntentPort(manager, value, name, name).install(),
                             OZ_DECLARATIONS=())
    register_strategy_loader(name, lambda: plugin, dependencies=dependencies(value))
    records = {'1m': [], '1d': []}
    for tf, stride in [('1m', 60), ('1d', 86400)]:
        times, volume, values = arrays(stride=stride)
        for seq, kind in enumerate(('full', 'row', 'heartbeat', 'correction', 'new_bar'), 1):
            if kind == 'row':
                values[-1, COLUMNS['high']] = 110
                raw = wire.pack_v2('TEST', tf, times[-1:], volume[-1:], values[-1:], seq=seq, kind=wire.WIRE_ROW)
            elif kind == 'heartbeat': raw = wire.pack_v2('TEST', tf, seq=seq, kind=wire.WIRE_HEARTBEAT)
            else:
                if kind == 'correction':
                    for column, number in {'open': 102.5, 'high': 103., 'low': 102., 'close': 102.5}.items():
                        values[-2, COLUMNS[column]] = number
                if kind == 'new_bar': times += stride
                raw = wire.pack_v2('TEST', tf, times, volume, values, seq=seq)
            records[tf].append(raw)
    capture = tmp_path / 'capture'
    capture.mkdir()
    manifest = ['key\tvalue', 'symbol\tTEST', 'pipe_capture\tSTAFF_PIPE_V2', 'pipe_observation_unit\tmilliseconds']
    for index, (tf, items) in enumerate(records.items()):
        name_on_disk = 'feed_' + tf + '.bin'
        manifest.append(f'pipe_feed\t{index}\t{tf}\t{name_on_disk}\t{len(items)}')
        with (capture / name_on_disk).open('wb') as handle:
            handle.write(FILE_HEADER.pack(0x4D535033, 2, len(COLUMNS), 100))
            for seq, raw in enumerate(items, 1):
                handle.write(RECORD_HEADER.pack(1791080000000 + seq, 0, len(raw)))
                handle.write(raw)
    (capture / 'manifest.tsv').write_text('\n'.join(manifest), encoding='ascii')
    (capture / 'complete.txt').write_text('VERIFIED\n', encoding='ascii')
    try:
        outputs, calculations = [], []
        for replay in (False, True):
            engine = create_event_engine({'SYMBOLS': 'TEST', 'TELEGRAM_CHAT_ID': 'TEST'}, symbols=['TEST'], selection=[name], backtest=replay)
            cache = staff.StaffPipeCache('', health_session='cache95', monotonic=lambda: 0., gap_journal=tmp_path / 'unused.jsonl')
            if replay:
                for item in CaptureInputs(staff, cache, [capture], capture_start='beginning'):
                    engine.ingress.post(item.kind, source=item.source, source_seq=item.source_seq, source_time=item.source_time, payload=item.payload)
                    engine.run()
            else:
                adapter = StaffIngressAdapter(cache, engine.ingress)
                for seq in range(1, 6):
                    raw = wire.pack_bundle('TEST', [records[tf][seq-1] for tf in records], seq=seq, sent_at_ms=1791080000000 + seq)
                    adapter.receive_one(io.BytesIO(raw).read, allowed_symbols=('TEST',))
                    engine.run()
            assert not engine.error_log, engine.error_log
            current = {tf: MarketView(engine.board._feeds['TEST', tf]) for tf in records}
            fvg = engine.processor_state['FVG_STATE']['runtime'].structures
            sweep = engine.processor_state['SWEEP_STATE']['runtime'].levels
            fvg_result = fvg.evaluate('TEST', '1m', current['1m'])
            sweep_result = sweep.get({'1d': current['1d']}, symbol='TEST')
            assert fvg_result == StructureStore().evaluate('TEST', '1m', current['1m'])
            assert sweep_result == external_levels({'1d': current['1d']})
            outputs.append([signal.payload for signal in engine.signals])
            calculations.append((fvg_result, sweep_result))
        assert outputs[0] == outputs[1]
        assert calculations[0] == calculations[1]
        assert outputs[0], 'The comparison must include real processor events'
    finally:
        unregister_strategy_loader(name)
