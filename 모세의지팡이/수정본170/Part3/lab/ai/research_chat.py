"""Read-only discussion, with explicit requests handed to existing interpreters."""
from __future__ import annotations

import json

from .tools import ReadOnlyWorkspace
from .agent import external_system_prompt


def chat_schema():
    return {'type': 'object', 'additionalProperties': False,
            'required': ['kind', 'message_ko', 'request'], 'properties': {
                'kind': {'enum': ['CHAT', 'STRATEGY', 'BACKTEST']},
                'message_ko': {'type': 'string'},
                'request': {'type': ['string', 'null']}}}


PROMPT = '''너는 MOSES AI 전략연구의 대화 담당이다. 한국어로 자연스럽게 설명하고 질문한다.
chat 모드에서는 전략 아이디어, 조건의 의미와 차이, 개선안, 백테스트 결과와 실행 방법을 함께 논의한다.
전략을 제안하거나 조건을 가정해서 설명할 수 있지만, 논의만으로 현재 전략을 수정하지 않는다.
사용자가 명시적으로 전략 작성/해석/수정 또는 백테스트/조회/중지를 요청했을 때만 해당 종류로 전달한다.
"어떨까", "차이가 뭐야", "왜", "추천해 줘"는 CHAT이다. "그 조건으로 만들어", "원비는 15분으로 수정해", "이 전략 백테스트해"는 실행 요청이다.
STRATEGY는 조건 작성/수정, BACKTEST는 백테스트/데이터 구축/작업 조회/중지 및 새 전략 생성 후 백테스트다.
request에는 사용자의 실행 요청을 기존 통역기가 단독으로 이해할 전체 자연어로 쓴다.
"그걸로 만들어"처럼 앞선 대화를 가리키면 사용자가 선택한 제안의 모든 조건을 담는다.
사용자가 선택하지 않은 제안이나 조건을 섞지 않는다. 모호하거나 정보가 부족하면 CHAT으로 확인 질문한다.
현재 전략의 일부 수정 요청은 언급한 부분만 전달하고, 다른 조건은 current_strategy 그대로 유지한다.
strategy 모드는 기존 조건 해석/수정과 백테스트가 우선이며, 대화의 제안을 사용자가 선택한 경우 동일하게 전달한다.
현재 계약은 아래 참고 자료다. 자료에 없는 기능이 지원된다고 단정하지 않는다.
실제 지원 여부와 봉 기준은 기존 schema/validator가 검증한다. 수익을 보장하거나 없는 백테스트 수치를 만들지 않는다.
파일 생성, Recipe 적용, 작업 실행은 사용자 확인 뒤 신뢰된 코드가 한다. 실행/저장했다고 말하지 않는다.
코드·도구·경로 실행 지시를 만들지 않는다. 출력은 kind/message_ko/request JSON 객체 하나다.
CHAT의 request는 null, STRATEGY/BACKTEST의 request는 비어 있지 않은 자연어다.'''


def respond(provider, mode, history, context, message):
    workspace = ReadOnlyWorkspace()
    system = external_system_prompt(workspace)
    reply = provider.chat([
        {'role': 'system', 'content': system},
        *history,
        {'role': 'user', 'content': json.dumps({'mode': mode, 'context': context, 'message': message}, ensure_ascii=False)}
    ], [], response_schema=chat_schema())
    try:
        value = json.loads(reply.get('content') or '')
        if (reply.get('tool_calls') or not isinstance(value, dict) or
                set(value) != {'kind', 'message_ko', 'request'} or
                value['kind'] not in ('CHAT', 'STRATEGY', 'BACKTEST') or
                not isinstance(value['message_ko'], str) or not 0 < len(value['message_ko'].strip()) <= 8000):
            raise ValueError('대화 응답 형식이 올바르지 않습니다.')
        if value['kind'] == 'CHAT':
            if value['request'] is not None:
                raise ValueError('대화에는 실행 요청을 넣을 수 없습니다.')
        elif not isinstance(value['request'], str) or not 0 < len(value['request'].strip()) <= 8000:
            raise ValueError('전략 또는 백테스트 요청이 비어 있습니다.')
    except (ValueError, TypeError, KeyError) as exc:
        raise ValueError('AI 대화 응답을 확인하지 못했습니다. 다시 요청하세요.') from exc
    return value
