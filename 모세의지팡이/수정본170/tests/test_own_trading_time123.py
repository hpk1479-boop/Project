"""Each SPECIAL carries its own trading time; 주요 거래시간(MAIN_*) is only for command words.

Changing MAIN_* in config.txt must not move any SPECIAL's trading time, while a command such as
"아시아장만 알려줘" keeps following the current MAIN_* value.
"""
import datetime as dt
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'Part1/program'))
from command_interpreter import CommandInterpreter
from domain_clock import event_scope
from special_time_slot import strategy_trading_time, trading_time_allowed
from strategy_recipe.registry import builtin_entries

KST = dt.timezone(dt.timedelta(hours=9))
MAIN = {'MAIN_ASIA': '0800-1200', 'MAIN_LONDON': '1400-1900', 'MAIN_NEWYORK': '2100-2400'}
MOVED = {'MAIN_ASIA': '0000-0300', 'MAIN_LONDON': '0400-0500', 'MAIN_NEWYORK': '0600-0700'}


def allowed(filters, config, hour, minute=0):
    stamp = dt.datetime(2026, 9, 21, hour, minute, tzinfo=KST).timestamp()
    with event_scope(int(stamp * 1000), 'own-time123', {}):
        return trading_time_allowed(filters, config.get)


def own_time(entry):
    return strategy_trading_time({'time_filters': entry.get('time_filters', []),
                                  'final_time_filters': entry.get('final_time_filters', 0)})


@pytest.mark.parametrize('name', sorted(builtin_entries()))
def test_a_special_does_not_follow_main_hours(name):
    filters = own_time(builtin_entries()[name])
    for minute in range(0, 1440, 1):
        hour, rest = divmod(minute, 60)
        assert allowed(filters, MAIN, hour, rest) == allowed(filters, MOVED, hour, rest), (name, hour, rest)


@pytest.mark.parametrize('name', ['SPECIAL1', 'SPECIAL2', 'SPECIAL4', 'SPECIAL5', 'SPECIAL6', 'SPECIAL7'])
@pytest.mark.parametrize('hour,minute,expected', [
    (8, 0, True), (12, 0, True), (12, 1, False), (7, 59, False),
    (14, 0, True), (19, 0, True), (13, 59, False), (19, 1, False),
    (21, 0, True), (23, 59, True), (20, 59, False), (0, 0, False)])
def test_default_specials_keep_the_copied_hours(name, hour, minute, expected):
    filters = own_time(builtin_entries()[name])
    assert allowed(filters, MOVED, hour, minute) is expected


def test_a_command_session_word_follows_the_current_main_hours():
    filters = CommandInterpreter.parse_time_filters('골드 1분 올존 아시아장만 알려줘')
    assert filters == ('MAIN_ASIA',)
    assert allowed(filters, MAIN, 8, 30) is True
    assert allowed(filters, {'MAIN_ASIA': '0900-1200'}, 8, 30) is False


def test_screen_saved_times_do_not_follow_main_hours():
    saved = {'MAIN_ASIA': {'enabled': True, 'start': '0900', 'end': '1100'}, 'MAIN_LONDON': {'enabled': False}}
    for config in (MAIN, MOVED):
        assert allowed(saved, config, 8, 30) is False
        assert allowed(saved, config, 10, 0) is True
