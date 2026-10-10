"""Complete research payloads keep canonical meaning without a second interpretation."""
import copy
import json
from unittest.mock import Mock

import pytest

from test_research79 import (
    Plan, fixture, plan, research_reply, step, snapshot, sequences, storage,
)
from test_research_modes80 import chat, conversation
from lab.ai.intent import validate_intent
from lab.ai import backtest_commands
from lab.ai.research import DiscussionError
from lab.ai.research_interpreter import response_schema
from lab.ai.schema import output_schema


def block_reinterpretation(monkeypatch, agent, backtest=None):
    strategy_send = Mock(side_effect=AssertionError('canonical을 다시 해석하면 안 됩니다.'))
    monkeypatch.setattr(agent, 'send', strategy_send)
    if backtest is not None:
        monkeypatch.setattr(backtest, 'send', Mock(side_effect=AssertionError('계획을 다시 해석하면 안 됩니다.')))
    return strategy_send


def test_strategy_payload_is_accepted_once_and_preserves_every_canonical_field(monkeypatch):
    canonical = fixture().original()
    research, agent, provider, apply = conversation([research_reply('STRATEGY', strategy=canonical)])
    send = block_reinterpretation(monkeypatch, agent)
    result = research.send('15분 확정봉 상승추세에서 3분 하단 원비, 1분 브레이커 올존')
    assert len(provider.exchanges) == 1
    assert result['can_apply'] and agent.last_intent == canonical
    assert result['result'] == canonical
    from jsonschema import Draft202012Validator
    wire_schema = provider.response_schemas[0]
    Draft202012Validator.check_schema(wire_schema)
    Draft202012Validator(wire_schema).validate(canonical)
    assert [variant['properties']['kind']['const'] for variant in provider.response_schemas[0]['anyOf']
            if 'kind' in variant['properties']] == ['BACKTEST']
    send.assert_not_called(); apply.assert_not_called()
    research.confirm(result['revision'])
    apply.assert_called_once()
    assert len(provider.exchanges) == 1


def test_strategy_mode_rejects_chat_after_discussion_and_keeps_original_confirmation():
    canonical = fixture().original()
    research, agent, provider, apply = conversation([
        research_reply('STRATEGY', strategy=canonical), chat(), chat('설명을 더 이어갑니다.')])
    first = research.send('전략 조건을 해석해')
    research.send('조건 차이를 설명해', mode='chat')
    before = (copy.deepcopy(agent.last_intent), copy.deepcopy(agent.messages), agent.revision, research.pending)
    with pytest.raises(ValueError):
        research.send('이제 전략생성에서 조건을 확정해', mode='strategy')
    assert (agent.last_intent, agent.messages, agent.revision, research.pending) == before
    assert [variant['properties']['kind']['const'] for variant in provider.response_schemas[-1]['anyOf']
            if 'kind' in variant['properties']] == ['BACKTEST']
    assert len(provider.exchanges) == 3
    research.confirm(first['revision'])
    apply.assert_called_once()


def test_new_strategy_and_backtest_share_one_response_and_wait_for_confirmation(monkeypatch):
    canonical = fixture().original()
    payload = plan(step(draft=True, target_mode='GENERATED', specials=[], filename=None))
    research, agent, provider, apply = conversation([
        research_reply('BACKTEST', strategy=canonical, research_plan=payload)])
    execute, generate = Mock(), Mock(return_value={'filename': 'Test_SPECIAL002.py'})
    start = Mock(return_value={'sequence_id': 'c' * 32, 'jobs': [], 'phase': 'run'})
    monkeypatch.setattr(sequences, 'start', start)
    research.backtest = Plan(provider, lambda: agent, context_loader=snapshot, executor=execute,
                             generator=generate, previewer=lambda _: {'filename': 'Test_SPECIAL002.py'})
    send = block_reinterpretation(monkeypatch, agent, research.backtest)
    result = research.send('15분 추세, 3분 하단 원비와 1분 올존을 골드 9월 백테스트')
    assert result['kind'] == 'BACKTEST' and result['can_confirm']
    assert result['strategy']['result'] == canonical and agent.last_intent == canonical
    assert research.backtest.last_plan['strategy_text'] is None
    assert len(provider.exchanges) == 1
    send.assert_not_called(); generate.assert_not_called(); execute.assert_not_called(); start.assert_not_called()
    apply.assert_not_called()
    research.confirm(result['revision'])
    generate.assert_called_once(); start.assert_called_once()
    assert start.call_args.args[0][0]['request']['filename'] == 'Test_SPECIAL002.py'
    assert len(provider.exchanges) == 1


