"""Trading time: nothing checked means 24 hours; checked sessions limit alerts to their own times.

The shipped SPECIAL7 recipe runs through the real IntentPort with a real TimePolicy, at KST clock times.
"""
import datetime as dt
from types import SimpleNamespace as NS

import numpy as np
import pytest

from recipe_harness114 import Harness, SYMBOL, feed
from domain_clock import event_scope
from event_composer_domain import TimePolicy
from special_settings_model import time_summary
from special_time_slot import session_filters_allowed, special_time_errors
from strategy_recipe.registry import default_settings, load_plugins

KST = dt.timezone(dt.timedelta(hours=9))
CONFIG = {'MAIN_ASIA': '0900-1100', 'MAIN_LONDON': '1600-1800', 'MAIN_NEWYORK': '2100-2400'}
NOTHING_CHECKED = {key: {'enabled': False} for key in CONFIG}
ASIA_ONLY = {'MAIN_ASIA': {'enabled': True}, 'MAIN_LONDON': {'enabled': False}, 'MAIN_NEWYORK': {'enabled': False}}
API = NS(config_get=lambda key, default='': CONFIG.get(key, default), time_allowed=lambda filters: True)


def stamp(hour, minute=0):
    return float(int(dt.datetime(2026, 9, 21, hour, minute, tzinfo=KST).timestamp()))


def at(hour, minute=0):
    return event_scope(int(stamp(hour, minute) * 1000), 'time-test', {})


# --- the time rule itself ----------------------------------------------------------------------------

@pytest.mark.parametrize('filters', [NOTHING_CHECKED, {}, 0, None, []])
@pytest.mark.parametrize('hour', [3, 10, 13, 17, 22])
def test_nothing_checked_has_no_time_limit(filters, hour):
    with at(hour):
        assert session_filters_allowed(API, filters) is True


@pytest.mark.parametrize('hour,expected', [(8, False), (9, True), (10, True), (11, True), (12, False),
                                           (17, False), (22, False)])
def test_only_the_checked_session_allows(hour, expected):
    with at(hour):
        assert session_filters_allowed(API, ASIA_ONLY) is expected


def test_checked_sessions_use_their_own_edited_times():
    edited = {'MAIN_ASIA': {'enabled': True, 'start': '12:00', 'end': '13:00'}, 'MAIN_LONDON': {'enabled': False}}
    for hour, minute, expected in ((11, 59, False), (12, 0, True), (13, 0, True), (13, 1, False), (10, 0, False)):
        with at(hour, minute):
            assert session_filters_allowed(API, edited) is expected


def test_midnight_crossing_and_range_defaults_still_work():
    with at(1):
        assert session_filters_allowed(API, {'MAIN_NEWYORK': {'enabled': True, 'start': '2300', 'end': '0200'}})
        assert not session_filters_allowed(API, {'MAIN_ASIA': '0900-1100'})
    with at(10):
        assert session_filters_allowed(API, {'MAIN_ASIA': '0900-1100'})


@pytest.mark.parametrize('filters', [NOTHING_CHECKED, {}, 0, None, [], ASIA_ONLY, ['MAIN_ASIA'],
                                     ['MAIN_LONDON', 'MAIN_NEWYORK'], ['1200-1300'], ['ALL'], {'MAIN_ASIA': '0900-1100'}])
@pytest.mark.parametrize('hour', [1, 10, 12, 17, 22])
def test_composer_and_recipe_use_the_one_rule(filters, hour):
    # Recipe strategies and KIM private strategies/chains give the same answer for the same value.
    with at(hour, 30):
        assert TimePolicy(CONFIG).allows(filters) is session_filters_allowed(API, filters)


def test_a_time_range_in_a_list_is_honoured():
    # A recipe list may hold "HHMM-HHMM"; it used to be read as a session name and always blocked.
    with at(12, 30):
        assert session_filters_allowed(API, ['1200-1300']) is True
        assert TimePolicy(CONFIG).allows(['1200-1300']) is True
    with at(13, 1):
        assert session_filters_allowed(API, ['1200-1300']) is False


