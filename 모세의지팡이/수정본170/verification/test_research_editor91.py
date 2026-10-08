"""Direct slots share canonical validation, revisions and confirmation boundaries."""
import copy
import json
from unittest.mock import Mock

import pytest

from test_research79 import fixture, research_reply, plan, step, snapshot, Plan, sequences
from test_research_modes80 import conversation
from lab import server
from lab.ai.schema import output_schema


def started(*more):
    research, agent, provider, apply = conversation([
        research_reply('STRATEGY', strategy=fixture().original()), *more])
    first = research.send('15분 추세와 3분 원비, 1분 브레이커 올존')
    return research, agent, provider, apply, first


def draft_plan(**changes):
    return plan(step(draft=True, target_mode='GENERATED', specials=[], filename=None, **changes))


def paths(response):
    return [row['path'] for row in response['errors']]


def test_contract_is_existing_schema_and_safe_selection_only():
    research, agent, provider, apply, first = started()
    contract = research.editor()
    assert contract['schema'] == output_schema()
    assert contract['revision'] == first['revision']
    assert contract['strategy'] == first['result']
    assert contract['options']['backtest_symbols'] == snapshot()['options']['symbols']
    assert 'jobs' not in contract and 'execution_defaults' not in contract and 'generated' not in contract
    assert len(provider.exchanges) == 1
    apply.assert_not_called()


def test_edit_changes_only_requested_tf_and_keeps_every_other_meaning_until_confirmation():
    research, agent, provider, apply, first = started()
    changed = copy.deepcopy(first['result'])
    changed['interpretation']['steps'][1]['tfs'] = ['15m']
    result = research.edit(first['revision'], changed, 'STRATEGY')
    assert result['ok'] and result['can_apply']
    assert result['result'] == changed == agent.last_intent
    assert result['revision'] != first['revision']
    assert len(provider.exchanges) == 1
    apply.assert_not_called()
    with pytest.raises(ValueError):
        research.confirm(first['revision'])
    confirmed = research.confirm(result['revision'])
    assert confirmed['recipe']['strategy_intent'] == changed['interpretation']
    apply.assert_called_once()
    with pytest.raises(ValueError):
        research.confirm(result['revision'])


@pytest.mark.parametrize('mutate,path', [
    (lambda value: value['interpretation']['steps'][1].update(tfs=['BAD']),
        ['strategy', 'interpretation', 'steps', 1, 'tfs', 0]),
    (lambda value: value['interpretation'].update(within_sec=0),
        ['strategy', 'interpretation', 'within_sec']),
    (lambda value: value['interpretation']['final'].update(trigger_mode='UNKNOWN'),
        ['strategy', 'interpretation', 'final', 'trigger_mode']),
    (lambda value: value['interpretation']['steps'][0].update(invented=True),
        ['strategy', 'interpretation', 'steps', 0, 'invented']),
])
def test_invalid_cell_is_retained_revokes_old_confirmation_and_never_updates_valid_intent(mutate, path):
    research, agent, provider, apply, first = started()
    bad = copy.deepcopy(first['result'])
    mutate(bad)
    result = research.edit(first['revision'], bad, 'STRATEGY')
    assert not result['ok'] and not result['can_apply'] and not result['can_confirm']
    assert path in paths(result)
    assert agent.last_intent == first['result']
    assert agent.candidate is None and research.pending is None
    assert research.editor()['strategy'] == bad
    for revision in (first['revision'], result['revision']):
        with pytest.raises(ValueError):
            research.confirm(revision)
    apply.assert_not_called()
    assert len(provider.exchanges) == 1


def test_semantic_error_marks_only_bad_field_among_two_edits():
    research, agent, provider, apply, first = started()
    changed = copy.deepcopy(first['result'])
    changed['interpretation']['steps'][1]['tfs'] = ['15m']
    changed['interpretation']['within_sec'] = 600  # not allowed for SIMULTANEOUS
    response = research.edit(first['revision'], changed, 'STRATEGY')
    assert not response['ok']
    assert paths(response) == [['strategy', 'interpretation', 'within_sec']]
    assert not any('tfs' in path for path in paths(response))