def test_strategy_then_period_corrections_keep_tf_other_conditions_and_plan_order(monkeypatch):
    f = fixture()
    initial = plan(
        step(draft=True, target_mode='GENERATED', specials=[], filename=None),
        step(draft=True, target_mode='GENERATED', specials=[], filename=None,
             start='2026-08-01', end='2026-09-01'))
    period_changed = copy.deepcopy(initial)
    period_changed['steps'][0]['command']['request']['start'] = '2026-07-01'
    research, agent, provider, apply = conversation([
        research_reply('BACKTEST', strategy=f.original(), research_plan=initial),
        research_reply('STRATEGY', strategy=f.changed()),
        research_reply('BACKTEST', research_plan=period_changed)])
    execute, generate = Mock(), Mock()
    research.backtest = Plan(provider, lambda: agent, context_loader=snapshot, executor=execute,
                             generator=generate, previewer=lambda _: {'filename': 'Test_SPECIAL002.py'})
    block_reinterpretation(monkeypatch, agent, research.backtest)
    first = research.send('새 전략 골드 9월, 끝나면 같은 전략 8월 백테스트')
    requests = copy.deepcopy([row['command']['request'] for row in research.backtest.pending['steps']])
    second = research.send('원비 시간봉만 15분으로 수정해')
    assert second['kind'] == 'BACKTEST' and second['can_confirm']
    assert agent.last_intent == f.changed()
    assert [row['command']['request'] for row in research.backtest.pending['steps']] == requests
    unchanged = copy.deepcopy(f.changed())
    unchanged['interpretation']['steps'][1]['tfs'] = ['3m']
    assert unchanged == f.original()
    with pytest.raises(ValueError):
        research.confirm(first['revision'])
    third = research.send('첫 백테스트 시작일만 7월 1일로')
    assert third['can_confirm'] and agent.last_intent == f.changed()
    revised = copy.deepcopy([row['command']['request'] for row in research.backtest.pending['steps']])
    assert revised[0]['start'] == '2026-07-01'
    original_start = requests[0]['start']
    revised[0]['start'] = original_start
    assert revised == requests
    assert json.loads(provider.seen[-1]['content'])['context']['current_strategy'] == f.changed()
    assert research.backtest.last_plan['strategy_text'] is None
    assert len(provider.exchanges) == 3
    with pytest.raises(ValueError):
        research.confirm(second['revision'])
    apply.assert_not_called(); generate.assert_not_called(); execute.assert_not_called()


def test_invalid_second_step_cannot_generate_or_partially_start_first(monkeypatch):
    research, agent, provider, apply = conversation([
        research_reply('BACKTEST', strategy=fixture().original(), research_plan=plan(
            step(draft=True, target_mode='GENERATED', specials=[], filename=None),
            step(specials=['not-existing'])))])
    execute, generate, start = Mock(), Mock(), Mock()
    monkeypatch.setattr(sequences, 'start', start)
    research.backtest = Plan(provider, lambda: agent, context_loader=snapshot, executor=execute,
                             generator=generate, previewer=lambda _: {'filename': 'Test_SPECIAL002.py'})
    block_reinterpretation(monkeypatch, agent, research.backtest)
    with pytest.raises(ValueError):
        research.send('첫 백테스트 끝나면 두 번째 백테스트')
    assert research.pending is None and len(provider.exchanges) == 1
    with pytest.raises(ValueError):
        research.confirm('unconfirmed')
    apply.assert_not_called(); generate.assert_not_called(); execute.assert_not_called(); start.assert_not_called()


@pytest.mark.parametrize('missing', ['symbol', 'start', 'end'])
def test_incomplete_backtest_payload_asks_without_generation_or_execution(monkeypatch, missing):
    research, agent, provider, apply = conversation([
        research_reply('BACKTEST', research_plan=plan(step(**{missing: None})))])
    execute, generate, start = Mock(), Mock(), Mock()
    monkeypatch.setattr(sequences, 'start', start)
    research.backtest = Plan(provider, lambda: agent, context_loader=snapshot, executor=execute, generator=generate)
    block_reinterpretation(monkeypatch, agent, research.backtest)
    result = research.send('스페셜1 백테스트')
    assert result['needs_clarification'] and not result['can_confirm']
    assert len(provider.exchanges) == 1
    with pytest.raises(ValueError):
        research.confirm(result['revision'])
    apply.assert_not_called(); generate.assert_not_called(); execute.assert_not_called(); start.assert_not_called()


