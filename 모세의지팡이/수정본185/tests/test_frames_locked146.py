"""수정본146: 시간봉은 기준 프레임 하나로만 정한다.

사용자 결정(2026-10-07): "기준프레임바꾸면 내부 피라미터 프레임은 수정안되게 고정하면 다 해결되는 문제 아니야? …
그냥 내가 2분 바꾸면 레시피도 2분 기준으로 바뀌고 그건 수정 못하게 하면 되지" / "직접 고르는게 왜 필요하냐고 …
이미 맵핑으로 설정을 다 해놨는데" / "레거시 남지 않도록 깨끗하게 정리해줘"

- 기준 프레임을 고르면 전략 조건과 진입 칸의 모든 시간봉이 맵핑표대로 채워지고, 어느 칸도 자기 시간봉을 고르지 않는다.
- 진입 칸의 시간봉은 SIGNAL(전략 시간봉)·ENV(환경 프레임)·그 기준의 레시피 시간봉뿐이다. 새로 넣는 조건·제한은 SIGNAL이다.
- 화면·AI·저장값·시나리오 어느 길로도 다른 시간봉은 들어가지 않는다. 시간봉이 아닌 값은 그대로 고칠 수 있다.
"""
from copy import deepcopy
from pathlib import Path
import socket
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'Part3'), str(ROOT / 'Part2'), str(ROOT / 'Part1/program')]
from event_backtest import ui_model  # noqa: E402
from event_backtest.settings import scenario  # noqa: E402
from event_backtest.virtual_defaults import recipe_on_base, strategy_profile, target_policy  # noqa: E402

SPECIALS = [f'SPECIAL{n}' for n in range(1, 10)]
LOCKED = '기준 프레임으로만'


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def deny(*args, **kwargs):
        raise AssertionError('No network in frame tests')
    for name in ('connect', 'connect_ex', 'sendto'):
        monkeypatch.setattr(socket.socket, name, deny)


@pytest.mark.parametrize('name', SPECIALS)
def test_the_recipe_on_every_base_frame_is_its_own_frames(name):
    profile = strategy_profile(name)
    for tf in ['SIGNAL', *profile['bases']]:
        policy = recipe_on_base(profile, tf)
        assert target_policy([name], policy=policy) == policy


@pytest.mark.parametrize('base', ['SIGNAL', '2m'])
@pytest.mark.parametrize('place,frame', [('condition', '3m'), ('filter', '5m'), ('atr', '3m'), ('stop', '15m'),
                                         ('stop', '1m'), ('stop', '2m')])
def test_a_frame_chosen_in_an_entry_field_is_refused(base, place, frame):
    # SPECIAL8's fields show its frame (SIGNAL); any frame written into one, even the one shown, is refused.
    policy = recipe_on_base(strategy_profile('SPECIAL8'), base)
    row = {'condition': policy['conditions'][1], 'filter': policy['filters'][0],
           'atr': policy['atr'], 'stop': policy['stop']}[place]
    row['tf'] = frame
    with pytest.raises(ValueError, match=LOCKED):
        target_policy(['SPECIAL8'], policy=policy)


def test_a_recipe_frame_moves_with_the_base_frame_and_nothing_else_is_accepted():
    profile = strategy_profile('SPECIAL9')
    for base, top in (('SIGNAL', '15m'), ('2m', '20m'), ('3m', '30m')):
        policy = recipe_on_base(profile, base)
        assert policy['filters'][1]['tf'] == top and target_policy(['SPECIAL9'], policy=policy) == policy
        for other in {'15m', '20m', '30m'} - {top}:
            wrong = deepcopy(policy)
            wrong['filters'][1]['tf'] = other
            with pytest.raises(ValueError, match=LOCKED):
                target_policy(['SPECIAL9'], policy=wrong)