def test_missing_object_reference_is_rejected_by_common_validator():
    research, agent, provider, apply, first = started()
    changed = copy.deepcopy(first['result'])
    changed['interpretation']['order_mode'] = 'SEQUENTIAL'
    changed['interpretation']['steps'][1]['ref'] = 'absent_object'
    response = research.edit(first['revision'], changed, 'STRATEGY')
    assert not response['ok']
    assert paths(response) == [['strategy', 'interpretation', 'steps', 1, 'ref']]
    assert agent.candidate is None


def test_valid_direct_edit_is_next_natural_language_current_strategy():
    research, agent, provider, apply, first = started(
        research_reply('STRATEGY', strategy=fixture().changed()))
    changed = copy.deepcopy(first['result'])
    changed['interpretation']['steps'][1]['tfs'] = ['15m']
    saved = research.edit(first['revision'], changed, 'STRATEGY')
    result = research.send('이 조건으로 설명을 수정해')
    context = json.loads(provider.seen[-1]['content'])['context']
    assert context['current_strategy'] == changed
    assert result['can_apply'] and result['is_revision']
    with pytest.raises(ValueError):
        research.confirm(saved['revision'])


def test_invalid_direct_edit_is_next_ai_draft_instead_of_stale_approved_canonical():
    research, agent, provider, apply, first = started(
        research_reply('STRATEGY', strategy=fixture().changed()))
    changed = copy.deepcopy(first['result'])
    changed['interpretation']['steps'][1]['tfs'] = ['BAD']
    saved = research.edit(first['revision'], changed, 'STRATEGY')
    assert not saved['ok'] and agent.last_intent == first['result']
    result = research.send('원비 시간봉을 15분으로 고쳐')
    context = json.loads(provider.seen[-1]['content'])['context']
    assert context['current_strategy'] == changed and '검증하지 못' in context['question']
    assert result['can_apply'] and agent.last_intent == fixture().changed()


def test_invalid_then_fixed_cell_gets_new_valid_revision_and_clears_only_editor_errors():
    research, agent, provider, apply, first = started()
    changed = copy.deepcopy(first['result'])
    changed['interpretation']['steps'][1]['tfs'] = ['BAD']
    invalid = research.edit(first['revision'], changed, 'STRATEGY')
    changed['interpretation']['steps'][1]['tfs'] = ['15m']
    fixed = research.edit(invalid['revision'], changed, 'STRATEGY')
    assert fixed['ok'] and fixed['errors'] == [] and not research.editor_invalid
    assert agent.last_intent == changed
    assert research.editor()['errors'] == []


def test_old_editor_revision_cannot_replace_latest_edit_or_approval():
    research, agent, provider, apply, first = started()
    changed = copy.deepcopy(first['result'])
    changed['interpretation']['steps'][1]['tfs'] = ['15m']
    current = research.edit(first['revision'], changed, 'STRATEGY')
    stale = research.edit(first['revision'], first['result'], 'STRATEGY')
    assert not stale['ok'] and paths(stale) == [['revision']]
    assert stale['revision'] == current['revision']
    assert agent.last_intent == changed
    research.confirm(current['revision'])
    apply.assert_called_once()


def test_switch_to_backtest_prepares_without_ai_file_generation_or_execution(monkeypatch):
    research, agent, provider, apply, first = started()
    generate, execute, start = Mock(return_value={'filename': 'Test_SPECIAL002.py'}), Mock(), Mock(return_value={'sequence_id': 'c' * 32})
    research.backtest = Plan(provider, lambda: agent, context_loader=snapshot, generator=generate,
        executor=execute, previewer=lambda _: {'filename': 'Test_SPECIAL002.py'})
    monkeypatch.setattr(sequences, 'start', start)
    result = research.edit(first['revision'], first['result'], 'BACKTEST', draft_plan())
    assert result['ok'] and result['kind'] == 'BACKTEST' and result['can_confirm']
    # The plan shows and keeps the effective result mode and entry policy (null = the recipe's own).
    assert result['plan'] == draft_plan(result_mode='VIRTUAL_ENTRY', virtual_entry=None)
    generate.assert_not_called(); execute.assert_not_called(); start.assert_not_called(); apply.assert_not_called()
    assert len(provider.exchanges) == 1
    research.confirm(result['revision'])
    generate.assert_called_once(); start.assert_called_once(); apply.assert_not_called()


