"""Generated strategies share approval, cancellation and portable run artifacts."""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT / 'Part3', ROOT / 'Part2', ROOT / 'Part1' / 'program'):
    sys.path.insert(0, str(path))
from lab import backtest_adapters as adapter, integration, storage
from event_backtest import settings, workflow, runner


def load(name):
    spec = importlib.util.spec_from_file_location('adapter67_' + name, ROOT / 'Part3' / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def project(monkeypatch, tmp_path):
    root = tmp_path / '이동된 프로젝트'
    part3 = root / 'Part3'
    folder = part3 / 'generated'
    folder.mkdir(parents=True)
    strategy = folder / 'Test_SPECIAL777.py'
    recipe = dict(schema_version=2, base='AI', name='Selected generated',
        strategy_intent=dict(symbols=['XAUUSD+'], direction='LONG', order_mode='SIMULTANEOUS',
            steps=[dict(kind='MA_STATE', tfs=['1m'], ma_family='HMA',
                        fast_period=90, slow_period=270, side='ABOVE', bar_state='CLOSED')],
            final=dict(kind='OZ', tfs=['1m'], validation_mode='NORMAL', trigger_mode='OZ')))
    strategy.write_text('PART3_RECIPE = ' + repr(recipe) + '\nPART3_COMPILE_MODE = "CANONICAL"\n'
        'raise RuntimeError("saved source must not execute")\n'
        'def register(manager):\n    raise RuntimeError("old source register must not execute")\n', encoding='utf-8')
    warehouse = tmp_path / '이동된 창고'
    identifier = 'a' * 32
    jobfolder = warehouse / 'runs' / identifier
    jobfolder.mkdir(parents=True)
    monkeypatch.setattr(storage, 'ROOT', part3)
    monkeypatch.setattr(storage, 'connections', lambda: {'warehouse': str(warehouse), 'python_executable': ''})
    monkeypatch.setattr(storage, 'project_path', lambda: str(root))
    monkeypatch.setattr(adapter, '_part2', lambda project_root: settings)
    monkeypatch.setattr(settings, 'settings', lambda: {'cores': 2, 'work_size': 'MONTH',
        'capture_start': 'keyframe', 'overlap_trading_days': 3, 'oz_evaluation': 'selected'})
    monkeypatch.delenv('PART3_BT_REQUEST', raising=False)
    return SimpleNamespace(root=root, part3=part3, strategy=strategy, warehouse=warehouse,
                           identifier=identifier, folder=jobfolder)


def data(project, **changes):
    return {'filename': project.strategy.name, 'symbol': 'XAUUSD+', 'start': '2026-01-01',
            'end': '2026-02-01', 'mode': 'BAR', **changes}


def job(project, **changes):
    scenario, options = adapter.prepare_generated(data(project), project.root)
    return {'id': project.identifier, 'project_root': project.root, 'warehouse': project.warehouse,
            'folder': project.folder, 'python_executable': sys.executable,
            'scenario': scenario, 'adapter': options, 'rebuild': True, **changes}


def request(project, **changes):
    record = job(project)
    adapter.command(record, 'plan')
    spec = json.loads((project.folder / 'generated_request.json').read_text('utf-8'))
    return {**spec, **changes}


def entry(project, monkeypatch, **changes):
    module = load('backtest')
    spec = request(project, **changes)
    monkeypatch.setenv('PART3_BT_PROJECT_ROOT', str(project.root))
    monkeypatch.setenv('PART3_BT_WAREHOUSE', str(project.warehouse))
    module.REQUEST = spec
    module.STRATEGY_KEY = module.initialize(spec)
    import event_application
    yield module
    event_application.unregister_strategy_loader(module.STRATEGY_KEY)


def test_generated_scenario_selects_only_the_file_and_shared_ma_dependencies(project):
    scenario, options = adapter.prepare_generated(data(project, result_mode='VIRTUAL_ENTRY', spread_points=12), project.root)
    assert scenario['strategies'] == ['Test_SPECIAL777']
    assert scenario['oz_evaluation'] == 'all'
    assert scenario['result_mode'] == 'VIRTUAL_ENTRY' and scenario['spread_points'] == {'XAUUSD+': 12.0}
    assert scenario['cores'] == 2
    assert options['strategy_sha256'] == hashlib.sha256(project.strategy.read_bytes()).hexdigest()


@pytest.mark.parametrize('changes', [
    {'filename': '../Part1/program/SPECIAL/SPECIAL1.py'}, {'filename': 'SPECIAL1.py'},
    {'filename': 'Test_SPECIAL999.py'}, {'filename': None},
    {'mode': 'wrong'}, {'symbol': 'bad symbol'}, {'start': '2026-03-01'},
    {'result_mode': 'wrong'}, {'spread_points': -1}, {'spread_points': float('nan')},
    {'cores': 0}, {'cores': True}, {'cores': 1.5}, {'build_only': True},
])
def test_invalid_generated_request_is_blocked_before_start(project, changes):
    with pytest.raises((ValueError, FileNotFoundError, TypeError)):
        adapter.prepare_generated(data(project, **changes), project.root)


def test_generated_command_persists_only_root_relative_references(project):
    record = job(project)
    args, env, cwd = adapter.command(record, 'run', approved='shown token')
    content = (project.folder / 'generated_request.json').read_text('utf-8')
    spec = json.loads(content)
    assert spec['project_root'] == '.' and spec['warehouse'] == '.'
    assert spec['strategy'] == 'Part3/generated/Test_SPECIAL777.py'
    assert spec['job_dir'] == 'runs/' + project.identifier and spec['session_id'] == project.identifier
    assert spec['approved_token'] == 'shown token' and spec['rebuild'] is True
    assert str(project.root) not in content and str(project.warehouse) not in content
    assert args == [sys.executable, '-B', '-X', 'utf8', str(project.root / 'Part3' / 'backtest.py')]
    assert env['PART3_BT_PROJECT_ROOT'] == str(project.root) and env['PART3_BT_WAREHOUSE'] == str(project.warehouse)
    assert cwd == project.root / 'Part2'


def test_strategy_changed_after_plan_is_blocked(project):
    record = job(project)
    project.strategy.write_text('def register(manager):\n    pass\n', encoding='utf-8')
    with pytest.raises(ValueError, match='변경'):
        adapter.command(record, 'run', approved='old token')
    assert not (project.folder / 'generated_request.json').exists()


@pytest.mark.parametrize('value', ['../outside.json', 'C:/outside.json', '/outside.json', r'..\outside.json'])
def test_relative_reference_rejects_root_escape(project, value):
    with pytest.raises(ValueError):
        adapter.relative_reference(project.root, value)


def test_portable_request_resolves_after_both_roots_move(project, monkeypatch, tmp_path):
    spec = request(project)
    moved = tmp_path / '별도 위치'
    moved_root = moved / '프로젝트'
    moved_store = moved / '창고'
    target = moved_root / spec['strategy']
    target.parent.mkdir(parents=True)
    target.write_bytes(project.strategy.read_bytes())
    (moved_store / spec['job_dir']).mkdir(parents=True)
    monkeypatch.setenv('PART3_BT_PROJECT_ROOT', str(moved_root))
    monkeypatch.setenv('PART3_BT_WAREHOUSE', str(moved_store))
    paths = load('backtest').resolve_request(spec)
    assert paths['strategy'] == target and paths['job_dir'] == moved_store / spec['job_dir']
    assert paths['project_root'] == moved_root


@pytest.mark.parametrize('changes', [
    {'project_root': 'C:/machine'}, {'warehouse': '../warehouse'},
    {'job_dir': '../outside'}, {'job_dir': 'runs/' + 'b' * 32},
    {'strategy': 'Part1/program/SPECIAL/SPECIAL1.py'},
    {'session_id': 'old12id'}, {'strategy_sha256': 'wrong'},
])
def test_child_revalidates_portable_request_before_engine_registration(project, monkeypatch, changes):
    spec = request(project, **changes)
    monkeypatch.setenv('PART3_BT_PROJECT_ROOT', str(project.root))
    monkeypatch.setenv('PART3_BT_WAREHOUSE', str(project.warehouse))
    with pytest.raises((ValueError, FileNotFoundError)):
        load('backtest').resolve_request(spec)


def test_real_public_loader_registers_only_selected_recipe_via_official_api(project, monkeypatch):
    generator = entry(project, monkeypatch)
    module = next(generator)
    try:
        import event_application
        _, _, plugins = event_application.load_strategy_inputs({}, (module.STRATEGY_KEY,), {})
        assert list(plugins) == ['Test_SPECIAL777']
        record = []
        api = SimpleNamespace(market_context=lambda: (None,'XAUUSD+',0),
            shared_resource=lambda name,factory: factory(),
            register_subscription_provider=lambda name,provider: record.append(('subscription',name)),
            register_fact_observer=lambda name,observer: record.append(('fact',name)),
            register_strategy_state_provider=lambda name,provider: record.append(('state',name)),
            register_watch_handler=lambda name,handler: record.append(('watch',name)),
            register_oz_handler=lambda name,handler: record.append(('oz',name)),
            snapshot_oz_watches=lambda **kwargs: [])
        manager = SimpleNamespace(special_api=api)
        plugins['Test_SPECIAL777'].register(manager)
        assert record[:4] == [(kind,'Test_SPECIAL777') for kind in ('subscription','fact','state','watch')]
        assert len(record) == 5 and record[4][0] == 'oz'
        assert plugins['Test_SPECIAL777']._INTENT_PLAN['steps'][0]['kind'] == 'MA_STATE'
        assert plugins['Test_SPECIAL777'].OZ_DECLARATIONS == frozenset({('1m','NORMAL','OZ')})
    finally:
        generator.close()


def test_loader_rechecks_selected_file_when_replay_worker_imports_it(project, monkeypatch):
    generator = entry(project, monkeypatch)
    module = next(generator)
    try:
        import event_application
        project.strategy.write_text('def register(manager):\n    manager.record.append("changed")\n', encoding='utf-8')
        with pytest.raises(ValueError, match='변경'):
            event_application.load_strategy_inputs({}, (module.STRATEGY_KEY,), {})
    finally:
        generator.close()


def test_plan_uses_existing_proposal_and_never_writes_fake_result(project, monkeypatch, capsys):
    generator = entry(project, monkeypatch)
    module = next(generator)
    plan = {'record': [{'start': '2026-01-01'}], 'approval_token': 'fresh plan token'}
    proposal = Mock(return_value=plan)
    monkeypatch.setattr(workflow, 'proposal', proposal)
    monkeypatch.setattr(workflow, 'execute', Mock(side_effect=AssertionError('plan cannot run')))
    monkeypatch.setattr(runner, 'code_hash', lambda: 'engine hash')
    try:
        assert module.main() == 0
        scenario = proposal.call_args.args[0]
        assert scenario['_run_id'] == project.identifier and scenario['strategies'] == ['Test_SPECIAL777']
        assert proposal.call_args.kwargs == {'rebuild': True}
        assert not (project.folder / 'result.json').exists()
        output = json.loads(capsys.readouterr().out.strip())
        assert output == {'event': 'COMPLETE', 'result': plan}
    finally:
        generator.close()


def test_run_reuses_token_session_progress_and_cooperative_stop(project, monkeypatch, capsys):
    generator = entry(project, monkeypatch, action='run', approved_token='approved actual plan')
    module = next(generator)
    monkeypatch.setattr(runner, 'code_hash', lambda: 'engine hash')
    from event_backtest.cancellation import Cancelled
    called = []
    def execute(scenario, warehouse, **options):
        called.append((scenario, warehouse, options))
        assert options['yes'] is False and options['approved_token'] == 'approved actual plan'
        options['cancel']()
        (project.folder / 'stop.request').write_text('STOP\n', encoding='ascii')
        with pytest.raises(Cancelled):
            options['cancel']()
        options['emit']('BUILD_VERIFIED', {'pieces': 1})
        result = {'status': 'CANCELLED', 'run_id': project.identifier,
                  'result_path': 'runs/' + project.identifier + '/result.json'}
        (project.folder / 'result.json').write_text(json.dumps(result), encoding='utf-8')
        return result
    monkeypatch.setattr(workflow, 'execute', execute)
    try:
        assert module.main() == 0
        scenario, warehouse, options = called[0]
        assert scenario['_run_id'] == project.identifier and warehouse == project.warehouse
        assert options['cores'] == 2 and options['rebuild'] is True
        events = [json.loads(line) for line in (project.folder / 'progress.jsonl').read_text('utf-8').splitlines()]
        assert [row['event'] for row in events] == ['BUILD_VERIFIED', 'COMPLETE']
        assert events[-1]['result']['status'] == 'CANCELLED'
        assert str(project.root) not in capsys.readouterr().out
        digest = hashlib.sha256(project.strategy.read_bytes()).hexdigest()
        assert runner.code_hash() == hashlib.sha256(('engine hash' + digest).encode()).hexdigest()
    finally:
        generator.close()


def test_changed_plan_is_reconfirmed_instead_of_yes_bypass(project, monkeypatch, capsys):
    generator = entry(project, monkeypatch, action='run', approved_token='old plan')
    module = next(generator)
    from event_backtest.build_plan import ConfirmationRequired
    new_plan = {'record': [{'start': '2026-01-15'}], 'approval_token': 'new plan'}
    monkeypatch.setattr(runner, 'code_hash', lambda: 'engine hash')
    monkeypatch.setattr(workflow, 'execute', Mock(side_effect=ConfirmationRequired(new_plan)))
    try:
        assert module.main() == 2
        result = json.loads(capsys.readouterr().out.strip())
        assert result == {'event': 'CONFIRMATION_REQUIRED', **new_plan}
    finally:
        generator.close()


def test_spawn_import_initializes_same_portable_request(project, monkeypatch):
    spec = request(project)
    path = project.folder / 'generated_request.json'
    path.write_text(json.dumps(spec), encoding='utf-8')
    monkeypatch.setenv('PART3_BT_REQUEST', str(path))
    monkeypatch.setenv('PART3_BT_PROJECT_ROOT', str(project.root))
    monkeypatch.setenv('PART3_BT_WAREHOUSE', str(project.warehouse))
    import event_application
    module = load('backtest')
    try:
        assert module.REQUEST_ERROR is None and module.STRATEGY_KEY == 'Test_SPECIAL777'
        _, _, plugins = event_application.load_strategy_inputs({}, (module.STRATEGY_KEY,), {})
        assert list(plugins) == ['Test_SPECIAL777']
    finally:
        event_application.unregister_strategy_loader('Test_SPECIAL777')


def test_integration_delegates_plan_only_to_durable_common_job(project, monkeypatch):
    from lab import backtest_jobs
    start = Mock(return_value={'job_id': 'c' * 32, 'phase': 'planning'})
    monkeypatch.setattr(backtest_jobs, 'start', start)
    output = integration.backtest(data(project, action='plan'))
    trusted = start.call_args.args[0]
    assert output['job_id'] == 'c' * 32
    assert trusted['plan_only'] is True and trusted['kind'] == 'generated'
    assert trusted['warehouse'] == project.warehouse
    assert trusted['adapter']['filename'] == project.strategy.name
    assert not (project.part3 / 'logs').exists()


def test_integration_run_does_not_directly_spawn_or_approve(project, monkeypatch):
    from lab import backtest_jobs
    start = Mock(return_value={'job_id': 'c' * 32, 'phase': 'planning'})
    monkeypatch.setattr(backtest_jobs, 'start', start)
    monkeypatch.setattr(integration.subprocess, 'Popen', Mock(side_effect=AssertionError('common manager owns process')))
    integration.backtest(data(project, action='run'))
    assert start.call_args.args[0]['plan_only'] is False
    assert 'approved_token' not in start.call_args.args[0]


def test_job_status_reconnects_through_common_status_and_bounded_log(project, monkeypatch):
    from lab import backtest_jobs
    status = Mock(return_value={'job_id': project.identifier, 'phase': 'complete', 'active': False,
                                'filename': project.strategy.name})
    log = Mock(return_value={'text': 'bounded shared log'})
    monkeypatch.setattr(backtest_jobs, 'status', status)
    monkeypatch.setattr(backtest_jobs, 'log', log)
    info = integration.job_status(project.identifier, warehouse=project.warehouse)
    assert info['running'] is False and info['returncode'] == 0 and info['log'] == 'bounded shared log'
    status.assert_called_once_with(project.identifier, warehouse=project.warehouse)
    log.assert_called_once_with(project.identifier, warehouse=project.warehouse)


def test_cli_run_shows_plan_and_uses_its_revision_for_explicit_approval(project, monkeypatch, capsys):
    from lab import backtest_jobs
    cli = load('cli')
    monkeypatch.setattr(sys, 'argv', ['cli.py', 'backtest', project.strategy.name, '--start', '2026-01-01',
        '--end', '2026-02-01', '--warehouse', str(project.warehouse), '--action', 'run', '--yes'])
    monkeypatch.setattr(integration, 'backtest', Mock(return_value={'job_id': project.identifier, 'message': 'planning'}))
    monkeypatch.setattr(integration, 'job_status', Mock(side_effect=[
        {'phase': 'confirm', 'running': False, 'returncode': 0, 'log': '',
         'plan': {'record': [1]}, 'plan_revision': 'shown revision'},
        {'phase': 'complete', 'running': False, 'returncode': 0, 'log': 'finished\n'}]))
    confirm = Mock()
    monkeypatch.setattr(backtest_jobs, 'confirm', confirm)
    monkeypatch.setattr(cli.time, 'sleep', lambda seconds: None)
    assert cli.main() == 0
    assert confirm.call_args.kwargs['plan_revision'] == 'shown revision'
    assert 'record' in capsys.readouterr().out


def test_cli_without_approval_leaves_plan_pending(project, monkeypatch):
    from lab import backtest_jobs
    cli = load('cli')
    monkeypatch.setattr(sys, 'argv', ['cli.py', 'backtest', project.strategy.name, '--start', '2026-01-01',
        '--end', '2026-02-01', '--warehouse', str(project.warehouse), '--action', 'run'])
    monkeypatch.setattr(sys, 'stdin', SimpleNamespace(isatty=lambda: False))
    monkeypatch.setattr(integration, 'backtest', Mock(return_value={'job_id': project.identifier, 'message': 'planning'}))
    monkeypatch.setattr(integration, 'job_status', Mock(return_value={'phase': 'confirm', 'running': False,
        'returncode': 0, 'log': '', 'plan': {'record': [1]}, 'plan_revision': 'shown revision'}))
    confirm = Mock(side_effect=AssertionError('unconfirmed plan'))
    monkeypatch.setattr(backtest_jobs, 'confirm', confirm)
    assert cli.main() == 0
    confirm.assert_not_called()


def test_cli_job_status_uses_recoverable_common_lookup(project, monkeypatch):
    from lab import backtest_jobs
    cli = load('cli')
    monkeypatch.setattr(sys, 'argv', ['cli.py', 'job', 'status', project.identifier,
                                    '--warehouse', str(project.warehouse)])
    reconnect = Mock(return_value={'job_id': project.identifier, 'phase': 'complete'})
    monkeypatch.setattr(backtest_jobs, 'reconnect', reconnect)
    assert cli.main() == 0
    reconnect.assert_called_once_with(project.identifier, warehouse=project.warehouse)
