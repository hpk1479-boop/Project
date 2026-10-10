"""수정본172: MT5 times are the broker's server clock; Moses reads them in real (Korean) time.

LIVE and replay convert at the same STAFF boundary with the same function (server_time.py), so the same
market moment reaches the engine with the same real times. Decisions on the clock (trading time, session
levels) follow Korean time; trading days (previous-week levels, anchored VWAP periods) stay the broker's
server days, as before.
"""
import datetime as dt
import io
import json
import socket
import struct
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'Part1/program'), str(ROOT / 'Part2')]
import server_time
from server_time import DEFAULT, IDENTITY, ServerTime
import staff_schema as wire
from event_backtest.bridge import CaptureInputs, Collector
from event_engine.model import FeedSnapshot, Kind
from event_engine.staff_adapter import StaffIngressAdapter
from event_host import load_staff

staff = load_staff()
COLUMNS = len(wire.PIPE_VALUE_COLUMNS)


@pytest.fixture(autouse=True)
def offline_and_inactive(monkeypatch):
    def deny(*a, **k):
        raise AssertionError('real network is forbidden')
    for name in ('connect', 'connect_ex', 'sendto'):
        monkeypatch.setattr(socket.socket, name, deny)
    yield
    server_time.activate(None)


def seconds(*args):
    return int(dt.datetime(*args, tzinfo=dt.timezone.utc).timestamp())


def text(value):
    return dt.datetime.fromtimestamp(value, dt.timezone.utc).strftime('%Y-%m-%d %H:%M')


# The conversion --------------------------------------------------------------------------------------

def test_the_default_broker_is_new_york_plus_seven():
    # LIVE record of 2026-10-06: a 1m bar the EA labelled 11:25 opened at 08:25 UTC (17:25 KST).
    assert text(DEFAULT.to_utc(seconds(2026, 10, 6, 11, 25))) == '2026-10-06 08:25'
    assert text(DEFAULT.to_utc(seconds(2026, 1, 15, 0, 30))) == '2026-01-14 22:30'
    # The week opens at server Monday 01:00 = Sunday 18:00 New York, on both sides of each US change;
    # the EU change (last Sunday of October) does not move it.
    for server, utc in [((2026, 3, 2, 1), '2026-03-01 23:00'), ((2026, 3, 9, 1), '2026-03-08 22:00'),
                        ((2026, 10, 26, 1), '2026-10-25 22:00'), ((2026, 11, 2, 1), '2026-11-01 23:00')]:
        assert text(DEFAULT.to_utc(seconds(*server))) == utc
        assert DEFAULT.to_server(DEFAULT.to_utc(seconds(*server))) == seconds(*server)
    # Milliseconds keep their sub-second part.
    assert DEFAULT.to_utc_ms(seconds(2026, 10, 6, 11, 25) * 1000 + 789) == seconds(2026, 10, 6, 8, 25) * 1000 + 789


def test_other_brokers_and_the_identity():
    eu = ServerTime(2, 'EU')
    assert text(eu.to_utc(seconds(2026, 3, 20, 12))) == '2026-03-20 10:00'     # US summer, EU not yet
    assert text(eu.to_utc(seconds(2026, 4, 1, 12))) == '2026-04-01 09:00'
    fixed = ServerTime(3, 'NONE')
    assert {text(fixed.to_utc(seconds(2026, m, 1, 12))) for m in (1, 7)} == {'2026-01-01 09:00', '2026-07-01 09:00'}
    assert IDENTITY.identity and IDENTITY.to_utc(123) == 123
    stamps = np.array([1, 2, 3], dtype='<i8')
    assert IDENTITY.to_utc_array(stamps) is stamps


def test_arrays_match_single_times_across_a_change():
    stamps = np.arange(seconds(2026, 3, 6, 20), seconds(2026, 3, 10, 2), 3600, dtype='<i8')
    for convert, single in ((DEFAULT.to_utc_array, DEFAULT.to_utc), (DEFAULT.to_server_array, DEFAULT.to_server)):
        converted = convert(stamps)
        assert converted.dtype == stamps.dtype
        assert converted.tolist() == [single(value) for value in stamps.tolist()]
    window = np.arange(seconds(2026, 7, 1), seconds(2026, 7, 2), 60, dtype='<i8')    # no change inside
    assert (window - DEFAULT.to_utc_array(window) == 3 * 3600).all()
    assert len(DEFAULT.to_utc_array(np.array([], dtype='<i8'))) == 0