def test_read_only_lookup_can_add_a_round_without_reinterpreting_strategy(monkeypatch):
    canonical = fixture().original()
    research, agent, provider, apply = conversation([
        {'content': '', 'tool_calls': [{'id': 'lookup1', 'name': 'list_specials', 'arguments': {}}]},
        research_reply('STRATEGY', strategy=canonical)])
    lookup = Mock(return_value=[{'name': 'SPECIAL1'}])
    monkeypatch.setattr(agent.workspace, 'list_specials', lookup)
    send = block_reinterpretation(monkeypatch, agent)
    result = research.send('현재 계약을 확인하고 전략 조건을 해석해')
    assert result['can_apply'] and agent.last_intent == canonical
    assert len(provider.exchanges) == 2
    # Public preset names also seed the common contract. The requested tool
    # executes once in the loop, independently of contract preparation reads.
    lookup.assert_called_with(); send.assert_not_called(); apply.assert_not_called()
    assert result['actions'] == [{'tool': 'list_specials', 'ok': True, 'error': None}]
    assert any(row['role'] == 'tool' and row['tool_call_id'] == 'lookup1' for row in provider.exchanges[-1])


def test_history_budget_keeps_recent_complete_discussion_in_next_strategy_request():
    proposal = '15분 추세와 3분 원비를 살펴보자. ' * 250
    canonical = fixture().original()
    research, agent, provider, apply = conversation(
        [chat(text=proposal)] * 5 + [research_reply('STRATEGY', strategy=canonical)])
    for number in range(5):
        research.send('조건 설명 ' + str(number), mode='chat')
    assert sum(len(row['content']) for row in research.history) <= 16000
    assert len(research.history) <= 8 and research.history[-1]['content'] == proposal
    result = research.send('그 제안으로 전략을 해석해', mode='strategy')
    sent = json.loads(provider.seen[-1]['content'])
    assert sum(len(row['content']) for row in sent['discussion']) <= 16000
    assert sent['discussion'][-1]['content'] == proposal
    assert result['can_apply'] and agent.last_intent == canonical and len(provider.exchanges) == 6
    apply.assert_not_called()


def preserved_draft(monkeypatch, tmp_path, invalid_reply):
    canonical = fixture().original()
    research, agent, provider, apply = conversation([
        research_reply('BACKTEST', strategy=canonical,
            research_plan=plan(step(draft=True, target_mode='GENERATED', specials=[], filename=None))),
        invalid_reply])
    root = tmp_path / 'Part3'
    generated = root / 'generated'
    generated.mkdir(parents=True)
    (generated / 'existing.py').write_text('existing = True\n', encoding='utf-8')
    monkeypatch.setattr(storage, 'ROOT', root)
    execute, generate, start = Mock(), Mock(), Mock()
    monkeypatch.setattr(sequences, 'start', start)
    research.backtest = Plan(provider, lambda: agent, context_loader=snapshot, executor=execute,
                             generator=generate, previewer=lambda _: {'filename': 'Test_SPECIAL002.py'})
    block_reinterpretation(monkeypatch, agent, research.backtest)
    initial = research.send('현재 전략 골드 9월 백테스트')
    assert initial['can_confirm']
    return research, agent, provider, apply, execute, generate, start, root


def draft_state(research, agent):
    return copy.deepcopy((agent.last_intent, agent.candidate, agent.messages, agent.revision,
        agent.original_text, agent.pending_clarification, agent.first_interpretation,
        research.pending, research.number, research.revision, research.kind, research.question, research.history,
        research.backtest.last_plan, research.backtest.pending, research.backtest.revision))


def project_files(root):
    return {path.relative_to(root).as_posix(): path.read_bytes() for path in root.rglob('*') if path.is_file()}


def test_chat_output_over_8000_characters_keeps_pending_canonical_plan_and_files(monkeypatch, tmp_path):
    research, agent, provider, apply, execute, generate, start, root = preserved_draft(
        monkeypatch, tmp_path, chat(text='설' * 8001))
    provider.script.insert(0, chat(text='설' * 8000))
    valid = research.send('긴 조건 설명', mode='chat')
    assert valid['kind'] == 'CHAT' and len(valid['message_ko']) == 8000
    before, files = draft_state(research, agent), project_files(root)
    with pytest.raises(DiscussionError) as failure:
        research.send('다른 조건 설명', mode='chat')
    assert failure.value.pending_preserved is True
    assert draft_state(research, agent) == before
    assert project_files(root) == files and len(provider.exchanges) == 3
    apply.assert_not_called(); generate.assert_not_called(); execute.assert_not_called(); start.assert_not_called()


