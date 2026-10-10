"""Entry metadata comes from accepted Recipe observations, not TF guesses."""
import csv
import json
import socket
import sys
from types import SimpleNamespace as NS

import numpy as np
import pytest

from test_recipe_alerts101 import ROOT, manager, rule
from staff_schema import PIPE_VALUE_COLUMNS
from strategy_recipe.contract import execution_plan
from strategy_recipe.port import IntentPort
from strategy_recipe.runtime import IntentMachine


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def deny(*args, **kwargs):
        raise AssertionError('Recipe metadata tests cannot send network traffic')
    monkeypatch.setattr(socket, 'create_connection', deny)
    monkeypatch.setattr(socket.socket, 'connect', deny)


def setup(steps, **extra):
    owner, kernel = manager()
    for tf, seconds in (('1m', 60), ('1h', 3600)):
        stamp = 1500 // seconds * seconds
        values = np.full((3, len(PIPE_VALUE_COLUMNS)), 100., dtype=float)
        for name, value in (('close', 110.), ('high', 111.), ('low', 99.)):
            values[:, PIPE_VALUE_COLUMNS.index(name)] = value
        kernel.board.feeds['TEST', tf] = NS(time=np.array([stamp - 2 * seconds,
            stamp - seconds, stamp]), values=values, volume=np.ones(3),
            source_epoch='feed-' + tf, seq=3)
        kernel.board.observed['TEST', tf] = kernel.timestamp
    kernel.board.processor = lambda name: {'resources': None}
    meaning = rule('NOTIFY', steps=steps, **extra)
    return IntentPort(owner, meaning, 'accepted bindings', 'USER_BINDING'), kernel


def clock(kernel, now):
    kernel.timestamp = now * 1000
    kernel.board.source_time = kernel.timestamp
    for key in kernel.board.observed: kernel.board.observed[key] = kernel.timestamp
    kernel.board.publication_token += 1


def ma(kind, tfs, **fields):
    return {'kind': kind, 'tfs': tfs, 'ma_family': 'EMA', 'slow_period': 50, **fields}


def oz(tfs=('1m',), **fields):
    return {'kind': 'OZ_ALERT', 'tfs': list(tfs), 'validation_mode': 'BLIND',
            'trigger_mode': 'BREAKER', **fields}


def observation(tf, at, token, *, matched=True, event=True, tfs=None, resource=None):
    return {'ready': True, 'matched': matched, 'event': event, 'source_tf': tf,
            'condition_tfs': tuple(tfs or (tf,)), 'at': at, 'token': token, 'resource': resource}


def test_sequential_source_is_frozen_before_event_only_notification_reset():
    port, kernel = setup([ma('MA_PRICE_TOUCH', ['5m', '1h']),
        ma('MA_PRICE_CROSS', ['SOURCE'], relation='BREAK_UP')], order_mode='SEQUENTIAL')
    phase = [0]
    def observe(board, symbol, step, direction, now, machine):
        if step['kind'] == 'MA_PRICE_TOUCH':
            return observation('1h', 1500, 'touch')
        return observation(port._bound_tf('SOURCE', machine), 1520, 'cross', matched=phase[0] == 1)
    port.observe = observe
    port.poll()
    assert port.machines[0].source_tf == '1h' and not kernel.messages
    phase[0] = 1; clock(kernel, 1520); port.poll()
    message = kernel.messages[-1]
    assert message['source_tf'] == message['signal_tf'] == '1h'
    assert message['signal_source'] == 'SIGNAL'
    assert port.machines[0].source_tf is None
    assert not port.machines[0].step_contexts
    assert port.machines[0].notification_context['condition_tfs'] == ('1h',)


def test_previous_any_choices_survive_json_checkpoint_and_source_completion():
    steps = [ma('MA_PRICE_TOUCH', ['5m', '1h']),
        ma('MA_PRICE_CROSS', ['1m', '5m'], relation='BREAK_UP'),
        {'kind': 'BAR_CLOSE', 'tfs': ['SOURCE']}]
    port, kernel = setup(steps, order_mode='SEQUENTIAL')
    phase = [0]
    def observe(board, symbol, step, direction, now, machine):
        i = next(i for i, candidate in enumerate(machine.meaning['steps']) if candidate is step)
        tf = ('1h', '5m', port._bound_tf('SOURCE', machine))[i]
        return observation(tf, 1500 + i * 20, ('step', i), matched=i <= phase[0])
    port.observe = observe; port.poll()
    phase[0] = 1; clock(kernel, 1520); port.poll()
    saved = json.loads(json.dumps(port.checkpoint()))
    fresh = IntentPort(port.manager, port.meaning, port.name, port.namespace)
    assert fresh.restore(saved)
    assert fresh.machines[0].step_contexts[0]['condition_tfs'] == ('1h',)
    assert fresh.machines[0].step_contexts[1]['condition_tfs'] == ('5m',)
    fresh.observe = observe
    phase[0] = 2; clock(kernel, 1540); fresh.poll()
    assert len(kernel.messages) == 1
    assert kernel.messages[0]['source_tf'] == '1h'
    assert kernel.messages[0]['signal_tf'] == '5m'
    assert fresh.machines[0].notification_context['condition_tfs'] == ('1h', '5m')