def test_settings_and_recordings_name_the_clock():
    assert ServerTime.from_config({}) == DEFAULT == ServerTime.from_record(None)
    assert ServerTime.from_config({'SERVER_UTC_OFFSET': '+3', 'SERVER_DST': 'none'}) == ServerTime(3, 'NONE')
    assert ServerTime.from_record(ServerTime(-5, 'US').record()) == ServerTime(-5, 'US')
    assert DEFAULT.label() == 'UTC+2 · 미국 서머타임'
    for bad in ({'SERVER_UTC_OFFSET': '15'}, {'SERVER_UTC_OFFSET': '2.5'}, {'SERVER_UTC_OFFSET': 'x'},
                {'SERVER_DST': 'JP'}):
        with pytest.raises(ValueError):
            ServerTime.from_config(bad)
    from event_backtest.recording_time import recording_clock, clocks_of
    assert recording_clock([{}, {'server_time': DEFAULT.record()}]) == DEFAULT
    assert clocks_of([{}, {'server_time': {'utc_offset': 0, 'dst': 'NONE'}}]) == [DEFAULT, ServerTime(0, 'NONE')]
    with pytest.raises(ValueError):
        recording_clock([{}, {'server_time': {'utc_offset': 0, 'dst': 'NONE'}}])


# LIVE and replay ------------------------------------------------------------------------------------

def frame(end, seq, tf='1m', step=60, rows=30):
    times = np.arange(end - (rows - 1) * step, end + 1, step, dtype='<i8')
    values = np.full((rows, COLUMNS), 10., dtype='<f8'); values[:, :4] = 100.
    return wire.pack_v2('XAUUSD+', tf, times, np.ones(rows, dtype='<i8'), values, seq=seq)


def write_capture(root, rows):
    """MSP3 pieces: one 1m frame per observation (observed on the server clock)."""
    root.mkdir()
    with (root / 'pipe_000.bin').open('wb') as f:
        f.write(struct.pack('<IIII', 0x4D535033, 2, COLUMNS, 650))
        for observed, raw in rows:
            f.write(struct.pack('<qiI', observed, 31, len(raw))); f.write(raw)
    (root / 'complete.txt').write_text('complete')
    (root / 'manifest.tsv').write_text('MSP3\nsymbol\tXAUUSD+\npipe_capture\tSTAFF_PIPE_V2\npipe_observation_unit\t'
                                       'milliseconds\npipe_feed\t0\t1m\tpipe_000.bin\t' + str(len(rows)) + '\n')


def test_live_and_replay_give_the_engine_the_same_real_times(tmp_path):
    bar = seconds(2026, 10, 6, 11, 25)                  # server clock: the EA's bar labels
    observed = (bar + 40) * 1000                        # the recording: tick time on the server clock
    real = DEFAULT.to_utc_ms(observed)                  # LIVE: the EA's TimeGMT at the same moment
    live = Collector()
    adapter = StaffIngressAdapter(staff.StaffPipeCache('', monotonic=lambda: 0., gap_journal=tmp_path / 'live.jsonl'),
                                  live, server_time=DEFAULT)
    adapter.receive_one(io.BytesIO(wire.pack_bundle('XAUUSD+', [frame(bar, 1)], seq=1, sent_at_ms=real)).read)
    write_capture(tmp_path / 'capture', [(observed, frame(bar, 1))])
    clock = [0.]
    cache = staff.StaffPipeCache('', monotonic=lambda: clock[0], gap_journal=tmp_path / 'replay.jsonl')
    replayed = [item for item in CaptureInputs(staff, cache, [tmp_path / 'capture'], clock=clock, server_times=[DEFAULT])
                if item.kind == Kind.MARKET_BUNDLE]
    (a,), (b,) = [item for item in live.pending if item.kind == Kind.MARKET_BUNDLE], replayed
    assert a.source_time == b.source_time == real
    assert a.payload['feeds']['1m'].time.tolist() == b.payload['feeds']['1m'].time.tolist()
    assert int(b.payload['feeds']['1m'].time[-1]) == seconds(2026, 10, 6, 8, 25)
    # STAFF itself keeps what it received; only the engine's snapshot is converted.
    assert int(cache.snapshots_with_age('XAUUSD+', ['1m'])['1m'][0].time[-1]) == bar


