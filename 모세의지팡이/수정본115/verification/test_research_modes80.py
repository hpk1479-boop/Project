"""Both modes share validated strategy/jobs; discussion is read-only."""
import copy
import json
import sys
import threading
import urllib.request
import urllib.error
from unittest.mock import Mock

import pytest

from test_research79 import (Agent, Research, Plan, ScriptedProvider, fixture,
                             reply, research_reply, plan, step, snapshot, sequences, storage, JOB, OTHER_JOB, ROOT)
from lab import server
from lab.ai.intent import unsupported


def chat(text='두 조건의 시간봉 차이를 함께 살펴볼 수 있습니다.'):
    return research_reply('CHAT', text=text)


def conversation(script):
    provider = ScriptedProvider(script)
    # Capture all contextual messages rather than only the last message.
    provider.exchanges = []
    native = provider.chat
    def capture(messages, tools, **kwargs):
        provider.exchanges.append(copy.deepcopy(messages))
        return native(messages, tools, **kwargs)
    provider.chat = capture
    agent = Agent(provider)
    apply = Mock(side_effect=lambda revision: {'recipe': agent.apply(revision)})
    return Research(provider, lambda: agent, apply, context_loader=snapshot), agent, provider, apply


def test_discussion_does_not_interpret_apply_create_or_run(monkeypatch):
    research, agent, provider, apply = conversation([chat()])
    send, generate, execute = Mock(), Mock(), Mock()
    monkeypatch.setattr(agent, 'send', send)
    monkeypatch.setattr(storage, 'generate', generate)
    monkeypatch.setattr(sequences, 'start', execute)
    result = research.send('15분 원비와 3분 원비 차이가 뭐야?', mode='chat')
    assert result['kind'] == 'CHAT' and result['pending_preserved']
    assert agent.last_intent is None and research.pending is None
    send.assert_not_called(); apply.assert_not_called(); generate.assert_not_called(); execute.assert_not_called()
    with pytest.raises(ValueError):
        research.confirm('arbitrary')
    assert len(provider.exchanges) == 1
    assert [variant['properties']['kind']['const'] for variant in provider.response_schemas[-1]['anyOf']
            if 'kind' in variant['properties']] == ['BACKTEST', 'CHAT']


def test_discussion_and_mode_switch_keep_current_canonical_and_confirmation():
    f = fixture()
    research, agent, provider, apply = conversation([
        research_reply('STRATEGY', strategy=f.original()), chat(),
        research_reply('STRATEGY', strategy=f.changed())])
    first = research.send('15분 상승추세와 3분 하단 원비')
    before = (copy.deepcopy(agent.last_intent), copy.deepcopy(agent.messages), agent.revision, research.pending)
    result = research.send('원비를 15분으로 바꾸면 어떤 차이가 있어?', mode='chat')
    assert result['kind'] == 'CHAT'
    assert (agent.last_intent, agent.messages, agent.revision, research.pending) == before
    context = json.loads(provider.seen[1]['content'])['context']
    assert context['current_strategy'] == f.original()
    assert any('15분 상승추세' in m['content'] for m in provider.exchanges[1])
    second = research.send('좋아 원비만 15분으로 수정해', mode='strategy')
    assert second['kind'] == 'STRATEGY' and second['is_revision'] and agent.last_intent == f.changed()
    with pytest.raises(ValueError):
        research.confirm(first['revision'])
    apply.assert_not_called()
    assert research.confirm(second['revision'])['recipe']['schema_version'] == 2
    assert apply.call_count == 1
    assert len(provider.exchanges) == 3
    with pytest.raises(ValueError):
        research.confirm(second['revision'])


def test_existing_confirmation_remains_for_the_original_intent_after_discussion():
    f = fixture()
    research, agent, _, apply = conversation([
        research_reply('STRATEGY', strategy=f.original()), chat()])
    first = research.send('전략 해석')
    research.send('이 조건을 설명해줘', mode='chat')
    research.confirm(first['revision'])
    assert agent.last_intent == f.original() and apply.call_count == 1


def test_chat_proposal_can_be_created_in_strategy_mode_only_after_confirmation():
    f = fixture()
    proposal = '15분 상승추세에서 3분 하단 WONBI를 터치하면 1분 브레이커 올존 매수'
    research, agent, provider, apply = conversation([
        chat(text=proposal), research_reply('STRATEGY', strategy=f.original())])
    research.send('어떤 조건으로 연구할까?', mode='chat')
    assert agent.last_intent is None
    response = research.send('그 조건으로 만들어', mode='strategy')
    assert response['can_apply'] and agent.last_intent == f.original()
    assert any(m['content'] == proposal for m in json.loads(provider.seen[1]['content'])['discussion'])
    assert len(provider.exchanges) == 2
    assert json.loads(provider.seen[-1]['content'])['message'] == '그 조건으로 만들어'
    apply.assert_not_called()
    research.confirm(response['revision'])
    assert apply.call_count == 1