def test_new_conditions_and_limits_follow_the_base_frame():
    policy = recipe_on_base(strategy_profile('SPECIAL8'), '2m')
    policy['conditions'].append({'kind': 'MA_TOUCH_CANDLE', 'family': 'HMA', 'period': 6, 'tf': 'SIGNAL'})
    policy['filters'].append({'kind': 'ENVIRONMENT', 'condition': 'TREND', 'tf': 'SIGNAL', 'bar_state': 'CLOSED'})
    assert target_policy(['SPECIAL8'], policy=policy) == policy
    # The original limit keeps ENV; a newly added limit uses SIGNAL even if its kind already exists.
    special1 = deepcopy(strategy_profile('SPECIAL1')['default'])
    assert special1['filters'][1]['condition'] == 'TREND' and special1['filters'][1]['tf'] == 'ENV'
    special1['filters'].append({'kind': 'ENVIRONMENT', 'condition': 'TREND', 'tf': 'SIGNAL', 'bar_state': 'CLOSED'})
    assert target_policy(['SPECIAL1'], policy=special1) == special1
    special1['filters'][-1]['tf'] = 'ENV'
    with pytest.raises(ValueError, match=LOCKED):
        target_policy(['SPECIAL1'], policy=special1)


def test_values_other_than_frames_are_still_this_tests_own():
    policy = recipe_on_base(strategy_profile('SPECIAL9'), '2m')
    policy['stop'].update(multiplier=2.5, period=21)
    policy['conditions'] = [row for row in policy['conditions'] if row['kind'] != 'MA_POSITION']
    policy['filters'][0].update(fast=10, slow=30)
    assert target_policy(['SPECIAL9'], policy=policy) == policy


def test_a_scenario_refuses_a_chosen_frame():
    policy = recipe_on_base(strategy_profile('SPECIAL8'), '2m')
    policy['stop']['tf'] = '5m'
    with pytest.raises(ValueError, match=LOCKED):
        scenario(symbol='XAUUSD+', start='2025-09-01', end='2025-09-04', strategies=['SPECIAL8'],
                 result_mode='VIRTUAL_ENTRY', virtual_entry=policy)


def test_a_saved_policy_with_a_chosen_frame_is_not_restored(tmp_path):
    path = tmp_path / 'backtest_ui.json'
    policy = recipe_on_base(strategy_profile('SPECIAL8'), '2m')
    ui_model.save_virtual_entry(policy, 'SPECIAL8', path)
    assert ui_model.load(path)['virtual_entry'] == policy
    chosen = deepcopy(policy)
    chosen['stop']['tf'] = '5m'                       # a field frame saved by an earlier revision
    ui_model.save_virtual_entry(chosen, 'SPECIAL8', path)
    loaded = ui_model.load(path)
    assert (loaded['virtual_entry'], loaded['virtual_entry_target']) == (None, None)


def test_an_ai_command_cannot_choose_a_frame_but_can_change_the_base_frame():
    from lab.ai import backtest_commands as commands
    profile = strategy_profile('SPECIAL9')
    chosen = recipe_on_base(profile, '2m')
    chosen['stop']['tf'] = '5m'

    def command(**request):
        return {'supported': True, 'action': 'START', 'job_id': None, 'needs_clarification': False,
                'clarification_question': None, 'message_ko': '확인', 'request': {
                    'target_mode': 'SPECIAL', 'specials': ['SPECIAL9'], 'filename': None, 'watch_text': None,
                    'symbol': 'TEST', 'start': '2026-09-01', 'end': '2026-10-01', 'mode': 'BAR',
                    'result_mode': 'VIRTUAL_ENTRY', 'spread_points': 0, 'build_only': False, 'rebuild': False,
                    'available_only': True, **request}}
    snapshot = {'today': '2026-10-07', 'jobs': [], 'generated': [], 'execution_version': 'stable',
                'execution_defaults': {}, 'options': {'specials': ['SPECIAL9'], 'symbols': ['TEST'], 'mode': 'BAR',
                                                      'result_mode': 'VIRTUAL_ENTRY'}}
    with pytest.raises(ValueError, match=LOCKED):
        commands._normalize(command(base_frame=None, virtual_entry=chosen), snapshot)
    done, question = commands._normalize(command(base_frame='3m', virtual_entry=chosen), snapshot)
    assert not question and done['request']['virtual_entry'] == recipe_on_base(profile, '3m')
