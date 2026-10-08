"""Synthetic decision probes through real engine, Wire transports and CSV writer.

Not a Windows named-pipe, MT5, external network or SQL integration test.
"""
from pathlib import Path
import csv
import json
import sys

from probe43_support import ROOT, synthetic_snapshot, make_capture, load_warehouse
from event_engine import EventEngine, IngressSequencer
from event_engine.model import Input, Kind, Subscriptions, Signal
from event_engine.replay import replay
from event_engine.market import select
from event_engine.domain_support import plain
from event_backtest.instrumentation import measure_consumer
from oz_engine.runtime import ready

SYMBOL = 'XAUUSD+'


class Validity:
    name = 'VALID43'
    def subscriptions(self):
        return Subscriptions(timeframes=('1m',), facts=('WONBI_BANDS',),
                             kinds=(Kind.MARKET_BUNDLE, Kind.FEED_HEALTH, Kind.TIMER))
    def on_event(self, event, board, state):
        selected = select(board, SYMBOL, '1m', ('EMA',))
        eligible = bool(ready(board, SYMBOL, '1m')) and selected is not None
        select(board, SYMBOL, '1m')
        select(board, SYMBOL, '1m', ('WONBI',))
        state.setdefault('observations', []).append((event.kind.value, event.source_time, eligible))
        state['__board__'] = {'eligible': eligible}


class Threshold:
    name = 'THRESHOLD43'
    def subscriptions(self):
        return Subscriptions(timeframes=('1m',), processor_states=('VALID43',),
                             kinds=(Kind.MARKET_BUNDLE,))
    def on_event(self, event, board, state, emit):
        if not board.processor('VALID43')['eligible']:
            return
        v = select(board, SYMBOL, '1m', ('EMA',))
        value = float(v.column('high')[-1])
        above = value > 105
        if above and not state.get('above', False):
            raw = {'b0_price': value, 'b0_time': int(v.time[-2]), 'direction': 'LONG',
                   'source_tf': '1m', 'grade': 'S', 'profile': 'NORMAL/OZ', 'trigger': 'B0'}
            emit(Signal(SYMBOL, 'above105', {'type': 'DOMAIN_FACT', 'family': 'PROBE43', 'event': raw}))
        state['above'] = above


class Notify:
    name = 'NOTIFY43'
    def subscriptions(self):
        return Subscriptions(kinds=(Kind.SIGNAL,))
    def accepts_event(self, event):
        return event.payload.get('content', {}).get('family') == 'PROBE43'
    def on_event(self, event, board, state, emit):
        state['sent'] = state.get('sent', 0) + 1
        # Intentionally no B0 fields in the notification: the wrapper MUST
        # collect them from the originating domain event for ResultWriter.
        emit(Signal(SYMBOL, 'notice:' + event.payload['signal_id'],
                    {'type': 'NOTIFICATION', 'message': 'synthetic probe43',
                     'recipients': ('A', 'B')}, strategy='PROBE43'))


def evaluate(inputs, folder, *, dispatch='direct', measured=True):
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    warehouse = load_warehouse()
    writer = warehouse.ResultWriter(folder / 'alerts.csv', {'run_id': 'probe43'}, 0, 2**63-1)
    engine = EventEngine(IngressSequencer(), [Threshold(), Notify()], [Validity()])
    timings = {}
    if measured:
        for consumer in (*engine.processors, *engine.strategies):
            consumer.on_event = measure_consumer(consumer.on_event, consumer.name, writer, timings)
    engine.signal_sink = writer.accept
    try:
        if dispatch == 'direct':
            count = 0
            for item in inputs:
                engine.ingress.post(item.kind, source=item.source, source_seq=item.source_seq,
                                    source_time=item.source_time, payload=item.payload)
                engine.run()
                count += 1
        else:
            count = replay(engine, inputs)['input_count']
        assert not engine.error_log, engine.error_log
    finally:
        writer.close()
    with writer.path.open(encoding='utf-8', newline='') as stream:
        rows = list(csv.DictReader(stream))
    notifications = [e for e in engine.signals if e.payload['content'].get('type') == 'NOTIFICATION']
    assert len(notifications) == 2 and len(rows) == 4
    if measured:
        assert [row['recipient'] for row in rows] == ['A', 'B', 'A', 'B']
        assert {row['b0_price'] for row in rows} == {'108.0', '110.0'}
        assert all(row['b0_time'] and row['grade'] == 'S' and row['profile'] == 'NORMAL/OZ'
                   and row['tf'] == '1m' and row['direction'] == 'LONG'
                   and row['trigger'] == 'B0' and row['strategy'] == 'PROBE43' for row in rows)
        assert not writer.context
        assert all(row['ns'] >= 0 and row['calls'] > 0 for row in timings.values())
    return {'input_count': count, 'notification_count': len(notifications), 'csv_rows': rows,
            'signals': [(e.source_time, plain(e.payload)) for e in engine.signals],
            'processor_state': plain(engine.processor_state), 'strategy_state': plain(engine.strategy_state),
            'timing_calls': {key: row['calls'] for key, row in timings.items()},
            'sql_tested': False}


