"""Exercise actual egress boundaries with synthetic data and captured HTTP."""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import sys
import time
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'Part3')]

from common_ai import gemini, model_runtime, settings
from common_ai.gemini import Gemini
from common_ai.process_identity import identity
from common_ai.security import external_payload, safe_tool_result
from common_ai.service import Service
from lab.ai.schema import output_schema
from lab.ai.tools import ReadOnlyWorkspace, TOOL_SPECS
from lab.ai.research_interpreter import response_schema


KEY = 'synthetic-egress-credential-86'
PRIVATE = 'synthetic-private-file-content-86'
SETTINGS = {'provider': 'gemini', 'gemini_model': 'test-model', 'gemini_api_key': KEY, 'timeout': 90}


def canonical():
    return {'supported': True, 'intent': 'CREATE_STRATEGY', 'interpretation': {
        'direction': 'LONG', 'symbols': ['XAUUSD+'],
        'steps': [{'kind': 'FVG_STATE', 'tfs': ['15m'], 'side': 'BULL', 'state': 'AREA'},
                  {'kind': 'MA_CROSS', 'tfs': ['1m'], 'ma_left': 'WMA17', 'ma_right': 'EMA50'}],
        'order_mode': 'SEQUENTIAL', 'within_sec': None,
        'lifecycle': {'expires': {'seconds': 3600}, 'snapshots': {
            'atr_anchor': {'tf': '15m', 'field': 'ATR14', 'bar_state': 'CLOSED'}}},
        'time_filters': {'MAIN_LONDON': '1600-1800'},
        'final_time_filters': {'MAIN_NEWYORK': {'enabled': True, 'start': '2100', 'end': '2400'}},
        'final': {'kind': 'OZ', 'tfs': ['1m'], 'validation_mode': 'BLIND', 'trigger_mode': 'BREAKER'}},
        'needs_clarification': False, 'clarification_question': None, 'message_ko': '현재 전략'}


def messages():
    workspace = ReadOnlyWorkspace()
    return [
        {'role': 'system', 'content': '# old local manual\n' + PRIVATE},
        {'role': 'system', 'content': json.dumps({'moses_contract': {
            'vocabulary': workspace.vocabulary(), 'presets': workspace.list_specials(),
            'runtime': PRIVATE, 'connections': {'password': KEY}}})},
        {'role': 'assistant', 'content': PRIVATE},
        {'role': 'assistant', 'content': '', 'tool_calls': [{'id': PRIVATE, 'function': {
            'name': 'read_project_code', 'arguments': json.dumps({'path': 'Part1/program/private.py'})}}]},
        {'role': 'tool', 'name': 'read_project_code', 'tool_call_id': PRIVATE,
         'content': json.dumps({'ok': True, 'result': {'text': PRIVATE}})},
        {'role': 'user', 'content': json.dumps({'mode': 'strategy', 'message': '추세를 바꿔',
            'context': {'current_strategy': canonical(), 'question': '시간봉은?', 'logs': PRIVATE,
                        'runtime': PRIVATE, 'account': 'synthetic-account', 'password': KEY},
            'discussion': [{'role': 'assistant', 'content': PRIVATE},
                           {'role': 'tool', 'content': PRIVATE}, {'role': 'user', 'content': '1분으로'}]})},
    ]


def test_allowlist_drops_private_transcripts_and_keeps_exact_canonical_semantics():
    original_messages, original_tools, schema = messages(), copy.deepcopy(TOOL_SPECS), output_schema()
    before = copy.deepcopy((original_messages, original_tools, schema))
    filtered, tools, filtered_schema = external_payload(original_messages, original_tools, schema, settings=SETTINGS)
    payload = json.dumps((filtered, tools, filtered_schema), ensure_ascii=False)
    assert PRIVATE not in payload and KEY not in payload
    assert 'synthetic-account' not in payload
    assert not {'read_doc', 'read_project_code', 'search_project_code'} & {row['function']['name'] for row in tools}
    request = json.loads(filtered[-1]['content'])
    assert request['context']['current_strategy'] == canonical()
    assert request['context']['question'] == '시간봉은?'
    assert request['discussion'] == [{'role': 'user', 'content': '1분으로'}]
    assert (original_messages, original_tools, schema) == before
    assert filtered_schema == schema
    from jsonschema import Draft202012Validator
    Draft202012Validator(filtered_schema).validate(canonical())


def test_service_and_transport_filtering_is_idempotent():
    first = external_payload(messages(), TOOL_SPECS, output_schema(), settings=SETTINGS)
    assert external_payload(*first, settings=SETTINGS) == first


