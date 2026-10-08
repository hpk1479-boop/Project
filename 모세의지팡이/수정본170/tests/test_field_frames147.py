"""수정본147: 칸마다 정해진 시간봉만 받는다. AI 전략연구 요약도 실제 시간봉으로 보여 준다.

검토 지적(2026-10-07, 146): SPECIAL9를 기준 2분으로 두면 손절은 2분, 추세는 20분이어야 하는데, 손절에 20분을 넣어도
검사를 통과하고 실제 손절도 20분 ATR로 계산됐다. 146 검사가 "레시피 어딘가에 쓰인 시간봉"이면 어느 칸에나 받았다.
사용자: "다음버전에서 다 고쳐".

- 칸(손절, ATR, 진입 조건 종류, 진입 제한 종류)마다 그 기준에서 레시피가 정한 시간봉만 받는다.
  새로 넣은 조건·제한은 전략 시간봉(SIGNAL)이다.
- AI 전략연구의 "핵심 요약"은 SIGNAL·ENV를 레시피(또는 쓰고 있는 초안)의 실제 시간봉으로 보여 준다.
"""
from copy import deepcopy
from pathlib import Path
import socket
import sys
from types import SimpleNamespace

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'Part3'), str(ROOT / 'Part2'), str(ROOT / 'Part1/program')]
from event_backtest import ui_model  # noqa: E402
from event_backtest.virtual_contract import immediate_virtual_entry  # noqa: E402
from event_backtest.virtual_defaults import recipe_on_base, strategy_frames, strategy_profile, target_policy  # noqa: E402
from event_backtest.virtual_entry import VirtualEntry  # noqa: E402
from staff_schema import PIPE_VALUE_COLUMNS as C  # noqa: E402

LOCKED = '기준 프레임으로만'


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def deny(*args, **kwargs):
        raise AssertionError('No network in field-frame tests')
    for name in ('connect', 'connect_ex', 'sendto'):
        monkeypatch.setattr(socket.socket, name, deny)


def special9_on_2m():
    return recipe_on_base(strategy_profile('SPECIAL9'), '2m')


FIELDS = {'stop': lambda p: p['stop'], 'atr': lambda p: p['atr'], 'touch': lambda p: p['conditions'][1],
          'position': lambda p: p['conditions'][2], 'ma_state': lambda p: p['filters'][0]}


# ----------------------------------------------------------------- each field takes its own frame

def test_special9_on_2m_gives_each_field_its_frame():
    policy = special9_on_2m()
    assert [policy['stop']['tf'], policy['atr']['tf'], policy['filters'][0]['tf'], policy['filters'][1]['tf']] == \
        ['SIGNAL', 'SIGNAL', 'SIGNAL', '20m']
    assert target_policy(['SPECIAL9'], policy=policy) == policy


@pytest.mark.parametrize('field', FIELDS)
def test_the_trend_frame_in_another_field_is_refused(field):
    # The reported case: 20m is SPECIAL9's trend frame on 2m, never its stop's (nor any other field's).
    policy = special9_on_2m()
    FIELDS[field](policy)['tf'] = '20m'
    with pytest.raises(ValueError, match=LOCKED):
        target_policy(['SPECIAL9'], policy=policy)


def test_readding_the_trend_limit_uses_the_strategy_frame_as_a_new_limit_does():
    policy = special9_on_2m()
    removed = policy['filters'].pop(1)
    assert removed['condition'] == 'TREND' and removed['tf'] == '20m'
    policy['filters'].append({'kind': 'ENVIRONMENT', 'condition': 'TREND', 'tf': 'SIGNAL', 'bar_state': 'CLOSED'})
    assert 'recipe_index' not in policy['filters'][-1]   # a newly added limit has no recipe-slot origin
    assert target_policy(['SPECIAL9'], policy=policy) == policy
    policy = special9_on_2m()
    policy['filters'].append({'kind': 'ENVIRONMENT', 'condition': 'TREND', 'tf': 'SIGNAL', 'bar_state': 'CLOSED'})
    assert target_policy(['SPECIAL9'], policy=policy) == policy


@pytest.mark.parametrize('name', ['SPECIAL1', 'SPECIAL8', 'SPECIAL9'])
def test_an_environment_frame_only_where_the_recipe_has_it(name):
    # Each recipe's HMA-order limit is on its own frame; SPECIAL1's trend limit alone is on the environment frame.
    policy = deepcopy(strategy_profile(name)['default'])
    assert policy['filters'][0]['condition'] == 'MA_STATE' and policy['filters'][0]['tf'] == 'SIGNAL'
    policy['filters'][0]['tf'] = 'ENV'
    with pytest.raises(ValueError, match=LOCKED):
        target_policy([name], policy=policy)
    special1 = deepcopy(strategy_profile('SPECIAL1')['default'])
    assert special1['filters'][1]['tf'] == 'ENV' and target_policy(['SPECIAL1'], policy=special1) == special1


