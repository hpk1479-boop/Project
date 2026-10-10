"""One AI response carries the operation and its complete validated payload."""
from __future__ import annotations

import copy
import json

from .agent import MAX_TOOL_ROUNDS, _json_object, external_system_prompt, tool_name_for_display
from common_ai.security import filter_tools, require_tool, safe_tool_result
from common_ai.external_errors import ReplyValidationError
from .schema import output_schema
from .intent import validate_intent
from .tools import TOOL_SPECS
from . import backtest_commands as commands
from .research_backtest import plan_schema, validate_plan_shape


def response_schema(mode):
    # The canonical strategy schema is embedded intact, once. The envelope only
    # transports it alongside an existing job plan; it adds no strategy rules.
    canonical = output_schema()
    definitions = canonical.pop('$defs', {})
    plan = plan_schema()
    plan['properties']['strategy_text'] = {'type': 'null'}
    step = plan['properties']['steps']['items']
    existing = copy.deepcopy(step)
    existing['properties']['draft'] = {'const': False}
    draft = copy.deepcopy(step)
    draft['properties']['draft'] = {'const': True}
    command = draft['properties']['command']
    command['properties']['action'] = {'const': 'START'}
    request = command['properties']['request']
    request['type'] = 'object'
    request['required'] = ['target_mode']
    request['properties']['target_mode'] = {'const': 'GENERATED'}
    plan['properties']['steps']['items'] = {'anyOf': [existing, draft]}
    backtest = {'type': 'object', 'additionalProperties': False,
            'required': ['kind', 'strategy', 'plan', 'message_ko'], 'properties': {
                'kind': {'const': 'BACKTEST'},
                'strategy': {'anyOf': [copy.deepcopy(canonical), {'type': 'null'}]},
                'plan': plan,
                'message_ko': {'type': 'string'}}}
    variants = [canonical, backtest]
    if mode == 'chat':
        variants.append({'type': 'object', 'additionalProperties': False,
            'required': ['kind', 'strategy', 'plan', 'message_ko'], 'properties': {
                'kind': {'const': 'CHAT'}, 'strategy': {'type': 'null'},
                'plan': {'type': 'null'}, 'message_ko': {'type': 'string'}}})
    return {'anyOf': variants, '$defs': definitions}


PROMPT = '''AI 전략연구 요청을 한 번에 해석한다. 종류만 분류하거나 요청을 다른 문장으로 바꾸지 않는다.
새 전략 조건 또는 현재 조건 수정은 기존 canonical JSON 자체만 반환한다.
supported, intent, interpretation, needs_clarification, clarification_question, message_ko가 최상위 필드다.
전략 결과를 kind/strategy/plan 객체로 감싸거나 대화 문장으로 바꾸지 않는다.
필수 필드와 전략 의미에 필요한 필드만 담는다. 의미에 필요 없는 선택 필드의 기본값·빈 배열·빈 객체를 반복해서 채우지 않는다.
백테스트와 챗에만 kind, strategy, plan, message_ko 네 필드를 쓴다.
한 방향 조건이면 전략 direction도 그 방향으로 유지하고 반대 방향 조건이나 분기를 추가하지 않는다.
독립 branches를 사용하는 경우 interpretation.steps는 빈 목록이다. 각 branch에 그 분기의 전체 조건을 넣는다.
BACKTEST: 백테스트/데이터 구축/작업 조회·중지·재연결 요청. plan에 기존 전체 작업 계획을 쓴다.
새 조건을 백테스트하면 동일 응답의 strategy에 전체 canonical을 쓰고 해당 작업 draft=true다.
기존 SPECIAL/생성 파일 또는 이미 해석한 현재 전략의 백테스트는 strategy=null이다.
사용자가 기존 SPECIAL이나 생성 파일을 지정하면 draft=false다. 현재 연구 전략을 대신 실행하지 않는다.
백테스트/데이터 구축 시작은 command.action=START다. 중지=STOP, 상태=STATUS, 목록=RECENT, 재연결=RECONNECT, 결과 표=REPORT다.
여러 전략 × 올존 트리거 조합은 START 하나에 triggers 목록(생성 전략 여러 개는 filenames 목록)으로 쓴다. 조합마다 작업을 나누지 않는다.
기존 SPECIAL은 command.request.target_mode=SPECIAL, specials에 선택한 이름을 쓴다.
현재 연구 전략은 draft=true, target_mode=GENERATED, filename=null이다. 기존 생성 파일은 draft=false, target_mode=GENERATED다.
command.request.start와 end는 YYYY-MM-DD 형식이다. 시각이나 시간대 문자열을 붙이지 않는다.
최근 N일/개월/년 백테스트는 선택 정보의 today 기준 기간을 유지하고 command.request.available_only=true다.
이는 요청 기간 안의 검증된 보유 구간만 실행하며 자동 구축하지 않는 정책이다. 보유 마지막 날짜로 요청 기간을 옮기지 않는다.
데이터 구축·재구축이나 오늘까지 새 데이터를 채우라고 명시하면 available_only=false다. 일반 명시 기간의 기본값은 false이며 수정 요청은 기존 정책도 보존한다.
plan.strategy_text는 항상 null이다. 자연어를 다시 해석하도록 전달하지 않는다.
CHAT은 mode=chat에서 대화·설명·아이디어 논의에만 사용하고 strategy=null, plan=null, message_ko에 답한다.
mode=strategy에서는 채팅으로 답하지 않는다. 전략 결과나 실행 계획의 확인 질문으로 처리한다.
chat에서도 사용자가 실제 전략 생성·수정을 요청하면 기존 전체 canonical 자체를 반환한다. 백테스트는 BACKTEST 전체 계획이다.
discussion은 참고 대화이며 제안만으로 적용하지 않는다. 실제 선택·요청한 조건만 해석한다.
current_strategy가 있으면 수정 요청은 그 전체 intent에서 언급한 부분만 변경한다.
previous_plan이 있으면 언급하지 않은 날짜·종목·순서와 현재 전략은 보존한다.
조건 시간봉과 최종 OZ 시간봉은 각각 원문 그대로 보존하며 같은 시간봉으로 바꾸지 않는다.
시간 제한을 말하지 않았다는 이유만으로 질문하지 않는다. 조건·시간을 임의로 추가하지 않는다.
적용·파일 생성·실행은 사용자 확인 뒤 프로그램이 한다. 조회 도구만 사용할 수 있다.
아래는 기존 전략 canonical과 백테스트 plan 계약이다. 전략 출력은 기존 canonical 원형을 유지한다.
'''

