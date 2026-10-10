"""Qwen translates language into intent; trusted Part3 applies it later."""
from __future__ import annotations

import json
import re
import copy
import threading

from .intent import unsupported, validate_intent, recipe_from_intent
from .tools import ReadOnlyWorkspace, TOOL_SPECS
from common_ai.security import filter_tools, require_tool, safe_tool_result

MAX_TOOL_ROUNDS = 8  # Read-only lookup rounds, not strategy condition count.
CONVERSATION_FIELDS = ('messages', 'candidate', 'original_text', 'pending_clarification',
                       'first_interpretation', 'last_intent', 'revision')


def contract_context(workspace):
    """Public language data only; AI prompts never read project manuals."""
    vocabulary = safe_tool_result('vocabulary', workspace.vocabulary())
    return {'vocabulary': vocabulary,
            'presets': safe_tool_result('list_specials', workspace.list_specials()),
            'capabilities': {'condition_kinds': vocabulary.get('intent_kinds', []),
                             'lifecycle_fields': vocabulary.get('lifecycle_fields', [])}}


def external_system_prompt(workspace):
    return json.dumps({'moses_contract': contract_context(workspace)}, ensure_ascii=False)


def tool_name_for_display(provider, name):
    allowed = {spec['function']['name'] for spec in filter_tools(provider, TOOL_SPECS)}
    if name not in allowed:
        return 'forbidden_tool'
    return name


def system_prompt() -> str:
    """Public strategy contract, identical for all AI Providers."""
    return external_system_prompt(ReadOnlyWorkspace())


def _json_object(content):
    text = re.sub(r'<think>.*?</think>', '', str(content), flags=re.S).strip()
    if text.startswith('```'):
        text = re.sub(r'^```(?:json)?\s*|\s*```$', '', text, flags=re.I).strip()
    value = json.loads(text)
    if not isinstance(value, dict):
        raise ValueError('AI 출력은 JSON 객체여야 합니다.')
    return value