def stop_risk(policy):
    """A 2m alert of SPECIAL9 on a market whose 2m ATR is 2 and 20m ATR is 10."""
    def bars(seconds, half):
        values = np.ones((40, len(C)))
        for i in range(40):
            for column, value in dict(open=100., high=100. + half, low=100. - half, close=100.).items():
                values[i, C.index(column)] = value
        return SimpleNamespace(time=np.asarray([-seconds * k for k in range(40, 0, -1)], dtype=np.int64),
                               values=values, columns=C)
    alert = dict(signal_id='s', strategy='SPECIAL9', symbol='X', tf='2m', direction='LONG', time_ms=0,
                 signal_source='SIGNAL', signal_tf='2m', env_tf='2m', signal_price=100.)
    calculator = VirtualEntry([alert], 0, policy)
    calculator.observe(0, {'1m': bars(60, .5), '2m': bars(120, 1.), '20m': bars(1200, 5.)}, end_ms=10 ** 9)
    return calculator.trades[0]['risk']


def test_the_stop_runs_on_the_base_frame():
    policy = special9_on_2m()
    policy.update(mode='IMMEDIATE', conditions=[], filters=[])
    assert stop_risk(target_policy(['SPECIAL9'], policy=policy)) == pytest.approx(2.)
    policy['stop']['tf'] = '20m'
    with pytest.raises(ValueError, match=LOCKED):
        target_policy(['SPECIAL9'], policy=policy)


def test_a_saved_stop_on_the_trend_frame_is_not_restored(tmp_path):
    path = tmp_path / 'backtest_ui.json'
    saved = special9_on_2m()
    saved['stop']['tf'] = '20m'
    ui_model.save_virtual_entry(saved, 'SPECIAL9', path)
    loaded = ui_model.load(path)
    assert (loaded['virtual_entry'], loaded['virtual_entry_target']) == (None, None)


def test_an_ai_command_with_a_stop_on_the_trend_frame_is_refused():
    from lab.ai import backtest_commands as commands
    policy = special9_on_2m()
    policy['stop']['tf'] = '20m'
    value = {'supported': True, 'action': 'START', 'job_id': None, 'needs_clarification': False,
             'clarification_question': None, 'message_ko': '확인', 'request': {
                 'target_mode': 'SPECIAL', 'specials': ['SPECIAL9'], 'filename': None, 'watch_text': None,
                 'symbol': 'TEST', 'start': '2026-09-01', 'end': '2026-10-01', 'mode': 'BAR',
                 'result_mode': 'VIRTUAL_ENTRY', 'spread_points': 0, 'build_only': False, 'rebuild': False,
                 'available_only': True, 'base_frame': None, 'virtual_entry': policy}}
    snapshot = {'today': '2026-10-07', 'jobs': [], 'generated': [], 'execution_version': 'stable',
                'execution_defaults': {}, 'options': {'specials': ['SPECIAL9'], 'symbols': ['TEST'], 'mode': 'BAR',
                                                      'result_mode': 'VIRTUAL_ENTRY'}}
    with pytest.raises(ValueError, match=LOCKED):
        commands._normalize(value, snapshot)


# ----------------------------------------------------------------- the AI research summary names real frames

def test_the_editor_contract_carries_each_recipes_frames_on_each_base_frame():
    from lab.ai import research_editor
    profiles = {name: strategy_profile(name) for name in ('SPECIAL1', 'SPECIAL4', 'SPECIAL7', 'SPECIAL9')}
    options = research_editor.contract({'options': {'virtual_profiles': profiles}})['options']
    frames = options['virtual_frames']
    assert frames['SPECIAL9']['SIGNAL'] == {'timeframes': ['1m'], 'env_timeframes': ['1m']}
    assert frames['SPECIAL9']['2m'] == {'timeframes': ['2m'], 'env_timeframes': ['2m']}
    assert frames['SPECIAL7']['2m'] == {'timeframes': ['2m'], 'env_timeframes': ['20m']}
    assert set(frames['SPECIAL4']) == {'SIGNAL', '1m'}
    assert frames['SPECIAL1']['SIGNAL']['env_timeframes'] == ['1h', '2h', '3h', '4h'] and set(frames['SPECIAL1']) == {'SIGNAL'}
    assert options['virtual_draft_frames'] is None


def test_the_draft_being_written_gives_its_own_frames():
    from lab.ai import research_editor
    meaning = strategy_profile('SPECIAL9')['strategy']
    options = research_editor.contract({}, {'interpretation': meaning})['options']
    assert options['virtual_draft_frames'] == strategy_frames(meaning) == {'timeframes': ['1m'], 'env_timeframes': ['1m']}
    assert research_editor.contract({}, {'interpretation': {'steps': 'unfinished'}})['options']['virtual_draft_frames'] is None


def test_the_command_preview_never_writes_alert_frame_wording():
    from lab.ai import backtest_commands as commands
    text = '\n'.join(commands._virtual_preview(immediate_virtual_entry(), None))
    assert '손절: 전략 시간봉 ATR14 × 1배' in text and '알림 시간봉' not in text
    text = '\n'.join(commands._virtual_preview(special9_on_2m(), strategy_profile('SPECIAL9')))
    assert '진입 제한: 20분 추세 유지' in text and '손절: 2분 ATR14 × 1배' in text