def test_without_a_clock_times_pass_unchanged(tmp_path):
    collector = Collector()
    adapter = StaffIngressAdapter(staff.StaffPipeCache('', monotonic=lambda: 0., gap_journal=tmp_path / 'g.jsonl'), collector)
    adapter.receive_one(io.BytesIO(wire.pack_bundle('XAUUSD+', [frame(seconds(2026, 1, 5, 10), 1)], seq=1,
                                                    sent_at_ms=1000)).read)
    (item,) = [x for x in collector.pending if x.kind == Kind.MARKET_BUNDLE]
    assert int(item.payload['feeds']['1m'].time[-1]) == seconds(2026, 1, 5, 10)


def test_a_live_clock_that_differs_from_the_setting_is_reported_once(tmp_path):
    reports = []
    adapter = StaffIngressAdapter(staff.StaffPipeCache('', monotonic=lambda: 0., gap_journal=tmp_path / 'g.jsonl'),
                                  Collector(), server_time=DEFAULT, on_clock=lambda *row: reports.append(row))
    start = seconds(2026, 7, 6, 10, 0)

    def publish(index, server_offset_hours, seq):
        bar = start + index * 60                        # real minute of the bar, labelled on the broker clock
        raw = wire.pack_bundle('XAUUSD+', [frame(bar + server_offset_hours * 3600, seq)], seq=seq,
                               sent_at_ms=(bar + 20) * 1000)
        adapter.receive_one(io.BytesIO(raw).read)
    seq = 0
    for index in range(40):                             # the setting's own UTC+3 (summer): nothing to say
        seq += 1; publish(index, 3, seq)
    assert reports == []
    for index in range(40, 100):                        # a UTC+2 broker in summer: one report after 30
        seq += 1; publish(index, 2, seq)
    assert reports == [('XAUUSD+', 2, 3)]
    assert server_time.observed_offset(start, (start + 2 * 86400) * 1000) is None   # a closed market's old bar


def test_trading_time_is_judged_in_real_korean_time_in_replay(tmp_path):
    from domain_clock import event_scope
    from special_time_slot import trading_time_allowed
    bar = seconds(2026, 7, 15, 0, 30)                   # summer: server 00:30 = 21:30 UTC = KST 06:30
    write_capture(tmp_path / 'capture', [(bar * 1000 + 5000, frame(bar, 1))])
    clock = [0.]
    cache = staff.StaffPipeCache('', monotonic=lambda: clock[0], gap_journal=tmp_path / 'g.jsonl')
    (item,) = [x for x in CaptureInputs(staff, cache, [tmp_path / 'capture'], clock=clock, server_times=[DEFAULT])
               if x.kind == Kind.MARKET_BUNDLE]
    def allowed(window):
        with event_scope(item.source_time, 'test', {}):
            return trading_time_allowed([window], lambda key, default='': default)
    assert allowed('0600-0700') and not allowed('0900-1000')   # 0900: the server clock read as UTC


# Trading days and sessions --------------------------------------------------------------------------

def view(times, high=None, low=None):
    from event_engine.market import MarketView, COLUMNS as NAMES
    n = len(times)
    x = np.full((n, len(NAMES)), 100., dtype=float)
    x[:, NAMES['high']] = 101 if high is None else high
    x[:, NAMES['low']] = 99 if low is None else low
    return MarketView(FeedSnapshot(np.asarray(times, dtype='<i8'), np.ones(n, dtype='<i8'), x, 1, 'test', {}))


def test_previous_week_levels_stay_on_the_broker_trading_days():
    from event_engine.sweep_levels import external_levels
    # Daily bars open at server midnight; a Monday is in the list of every week.
    days = np.arange(seconds(2026, 9, 14), seconds(2026, 9, 30), 86400, dtype='<i8')
    high, low = np.arange(len(days)) + 100., 90. - np.arange(len(days))
    before = {x['level_code']: x for x in external_levels({'1d': view(days, high, low)})}
    server_time.activate(DEFAULT)
    after = {x['level_code']: x for x in external_levels({'1d': view(DEFAULT.to_utc_array(days), high, low)})}
    for code in ('PDH', 'PDL', 'PWH', 'PWL'):
        assert after[code] == before[code]


