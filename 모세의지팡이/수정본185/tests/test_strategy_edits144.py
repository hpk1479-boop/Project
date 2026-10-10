"""수정본144: 기준 프레임은 레시피를 다시 불러오는 기준점, 전략 조건 칸은 이 시험만의 레시피 값.

- 기준 프레임을 바꾸면 손으로 고친 값을 모두 버리고, 그 기준으로 처음부터 쓴 레시피 값으로 다시 채운다
  (시간봉·이평 기간·ATR 배수·넣거나 뺀 조건까지). 그 뒤에 고친 칸은 그 값으로 시험한다.
- 전략 조건(레시피의 알림 쪽 조건)도 칸으로 고칠 수 있다. 값만 바뀌고 조건의 종류·개수·순서는 레시피 그대로다.
  고친 값은 이 백테스트의 전략 사본에만 쓰이고 레시피 파일·라이브(Part1)는 그대로다.
- 수정본146: 시간봉은 기준 프레임으로만 정한다. 전략 조건의 시간봉은 고칠 수 없다.
"""
from copy import deepcopy
from pathlib import Path
import json
import socket
import sys
from unittest.mock import patch

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'tests'), str(ROOT), str(ROOT / 'Part3'), str(ROOT / 'Part2'), str(ROOT / 'Part1/program')]
from recipe_harness114 import Harness, feed, manager  # noqa: E402
from strategy_recipe import registry  # noqa: E402
from event_backtest import ui_model  # noqa: E402
from event_backtest.base_frames import edited_plugin, engine_plugins  # noqa: E402
from event_backtest.settings import scenario  # noqa: E402
from event_backtest.virtual_defaults import (check_strategy, recipe_on_base, strategy_on_base,  # noqa: E402
                                             strategy_profile)

SPECIALS = [f'SPECIAL{n}' for n in range(1, 10)]


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def deny(*args, **kwargs):
        raise AssertionError('No network in strategy-edit tests')
    for name in ('connect', 'connect_ex', 'sendto'):
        monkeypatch.setattr(socket.socket, name, deny)


def profile(name):
    return strategy_profile(name)


def edited(name, change, tf='SIGNAL'):
    value = strategy_on_base(profile(name), tf)
    change(value)
    return value


# ----------------------------------------------------------------- the recipe on a base frame

def test_the_profile_carries_the_recipe_strategy_conditions():
    for name in SPECIALS:
        assert profile(name)['strategy'] == registry.preset_entry(name)['recipe']['strategy_intent']


def test_a_base_frame_gives_the_recipe_as_if_written_on_it():
    p = profile('SPECIAL9')
    policy, strategy = recipe_on_base(p, '2m'), strategy_on_base(p, '2m')
    assert policy['tf'] == '2m' and policy['filters'][1]['tf'] == '20m'
    assert all(row.get('tf', 'SIGNAL') == 'SIGNAL' for row in (policy['atr'], policy['stop'], *policy['conditions']))
    assert [(s['kind'], s['tfs']) for s in strategy['steps']] == [
        ('MA_CROSS', ['2m']), ('TREND', ['20m']), ('CANDLE_STATE', ['2m']), ('MA_PRICE_STATE', ['2m']),
        ('MA_PRICE_TOUCH', ['2m'])]
    assert recipe_on_base(p, 'SIGNAL') == p['default'] and strategy_on_base(p, 'SIGNAL') == p['strategy']


# ----------------------------------------------------------------- which strategy values a test may change

@pytest.mark.parametrize('name,tf,change', [
    ('SPECIAL8', 'SIGNAL', lambda m: m['steps'][1].update(slow_period=60)),
    ('SPECIAL8', 'SIGNAL', lambda m: m['steps'][0].update(ma_left='EMA20')),
    ('SPECIAL8', '2m', lambda m: m['cancel_conditions'][0].update(ma_right='HMA60')),
    ('SPECIAL9', 'SIGNAL', lambda m: m['steps'][2].update(side='BULL')),
    ('SPECIAL9', '3m', lambda m: m['steps'][3].update(bar_state='FORMING')),
    ('SPECIAL1', 'SIGNAL', lambda m: m['branches'][0]['steps'][1].update(side='UPPER')),
    ('SPECIAL6', 'SIGNAL', lambda m: m['branches'][2]['steps'][1].update(slow_period=200, lookback=3)),
])
def test_a_shown_value_is_this_tests_own(name, tf, change):
    value = edited(name, change, tf)
    assert check_strategy(profile(name), tf, value) == value


