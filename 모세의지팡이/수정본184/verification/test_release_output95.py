"""Release 95 output/shutdown contracts, with fake transports and no services."""
from __future__ import annotations

import json
from pathlib import Path
import sys
import threading
from types import SimpleNamespace

import pytest
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'Part1/program'))

from event_engine import EventEngine, IngressSequencer
from event_engine.model import Event, FeedSnapshot, Kind, Signal, Subscriptions
from staff_schema import PIPE_VALUE_COLUMNS
from event_host import EventHost, EconomyOutputPort
from event_startup import export_engine_state
from manager_KIM import SignalOutput


def signal(key='one', *, token=None, reply=None):
    return Event(1, 0, 'offline', 1, 1000, Kind.SIGNAL,
                 {'signal_id': key, 'symbol': 'TEST', 'strategy': 'TEST',
                  'content': {'type': 'NOTIFICATION', 'message': key,
                              'message_token': token, 'reply_to_message_id': reply}})


def response(code=200, delay=2, message=22):
    body = ({'ok': True, 'result': {'message_id': message}} if code == 200 else
            {'ok': False, 'parameters': {'retry_after': delay}})
    return SimpleNamespace(status_code=code, json=lambda: body)


def test_rate_limit_waits_then_delivers_once_and_deduplicates():
    calls, waits, clock = [], [], [0.0]
    def transport(data):
        calls.append((clock[0], data))
        return response(429) if len(calls) == 1 else response()
    def sleep(delay):
        waits.append(delay)
        clock[0] += delay
    output = SignalOutput({'TELEGRAM_CHAT_ID': 'offline'}, transport=transport, sleep=sleep)
    delivered = output.accept(signal())
    assert len(delivered) == 1 and len(output.receipts) == 1
    assert waits == [2] and [item[0] for item in calls] == [0, 2]
    assert output.accept(signal()) == delivered and len(calls) == 2


@pytest.mark.parametrize('delay,budget,attempts,expected_calls,expected_waits', [
    (2, 30, 2, 2, [2]),
    (2, 3, 4, 2, [2]),
    (31, 30, 2, 1, []),
    (2, 30, 1, 1, []),
    (-1, 30, 2, 1, []),
    (float('nan'), 30, 2, 1, []),
    ('invalid', 30, 2, 1, []),
])
def test_rate_limit_is_bounded_and_never_retries_early(delay, budget, attempts, expected_calls, expected_waits):
    calls, waits, recorded = [], [], []
    output = SignalOutput({'TELEGRAM_CHAT_ID': 'offline'},
                          transport=lambda data: calls.append(data) or response(429, delay),
                          sleep=waits.append, attempts=attempts, max_retry_wait=budget,
                          result_observer=lambda _event, _chat, status: recorded.append(status))
    assert output.accept(signal()) == ()
    assert len(calls) == expected_calls and waits == expected_waits
    assert not output.receipts and recorded == ['전송 실패']


def test_unknown_transport_outcome_is_never_retried():
    calls, waits = [], []
    def unknown(data):
        calls.append(data)
        raise TimeoutError('fake response lost')
    output = SignalOutput({'TELEGRAM_CHAT_ID': 'offline'}, transport=unknown, sleep=waits.append)
    assert output.accept(signal()) == ()
    assert len(calls) == 1 and not waits and not output.receipts


@pytest.mark.parametrize('method,args,expected', [
    ('reply_symbol', ('offline', 999), None),
    ('logical_reply_id', ('offline', 999), 999),
    ('_message_id', ('TEST', 'offline', 10**15 + 999), ValueError),
])
def test_reply_lookup_and_receipt_write_can_overlap(method, args, expected):
    paused, release, transport_called = threading.Event(), threading.Event(), threading.Event()
    def transport(_data):
        transport_called.set()
        return response()
    output = SignalOutput({'TELEGRAM_CHAT_ID': 'offline'}, transport=transport,
                          receipts={'old': {'recipient': 'offline', 'message_id': 1,
                                            'symbol': 'TEST', 'message_token': 10**15 + 1}})
    result = {}
    def reader():
        def trace(frame, event, _arg):
            if (frame.f_code.co_name == method and event == 'line' and
                    'record' in frame.f_locals and not paused.is_set()):
                paused.set()
                assert release.wait(3)
            return trace
        sys.settrace(trace)
        try: result['value'] = getattr(output, method)(*args)
        except Exception as exc: result['error'] = type(exc)
        finally: sys.settrace(None)
    reading = threading.Thread(target=reader)
    writing = threading.Thread(target=lambda: output.accept(signal(token=10**15 + 2)))
    reading.start()
    try:
        assert paused.wait(3)
        writing.start()
        assert transport_called.wait(3)
    finally:
        release.set()
        reading.join(3)
        if writing.ident is not None: writing.join(3)
    assert not reading.is_alive() and not writing.is_alive()
    assert result == ({'error': ValueError} if expected is ValueError else {'value': expected})
    assert len(output.receipts) == 2
    restored = SignalOutput({}, transport=transport, receipts=output.receipts)
    assert restored.reply_symbol('offline', 22) == 'TEST'
    assert restored.logical_reply_id('offline', 22) == 10**15 + 2
    assert restored._message_id('TEST', 'offline', 10**15 + 2) == 22
    assert restored.reply_symbol('offline', 1) == 'TEST'


