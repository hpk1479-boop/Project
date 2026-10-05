"""User-facing trading-time wording outside the editor: the AI backtest preview and the save checks."""
import copy
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / 'Part1/program'), str(ROOT / 'Part2'), str(ROOT / 'Part3')]

SESSIONS = ['MAIN_ASIA', 'MAIN_LONDON', 'MAIN_NEWYORK']
TIMES = {'MAIN_ASIA': '0900-1100', 'MAIN_LONDON': '1600-1800', 'MAIN_NEWYORK': '2100-2400'}
NOTHING_CHECKED = {key: {'enabled': False} for key in SESSIONS}
DEVELOPER_WORDS = ('코드 기본값', '알림 꺼짐', '선택한 세션 없음', '기존 시작', '기존 종료', '체크값')


def snapshot(settings):
    return {'today': '2026-10-01', 'options': {
        'mode': 'BAR', 'result_mode': 'ALERT_ONLY', 'specials': ['SPECIAL1', 'SPECIAL2', 'SPECIAL3'],
        'symbols': ['XAUUSD+'], 'spread_points': {'XAUUSD+': 12}, 'default_triggers': {n: 'BREAKER' for n in ('SPECIAL1', 'SPECIAL2', 'SPECIAL3')},
        'session_times': TIMES, 'default_time_filters': {'SPECIAL1': SESSIONS, 'SPECIAL2': 0, 'SPECIAL3': TIMES},
        'special_settings': settings}, 'generated': [], 'jobs': []}


def preview(settings):
    from lab.ai import backtest_commands as commands
    value = {'supported': True, 'action': 'START', 'job_id': None, 'needs_clarification': False,
             'clarification_question': None, 'message_ko': '요청을 해석했습니다.',
             'request': {'target_mode': 'SPECIAL', 'specials': ['SPECIAL1', 'SPECIAL2', 'SPECIAL3'], 'symbol': 'XAUUSD+',
                         'start': '2026-09-01', 'end': '2026-10-01'}}
    current = snapshot(copy.deepcopy(settings))
    command, question = commands._normalize(value, current)
    assert not question
    return {line.split(':', 1)[0]: line for line in commands._preview(command, current).splitlines()
            if line.startswith('SPECIAL')}


def test_preview_names_the_real_sessions_and_times():
    lines = preview({'SPECIAL1': {'enabled': True, 'trigger': None, 'time_filters': None},
                     'SPECIAL2': {'enabled': True, 'trigger': None, 'time_filters': None},
                     'SPECIAL3': {'enabled': True, 'trigger': None, 'time_filters': None}})
    assert lines['SPECIAL1'].endswith('거래시간 아시아 09:00~11:00, 런던 16:00~18:00, 뉴욕 21:00~24:00')
    assert lines['SPECIAL2'].endswith('거래시간 24시간')
    assert lines['SPECIAL3'].endswith('거래시간 아시아 09:00~11:00, 런던 16:00~18:00, 뉴욕 21:00~24:00')


def test_preview_follows_what_the_user_set():
    lines = preview({
        'SPECIAL1': {'enabled': True, 'trigger': None, 'time_filters': NOTHING_CHECKED},
        'SPECIAL2': {'enabled': True, 'trigger': None, 'time_filters': {
            'MAIN_ASIA': {'enabled': True, 'start': '0830', 'end': '1200'}, 'MAIN_LONDON': {'enabled': False}}},
        'SPECIAL3': {'enabled': True, 'trigger': None, 'time_filters': {
            'MAIN_NEWYORK': {'enabled': True}, 'MAIN_ASIA': {'enabled': False}}}})
    assert lines['SPECIAL1'].endswith('거래시간 24시간')
    assert lines['SPECIAL2'].endswith('거래시간 아시아 08:30~12:00')
    assert lines['SPECIAL3'].endswith('거래시간 뉴욕 21:00~24:00')


@pytest.mark.parametrize('times', [None, NOTHING_CHECKED, {'MAIN_ASIA': {'enabled': True}}])
def test_preview_has_no_developer_wording(times):
    lines = preview({name: {'enabled': True, 'trigger': None, 'time_filters': times}
                     for name in ('SPECIAL1', 'SPECIAL2', 'SPECIAL3')})
    text = '\n'.join(lines.values())
    for word in DEVELOPER_WORDS[1:]:
        assert word not in text


@pytest.mark.parametrize('value', ['오전', ['MAIN_ASIA'], {'MAIN_MOON': {'enabled': True}},
                                   {'MAIN_ASIA': {'enabled': 'false'}}, {'MAIN_ASIA': True}])
def test_a_bad_setting_gets_one_plain_message(value):
    from lab.unified_live import _validate_time_filters
    with pytest.raises(ValueError) as caught:
        _validate_time_filters(value)
    assert str(caught.value) == '거래시간 설정을 확인해 주세요.'


@pytest.mark.parametrize('text', ['25:00', '12:60', '9', 'abc'])
def test_a_bad_time_gets_one_plain_message(text):
    from lab.unified_live import _validate_time_filters
    with pytest.raises(ValueError) as caught:
        _validate_time_filters({'MAIN_ASIA': {'enabled': True, 'start': text}})
    assert str(caught.value) == '시각은 00:00~24:00 범위로 입력해 주세요.'


def test_nothing_checked_and_defaults_are_accepted():
    from lab.unified_live import _validate_time_filters
    assert _validate_time_filters(None) is None
    assert _validate_time_filters(NOTHING_CHECKED) == NOTHING_CHECKED