@pytest.mark.parametrize('changes,key', [({'start': None}, 'start'), ({'end': 'bad'}, 'end'),
    ({'end': '2026-08-01'}, 'end'), ({'symbol': 'NOT_LISTED'}, 'symbol')])
def test_backtest_invalid_period_and_symbol_are_field_errors(changes, key):
    research, agent, provider, apply, first = started()
    response = research.edit(first['revision'], first['result'], 'BACKTEST', draft_plan(**changes))
    assert not response['ok'] and not response['can_confirm']
    assert ['plan', 'steps', 0, 'command', 'request', key] in paths(response)
    with pytest.raises(ValueError):
        research.confirm(response['revision'])
    apply.assert_not_called()


def test_switch_back_to_generation_detaches_backtest_even_for_next_natural_correction():
    research, agent, provider, apply, first = started(
        research_reply('STRATEGY', strategy=fixture().changed()))
    research.backtest = Plan(provider, lambda: agent, context_loader=snapshot,
        executor=Mock(), generator=Mock(), previewer=lambda _: {'filename': 'Test_SPECIAL002.py'})
    backtest = research.edit(first['revision'], first['result'], 'BACKTEST', draft_plan())
    strategy = research.edit(backtest['revision'], first['result'], 'STRATEGY')
    assert strategy['ok'] and research.backtest.last_plan is None
    followup = research.send('원비 시간봉만 15분')
    assert followup['kind'] == 'STRATEGY' and followup['can_apply']
    assert json.loads(provider.seen[-1]['content'])['context']['previous_plan'] is None
    research.backtest.executor.assert_not_called(); research.backtest.generator.assert_not_called()


def test_invalid_second_backtest_never_generates_or_starts_first():
    research, agent, provider, apply, first = started()
    generate, execute = Mock(), Mock()
    research.backtest = Plan(provider, lambda: agent, context_loader=snapshot, generator=generate,
        executor=execute, previewer=lambda _: {'filename': 'Test_SPECIAL002.py'})
    invalid_plan = draft_plan()
    invalid_plan['steps'].append(step(draft=True, target_mode='GENERATED', specials=['SPECIAL1'], filename=None))
    response = research.edit(first['revision'], first['result'], 'BACKTEST', invalid_plan)
    assert not response['ok'] and research.pending is None
    generate.assert_not_called(); execute.assert_not_called(); apply.assert_not_called()


def test_server_editor_routes_deliver_structured_validation_errors(monkeypatch):
    research, agent, provider, apply, first = started()
    monkeypatch.setattr(server, 'RESEARCH_SESSIONS', {'editor-test': research})
    contract = server.ai_post('/api/ai/editor', {'research': True, 'session': 'editor-test'})
    changed = copy.deepcopy(first['result'])
    changed['interpretation']['steps'][1]['tfs'] = ['bad']
    response = server.ai_post('/api/ai/edit', {'research': True, 'session': 'editor-test',
        'revision': contract['revision'], 'strategy': changed, 'operation': 'STRATEGY'})
    assert not response['ok'] and response['errors'][0]['path'][0] == 'strategy'
    assert json.loads(json.dumps(response)) == response
    apply.assert_not_called()


def test_editor_can_be_queried_before_the_first_ai_request():
    research, agent, provider, apply = conversation([])
    contract = research.editor()
    assert contract['revision'].endswith(':0') and contract['strategy'] is None
    assert not contract['can_apply'] and not contract['can_confirm']
    assert provider.exchanges == []


def test_public_level_choices_preserve_valid_composite_selector():
    from lab.ai.research_editor import contract
    value = fixture().original()
    value['interpretation']['steps'].append({'kind': 'LIQUIDITY_LEVEL', 'tfs': ['1m'],
        'level': 'PDH,PREV_4H_HIGH', 'relation': 'TOUCH'})
    choices = contract(snapshot(), value)['options']['level_choices']
    assert 'PDH' in choices['LIQUIDITY_LEVEL']
    assert 'PDH,PREV_4H_HIGH' in choices['LIQUIDITY_LEVEL']
    assert 'DAY_OPEN' in choices['PRICE_LEVEL']


