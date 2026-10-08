"""Candle gates at the actual shared port and event/replay boundary, offline."""
import copy
from pathlib import Path
import sys
from types import SimpleNamespace as NS

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'Part1/program')]
from command_interpreter import MT5_TIMEFRAMES
from event_engine.board import Board
from event_engine.engine import EventEngine
from event_engine.facts import EventFacts
from event_engine.ingress import IngressSequencer
from event_engine.model import Event, FeedSnapshot, Input, Kind, Resolution, Subscriptions
from event_engine.replay import replay
from staff_schema import PIPE_VALUE_COLUMNS
from strategy_recipe.contract import execution_plan, validate_meaning
from strategy_recipe.port import IntentPort


def candle(side='BULL', bar_state='FORMING', tfs=('1h',), **extras):
    return dict(kind='CANDLE_STATE', side=side, bar_state=bar_state, tfs=list(tfs), **extras)


def rule(condition=None, direction='LONG', **extras):
    result = dict(symbols=['TEST'], direction=direction, steps=[], persistent=True,
        order_mode='SIMULTANEOUS', final_conditions=[condition or candle()],
        final=dict(kind='OZ', tfs=['1m'], validation_mode='BLIND', trigger_mode='OZ'))
    result.update(extras)
    return result


def snapshot(prices=(99., 101., 101.), opens=None, stamps=None, epoch='feed', seq=1):
    opens = [100.] * len(prices) if opens is None else opens
    stamps = list(range(0, len(prices) * 3600, 3600)) if stamps is None else stamps
    values = np.full((len(prices), len(PIPE_VALUE_COLUMNS)), 100., dtype=float)
    for name, data in dict(open=opens, close=prices, low=np.minimum(opens, prices)-1,
                           high=np.maximum(opens, prices)+1).items():
        values[:, PIPE_VALUE_COLUMNS.index(name)] = data
    return FeedSnapshot(np.array(stamps, dtype=np.int64), np.ones(len(prices)), values, seq, epoch)


class Consumer:
    name = 'candle_test'
    def subscriptions(self):
        return Subscriptions(symbols=('TEST',), kinds=(Kind.MARKET_BUNDLE, Kind.EXTERNAL_REPLY),
                             resolution=Resolution.TICK)


class API:
    def __init__(self, board=None, now=7200.):
        self.board, self.now = board, now
        self.resources, self.armed, self.deliveries = {}, {}, []
    def market_context(self): return self.board, 'TEST', self.now
    def shared_resource(self, name, factory):
        if name not in self.resources: self.resources[name] = factory()
        return self.resources[name]
    def time_allowed(self, filters): return True
    def ensure_oz_watch(self, payload, **kwargs): self.armed[payload['watch_id']] = copy.deepcopy(payload)
    def cancel_oz_watches(self, ids):
        for key in ids: self.armed.pop(key, None)
    def deliver_oz_event_core(self, event):
        self.deliveries.append(copy.deepcopy(event))
        return {'delivered': True}


def port_for(condition=None, direction='LONG', **extras):
    board = Board(EventFacts())
    api = API()
    port = IntentPort(NS(special_api=api), execution_plan(rule(condition, direction, **extras))['meaning'],
                      '캔들 전략', 'CANDLE_TEST')
    return board, api, port


def publish(board, api, feeds, now=7200.):
    board.commit(Event(1, 1, 'test', 1, int(now * 1000), Kind.MARKET_BUNDLE,
                       dict(symbol='TEST', feeds=feeds)))
    api.now = now
    api.board = board.view({}, Consumer(), int(now * 1000))


def final_event(port, now, direction='LONG'):
    return dict(source_spec_id=port._sid(port.machines[0]), symbol='TEST', source_tf='1m',
        direction=direction, event_time=now, current_price=100.5,
        validation_mode='BLIND', trigger_mode='OZ', indicators_text='PRICE')


@pytest.mark.parametrize('side,close,expected', [('BULL',101.,True), ('BULL',99.,False),
    ('BULL',100.,False), ('BEAR',99.,True), ('BEAR',101.,False), ('BEAR',100.,False)])
@pytest.mark.parametrize('direction', ['LONG', 'SHORT'])
def test_candle_side_is_independent_of_trade_direction(side, close, expected, direction):
    board, api, port = port_for(candle(side), direction)
    publish(board, api, {'1h':snapshot(prices=(close,))})
    result = port.observe(api.board, 'TEST', candle(side), direction, api.now)
    assert result['ready'] and result['matched'] is expected and not result['event']


@pytest.mark.parametrize('tf', MT5_TIMEFRAMES)
@pytest.mark.parametrize('bar_state,expected', [('FORMING',False), ('CLOSED',True)])
def test_every_frame_and_explicit_forming_closed_boundary(tf, bar_state, expected):
    step = candle(tfs=(tf,), bar_state=bar_state)
    board, api, port = port_for(step)
    publish(board, api, {tf:snapshot(prices=(101.,99.))})
    assert port.observe(api.board, 'TEST', step, 'LONG', api.now)['matched'] is expected