@pytest.mark.parametrize('field,malformed', [
    ('steps', None),
    ('steps', '계획'),
    ('steps', {'first': '작업'}),
    ('request', '백테스트 요청'),
    ('request', ['백테스트 요청']),
], ids=['steps-null', 'steps-string', 'steps-object', 'request-string', 'request-list'])
def test_malformed_backtest_structure_keeps_pending_canonical_plan_and_files(monkeypatch, tmp_path, field, malformed):
    bad_plan = plan(step(draft=True, target_mode='GENERATED', specials=[], filename=None))
    if field == 'steps':
        bad_plan['steps'] = malformed
    else:
        bad_plan['steps'][0]['command']['request'] = malformed
    research, agent, provider, apply, execute, generate, start, root = preserved_draft(
        monkeypatch, tmp_path, research_reply('BACKTEST', strategy=fixture().changed(), research_plan=bad_plan))
    before, files = draft_state(research, agent), project_files(root)
    with pytest.raises(DiscussionError) as failure:
        research.send('원비를 15분으로 고쳐 다시 백테스트')
    assert failure.value.pending_preserved is True
    assert draft_state(research, agent) == before
    assert project_files(root) == files and len(provider.exchanges) == 2
    apply.assert_not_called(); generate.assert_not_called(); execute.assert_not_called(); start.assert_not_called()


def test_invalid_canonical_gets_one_validator_correction_with_original_request_and_meaning(monkeypatch, tmp_path):
    f = fixture()
    corrected = f.changed()
    invalid = copy.deepcopy(corrected)
    invalid['interpretation']['unsupported_structure'] = True
    with pytest.raises(ValueError) as validation_error:
        validate_intent(invalid)
    research, agent, provider, apply, execute, generate, start, root = preserved_draft(
        monkeypatch, tmp_path, research_reply('STRATEGY', strategy=invalid))
    provider.script.append(research_reply('STRATEGY', strategy=corrected))
    original_plan = copy.deepcopy(research.backtest.last_plan)
    requests = copy.deepcopy(research.backtest.pending['steps'])
    files = project_files(root)
    original_request = '직전 전략에서 원비 시간봉만 15분으로 바꿔. 나머지 조건과 백테스트 기간은 그대로야.'
    calls_before = len(provider.exchanges)
    result = research.send(original_request)
    assert len(provider.exchanges) - calls_before == 2
    assert result['kind'] == 'BACKTEST' and result['can_confirm']
    assert result['strategy']['result'] == corrected and agent.last_intent == corrected
    assert research.backtest.last_plan == original_plan and research.backtest.pending['steps'] == requests
    first, repaired = provider.exchanges[-2:]
    first_request = json.loads(first[1]['content'])
    assert first_request['message'] == original_request and first_request['context']['current_strategy'] == f.original()
    assert repaired[:len(first)] == first
    assert json.loads(repaired[-2]['content']) == invalid
    assert repaired[-1]['role'] == 'user'
    correction = json.loads(repaired[-1]['content'])['message']
    assert '전체 전략 schema 검증 오류' in correction
    assert '원래 요청과 직전 canonical의 의미를 보존' in correction
    assert str(validation_error.value) not in correction  # Internal errors are never model context.
    assert project_files(root) == files
    apply.assert_not_called(); generate.assert_not_called(); execute.assert_not_called(); start.assert_not_called()


def test_second_invalid_canonical_stops_correction_and_preserves_original_pending(monkeypatch, tmp_path):
    invalid = fixture().changed()
    invalid['interpretation']['unsupported_structure'] = True
    invalid_reply = research_reply('STRATEGY', strategy=invalid)
    research, agent, provider, apply, execute, generate, start, root = preserved_draft(
        monkeypatch, tmp_path, invalid_reply)
    unused = research_reply('STRATEGY', strategy=fixture().changed())
    provider.script.extend([copy.deepcopy(invalid_reply), unused])
    before, files = draft_state(research, agent), project_files(root)
    calls_before = len(provider.exchanges)
    with pytest.raises(DiscussionError) as failure:
        research.send('원비 시간봉만 15분으로 수정해')
    assert failure.value.pending_preserved is True
    assert len(provider.exchanges) - calls_before == 2 and provider.script == [unused]
    assert draft_state(research, agent) == before and project_files(root) == files
    assert json.loads(provider.exchanges[-1][1]['content'])['context']['current_strategy'] == fixture().original()
    apply.assert_not_called(); generate.assert_not_called(); execute.assert_not_called(); start.assert_not_called()