@pytest.mark.parametrize('combine,expected', [('ANY', ('5m',)), ('ALL', ('5m', '1h'))])
def test_actual_observe_keeps_selected_any_frame_and_all_required_frames(combine, expected):
    port, kernel = setup([ma('MA_PRICE_STATE', ['5m', '1h'], tf_combine=combine, side='BELOW')])
    # 5m close=2400 is above its EMA2400 only if changed; make both states BELOW.
    for snapshot in kernel.board.feeds.values():
        snapshot.values[:, PIPE_VALUE_COLUMNS.index('ema_50')] = 3000.
    obs = port.observe(kernel.board, 'TEST', port.meaning['steps'][0], 'LONG', 1500, port.machines[0])
    assert obs['matched'] and obs['condition_tfs'] == expected
    port.poll()
    assert kernel.messages[0]['signal_tf'] == '5m'
    assert port.machines[0].notification_context['condition_tfs'] == expected


@pytest.mark.parametrize('price_tf', ['1m', 'SOURCE'])
def test_price_frame_is_included_in_signal_binding(price_tf):
    port, kernel = setup([ma('MA_PRICE_STATE', ['1h'], price_tf=price_tf, side='ABOVE')])
    # SOURCE is an existing concrete binding supplied by an accepted event.
    if price_tf == 'SOURCE': port.machines[0].source_tf = '1m'
    port.poll()
    assert len(kernel.messages) == 1
    assert kernel.messages[0]['source_tf'] == '1h'
    assert kernel.messages[0]['signal_tf'] == '1m'
    assert port.machines[0].notification_context['condition_tfs'] == ('1h', '1m')


def test_source_price_frame_is_an_actual_consumed_event_binding():
    port, kernel = setup([ma('MA_PRICE_TOUCH', ['1m', '5m']),
        ma('MA_PRICE_STATE', ['1h'], price_tf='SOURCE', side='ABOVE')], order_mode='SEQUENTIAL')
    kernel.board.feeds['TEST', '1h'].values[:, PIPE_VALUE_COLUMNS.index('close')] = 90.
    kernel.board.feeds['TEST', '1h'].values[:, PIPE_VALUE_COLUMNS.index('low')] = 89.
    real_observe = port.observe
    def observe(board, symbol, step, direction, now, machine):
        if step['kind'] == 'MA_PRICE_TOUCH': return observation('1m', 1500, 'touch')
        return real_observe(board, symbol, step, direction, now, machine)
    port.observe = observe
    port.poll(); assert not kernel.messages and port.machines[0].source_tf == '1m'
    clock(kernel, 1520); port.poll()
    assert kernel.messages[0]['signal_tf'] == '1m'
    assert port.machines[0].notification_context['condition_tfs'] == ('1m', '1h')


def test_final_price_frame_uses_existing_final_binding_without_guessing():
    owner, kernel = manager()
    values = np.full((3, len(PIPE_VALUE_COLUMNS)), 100., dtype=float)
    values[:, PIPE_VALUE_COLUMNS.index('close')] = 110.
    for tf in ('1m', '1h'):
        kernel.board.feeds['TEST', tf] = NS(time=np.array([0, 60, 120]),
            values=values.copy(), volume=np.ones(3), source_epoch='feed-' + tf, seq=3)
        kernel.board.observed['TEST', tf] = kernel.timestamp
    meaning = execution_plan({'symbols': ['TEST'], 'direction': 'LONG',
        'steps': [ma('MA_PRICE_STATE', ['1h'], price_tf='FINAL', side='ABOVE')],
        'final': {'kind': 'OZ', 'tfs': ['1m'], 'validation_mode': 'BLIND', 'trigger_mode': 'BREAKER'}})['meaning']
    port = IntentPort(owner, meaning, 'final binding', 'USER_FINAL')
    obs = port.observe(kernel.board, 'TEST', port.meaning['steps'][0], 'LONG', 1500, port.machines[0])
    assert obs['matched'] and obs['condition_tfs'] == ('1h', '1m')


