"""Source-event windows, lifecycle boundaries and the shared LIVE/replay path."""
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'Part1/program'))
from strategy_recipe.contract import execution_plan
from strategy_recipe.runtime import IntentMachine
from strategy_recipe.port import IntentPort

spec = importlib.util.spec_from_file_location('window95_helpers', ROOT / 'Part3/tests/test_recipe_runtime84.py')
h = importlib.util.module_from_spec(spec)
spec.loader.exec_module(h)


def plan(order='SEQUENTIAL', **extra):
    value = h.meaning([{'kind': 'OZ_ALERT', 'tfs': [tf], 'validation_mode': 'NORMAL', 'trigger_mode': 'OZ'}
                      for tf in ('1m', '5m')], order_mode=order, within_sec=600, final={'kind': 'NOTIFY'})
    value.update(extra)
    return execution_plan(value)['meaning']


def event(token, at, matched=True):
    return {'ready': True, 'matched': matched, 'event': True, 'token': token, 'at': at}


def machine(order='SEQUENTIAL', **extra):
    result = IntentMachine(plan(order, **extra), 'TEST', 'LONG')
    result.advance(0, [event('A0', 0, False), event('B0', 0, False)])
    return result


def make_port(order='SEQUENTIAL', now=0):
    board = h.Board({'1m': h.rows([-120, -60, 0]), '5m': h.rows([-600, -300, 0])}, now=now)
    board.processor = lambda name: {'resources': {('TEST', tf, 'NORMAL', 'OZ', 'LONG'): 0 for tf in ('1m', '5m')}}
    port, api = h.make_port(plan(order), board, now=now)
    notices = []
    api.notify = lambda *args, **kwargs: notices.append(kwargs['event'])
    return port, api, notices


def deliver(port, api, tf, source_at, observed_at):
    api.now = observed_at
    port.handle_oz_event({'source_spec_id': 'CUSTOM:OZ_SOURCE:fixture', 'symbol': 'TEST',
                         'source_tf': tf, 'validation_mode': 'NORMAL', 'trigger_mode': 'OZ',
                         'direction': 'LONG', 'event_time': source_at, 'b0_time': 0, 'b0_price': 100})


@pytest.mark.parametrize('order', ['SEQUENTIAL', 'UNORDERED'])
@pytest.mark.parametrize('last_at,expected', [(600, 1), (601, 1), (602, 0)])
@pytest.mark.parametrize('delivery', ['immediate', 'first_delayed', 'last_delayed'])
def test_real_port_uses_source_gap_and_inclusive_boundary(order, last_at, expected, delivery):
    port, api, notices = make_port(order)
    deliver(port, api, '1m', 1, 100 if delivery == 'first_delayed' else 1)
    deliver(port, api, '5m', last_at, 700 if delivery == 'last_delayed' else last_at)
    assert len(notices) == expected
    port.poll()
    assert len(notices) == expected, 'Repeated observation must not repeat an event notification'


@pytest.mark.parametrize('order', ['SEQUENTIAL', 'UNORDERED'])
@pytest.mark.parametrize('last_at,expected', [(601, ['NOTIFY']), (602, [])])
def test_events_observed_together_still_obey_source_span(order, last_at, expected):
    m = machine(order)
    assert m.advance(700, [event('A', 1), event('B', last_at)]) == expected


@pytest.mark.parametrize('order', ['SEQUENTIAL', 'UNORDERED'])
def test_prior_expiry_cannot_be_revived_by_late_delivery(order):
    m = machine(order)
    assert m.advance(100, [event('A', 1), event('B', 0, False)]) == []
    assert m.advance(602, [event('A', 1, False), event('B', 0, False)]) == []
    assert m.advance(700, [event('A', 1), event('B', 601)]) == []
    assert m.advance(701, [event('A2', 701), event('B', 601, False)]) == []
    assert m.advance(702, [event('A2', 701), event('B2', 702)]) == ['NOTIFY']


@pytest.mark.parametrize('order', ['SEQUENTIAL', 'UNORDERED'])
def test_expiry_and_new_first_event_in_same_publication_preserve_new_chain(order):
    m = machine(order)
    m.advance(1, [event('A', 1), event('B0', 0, False)])
    assert m.advance(602, [event('A2', 602), event('B0', 0, False)]) == []
    # A BAR_CLOSE-like observation is present only in its own publication.
    assert m.advance(603, [event('A2', 602, False), event('B2', 603)]) == ['NOTIFY']


@pytest.mark.parametrize('order', ['SEQUENTIAL', 'UNORDERED'])
def test_missing_activation_snapshot_does_not_extend_pending_window(order):
    m = machine(order, lifecycle={'snapshots': {'anchor': {'tf': '1m', 'field': 'close', 'bar_state': 'CLOSED'}}})
    assert m.advance(100, [event('A', 1), event('B', 100)]) == []
    assert m.advance(700, [event('A', 1), event('B', 100)],
                     lifecycle_context={'snapshots': {'anchor': 100.}}) == []
    assert not m.active


