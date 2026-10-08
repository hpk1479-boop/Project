"""수정본131: a replay publishes only the timeframes its strategies read.

STAFF keeps the other feeds as shadows with the same checks and bookkeeping; the board records any
request for a missing timeframe; the replay then repeats the period with every feed. Results never
depend on the limit: the published feeds equal a full cache's, and a too-short limit falls back.
"""
from pathlib import Path
import io, json, os, socket, subprocess, sys

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'Part1/program'), str(ROOT / 'Part2')]
import staff_schema as wire
from event_backtest.bridge import Collector
from event_engine.board import Board, LimitedFeeds
from event_engine.staff_adapter import StaffIngressAdapter
from event_host import load_staff

staff = load_staff()
SYMBOL, EMPTY = 'XAUUSD+', 1.7976931348623157e308
COLS = len(wire.PIPE_VALUE_COLUMNS)
TIMES = np.arange(30, dtype='<i8') * 60 + 1756684800


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def deny(*a, **k):
        raise AssertionError('real network is forbidden')
    for name in ('connect', 'connect_ex', 'sendto'):
        monkeypatch.setattr(socket.socket, name, deny)


def bars(base=0.):
    values = np.full((30, COLS), 10. + base, dtype='<f8')
    values[:, 0] = np.arange(30) + 100. + base
    values[:, 1], values[:, 2], values[:, 3] = values[:, 0] + 2, values[:, 0] - 2, values[:, 0] + 1
    values[5, 20:24] = EMPTY; values[-1, 30:33] = EMPTY
    return values


def full(tf, seq, base=0.):
    return wire.pack_v2(SYMBOL, tf, TIMES, np.ones(30, dtype='<i8'), bars(base), seq=seq)


def row(tf, seq, base=0., time=None, empty=()):
    values = bars(base)[-1:].copy(); values[0, list(empty)] = EMPTY
    when = TIMES[-1:] if time is None else np.array([time], dtype='<i8')
    return wire.pack_v2(SYMBOL, tf, when, np.full(1, 3, dtype='<i8'), values, seq=seq, kind=wire.WIRE_ROW)


def beat(tf, seq):
    return wire.pack_v2(SYMBOL, tf, seq=seq, kind=wire.WIRE_HEARTBEAT)


def caches(tmp_path):
    made = []
    for name, limit in (('full', None), ('limited', {'1m'})):
        cache = staff.StaffPipeCache('', health_session='TEST', monotonic=lambda: 0., gap_journal=tmp_path / f'{name}.jsonl',
                                     published_timeframes=limit)
        made.append((cache, StaffIngressAdapter(cache, Collector())))
    return made


def send(adapter, frames, seq):
    adapter.receive_one(io.BytesIO(wire.pack_bundle(SYMBOL, frames, seq=seq, sent_at_ms=1000 * seq)).read)


SEQUENCE = [
    [full('1m', 1), full('5m', 1, 5.)],
    [row('1m', 2, 1.), row('5m', 2, 6., empty=(38,))],     # price_lower_out: cleaned, no validity change
    [beat('1m', 3), beat('5m', 3)],
    [row('1m', 5, 2.), row('5m', 5, 7.)],                 # a sequence gap on both
    [full('1m', 6, 3.), full('5m', 6, 8.)],
    [row('1m', 7, 4.), row('5m', 7, 9.)],
]


def test_published_feeds_and_bookkeeping_equal_a_full_cache(tmp_path):
    (whole, a), (limited, b) = caches(tmp_path)
    for seq, frames in enumerate(SEQUENCE, 1):
        send(a, frames, seq); send(b, frames, seq)
        x, y = whole._entries[(SYMBOL, '1m')].snapshot, limited._entries[(SYMBOL, '1m')].snapshot
        assert x.values.tobytes() == y.values.tobytes() and x.time.tobytes() == y.time.tobytes()
        assert (x.seq, x.source_epoch, dict(x.indicator_validity)) == (y.seq, y.source_epoch, dict(y.indicator_validity))
        assert (SYMBOL, '5m') not in limited._entries
        shadow = limited._shadow[(SYMBOL, '5m')]; reference = whole._entries[(SYMBOL, '5m')].snapshot
        assert shadow.time.tobytes() == reference.time.tobytes() and shadow.volume.tobytes() == reference.volume.tobytes()
        assert shadow.values.tobytes() == np.ascontiguousarray(reference.values[:, :4]).tobytes()
        assert (shadow.seq, shadow.indicator_validity) == (reference.seq, dict(reference.indicator_validity))
        for name in ('_snapshot', '_updated', '_health_epochs', '_requires_full', '_schema_errors'):
            assert getattr(whole, name) == getattr(limited, name), name
        clock = 'received_at_unix_ms'                     # the wall clock at receipt
        assert [{k: v for k, v in g.items() if k != clock} for g in whole._gap_records] == \
               [{k: v for k, v in g.items() if k != clock} for g in limited._gap_records]
    published = [set(item.payload['feeds']) for item in b.ingress.pending if item.kind.name == 'MARKET_BUNDLE']
    assert published and all(feeds == {'1m'} for feeds in published)
    assert limited.snapshots_with_age(SYMBOL, ['5m'])['5m'][0] is limited._shadow[(SYMBOL, '5m')]