class Consumer:
    name = 'TEST'
    def subscriptions(self):
        return Subscriptions(kinds=(Kind.COMMAND, Kind.EXTERNAL_REPLY, Kind.MARKET_BUNDLE))
    def on_event(self, event, board, state, emit):
        state.setdefault('seen', []).append(event.kind.value)
        state['memory'] = {'test_state.json': json.dumps(state['seen'])}
        if event.kind == Kind.COMMAND and event.payload.get('external'):
            emit(Signal('TEST', 'request', {'type': 'EXTERNAL_REQUEST', 'text': 'offline',
                                          'command': {'text': 'offline', 'chat_id': 'offline'}}))
        else:
            emit(Signal('TEST', str(len(state['seen'])), {'type': 'NOTIFICATION', 'message': 'done'}))


def make_host(external=None, transport=None):
    engine = EventEngine(IngressSequencer(), (Consumer(),))
    sent = []
    host = EventHost(engine, {'TELEGRAM_CHAT_ID': 'offline'}, SimpleNamespace(),
                     transport=transport or (lambda data: sent.append(data) or response()), external=external)
    return engine, host, sent


def post(engine, *, external=False):
    engine.ingress.post(Kind.COMMAND, source='offline', source_seq=1, source_time=1000,
                        payload={'symbol': 'TEST', 'text': 'accepted', 'external': external})


def test_stop_processes_preaccepted_input_and_matches_uninterrupted_replay():
    live, host, sent = make_host()
    replay, replay_host, replay_sent = make_host()
    post(live); post(replay)
    host.stop.set(); host.run()
    assert host.close()
    replay.run(); replay_host.drain_outputs()
    assert sent == replay_sent and len(sent) == 1
    assert export_engine_state(live) == export_engine_state(replay)
    assert not len(live.ingress) and not live._internal
    assert host.close() and len(sent) == 1


def test_late_producer_command_and_external_reply_are_drained_before_close():
    completed = []
    class External:
        def telegram_loop(self, inputs, stop):
            assert stop.wait(3)
            post(inputs.engine, external=True)
        def canonicalize(self, _text):
            completed.append('reply')
            return 'canonical'
        def close(self):completed.append('close')
    engine, host, sent = make_host(External())
    host.start_io()
    assert host.close(timeout=3)
    assert engine.strategy_state['TEST']['seen'] == [Kind.COMMAND.value, Kind.EXTERNAL_REPLY.value]
    assert completed == ['close', 'reply'] and len(sent) == 1
    assert not host.pending_external and not len(engine.ingress)
    assert export_engine_state(engine)['test_state.json'] == json.dumps(engine.strategy_state['TEST']['seen'])


def test_preaccepted_market_publication_is_processed_before_state_export():
    engine, host, sent = make_host()
    snapshot = FeedSnapshot(np.array([60, 120]), np.array([1, 1]),
                            np.ones((2, len(PIPE_VALUE_COLUMNS))), seq=1)
    engine.ingress.post(Kind.MARKET_BUNDLE, source='staff', source_seq=1, source_time=120000,
                        payload={'symbol': 'TEST', 'feeds': {'1m': snapshot}})
    host.stop.set()
    assert host.close()
    assert engine.board._observed[('TEST', '1m')] == 120000
    assert json.loads(export_engine_state(engine)['test_state.json']) == [Kind.MARKET_BUNDLE.value]
    assert len(sent) == 1


def test_clean_shutdown_reports_bounded_delivery_failure_without_success_receipt():
    engine, host, _sent = make_host(transport=lambda _data: response(429, 31))
    recorded = []
    host.output.result_observer = lambda _event, _chat, status: recorded.append(status)
    post(engine)
    assert host.close()
    assert recorded == ['전송 실패'] and not host.output.receipts
    assert not host.outputs.unfinished_tasks


def test_economy_producer_waiting_for_receipt_finishes_during_shutdown():
    engine, host, sent = make_host()
    host.start_io()
    accepted, result = threading.Event(), []
    original = engine.ingress.post
    def tracked(*args, **kwargs):
        original(*args, **kwargs)
        accepted.set()
    engine.ingress.post = tracked
    producer = threading.Thread(target=lambda: result.append(EconomyOutputPort(host).send('offline news')))
    host.workers.append(producer)
    producer.start()
    assert accepted.wait(3)
    assert host.close(timeout=3)
    assert result == [True] and len(sent) == 1


@pytest.mark.parametrize('internal', [False, True])
def test_export_refuses_unprocessed_input_or_internal_event(internal):
    engine, _host, _sent = make_host()
    if internal:engine._internal.append(object())
    else:post(engine)
    with pytest.raises(RuntimeError, match='drained'):
        export_engine_state(engine)


def test_shutdown_timeout_with_active_producer_is_not_clean():
    engine, host, _sent = make_host()
    release = threading.Event()
    producer = threading.Thread(target=lambda: release.wait(3))
    host.workers.append(producer)
    producer.start()
    try:
        assert host.close(timeout=0) is False
    finally:
        release.set(); producer.join(3)


def test_shutdown_timeout_with_active_output_is_not_clean():
    entered, release = threading.Event(), threading.Event()
    def transport(_data):
        entered.set()
        assert release.wait(3)
        return response()
    engine, host, _sent = make_host(transport=transport)
    host.start_io(); post(engine); engine.run()
    assert entered.wait(3)
    try:
        assert host.close(timeout=0) is False
        assert host.outputs.unfinished_tasks
    finally:
        release.set()
        for worker in host.workers:worker.join(3)


def test_shutdown_dispatch_failure_never_reports_clean(monkeypatch):
    engine, host, _sent = make_host()
    post(engine)
    def broken():raise RuntimeError('fake dispatch failure')
    monkeypatch.setattr(engine, 'run', broken)
    assert host.close() is False
    assert len(engine.ingress) == 1
    with pytest.raises(RuntimeError):export_engine_state(engine)
