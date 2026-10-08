"""Confirmed research plans reuse canonical Agent and existing job validation."""
from __future__ import annotations

import copy
import json
import threading
import uuid

from . import backtest_commands as commands
from .intent import recipe_from_intent

PLAN_FIELDS = ('last_plan', 'strategy', 'pending', 'question', 'draft_filename',
               'version', 'revision', 'sequence_id')


def plan_schema():
    step = {'type': 'object', 'additionalProperties': False, 'required': ['draft', 'command'],
            'properties': {'draft': {'type': 'boolean'}, 'command': commands.command_schema()}}
    return {'type': 'object', 'additionalProperties': False,
            'required': ['strategy_text', 'steps', 'needs_clarification', 'clarification_question'],
            'properties': {'strategy_text': {'type': ['string', 'null']},
                           'steps': {'type': 'array', 'items': step, 'minItems': 1},
                           'needs_clarification': {'type': 'boolean'},
                           'clarification_question': {'type': ['string', 'null']}}}


PROMPT = '''AI 전략연구의 백테스트 요청을 기존 작업 명령으로 해석한다. 지정된 JSON만 반환한다.
steps는 실행 순서다. 여러 START는 순차 실행하며 이전 작업이 완전히 완료된 뒤 다음을 시작한다.
임의의 종목/전략/조건을 추가하지 않는다. 부족하거나 모호하면 needs_clarification=true로 질문한다.
기존 SPECIAL, 생성 파일, WATCH는 현재 선택 정보에서 해석한다.
새로운 전략 조건을 백테스트하려면 strategy_text에 실행 기간을 제외한 모든 전략 조건을 그대로 담고,
해당 steps의 draft=true, target_mode=GENERATED, filename=null로 둔다. 전략 지원 판단은 기존 canonical 통역기가 한다.
직전 연구 전략을 백테스트하면 strategy_text=null, draft=true다. 이미 생성된 파일을 명시하면 draft=false다.
수정은 직전 전체 계획의 언급한 부분만 바꾼 전체 계획으로 반환한다. 날짜/종목 수정으로 전략 조건을 바꾸지 않는다.
strategy_text는 이번 요청에서 새 전략 조건을 해석해야 할 때만 사용한다. 이미 해석한 조건은 current_strategy로 유지하며 기간·종목만 수정하는 요청에는 strategy_text=null이다.
사용자에게 전략 조건을 다시 물을 경우 앞서 요청한 기간/종목과 실행 순서를 보존한다.
일반 백테스트/승률 요청은 result_mode=VIRTUAL_ENTRY. 알림 횟수만 요청하면 ALERT_ONLY다.
가상진입 조건·ATR 필터·손절은 command.request.virtual_entry에만 담고 전략 알림 조건과 분리한다. 가상 진입은 전략 1개만 실행한다.
즉시 진입은 알림 시점 가격, 조건 진입은 conditions(CANDLE_CLOSE 양봉·음봉 마감 포함)를 모두 만족한 확인봉 다음 봉 시가다.
진입·손절을 지정하지 않으면 virtual_entry는 null이며 전략 기본값을 쓴다. 기간·종목만 수정할 때 직전 정책을 유지한다.
기준 프레임을 바꾸면 command.request.base_frame에 목적 시간봉을 넣고 virtual_entry는 null로 둔다. 프로그램이 그 기준으로 쓴 레시피 값으로 전략과 진입 정책을 모두 다시 채운다(변경 전에 바꾼 값은 쓰지 않는다).
시간봉은 기준 프레임으로만 바꾼다. virtual_entry의 시간봉(tf)은 레시피 값 그대로 두고 진입·손절 시간봉만 따로 바꾸지 않는다. 기준을 바꾸지 않는 요청은 base_frame=null이다.
기존 conditions·filters 행의 recipe_index는 수정해도 그대로 보존한다. 조건 종류·수치를 바꿔도 그 칸의 시간봉은 유지한다. 삭제 후 새로 추가한 행은 recipe_index 없이 tf=SIGNAL로 넣는다. 칸 번호를 바꾸거나 새로 만들지 않는다.
단, 확인 질문에 답할 때 직전 명령에 미처리 base_frame이 남아 있으면 그 값을 보존한다.
날짜는 현재 한국 날짜와 요청을 기준으로 계산, end는 미포함이다. 최근 N개월/년은 달력 기준이다.
최근 N일/개월/년 백테스트는 요청 기간을 today 기준으로 유지하며 available_only=true로 해석한다.
보유 데이터의 마지막 날짜로 요청 기간을 옮기지 않는다. available_only=true는 요청 기간의 검증된 보유 구간만 실행하고 자동 구축하지 않는다.
데이터 구축·재구축이나 오늘까지 데이터를 채우라고 명시하면 available_only=false다. 일반 명시 기간 요청의 기본값은 false이며 후속 수정은 기존 정책을 보존한다.
종목 별칭은 현재 실제 목록에서 유일하게 선택할 수 있을 때만 해석한다. 모호하면 실제 종목명을 질문한다.
START와 조회/중지는 한 계획에서 혼합하지 않는다. 조회/중지는 단일 command다.
백테스트 모드는 명시하지 않으면 null(기존 기본값). 임의로 데이터 모드나 가격을 만들지 않는다.
파일 경로나 실행 코드는 만들지 않는다. 생성과 실행은 사용자 확인 후 신뢰된 프로그램이 한다.
'''


