"""수정본139: 레시피의 가상진입 칸, 진입 제한(환경조건·알림 후 N봉), 공통경로.

- 가상진입 값은 각 레시피의 virtual_entry가 단 하나의 원본이다. 칸이 없으면 즉시 진입이다.
- 환경조건은 알림 뒤 기다리는 동안 확인봉마다 본다. 깨지면 그 알림은 패스(PASS_ENV)한다.
  깨지는 그 봉에서는 진입 조건이 맞아도 진입하지 않는다.
- 알림 후 N봉: N번째 확인봉까지 진입할 수 있고, 그 뒤는 패스(EXPIRED)한다.
- 환경 프레임(ENV)은 알림을 만든 레시피 첫 조건의 시간봉이다(알림 기록의 env_tf).
"""
from copy import deepcopy
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'tests'), str(ROOT / 'Part2'), str(ROOT / 'Part1/program')]
from event_backtest.virtual_contract import immediate_virtual_entry, normalize_virtual_entry, required_timeframes  # noqa: E402
from event_backtest.virtual_defaults import strategy_profile, target_policy  # noqa: E402
from event_backtest.virtual_entry import VirtualEntry  # noqa: E402
from test_virtual_common113 import alert, history, oz_policy, policy, replay, view  # noqa: E402


def env(condition='MA_STATE', tf='SIGNAL', bar_state='CLOSED', **fields):
    if condition == 'MA_STATE' and not fields:
        fields = {'family': 'HMA', 'fast': 6, 'slow': 17}
    return {'kind': 'ENVIRONMENT', 'condition': condition, 'tf': tf, 'bar_state': bar_state, **fields}


def recipe_values(policy):
    """Compare recipe execution values; revision148 adds editing origins outside the recipe body."""
    result = deepcopy(policy)
    for row in result['conditions'] + result['filters']:
        row.pop('recipe_index', None)
    return result


# ----------------------------------------------------------------- recipe values

OZ_SPECIALS = [f'SPECIAL{i}' for i in range(1, 8)]


def test_every_shipped_special_carries_its_own_virtual_entry():
    for name in (*OZ_SPECIALS, 'SPECIAL8', 'SPECIAL9'):
        profile = strategy_profile(name)
        raw = __import__('strategy_recipe.registry', fromlist=['preset_entry']).preset_entry(name)['recipe']['virtual_entry']
        assert normalize_virtual_entry(raw) == raw, name
        assert recipe_values(profile['default']) == raw, name
        assert recipe_values(target_policy([name])) == raw


@pytest.mark.parametrize('name', OZ_SPECIALS)
def test_oz_recipes_enter_on_the_hma6_candle_with_b0_while_hma6_17_holds(name):
    policy_ = recipe_values(strategy_profile(name)['default'])
    assert policy_['mode'] == 'CONFIRM' and policy_['stop']['kind'] == 'OZ_B0'
    assert policy_['conditions'] == [{'kind': 'CANDLE_CLOSE'},
                                     {'kind': 'MA_POSITION', 'family': 'HMA', 'period': 6, 'tf': 'SIGNAL'}]
    assert policy_['filters'][0] == env()


@pytest.mark.parametrize('name,extra', [
    ('SPECIAL1', [env('TREND', 'ENV', 'FORMING')]), ('SPECIAL2', []),
    ('SPECIAL3', [env('MA_STATE', 'ENV', family='EMA', fast=50, slow=200)]), ('SPECIAL4', []),
    ('SPECIAL5', [env('MA_STATE', 'ENV', family='EMA', fast=50, slow=200)]),
    ('SPECIAL6', [env('MA_STATE', 'ENV', 'FORMING', family='EMA', fast=50, slow=200),
                  env('MA_SLOPE_STATE', 'ENV', 'FORMING', family='HMA', period=168, lookback=2)]),
    ('SPECIAL7', [env('TREND', '15m', 'FORMING')]),
    ('SPECIAL8', []), ('SPECIAL9', [env('TREND', '15m', 'FORMING'), {'kind': 'MAX_BARS', 'bars': 1}])])
def test_each_recipe_keeps_its_own_environment(name, extra):
    assert recipe_values(strategy_profile(name)['default'])['filters'][1:] == extra