def test_screen_setting_errors_are_reported_per_strategy():
    errors = special_time_errors({'S_OK': ASIA_ONLY, 'S_ALL_OFF': NOTHING_CHECKED, 'S_BAD': {'MAIN_ASIA': {'start': '25:00'}},
                                  'S_TYPE': ['MAIN_ASIA']})
    assert set(errors) == {'S_BAD', 'S_TYPE'}


# --- a user setting replaces the recipe's own time limits --------------------------------------------

def meaning_of(times):
    plugin = load_plugins({'SYMBOLS': SYMBOL}, selected=['SPECIAL7'], times=times)['SPECIAL7']
    return plugin.recipe['strategy_intent']


def test_without_a_user_setting_the_recipe_default_applies_everywhere():
    meaning = meaning_of(None)
    assert meaning['time_filters'] == []
    assert meaning['final_time_filters'] == default_settings('SPECIAL7')[1]


@pytest.mark.parametrize('override', [NOTHING_CHECKED, ASIA_ONLY, {}])
def test_a_user_setting_replaces_both_time_limits(override):
    meaning = meaning_of({'SPECIAL7': override})
    assert meaning['time_filters'] == [], 'the recipe\'s own session limit must not keep blocking or cancelling'
    assert meaning['final_time_filters'] == override


def test_one_strategys_setting_does_not_touch_another():
    plugins = load_plugins({'SYMBOLS': SYMBOL}, selected=['SPECIAL1', 'SPECIAL7'], times={'SPECIAL7': ASIA_ONLY})
    assert plugins['SPECIAL1'].recipe['strategy_intent']['final_time_filters'] == default_settings('SPECIAL1')[1]
    assert plugins['SPECIAL7'].recipe['strategy_intent']['final_time_filters'] == ASIA_ONLY


def test_live_environment_and_backtest_mapping_give_the_same_time_limits(monkeypatch):
    """LIVE reads the host's OZ_SPECIAL_TIME_FILTERS; backtest passes the same settings directly."""
    from event_application import load_strategy_inputs
    from special_settings_model import time_env
    names = ['SPECIAL1', 'SPECIAL2', 'SPECIAL7']
    settings = {'SPECIAL1': {'time_filters': NOTHING_CHECKED}, 'SPECIAL2': {'time_filters': None},
                'SPECIAL7': {'time_filters': ASIA_ONLY}}
    monkeypatch.setenv('OZ_SPECIAL_TIME_FILTERS', time_env(settings))
    live = load_strategy_inputs(None, names, None, {'SYMBOLS': SYMBOL})[2]
    backtest = load_strategy_inputs({}, names, {n: i['time_filters'] for n, i in settings.items()
                                                if i['time_filters'] is not None}, {'SYMBOLS': SYMBOL})[2]
    for name in names:
        a, b = live[name].recipe['strategy_intent'], backtest[name].recipe['strategy_intent']
        assert (a['time_filters'], a['final_time_filters']) == (b['time_filters'], b['final_time_filters']), name
    assert live['SPECIAL1'].recipe['strategy_intent']['final_time_filters'] == NOTHING_CHECKED
    assert live['SPECIAL2'].recipe['strategy_intent']['final_time_filters'] == default_settings('SPECIAL2')[1]
    for hour in (4, 10, 13, 17, 22):
        with at(hour):
            for name in names:
                limits = [live[name].recipe['strategy_intent']['final_time_filters'],
                          backtest[name].recipe['strategy_intent']['final_time_filters']]
                assert session_filters_allowed(API, limits[0]) == session_filters_allowed(API, limits[1])


# --- the real strategy, at real clock times ----------------------------------------------------------

