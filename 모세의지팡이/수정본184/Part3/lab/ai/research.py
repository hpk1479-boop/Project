"""One conversation routes interpretation to the existing strategy and job APIs."""
from __future__ import annotations

import json
import copy
import threading
import uuid
from contextlib import nullcontext

from . import backtest_commands as commands
from .research_interpreter import interpret, selection


class DiscussionError(ValueError):
    """Discussion failed before any canonical/confirmation state was changed."""
    pending_preserved = True


class Session:
    def __init__(self, provider, strategy_loader, apply_strategy, context_loader=None):
        self.provider = provider
        self.strategy_loader = strategy_loader
        self.apply_strategy = apply_strategy
        self.context_loader = context_loader or commands.context
        self.lock = threading.RLock()
        self.prefix = uuid.uuid4().hex
        self.number = 0
        self.kind = None
        self.pending = None
        self.question = None
        self.backtest = None
        self.history = []
        self.revision = f'{self.prefix}:0'
        self.editor_strategy = None
        self.editor_plan = None
        self.editor_errors = []
        self.editor_invalid = False

    def _remember(self, message, response):
        self.history.extend([{'role': 'user', 'content': message},
                             {'role': 'assistant', 'content': response[:8000]}])
        # Keep recent discussion; current canonical/plan are supplied separately.
        self.history = self.history[-8:]
        while len(self.history) > 2 and sum(len(row['content']) for row in self.history) > 16000:
            del self.history[:2]

    def _backtest(self):
        if self.backtest is None:
            from .research_backtest import Session
            self.backtest = Session(self.provider, self.strategy_loader, context_loader=self.context_loader)
        return self.backtest

    def _cancel_pending(self):
        if self.pending:
            kind, _ = self.pending
            (self.strategy_loader() if kind == 'STRATEGY' else self._backtest()).cancel()
        self.pending = None

    def send(self, message, mode='strategy'):
        with self.lock:
            if mode not in ('strategy', 'chat'):
                raise ValueError('연구 모드는 전략생성 또는 챗을 선택하세요.')
            if not isinstance(message, str) or not message.strip() or len(message) > 8000:
                raise ValueError('연구 요청은 1~8000자의 문장으로 입력하세요.')
            agent = self.strategy_loader()
            snapshot = (self.backtest.context_loader if self.backtest else self.context_loader)()
            context = {'previous_kind': self.kind, 'question': self.question,
                       'backtest_has_draft': bool(self.backtest and self.backtest.has_draft),
                       'previous_plan': copy.deepcopy(self.backtest.last_plan) if self.backtest else None,
                       'selection': selection(snapshot)}
            with agent.lock:
                context['current_strategy'] = copy.deepcopy(self.editor_strategy if self.editor_invalid else agent.last_intent)
            if self.editor_invalid:
                # An invalid direct edit is a visible draft, never permission
                # to reuse an older valid strategy or execution plan silently.
                context['previous_plan'] = copy.deepcopy(self.editor_plan)
                context['backtest_has_draft'] = bool(isinstance(self.editor_plan, dict) and
                    isinstance(self.editor_plan.get('steps'), list) and any(
                        isinstance(row, dict) and row.get('draft') is True for row in self.editor_plan['steps']))
                context['question'] = '표 수정값을 검증하지 못했습니다. 현재 초안에서 요청한 부분만 수정하고 전체 해석을 다시 반환하세요.'
            try:
                value, actions = interpret(self.provider, agent, mode, self.history, context, message)
            except (ValueError, TypeError, KeyError, RuntimeError, OSError) as exc:
                raise DiscussionError(str(exc)) from exc
            if value['kind'] == 'CHAT':
                self._remember(message, value['message_ko'])
                return {'kind': 'CHAT', 'message_ko': value['message_ko'],
                        'pending_preserved': True, 'can_confirm': False}
            kind = value['kind']
            previous_backtest = self.backtest
            previous_pending = copy.deepcopy(self.pending)
            # Only commit a new conversational result after all existing
            # canonical, Recipe and job-plan checks have succeeded. No files
            # or jobs are created in this preparation path.
            # Legacy plan entry points acquire Plan before Agent as well.
            with (previous_backtest.lock if previous_backtest else nullcontext()), agent.lock:
                previous_plan = previous_backtest.checkpoint() if previous_backtest else None
                previous_agent = agent.checkpoint()
                try:
                    self._cancel_pending()
                    if kind == 'STRATEGY':
                        response = agent.accept(value['strategy'], message, actions)
                    else:
                        strategy = agent.accept(value['strategy'], message, actions) if value['strategy'] is not None else None
                        response = self._backtest().accept_plan(value['plan'], strategy=strategy, snapshot=snapshot)
                    if kind == 'STRATEGY':
                        if self.editor_invalid and self.kind == 'BACKTEST' and context['backtest_has_draft']:
                            # Keep the edited execution settings, including
                            # invalid fields which still require correction.
                            # Falling back to an older plan would silently undo
                            # a manual date/symbol change.
                            response = self._backtest().accept_plan(self.editor_plan, strategy=response, snapshot=snapshot)
                            kind = 'BACKTEST'
                        elif self.backtest and self.backtest.has_draft:
                            response = self.backtest.update_strategy(response)
                            kind = 'BACKTEST'
                except Exception as exc:
                    agent.restore_checkpoint(previous_agent)
                    if previous_backtest:
                        previous_backtest.restore_checkpoint(previous_plan)
                    self.backtest = previous_backtest
                    self.pending = previous_pending
                    if isinstance(exc, (ValueError, TypeError, KeyError, RuntimeError, OSError)):
                        raise DiscussionError(str(exc)) from exc
                    raise
            self.number += 1
            revision = f'{self.prefix}:{self.number}'
            self.kind = kind
            self.question = (response.get('result') or {}).get('clarification_question') if kind == 'STRATEGY' else response.get('clarification_question')
            if response.get('can_apply') or response.get('can_confirm'):
                self.pending = (kind, response['revision'])
            self.revision = revision
            next_strategy = response.get('result') if kind == 'STRATEGY' else (response.get('strategy') or {}).get('result')
            if not (self.editor_invalid and next_strategy and not next_strategy.get('supported')):
                self.editor_strategy = copy.deepcopy(next_strategy)
                self.editor_plan = copy.deepcopy(self.backtest.last_plan) if kind == 'BACKTEST' else None
                self.editor_errors = []
                self.editor_invalid = False
            self._remember(message, json.dumps({'kind': kind,
                'message_ko': response.get('message_ko') or (response.get('result') or {}).get('message_ko'),
                'preview': (response.get('preview') or '')[:2000]}, ensure_ascii=False))
            return {**response, 'kind': kind, 'revision': revision, 'operation': kind,
                    'plan': copy.deepcopy(self.editor_plan), 'editor_strategy': copy.deepcopy(self.editor_strategy)}

    def editor(self):
        """Selection contract only; querying it neither applies nor starts jobs."""
        from .research_editor import contract
        with self.lock:
            snapshot = self.context_loader()
            return {**contract(snapshot, self.editor_strategy), 'revision': self.revision,
                'operation': self.kind if self.kind in ('STRATEGY', 'BACKTEST') else 'STRATEGY',
                'strategy': copy.deepcopy(self.editor_strategy), 'plan': copy.deepcopy(self.editor_plan),
                'errors': copy.deepcopy(self.editor_errors), 'can_apply': bool(self.pending and self.pending[0] == 'STRATEGY'),
                'can_confirm': self.pending is not None}

    def presets(self):
        """Read-only templates; listing never asks a model to interpret them."""
        from .research_presets import catalog
        with self.lock:
            return {'items': catalog(), 'revision': self.revision}

    def load_preset(self, preset_id, revision):
        """Prepare a new editable strategy without changing the source preset."""
        from .research_presets import load
        from .research_editor import contract
        with self.lock:
            if not isinstance(revision, str) or revision != self.revision:
                return {'ok': False, 'revision': self.revision,
                    'errors': [{'path': ['revision'], 'message': '해석 결과가 변경되었습니다. 최신 표를 다시 확인하세요.'}],
                    'can_apply': False, 'can_confirm': False}
            # Every read/validation succeeds before any existing conversation
            # or approval state is replaced. No natural-language round trip.
            template = load(preset_id)
            editor_contract = contract(self.context_loader(), template['strategy'])
            agent = self.strategy_loader()
            with (self.backtest.lock if self.backtest else nullcontext()), agent.lock:
                checkpoint = agent.checkpoint()
                try:
                    fresh = copy.deepcopy(checkpoint)
                    fresh.update(messages=fresh['messages'][:1], candidate=None, original_text='',
                        pending_clarification=None, first_interpretation=None, last_intent=None)
                    agent.restore_checkpoint(fresh)
                    response = agent.accept(template['strategy'], template['example'])
                    if not response.get('can_apply'):
                        raise ValueError(response.get('application_error') or '등록 전략의 조건을 검증하지 못했습니다.')
                except Exception:
                    agent.restore_checkpoint(checkpoint)
                    raise
                if self.backtest:
                    self.backtest.cancel()
                self.backtest = None
                self.history = []
                self.number += 1
                self.revision = f'{self.prefix}:{self.number}'
                self.kind = 'STRATEGY'
                self.pending = ('STRATEGY', response['revision'])
                self.question = None
                self.editor_strategy = copy.deepcopy(response['result'])
                self.editor_plan = None
                self.editor_errors = []
                self.editor_invalid = False
                self._remember(template['example'], response['result']['message_ko'])
                editor_contract.update(revision=self.revision, operation='STRATEGY',
                    strategy=copy.deepcopy(self.editor_strategy), plan=None, errors=[],
                    can_apply=True, can_confirm=True)
                return {**response, 'ok': True, 'kind': 'STRATEGY', 'operation': 'STRATEGY',
                    'revision': self.revision, 'example': template['example'],
                    'preset': {'id': template['id'], 'name': template['name']},
                    'plan': None, 'errors': [], 'editor_strategy': copy.deepcopy(self.editor_strategy),
                    'editor_contract': editor_contract}

    def edit(self, revision, strategy, operation, plan=None):
        """Validate a complete user-edited draft using the existing trusted path."""
        from .research_editor import check_strategy, check_plan, semantic_error, draft_frames
        with self.lock:
            if not isinstance(revision, str) or revision != self.revision:
                return {'ok': False, 'revision': self.revision,
                    'errors': [{'path': ['revision'], 'message': '해석 결과가 변경되었습니다. 최신 표를 다시 확인하세요.'}],
                    'can_apply': False, 'can_confirm': False}
            agent = self.strategy_loader()
            previous_strategy = copy.deepcopy(agent.last_intent)
            previous_plan = copy.deepcopy(self.backtest.last_plan) if self.backtest else None
            self._cancel_pending()
            # Also invalidate native approval when the previous operation was
            # BACKTEST but a separate strategy candidate remains on the Agent.
            agent.cancel()
            self.number += 1
            self.revision = f'{self.prefix}:{self.number}'
            self.editor_strategy = copy.deepcopy(strategy)
            self.editor_plan = copy.deepcopy(plan)
            self.editor_invalid = True
            self.editor_errors = []
            errors = []
            if operation not in ('STRATEGY', 'BACKTEST'):
                errors.append({'path': ['operation'], 'message': '전략 생성 또는 백테스트를 선택하세요.'})
            else:
                self.kind = operation
            if operation == 'STRATEGY':
                # The explicit work slot detaches the old plan. A later
                # strategy correction must not reconnect it automatically.
                self.editor_plan = None
                if self.backtest:
                    with self.backtest.lock:
                        self.backtest.last_plan = None
                        self.backtest.strategy = None
                        self.backtest.cancel()
            validated = None
            if strategy is not None:
                validated, strategy_errors = check_strategy(strategy, previous_strategy)
                errors.extend(strategy_errors)
            elif operation == 'STRATEGY' or (isinstance(plan, dict) and isinstance(plan.get('steps'), list) and any(
                    isinstance(row, dict) and row.get('draft') is True for row in plan['steps'])):
                errors.append({'path': ['strategy', 'interpretation'], 'message': '전략 조건을 먼저 해석하거나 선택해 주세요.'})
            snapshot = self.context_loader()
            if operation == 'BACKTEST':
                errors.extend(check_plan(plan, snapshot, validated, previous_plan))
            if not errors:
                with (self.backtest.lock if self.backtest else nullcontext()), agent.lock:
                    checkpoint = agent.checkpoint()
                    backtest_state = self.backtest.checkpoint() if self.backtest else None
                    try:
                        response = agent.accept(validated, '사용자가 해석표를 직접 수정했습니다.') if validated is not None else None
                        if validated is not None and not response.get('can_apply'):
                            raise ValueError(response.get('application_error') or '전략 조건을 다시 확인하세요.')
                        if operation == 'BACKTEST':
                            response = self._backtest().accept_plan(plan, strategy=response, snapshot=snapshot)
                            if not response.get('can_confirm'):
                                raise ValueError(response.get('clarification_question') or '백테스트 설정을 다시 확인하세요.')
                    except (ValueError, TypeError, KeyError, RuntimeError, OSError) as exc:
                        agent.restore_checkpoint(checkpoint)
                        if backtest_state is not None:
                            self.backtest.restore_checkpoint(backtest_state)
                        else:
                            if self.backtest:
                                self.backtest.cancel()
                        errors.extend(semantic_error(str(exc), previous_plan if operation == 'BACKTEST' else previous_strategy,
                            plan if operation == 'BACKTEST' else strategy, ['plan'] if operation == 'BACKTEST' else ['strategy']))
                    else:
                        self.editor_strategy = copy.deepcopy(validated)
                        self.editor_plan = copy.deepcopy(self.backtest.last_plan) if operation == 'BACKTEST' else None
                        self.editor_invalid = False
                        self.question = None
                        self.pending = (operation, response['revision'])
                        self._remember('해석표 직접 수정', json.dumps({'kind': operation, 'preview': response.get('preview', '')}, ensure_ascii=False))
                        return {**response, 'ok': True, 'kind': operation, 'operation': operation,
                            'revision': self.revision, 'errors': [], 'plan': copy.deepcopy(self.editor_plan),
                            'editor_strategy': copy.deepcopy(self.editor_strategy),
                            'virtual_draft_frames': draft_frames(self.editor_strategy)}
            self.editor_errors = copy.deepcopy(errors)
            self.question = '표의 오류를 수정한 뒤 다시 확인하세요.'
            return {'ok': False, 'kind': self.kind, 'operation': self.kind, 'revision': self.revision,
                'errors': errors, 'editor_strategy': copy.deepcopy(self.editor_strategy),
                'plan': copy.deepcopy(self.editor_plan), 'can_apply': False, 'can_confirm': False,
                'virtual_draft_frames': None}

    def confirm(self, revision):
        with self.lock:
            if self.pending is None or not isinstance(revision, str) or revision != self.revision:
                raise ValueError('최신 해석을 다시 확인하세요. 적용 또는 실행할 요청이 없습니다.')
            kind, native_revision = self.pending
            self.pending = None
            if kind == 'STRATEGY':
                result = self.apply_strategy(native_revision)
            else:
                result = self._backtest().confirm(native_revision)
            return {**result, 'kind': kind, 'revision': revision}

    def cancel(self):
        with self.lock:
            self._cancel_pending()
        return {'ok': True}