def original_oz(port, at=1500):
    step = next(s for s in port._all_steps() if s['kind'] == 'OZ_ALERT')
    return {'source_spec_id': port._dependency_id('TEST', '1m', 'OZ_SOURCE', step),
        'symbol': 'TEST', 'source_tf': '1m', 'signal_tf': '1m', 'direction': 'LONG',
        'validation_mode': 'BLIND', 'trigger_mode': 'BREAKER', 'event_time': at,
        'b0_time': 1380, 'b0_price': 99., 'current_price': 101.25,
        'neckline_price': 104., 'neckline_time_ms': 1440000}


def test_final_consumed_oz_preserves_original_pattern_neckline_and_price():
    port, kernel = setup([oz()])
    event = original_oz(port)
    assert port.handle_oz_event(event)['suppressed']
    assert len(kernel.messages) == 1
    message = kernel.messages[0]
    assert message['signal_source'] == 'OZ'
    assert message['signal_price'] == event['current_price']
    for key in ('signal_tf', 'source_tf', 'b0_time', 'b0_price',
                'neckline_price', 'neckline_time_ms'):
        assert message[key] == event[key]
    # The frame its first condition matched on: the OZ step's own 1m.
    assert message['env_tf'] == '1m'
    assert not port.machines[0].step_contexts


@pytest.mark.parametrize('oz_first', [False, True])
def test_state_gate_polling_time_never_reclassifies_actual_oz_trigger(oz_first):
    steps = [ma('MA_PRICE_STATE', ['1h'], side='ABOVE'), oz()]
    if oz_first: steps.reverse()
    port, kernel = setup(steps)
    event = original_oz(port, at=1500)
    clock(kernel, 1510)  # State is current, while the accepted event is older.
    port.handle_oz_event(event)
    assert len(kernel.messages) == 1
    assert kernel.messages[0]['signal_source'] == 'OZ'
    assert kernel.messages[0]['signal_price'] == 110.
    assert kernel.messages[0]['event_time'] == 1510
    assert kernel.messages[0]['b0_price'] == event['b0_price']


def test_prior_oz_then_ordinary_event_does_not_inherit_oz_pattern_metadata():
    port, kernel = setup([oz(), ma('MA_PRICE_CROSS', ['SOURCE'], relation='BREAK_UP')],
                          order_mode='SEQUENTIAL')
    real_observe = port.observe
    def observe(board, symbol, step, direction, now, machine):
        if step['kind'] == 'OZ_ALERT': return real_observe(board, symbol, step, direction, now, machine)
        return observation(port._bound_tf('SOURCE', machine), 1520, 'cross', matched=now >= 1520)
    port.observe = observe
    port.handle_oz_event(original_oz(port)); assert not kernel.messages
    clock(kernel, 1520); port.poll()
    message = kernel.messages[0]
    assert message['signal_source'] == 'SIGNAL' and message['signal_tf'] == '1m'
    assert not any(message.get(key) is not None for key in ('neckline_price', 'b0_price', 'b0_time'))


@pytest.mark.parametrize('oz_after', [False, True])
def test_after_stage_classifies_its_actual_completing_trigger(oz_after):
    first = ma('MA_PRICE_TOUCH', ['5m']) if oz_after else oz()
    after = oz() if oz_after else ma('MA_PRICE_CROSS', ['1h'], relation='BREAK_UP')
    port, kernel = setup([first], after_conditions=[after], order_mode='SEQUENTIAL')
    real_observe = port.observe
    def observe(board, symbol, step, direction, now, machine):
        if step['kind'] == 'OZ_ALERT': return real_observe(board, symbol, step, direction, now, machine)
        is_after = step is machine.meaning['after_conditions'][0]
        return observation(step['tfs'][0], 1520 if is_after else 1500,
                           'after' if is_after else 'setup', matched=now >= (1520 if is_after else 1500))
    port.observe = observe
    if oz_after: port.poll()
    else: port.handle_oz_event(original_oz(port))
    assert port.machines[0].active and not kernel.messages
    clock(kernel, 1520)
    if oz_after: port.handle_oz_event(original_oz(port, at=1520))
    else: port.poll()
    message = kernel.messages[0]
    assert message['signal_source'] == ('OZ' if oz_after else 'SIGNAL')
    assert (message.get('b0_price') is not None) == oz_after
    assert message['signal_tf'] == '1m'