@pytest.mark.parametrize('name,change,message', [
    ('SPECIAL8', lambda m: m['steps'].append(deepcopy(m['steps'][0])), '조건 수'),
    ('SPECIAL8', lambda m: m['cancel_conditions'].clear(), '조건 수'),
    ('SPECIAL8', lambda m: m['steps'][1].update(kind='MA_PRICE_CROSS'), '바꿀 수 없는 값'),
    ('SPECIAL8', lambda m: m['steps'][0].update(direction='SHORT'), '바꿀 수 없는 값'),
    ('SPECIAL8', lambda m: m.update(order_mode='SIMULTANEOUS'), '바꿀 수 없는 값'),
    ('SPECIAL8', lambda m: m['steps'][1].update(negated=True), '항목'),
    ('SPECIAL4', lambda m: m['lifecycle']['expires'].update(seconds=600), '바꿀 수 없는 값'),
    ('SPECIAL8', lambda m: m['steps'][1].update(slow_period=50.5), '형식'),
    ('SPECIAL8', lambda m: m['steps'][1].update(slow_period=0), 'MA 기간'),
    ('SPECIAL8', lambda m: m['steps'][0].update(ma_left='HMA50'), 'MA 두 기간'),
    ('SPECIAL9', lambda m: m['steps'][2].update(side='UP'), '양봉'),
    # 수정본146: frames come only from the base frame, in every condition, list and final.
    ('SPECIAL8', lambda m: m['steps'][0].update(tfs=['3m']), '기준 프레임으로만'),
    ('SPECIAL8', lambda m: [step.update(tfs=['3m']) for step in m['steps']], '기준 프레임으로만'),
    ('SPECIAL9', lambda m: m['steps'][1].update(tfs=['30m']), '기준 프레임으로만'),
    ('SPECIAL8', lambda m: m['cancel_conditions'][0].update(tfs=['2m']), '기준 프레임으로만'),
    ('SPECIAL7', lambda m: m['final'].update(tfs=['3m']), '기준 프레임으로만'),
    ('SPECIAL1', lambda m: m['final'].update(tfs=['1m', '2m']), '기준 프레임으로만'),
    ('SPECIAL2', lambda m: m['final'].update(tfs=['5m']), '기준 프레임으로만'),
    ('SPECIAL1', lambda m: m['branches'][0]['steps'][0].update(tfs=['30m']), '기준 프레임으로만'),
])
def test_the_recipes_structure_and_other_values_cannot_change(name, change, message):
    with pytest.raises(ValueError, match=message):
        check_strategy(profile(name), 'SIGNAL', edited(name, change))


@pytest.mark.parametrize('name', SPECIALS)
def test_the_recipe_on_any_allowed_base_frame_is_not_an_edit(name):
    p = profile(name)
    for tf in ['SIGNAL', *p['bases']]:
        assert check_strategy(p, tf, strategy_on_base(p, tf)) is None


def run(result_mode='VIRTUAL_ENTRY', name='SPECIAL9', tf='2m', strategy=None):
    p = profile(name)
    return scenario(symbol='XAUUSD+', start='2025-09-01', end='2025-09-04', strategies=[name],
                    result_mode=result_mode, virtual_entry=recipe_on_base(p, tf), virtual_strategy=strategy)


def test_the_scenario_keeps_edits_only_for_a_virtual_entry_test():
    change = edited('SPECIAL9', lambda m: m['steps'][0].update(ma_left='HMA20'), '2m')
    assert run(strategy=change)['virtual_strategy'] == change
    assert run(strategy=strategy_on_base(profile('SPECIAL9'), '2m'))['virtual_strategy'] is None
    assert run()['virtual_strategy'] is None
    assert run('ALERT_ONLY', strategy=change)['virtual_strategy'] is None
    with pytest.raises(ValueError):
        run(strategy=edited('SPECIAL9', lambda m: m['steps'].pop(), '2m'))
    with pytest.raises(ValueError, match='기준 프레임으로만'):
        run(strategy=edited('SPECIAL9', lambda m: m['steps'][1].update(tfs=['1h']), '2m'))


# ----------------------------------------------------------------- the replay runs the edited copy