@pytest.mark.parametrize('order', ['SEQUENTIAL', 'UNORDERED'])
def test_json_port_checkpoint_keeps_source_window_and_activation(order):
    port, api, notices = make_port(order)
    deliver(port, api, '1m', 1, 100)
    saved = json.loads(json.dumps(port.checkpoint()))
    replay, replay_api, replay_notices = make_port(order, now=100)
    assert replay.restore(saved)
    for at, observed in [(601, 700), (602, 701)]:
        deliver(port, api, '5m', at, observed)
        deliver(replay, replay_api, '5m', at, observed)
    assert notices == replay_notices and len(notices) == 1
    # The completed activation was reset at observation 700. A retained
    # source event from before that boundary cannot form another setup.
    deliver(port, api, '1m', 699, 702)
    deliver(replay, replay_api, '1m', 699, 702)
    assert notices == replay_notices and len(notices) == 1


@pytest.mark.parametrize('at', [None, -1])
def test_missing_timestamp_and_pre_activation_event_remain_ineligible(at):
    m = machine()
    assert m.advance(100, [event('A', at), event('B', 99)]) == []
    assert m.stage == 0


@pytest.mark.parametrize('follow_at', [0, 1])
def test_sequence_rejects_backward_and_same_instant_event(follow_at):
    m = machine()
    assert m.advance(100, [event('A', 1), event('B', follow_at)]) == []
    assert m.advance(101, [event('A', 1), event('B2', 2)]) == ['NOTIFY']


def test_unordered_accepts_source_order_independent_of_delivery_order():
    m = machine('UNORDERED')
    assert m.advance(600, [event('A', 600), event('B', 0, False)]) == []
    assert m.advance(601, [event('A', 600), event('B', 1)]) == ['NOTIFY']


@pytest.mark.parametrize('order', ['SEQUENTIAL', 'UNORDERED'])
@pytest.mark.parametrize('now,expected', [(601, ['NOTIFY']), (602, [])])
def test_state_gate_uses_current_board_time(order, now, expected):
    value = plan(order)
    value['steps'][1] = {'kind': 'MA_STATE', 'tfs': ['5m']}
    m = IntentMachine(value, 'TEST', 'LONG')
    state = {'ready': True, 'matched': False, 'event': False, 'at': 0}
    m.advance(0, [event('A0', 0, False), state])
    m.advance(100, [event('A', 1), state])
    assert m.advance(now, [event('A', 1), dict(state, matched=True, at=1)]) == expected


def test_final_window_still_begins_when_setup_becomes_active():
    m = machine(final={'kind': 'OZ', 'tfs': ['1m'], 'validation_mode': 'NORMAL', 'trigger_mode': 'OZ'},
                final_window_sec=20)
    m.advance(100, [event('A', 1), event('B', 0, False)])
    obs = [event('A', 1), event('B', 601)]
    assert m.advance(700, obs) == ['ARM']
    assert m.final_allowed(720, obs)
    assert not m.final_allowed(721, obs)
    assert m.advance(721, obs) == ['CANCEL']


@pytest.mark.parametrize('gap,expected', [(600, 1), (599, 0)])
def test_closed_bar_events_have_identical_live_and_replay_outputs(gap, expected):
    from event_application import create_event_engine, register_strategy_loader, unregister_strategy_loader
    from event_engine import Kind, FeedSnapshot
    from staff_schema import PIPE_VALUE_COLUMNS
    from strategy_recipe.registry import dependencies

    value = plan(within_sec=gap)
    value['steps'] = [{'kind': 'BAR_CLOSE', 'tfs': [tf]} for tf in ('1m', '5m')]
    # Keep a declared native Fact dependency for the external-plugin loader.
    # Equal MA values in this fixture never trigger the cancellation gate.
    value['cancel_conditions'] = [{'kind': 'MA_STATE', 'tfs': ['1m'], 'ma_family': 'EMA',
                                   'fast_period': 50, 'slow_period': 200, 'side': 'ABOVE'}]
    value = execution_plan(value)['meaning']
    name = 'WINDOW95'
    plugin = SimpleNamespace(__name__=name, register=lambda manager: IntentPort(manager, value, name, name).install(),
                             OZ_DECLARATIONS=())
    register_strategy_loader(name, lambda: plugin, dependencies=dependencies(value))
    base = 1790000040

    def snapshot(end, seconds, seq):
        times = np.arange(end - 239 * seconds, end + 1, seconds, dtype=np.int64)
        values = np.full((len(times), len(PIPE_VALUE_COLUMNS)), 100., dtype=float)
        return FeedSnapshot(times, np.ones(len(times), dtype=np.int64), values, seq, 'window95', {})

    try:
        outputs = []
        for replay in (False, True):
            engine = create_event_engine({'SYMBOLS': 'TEST', 'TELEGRAM_CHAT_ID': 'TEST'}, symbols=['TEST'],
                                         selection=[name], backtest=replay)
            # The first update is a baseline; a forming-bar refresh is not a
            # close. Later closes at +60 and +660 are observed at +100/+700.
            for seq, (now, one, five) in enumerate([(0, 0, 0), (1, 0, 0), (100, 60, 0), (700, 60, 660)], 1):
                engine.ingress.post(Kind.MARKET_BUNDLE, source='staff', source_seq=seq,
                    source_time=(base + now) * 1000, payload={'symbol': 'TEST', 'feeds': {
                        '1m': snapshot(base + one, 60, seq), '5m': snapshot(base + five, 300, seq)}})
                engine.run()
            assert not engine.error_log, engine.error_log
            outputs.append([s.payload['content'] for s in engine.signals
                            if s.payload['content'].get('type') == 'NOTIFICATION'])
        assert outputs[0] == outputs[1]
        assert len(outputs[0]) == expected
    finally:
        unregister_strategy_loader(name)