def test_after_state_gate_does_not_replace_its_oz_event_with_poll_time():
    port, kernel = setup([ma('MA_PRICE_TOUCH', ['5m'])],
        after_conditions=[oz(), ma('MA_PRICE_STATE', ['1h'], side='ABOVE')], order_mode='SEQUENTIAL')
    real_observe = port.observe
    def observe(board, symbol, step, direction, now, machine):
        if step['kind'] != 'MA_PRICE_TOUCH': return real_observe(board, symbol, step, direction, now, machine)
        return observation('5m', 1500, 'setup')
    port.observe = observe
    port.poll(); assert not kernel.messages
    clock(kernel, 1530)
    port.handle_oz_event(original_oz(port, at=1520))
    assert kernel.messages[0]['signal_source'] == 'OZ'
    assert kernel.messages[0]['signal_price'] == 110.
    assert kernel.messages[0]['event_time'] == 1530
    assert kernel.messages[0]['b0_price'] == 99.


def test_delayed_oz_completion_csv_uses_current_time_price_and_original_pattern(tmp_path):
    sys.path.insert(0, str(ROOT / 'Part2'))
    from event_backtest.warehouse import ResultWriter
    from event_engine.model import Event, Kind
    port, kernel = setup([ma('MA_PRICE_TOUCH', ['5m'])],
                         after_conditions=[oz()], order_mode='SEQUENTIAL')
    real_observe = port.observe
    def observe(board, symbol, step, direction, now, machine):
        if step['kind'] == 'OZ_ALERT': return real_observe(board, symbol, step, direction, now, machine)
        return observation('5m', 1500, 'setup')
    port.observe = observe; port.poll()
    clock(kernel, 1530); port.handle_oz_event(original_oz(port, at=1520))
    path = tmp_path / 'delayed.csv'
    writer = ResultWriter(path, {'run_id': 'delayed'}, 0, 2000000)
    writer.accept(Event(1, 0, 'test', None, kernel.timestamp, Kind.SIGNAL,
        {'strategy': port.namespace, 'symbol': 'TEST', 'signal_id': 'delayed',
         'content': {'type': 'NOTIFICATION', **kernel.messages[0]}}))
    writer.close()
    with path.open(encoding='utf-8', newline='') as handle:
        row = next(csv.DictReader(handle))
    assert int(row['time_ms']) == 1530000
    assert float(row['signal_price']) == 110.
    assert row['signal_source'] == 'OZ'
    assert float(row['neckline_price']) == original_oz(port)['neckline_price']


def test_after_state_completion_does_not_reuse_prior_oz_trigger():
    port, kernel = setup([oz()], after_conditions=[ma('MA_PRICE_STATE', ['1h'], side='ABOVE')],
                         order_mode='SEQUENTIAL')
    port.handle_oz_event(original_oz(port))
    assert kernel.messages[0]['signal_source'] == 'SIGNAL'
    assert kernel.messages[0].get('neckline_price') is None


def test_current_state_any_binding_is_read_when_after_trigger_completes():
    port, kernel = setup([ma('MA_PRICE_STATE', ['1m', '1h'], side='ABOVE')],
        after_conditions=[ma('MA_PRICE_CROSS', ['5m'], relation='BREAK_UP')], order_mode='SEQUENTIAL')
    def observe(board, symbol, step, direction, now, machine):
        if step['kind'] == 'MA_PRICE_STATE':
            return observation('1m' if now == 1500 else '1h', now, None, event=False)
        return observation('5m', 1520, 'after', matched=now >= 1520)
    port.observe = observe
    port.poll(); assert not kernel.messages
    clock(kernel, 1520); port.poll()
    assert kernel.messages[0]['signal_tf'] == '5m'
    assert port.machines[0].notification_context['condition_tfs'] == ('1h', '5m')


def test_rewind_reset_restart_and_unordered_window_remove_only_metadata_descendants():
    steps = [oz(capture='parent'), oz(capture='child')]
    meaning = rule('NOTIFY', steps=steps, order_mode='SEQUENTIAL')
    machine = IntentMachine(meaning, 'TEST', 'LONG')
    a = observation('1h', 10, 'parent', resource={'tf': '1h', 'at': 10})
    b = observation('1m', 20, 'child', matched=False, resource={'tf': '1m', 'at': 20})
    assert machine.advance(10, [a, b]) == []
    machine.rewind(['child'])
    assert set(machine.step_contexts) == {0}
    machine._reset(); assert not machine.step_contexts and machine.notification_context is None
    assert machine.advance(30, [dict(a, at=30, token='new'), b], restarted=True) == []
    assert machine.step_contexts[0]['at'] == 30
    unordered = IntentMachine(rule('NOTIFY', steps=steps, order_mode='UNORDERED', within_sec=5), 'TEST', 'LONG')
    unordered.advance(10, [a, b])
    unordered.advance(20, [dict(a, matched=False), dict(b, matched=True)])
    assert set(unordered.step_contexts) == {1}