@pytest.mark.parametrize('bad,message', [
    (row('5m', 2, time=int(TIMES[-1]) + 300), 'ROW new bar requires FULL'),
    (row('5m', 2, empty=(wire.PIPE_VALUE_COLUMNS.index('ema_20'),)), 'ROW validity transition requires FULL'),
])
def test_an_unpublished_feed_is_rejected_like_a_published_one(tmp_path, bad, message):
    (whole, a), (limited, b) = caches(tmp_path)
    send(a, SEQUENCE[0], 1); send(b, SEQUENCE[0], 1)
    before = dict(limited._shadow), dict(limited._entries)
    for cache, adapter in ((whole, a), (limited, b)):
        with pytest.raises(wire.WireError, match=message):
            send(adapter, [row('1m', 2, 1.), bad], 2)
    assert (dict(limited._shadow), dict(limited._entries)) == before, 'the whole bundle is rolled back'


def test_after_reconnect_an_unpublished_feed_needs_full_again(tmp_path):
    (whole, a), (limited, b) = caches(tmp_path)
    send(b, SEQUENCE[0], 1)
    limited.reconnect()
    with pytest.raises(wire.WireError, match='requires FULL'):
        send(b, [full('1m', 2), row('5m', 2)], 2)


def test_the_board_records_requests_for_missing_timeframes():
    misses = set()
    feeds = LimitedFeeds({(SYMBOL, '1m'): 'one', (SYMBOL, '15m'): 'fifteen'}, frozenset({'1m', '15m'}), misses)
    assert (SYMBOL, '1m') in feeds and feeds[(SYMBOL, '15m')] == 'fifteen' and not misses
    assert (SYMBOL, '5m') not in feeds and misses == {'5m'}
    assert feeds.get((SYMBOL, '4h')) is None and misses == {'5m', '4h'}
    assert sorted(feeds) == sorted([(SYMBOL, '1m'), (SYMBOL, '15m')]) and misses == {'5m', '4h'}
    lonely = LimitedFeeds({(SYMBOL, '15m'): 'fifteen'}, frozenset({'1m', '15m'}), set())
    list(lonely)
    assert lonely._misses == {'1m missing while listing feeds'}


def test_an_unlimited_board_is_the_plain_view():
    from types import MappingProxyType
    from event_engine.facts import EventFacts
    board = Board(EventFacts())
    view = board.view({}, type('C', (), {'subscriptions': lambda self: __import__('event_engine').Subscriptions()})())
    assert type(view.feeds) is MappingProxyType and board.timeframe_limit is None


def required(selection, commands=()):
    from event_application import create_event_engine
    from event_backtest.timeframe_selection import required_timeframes
    engine = create_event_engine({'STAFF_ALLOWED_SYMBOLS': SYMBOL, 'TARGET_SYMBOLS': SYMBOL, 'TELEGRAM_TOKEN': 'OFFLINE',
                                  'TELEGRAM_CHAT_ID': 'OFFLINE'}, symbols=(SYMBOL,), selection=selection, backtest=True)
    return required_timeframes(engine, commands)


def test_the_list_holds_what_a_strategy_reads():
    found = required(['SPECIAL7'])
    assert {'1m', '3m', '6m', '15m'} <= found and '12h' not in found
    assert required(['SPECIAL8']) >= {'1m'}
    assert required(['ALL']) is None


SCRIPT = r'''
import json, sys
from pathlib import Path
from event_backtest import runner, timeframe_selection
from event_backtest.settings import scenario
root, capture, mode = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
s = scenario(symbol='XAUUSD+', start='2025-09-01', end='2025-09-04', mode='TICK', strategies=['SPECIAL7'], overlap_trading_days=0)
config = {'STAFF_ALLOWED_SYMBOLS': 'XAUUSD+', 'TARGET_SYMBOLS': 'XAUUSD+', 'TELEGRAM_TOKEN': 'OFFLINE', 'TELEGRAM_CHAT_ID': 'OFFLINE'}
if mode == 'short':
    timeframe_selection.required_timeframes = lambda engine, registrations=(): frozenset({'5m'})
task = {'scenario': s, 'config': config, 'out': str(root / ('job_' + mode)), 'run_id': mode, 'start': s['start'], 'end': s['end'],
        'warm_start': s['start'], 'captures': [{'path': capture.relative_to(root).as_posix()}], 'warehouse': str(root)}
result = runner._run_chunk(task, False) if mode == 'every' else runner.run_chunk(task)
rows = (root / ('job_' + mode) / 'alerts.csv').read_text('utf-8').splitlines()
print(json.dumps({'bundles': result['bundles'], 'alerts': result['alerts'], 'timeframes': result.get('timeframes'),
                  'fallback': result.get('timeframe_fallback'), 'rows': [r.replace(mode, '') for r in rows]}))
'''


def test_a_short_list_replays_the_period_with_every_feed(tmp_path):
    from test_parallel_oz import keyframe_fixture
    _, _, capture = keyframe_fixture(tmp_path)
    env = {**os.environ, 'PYTHONPATH': os.pathsep.join((str(ROOT / 'Part1/program'), str(ROOT / 'Part2')))}
    out = {}
    for mode in ('every', 'limited', 'short'):
        done = subprocess.run([sys.executable, '-X', 'utf8', '-B', '-c', SCRIPT, str(tmp_path), str(capture), mode],
                              env=env, capture_output=True, text=True, encoding='utf-8')
        assert done.returncode == 0, done.stderr
        out[mode] = json.loads(done.stdout.strip().splitlines()[-1])
    assert out['every']['timeframes'] is None and out['every']['fallback'] is None
    assert out['limited']['timeframes'] and '5m' not in out['limited']['timeframes'] and out['limited']['fallback'] is None
    assert out['short']['fallback'] and out['short']['timeframes'] is None
    for mode in ('limited', 'short'):
        assert (out[mode]['bundles'], out[mode]['alerts'], out[mode]['rows']) == \
               (out['every']['bundles'], out['every']['alerts'], out['every']['rows'])