# SPECIAL9's entry (수정본140: the next candle touches HMA50) is checked in test_special9_hammer140.py.
@pytest.mark.parametrize('name', ['SPECIAL8'])
def test_special8_and_9_enter_on_a_bullish_close_above_hma50_while_hma17_50_holds(name):
    policy_ = recipe_values(strategy_profile(name)['default'])
    assert policy_['mode'] == 'CONFIRM' and policy_['stop']['kind'] == 'ATR'
    assert policy_['conditions'] == [{'kind': 'CANDLE_CLOSE'},
                                     {'kind': 'MA_POSITION', 'family': 'HMA', 'period': 50, 'tf': 'SIGNAL'}]
    assert policy_['filters'][0] == env(family='HMA', fast=17, slow=50)


def test_a_recipe_without_a_virtual_entry_enters_immediately(monkeypatch):
    from strategy_recipe import registry
    original = registry.preset_entry
    def without(name):
        entry = original(name)
        entry['recipe'] = {k: v for k, v in entry['recipe'].items() if k != 'virtual_entry'}
        return entry
    monkeypatch.setattr(registry, 'preset_entry', without)
    assert strategy_profile('SPECIAL8')['default'] == immediate_virtual_entry()
    assert target_policy(['SPECIAL8']) == immediate_virtual_entry()


def test_environment_frames_are_the_first_conditions_frames():
    assert strategy_profile('SPECIAL1')['env_timeframes'] == ['1h', '2h', '3h', '4h']
    assert strategy_profile('SPECIAL5')['env_timeframes'] == ['5m', '6m', '10m', '12m', '15m', '20m', '30m', '1h']
    assert strategy_profile('SPECIAL6')['env_timeframes'] == ['15m', '30m']


# ----------------------------------------------------------------- environment limit

@pytest.mark.parametrize('hma17,status', [(8, 'ENTERED'), (10, 'PASS_ENV')])
def test_a_broken_environment_passes_the_alert_even_when_the_candle_confirms(hma17, status):
    rows = [(0, 10, 12, 8, 11, 9, hma17), (60, 10, 11, 9, 10, 9, 8)]
    c = replay(rows, alert(oz=True), oz_policy(env()))
    assert c.trades[0]['status'] == status
    assert (c.trades[0]['entry_time'] == 60000) == (status == 'ENTERED')


def test_the_environment_breaking_on_the_confirmation_candle_blocks_that_entry():
    # Bar 0 bearish (no entry, environment fine); bar 60 bullish above HMA6 but HMA17 crossed above HMA6.
    rows = [(0, 10, 12, 8, 9.5, 9, 8), (60, 10, 12, 8, 11, 9, 10), (120, 10, 11, 9, 10, 9, 8)]
    c = replay(rows, alert(oz=True), oz_policy(env()))
    assert c.trades[0]['status'] == 'PASS_ENV' and c.trades[0]['entry_time'] is None
    summary = c.results()[0][0]
    assert summary['pass_env'] == 1 and summary['passes'] == 1


@pytest.mark.parametrize('hma6,hma17,status', [(9, 10, 'ENTERED'), (9, 8, 'PASS_ENV')])
def test_a_sell_keeps_hma6_under_hma17(hma6, hma17, status):
    rows = [(0, 12, 13, 8, 8.5, hma6, hma17), (60, 9, 10, 8, 9, 9, 8)]
    c = replay(rows, alert(oz=True, direction='SHORT', stop=13), oz_policy(env()))
    assert c.trades[0]['status'] == status


@pytest.mark.parametrize('hma17,status', [(8, 'ENTERED'), (10, 'PASS_ENV')])
def test_an_immediate_entry_checks_the_environment_at_the_alert(hma17, status):
    rows = [(i * 60, 10, 11, 9, 10.5, 9, hma17) for i in range(-30, 1)]
    c = VirtualEntry([alert(time=30000, signal_price=10)], config=policy('IMMEDIATE', filters=[env()]))
    c.observe(30000, {'1m': view(rows)}, end_ms=1000000)
    assert c.trades[0]['status'] == status


@pytest.mark.parametrize('opened,status', [(9.5, 'ENTERED'), (8.5, 'PASS_ENV')])
def test_a_forming_environment_reads_the_forming_candle_at_the_entry_price(opened, status):
    # Price above HMA6 (9) on the forming candle: its open is the quote when the entry is decided.
    rows = [(0, 10, 12, 8, 11, 9, 8), (60, opened, 11, 8, 10, 9, 8)]
    p = oz_policy(env('MA_PRICE_STATE', bar_state='FORMING', family='HMA', period=6))
    assert replay(rows, alert(oz=True), p).trades[0]['status'] == status


