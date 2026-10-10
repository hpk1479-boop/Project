"""Plan-only editing uses the existing null-strategy contract and confirmation path."""
import copy
from unittest.mock import Mock

import pytest

from test_research79 import research_reply, plan, step, sequences, OTHER_JOB
from test_research_modes80 import conversation


# 수정본137·139: a virtual entry runs one strategy and WATCH is alert-only, so those targets state ALERT_ONLY.
@pytest.mark.parametrize('target', [
    {'target_mode': 'SPECIAL', 'specials': ['SPECIAL1', 'SPECIAL2'], 'result_mode': 'ALERT_ONLY'},
    {'target_mode': 'GENERATED', 'specials': [], 'filename': 'Test_SPECIAL001.py'},
    {'target_mode': 'WATCH', 'specials': [], 'watch_text': '1분 RSI 70 상향 돌파', 'result_mode': 'ALERT_ONLY'},
])
def test_plan_only_edit_preserves_target_null_strategy_and_explicit_confirmation(target, monkeypatch):
    research, agent, provider, apply = conversation([
        research_reply('BACKTEST', research_plan=plan(step(**target)))])
    first = research.send('선택한 기존 전략을 백테스트해 주세요')
    assert first['can_confirm'] and first['editor_strategy'] is None
    generate, execute = Mock(), Mock()
    research.backtest.generator, research.backtest.executor = generate, execute
    changed = copy.deepcopy(first['plan'])
    changed['steps'][0]['command']['request'].update(start='2026-08-01', symbol='BTCUSD', mode='EVENT')
    edited = research.edit(first['revision'], None, 'BACKTEST', changed)
    assert edited['ok'] and edited['can_confirm']
    assert edited['editor_strategy'] is None and agent.last_intent is None
    assert edited['plan'] == changed and research.editor()['strategy'] is None
    assert agent.candidate is None and len(provider.exchanges) == 1
    generate.assert_not_called()
    execute.assert_not_called()
    apply.assert_not_called()
    with pytest.raises(ValueError):
        research.confirm(first['revision'])
    start = Mock(return_value={'sequence_id': OTHER_JOB, 'jobs': []})
    monkeypatch.setattr(sequences, 'start', start)
    confirmed = research.confirm(edited['revision'])
    assert confirmed['kind'] == 'BACKTEST'
    command = start.call_args.args[0][0]
    assert command['request']['start'] == '2026-08-01'
    assert command['request']['symbol'] == 'BTCUSD'
    assert command['request']['mode'] == 'EVENT'
    for key, value in target.items():
        assert command['request'][key] == value
    start.assert_called_once()
    generate.assert_not_called()
    execute.assert_not_called()
    apply.assert_not_called()
    with pytest.raises(ValueError):
        research.confirm(edited['revision'])


def test_plan_only_sequence_keeps_each_target_and_options():
    research, agent, provider, apply = conversation([
        research_reply('BACKTEST', research_plan=plan(
            step(specials=['SPECIAL1']), step(specials=['SPECIAL2'], mode='EVENT')))])
    first = research.send('두 전략을 순서대로 백테스트해 주세요')
    changed = copy.deepcopy(first['plan'])
    changed['steps'][1]['command']['request']['spread_points'] = 25
    edited = research.edit(first['revision'], None, 'BACKTEST', changed)
    assert edited['ok'] and edited['plan'] == changed
    assert edited['plan']['steps'][0] == first['plan']['steps'][0]
    assert edited['editor_strategy'] is None and agent.last_intent is None
    assert len(provider.exchanges) == 1
    apply.assert_not_called()


@pytest.mark.parametrize('mutate', [
    lambda value: value.pop('needs_clarification'),
    lambda value: value.pop('clarification_question'),
    lambda value: value.update(invented=True),
    lambda value: value['steps'][0].update(draft=True),
])
def test_plan_only_invalid_structure_never_fills_missing_fields_or_creates_strategy(mutate):
    research, agent, provider, apply = conversation([
        research_reply('BACKTEST', research_plan=plan(step()))])
    first = research.send('기존 전략을 백테스트해 주세요')
    changed = copy.deepcopy(first['plan'])
    mutate(changed)
    edited = research.edit(first['revision'], None, 'BACKTEST', changed)
    assert not edited['ok'] and not edited['can_confirm'] and edited['errors']
    assert edited['plan'] == changed and edited['editor_strategy'] is None
    assert agent.last_intent is None and research.pending is None
    assert len(provider.exchanges) == 1
    apply.assert_not_called()


def test_plan_only_cannot_become_strategy_without_interpretation():
    research, agent, provider, apply = conversation([
        research_reply('BACKTEST', research_plan=plan(step()))])
    first = research.send('기존 전략을 백테스트해 주세요')
    edited = research.edit(first['revision'], None, 'STRATEGY', first['plan'])
    assert not edited['ok'] and not edited['can_confirm'] and not edited['can_apply']
    assert agent.last_intent is None and research.pending is None
    apply.assert_not_called()
