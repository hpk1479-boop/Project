"""수정본142 기준 프레임: 가상진입 백테스트 전체를 다른 시간봉으로 시험한다.

- 가상진입의 기준 프레임을 바꾸면 그 백테스트는 전략 조건과 진입이 모두 표(기준·중위·상위·최상위)대로
  옮겨진 전략 사본으로 돈다. 예: SPECIAL9 2분 → 2분 17헐·50헐 골크 뒤 2분 50헐 터치(50헐 위 음봉 마감),
  그때 20분 상승추세, 다음 2분봉이 50헐 터치·50헐 위 양봉 마감이면 진입.
- 기준 프레임을 그대로 두거나(SIGNAL) 얼럿 온리면 레시피 그대로 돈다. 레시피 파일과 Part1 코드는 그대로다.
- 전략 조건이 여러 시간봉에서 맞는 전략(SPECIAL1·2·3·5·6)과 표에 없는 시간봉을 쓰는 전략(SPECIAL4)은
  바꿀 수 없다. 최상위를 쓰는 SPECIAL7·9는 최상위가 없는 4시간을 고를 수 없다.
"""
import copy
from pathlib import Path
import socket
import sys
from types import SimpleNamespace

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'tests'), str(ROOT / 'Part2'), str(ROOT / 'Part1/program')]
from recipe_harness114 import Harness, feed, manager  # noqa: E402
from staff_schema import PIPE_VALUE_COLUMNS as C  # noqa: E402
from oz_engine.common import TF_MAP  # noqa: E402
from strategy_recipe import registry  # noqa: E402
from event_backtest.base_frames import LADDER, engine_plugins, move, moved_plugins  # noqa: E402
from event_backtest.settings import scenario  # noqa: E402
from event_backtest.virtual_contract import required_timeframes  # noqa: E402
from event_backtest.virtual_defaults import on_base, recipe_on_base, strategy_profile, target_policy  # noqa: E402
from event_backtest.virtual_entry import VirtualEntry  # noqa: E402

# The user's table (2026-10-07): base -> middle, upper, top.
TABLE = {'1m': ('3m', '6m', '15m'), '2m': ('6m', '12m', '20m'), '3m': ('10m', '20m', '30m'),
         '4m': ('12m', '30m', '1h'), '5m': ('15m', '30m', '1h'), '6m': ('15m', '30m', '1h'),
         '10m': ('30m', '1h', '2h'), '12m': ('30m', '1h', '2h'), '15m': ('1h', '2h', '4h'),
         '20m': ('1h', '2h', '4h'), '30m': ('2h', '4h', '8h'), '1h': ('3h', '6h', '12h'),
         '2h': ('6h', '12h', '1d'), '3h': ('8h', '12h', '1d'), '4h': ('12h', '1d', None)}
UP_TO_3H = ['1m', '2m', '3m', '4m', '5m', '6m', '10m', '12m', '15m', '20m', '30m', '1h', '2h', '3h']


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def deny(*args, **kwargs):
        raise AssertionError('No network in base-frame tests')
    for name in ('connect', 'connect_ex', 'sendto'):
        monkeypatch.setattr(socket.socket, name, deny)


def intent(name):
    return registry.builtin_entries()[name]['recipe']['strategy_intent']


# ----------------------------------------------------------------- the table and the choices

def test_the_ladder_is_the_users_table_on_the_oz_monitors_map():
    assert {base: row[1:] for base, row in LADDER.items()} == TABLE
    assert all(row[0] == base and row[1:3] == TF_MAP[base] for base, row in LADDER.items())


@pytest.mark.parametrize('name,choices', [
    ('SPECIAL7', UP_TO_3H), ('SPECIAL8', [*UP_TO_3H, '4h']), ('SPECIAL9', UP_TO_3H),
    *((f'SPECIAL{n}', []) for n in (1, 2, 3, 4, 5, 6))])
def test_which_base_frames_a_strategy_can_be_tested_on(name, choices):
    profile = strategy_profile(name)
    assert profile['bases'] == choices
    assert profile['base'] == ('1m' if choices else None)


@pytest.mark.parametrize('target,small,top', [('2m', '2m', '20m'), ('3m', '3m', '30m'), ('15m', '15m', '4h'),
                                              ('3h', '3h', '1d')])
