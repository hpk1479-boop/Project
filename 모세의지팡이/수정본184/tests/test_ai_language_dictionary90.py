"""AI reads bounded shared language references, without network or model loads."""
import copy
import json
import os
from pathlib import Path
import threading
from types import SimpleNamespace

import pytest

import moses_language as language
from common_ai import external_prompt, security, service as service_module
from common_ai.process_identity import identity
from common_ai.reply_format import reply_schema
from common_ai.schema_contract import contract_schema


SCHEMA = {'type': 'object', 'additionalProperties': False,
          'required': ['canonical_text'], 'properties': {'canonical_text': {'type': 'string'}}}
TOOLS = [{'type': 'function', 'function': {'name': name, 'parameters': {'type': 'object'}}}
         for name in ('vocabulary', 'read_project_code', 'search_project_code')]


@pytest.fixture(autouse=True)
def reset_shared_cache():
    language.cache_clear()
    yield
    language.cache_clear()


def request(text, purpose='strategy'):
    return [{'role': 'system', 'content': json.dumps({'purpose': purpose,
        'moses_contract': {'vocabulary': {'ma': {'families': ['SMA', 'WMA', 'EMA', 'HMA']}}}})},
        {'role': 'user', 'content': json.dumps({'message': text, 'mode': 'strategy', 'context': {}})}]


def _reference(rows):
    return next(row for row in rows if row.get('content', '').startswith(external_prompt._LANGUAGE_REFERENCE_PREFIX))


def _custom(tmp_path, monkeypatch):
    raw = json.loads(language.language_path().read_text(encoding='utf-8'))
    raw['condition_terms']['WONBI_TOUCH'].append('통통')
    raw['command_language']['phrase_aliases']['원비'].append('통통')
    raw['concepts']['wonbi'].append('통통')
    raw['syntax_literals']['private.regex'] = 'private_parser_marker'
    raw['runtime'] = {'api_key': 'private_settings_marker'}
    path = tmp_path / 'language.json'
    path.write_text(json.dumps(raw, ensure_ascii=False), encoding='utf-8')
    monkeypatch.setattr(language, '__file__', str(tmp_path / '__init__.py'))
    return path


def test_external_and_local_read_same_new_dictionary_alias(tmp_path, monkeypatch):
    _custom(tmp_path, monkeypatch)
    rows = request('15분 통통이면 1분 올존')
    local = external_prompt.attach_language_reference(rows)
    external, tools, schema = external_prompt.prepare(rows, TOOLS, SCHEMA, {'provider': 'gemini'})
    assert _reference(local) == _reference(external)
    assert 'WONBI_TOUCH' in _reference(local)['content']
    assert '통통' in _reference(local)['content']
    assert language.command_language()['phrase_aliases']['원비'][-1] == '통통'
    assert {spec['function']['name'] for spec in tools} == {'vocabulary'}
    assert schema == SCHEMA


def test_reference_keeps_full_schema_rules_and_existing_example_selection():
    rows = request('15분 상승추세에 1분 올존')
    output, _, schema = external_prompt.prepare(rows, [], SCHEMA, {'provider': 'gemini'})
    prefix = 'JSON response schema: '
    transmitted, _ = json.JSONDecoder().raw_decode(output[0]['content'].split(prefix, 1)[1])
    assert transmitted == contract_schema(reply_schema(SCHEMA, []))
    assert 'within-N and active-for-M are separate windows' in output[0]['content']
    from common_ai.reference_pack import select_examples
    selected = next(row for row in output if row['content'].startswith('Reference examples'))
    expected = [{'input': e['input'], 'meaning': e['meaning']} for e in select_examples(
        '15분 상승추세에 1분 올존', limit=2, max_chars=900)]
    assert json.loads(selected['content'].split(': ', 1)[1]) == expected
    assert json.loads(output[-1]['content']) == json.loads(rows[-1]['content'])
    assert schema == SCHEMA


def test_reference_is_bounded_complete_and_does_not_send_all_examples(tmp_path, monkeypatch):
    _custom(tmp_path, monkeypatch)
    terms = external_prompt.language_reference('15분 통통이면 1분 올존')
    content = json.dumps(terms, ensure_ascii=False, separators=(',', ':'))
    assert len(content) <= 900
    assert len(external_prompt.language_reference('15분 통통', max_chars=2)) == 0
    assert terms['conditions']['WONBI_TOUCH'] == ['통통']
    assert not any(marker in content for marker in ('private_parser_marker', 'private_settings_marker',
        'defaults', 'provenance', 'examples', 'syntax_literals', 'runtime'))
    assert external_prompt.language_reference('#088') == {}


