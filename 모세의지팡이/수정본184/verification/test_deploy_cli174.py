"""174: `Part3/cli.py backtest` runs a generated strategy with the screen's settings, without promotion.

An installed MOSES has no sources. An AI working in it makes a strategy with `cli.py generate` and
tests it with `cli.py backtest`: the same durable job and the same scenario checks as the screen,
with the result mode, virtual entry (a policy file or a base frame), spread and held-data-only choice.
"""
from __future__ import annotations

import hashlib
import json
import sys
from unittest.mock import Mock

import pytest

from test_generated_backtest_adapter67 import integration, load, project  # noqa: F401  (project is a fixture)


@pytest.fixture
def library(project, monkeypatch):
    """The generated file with its recipe beside it, read as the library of the moved project (as 163)."""
    import ast
    from strategy_recipe import user_catalog
    code = project.strategy.read_text('utf-8')
    recipe = ast.literal_eval(code.split('PART3_RECIPE = ', 1)[1].split('\n', 1)[0])
    project.strategy.with_suffix('.recipe.json').write_text(json.dumps(
        {'filename': project.strategy.name, 'recipe': recipe,
         'sha256': hashlib.sha256(project.strategy.read_bytes()).hexdigest()}), encoding='utf-8')
    read = user_catalog.generated_entries
    monkeypatch.setattr(user_catalog, 'generated_entries', lambda root=None, errors=None: read(project.root, errors))
    return project


def run(monkeypatch, project, *arguments, phases=('complete',), plan=None, start=None):
    """The CLI against the real Part3 adapter and scenario checks; only the job launch and its status are stand-ins."""
    from lab import backtest_jobs
    cli = load('cli')
    start = start or Mock(return_value={'job_id': project.identifier, 'kind': 'generated', 'phase': 'planning'})
    monkeypatch.setattr(backtest_jobs, 'start', start)
    status = Mock(side_effect=[{'phase': phase, 'running': False, 'returncode': 1 if phase == 'error' else 0,
                                'log': '', 'plan': plan, 'message': 'Part2 실행 오류' if phase == 'error' else ''}
                               for phase in phases])
    monkeypatch.setattr(integration, 'job_status', status)
    monkeypatch.setattr(cli.time, 'sleep', lambda seconds: None)
    monkeypatch.setattr(sys, 'argv', ['cli.py', 'backtest', project.strategy.name,
                                      '--start', '2026-01-01', '--end', '2026-02-01', *arguments])
    return cli.main(), start, status


def other_base(name):
    from event_backtest.virtual_defaults import strategy_profile
    profile = strategy_profile(name)
    return next(tf for tf in profile['bases'] if tf != profile['base'])


def test_screen_settings_reach_the_generated_scenario(library, monkeypatch, capsys):
    code, start, status = run(monkeypatch, library, '--action', 'run', '--mode', 'BAR', '--cores', '2',
                              '--result-mode', 'VIRTUAL_ENTRY', '--spread-points', '12', '--available-only')
    assert code == 0
    trusted = start.call_args.args[0]
    scenario = trusted['scenario']
    assert trusted['kind'] == 'generated' and trusted['plan_only'] is False
    assert scenario['strategies'] == ['Test_SPECIAL777'] and scenario['oz_evaluation'] == 'all'
    assert scenario['mode'] == 'BAR' and scenario['cores'] == 2
    assert scenario['result_mode'] == 'VIRTUAL_ENTRY' and scenario['spread_points'] == {'XAUUSD+': 12.0}
    assert scenario['available_only'] is True
    # No policy given: the recipe's own (this recipe has none, so immediate entry with an ATR stop).
    assert scenario['virtual_entry']['mode'] == 'IMMEDIATE' and scenario['virtual_entry']['tf'] == 'SIGNAL'
    assert scenario['base_frames'] == {}
    assert '결과 폴더(창고 기준): runs/' + library.identifier in capsys.readouterr().out


def test_without_options_the_old_alert_only_plan_is_unchanged(library, monkeypatch, capsys):
    plan = {'record': [], 'convert': [], 'estimate': {}, 'period_adjustment': None,
            'available_periods': [], 'excluded_periods': []}
    code, start, _ = run(monkeypatch, library, phases=('planned',), plan=plan)
    assert code == 0
    trusted = start.call_args.args[0]
    scenario = trusted['scenario']
    assert trusted['plan_only'] is True and scenario['mode'] == 'TICK'
    # Workers as on the screen: the saved setting (2 here; when unset, the run takes this PC's measured count).
    assert scenario['cores'] == 2 and trusted['adapter']['cores'] == 2
    assert scenario['result_mode'] == 'ALERT_ONLY' and scenario['virtual_entry'] is None
    assert scenario['spread_points'] == {'XAUUSD+': 0.0} and scenario['available_only'] is False
    output = capsys.readouterr().out
    assert '"record": []' in output and '결과 폴더' not in output       # a plan shows the plan, no result