def test_special9_moves_by_the_table(target, small, top):
    moved = move(intent('SPECIAL9'), '1m', target)
    assert [(s['kind'], s['tfs']) for s in moved['steps']] == [
        ('MA_CROSS', [small]), ('TREND', [top]), ('CANDLE_STATE', [small]), ('MA_PRICE_STATE', [small]),
        ('MA_PRICE_TOUCH', [small])]
    assert [(s['kind'], s['tfs']) for s in moved['cancel_conditions']] == [('MA_CROSS', [small])]
    # Everything else, and the recipe itself, stays as written.
    assert {k: v for k, v in moved.items() if k not in ('steps', 'cancel_conditions')} == \
        {k: v for k, v in intent('SPECIAL9').items() if k not in ('steps', 'cancel_conditions')}
    assert [s['tfs'] for s in intent('SPECIAL9')['steps']] == [['1m'], ['15m'], ['1m'], ['1m'], ['1m']]


def test_special7_moves_its_oz_and_trend():
    plugins = registry.load_plugins({'SYMBOLS': 'TEST'}, ['SPECIAL7'], part='Part2')
    moved = moved_plugins(plugins, {'SPECIAL7': '2m'})['SPECIAL7']
    plan = moved.recipe['strategy_intent']
    assert plan['final']['tfs'] == ['2m'] and plan['steps'][0]['tfs'] == ['20m']
    assert moved.OZ_DECLARATIONS == {('2m', 'NORMAL', 'BREAKER')}
    # The loaded strategy is not changed by its copy.
    assert plugins['SPECIAL7'].recipe['strategy_intent']['final']['tfs'] == ['1m']


@pytest.mark.parametrize('name,tf,message', [
    ('SPECIAL9', '4h', '1분·2분·3분·4분·5분·6분·10분·12분·15분·20분·30분·1시간·2시간·3시간 중에서'),
    ('SPECIAL9', '8h', '중에서 고르세요'),
    ('SPECIAL1', '5m', '시간봉이 여러 개인 전략'),
    ('SPECIAL4', '2m', '표에 없는 시간봉'),
])
def test_a_base_frame_the_strategy_cannot_use_is_refused(name, tf, message):
    policy = copy.deepcopy(strategy_profile(name)['default'])
    policy['tf'] = tf
    with pytest.raises(ValueError, match=message):
        target_policy([name], policy=policy)


@pytest.mark.parametrize('name,tf', [('SPECIAL9', '2m'), ('SPECIAL8', '4h'), ('SPECIAL4', '1m'),
                                     ('SPECIAL1', 'SIGNAL'), ('SPECIAL9', 'SIGNAL')])
def test_a_usable_base_frame_is_kept(name, tf):
    # 수정본146: the policy on a base frame is the recipe's there (SPECIAL9's 15m trend is 20m on 2m).
    policy = recipe_on_base(strategy_profile(name), tf)
    policy['tf'] = tf
    assert target_policy([name], policy=policy)['tf'] == tf


def test_the_shown_frames_follow_the_base_frame():
    profile = strategy_profile('SPECIAL7')
    assert (on_base(profile, '2m')['timeframes'], on_base(profile, '2m')['env_timeframes']) == (['2m'], ['20m'])
    assert on_base(profile, 'SIGNAL') is profile and on_base(profile, '1m') is profile


# ----------------------------------------------------------------- the scenario

def run_scenario(result_mode='VIRTUAL_ENTRY', tf='2m', **extra):
    policy = recipe_on_base(strategy_profile('SPECIAL9'), tf)
    policy['tf'] = tf
    return scenario(symbol='XAUUSD+', start='2025-09-01', end='2025-09-04', strategies=['SPECIAL9'],
                    result_mode=result_mode, virtual_entry=policy, **extra)


def test_only_a_virtual_entry_on_another_base_frame_moves_the_test():
    assert run_scenario()['base_frames'] == {'SPECIAL9': '2m'}
    assert run_scenario(tf='SIGNAL')['base_frames'] == {}
    assert run_scenario(tf='1m')['base_frames'] == {}
    assert run_scenario('ALERT_ONLY')['base_frames'] == {}
    # Never taken from the input as given.
    assert run_scenario(tf='SIGNAL', base_frames={'SPECIAL9': '3m'})['base_frames'] == {}
    assert run_scenario('ALERT_ONLY', base_frames={'SPECIAL9': '3m'})['base_frames'] == {}


# ----------------------------------------------------------------- the replay engine