def test_macro_reference_contains_name_without_old_execution_contract():
    reference = external_prompt.language_reference('15분 기본 더블비에 1분 올존')
    body = json.dumps(reference, ensure_ascii=False)
    assert '기본더블비' in body
    assert '$tf' not in body and '$direction' not in body
    assert 'conditions' in reference
    assert 'combination' not in body and '"kind"' not in body


def test_multiple_requests_read_dictionary_once(monkeypatch):
    original, reads = Path.open, []

    def counted(path, *args, **kwargs):
        if path.name == 'language.json':
            reads.append(path)
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'open', counted)
    language.command_language()
    external_prompt.attach_language_reference(request('15분 원비 1분 올존'))
    external_prompt.prepare(request('5분 FVG 생기면 1분 올존'), [], SCHEMA, {'provider': 'gemini'})
    external_prompt.prepare(request('1분 50 200 골크'), [], SCHEMA, {'provider': 'gemini'})
    assert len(reads) == 1


@pytest.mark.parametrize('purpose', ['watch', 'strategy', 'backtest'])
def test_purposes_remain_distinct_and_watch_does_not_get_training_examples(purpose):
    rows = request('15분 원비 1분 올존', purpose)
    output, _, schema = external_prompt.prepare(rows, [], SCHEMA, {'provider': 'gemini'})
    assert _reference(output)
    assert schema == SCHEMA
    if purpose == 'watch':
        assert output[0]['content'].startswith('WATCH 보조 해석이다.')
        assert not any(row['content'].startswith('Reference examples') for row in output)
    elif purpose == 'backtest':
        assert '백테스트 명령 전용이다.' in output[0]['content']


@pytest.mark.parametrize('provider_name', ['gemini', 'ollama', 'local_gguf'])
def test_service_all_connections_share_public_contract_tool_permissions(monkeypatch, provider_name):
    observed = []
    provider = SimpleNamespace(chat=lambda rows, tools, response_schema: (
        observed.append((rows, tools, response_schema)) or {'content': '{}', 'tool_calls': []}))
    owner = service_module.Service.__new__(service_module.Service)
    owner.clients = {'test': {'pid': os.getpid(), 'created': identity(os.getpid())}}
    owner.client_lock = threading.Lock()
    owner.settings_lock = threading.Lock()
    owner.turn_lock = threading.Lock()
    owner.runtime = SimpleNamespace(generation=0)
    owner.closing = False
    owner.provider = provider
    owner.provider_generation = 0
    owner._sync_settings = lambda: ({'provider': provider_name, 'model': 'arbitrary-model', 'timeout': 90}, 0)
    messages = [{'role': 'user', 'content': '15분 원비에 1분 올존'}]
    owner.chat({'client_id': 'test', 'generation': 0, 'role': 'watch',
        'messages': messages, 'tools': TOOLS, 'response_schema': SCHEMA})
    rows, tools, schema = observed[0]
    assert rows[-1] == messages[-1]
    assert 'moses_contract' in json.loads(rows[0]['content'])
    if provider_name != 'gemini':
        assert _reference(rows)
    assert {spec['function']['name'] for spec in tools} == {'vocabulary'}
    assert schema == SCHEMA
    assert [spec['function']['name'] for spec in security.filter_tools(provider_name, TOOLS)] == ['vocabulary']


def test_local_reference_does_not_mutate_input_or_last_user_request():
    rows = request('원비는 15분으로 수정해')
    before = copy.deepcopy(rows)
    output = external_prompt.attach_language_reference(rows)
    assert rows == before
    assert output[0] == rows[0] and output[-1] == rows[-1]


def test_command_phrase_rewrites_are_not_ai_semantic_aliases():
    reference = external_prompt.language_reference('골크 후 30분 이내에 FVG 생기면 올존')
    assert 'phrase_aliases' not in reference
    assert '동안' not in json.dumps(reference, ensure_ascii=False)
    assert '이내' in language.command_language()['phrase_aliases']['동안']