def validate_plan_shape(value):
    """Reject malformed shared payloads before changing conversation state."""
    if (not isinstance(value, dict) or set(value) !=
            {'strategy_text', 'steps', 'needs_clarification', 'clarification_question'} or
            value['strategy_text'] is not None or
            not isinstance(value['steps'], list) or not value['steps'] or
            type(value['needs_clarification']) is not bool or
            value['clarification_question'] is not None and not isinstance(value['clarification_question'], str)):
        raise ValueError('백테스트 연구 계획 형식이 올바르지 않습니다.')
    for row in value['steps']:
        if (not isinstance(row, dict) or set(row) != {'draft', 'command'} or
                type(row['draft']) is not bool or not isinstance(row['command'], dict) or
                row['command'].get('request') is not None and not isinstance(row['command'].get('request'), dict)):
            raise ValueError('백테스트 순서 형식이 올바르지 않습니다.')
        if row['draft'] and (row['command'].get('action') != 'START' or
                (row['command'].get('request') or {}).get('target_mode') != 'GENERATED'):
            raise ValueError('현재 전략은 생성 전략 백테스트에만 사용할 수 있습니다.')


class Session:
    def __init__(self, provider, strategy_loader, context_loader=None, executor=None, generator=None, previewer=None):
        from .. import storage
        self.provider = provider
        self.strategy_loader = strategy_loader
        self.context_loader = context_loader or commands.context
        self.executor = executor or commands.execute
        self.generator = generator or storage.generate
        self.previewer = previewer or storage.preview
        self.lock = threading.RLock()
        self.last_plan = None
        self.strategy = None
        self.pending = None
        self.question = None
        self.draft_filename = None
        self.version = None
        self.revision = None
        self.sequence_id = None

    @property
    def has_draft(self):
        return bool(self.last_plan and any(row['draft'] for row in self.last_plan['steps']))

    def cancel(self):
        self.pending = None

    def checkpoint(self):
        """Research plan data only; running jobs and files are not changed."""
        with self.lock:
            return copy.deepcopy({name: getattr(self, name) for name in PLAN_FIELDS})

    def restore_checkpoint(self, state):
        with self.lock:
            for name in PLAN_FIELDS:
                setattr(self, name, copy.deepcopy(state[name]))

    def send(self, message):
        with self.lock:
            self.pending = None
            snapshot = self.context_loader()
            agent = self.strategy_loader()
            context = {'selection': commands._safe_snapshot(snapshot), 'previous_plan': self.last_plan,
                       'question': self.question, 'current_strategy': agent.last_intent}
            reply = self.provider.chat([{'role': 'system', 'content': PROMPT},
                {'role': 'user', 'content': json.dumps({'context': context, 'request': message}, ensure_ascii=False)}],
                [], response_schema=plan_schema())
            value = json.loads(reply.get('content') or '')
            if (reply.get('tool_calls') or not isinstance(value, dict) or set(value) !=
                    {'strategy_text', 'steps', 'needs_clarification', 'clarification_question'} or
                    not isinstance(value['steps'], list) or not value['steps'] or
                    type(value['needs_clarification']) is not bool or
                    value['strategy_text'] is not None and not isinstance(value['strategy_text'], str) or
                    value['clarification_question'] is not None and not isinstance(value['clarification_question'], str)):
                raise ValueError('백테스트 연구 계획 형식이 올바르지 않습니다.')
            for row in value['steps']:
                if not isinstance(row, dict) or set(row) != {'draft', 'command'} or type(row['draft']) is not bool:
                    raise ValueError('백테스트 순서 형식이 올바르지 않습니다.')
            self.last_plan = copy.deepcopy(value)
            if value['strategy_text']:
                if not self.has_draft:
                    raise ValueError('새 전략 해석을 실행할 작업에 연결하세요.')
                symbols = list(dict.fromkeys((row['command'].get('request') or {}).get('symbol')
                    for row in value['steps'] if row['draft'] and (row['command'].get('request') or {}).get('symbol')))
                text = value['strategy_text']
                if symbols:
                    text += '\n이 전략으로 백테스트할 실제 종목: ' + json.dumps(symbols, ensure_ascii=False) + '. 모든 대상 종목에 같은 조건을 사용한다.'
                self.strategy = agent.send(text)
                self.last_plan['strategy_text'] = None
            elif self.has_draft:
                if agent.last_intent is None:
                    self.strategy = None
                else:
                    self.strategy = {'result': copy.deepcopy(agent.last_intent)}
            else:
                self.strategy = None
            return self._prepare(snapshot)

    def accept_plan(self, value, *, strategy=None, snapshot=None):
        """Prepare an already interpreted plan without a second AI request."""
        with self.lock:
            validate_plan_shape(value)
            if strategy is not None and not any(row['draft'] for row in value['steps']):
                raise ValueError('새 전략 해석을 실행할 작업에 연결하세요.')
            self.pending = None
            self.last_plan = copy.deepcopy(value)
            agent = self.strategy_loader()
            self.strategy = (copy.deepcopy(strategy) if strategy is not None else
                {'result': copy.deepcopy(agent.last_intent)} if self.has_draft and agent.last_intent is not None else None)
            return self._prepare(snapshot if snapshot is not None else self.context_loader())

    def update_strategy(self, response):
        with self.lock:
            self.strategy = response
            self.last_plan['strategy_text'] = None
            return self._prepare(self.context_loader())

    def _prepare(self, snapshot):
        self.revision = uuid.uuid4().hex
        self.pending = None
        self.question = None
        recipe = None
        if self.has_draft:
            self.draft_filename = None
            intent = (self.strategy or {}).get('result')
            if not intent or not intent.get('supported') or intent.get('needs_clarification'):
                self.question = (intent or {}).get('clarification_question') or (intent or {}).get('message_ko') or '먼저 백테스트할 전략 조건을 알려 주세요.'
            else:
                recipe = recipe_from_intent(intent)
                targets = {(row['command'].get('request') or {}).get('symbol') for row in self.last_plan['steps'] if row['draft']}
                symbols = set(intent['interpretation']['symbols'])
                if targets - symbols - {None, ''}:
                    self.question = '백테스트 대상 종목이 현재 전략의 감시 종목에 없습니다. 전략에 해당 종목을 포함할지 확인해 주세요.'
                # Compilation is read-only here. No reservation or generated file yet.
                self.draft_filename = self.previewer(recipe)['filename']
                snapshot = copy.deepcopy(snapshot)
                snapshot.setdefault('generated', []).append({'filename': self.draft_filename, 'name': '현재 연구 전략'})
        normalized, previews = [], []
        actions = set()
        for number, row in enumerate(self.last_plan['steps'], 1):
            value = copy.deepcopy(row['command'])
            if not isinstance(value, dict):
                raise ValueError('백테스트 명령 형식을 확인하세요.')
            if row['draft']:
                if value.get('action') != 'START' or (value.get('request') or {}).get('target_mode') != 'GENERATED':
                    raise ValueError('현재 전략은 생성 전략 백테스트에만 사용할 수 있습니다.')
                value['request']['filename'] = self.draft_filename
            if value.get('action') == 'START' and isinstance(value.get('request'), dict):
                if value['request'].get('result_mode') is None:
                    value['request']['result_mode'] = 'VIRTUAL_ENTRY'
            command, question = commands._normalize(value, snapshot)
            if command is None:
                self.question = self.question or value.get('message_ko') or '지원하지 않는 요청입니다.'
                continue
            if command['action'] == 'START':
                # Show and retain the effective checked policy rather than a
                # null model slot. Later date/symbol edits must preserve it.
                saved = self.last_plan['steps'][number - 1]['command']['request']
                saved['result_mode'] = command['request']['result_mode']
                saved['virtual_entry'] = copy.deepcopy(command['request']['virtual_entry'])
                if 'base_frame' in command['request']:
                    saved['base_frame'] = command['request']['base_frame']
                else:
                    saved.pop('base_frame', None)  # Confirmation and later edits use the effective policy.
            self.question = self.question or question
            actions.add(command['action'])
            normalized.append({'draft': row['draft'], 'command': command})
            if not question:
                previews.append(f'{number}. ' + commands._preview(command, snapshot))
        if len(self.last_plan['steps']) > 1 and actions != {'START'}:
            raise ValueError('여러 작업의 순차 실행은 백테스트 시작 요청끼리만 가능합니다.')
        if self.last_plan['needs_clarification']:
            self.question = self.question or self.last_plan['clarification_question'] or '요청을 조금 더 구체적으로 알려 주세요.'
        result = None
        if not self.question:
            if actions <= {'STATUS', 'RECENT', 'RECONNECT'}:
                result = self._execute(copy.deepcopy(normalized[0]['command']))
            else:
                self.pending = {'steps': normalized, 'recipe': recipe}
                self.version = commands._settings_version(snapshot)
        return {'action': normalized[0]['command']['action'] if len(normalized) == 1 else 'SEQUENTIAL',
                'strategy': self.strategy, 'preview': '\n\n'.join(previews), 'needs_clarification': bool(self.question),
                'clarification_question': self.question, 'message_ko': self.question or
                    ('앞선 백테스트가 완료된 뒤 다음 작업을 실행합니다.' if len(normalized) > 1 else '해석한 조건을 확인해 주세요.'),
                'can_confirm': self.pending is not None, 'revision': self.revision, 'result': result}

    def confirm(self, revision):
        with self.lock:
            if self.pending is None or revision != self.revision:
                raise ValueError('최신 백테스트 연구 계획을 다시 확인하세요.')
            pending = self.pending
            self.pending = None
            snapshot = self.context_loader()
            if commands._settings_version(snapshot) != self.version:
                raise ValueError('실행 설정이 변경되었습니다. 최신 계획을 다시 해석하세요.')
            generated = None
            if pending['recipe'] is not None:
                generated = self.generator(pending['recipe'])
                if generated.get('warning'):
                    raise ValueError('전략 파일은 생성되었으나 설정 저장에 실패했습니다. 실행을 중단했습니다: ' + generated['warning'])
                snapshot.setdefault('generated', []).append({'filename': generated['filename']})
            checked = []
            for row in pending['steps']:
                value = copy.deepcopy(row['command'])
                if row['draft']:
                    value['request']['filename'] = generated['filename']
                # Normalize against fresh selections again, retaining the checked default settings.
                original = {'supported': True, 'needs_clarification': False, 'message_ko': '',
                            'job_id': value.get('job_id'), 'action': value['action'], 'request': value.get('request')}
                if original['request']:
                    original['request'] = {key: item for key, item in original['request'].items() if key in commands.REQUEST_FIELDS}
                command, question = commands._normalize(original, snapshot)
                if command is None or question:
                    raise ValueError(question or '백테스트 요청을 다시 확인하세요.')
                checked.append(command)
            if checked[0]['action'] != 'START':
                result = self._execute(checked[0])
            else:
                from .. import backtest_sequences
                expected = self.version
                def validate_settings():
                    if commands._settings_version(self.context_loader()) != expected:
                        raise ValueError('확인한 실행 설정이 변경되어 이후 백테스트를 중단했습니다.')
                result = backtest_sequences.start(checked, executor=self.executor, validator=validate_settings)
                self.sequence_id = result['sequence_id']
            if generated:
                result = {**result, 'generated_filename': generated['filename']}
            return {'action': checked[0]['action'] if len(checked) == 1 else 'SEQUENTIAL',
                    'result': result, 'can_confirm': False, 'revision': self.revision}

    def _execute(self, command):
        if self.sequence_id and command['action'] in ('STOP', 'STATUS', 'RECONNECT'):
            from .. import backtest_sequences
            sequence = backtest_sequences.status(self.sequence_id)
            if command.get('job_id') in {row['job_id'] for row in sequence['jobs']}:
                if command['action'] == 'STOP' and sequence['phase'] == 'run':
                    return backtest_sequences.stop(self.sequence_id)
                if command['action'] != 'STOP':
                    return sequence
        return self.executor(command)