CHAT_MODE_PROMPT = '''현재 화면은 챗 모드다. 위의 전략 통역기 계약은 전략 작성을 명시적으로 요청했을 때만 사용하는 참고 계약이다.
이 모드의 역할은 한국어 대화와 전략 연구 설명이다. 인사, 질문, 설명, 의견·아이디어 논의는 kind=CHAT으로 답한다.
current_strategy는 참고용 초안이다. 초안이 있다는 이유만으로 전략 JSON을 다시 반환하거나 조건을 바꾸지 않는다.
전략 제안·가정·비교만으로 현재 전략을 수정하지 않는다. 사용자가 작성·해석·수정을 명시적으로 요청한 경우에만 전체 canonical을 반환한다.
백테스트·데이터 구축·작업 조회·중지 요청에는 BACKTEST 전체 계획을 반환한다. 실행 요청을 자연어로 다시 쓰거나 별도 분류를 하지 않는다.
CHAT 출력은 {"kind":"CHAT","strategy":null,"plan":null,"message_ko":"한국어 답변"}이다.
없는 기능이나 백테스트 수치를 만들지 않는다. 적용·파일 생성·실행은 사용자 확인 전에는 하지 않는다.'''


def selection(snapshot):
    value = commands._safe_snapshot(snapshot)
    # Full execution settings stay in the trusted normalizer. The AI selects
    # commands and targets, and never rewrites the calculation settings.
    allowed = ('symbol', 'symbols', 'specials', 'selected_specials', 'mode', 'result_mode', 'spread_points', 'virtual_profiles')
    value['options'] = {key: item for key, item in value['options'].items() if key in allowed}
    return value


def _validate(value, mode):
    if isinstance(value, dict) and 'supported' in value and 'kind' not in value:
        return {'kind': 'STRATEGY', 'strategy': value, 'plan': None, 'message_ko': ''}
    if not isinstance(value, dict) or set(value) != {'kind', 'strategy', 'plan', 'message_ko'}:
        raise ValueError('AI 연구 결과 형식이 올바르지 않습니다.')
    if not isinstance(value['message_ko'], str):
        raise ValueError('AI 안내는 한국어 문장이어야 합니다.')
    kind = value['kind']
    if kind == 'CHAT':
        if not value['message_ko'].strip():
            raise ValueError('AI 대화 응답이 비어 있습니다.')
        if len(value['message_ko']) > 8000:
            raise ValueError('AI 대화 응답이 너무 깁니다. 짧게 다시 요청하세요.')
        if mode != 'chat' or value['strategy'] is not None or value['plan'] is not None:
            raise ValueError('전략생성에서는 전체 전략 해석 결과를 반환해야 합니다.')
    elif kind == 'STRATEGY':
        if not isinstance(value['strategy'], dict) or value['plan'] is not None:
            raise ValueError('전체 전략 해석 결과가 없습니다.')
    elif kind == 'BACKTEST':
        if not isinstance(value['plan'], dict) or (value['strategy'] is not None and not isinstance(value['strategy'], dict)):
            raise ValueError('전체 백테스트 계획이 없습니다.')
        if value['plan'].get('strategy_text') is not None:
            raise ValueError('전략 조건은 전체 canonical 결과로 반환해야 합니다.')
        validate_plan_shape(value['plan'])
        if value['strategy'] is not None and not any(row.get('draft') is True for row in value['plan'].get('steps', []) if isinstance(row, dict)):
            raise ValueError('새 전략 해석을 실행할 작업에 연결하세요.')
    else:
        raise ValueError('AI 연구 요청 종류가 올바르지 않습니다.')
    return value


