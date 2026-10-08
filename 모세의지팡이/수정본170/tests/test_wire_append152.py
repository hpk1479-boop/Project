"""152: APPEND (the closed bar's final row + the new bar) gives STAFF the window a new-bar FULL gives.

Synthetic input only. The reference is the FULL the EA would send at each new bar (its last
min(650, available) bars); APPEND must reproduce it bit for bit, in LIVE and in replay.
"""
from pathlib import Path
import io
import sys
import threading

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'Part1/program'), str(ROOT / 'Part2'), str(ROOT / 'tests')]
import staff_schema as wire
from event_application import create_event_engine
from event_backtest.delta import DeltaCodec
from event_backtest.virtual_msd import Projection
from event_engine.staff_adapter import StaffIngressAdapter
from event_host import load_staff
from event_pipe_host import PipeReceiver
from engine_harness114 import CONFIG, SYMBOL, plugin
from recipe_harness114 import feed
from test_closed_facts151 import hourly as hourly_steps, variable_hourly, no_network  # noqa: F401

STAFF = load_staff()
START = 1_790_002_800
COLS = len(wire.PIPE_VALUE_COLUMNS)


def bundle(tf, times, volumes, values, seq, kind):
    child = wire.pack_v2(SYMBOL, tf, times, volumes, values, seq=seq, kind=kind)
    return wire.pack_bundle(SYMBOL, [child], seq=seq, sent_at_ms=int(times[-1]) * 1000)