def test_existing_validator_provenance_is_preserved_but_cannot_be_edited():
    value = fixture().original()
    value['interpretation']['symbol_source'] = 'USER'
    research, agent, provider, apply = conversation([research_reply('STRATEGY', strategy=value)])
    first = research.send('사용자가 종목을 명시한 전략')
    changed = copy.deepcopy(first['result'])
    changed['interpretation']['steps'][1]['tfs'] = ['15m']
    accepted = research.edit(first['revision'], changed, 'STRATEGY')
    assert accepted['ok'] and accepted['result']['interpretation']['symbol_source'] == 'USER'
    changed['interpretation']['symbol_source'] = 'CURRENT_DEFAULT'
    rejected = research.edit(accepted['revision'], changed, 'STRATEGY')
    assert not rejected['ok']
    assert ['strategy', 'interpretation', 'symbol_source'] in paths(rejected)


def test_unsupported_ai_retry_after_invalid_edit_never_revives_stale_strategy_context():
    from lab.ai.intent import unsupported
    research, agent, provider, apply, first = started(
        research_reply('STRATEGY', strategy=unsupported()),
        research_reply('STRATEGY', strategy=fixture().changed()))
    bad = copy.deepcopy(first['result'])
    bad['interpretation']['steps'][1]['tfs'] = ['BAD']
    research.edit(first['revision'], bad, 'STRATEGY')
    response = research.send('다시 고쳐')
    assert not response['can_apply'] and research.editor_invalid
    research.send('원비 시간봉을 15분으로')
    assert json.loads(provider.seen[-1]['content'])['context']['current_strategy'] == bad


def test_ai_condition_retry_keeps_manual_invalid_plan_instead_of_old_valid_dates():
    research, agent, provider, apply, first = started(
        research_reply('STRATEGY', strategy=fixture().changed()))
    research.backtest = Plan(provider, lambda: agent, context_loader=snapshot,
        executor=Mock(), generator=Mock(), previewer=lambda _: {'filename': 'Test_SPECIAL002.py'})
    accepted = research.edit(first['revision'], first['result'], 'BACKTEST', draft_plan())
    invalid_plan = draft_plan(start='not-a-date')
    rejected = research.edit(accepted['revision'], first['result'], 'BACKTEST', invalid_plan)
    assert not rejected['ok']
    with pytest.raises(ValueError, match='날짜'):
        research.send('원비만 15분으로')
    context = json.loads(provider.seen[-1]['content'])['context']
    assert context['previous_plan'] == invalid_plan
    assert research.pending is None and research.editor_plan == invalid_plan
    research.backtest.executor.assert_not_called(); research.backtest.generator.assert_not_called()


def test_draft_backtest_symbol_must_also_belong_to_the_confirmed_strategy():
    research, agent, provider, apply, first = started()
    response = research.edit(first['revision'], first['result'], 'BACKTEST', draft_plan(symbol='BTCUSD'))
    assert not response['ok']
    assert paths(response) == [['plan', 'steps', 0, 'command', 'request', 'symbol']]
    assert '감시 종목' in response['errors'][0]['message']
    assert research.pending is None
    apply.assert_not_called()


def test_user_explicitly_resolves_ai_clarification_before_edited_table_can_be_applied():
    proposed = fixture().original()
    proposed['needs_clarification'] = True
    proposed['clarification_question'] = '원비 시간봉을 확인해 주세요.'
    research, agent, provider, apply = conversation([research_reply('STRATEGY', strategy=proposed)])
    first = research.send('15분 추세에서 원비 조건으로 전략을 만들어')
    assert not first['can_apply'] and research.pending is None
    unresolved = copy.deepcopy(first['result'])
    unresolved['interpretation']['steps'][1]['tfs'] = ['15m']
    rejected = research.edit(first['revision'], unresolved, 'STRATEGY')
    assert not rejected['ok'] and not rejected['can_apply']
    apply.assert_not_called()
    resolved = copy.deepcopy(unresolved)
    resolved['needs_clarification'] = False
    resolved['clarification_question'] = None
    accepted = research.edit(rejected['revision'], resolved, 'STRATEGY')
    assert accepted['ok'] and accepted['can_apply']
    assert agent.last_intent == resolved and len(provider.exchanges) == 1
    apply.assert_not_called()
    research.confirm(accepted['revision'])
    apply.assert_called_once()