def test_the_replay_engine_runs_the_moved_copy_and_part1_loads_the_recipe_as_written():
    from engine_harness114 import CONFIG, SYMBOL, quiet
    from command_interpreter import MT5_TIMEFRAMES
    from event_application import create_event_engine
    from event_engine.model import Kind
    from event_backtest.timeframe_selection import required_timeframes as replay_timeframes
    stamp = 1790000040

    def started(plugins=None):
        engine = create_event_engine(dict(CONFIG), symbols=(SYMBOL,), selection=['SPECIAL9'], backtest=True, plugins=plugins)
        engine.ingress.post(Kind.MARKET_BUNDLE, source='t', source_seq=1, source_time=stamp * 1000,
                            payload={'symbol': SYMBOL, 'feeds': {tf: quiet(stamp, tf=tf) for tf in MT5_TIMEFRAMES}})
        engine.run()
        assert not engine.error_log, engine.error_log
        kernel = engine.strategy_state['COMPOSER']['kernels'][SYMBOL]
        return engine, kernel.manager._special_watch_handlers['SPECIAL9']

    s = {'strategies': ['SPECIAL9'], 'triggers': {}, 'special_time_filters': {}, 'base_frames': {'SPECIAL9': '2m'}}
    plugins = engine_plugins(dict(CONFIG), s)
    engine, port = started(plugins)
    assert [s['tfs'] for s in port.meaning['steps']] == [['2m'], ['20m'], ['2m'], ['2m'], ['2m']]
    assert {'2m', '20m'} <= replay_timeframes(engine, [plugins['SPECIAL9'].recipe['strategy_intent']])
    _, plain = started()
    assert [s['tfs'] for s in plain.meaning['steps']] == [['1m'], ['15m'], ['1m'], ['1m'], ['1m']]
    with pytest.raises(ValueError, match='바꿀 수 없습니다'):
        engine_plugins(dict(CONFIG), {**s, 'base_frames': {'SPECIAL9': '4h'}})


# ----------------------------------------------------------------- SPECIAL9 on 2m: strategy conditions

T0 = 1_790_000_000 - 1_790_000_000 % 1200          # a 20-minute boundary
CROSS = 30
# open, high, low, close with HMA50 = 100 (the 2m candles)
BARS = {'plain': (101.8, 102.5, 101.5, 102.0),
        'touch_bull': (101.0, 101.8, 99.5, 101.5),
        'touch_bear': (101.5, 101.8, 99.5, 100.5),    # touches HMA50, bearish, closes above it
        'touch_low': (101.0, 101.2, 99.0, 99.5)}


def moved_harness(target='2m'):
    """SPECIAL9 as Part1 loads it, moved by base_frames, in a quiet recipe harness."""
    plugins = registry.load_plugins({'SYMBOLS': 'TEST'}, ['SPECIAL9'], part='Part2')
    plugin = moved_plugins(plugins, {'SPECIAL9': target})['SPECIAL9']
    h = Harness.__new__(Harness)
    h.manager, h.kernel = manager()
    h.board = h.kernel.board
    plugin.register(h.manager)
    h.port = h.manager._special_watch_handlers['SPECIAL9']
    h.manager.commands.clear()
    return h


def two_minute_bars(count, events):
    rows, hma17 = [], 99.
    for i in range(count):
        kind = events.get(i, 'plain')
        if kind == 'golden': hma17, kind = 101., 'plain'
        if kind == 'dead': hma17, kind = 99., 'plain'
        rows.append((*BARS[kind], hma17, 100.))
    return rows


def two_minute_feed(rows, upto):
    part = rows[:upto + 1]
    values = {n: np.array([r[i] for r in part]) for i, n in enumerate(('open', 'high', 'low', 'close', 'hma_17', 'hma_50'))}
    return feed('2m', T0 + upto * 120, n=len(part), **values)


def twenty_minute_feed(now, rising):
    step = 1. if rising else -1.
    opens = 2400. + step * np.arange(40)
    return feed('20m', now - now % 1200, n=40, open=opens, close=opens, high=opens + 1, low=opens - 1, hma_50=opens - 5 * step)