def interpret(provider, agent, mode, history, context, message):
    # Keep the existing strategy/plan contracts intact. Their standalone roles
    # apply only to the selected operation, under this research mode's role.
    system = external_system_prompt(agent.workspace)
    messages = [{'role': 'system', 'content': system}, {'role': 'user', 'content': json.dumps({
        'mode': mode, 'context': context, 'discussion': history, 'message': message}, ensure_ascii=False, default=str)}]
    actions = []
    corrected = False
    for _ in range(MAX_TOOL_ROUNDS):
        try:
            reply = provider.chat(messages, filter_tools(provider, TOOL_SPECS), response_schema=response_schema(mode))
        except ReplyValidationError as exc:
            if corrected:
                raise
            # Use the existing single correction allowance for schema failures
            # detected by the transport, too. Never repair fields locally or
            # accept the failed reply as an intent/tool call. The same provider
            # receives the same complete contract and validates the next reply.
            corrected = True
            messages.append({'role': 'assistant', 'content': exc.reply_text})
            messages.append({'role': 'user', 'content': json.dumps({'message':
                str(exc) + '\n직전 응답이 원래 MOSES schema 검증을 통과하지 못했습니다. '
                '원래 사용자 요청과 현재 전략의 의미를 보존하고 잘못된 구조만 교정하세요. '
                '조건·방향·시간봉·순서·객체 참조를 추가하거나 바꾸지 마세요. '
                '전체 응답 JSON을 같은 schema로 다시 반환하세요.'}, ensure_ascii=False)})
            continue
        calls = reply.get('tool_calls') or []
        if not calls:
            value = _validate(_json_object(reply.get('content') or ''), mode)
            if value['strategy'] is not None:
                try:
                    validate_intent(value['strategy'])
                except (ValueError, TypeError) as exc:
                    if corrected:
                        raise ValueError('AI 해석을 검증할 수 없습니다. 표현을 다시 입력해 주세요.') from exc
                    # Reuse the existing validation-repair contract. This is a
                    # correction within one interpretation, never classification
                    # or a second natural-language strategy request.
                    corrected = True
                    messages.append({'role': 'assistant', 'content': reply.get('content') or ''})
                    correction = '전체 전략 schema 검증 오류. ' + (
                        '\n원래 요청과 직전 canonical의 의미를 보존하고 잘못된 구조만 교정하세요. '
                        '조건·방향·시간봉을 임의로 추가하거나 바꾸지 마세요. 전체 연구 JSON만 반환하세요.')
                    messages.append({'role': 'user', 'content':
                        json.dumps({'message': correction}, ensure_ascii=False)})
                    continue
            return copy.deepcopy(value), actions
        messages.append({'role': 'assistant', 'content': reply.get('content') or '', 'tool_calls': [
            {'id': call['id'], 'type': 'function', 'function': {'name': call['name'],
             'arguments': json.dumps(call.get('arguments') or {}, ensure_ascii=False)}} for call in calls]})
        for call in calls:
            try:
                require_tool(provider, call['name'])
                result = agent.call_tool(call['name'], call.get('arguments') or {})
                result = safe_tool_result(call['name'], result, schema=output_schema())
                output = {'ok': True, 'result': result}
            except (ValueError, TypeError, OSError, AttributeError) as exc:
                output = {'ok': False, 'error': 'AI 조회 요청을 허용하지 않았습니다.'}
            display_name = tool_name_for_display(provider, call['name'])
            actions.append({'tool': display_name, 'ok': output['ok'], 'error': output.get('error')})
            messages.append({'role': 'tool', 'tool_call_id': call['id'], 'name': display_name,
                'content': json.dumps(output, ensure_ascii=False, default=str)[:16000]})
    raise ValueError('AI 조회가 완료되지 않았습니다. 다시 요청해 주세요.')
