"""Combined ROW validation, Delta restoration and closed MA alert behavior."""
from pathlib import Path
import io
import sys
import threading

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'Part1/program'), str(ROOT/'Part2'), str(ROOT/'tests')]
import staff_schema as wire
from event_application import create_event_engine
from event_backtest.delta import DeltaCodec
from event_engine.staff_adapter import StaffIngressAdapter
from event_host import load_staff
from event_pipe_host import PipeReceiver
from engine_harness114 import CONFIG, SYMBOL, plugin
from recipe_harness114 import feed
from test_closed_facts151 import hourly, variable_hourly, publications, no_network  # noqa: F401


def captured(outcome, variable):
    previous = {}
    for seq, (stamp, hour) in enumerate(publications(outcome, variable), 1):
        minute = feed('1m', stamp-stamp % 60, open=102., close=102., high=103., low=101.)
        children = []
        for tf, snapshot in (('1m', minute), ('1h', hour)):
            old = previous.get(tf)
            same_history = old is not None and np.array_equal(old.time, snapshot.time) and np.array_equal(
                old.values[:-1], snapshot.values[:-1], equal_nan=True)
            kind = wire.WIRE_ROW if same_history else wire.WIRE_FULL
            chosen = slice(-1, None) if same_history else slice(None)
            children.append(wire.pack_v2(SYMBOL, tf, snapshot.time[chosen], snapshot.volume[chosen],
                snapshot.values[chosen], seq=seq, kind=kind))
            previous[tf] = snapshot
        yield wire.pack_bundle(SYMBOL, children, seq=seq, sent_at_ms=stamp*1000)


def notices(engine):
    result = []
    for signal in engine.signals:
        content = signal.payload.get('content', {})
        if content.get('type') == 'NOTIFICATION' and content.get('signal_strategy') == 'SPECIAL8':
            result.append((content['event_time'], content['direction'], content['signal_tf'], content['signal_price']))
    return result


@pytest.mark.parametrize('variable', [False, True])
@pytest.mark.parametrize('outcome, expected', [('touch', 1), ('cancel', 0), ('miss', 0)])
def test_live_pipe_and_delta_replay_produce_intended_special8_alerts(tmp_path, variable, outcome, expected):
    original = list(captured(outcome, variable))
    # Interleaved high-frame ROWs exercise the checked prefix and closed Fact cache.
    assert any(frame.kind == wire.WIRE_ROW for raw in original for frame in wire.decode_v2(raw).children)
    encoder, decoder = DeltaCodec(), DeltaCodec(verify_crc=False)
    restored = [decoder.decode(*encoder.encode(raw)) for raw in original]
    engines = []
    for live, rows in ((True, original), (False, restored)):
        engine = create_event_engine(dict(CONFIG), symbols=(SYMBOL,), selection=['SPECIAL8'],
            plugins={'SPECIAL8':plugin('SPECIAL8', variable_hourly if variable else hourly)}, backtest=not live)
        staff = load_staff()
        cache = staff.StaffPipeCache('', health_session='COMBINED151', monotonic=lambda:0.,
                                     gap_journal=tmp_path/('live.jsonl' if live else 'replay.jsonl'))
        adapter = StaffIngressAdapter(cache, engine.ingress)
        receiver = PipeReceiver(staff, cache, adapter, (), threading.Event())
        for raw in rows:
            if live:
                receiver.receive(io.BytesIO(raw).read, lambda _:None)
            else:
                adapter.publish(raw)
            engine.run()
            assert not engine.error_log, engine.error_log
        assert not cache.wire_diagnostics()['gaps']
        engines.append(engine)
    observed = notices(engines[0])
    assert len(observed) == expected
    assert observed == notices(engines[1])
    if expected:
        assert observed[0][1:3] == ('LONG','1h')