def test_chat_backtest_uses_existing_plan_and_waits_for_confirmation(monkeypatch):
    research, agent, provider, apply = conversation([
        research_reply('BACKTEST', research_plan=plan(step()))])
    execute = Mock(return_value={'job_id': JOB})
    start = Mock(return_value={'sequence_id': OTHER_JOB, 'jobs': [], 'phase': 'run'})
    monkeypatch.setattr(sequences, 'start', start)
    research.backtest = Plan(provider, lambda: agent, context_loader=snapshot, executor=execute)
    result = research.send('스페셜1 골드 한달 백테스트해', mode='chat')
    assert result['kind'] == 'BACKTEST' and result['can_confirm']
    assert len(provider.exchanges) == 1
    start.assert_not_called(); execute.assert_not_called(); apply.assert_not_called()
    confirmed = research.confirm(result['revision'])
    assert confirmed['result']['sequence_id'] == OTHER_JOB and start.call_count == 1
    assert start.call_args.args[0][0]['request']['symbol'] == 'XAUUSD+'
    assert start.call_args.kwargs['executor'] is execute


def test_chat_new_strategy_backtest_generates_and_registers_through_existing_path(tmp_path, monkeypatch):
    f = fixture()
    research, agent, provider, apply = conversation([
        research_reply('BACKTEST', strategy=f.original(),
            research_plan=plan(step(draft=True, target_mode='GENERATED', specials=[], filename=None)))])
    root = tmp_path / 'Part3'; root.mkdir()
    monkeypatch.setattr(storage, 'ROOT', root)
    execute = Mock(return_value={'job_id': JOB})
    def start(commands, **kwargs):
        return {'sequence_id': OTHER_JOB, 'jobs': [kwargs['executor'](commands[0])],
                'phase': 'run'}
    monkeypatch.setattr(sequences, 'start', start)
    research.backtest = Plan(provider, lambda: agent, context_loader=snapshot, executor=execute)
    response = research.send('그 전략 골드 1년 백테스트해', mode='chat')
    assert response['can_confirm'] and response['strategy']['can_apply']
    assert len(provider.exchanges) == 1 and agent.last_intent == f.original()
    assert not list(root.glob('generated/*'))
    execute.assert_not_called()
    result = research.confirm(response['revision'])
    filename = result['result']['generated_filename']
    code = (root / 'generated' / filename).read_text('utf-8')
    namespace = {'__name__': 'research_generated80', '__file__': str(root / 'generated' / filename)}
    exec(compile(code, filename, 'exec'), namespace)
    sys.path.insert(0, str(ROOT / 'Part1/program'))
    assert callable(namespace['register'])
    api = Mock()
    api.shared_resource.side_effect = lambda _, factory: factory()
    api.market_context.return_value = (None, 'XAUUSD+', 0)
    api.snapshot_oz_watches.return_value = []
    manager = Mock(special_api=api)
    namespace['register'](manager)
    api.register_subscription_provider.assert_called_once()
    api.register_fact_observer.assert_called_once()
    api.register_strategy_state_provider.assert_called_once()
    api.register_watch_handler.assert_called_once()
    assert execute.call_args.args[0]['request']['filename'] == filename
    assert execute.call_count == 1
    apply.assert_not_called()


@pytest.mark.parametrize('value', [
    {'kind': 'CHAT', 'message_ko': '설명', 'strategy': {}, 'plan': None},
    {'kind': 'STRATEGY', 'message_ko': '해석', 'strategy': None, 'plan': None},
    {'kind': 'BACKTEST', 'message_ko': '실행', 'strategy': None, 'plan': None},
    {'kind': 'EXECUTE', 'message_ko': '실행', 'strategy': None, 'plan': None},
    {'kind': 'CHAT', 'message_ko': '', 'strategy': None, 'plan': None},
    {'kind': 'CHAT', 'message_ko': '설명', 'strategy': None, 'plan': None, 'recipe': {}},
    None])
def test_invalid_chat_output_never_reaches_strategy_or_jobs(value):
    research, agent, _, apply = conversation([reply(value)])
    with pytest.raises(ValueError):
        research.send('조건을 이야기하자', mode='chat')
    assert agent.last_intent is None and research.pending is None
    apply.assert_not_called()