def rising(now):
    n = 40
    ramp = 2400. + np.arange(n) * 1.5
    return feed('15m', int(now) - int(now) % 900, n, open=ramp, hma_50=ramp)


def alerts(hour, times):
    """Messages SPECIAL7 sends for a rising 15m trend at KST `hour`; None when the setup never started."""
    now = stamp(hour)
    h = Harness(meaning_of(times), 'SPECIAL7', dict(CONFIG))
    h.manager._time_policy = TimePolicy(CONFIG)
    with event_scope(int(now * 1000), 'time-test', {}):
        h.publish(now, {'15m': rising(now)})
        machine = next(m for m in h.machines if m.direction == 'LONG')
        if not machine.active:
            return None
        h.final_oz(machine, now)
    return h.messages


@pytest.mark.parametrize('hour,expected', [(10, True), (17, True), (22, True), (13, False), (4, False)])
def test_recipe_default_alerts_only_in_its_sessions(hour, expected):
    assert bool(alerts(hour, None)) is expected


@pytest.mark.parametrize('hour', [4, 10, 13, 17, 22])
@pytest.mark.parametrize('override', [NOTHING_CHECKED, {}])
def test_nothing_checked_alerts_around_the_clock(hour, override):
    assert alerts(hour, {'SPECIAL7': override})


@pytest.mark.parametrize('hour,expected', [(10, True), (13, False), (17, False), (22, False), (4, False)])
def test_only_asia_checked_alerts_only_in_asia(hour, expected):
    assert bool(alerts(hour, {'SPECIAL7': ASIA_ONLY})) is expected


def test_asia_only_does_not_cancel_a_watch_that_started_in_london_time():
    # The recipe's own session limit used to cancel the setup outside its sessions even though
    # the user only restricted the final alert. Now the setup runs; only the final alert is held back.
    now = stamp(13)
    h = Harness(meaning_of({'SPECIAL7': ASIA_ONLY}), 'SPECIAL7', dict(CONFIG))
    h.manager._time_policy = TimePolicy(CONFIG)
    with event_scope(int(now * 1000), 'time-test', {}):
        h.publish(now, {'15m': rising(now)})
        machine = next(m for m in h.machines if m.direction == 'LONG')
        assert machine.active
        h.final_oz(machine, now)
        assert h.messages == []


# --- wording shown to the user -----------------------------------------------------------------------

def test_summary_names_actual_sessions_and_times_or_24_hours():
    times = {'MAIN_ASIA': '0900-1100', 'MAIN_LONDON': '1600-1800', 'MAIN_NEWYORK': '2100-2400'}
    sessions = ['MAIN_ASIA', 'MAIN_LONDON', 'MAIN_NEWYORK']
    assert time_summary(None, sessions, times) == '아시아 09:00~11:00, 런던 16:00~18:00, 뉴욕 21:00~24:00'
    assert time_summary(None, 0, times) == '24시간'
    assert time_summary(NOTHING_CHECKED, sessions, times) == '24시간'
    assert time_summary(ASIA_ONLY, sessions, times) == '아시아 09:00~11:00'
    assert time_summary({'MAIN_LONDON': {'enabled': True, 'start': '0830', 'end': '12:00'}}, 0, times) == '런던 08:30~12:00'
    ranges = {'MAIN_ASIA': '0900-1100', 'MAIN_NEWYORK': '2100-2400'}
    assert time_summary(None, ranges, {'MAIN_ASIA': '0100-0200'}) == '아시아 09:00~11:00, 뉴욕 21:00~24:00'


@pytest.mark.parametrize('text', ['코드 기본값', '알림 꺼짐', '선택한 세션 없음', '최종 알림이 없습니다', '기존 거래시간을 복원'])
def test_summary_has_no_developer_wording(text):
    for value in (None, NOTHING_CHECKED, ASIA_ONLY):
        assert text not in time_summary(value, 0, CONFIG)