def test_previous_session_levels_follow_korean_time():
    from event_engine.sweep_levels import external_levels
    server_time.activate(DEFAULT)
    # 5m bars on the server clock from server 00:00 (KST 06:00 in summer) to the bar before NY at KST 21:00.
    start = seconds(2026, 7, 15)
    def levels(last_kst_hour):
        end = start + (last_kst_hour - 6) * 3600
        times = DEFAULT.to_utc_array(np.arange(start, end + 601, 300, dtype='<i8'))
        result = {x['level_code']: x for x in external_levels({'5m': view(times, np.arange(len(times)) + 100.)},
                                                             '1600-0100', '2100-0600')}
        return result.get('PREV_SESSION_HIGH', {}).get('id', '')
    assert ':LONDON:2026-07-15T16:00:00+09:00' in levels(20)
    assert ':NY:2026-07-15T21:00:00+09:00' in levels(22)


def test_anchored_vwap_and_history_windows_keep_the_server_days():
    from indicator_facts import _anchored_vwap
    from event_engine.history import required_rows
    times = np.arange(seconds(2026, 7, 13, 20), seconds(2026, 7, 15, 4), 300, dtype='<i8')
    n = len(times)
    h, l, c = (pd.Series(np.linspace(100, 110, n) + k) for k in (1, -1, 0))
    v = pd.Series(np.ones(n))
    server = _anchored_vwap(pd.Series(pd.to_datetime(times, unit='s')), h, l, c, v, '5m')
    rows = {tf: required_rows({'time': pd.to_datetime(times, unit='s')}, tf, (), role=role)
            for tf, role in (('5m', 'indicator'), ('1d', 'sweep'))}
    server_time.activate(DEFAULT)
    real = pd.Series(pd.to_datetime(DEFAULT.to_utc_array(times), unit='s'))
    np.testing.assert_array_equal(_anchored_vwap(real, h, l, c, v, '5m').to_numpy(), server.to_numpy())
    for (tf, role) in (('5m', 'indicator'), ('1d', 'sweep')):
        assert required_rows({'time': real}, tf, (), role=role) == rows[tf]


# The virtual entry and the run bounds ---------------------------------------------------------------

def test_virtual_entry_reads_the_recordings_in_real_time(tmp_path):
    from test_parallel_oz import keyframe_fixture
    from event_backtest.keyframes import verify_indexed
    from event_backtest.virtual_source import shared_observations
    rows, index, root = keyframe_fixture(tmp_path)
    verify_indexed(root / 'capture.delta2', {k: index[k] for k in ('bundles', 'bundle_sha256')})
    (root / 'storage.json').write_text(json.dumps(index)); (root / 'complete.txt').write_text('VERIFIED\n')
    captures = [dict(start='2025-09-01', end='2025-09-04', path='capture')]
    selected = {('XAUUSD+', '1m')}
    recorded = [(stamp, feeds[('XAUUSD+', '1m')].time.copy()) for stamp, feeds in
                shared_observations(captures, tmp_path, selected, rows[0][0], rows[-1][0])]
    real = [(stamp, feeds[('XAUUSD+', '1m')].time.copy()) for stamp, feeds in
            shared_observations(captures, tmp_path, selected, DEFAULT.to_utc_ms(rows[0][0]),
                                DEFAULT.to_utc_ms(rows[-1][0]), server_time=DEFAULT)]
    assert [stamp for stamp, _ in real] == [DEFAULT.to_utc_ms(stamp) for stamp, _ in recorded]
    for (_, server), (_, converted) in zip(recorded, real):
        assert converted.tolist() == DEFAULT.to_utc_array(server).tolist()


def test_join_bounds_are_compared_in_real_time():
    from event_backtest.joins import real_plan
    plan = {'start_ms': 1000, 'end_ms': 5000, 'until_ms': None, 'record_until_ms': 0, 'until': None, 'position': 0}
    assert real_plan(plan, lambda ms: ms - 7) == {**plan, 'start_ms': 993, 'end_ms': 4993}
    plan = {**plan, 'until_ms': 9000, 'record_until_ms': 8000}
    assert real_plan(plan, lambda ms: ms - 7)['until_ms'] == 8993 and real_plan(plan, lambda ms: ms - 7)['record_until_ms'] == 7993


# LIVE state saved before 수정본172 -------------------------------------------------------------------