@pytest.mark.parametrize('role', ['watch', 'backtest'])
def test_safe_limited_roles_keep_no_tools_and_only_command_selection(role):
    schema = {'type': 'object', 'properties': {'canonical_text': {'type': 'string'}}}
    selection = {'today': '2026-10-02', 'options': {'symbols': ['XAUUSD+'], 'runtime': PRIVATE},
                 'jobs': [{'job_id': 'a' * 32, 'phase': 'run', 'logs': PRIVATE}],
                 'generated': [{'filename': 'Test_One.py', 'path': PRIVATE}], 'data': PRIVATE}
    content = ('현재 읽기 전용 선택 정보:\n' + json.dumps(selection)) if role == 'backtest' else PRIVATE
    filtered = external_payload([{'role': 'system', 'content': content},
        {'role': 'user', 'content': '15분 상승추세'}], [], schema, role=role, settings=SETTINGS)
    assert filtered[1] == []
    assert PRIVATE not in json.dumps(filtered, ensure_ascii=False)
    second = external_payload(*filtered, settings=SETTINGS)
    assert second == filtered
    if role == 'backtest':
        assert json.loads(filtered[0][0]['content'])['moses_contract']['selection']['jobs'] == [
            {'job_id': 'a' * 32, 'phase': 'run'}]


def test_schema_and_tool_annotations_cannot_smuggle_documents_or_secrets():
    schema = {'type': 'object', 'description': PRIVATE, 'examples': [PRIVATE], 'properties': {
        'canonical_text': {'type': 'string', 'description': PRIVATE},
        'api_key': {'const': KEY}, 'source_text': {'const': PRIVATE}}}
    tools = copy.deepcopy(TOOL_SPECS)
    tools[0]['function']['description'] = PRIVATE
    filtered = external_payload([{'role': 'user', 'content': '전략 해석'}], tools, schema, settings=SETTINGS)
    assert PRIVATE not in json.dumps(filtered) and KEY not in json.dumps(filtered)
    assert set(filtered[2]['properties']) == {'canonical_text'}


def test_user_pasted_config_credentials_are_filtered_without_changing_conditions():
    from common_ai.security import redact
    config = 'MT5_LOGIN=123456 MT5_PASSWORD=synthetic_mt5_secret_86 TELEGRAM_TOKEN=synthetic_telegram_token_86'
    request = config + ' validation_mode=BLIND 15분 상승추세'
    rows, _, _ = external_payload([{'role': 'user', 'content': request}], [], output_schema(), settings=SETTINGS)
    encoded = json.dumps(rows)
    assert '123456' not in encoded
    assert 'synthetic_mt5_secret_86' not in encoded
    assert 'synthetic_telegram_token_86' not in encoded
    assert 'validation_mode=BLIND' in rows[-1]['content']
    assert '15분 상승추세' in rows[-1]['content']
    assert 'synthetic-bearer' not in redact('Authorization: Bearer synthetic-bearer')
    assert redact({'apiKey': 'synthetic-other-key', 'safe': 'synthetic-other-key'}) == {'safe': '[REDACTED]'}


def test_preset_summary_drops_legacy_source_and_preserves_common_recipe():
    value = {'id': 'PRESET_A', 'name': '공통 전략', 'source': PRIVATE, 'recipe': {
        'schema_version': 2, 'base': 'AI', 'name': '전략', 'description': '전략 요약',
        'symbols': ['XAUUSD+'], 'strategy_intent': canonical()['interpretation'],
        'source_text': PRIVATE, 'runtime': PRIVATE, 'connections': {'password': KEY}}}
    summary = safe_tool_result('describe_special', value, schema=output_schema())
    assert summary['recipe']['strategy_intent'] == canonical()['interpretation']
    assert PRIVATE not in json.dumps(summary) and KEY not in json.dumps(summary)


@pytest.mark.parametrize('name,result', [('vocabulary', []), ('describe_special', []), ('list_specials', {})])
def test_malformed_safe_tool_results_are_rejected(name, result):
    with pytest.raises(ValueError, match='조회 결과 형식'):
        safe_tool_result(name, result)


class Reply:
    def __enter__(self):
        return self
    def __exit__(self, *_):
        return False
    def read(self, _limit):
        return json.dumps({'candidates': [{'finishReason': 'STOP', 'content': {
            'parts': [{'text': json.dumps(canonical(), ensure_ascii=False)}]}}]}).encode('utf-8')


def test_actual_gemini_http_body_contains_contract_but_no_internal_material(monkeypatch):
    sent = []
    monkeypatch.setattr(model_runtime.RUNTIME, 'remote_chat',
        lambda provider, operation: operation(time.monotonic() + provider.timeout))
    def capture(request, *, timeout):
        sent.append(request)
        return Reply()
    monkeypatch.setattr(gemini, '_open_request', capture)
    result = Gemini(SETTINGS).chat(messages(), TOOL_SPECS, response_schema=output_schema())
    body = sent[0].data.decode('utf-8')
    assert PRIVATE not in body and KEY not in body
    assert 'MA_CROSS' in body and 'SMA' in body and 'order_mode' in body and 'bar_state' in body
    assert 'read_project_code' not in body and 'read_doc' not in body
    assert sent[0].get_header('X-goog-api-key') == KEY
    assert json.loads(result['content']) == canonical()