@pytest.mark.parametrize('name', SPECIALS)
def test_an_unedited_recipe_loads_exactly_as_part1_loads_it(name):
    config = {'SYMBOLS': 'TEST'}
    triggers = {name: '무지성 브레이커 올존'} if profile(name)['oz'] else {}
    for times in ({}, {name: 0}, {name: {'MAIN_ASIA': '0800-1200'}}):
        loaded = registry.load_plugins(config, [name], triggers, times, part='Part2')[name]
        mine = edited_plugin(name, loaded, profile(name)['strategy'], config, triggers, times)
        assert mine.recipe['strategy_intent'] == loaded.recipe['strategy_intent']
        assert mine.OZ_DECLARATIONS == loaded.OZ_DECLARATIONS


def test_the_replay_engine_runs_the_edited_conditions_and_the_recipe_stays():
    from engine_harness114 import CONFIG, SYMBOL, quiet
    from command_interpreter import MT5_TIMEFRAMES
    from event_application import create_event_engine
    from event_engine.model import Kind
    from event_backtest.timeframe_selection import required_timeframes
    change = edited('SPECIAL9', lambda m: m['steps'][4].update(slow_period=60))
    s = {'strategies': ['SPECIAL9'], 'triggers': {}, 'special_time_filters': {}, 'base_frames': {},
         'virtual_strategy': change}
    plugins = engine_plugins(dict(CONFIG), s)
    engine = create_event_engine(dict(CONFIG), symbols=(SYMBOL,), selection=['SPECIAL9'], backtest=True, plugins=plugins)
    engine.ingress.post(Kind.MARKET_BUNDLE, source='t', source_seq=1, source_time=1790000040 * 1000,
                        payload={'symbol': SYMBOL, 'feeds': {tf: quiet(1790000040, tf=tf) for tf in MT5_TIMEFRAMES}})
    engine.run()
    assert not engine.error_log, engine.error_log
    port = engine.strategy_state['COMPOSER']['kernels'][SYMBOL].manager._special_watch_handlers['SPECIAL9']
    assert port.meaning['steps'][4]['slow_period'] == 60
    assert [s['tfs'] for s in port.meaning['steps']] == [['1m'], ['15m'], ['1m'], ['1m'], ['1m']]
    assert {'1m', '15m'} <= set(required_timeframes(engine, [plugins['SPECIAL9'].recipe['strategy_intent']]))
    assert registry.preset_entry('SPECIAL9')['recipe']['strategy_intent']['steps'][4]['slow_period'] == 50


T0 = 1_790_000_000 - 1_790_000_000 % 1800
CROSS = 30
BARS = {'plain': (101.8, 102.5, 101.5, 102.0), 'touch_bull': (101.0, 101.8, 99.5, 101.5),
        'touch_bear': (101.5, 101.8, 99.5, 100.5)}


def harness(strategy=None):
    loaded = registry.load_plugins({'SYMBOLS': 'TEST'}, ['SPECIAL9'], part='Part2')['SPECIAL9']
    plugin = loaded if strategy is None else edited_plugin('SPECIAL9', loaded, strategy, {'SYMBOLS': 'TEST'})
    h = Harness.__new__(Harness)
    h.manager, h.kernel = manager()
    h.board = h.kernel.board
    plugin.register(h.manager)
    h.port = h.manager._special_watch_handlers['SPECIAL9']
    h.manager.commands.clear()
    return h


def rising_15m(now):
    opens = 2400. + np.arange(40)
    return feed('15m', now - now % 900, n=40, open=opens, close=opens, high=opens + 1, low=opens - 1, hma_50=opens - 5)