def completions(events, upto, rising=lambda bar: True):
    """The 2m bars at whose opening poll the moved SPECIAL9 completed. Only 2m and 20m are on the board."""
    h = moved_harness()
    rows = two_minute_bars(upto + 1, {CROSS: 'golden', **events})
    for bar in range(CROSS - 1, upto + 1):
        now = T0 + bar * 120 + 1
        h.publish(now, {'2m': two_minute_feed(rows, bar), '20m': twenty_minute_feed(now, rising(bar))})
    return [int((m['event_time'] - T0) // 120) for m in h.kernel.messages], h


@pytest.mark.parametrize('events,expected', [
    ({31: 'touch_bear'}, [32]),
    ({31: 'touch_bull', 33: 'touch_bear'}, [34]),
    ({31: 'touch_bull', 33: 'touch_low'}, []),
    ({31: 'touch_bear', 33: 'dead', 35: 'golden', 36: 'touch_bear'}, [32, 37]),
])
def test_the_moved_special9_completes_on_2m_candles(events, expected):
    assert completions(events, 38)[0] == expected


def test_it_completes_once_the_2m_touch_candle_has_closed_and_reads_the_20m_trend():
    assert completions({31: 'touch_bear'}, 31)[0] == []
    found, h = completions({31: 'touch_bear'}, 32)
    assert found == [32]
    assert (h.kernel.messages[0]['signal_tf'], h.kernel.messages[0]['env_tf']) == ('2m', '2m')
    assert completions({31: 'touch_bear'}, 38, rising=lambda bar: False)[0] == []


# ----------------------------------------------------------------- SPECIAL9 on 2m: the entry

def view(times, rows, names):
    data = np.ones((len(rows), len(C)))
    for i, row in enumerate(rows):
        for name, value in zip(names, row):
            data[i, C.index(name)] = value
    return SimpleNamespace(time=np.asarray(times, dtype=np.int64), values=data, columns=C)


CANDLE = ('open', 'high', 'low', 'close', 'hma_17', 'hma_50')
HISTORY = [(-120 * k, 101.8, 102.5, 101.5, 102.0, 101., 100.) for k in range(30, 0, -1)]
HAMMER_TOUCH = (101.5, 102.0, 99.8, 101.9)            # touches HMA50, bullish, closes above it
NEXT = (101.9, 102.4, 101.6, 102.2)


def twenty(rising):
    step = 1. if rising else -1.
    opens = 2400. + step * np.arange(40)
    rows = [(o, o + 1, o - 1, o, o - 5 * step) for o in opens]
    return view(1200 * (np.arange(40) - 39), rows, ('open', 'high', 'low', 'close', 'hma_50'))


def minutes(rows, now):
    """The 1m candles opened by `now` inside the 2m ones (stops and targets are always judged on 1m)."""
    out = []
    for start, o, h, l, c, *_ in rows:
        middle = (o + c) / 2
        out += [row for row in ((start, o, h, l, middle), (start + 60, middle, h, l, c)) if row[0] <= now]
    return view([r[0] for r in out], [r[1:] for r in out], ('open', 'high', 'low', 'close'))


def enter(candles, *, rising=True):
    """The moved SPECIAL9 completed at 0; `candles` are the 2m candles from 0 on, the first is the confirmation candle."""
    policy = copy.deepcopy(strategy_profile('SPECIAL9')['default'])
    policy['tf'] = '2m'
    policy['filters'][1]['tf'] = '20m'                   # the panel fills the 15m trend as 20m
    policy = target_policy(['SPECIAL9'], policy=policy)
    alert = dict(signal_id='s', strategy='SPECIAL9', symbol='TEST', tf='2m', direction='LONG', time_ms=0,
                 signal_source='SIGNAL', signal_tf='2m', env_tf='2m', signal_price=100.5)
    assert required_timeframes(policy, alert) == {'1m', '2m', '20m'}
    rows = HISTORY + [(120 * i, *candle, 101., 100.) for i, candle in enumerate(candles)]
    calc = VirtualEntry([alert], 0, policy)
    for i, row in enumerate(rows):
        if row[0] >= 0:
            shown = rows[:i + 1]
            calc.observe(row[0] * 1000, {'2m': view([r[0] for r in shown], [r[1:] for r in shown], CANDLE),
                                         '1m': minutes(shown, row[0]), '20m': twenty(rising)}, end_ms=10 ** 9)
    return calc.trades[0]


def test_the_entry_is_the_next_2m_candle_touching_hma50_and_closing_bullish_above_it():
    trade = enter([HAMMER_TOUCH, NEXT, NEXT])
    assert trade['status'] == 'ENTERED' and trade['entry_time'] == 120000 and trade['entry_price'] == NEXT[0]
    assert enter([(101.9, 102.0, 99.8, 101.5), NEXT, NEXT])['status'] == 'EXPIRED'      # a bearish 2m candle
    assert enter([HAMMER_TOUCH, NEXT, NEXT], rising=False)['status'] == 'PASS_ENV'       # the 20m trend broke