def test_a_policy_file_is_the_tests_virtual_entry(library, monkeypatch, tmp_path):
    from event_backtest.virtual_contract import immediate_virtual_entry
    policy = immediate_virtual_entry()
    policy['stop']['multiplier'] = 2.0
    path = tmp_path / '가상진입.json'
    path.write_text(json.dumps(policy), encoding='utf-8')
    code, start, _ = run(monkeypatch, library, '--action', 'run', '--result-mode', 'VIRTUAL_ENTRY',
                         '--virtual-entry', str(path))
    assert code == 0
    scenario = start.call_args.args[0]['scenario']
    assert scenario['virtual_entry']['stop'] == {'kind': 'ATR', 'tf': 'SIGNAL', 'bars': 5, 'period': 14,
                                                 'multiplier': 2.0}


def test_a_policy_that_sets_its_own_frame_is_refused_before_the_job(library, monkeypatch, tmp_path):
    from event_backtest.virtual_contract import immediate_virtual_entry
    policy = immediate_virtual_entry()
    policy['atr']['tf'] = '5m'                                          # frames come only from the base frame
    path = tmp_path / 'policy.json'
    path.write_text(json.dumps(policy), encoding='utf-8')
    with pytest.raises(ValueError, match='기준 프레임으로만'):
        run(monkeypatch, library, '--action', 'run', '--result-mode', 'VIRTUAL_ENTRY', '--virtual-entry', str(path),
            start=Mock(side_effect=AssertionError('no job')))


def test_a_base_frame_refills_the_entry_from_the_recipe_on_that_frame(library, monkeypatch):
    from event_backtest.virtual_defaults import recipe_on_base, strategy_profile
    target = other_base('Test_SPECIAL777')
    code, start, _ = run(monkeypatch, library, '--action', 'run', '--result-mode', 'VIRTUAL_ENTRY',
                         '--base-frame', target)
    assert code == 0
    scenario = start.call_args.args[0]['scenario']
    assert scenario['virtual_entry']['tf'] == target
    assert scenario['virtual_entry'] == recipe_on_base(strategy_profile('Test_SPECIAL777'), target)
    assert scenario['base_frames'] == {'Test_SPECIAL777': target}       # the replay moves the strategy too


def test_a_frame_off_the_strategys_table_is_refused(library, monkeypatch):
    with pytest.raises(ValueError, match='기준 프레임은'):
        run(monkeypatch, library, '--action', 'run', '--result-mode', 'VIRTUAL_ENTRY', '--base-frame', '7m',
            start=Mock(side_effect=AssertionError('no job')))


@pytest.mark.parametrize('arguments, message', [
    (('--virtual-entry', 'policy.json'), 'VIRTUAL_ENTRY에서만'),
    (('--base-frame', '5m'), 'VIRTUAL_ENTRY에서만'),
    (('--result-mode', 'VIRTUAL_ENTRY', '--virtual-entry', 'policy.json', '--base-frame', '5m'), '함께 쓸 수 없습니다'),
])
def test_virtual_entry_options_without_their_mode_start_nothing(library, monkeypatch, arguments, message):
    with pytest.raises(ValueError, match=message):
        run(monkeypatch, library, *arguments, start=Mock(side_effect=AssertionError('no job')))


def test_the_run_and_its_status_use_the_connected_warehouse(library, monkeypatch):
    code, start, status = run(monkeypatch, library, '--action', 'run')
    assert code == 0
    store = library.warehouse.resolve()
    assert start.call_args.args[0]['warehouse'] == store
    assert status.call_args.kwargs['warehouse'] == store


def test_a_relative_warehouse_follows_the_project_wherever_it_moves(library, monkeypatch, tmp_path):
    from lab import storage
    code, start, status = run(monkeypatch, library, '--action', 'run', '--warehouse', '데이터창고')
    assert code == 0
    store = (library.root / '데이터창고').resolve()
    assert start.call_args.args[0]['warehouse'] == store and status.call_args.kwargs['warehouse'] == store
    moved = tmp_path / '다른 PC' / '모세'
    monkeypatch.setattr(storage, 'project_path', lambda: str(moved))
    assert integration.warehouse({'warehouse': '데이터창고'}) == (moved / '데이터창고').resolve()
    with pytest.raises(ValueError, match='상위 폴더'):
        integration.warehouse({'warehouse': '../밖'})


def test_job_commands_read_the_same_relative_warehouse(library, monkeypatch):
    from lab import backtest_jobs
    cli = load('cli')
    monkeypatch.setattr(sys, 'argv', ['cli.py', 'job', 'status', library.identifier, '--warehouse', '데이터창고'])
    reconnect = Mock(return_value={'job_id': library.identifier, 'phase': 'complete'})
    monkeypatch.setattr(backtest_jobs, 'reconnect', reconnect)
    assert cli.main() == 0
    reconnect.assert_called_once_with(library.identifier, warehouse=(library.root / '데이터창고').resolve())


def test_a_failed_run_reports_its_message_and_fails(library, monkeypatch, capsys):
    code, _, _ = run(monkeypatch, library, '--action', 'run', phases=('error',))
    assert code == 1
    output = capsys.readouterr().out
    assert 'Part2 실행 오류' in output and '결과 폴더' not in output