def test_chat_tool_call_is_rejected_and_unsupported_intent_stays_blocked():
    research, agent, _, apply = conversation([
        {**chat(), 'tool_calls': [{'id': 'not_allowed', 'name': 'generate', 'arguments': {}}]},
        reply({'bad': 'shape'}),
        research_reply('STRATEGY', strategy=unsupported('UNSUPPORTED_FEATURE', '이 조건은 지원하지 않습니다.'))])
    with pytest.raises(ValueError):
        research.send('설명해줘', mode='chat')
    result = research.send('전략으로 만들어', mode='chat')
    assert not result['can_apply'] and not result['result']['supported']
    with pytest.raises(ValueError):
        research.confirm(result['revision'])
    apply.assert_not_called()


def test_chat_history_is_bounded_and_reset_removes_both_modes_only(monkeypatch):
    research, agent, provider, _ = conversation([chat(text='설명' + str(i)) for i in range(12)])
    for i in range(12):
        research.send('질문' + str(i), mode='chat')
    assert len(research.history) == 8 and research.history[0]['content'] == '질문8'
    assert any(m['content'] == '설명10' for m in json.loads(provider.seen[-1]['content'])['discussion'])
    other = object()
    monkeypatch.setattr(server, 'RESEARCH_SESSIONS', {'a': research, 'b': other})
    monkeypatch.setattr(server, 'AI_SESSIONS', {'a': agent, 'b': other})
    monkeypatch.setattr(server, 'BACKTEST_COMMAND_SESSIONS', {'a': object(), 'b': other})
    assert server.ai_post('/api/ai/reset', {'session': 'a', 'research': True}) == {'ok': True}
    assert all(getattr(server, key) == {'b': other} for key in
               ('RESEARCH_SESSIONS', 'AI_SESSIONS', 'BACKTEST_COMMAND_SESSIONS'))


def test_mode_is_validated_before_provider_request_and_api_passes_selection(monkeypatch):
    research, _, provider, _ = conversation([chat()])
    with pytest.raises(ValueError, match='연구 모드'):
        research.send('질문', mode='unknown')
    assert not provider.exchanges
    monkeypatch.setattr(server, 'research_session', lambda _: research)
    assert server.ai_post('/api/ai/chat', {'session': 'a', 'research': True, 'mode': 'chat',
                                        'message': '설명해줘'})['kind'] == 'CHAT'


def test_failed_discussion_preserves_existing_canonical_and_confirmation():
    f = fixture()
    research, agent, _, apply = conversation([
        research_reply('STRATEGY', strategy=f.original()), reply({'bad': 'shape'})])
    first = research.send('최초 전략')
    before = copy.deepcopy(agent.last_intent)
    with pytest.raises(ValueError) as error:
        research.send('설명해줘', mode='chat')
    assert error.value.pending_preserved is True
    assert agent.last_intent == before and research.pending is not None
    research.confirm(first['revision'])
    assert apply.call_count == 1


def test_history_keeps_complete_recent_proposal_under_character_budget():
    proposal = '조건 설명 ' * 800
    research, _, provider, _ = conversation([chat(text=proposal)] * 4)
    for i in range(4):
        research.send('질문' + str(i), mode='chat')
    assert sum(len(row['content']) for row in research.history) <= 16000
    assert research.history[-1]['content'] == proposal
    assert any(row['content'] == proposal for row in json.loads(provider.seen[-1]['content'])['discussion'])


def test_http_discussion_failure_returns_preserved_confirmation_to_browser(monkeypatch):
    f = fixture()
    research, _, _, apply = conversation([
        research_reply('STRATEGY', strategy=f.original()), reply({'bad': 'shape'})])
    monkeypatch.setattr(server, 'research_session', lambda _: research)
    host = server.LabServer(0)
    worker = threading.Thread(target=host.serve_forever, daemon=True); worker.start()
    def post(path, data):
        request = urllib.request.Request(f'http://127.0.0.1:{host.server_port}/api/ai/{path}',
            data=json.dumps({'session': 'test80', 'research': True, **data}).encode('utf-8'),
            headers={'X-Lab-Token': host.token, 'Content-Type': 'application/json'})
        with urllib.request.urlopen(request, timeout=5) as response:
            return json.load(response)
    try:
        first = post('chat', {'message': '최초 전략', 'mode': 'strategy'})
        with pytest.raises(urllib.error.HTTPError) as failure:
            post('chat', {'message': '조건 설명', 'mode': 'chat'})
        body = json.load(failure.value)
        assert failure.value.code == 400 and body['pending_preserved'] is True
        assert 'error' in body
        apply.assert_not_called()
        result = post('apply', {'revision': first['revision']})
        assert result['recipe']['schema_version'] == 2 and apply.call_count == 1
    finally:
        host.shutdown(); host.server_close(); worker.join(timeout=5)