def completions(h, touch):
    """The 1m bars at whose opening poll SPECIAL9 completed: golden cross at 30, the touch candle at 31."""
    rows, hma17 = [], 99.
    for i in range(34):
        if i == CROSS:
            hma17 = 101.
        rows.append((*BARS[touch if i == 31 else 'plain'], hma17, 100.))
    for bar in range(CROSS - 1, 34):
        now = T0 + bar * 60 + 1
        part = rows[:bar + 1]
        minute = feed('1m', T0 + bar * 60, n=len(part), **{n: np.array([r[i] for r in part]) for i, n in
                                                           enumerate(('open', 'high', 'low', 'close', 'hma_17', 'hma_50'))})
        h.publish(now, {'1m': minute, '15m': rising_15m(now)})
    return [int((m['event_time'] - T0) // 60) for m in h.kernel.messages]


def test_an_edited_candle_side_decides_the_strategy():
    bullish = edited('SPECIAL9', lambda m: m['steps'][2].update(side='BULL'))
    assert completions(harness(), 'touch_bull') == [] and completions(harness(), 'touch_bear') == [32]
    assert completions(harness(bullish), 'touch_bull') == [32] and completions(harness(bullish), 'touch_bear') == []


# ----------------------------------------------------------------- the screen keeps the last run's edits

def test_the_last_runs_edits_return_for_their_own_target_and_base_frame(tmp_path):
    path = tmp_path / 'backtest_ui.json'
    policy = recipe_on_base(profile('SPECIAL9'), '2m')
    change = edited('SPECIAL9', lambda m: m['steps'][4].update(slow_period=60), '2m')
    ui_model.save_virtual_entry(policy, 'SPECIAL9', path, strategy=change)
    loaded = ui_model.load(path)
    assert (loaded['virtual_entry'], loaded['virtual_entry_target'], loaded['virtual_entry_strategy']) == (policy, 'SPECIAL9', change)
    # A later run with the recipe's own conditions clears them; edits that no longer fit are not shown.
    ui_model.save_virtual_entry(policy, 'SPECIAL9', path)
    assert ui_model.load(path)['virtual_entry_strategy'] is None
    data = json.loads(path.read_text('utf-8'))
    data['virtual_entry_strategy'] = edited('SPECIAL9', lambda m: m['steps'].pop(), '2m')
    path.write_text(json.dumps(data, ensure_ascii=False), 'utf-8')
    assert ui_model.load(path)['virtual_entry_strategy'] is None


def test_a_web_request_carries_the_edits_into_the_scenario_and_saves_them():
    from lab import unified_backtest
    change = edited('SPECIAL9', lambda m: m['steps'][4].update(slow_period=60), '2m')
    request = {'symbol': 'XAUUSD+', 'start': '2025-09-01', 'end': '2025-09-08', 'mode': 'BAR', 'target_mode': 'SPECIAL',
               'specials': ['SPECIAL9'], 'special_settings': {'SPECIAL9': {'enabled': True, 'trigger': None, 'time_filters': None}},
               'result_mode': 'VIRTUAL_ENTRY', 'virtual_entry': recipe_on_base(profile('SPECIAL9'), '2m'),
               'virtual_strategy': change}
    with patch.object(ui_model, 'save_virtual_entry') as save:
        result = unified_backtest._scenario(deepcopy(request))
    assert result['virtual_strategy'] == change and result['base_frames'] == {'SPECIAL9': '2m'}
    save.assert_called_once_with(result['virtual_entry'], 'SPECIAL9', strategy=change)
    with patch.object(ui_model, 'save_virtual_entry') as save:
        result = unified_backtest._scenario({**deepcopy(request), 'result_mode': 'ALERT_ONLY'})
    assert result['virtual_strategy'] is None and result['base_frames'] == {}
    save.assert_not_called()


# ----------------------------------------------------------------- AI: a base frame refills from the recipe

def test_an_ai_base_change_refills_from_the_recipe_and_drops_earlier_edits():
    from lab.ai import backtest_commands as commands
    p = profile('SPECIAL9')
    custom = recipe_on_base(p, '2m')
    custom['stop'].update(tf='15m', multiplier=2.0)
    custom['conditions'].append({'kind': 'CANDLE_SHAPE', 'shape': 'HAMMER'})
    value = {'supported': True, 'action': 'START', 'job_id': None, 'needs_clarification': False,
             'clarification_question': None, 'message_ko': '확인', 'request': {
                 'target_mode': 'SPECIAL', 'specials': ['SPECIAL9'], 'filename': None, 'watch_text': None,
                 'symbol': 'TEST', 'start': '2026-09-01', 'end': '2026-10-01', 'mode': 'BAR',
                 'result_mode': 'VIRTUAL_ENTRY', 'spread_points': 0, 'build_only': False, 'rebuild': False,
                 'available_only': True, 'base_frame': '3m', 'virtual_entry': custom}}
    snapshot = {'today': '2026-10-07', 'jobs': [], 'generated': [], 'execution_version': 'stable',
                'execution_defaults': {}, 'options': {'specials': ['SPECIAL9'], 'symbols': ['TEST'], 'mode': 'BAR',
                                                      'result_mode': 'VIRTUAL_ENTRY'}}
    command, question = commands._normalize(value, snapshot)
    assert not question and command['request']['virtual_entry'] == recipe_on_base(p, '3m')