def test_wire_schema_links_draft_to_generated_start_and_preserves_canonical_schema():
    import jsonschema

    schema = response_schema('strategy')
    jsonschema.Draft202012Validator.check_schema(schema)
    assert schema.get('$defs') == output_schema().get('$defs')
    jsonschema.validate(fixture().original(), schema)
    generated = json.loads(research_reply('BACKTEST', research_plan=plan(
        step(draft=True, target_mode='GENERATED', specials=[], filename=None)))['content'])
    existing = json.loads(research_reply('BACKTEST', research_plan=plan(step()))['content'])
    jsonschema.validate(generated, schema)
    jsonschema.validate(existing, schema)
    special_draft = json.loads(research_reply('BACKTEST', research_plan=plan(step(draft=True)))['content'])
    stopped_draft = copy.deepcopy(generated)
    stopped_draft['plan']['steps'][0]['command']['action'] = 'STOP'
    for invalid in (special_draft, stopped_draft):
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(invalid, schema)


@pytest.mark.parametrize('invalid_request', [
    {'start': '2026-09-01T00:00:00Z', 'specials': []},
    {'specials': ['SPECIAL1']},
], ids=['date-format', 'generated-special-conflict'])
def test_prepare_error_after_new_canonical_restores_strategy_confirmation_and_absent_plan(
        monkeypatch, tmp_path, invalid_request):
    f = fixture()
    bad_plan = plan(step(draft=True, target_mode='GENERATED', filename=None, **invalid_request))
    research, agent, provider, apply = conversation([
        research_reply('STRATEGY', strategy=f.original()),
        research_reply('BACKTEST', strategy=f.changed(), research_plan=bad_plan)])
    root = tmp_path / 'Part3'
    root.mkdir()
    (root / 'existing.txt').write_text('preserve\n', encoding='utf-8')
    monkeypatch.setattr(storage, 'ROOT', root)
    preview = Mock(return_value={'filename': 'Test_SPECIAL002.py'})
    generate, execute, start = Mock(), Mock(), Mock()
    monkeypatch.setattr(storage, 'preview', preview)
    monkeypatch.setattr(storage, 'generate', generate)
    monkeypatch.setattr(backtest_commands, 'execute', execute)
    monkeypatch.setattr(sequences, 'start', start)
    block_reinterpretation(monkeypatch, agent)
    first = research.send('15분 상승추세와 3분 하단 원비 전략')
    assert first['can_apply'] and research.backtest is None
    before_agent = copy.deepcopy((agent.last_intent, agent.messages, agent.revision, agent.candidate,
        agent.original_text, agent.pending_clarification, agent.first_interpretation))
    before_research = copy.deepcopy((research.pending, research.number, research.revision,
        research.kind, research.question, research.history))
    files = project_files(root)
    with pytest.raises(DiscussionError) as failure:
        research.send('원비를 15분으로 바꿔 백테스트')
    assert failure.value.pending_preserved is True
    assert (agent.last_intent, agent.messages, agent.revision, agent.candidate,
        agent.original_text, agent.pending_clarification, agent.first_interpretation) == before_agent
    assert (research.pending, research.number, research.revision,
        research.kind, research.question, research.history) == before_research
    assert research.backtest is None and project_files(root) == files
    assert len(provider.exchanges) == 2
    preview.assert_called_once()
    apply.assert_not_called(); generate.assert_not_called(); execute.assert_not_called(); start.assert_not_called()
    confirmed = research.confirm(first['revision'])
    assert confirmed['kind'] == 'STRATEGY' and agent.last_intent == f.original()
    apply.assert_called_once()
    assert project_files(root) == files


def test_preview_failure_restores_existing_backtest_plan_and_all_confirmation_state(monkeypatch, tmp_path):
    research, agent, provider, apply, execute, generate, start, root = preserved_draft(
        monkeypatch, tmp_path, research_reply('STRATEGY', strategy=fixture().changed()))
    backtest = research.backtest
    preview = Mock(side_effect=ValueError('현재 전략 미리보기를 만들 수 없습니다.'))
    monkeypatch.setattr(backtest, 'previewer', preview)
    before, files = draft_state(research, agent), project_files(root)
    with pytest.raises(DiscussionError) as failure:
        research.send('원비 시간봉만 15분으로 수정해')
    assert failure.value.pending_preserved is True
    assert research.backtest is backtest and draft_state(research, agent) == before
    assert project_files(root) == files and len(provider.exchanges) == 2
    preview.assert_called_once()
    apply.assert_not_called(); generate.assert_not_called(); execute.assert_not_called(); start.assert_not_called()
