"""Real interpretation/validation/generation boundaries; jobs use isolated fakes."""
import copy
import importlib.util
import json
from pathlib import Path
import sys
from unittest.mock import Mock

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'Part3'))
from lab import backtest_sequences as sequences, backtest_jobs, server, storage
from lab.ai.provider import ScriptedProvider
from lab.ai.research import Session as Research
from lab.ai.research_backtest import Session as Plan
from lab.ai.agent import Agent
from test_backtest_commands67 import snapshot, value, request, JOB, OTHER_JOB


def fixture():
    spec = importlib.util.spec_from_file_location('research_fixture79', ROOT / 'Part3/tests/test_ai_revision59.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def reply(value):
    return {'content': json.dumps(value, ensure_ascii=False)}


def research_reply(kind, *, strategy=None, research_plan=None, text='해석한 조건을 확인해 주세요.'):
    if kind == 'STRATEGY':
        return reply(strategy)
    return reply({'kind': kind, 'strategy': strategy, 'plan': research_plan, 'message_ko': text})


def plan(*steps, strategy_text=None, **changes):
    return {'strategy_text': strategy_text, 'steps': list(steps), 'needs_clarification': False,
            'clarification_question': None, **changes}


def step(*, draft=False, **changes):
    return {'draft': draft, 'command': value(data=request(**changes))}


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    monkeypatch.setattr(backtest_jobs, '_closing', False)
    monkeypatch.setattr(sequences, '_running', {})


def test_strategy_backtest_switch_preserves_canonical_and_requires_current_confirmation():
    f = fixture()
    model = ScriptedProvider([research_reply('STRATEGY', strategy=f.original()),
        research_reply('BACKTEST', research_plan=plan(step())),
        research_reply('STRATEGY', strategy=f.changed())])
    agent = Agent(model)
    execute = Mock()
    research = Research(model, lambda: agent, lambda rev: agent.apply(rev), context_loader=snapshot)
    research.backtest = Plan(model, lambda: agent, context_loader=snapshot, executor=execute)
    first = research.send('15분 상승추세일 때 3분 하단 원비')
    before = copy.deepcopy(agent.last_intent)
    second = research.send('스페셜1 골드 최근 한달 백테스트')
    assert agent.last_intent == before
    assert second['can_confirm'] and len(model.seen) == 2
    execute.assert_not_called()
    with pytest.raises(ValueError):
        research.confirm(first['revision'])
    modified = research.send('아니 원비는 15분이야')
    assert modified['kind'] == 'STRATEGY' and modified['is_revision']
    with pytest.raises(ValueError):
        research.confirm(second['revision'])
    recipe = research.confirm(modified['revision'])
    assert recipe['kind'] == 'STRATEGY'
    assert agent.last_intent == f.changed()
    assert len(model.seen) == 3
    execute.assert_not_called()


def test_new_strategy_generates_only_after_full_confirmation_then_uses_generated_job(tmp_path, monkeypatch):
    f = fixture()
    model = ScriptedProvider([reply(plan(step(draft=True, target_mode='GENERATED', specials=[], filename=None),
        strategy_text='15분 추세와 3분 하단 원비 조건')), f.reply(f.original())])
    agent = Agent(model)
    root = tmp_path / 'Part3'
    root.mkdir()
    monkeypatch.setattr(storage, 'ROOT', root)
    execute = Mock(return_value={'job_id': JOB, 'phase': 'planning'})
    started = []
    def start(commands, **kwargs):
        started.extend(commands)
        return {'sequence_id': OTHER_JOB, 'jobs': [kwargs['executor'](commands[0])], 'phase': 'run'}
    monkeypatch.setattr(sequences, 'start', start)
    session = Plan(model, lambda: agent, context_loader=snapshot, executor=execute)
    result = session.send('15분 추세와 3분 하단 원비 골드 1년 백테스트')
    assert result['can_confirm'] and result['strategy']['can_apply']
    assert not list(root.glob('generated/*'))
    execute.assert_not_called()
    confirmed = session.confirm(result['revision'])
    filename = confirmed['result']['generated_filename']
    assert started[0]['request']['filename'] == filename
    assert started[0]['request']['target_mode'] == 'GENERATED'
    code = (root / 'generated' / filename).read_text('utf-8')
    compile(code, filename, 'exec')
    assert 'def register(' in code
    assert (root / 'generated' / filename.replace('.py', '.recipe.json')).is_file()
    with pytest.raises(ValueError):
        session.confirm(result['revision'])
    assert execute.call_count == 1


def test_current_draft_corrections_preserve_dates_symbol_and_execution_order():
    f = fixture()
    model = ScriptedProvider([reply(plan(step(draft=True, target_mode='GENERATED', specials=[], filename=None))),
                             f.reply(f.changed())])
    agent = Agent(ScriptedProvider([f.reply(f.original())]))
    agent.send('최초 전략')
    session = Plan(model, lambda: agent, context_loader=snapshot, executor=Mock(),
                   previewer=lambda _: {'filename': 'Test_SPECIAL002.py'})
    first = session.send('이 전략 골드 9월 백테스트')
    before = copy.deepcopy(session.pending['steps'][0]['command']['request'])
    agent.provider = model
    changed = agent.send('아니 원비는 15분이야')
    second = session.update_strategy(changed)
    assert second['can_confirm'] and session.pending['steps'][0]['command']['request'] == before
    assert second['strategy']['is_revision']
    with pytest.raises(ValueError):
        session.confirm(first['revision'])


@pytest.mark.parametrize('missing', ['symbol', 'start', 'end'])
def test_missing_parameters_never_create_or_execute(missing):
    agent = Agent(ScriptedProvider([]))
    model = ScriptedProvider([reply(plan(step(**{missing: None})))])
    generate, execute = Mock(), Mock()
    session = Plan(model, lambda: agent, context_loader=snapshot, executor=execute, generator=generate)
    result = session.send('스페셜1 최근 1년')
    assert result['needs_clarification'] and not result['can_confirm']
    with pytest.raises(ValueError):
        session.confirm(result['revision'])
    generate.assert_not_called()
    execute.assert_not_called()


def test_second_job_invalid_means_no_partial_start():
    agent = Agent(ScriptedProvider([]))
    model = ScriptedProvider([reply(plan(step(), step(specials=['not-existing'])))])
    execute = Mock()
    session = Plan(model, lambda: agent, context_loader=snapshot, executor=execute)
    with pytest.raises(ValueError):
        session.send('A 끝나면 B')
    execute.assert_not_called()


def test_execution_settings_change_blocks_generation_and_start():
    agent = Agent(ScriptedProvider([]))
    state = snapshot()
    model = ScriptedProvider([reply(plan(step()))])
    execute = Mock()
    session = Plan(model, lambda: agent, context_loader=lambda: copy.deepcopy(state), executor=execute)
    result = session.send('스페셜1 백테스트')
    state['execution_defaults']['cores'] = 20
    with pytest.raises(ValueError, match='설정'):
        session.confirm(result['revision'])
    execute.assert_not_called()


def sequence(tmp_path, monkeypatch):
    statuses = {JOB: {'phase': 'run', 'active': True, 'result_ready': False},
                OTHER_JOB: {'phase': 'planning', 'active': True, 'result_ready': False}}
    execute = Mock(side_effect=[{'job_id': JOB, 'phase': 'planning'}, {'job_id': OTHER_JOB, 'phase': 'planning'}])
    saved = {'result_mode': 'VIRTUAL_ENTRY', 'virtual_entry': {'summary': [{'win_rate': 50}]}}
    result_reader = Mock(return_value=copy.deepcopy(saved))
    info = sequences.start([{'action': 'START', 'request': request()},
                            {'action': 'START', 'request': request(symbol='NAS100')}],
        executor=execute, status_reader=lambda id: statuses[id], result_reader=result_reader,
        folder=tmp_path / 'warehouse/runs/sequences', background=False)
    state = sequences._running[info['sequence_id']]
    return state, statuses, execute, result_reader


def test_sequence_waits_for_save_and_exit_then_reports_existing_results(tmp_path, monkeypatch):
    state, statuses, execute, results = sequence(tmp_path, monkeypatch)
    sequences.advance(state)
    assert execute.call_count == 1
    statuses[JOB].update(phase='complete', active=True, result_ready=True)
    sequences.advance(state)
    assert execute.call_count == 1
    statuses[JOB]['active'] = False
    sequences.advance(state)
    assert execute.call_count == 2 and len(state['record']['jobs']) == 2
    statuses[OTHER_JOB].update(phase='complete', active=False, result_ready=True)
    sequences.advance(state)
    assert state['record']['phase'] == 'complete' and results.call_count == 2
    assert state['record']['jobs'][0]['result']['virtual_entry']['summary'][0]['win_rate'] == 50
    # Only portable filenames and request data are saved; captures are untouched.
    saved = state['path'].read_text('utf-8')
    assert str(tmp_path) not in saved and 'C:' not in saved
    moved = tmp_path / 'relocated'
    (tmp_path / 'warehouse').rename(moved)
    monkeypatch.setattr(sequences, '_running', {})
    monkeypatch.setattr(sequences.unified_backtest, 'warehouse', lambda: moved)
    assert sequences.status(state['record']['sequence_id'])['phase'] == 'complete'


@pytest.mark.parametrize('phase,result_ready', [('error', False), ('cancelled', False), ('interrupted', False), ('complete', False)])
def test_failed_or_unsaved_child_cannot_start_next(tmp_path, monkeypatch, phase, result_ready):
    state, statuses, execute, results = sequence(tmp_path, monkeypatch)
    statuses[JOB].update(phase=phase, active=False, result_ready=result_ready)
    sequences.advance(state)
    assert state['record']['phase'] != 'run' and execute.call_count == 1
    results.assert_not_called()


def test_close_cancel_preserves_queue_but_actual_shutdown_cancels_future_work(tmp_path, monkeypatch):
    state, statuses, execute, _ = sequence(tmp_path, monkeypatch)
    statuses[JOB].update(phase='complete', active=False, result_ready=True)
    backtest_jobs.begin_shutdown()
    assert not state['record']['cancel_requested']
    with pytest.raises(ValueError, match='종료 중'):
        sequences.advance(state)
    assert execute.call_count == 1
    backtest_jobs.cancel_shutdown()
    sequences.begin_shutdown()
    sequences.advance(state)
    assert state['record']['phase'] == 'cancelled' and execute.call_count == 1


def test_queue_stop_cancels_remaining_work(tmp_path, monkeypatch):
    state, statuses, execute, _ = sequence(tmp_path, monkeypatch)
    stop = Mock(return_value={'ok': True})
    monkeypatch.setattr(sequences.unified_backtest, 'stop', stop)
    sequences.stop(state['record']['sequence_id'])
    statuses[JOB].update(phase='complete', active=False, result_ready=True)
    sequences.advance(state)
    stop.assert_called_once_with(JOB)
    assert state['record']['phase'] == 'cancelled' and execute.call_count == 1


def test_reset_clears_only_this_research_conversation(monkeypatch):
    a, b = object(), object()
    for name in ('RESEARCH_SESSIONS', 'AI_SESSIONS', 'BACKTEST_COMMAND_SESSIONS'):
        monkeypatch.setattr(server, name, {'a': a, 'b': b})
    assert server.ai_post('/api/ai/reset', {'session': 'a', 'research': True}) == {'ok': True}
    for name in ('RESEARCH_SESSIONS', 'AI_SESSIONS', 'BACKTEST_COMMAND_SESSIONS'):
        assert getattr(server, name) == {'b': b}


def test_unrepresented_strategy_symbol_never_silently_runs_zero_signal_backtest():
    f = fixture()
    agent = Agent(ScriptedProvider([f.reply(f.original())]))
    agent.send('골드 전략')
    session = Plan(ScriptedProvider([reply(plan(step(draft=True, target_mode='GENERATED', specials=[], filename=None, symbol='NAS100')))]),
                   lambda: agent, context_loader=snapshot, executor=Mock(), previewer=lambda _: {'filename': 'Test_SPECIAL002.py'})
    result = session.send('이 전략 나스닥으로 백테스트')
    assert not result['can_confirm'] and '감시 종목' in result['clarification_question']
    session.executor.assert_not_called()


def test_sequence_setting_change_and_modified_generated_file_prevent_next_child(tmp_path, monkeypatch):
    state, statuses, execute, _ = sequence(tmp_path, monkeypatch)
    statuses[JOB].update(phase='complete', active=False, result_ready=True)
    validator = Mock(side_effect=ValueError('확인한 설정이 변경되었습니다.'))
    state['validator'] = validator
    with pytest.raises(ValueError, match='설정'):
        sequences.advance(state)
    assert execute.call_count == 1
    state['validator'] = None
    import hashlib
    strategy = tmp_path / 'Test_SPECIAL002.py'
    strategy.write_text('original', encoding='utf-8')
    state['record']['strategy_digests'] = {strategy.name: hashlib.sha256(strategy.read_bytes()).hexdigest()}
    monkeypatch.setattr(storage, 'generated_path', lambda _: strategy)
    strategy.write_text('modified', encoding='utf-8')
    with pytest.raises(ValueError, match='전략 파일'):
        sequences.advance(state)
    assert execute.call_count == 1


def test_unsupported_canonical_condition_stops_the_whole_research_plan():
    from lab.ai.intent import unsupported
    model = ScriptedProvider([reply(plan(step(draft=True, target_mode='GENERATED', specials=[], filename=None),
        strategy_text='현재 schema에 없는 동적 가격 조건')), reply(unsupported('UNSUPPORTED_FEATURE', '이 조건은 현재 지원하지 않습니다.'))])
    agent = Agent(model)
    session = Plan(model, lambda: agent, context_loader=snapshot, executor=Mock(), generator=Mock())
    result = session.send('지원하지 않는 조건으로 백테스트')
    assert not result['can_confirm'] and result['needs_clarification']
    session.executor.assert_not_called()
    session.generator.assert_not_called()


def test_period_followup_uses_corrected_canonical_instead_of_replaying_old_strategy_text():
    f = fixture()
    model = ScriptedProvider([reply(plan(step(draft=True, target_mode='GENERATED', specials=[], filename=None),
        strategy_text='15분 추세와 3분 하단 원비')), f.reply(f.original()),
        reply(plan(step(draft=True, target_mode='GENERATED', specials=[], filename=None, start='2025-09-01')))])
    agent = Agent(model)
    session = Plan(model, lambda: agent, context_loader=snapshot, executor=Mock(),
                   previewer=lambda _: {'filename': 'Test_SPECIAL002.py'})
    session.send('새 조건 전략으로 1년 백테스트')
    assert session.last_plan['strategy_text'] is None
    agent.provider = ScriptedProvider([f.reply(f.changed())])
    changed = agent.send('원비는 15분이야')
    session.update_strategy(changed)
    result = session.send('시작일만 2025년 9월로')
    sent = json.loads(model.seen[-1]['content'])
    assert sent['context']['previous_plan']['strategy_text'] is None
    assert sent['context']['current_strategy'] == f.changed()
    assert agent.last_intent == f.changed() and result['can_confirm']
    assert session.pending['steps'][0]['command']['request']['start'] == '2025-09-01'
