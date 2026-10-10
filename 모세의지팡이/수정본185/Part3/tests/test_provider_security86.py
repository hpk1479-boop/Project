"""Every provider receives language contracts, never workspace read authority."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import sys
from unittest.mock import Mock

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / 'Part3')]

from common_ai.security import provider_scope
from lab.ai import research_chat, research_interpreter, shared_provider
from lab.ai.agent import Agent
from lab.ai.tools import ReadOnlyWorkspace


FORBIDDEN = ('read_project_code', 'search_project_code', 'read_doc')
PRIVATE = 'synthetic-private-workspace-content-86'


def intent():
    return {'supported': True, 'intent': 'CREATE_STRATEGY',
            'interpretation': {'symbols': ['XAUUSD+'], 'direction': 'LONG',
                'steps': [{'kind': 'TREND', 'tf': '15m', 'direction': 'LONG'}],
                'order_mode': 'SIMULTANEOUS',
                'final': {'kind': 'OZ', 'tf': '1m', 'validation_mode': 'NORMAL', 'trigger_mode': 'OZ'}},
            'needs_clarification': False, 'clarification_question': None, 'message_ko': '전략 조건'}


def reply(value):
    return {'content': json.dumps(value, ensure_ascii=False), 'tool_calls': []}


class Provider:
    def __init__(self, script=(), scope='external'):
        self.permission_scope = scope
        self.script = list(script)
        self.seen = []

    def chat(self, messages, tools, **kwargs):
        self.seen.append(copy.deepcopy((messages, tools, kwargs)))
        return self.script.pop(0)


def workspace():
    value = ReadOnlyWorkspace()
    for name in FORBIDDEN:
        setattr(value, name, Mock(return_value={'text': PRIVATE}))
    return value


@pytest.mark.parametrize('provider', ['gemini', 'future_external', None])
def test_unknown_and_external_provider_names_never_grant_local_authority(provider):
    assert provider_scope(provider) == 'external'


@pytest.mark.parametrize('scope', ['external', 'local'])
@pytest.mark.parametrize('name', FORBIDDEN)
def test_all_tool_requests_are_rejected_before_workspace_read(name, scope):
    selected = Provider(scope=scope)
    files = workspace()
    agent = Agent(selected, files)
    with pytest.raises(ValueError):
        agent.call_tool(name, {'path': 'Part1/program/example.py', 'query': PRIVATE})
    getattr(files, name).assert_not_called()


@pytest.mark.parametrize('scope', ['external', 'local'])
def test_all_agents_construct_no_manual_and_filter_advertised_tools(monkeypatch, scope):
    original_read = Path.read_text
    def no_manual(path, *args, **kwargs):
        if 'ai_context' in path.parts:
            raise AssertionError('private manual read')
        return original_read(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'read_text', no_manual)
    selected = Provider([reply(intent())], scope=scope)
    files = workspace()
    agent = Agent(selected, files)
    assert agent.send('15분 상승추세에서 1분 올존')['can_apply']
    messages, tools, _ = selected.seen[0]
    assert 'moses_contract' in json.loads(messages[0]['content'])
    assert json.loads(messages[1]['content'])['message'] == '15분 상승추세에서 1분 올존'
    assert not set(FORBIDDEN) & {row['function']['name'] for row in tools}


@pytest.mark.parametrize('scope', ['external', 'local'])
def test_any_model_cannot_force_a_reader_through_the_tool_loop(scope):
    selected = Provider([
        {'content': '', 'tool_calls': [{'id': 'read-86', 'name': 'read_project_code',
                                      'arguments': {'path': PRIVATE}}]},
        reply(intent()),
    ], scope=scope)
    files = workspace()
    result = Agent(selected, files).send('15분 상승추세에서 1분 올존')
    files.read_project_code.assert_not_called()
    assert result['can_apply']
    assert len(result['actions']) == 1
    denied = result['actions'][0]
    assert denied['tool'] == 'forbidden_tool' and not denied['ok']
    assert '허용' in denied['error']
    assert PRIVATE not in json.dumps(result, ensure_ascii=False)


@pytest.mark.parametrize('scope', ['external', 'local'])
def test_any_followup_keeps_canonical_and_discards_private_transcript(scope):
    selected = Provider(scope=scope)
    agent = Agent(selected, workspace())
    original = agent.accept(intent(), '첫 전략')['result']
    agent.messages.append({'role': 'tool', 'name': 'read_project_code', 'content': PRIVATE})
    assert agent._begin_turn('추세는 1시간으로 바꿔')
    assert PRIVATE not in json.dumps(agent.messages, ensure_ascii=False)
    assert 'moses_contract' in json.loads(agent.messages[0]['content'])
    current = json.loads(agent.messages[-1]['content'])
    assert current['context']['current_strategy'] == original
    assert current['message'] == '추세는 1시간으로 바꿔'


@pytest.mark.parametrize('scope', ['external', 'local'])
def test_all_safe_vocabulary_and_preset_tools_still_work(scope):
    agent = Agent(Provider(scope=scope), workspace())
    vocabulary = agent.call_tool('vocabulary', {})
    assert 'MA_CROSS' in vocabulary['intent_kinds']
    presets = agent.call_tool('list_specials', {})
    assert presets and all(set(row) <= {'id', 'name'} for row in presets)
    preset = agent.call_tool('describe_special', {'number': 1})
    assert 'recipe' in preset and 'strategy_intent' in preset['recipe']
    assert 'source' not in preset


@pytest.mark.parametrize('scope', ['external', 'local'])
def test_research_interpreter_blocks_reader_and_keeps_canonical_result(scope):
    selected = Provider([
        {'content': '', 'tool_calls': [{'id': 'read-86', 'name': 'search_project_code',
                                      'arguments': {'query': PRIVATE}}]},
        reply(intent()),
    ], scope=scope)
    files = workspace()
    agent = Agent(selected, files)
    result, actions = research_interpreter.interpret(selected, agent, 'strategy', [], {}, '전략 만들어')
    files.search_project_code.assert_not_called()
    assert result['kind'] == 'STRATEGY' and result['strategy'] == intent()
    assert actions[0]['tool'] == 'forbidden_tool' and not actions[0]['ok']
    assert all(not set(FORBIDDEN) & {tool['function']['name'] for tool in tools}
               for _, tools, _ in selected.seen)


@pytest.mark.parametrize('scope', ['external', 'local'])
def test_all_discussion_does_not_read_markdown_manual(monkeypatch, scope):
    original_read = Path.read_text
    def no_manual(path, *args, **kwargs):
        if 'ai_context' in path.parts:
            raise AssertionError('private manual read')
        return original_read(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'read_text', no_manual)
    selected = Provider([reply({'kind': 'CHAT', 'message_ko': '조건을 논의해요.', 'request': None})], scope=scope)
    assert research_chat.respond(selected, 'chat', [], {}, '추세 조건은?')['kind'] == 'CHAT'
    assert 'moses_contract' in json.loads(selected.seen[0][0][0]['content'])


def test_shared_adapter_rechecks_current_settings_and_fails_closed(monkeypatch, tmp_path):
    from common_ai import client
    monkeypatch.setattr(client, 'Client', Mock())
    path = tmp_path / 'settings/ai_settings.json'
    path.parent.mkdir()
    path.write_text(json.dumps({'provider': 'ollama', 'model': 'test:4b'}), encoding='utf-8')
    selected = shared_provider.SharedProvider({'provider': 'ollama', 'model': 'test:4b'}, root=tmp_path)
    assert selected.permission_scope == 'local'
    path.write_text(json.dumps({'provider': 'gemini', 'gemini_model': 'test-remote',
                                'gemini_api_key': 'synthetic_key_86'}), encoding='utf-8')
    assert selected.permission_scope == 'external'
    path.write_text('broken JSON', encoding='utf-8')
    with pytest.raises(ValueError):
        _ = selected.permission_scope