class Agent:
    def __init__(self, provider, workspace: ReadOnlyWorkspace | None = None):
        self.provider = provider
        self.workspace = workspace or ReadOnlyWorkspace()
        self.messages = [{'role': 'system', 'content':
            external_system_prompt(self.workspace)}]
        self.candidate = None
        self.original_text = ''
        self.pending_clarification = None
        self.first_interpretation = None
        self.last_intent = None  # Latest schema-validated conversation draft, independent of apply/cancel.
        self.revision = 0
        self.lock = threading.RLock()

    def call_tool(self, name: str, arguments: dict):
        require_tool(self.provider, name)
        allowed = {spec['function']['name'] for spec in TOOL_SPECS}
        if name not in allowed:
            raise ValueError('AI에는 읽기 전용 조회 도구만 제공됩니다.')
        result = getattr(self.workspace, name)(**(arguments or {}))
        from .schema import output_schema
        return safe_tool_result(name, result, schema=output_schema())

    def send(self, text: str) -> dict:
        with self.lock:
            return self._send(text)

    def checkpoint(self) -> dict:
        """Conversation data only; no provider, tools or external state."""
        with self.lock:
            return copy.deepcopy({name: getattr(self, name) for name in CONVERSATION_FIELDS})

    def restore_checkpoint(self, state: dict) -> None:
        with self.lock:
            for name in CONVERSATION_FIELDS:
                setattr(self, name, copy.deepcopy(state[name]))

    def accept(self, proposed, message: str, actions=None) -> dict:
        """Validate a shared AI result without another AI call or applying it."""
        with self.lock:
            revising = self._begin_turn(message)
            try:
                result = self._validate_proposed(proposed)
            except (ValueError, TypeError):
                result = unsupported('MODEL_OUTPUT_INVALID', 'AI 해석을 검증할 수 없습니다. 표현을 다시 입력해 주세요.')
            return self._finish(result, copy.deepcopy(actions or []), revising)

    def _begin_turn(self, text: str) -> bool:
        message = str(text).strip()
        if not message:
            raise ValueError('전략 설명 또는 수정 요청을 입력하세요.')
        self.revision += 1
        followup = self.pending_clarification is not None
        revising = self.last_intent is not None
        if not followup and not revising:
            self.original_text = message
            self.first_interpretation = None
        self.candidate = None
        # Previous tool transcripts are useful only within their own turn.
        # The latest canonical draft carries strategy meaning across turns.
        self.messages = [{'role': 'system', 'content': external_system_prompt(self.workspace)}]
        if revising or followup:
            self.original_text += ('\n[확인 답변] ' if followup else '\n[수정 요청] ') + message
        self.messages.append({'role': 'user', 'content': json.dumps({
            'message': message, 'context': {'current_strategy': self.last_intent,
                'question': (self.pending_clarification or {}).get('clarification_question')}},
            ensure_ascii=False)})
        return revising

    def _validate_proposed(self, proposed) -> dict:
        if (self.first_interpretation is None and isinstance(proposed, dict)
                and isinstance(proposed.get('interpretation'), dict)):
            self.first_interpretation = copy.deepcopy(proposed['interpretation'])
        # Validate structured output only; never reinterpret the user text.
        return validate_intent(proposed)

    def _finish(self, result: dict, actions: list, revising: bool) -> dict:
        application_error = None
        if result['supported'] and not result['needs_clarification']:
            try:
                recipe_from_intent(result)
            except ValueError as exc:
                application_error = str(exc)[:240]
        self.candidate = copy.deepcopy(result) if result['supported'] and not result['needs_clarification'] and not application_error else None
        if result['supported']:
            self.last_intent = copy.deepcopy(result)
        self.pending_clarification = result if result['supported'] and result['needs_clarification'] else None
        return {'result': result, 'actions': actions, 'can_apply': self.candidate is not None,
                'application_error': application_error, 'revision': self.revision,
                'is_revision': revising}

    def _send(self, text: str) -> dict:
        revising = self._begin_turn(text)
        actions = []
        corrected = False
        for _ in range(MAX_TOOL_ROUNDS):
            reply = self.provider.chat(self.messages, filter_tools(self.provider, TOOL_SPECS))
            calls = reply.get('tool_calls') or []
            assistant = {'role': 'assistant', 'content': reply.get('content') or ''}
            if calls:
                assistant['tool_calls'] = [{'id': c['id'], 'type': 'function', 'function':
                    {'name': c['name'], 'arguments': json.dumps(c.get('arguments') or {}, ensure_ascii=False)}} for c in calls]
            self.messages.append(assistant)
            if calls:
                for call in calls:
                    try:
                        output = {'ok': True, 'result': self.call_tool(call['name'], call.get('arguments') or {})}
                    except (ValueError, TypeError, OSError, AttributeError) as exc:
                        output = {'ok': False, 'error': 'AI 조회 요청을 허용하지 않았습니다.'}
                    display_name = tool_name_for_display(self.provider, call['name'])
                    actions.append({'tool': display_name, 'ok': output['ok'], 'error': output.get('error')})
                    self.messages.append({'role': 'tool', 'tool_call_id': call['id'], 'name': display_name,
                                          'content': json.dumps(output, ensure_ascii=False, default=str)[:16000]})
                continue
            try:
                proposed = _json_object(reply.get('content') or '')
                result = self._validate_proposed(proposed)
            except (ValueError, TypeError, json.JSONDecodeError) as exc:
                if not corrected:
                    corrected = True
                    correction = '조회 설명으로 종료하지 마세요. 원래 전략 요청에 대해 지정된 JSON 객체만 반환하세요.'
                    self.messages.append({'role': 'user', 'content':
                        json.dumps({'message': correction}, ensure_ascii=False)})
                    continue
                result = unsupported('MODEL_OUTPUT_INVALID', 'AI 해석을 검증할 수 없습니다. 표현을 다시 입력해 주세요.')
            return self._finish(result, actions, revising)
        return {'result': unsupported('MODEL_OUTPUT_INVALID', 'AI 조회가 완료되지 않았습니다. 다시 요청해 주세요.'),
                'actions': actions, 'can_apply': False, 'revision': self.revision,
                'is_revision': revising}

    def apply(self, revision=None):
        with self.lock:
            if revision is not None and (type(revision) is not int or revision != self.revision):
                raise ValueError('해석 결과가 변경되었습니다. 최신 해석을 다시 확인해 주세요.')
            if self.candidate is None:
                raise ValueError('화면에 적용할 확정 전략 의도가 없습니다.')
            return recipe_from_intent(self.candidate)

    def cancel(self):
        with self.lock:
            self.candidate = None