@pytest.mark.parametrize('combine,expected', [('ALL',False), ('ANY',True)])
def test_multi_frame_comparison(combine, expected):
    step = candle(tfs=('1h','4h'), tf_combine=combine)
    board, api, port = port_for(step)
    publish(board, api, {'1h':snapshot((101.,)), '4h':snapshot((99.,))})
    assert port.observe(api.board, 'TEST', step, 'LONG', api.now)['matched'] is expected


@pytest.mark.parametrize('bad', [dict(side=None),dict(side='LONG'),dict(side='ABOVE'),
    dict(bar_state=None),dict(bar_state='UNSPECIFIED')])
def test_no_implicit_or_ma_based_candle_semantics(bad):
    with pytest.raises(ValueError): validate_meaning(rule(candle(**bad)))


def test_forming_reversal_equal_and_next_bar_rechecked_at_oz_time():
    board, api, port = port_for()
    expected = []
    for index, close in enumerate((101.,99.,100.,101.,99.)):
        now = 7200. + index
        publish(board, api, {'1h':snapshot((99.,101.,close), seq=index+1)}, now)
        port.poll()
        result = port.handle_oz_event(final_event(port, now))
        expected.append(close > 100.)
        assert bool(result.get('suppressed')) is not expected[-1]
    assert [e['event_time'] for e in api.deliveries] == [7200.,7203.]
    publish(board, api, {'1h':snapshot((101.,99.,100.), stamps=(3600,7200,10800))}, 10800.)
    assert port.handle_oz_event(final_event(port,10800.))['suppressed']
    assert len(api.deliveries) == 2


def test_sequential_setup_cannot_latch_old_bullish_condition():
    board, api, port = port_for(steps=[{'kind':'BAR_CLOSE','tfs':['1m']}], order_mode='SEQUENTIAL')
    publish(board, api, {'1h':snapshot((101.,)), '1m':snapshot((100.,100.,100.),stamps=(7080,7140,7200))})
    port.poll()
    publish(board, api, {'1h':snapshot((101.,)), '1m':snapshot((100.,100.,100.),stamps=(7140,7200,7260))},7260.)
    port.poll()
    assert port.machines[0].active
    publish(board, api, {'1h':snapshot((99.,))},7261.)
    assert port.handle_oz_event(final_event(port,7261.))['suppressed']
    assert not api.deliveries


def test_missing_stale_invalid_and_negated_invalid_never_allow_signal():
    step = candle(negated=True)
    board, api, port = port_for(step)
    publish(board, api, {'1m':snapshot((100.,))})
    result = port.observe(api.board,'TEST',step,'LONG',api.now)
    assert not result['ready'] and not result['matched']
    publish(board, api, {'1h':snapshot((float('nan'),))})
    result = port.observe(api.board,'TEST',step,'LONG',api.now)
    assert not result['ready'] and not result['matched']
    publish(board, api, {'1h':snapshot((99.,))})
    api.board = board.view({},Consumer(),int((api.now+31)*1000))
    result = port.observe(api.board,'TEST',step,'LONG',api.now+31)
    assert not result['ready'] and not result['matched']


class GateConsumer(Consumer):
    def __init__(self, step):
        self.api = API()
        self.port = IntentPort(NS(special_api=self.api),execution_plan(rule(step))['meaning'],'캔들 전략','GATE')
    def on_event(self, event, board, state, emit):
        self.api.board, self.api.now = board, event.source_time/1000
        if event.kind == Kind.MARKET_BUNDLE: self.port.poll()
        else: self.port.handle_oz_event(final_event(self.port,self.api.now))


@pytest.mark.parametrize('bar_state,expected', [('FORMING',[7200.,7203.,7204.]),('CLOSED',[7200.,7201.,7202.,7203.])])
def test_live_ingress_and_backtest_replay_same_gates(bar_state, expected):
    inputs = []
    for index, close in enumerate((101.,99.,100.,101.,101.)):
        now = (7200+index)*1000
        prices = (99.,101.,close) if index < 4 else (101.,99.,close)
        inputs.append(Input('STAFF', index+1, now, Kind.MARKET_BUNDLE,
                            dict(symbol='TEST',feeds={'1h':snapshot(prices,seq=index+1)}),0))
        inputs.append(Input('OZ', index+1, now, Kind.EXTERNAL_REPLY,dict(symbol='TEST'),0))
    consumers = [GateConsumer(candle(bar_state=bar_state)) for _ in range(2)]
    engines = [EventEngine(IngressSequencer(),[consumer]) for consumer in consumers]
    for item in inputs:
        engines[0].ingress.post(item.kind,source=item.source,source_seq=item.source_seq,
                               source_time=item.source_time,payload=item.payload)
        engines[0].run()
    proof = replay(engines[1],inputs,Resolution.TICK)
    assert not proof['approximate']
    assert not any(engine.error_log or engine.disabled for engine in engines)
    assert consumers[0].api.deliveries == consumers[1].api.deliveries
    assert [e['event_time'] for e in consumers[0].api.deliveries] == expected