def direct_probe(folder):
    import numpy as np
    variants = [(), ((-1, 'high', 108.),), ((-1, 'high', 108.),),
                ((-1, 'high', 109.), (-1, 'ema_20', np.nan)), ((0, 'low', np.inf),),
                (), ((-1, 'high', 110.),), ()]
    start = 1756723740000
    inputs = [Input('direct43', i, start+i*1000, Kind.MARKET_BUNDLE,
                    {'symbol': SYMBOL, 'feeds': {'1m': synthetic_snapshot(i, changes)}}, 0)
              for i, changes in enumerate(variants, 1)]
    last = inputs[-1].source_time
    inputs += [Input('direct43', 9, last+1, Kind.FEED_HEALTH,
                     {'symbol': SYMBOL, 'status': 'STALE'}, 0),
               Input('direct43', 10, last+2, Kind.FEED_HEALTH,
                     {'symbol': SYMBOL, 'status': 'FRESH'}, 0),
               Input('direct43', 11, last+30000, Kind.TIMER,
                     {'symbol': SYMBOL, 'strategy': 'unused'}, 0),
               Input('direct43', 12, last+30001, Kind.TIMER,
                     {'symbol': SYMBOL, 'strategy': 'unused'}, 0)]
    plain_live = evaluate(inputs, Path(folder) / 'unwrapped_live', measured=False)
    live = evaluate(inputs, Path(folder) / 'wrapped_live')
    replayed = evaluate(inputs, Path(folder) / 'wrapped_replay', dispatch='replay')
    for key in ('signals', 'processor_state', 'strategy_state', 'input_count'):
        assert plain_live[key] == live[key] == replayed[key], key
    assert live['csv_rows'] == replayed['csv_rows']
    assert live['timing_calls'] == replayed['timing_calls']
    observations = live['processor_state']['VALID43']['observations']
    assert [row[2] for row in observations] == [True, True, True, False, False, True, True, True,
                                               False, True, True, False]
    return {'input_count': live['input_count'], 'notification_count': live['notification_count'],
            'csv_row_count': len(live['csv_rows']), 'timing_calls': live['timing_calls'],
            'unwrapped_vs_wrapped_decisions_equal': True, 'live_vs_replay_equal': True,
            'b0_recipient_order_and_identity_checked': True,
            'observations': observations, 'sql_tested': False}


def wire_probe(folder):
    from event_backtest.bridge import CaptureInputs
    from event_host import load_staff
    folder = Path(folder)
    variants = [(), ((-1, 'high', 108.),), ((-1, 'high', 108.),), (),
                ((-1, 'high', 110.),), ()]
    snaps = [synthetic_snapshot(i, changes, shift=(i//5)*60) for i, changes in enumerate(variants, 1)]
    capture = folder / 'synthetic_capture'
    make_capture(capture, snaps)
    results = []
    for transport in ('live', 'replay'):
        staff = load_staff()
        clock = [0.]
        cache = staff.StaffPipeCache('', health_session='PROBE43', monotonic=lambda: clock[0],
                                     gap_journal=folder / (transport + '_gap.jsonl'))
        inputs = CaptureInputs(staff, cache, [capture], transport=transport, clock=clock)
        results.append(evaluate(inputs, folder / transport, dispatch='replay'))
    for key in ('signals', 'processor_state', 'strategy_state', 'csv_rows', 'timing_calls', 'input_count'):
        assert results[0][key] == results[1][key], key
    return {'input_count': results[0]['input_count'], 'notification_count': results[0]['notification_count'],
            'csv_row_count': len(results[0]['csv_rows']), 'timing_calls': results[0]['timing_calls'],
            'live_transport_vs_replay_transport_equal': True, 'sql_tested': False,
            'windows_named_pipe_tested': False, 'actual_mt5_tested': False}


def main():
    import socket
    import tempfile
    from event_backtest.system import deny_network
    deny_network()
    out = ROOT / '검증결과/계측_유효성최적화43'
    out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='probe43_') as tmp:
        result = {'direct': direct_probe(Path(tmp) / 'direct'), 'wire': wire_probe(Path(tmp) / 'wire')}
    (out / 'integration.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
