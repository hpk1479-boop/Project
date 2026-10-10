"""Every AI transport has the same source-free strategy permission boundary.

All providers are stubs: no model process, network call or engine calculation.
"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'Part3')]

from common_ai import settings
from common_ai.process_identity import identity
from common_ai.security import filter_tools, require_tool
from common_ai.service import Service
from lab.ai.agent import Agent
from lab.ai.research_interpreter import interpret
from lab.ai.schema import output_schema
from lab.ai.tools import ReadOnlyWorkspace, TOOL_SPECS


PROVIDERS = ('ollama', 'local_gguf', 'gemini', 'future_provider')
SAFE_NAMES = {'vocabulary', 'list_specials', 'describe_special'}
DENIED_NAMES = ('read_project_code', 'search_project_code', 'read_doc', 'read_file')
PRIVATE = 'synthetic-private-implementation-93'
KEY = 'synthetic-credential-93'


def canonical():
    # Object references, window lifetime and dynamic snapshot names must survive
    # the permission change just as ordinary TREND/OZ conditions do.
    return {
        'supported': True, 'intent': 'CREATE_STRATEGY',
        'interpretation': {
            'direction': 'LONG', 'symbols': ['XAUUSD+'],
            'steps': [
                {'kind': 'FVG_NEW', 'tfs': ['5m'], 'side': 'BULL', 'capture': 'new_gap'},
                {'kind': 'FVG_TOUCH', 'tfs': ['5m'], 'side': 'BULL', 'ref': 'new_gap'},
            ],
            'order_mode': 'SEQUENTIAL', 'within_sec': None,
            'lifecycle': {
                'expires': {'seconds': 3600},
                'snapshots': {'atr_anchor': {'tf': '15m', 'field': 'ATR14', 'bar_state': 'CLOSED'}},
            },
            'time_filters': {'MAIN_LONDON': '1600-1800'},
            'final': {'kind': 'OZ', 'tfs': ['1m'], 'validation_mode': 'BLIND', 'trigger_mode': 'BREAKER'},
        },
        'needs_clarification': False, 'clarification_question': None,
        'message_ko': '같은 FVG 접촉 뒤 무지성 브레이커',
    }


def recipe():
    return {'schema_version': 2, 'base': 'AI', 'name': '공통 전략',
            'description': '동일 객체를 사용하는 전략', 'symbols': ['XAUUSD+'],
            'strategy_intent': canonical()['interpretation']}


def ordinary():
    value = canonical()
    value['interpretation'] = {
        'direction': 'LONG', 'symbols': ['XAUUSD+'],
        'steps': [{'kind': 'TREND', 'tfs': ['15m'], 'direction': 'LONG'}],
        'order_mode': 'SIMULTANEOUS', 'within_sec': None,
        'final': {'kind': 'OZ', 'tfs': ['1m'], 'validation_mode': 'NORMAL', 'trigger_mode': 'OZ'},
    }
    value['message_ko'] = '15분 상승추세에 1분 올존'
    return value


def stub_provider(name):
    # Even an object claiming local diagnostics cannot enable forbidden tools.
    return SimpleNamespace(settings={'provider': name}, permission_scope='local')


@pytest.mark.parametrize('provider', PROVIDERS)
def test_forced_private_tool_calls_are_rejected_before_workspace_access(monkeypatch, provider):
    workspace = ReadOnlyWorkspace()
    accessed = []

    def private_reader(**kwargs):
        accessed.append(kwargs)
        return PRIVATE

    for name in DENIED_NAMES:
        monkeypatch.setattr(workspace, name, private_reader, raising=False)
    selected = stub_provider(provider)
    agent = Agent(selected, workspace)
    advertised = filter_tools(selected, copy.deepcopy(TOOL_SPECS))
    assert {row['function']['name'] for row in advertised} == SAFE_NAMES
    for name in SAFE_NAMES:
        require_tool(selected, name)
    for name in DENIED_NAMES:
        with pytest.raises(ValueError):
            require_tool(selected, name)
        with pytest.raises(ValueError):
            agent.call_tool(name, {'path': 'Part1/program/private.py'})
    assert accessed == []


@pytest.mark.parametrize('provider', PROVIDERS)
def test_allowed_tool_results_keep_vocabulary_and_full_recipe_without_source(monkeypatch, provider):
    workspace = ReadOnlyWorkspace()
    vocabulary = workspace.vocabulary()
    public_vocabulary = copy.deepcopy(vocabulary)
    vocabulary.update({'source_code': PRIVATE, 'runtime': PRIVATE, 'connections': {'password': KEY}})
    monkeypatch.setattr(workspace, 'vocabulary', lambda: copy.deepcopy(vocabulary))
    monkeypatch.setattr(workspace, 'list_specials', lambda: [
        {'id': 'USER_PRESET', 'name': '등록 전략', 'path': PRIVATE, 'source_code': PRIVATE}])
    monkeypatch.setattr(workspace, 'describe_special', lambda number: {
        'id': number, 'name': '등록 전략', 'source': PRIVATE,
        'recipe': dict(recipe(), source_text=PRIVATE, logs=PRIVATE)})
    agent = Agent(stub_provider(provider), workspace)
    actual_vocab = agent.call_tool('vocabulary', {})
    assert actual_vocab['intent_kinds'] == public_vocabulary['intent_kinds']
    assert actual_vocab['intent_parameters'] == public_vocabulary['intent_parameters']
    assert actual_vocab['timeframes'] == public_vocabulary['timeframes']
    actual_presets = agent.call_tool('list_specials', {})
    assert actual_presets == [{'id': 'USER_PRESET', 'name': '등록 전략'}]
    actual_recipe = agent.call_tool('describe_special', {'number': 'USER_PRESET'})
    assert actual_recipe == {'id': 'USER_PRESET', 'name': '등록 전략', 'recipe': recipe()}
    encoded = json.dumps((agent.messages, actual_vocab, actual_presets, actual_recipe))
    assert PRIVATE not in encoded and KEY not in encoded


@pytest.mark.parametrize('provider', PROVIDERS)
def test_service_filters_every_transport_without_changing_allowed_strategy_json(monkeypatch, tmp_path, provider):
    chosen = {'provider': provider, 'model': 'stub', 'gemini_api_key': KEY, 'timeout': 1}
    monkeypatch.setattr(settings, 'read_settings', lambda root: dict(chosen))
    captured = []

    class Model:
        permission_scope = 'local'

        def chat(self, rows, tools, *, response_schema):
            captured.append(copy.deepcopy((rows, tools, response_schema)))
            return {'content': json.dumps(ordinary()), 'tool_calls': []}

    workspace = ReadOnlyWorkspace()
    rows = [
        {'role': 'system', 'content': 'private implementation: ' + PRIVATE},
        {'role': 'system', 'content': json.dumps({'moses_contract': {
            'vocabulary': workspace.vocabulary(), 'presets': workspace.list_specials(),
            'runtime': PRIVATE, 'source_code': PRIVATE, 'connections': {'password': KEY}}})},
        {'role': 'assistant', 'content': PRIVATE},
        {'role': 'tool', 'name': 'read_project_code', 'tool_call_id': 'private',
         'content': json.dumps({'ok': True, 'result': {'text': PRIVATE}})},
        {'role': 'user', 'content': json.dumps({
            'message': '최종 시간봉만 3분으로 바꿔 주세요',
            'context': {'current_strategy': canonical(), 'recipe': recipe(),
                        'question': '최종 시간봉은?', 'source_code': PRIVATE,
                        'runtime': PRIVATE, 'logs': PRIVATE, 'cache': PRIVATE, 'data': PRIVATE,
                        'path': 'Part1/program/private.py', 'password': KEY},
            'discussion': [{'role': 'assistant', 'content': PRIVATE},
                           {'role': 'user', 'content': '나머지 조건은 유지해 주세요'}],
        }, ensure_ascii=False)},
    ]
    before = copy.deepcopy(rows)
    schema = output_schema()
    service = Service(tmp_path, provider_factory=lambda _: Model(), runtime=SimpleNamespace(generation=0))
    service.clients['test'] = {'pid': os.getpid(), 'created': identity(os.getpid())}
    try:
        service.chat({'client_id': 'test', 'generation': 0, 'role': 'strategy',
                      'messages': rows, 'tools': TOOL_SPECS, 'response_schema': schema})
        sent_rows, sent_tools, sent_schema = captured[0]
        encoded = json.dumps(captured[0], ensure_ascii=False)
        assert PRIVATE not in encoded and KEY not in encoded
        assert 'Part1/program/private.py' not in encoded
        assert {row['function']['name'] for row in sent_tools} == SAFE_NAMES
        request = json.loads(sent_rows[-1]['content'])
        assert request['context'] == {'current_strategy': canonical(), 'recipe': recipe(), 'question': '최종 시간봉은?'}
        assert request['discussion'] == [{'role': 'user', 'content': '나머지 조건은 유지해 주세요'}]
        contract = json.loads(sent_rows[0]['content'])['moses_contract']
        assert contract['vocabulary']['intent_kinds'] == workspace.vocabulary()['intent_kinds']
        assert contract['presets'] == workspace.list_specials()
        assert sent_schema == schema
        assert rows == before
    finally:
        service.server.server_close()


@pytest.mark.parametrize('provider', PROVIDERS)
def test_research_tool_loop_refuses_private_request_and_still_returns_strategy(monkeypatch, provider):
    captured = []

    class Model:
        settings = {'provider': provider}
        permission_scope = 'local'

        def chat(self, rows, tools, *, response_schema):
            captured.append(copy.deepcopy((rows, tools)))
            if len(captured) == 1:
                return {'content': '', 'tool_calls': [{'id': 'private', 'name': 'read_project_code',
                                                       'arguments': {'path': 'Part1/program/private.py'}}]}
            return {'content': json.dumps(ordinary()), 'tool_calls': []}

    workspace = ReadOnlyWorkspace()
    accessed = []
    monkeypatch.setattr(workspace, 'read_project_code', lambda **args: accessed.append(args))
    model = Model()
    agent = Agent(model, workspace)
    value, actions = interpret(model, agent, 'strategy', [], {}, '15분 상승추세에 1분 올존 전략 만들어줘')
    assert accessed == []
    assert value['kind'] == 'STRATEGY' and value['strategy'] == ordinary()
    assert len(actions) == 1 and actions[0]['ok'] is False
    assert actions[0]['tool'] == 'forbidden_tool'
    assert all({row['function']['name'] for row in tools} == SAFE_NAMES for _, tools in captured)
    tool_reply = next(row for row in captured[-1][0] if row['role'] == 'tool')
    assert json.loads(tool_reply['content'])['ok'] is False


@pytest.mark.parametrize('provider', PROVIDERS)
def test_followup_uses_full_current_intent_and_discards_private_old_transcripts(provider):
    agent = Agent(stub_provider(provider))
    agent.last_intent = canonical()
    agent.pending_clarification = {'clarification_question': '최종 시간봉은?'}
    agent.messages.extend([
        {'role': 'assistant', 'content': PRIVATE},
        {'role': 'tool', 'name': 'read_project_code', 'content': PRIVATE},
    ])
    agent._begin_turn('최종 시간봉만 3분으로')
    encoded = json.dumps(agent.messages, ensure_ascii=False)
    assert PRIVATE not in encoded
    request = json.loads(agent.messages[-1]['content'])
    assert request['message'] == '최종 시간봉만 3분으로'
    assert request['context']['current_strategy'] == canonical()
    assert request['context']['question'] == '최종 시간봉은?'
    assert agent.last_intent == canonical()


@pytest.mark.parametrize('provider', ('ollama', 'local_gguf'))
def test_direct_local_provider_entrypoint_also_filters_before_transport(monkeypatch, provider):
    from common_ai import model_runtime
    from common_ai.local_gguf import LocalGGUF
    from common_ai.provider import OpenAICompatible
    import urllib.request

    captured = []
    expected_reply = {'content': json.dumps(ordinary()), 'tool_calls': []}

    def capture_runtime(owner, rows, tools, schema):
        captured.append(copy.deepcopy((rows, tools, schema)))
        return expected_reply

    class Reply:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def read(self):
            return json.dumps({'message': expected_reply}).encode('utf-8')

    def capture_http(request, *, timeout):
        body = json.loads(request.data)
        captured.append((body['messages'], body['tools'], body['format']))
        return Reply()

    monkeypatch.setattr(model_runtime.RUNTIME, 'gguf_chat', capture_runtime)
    monkeypatch.setattr(urllib.request, 'urlopen', capture_http)
    if provider == 'ollama':
        selected = OpenAICompatible(model='synthetic:93')
    else:
        selected = LocalGGUF({'provider': provider, 'gguf_model_path': 'models/synthetic.gguf'})
    rows = [
        {'role': 'system', 'content': PRIVATE},
        {'role': 'assistant', 'content': PRIVATE},
        {'role': 'user', 'content': json.dumps({'message': '최종 시간봉만 수정해 주세요',
            'context': {'current_strategy': canonical(), 'recipe': recipe(), 'runtime': PRIVATE,
                        'source_code': PRIVATE, 'path': 'Part1/program/private.py', 'password': KEY}})},
    ]
    schema = output_schema()
    selected.chat(rows, TOOL_SPECS, response_schema=schema)
    sent_rows, sent_tools, sent_schema = captured[0]
    encoded = json.dumps(captured[0])
    assert PRIVATE not in encoded and KEY not in encoded
    assert 'Part1/program/private.py' not in encoded
    assert {row['function']['name'] for row in sent_tools} == SAFE_NAMES
    assert json.loads(sent_rows[-1]['content'])['context'] == {
        'current_strategy': canonical(), 'recipe': recipe()}
    assert sent_schema == schema