@pytest.mark.parametrize('hma17,status', [(8, 'ENTERED'), (10, 'PASS_ENV')])
def test_the_environment_frame_is_each_alerts_own_first_condition_frame(hma17, status):
    p = oz_policy(env(tf='ENV'))
    a = alert(oz=True, env_tf='5m')
    minutes = [(0, 10, 12, 8, 11, 9, 8), (60, 10, 11, 9, 10, 9, 8)]
    fives = [(-300, 10, 11, 9, 10, 9, hma17), (0, 10, 12, 8, 11, 9, 8)]
    c = VirtualEntry([a], config=p)
    assert required_timeframes(p, a) == {'1m', '5m'}
    for stamp in (0, 60):
        c.observe(stamp * 1000, {'1m': view([r for r in minutes if r[0] <= stamp]),
                                 '5m': view([r for r in fives if r[0] <= stamp])}, end_ms=10 ** 9)
    assert c.trades[0]['status'] == status


def test_an_alert_without_its_environment_frame_is_not_guessed():
    with pytest.raises(ValueError, match='환경 프레임'):
        replay([(0, 10, 12, 8, 11, 9, 8), (60, 10, 11, 9, 10, 9, 8)], alert(oz=True), oz_policy(env(tf='ENV')))


# ----------------------------------------------------------------- N bars after the alert

@pytest.mark.parametrize('bars,status', [(2, 'EXPIRED'), (3, 'ENTERED')])
def test_n_bars_counts_confirmation_candles_after_the_alert(bars, status):
    # Two bearish candles, then a bullish one: the third candle after the alert confirms.
    rows = [(0, 10, 11, 9, 9.5), (60, 10, 11, 9, 9.5), (120, 10, 11, 9, 10.5), (180, 10, 11, 9, 10)]
    c = replay(rows, alert(), policy(filters=[{'kind': 'MAX_BARS', 'bars': bars}]))
    assert c.trades[0]['status'] == status
    if status == 'ENTERED':
        assert c.trades[0]['entry_time'] == 180000
    else:
        assert c.results()[0][0]['expired'] == 1


def test_without_a_limit_the_wait_never_expires_on_its_own():
    rows = [(i * 60, 10, 11, 9, 9.5) for i in range(30)]
    assert replay(rows, alert(), policy()).trades[0]['status'] == 'WAITING'


@pytest.mark.parametrize('bad,error', [
    ({'mode': 'IMMEDIATE', 'filters': [{'kind': 'MAX_BARS', 'bars': 3}]}, '조건 진입'),
    ({'mode': 'CONFIRM', 'conditions': [{'kind': 'CANDLE_CLOSE'}],
      'filters': [{'kind': 'MAX_BARS', 'bars': 3}, {'kind': 'MAX_BARS', 'bars': 4}]}, '하나만'),
    ({'mode': 'CONFIRM', 'conditions': [{'kind': 'MA_POSITION', 'tf': 'ENV'}]}, '환경 프레임'),
    ({'filters': [env('PRICE')]}, '환경조건'),
    ({'filters': [env(family='HMA', fast=17, slow=17)]}, '같습니다'),
    ({'filters': [env('TREND', family='HMA')]}, '지원하지 않는 필드'),
    ({'filters': [env(bar_state='NOW')]}, '봉 기준'),
    ({'filters': [{'kind': 'MAX_BARS', 'bars': 0}], 'mode': 'CONFIRM', 'conditions': [{'kind': 'CANDLE_CLOSE'}]}, '알림 후 봉 수'),
])
def test_invalid_limits_are_rejected(bad, error):
    with pytest.raises(ValueError, match=error):
        normalize_virtual_entry(bad)


# 수정본162: the scan for traces of the removed OZ-only wait and coded defaults was deleted.

def test_analytics_and_screens_name_the_new_passes():
    from event_backtest.analytics import RESULTS
    assert {'PASS_ENV', 'EXPIRED'} <= RESULTS
    dashboard = (ROOT / 'Part3/web/backtest_dashboard.js').read_text(encoding='utf-8')
    assert "PASS_ENV: '환경조건이 깨져 제외'" in dashboard and "EXPIRED: '알림 후 N봉 지나 제외'" in dashboard