@pytest.mark.parametrize('provider', ['gemini', 'future_remote', 'ollama', 'local_gguf', 'local_lora'])
def test_service_uses_current_provider_settings_as_the_authority(monkeypatch, tmp_path, provider):
    chosen = dict(SETTINGS, provider=provider)
    monkeypatch.setattr(settings, 'read_settings', lambda root: dict(chosen))
    seen = []
    class Model:
        permission_scope = 'local'  # A transport cannot override configured policy.
        def chat(self, rows, tools, *, response_schema):
            seen.append(copy.deepcopy((rows, tools, response_schema)))
            return {'content': json.dumps(canonical()), 'tool_calls': []}
    service = Service(tmp_path, provider_factory=lambda _: Model(), runtime=SimpleNamespace(generation=0))
    service.clients['test'] = {'pid': os.getpid(), 'created': identity(os.getpid())}
    rows = messages()
    original_rows = copy.deepcopy(rows)
    try:
        service.chat({'client_id': 'test', 'generation': 0, 'role': 'strategy', 'messages': rows,
                      'tools': TOOL_SPECS, 'response_schema': output_schema()})
        from common_ai.external_prompt import _LANGUAGE_REFERENCE_PREFIX
        sent_rows, sent_tools, sent_schema = seen[0]
        assert PRIVATE not in json.dumps(seen[0]) and KEY not in json.dumps(seen[0])
        assert not {'read_doc', 'read_project_code', 'search_project_code'} & {
            row['function']['name'] for row in sent_tools}
        references = [row for row in sent_rows if row.get('content', '').startswith(_LANGUAGE_REFERENCE_PREFIX)]
        if provider in ('ollama', 'local_gguf', 'local_lora'):
            assert len(references) == 1
            terms_text = references[0]['content'][len(_LANGUAGE_REFERENCE_PREFIX):]
            assert len(terms_text) <= 900
            assert json.loads(terms_text)['conditions']['TREND'] == ['추세']
        assert 'moses_contract' in json.loads(sent_rows[0]['content'])
        current = json.loads(sent_rows[-1]['content'])
        assert current['context']['current_strategy'] == canonical()
        assert current['context']['question'] == '시간봉은?'
        assert sent_schema == output_schema()
        assert rows == original_rows
    finally:
        service.server.server_close()


def test_research_plan_and_strategy_survive_same_external_schema(monkeypatch):
    schema = response_schema('strategy')
    plan = {'strategy_text': None, 'needs_clarification': False, 'clarification_question': None, 'steps': [
        {'draft': True, 'command': {'supported': True, 'action': 'START', 'job_id': None,
            'needs_clarification': False, 'clarification_question': None, 'message_ko': '백테스트', 'request': {
                'target_mode': 'GENERATED', 'symbol': 'XAUUSD+', 'start': '2026-01-01', 'end': '2026-02-01'}}}]}
    rows = [{'role': 'user', 'content': json.dumps({'message': '같은 전략을 한 달 백테스트',
        'context': {'current_strategy': canonical(), 'previous_plan': plan}})}]
    result = external_payload(rows, [], schema, settings=SETTINGS)
    context = json.loads(result[0][-1]['content'])['context']
    assert context == {'current_strategy': canonical(), 'previous_plan': plan}
    assert result[2] == schema


@pytest.mark.parametrize('provider', ['gemini', 'ollama', 'local_gguf', 'local_lora'])
@pytest.mark.parametrize('error', [False, True])
def test_service_masks_provider_credential_echo_in_responses_and_errors(monkeypatch, tmp_path, error, capsys, provider):
    monkeypatch.setattr(settings, 'read_settings', lambda root: dict(SETTINGS, provider=provider))
    class Model:
        def chat(self, *_args, **_kwargs):
            if error:
                raise ValueError('transport failed: ' + KEY)
            encoded = ''.join('\\u%04x' % ord(char) for char in KEY)
            return {'content': '{"message_ko":"' + encoded + '"}', 'tool_calls': []}
    service = Service(tmp_path, provider_factory=lambda _: Model(), runtime=SimpleNamespace(generation=0))
    service.clients['test'] = {'pid': os.getpid(), 'created': identity(os.getpid())}
    try:
        payload = {'client_id': 'test', 'generation': 0, 'role': 'strategy',
                   'messages': [{'role': 'user', 'content': '전략 해석'}], 'tools': [], 'response_schema': output_schema()}
        if error:
            with pytest.raises(ValueError) as caught:
                service.chat(payload)
            assert KEY not in str(caught.value) and '[REDACTED]' in str(caught.value)
        else:
            reply = service.chat(payload)
            assert json.loads(reply['content']) == {'message_ko': '[REDACTED]'}
        output = capsys.readouterr()
        assert KEY not in output.out + output.err
    finally:
        service.server.server_close()