def test_state_saved_with_server_clock_bar_times_is_set_aside_once(tmp_path):
    from types import SimpleNamespace
    from event_startup import TIME_BASE, export_engine_state, retire_server_clock_state
    state = tmp_path / 'event_state'; state.mkdir()
    for name in ('event_composer_memory.json', 'oz_observed_x.json', 'signal_receipts.json'):
        (state / name).write_text('{}', encoding='utf-8')
    (state / 'economy_briefing.txt').write_text('2026-10-06', encoding='utf-8')
    backup = retire_server_clock_state(state, today=dt.date(2026, 10, 9))
    assert backup.name == '서버시각_이전상태_2026-10-09'
    assert sorted(p.name for p in backup.iterdir()) == ['event_composer_memory.json', 'oz_observed_x.json']
    # The receipts of sent alerts carry LIVE's own real times: kept, so nothing is sent twice.
    assert [p.name for p in state.glob('*.json')] == ['signal_receipts.json']
    assert (state / 'economy_briefing.txt').is_file()
    # State saved from now on names its time base and is never set aside again.
    engine = SimpleNamespace(_running=False, _internal=False, ingress=(), processor_state={}, strategy_state={})
    saved = export_engine_state(engine)
    assert json.loads(saved[TIME_BASE]) == {'version': 1, 'bar_times': 'REAL'}
    for name, value in saved.items():
        (state / name).write_text(value, encoding='utf-8')
    (state / 'oz_observed_x.json').write_text('{}', encoding='utf-8')
    assert retire_server_clock_state(state) is None and (state / 'oz_observed_x.json').is_file()
    assert retire_server_clock_state(tmp_path / 'new_installation') is None


def test_only_the_live_start_sets_old_state_aside(tmp_path):
    from event_startup import create_live_event_engine
    source = tmp_path / 'read_only' / 'program'; source.mkdir(parents=True)
    (source / 'config.txt').write_text('TARGET_SYMBOLS=XAUUSD+\nTELEGRAM_CHAT_ID=user', encoding='utf-8')
    state = tmp_path / 'event_owned'; state.mkdir()
    saved = json.dumps({'version': 3, 'watches': {'restored': ['XAUUSD+', '1m', []]}})
    (state / 'trend_watch_state.json').write_text(saved, encoding='utf-8')
    # Another caller (a test, a tool) reads the state as it is and moves nothing.
    engine = create_live_event_engine(source, state_directory=state)
    assert engine.retired_state_directory is None and (state / 'trend_watch_state.json').is_file()
    engine = create_live_event_engine(source, state_directory=state, set_aside_old_state=True)
    assert Path(engine.retired_state_directory).parent == state.resolve()
    assert not (state / 'trend_watch_state.json').exists()
    assert (Path(engine.retired_state_directory) / 'trend_watch_state.json').read_text(encoding='utf-8') == saved


# The setting -----------------------------------------------------------------------------------------

def test_the_settings_screen_validates_the_broker_clock(tmp_path, monkeypatch):
    sys.path.insert(0, str(ROOT / 'Part3'))
    from lab import unified_settings
    validate = unified_settings._validate_live
    assert validate('SERVER_UTC_OFFSET', '+3', '2') == '3' and validate('SERVER_UTC_OFFSET', '-5', '2') == '-5'
    assert validate('SERVER_DST', 'eu', 'US') == 'EU'
    for key, value in (('SERVER_UTC_OFFSET', '15'), ('SERVER_UTC_OFFSET', '2.5'), ('SERVER_DST', 'JP')):
        with pytest.raises(ValueError):
            validate(key, value, '')
    # A config.txt from before 수정본172 shows the default and gains the line when it is saved.
    config = tmp_path / 'config.txt'; config.write_text('SYMBOLS=XAUUSD+\n', encoding='utf-8')
    monkeypatch.setattr(unified_settings, 'LIVE_CONFIG', config)
    values = unified_settings._live_values(unified_settings._live_lines()[0])
    assert {**unified_settings.LIVE_ADDED_DEFAULTS, **values}['SERVER_UTC_OFFSET'] == '2'
    unified_settings._save_live({'SERVER_UTC_OFFSET': '3', 'SERVER_DST': 'NONE'})
    assert ServerTime.from_config(unified_settings._live_values(unified_settings._live_lines()[0])) == ServerTime(3, 'NONE')