def streams(count=700, first_bar=649, start=0, seed=7, tf='1h'):
    """The same bars as two Wire streams: FULL at every new bar, or APPEND at every new bar.

    History starts at bar `start`; a FULL carries the last min(650, available) bars. Each bar is
    published opened, updated once by a ROW, and final in the next new-bar frame.
    """
    rng = np.random.default_rng(seed)
    final = 2400. + np.cumsum(rng.normal(0., 1., (count, COLS)), axis=0) * .1
    opened = final.copy(); opened[:, 1:4] = final[:, :1]
    middle = final.copy(); middle[:, 3] += .25
    volumes = rng.integers(2, 1000, count).astype(np.int64)
    times = START + np.arange(count, dtype=np.int64) * 3600
    full, append, seq = [], [], 0
    for k in range(first_bar, count):
        lo = max(start, k - wire.WIRE_MAX_BARS + 1)
        seq += 1
        window_volumes = np.r_[volumes[lo:k], 1]
        full.append(bundle(tf, times[lo:k + 1], window_volumes, np.r_[final[lo:k], opened[k:k + 1]], seq, wire.WIRE_FULL))
        append.append(full[-1] if k == first_bar else bundle(
            tf, times[k - 1:k + 1], np.r_[volumes[k - 1], 1], np.stack((final[k - 1], opened[k])), seq, wire.WIRE_APPEND))
        seq += 1
        row = bundle(tf, times[k:k + 1], volumes[k:k + 1] // 2, middle[k:k + 1], seq, wire.WIRE_ROW)
        full.append(row); append.append(row)
    return full, append


def cache(path, **options):
    options.setdefault('monotonic', lambda: 0.)
    return STAFF.StaffPipeCache('', health_session='APPEND152', gap_journal=path, **options)


def same_bits(left, right):
    return left.shape == right.shape and np.array_equal(np.asarray(left).view('<u8'), np.asarray(right).view('<u8'))


def assert_same_snapshot(a, b):
    assert np.array_equal(a.time, b.time) and np.array_equal(a.volume, b.volume)
    assert same_bits(a.values, b.values)
    assert (a.seq, a.source_epoch, dict(a.indicator_validity)) == (b.seq, b.source_epoch, dict(b.indicator_validity))


def test_append_frame_has_exactly_two_rows():
    rows = np.full((2, COLS), 2400.)
    raw = wire.pack_v2(SYMBOL, '1h', [START, START + 3600], [1, 1], rows, seq=1, kind=wire.WIRE_APPEND)
    frame = wire.decode_v2(raw)
    assert (frame.kind, len(frame.times)) == (wire.WIRE_APPEND, 2)
    assert wire.decode_v2(wire.pack_bundle(SYMBOL, [raw], seq=1)).children[0].kind == wire.WIRE_APPEND
    for count in (1, 3):
        with pytest.raises(wire.WireError, match='bar count'):
            wire.pack_v2(SYMBOL, '1h', np.arange(count) + START, np.ones(count), np.full((count, COLS), 2400.),
                         seq=1, kind=wire.WIRE_APPEND)


@pytest.mark.parametrize('count, first_bar', [(700, 649), (360, 299)], ids=['full-window-slides', 'short-history-grows'])
def test_published_window_equals_the_new_bar_full(tmp_path, count, first_bar):
    full, append = streams(count, first_bar)
    by_full, by_append = cache(tmp_path / 'full.jsonl'), cache(tmp_path / 'append.jsonl')
    assert sum(wire.decode_v2(raw).children[0].kind == wire.WIRE_APPEND for raw in append) == count - first_bar - 1
    for raw_full, raw_append in zip(full, append):
        by_full.publish_frame(raw_full); by_append.publish_frame(raw_append)
        assert_same_snapshot(by_full.snapshot(SYMBOL, '1h'), by_append.snapshot(SYMBOL, '1h'))
    assert len(by_append.snapshot(SYMBOL, '1h').time) == min(count, wire.WIRE_MAX_BARS)


def test_unpublished_window_equals_the_new_bar_full(tmp_path):
    full, append = streams(700, 649)
    caches = [cache(tmp_path / f'{name}.jsonl', published_timeframes={'1m'}) for name in ('full', 'append')]
    for raw_full, raw_append in zip(full, append):
        caches[0].publish_frame(raw_full); caches[1].publish_frame(raw_append)
        a, b = (c._shadow[SYMBOL, '1h'] for c in caches)
        assert np.array_equal(a.time, b.time) and np.array_equal(a.volume, b.volume) and same_bits(a.values, b.values)
        assert (a.seq, dict(a.indicator_validity)) == (b.seq, dict(b.indicator_validity))


@pytest.mark.parametrize('published', [None, {'1m'}], ids=['published', 'unpublished'])
def test_append_is_refused_unless_it_continues_the_window(tmp_path, published):
    full, append = streams(660, 649)
    staff = cache(tmp_path / 'x.jsonl', published_timeframes=published)
    with pytest.raises(wire.WireError, match='requires FULL'):
        staff.publish_frame(append[2])
    staff.publish_frame(append[0])
    window = staff.snapshot(SYMBOL, '1h') if published is None else staff._shadow[SYMBOL, '1h']
    last = int(window.time[-1])
    good = np.full((2, COLS), 2400.)
    stale = bundle('1h', [last - 3600, last + 3600], [1, 1], good, 2, wire.WIRE_APPEND)
    backward = bundle('1h', [last, last], [1, 1], good, 2, wire.WIRE_APPEND)
    for raw in (stale, backward):
        with pytest.raises(wire.WireError, match='close the last bar'):
            staff.publish_frame(raw)
    unready = good.copy(); unready[1, wire.PIPE_VALUE_COLUMNS.index('ema_20')] = np.nan
    with pytest.raises(wire.WireError, match='APPEND validity transition requires FULL'):
        staff.publish_frame(bundle('1h', [last, last + 3600], [1, 1], unready, 2, wire.WIRE_APPEND))
    after = staff.snapshot(SYMBOL, '1h') if published is None else staff._shadow[SYMBOL, '1h']
    assert after.seq == window.seq and np.array_equal(after.time, window.time)
    staff.publish_frame(bundle('1h', [last, last + 3600], [1, 1], good, 2, wire.WIRE_APPEND))


def test_append_epoch_follows_the_new_bar_full(tmp_path):
    full, append = streams(656, 649)
    clock = [0.]
    caches = [cache(tmp_path / f'{name}.jsonl', monotonic=lambda: clock[0], stale_seconds=30.)
              for name in ('full', 'append')]
    epochs = []
    for index, (raw_full, raw_append) in enumerate(zip(full, append)):
        clock[0] += 100. if index == 4 else 1.     # a silent minute longer than stale_seconds, before a new bar
        caches[0].publish_frame(raw_full); caches[1].publish_frame(raw_append)
        a, b = (c.snapshot(SYMBOL, '1h') for c in caches)
        assert a.source_epoch == b.source_epoch
        epochs.append(b.source_epoch)
    assert epochs[3].endswith(':1') and epochs[4].endswith(':2') and epochs[-1].endswith(':2')


def test_delta_codec_keeps_append_bytes_and_the_full_window():
    full, append = streams(700, 649)
    encoder, decoder, reference = DeltaCodec(), DeltaCodec(), DeltaCodec()
    for raw in append:
        assert decoder.decode(*encoder.encode(raw)) == raw
    for raw in full:
        reference.encode(raw)
    assert encoder.feeds.keys() == reference.feeds.keys()
    for key, (times, values) in reference.feeds.items():
        assert np.array_equal(encoder.feeds[key][0], times) and np.array_equal(encoder.feeds[key][1], values)


def test_virtual_entry_readers_see_the_full_window(tmp_path, monkeypatch):
    from event_backtest import virtual_source
    full, append = streams(700, 649)
    projected = []
    for stream in (full, append):
        encoder, projection = DeltaCodec(), Projection({(SYMBOL, '1h')})
        projected.append([(view.time.copy(), view.values.copy()) for raw in stream
                          for view in (projection.decode(*encoder.encode(raw))[SYMBOL, '1h'],)])
    for (ta, va), (tb, vb) in zip(*projected):
        assert np.array_equal(ta, tb) and same_bits(va, vb)
    (tmp_path / 'captures/X').mkdir(parents=True)
    observed = []
    for stream in (full, append):
        monkeypatch.setattr(virtual_source, 'bundles', lambda root, start_ms=None, bootstrap=None, stream=stream:
                            ((wire.decode_v2(raw).sent_at_ms, raw) for raw in stream))
        piece = {'start': '2020-01-01', 'end': '2030-01-01', 'path': 'captures/X'}
        observed.append([(feeds[SYMBOL, '1h'].time.copy(), feeds[SYMBOL, '1h'].values.copy()) for _, feeds in
                         virtual_source.shared_observations([piece], tmp_path, {(SYMBOL, '1h')}, 0, 2 ** 62)])
    assert len(observed[0]) == len(full)
    for (ta, va), (tb, vb) in zip(*observed):
        assert np.array_equal(ta, tb) and same_bits(va, vb)


TIMELINE = [(START, 0, False), (START + 1800, 0, True), (START + 3600, 1, False), (START + 4800, 1, True),
            (START + 7200, 2, False), (START + 7260, 2, False), (START + 10800, 3, False)]


def hourly_window(bar, outcome, forming_touch, variable):
    """Bars -5..bar (a short history grows), with the closed cross / touch / cancel of 151's scenario."""
    indices = np.arange(-5, bar + 1); n = len(indices)
    if variable:
        opened = np.where(indices >= 0, 103., 100.)
        if outcome == 'cancel': opened[indices >= 1] = 90.
        low = opened + .5
        if outcome == 'touch': low[indices == 1] = 101.
        if not forming_touch: low[-1] = opened[-1] + .5
        return feed('1h', START + bar * 3600, n, open=opened, close=opened + 1., low=low, high=opened + 2.)
    left = np.where(indices >= 0, 101., 99.)
    low = np.full(n, 101.)
    if outcome == 'cancel': left[indices >= 1] = 99.
    if outcome == 'touch': low[indices == 1] = 99.5
    if not forming_touch: low[-1] = 101.
    return feed('1h', START + bar * 3600, n, open=101., close=102., low=low, high=103., hma_17=left, hma_50=100.)


def hourly_streams(outcome, variable):
    full, append, previous = [], [], None
    for seq, (stamp, bar, forming) in enumerate(TIMELINE, 1):
        hour = hourly_window(bar, outcome, forming, variable)
        minute = feed('1m', stamp - stamp % 60, open=102., close=102., high=103., low=101.)
        first = wire.pack_v2(SYMBOL, '1m', minute.time, minute.volume, minute.values, seq=seq)
        if previous is None or bar != previous:
            new_full = wire.pack_v2(SYMBOL, '1h', hour.time, hour.volume, hour.values, seq=seq)
            new_append = new_full if previous is None else wire.pack_v2(
                SYMBOL, '1h', hour.time[-2:], hour.volume[-2:], hour.values[-2:], seq=seq, kind=wire.WIRE_APPEND)
        else:
            new_full = new_append = wire.pack_v2(SYMBOL, '1h', hour.time[-1:], hour.volume[-1:], hour.values[-1:],
                                                 seq=seq, kind=wire.WIRE_ROW)
        full.append(wire.pack_bundle(SYMBOL, [first, new_full], seq=seq, sent_at_ms=stamp * 1000))
        append.append(wire.pack_bundle(SYMBOL, [first, new_append], seq=seq, sent_at_ms=stamp * 1000))
        previous = bar
    return full, append


def notices(engine):
    result = []
    for signal in engine.signals:
        content = signal.payload.get('content', {})
        if content.get('type') == 'NOTIFICATION' and content.get('signal_strategy') == 'SPECIAL8':
            result.append((content['event_time'], content['direction'], content['signal_tf'], content['signal_price']))
    return result


def alerts(tmp_path, rows, *, live, variable, name):
    engine = create_event_engine(dict(CONFIG), symbols=(SYMBOL,), selection=['SPECIAL8'],
        plugins={'SPECIAL8': plugin('SPECIAL8', variable_hourly if variable else hourly_steps)}, backtest=not live)
    staff = cache(tmp_path / (name + '.jsonl'))
    adapter = StaffIngressAdapter(staff, engine.ingress)
    receiver = PipeReceiver(STAFF, staff, adapter, (), threading.Event())
    for raw in rows:
        if live:
            receiver.receive(io.BytesIO(raw).read, lambda _: None)
        else:
            adapter.publish(raw)
        engine.run()
        assert not engine.error_log, engine.error_log
    assert not staff.wire_diagnostics()['gaps']
    return notices(engine)


@pytest.mark.parametrize('variable', [False, True], ids=['native-hma', 'variable-sma'])
@pytest.mark.parametrize('outcome, expected', [('touch', 1), ('cancel', 0), ('miss', 0)])
def test_special8_alerts_equal_for_full_and_append_in_live_and_replay(tmp_path, variable, outcome, expected):
    full, append = hourly_streams(outcome, variable)
    assert any(frame.kind == wire.WIRE_APPEND for raw in append for frame in wire.decode_v2(raw).children)
    encoder, decoder = DeltaCodec(), DeltaCodec(verify_crc=False)
    replayed = [decoder.decode(*encoder.encode(raw)) for raw in append]
    runs = {'full-live': alerts(tmp_path, full, live=True, variable=variable, name='a'),
            'append-live': alerts(tmp_path, append, live=True, variable=variable, name='b'),
            'append-replay': alerts(tmp_path, replayed, live=False, variable=variable, name='c')}
    assert len(runs['full-live']) == expected
    assert runs['append-live'] == runs['full-live'] and runs['append-replay'] == runs['full-live']
    if expected:
        assert runs['full-live'][0][1:3] == ('LONG', '1h')
