"""Only warehouse folder choices and selected finished results are affected."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
from unittest.mock import Mock

import duckdb
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'Part3'), str(ROOT / 'Part2'), str(ROOT / 'Part1/program')]
from lab import backtest_jobs as jobs, unified_backtest, unified_live, server
from event_backtest import ui_model
from test_backtest_routes67 import handler


@pytest.fixture
def store(tmp_path, monkeypatch):
    warehouse, project = tmp_path / '창고', tmp_path / '프로젝트'
    warehouse.mkdir(); project.mkdir()
    context = {'warehouse': warehouse, 'project_root': project, 'python_executable': sys.executable}
    monkeypatch.setattr(jobs, '_context', lambda *a, **k: (warehouse, project, sys.executable))
    monkeypatch.setattr(jobs, '_closing', False)
    monkeypatch.setattr(jobs, '_SHUTDOWN_PENDING', {})
    monkeypatch.setattr(jobs, 'process_identity', lambda *a, **k: None)
    monkeypatch.setattr(unified_backtest, 'warehouse', lambda: warehouse)
    monkeypatch.setattr(ui_model, 'load', lambda: {})
    monkeypatch.setattr(unified_live, 'control', lambda: {'special_code_default_trigger': lambda _: '올존'})
    return warehouse, project, context


def make_job(store, index=1, phase='complete', kind='normal', legacy=False):
    warehouse, project, context = store
    identifier = f'{index:032x}'
    folder = warehouse / 'runs' / identifier
    folder.mkdir(parents=True)
    scenario = {'symbol': 'GOLDm', 'start': '2026-09-01', 'end': '2026-10-01', 'strategies': []}
    result = {'status': 'CANCELLED' if phase == 'cancelled' else 'COMPLETE', 'scenario': scenario,
              'pieces': [{'path': 'captures/GOLDm/BAR/2026/09/原本'}], 'result_path': 'captures/GOLDm/原本'}
    jobs._atomic(folder / 'result.json', result)
    (folder / 'console.log').write_text('log', encoding='utf-8')
    (folder / 'alerts.csv').write_text('header\nrow', encoding='utf-8')
    if not legacy:
        jobs._atomic(folder / 'web_scenario.json', scenario)
        jobs._write(folder, {'version': jobs.VERSION, 'id': identifier, 'kind': kind,
            'phase': phase, 'scenario': scenario, 'scenario_file': 'web_scenario.json',
            'result_path': 'runs/' + identifier + '/result.json', 'cancel_requested': False,
            'adapter': {'filename': 'Test_SPECIAL001.py'} if kind == 'generated' else {},
            'supervisor': None, 'process': None, 'created_at': 1, 'updated_at': 1})
    return identifier, folder


def originals(store):
    warehouse, project, _ = store
    capture = warehouse / 'captures/GOLDm/BAR/2026/09/原本'
    capture.mkdir(parents=True)
    (capture / 'capture.delta2').write_bytes(b'ORIGINAL CAPTURE NEVER DELETE')
    (capture / 'complete.txt').write_text('complete', encoding='utf-8')
    (warehouse / 'captures.duckdb').write_bytes(b'ORIGINAL CATALOG NEVER DELETE')
    generated = project / 'Part3/generated/Test_SPECIAL001.py'
    generated.parent.mkdir(parents=True)
    generated.write_text('generated strategy unchanged', encoding='utf-8')
    return {path: path.read_bytes() for path in (capture / 'capture.delta2', capture / 'complete.txt',
                                               warehouse / 'captures.duckdb', generated)}


@pytest.mark.parametrize('phase', ['complete', 'cancelled', 'error', 'interrupted', 'planned'])
def test_delete_only_selected_terminal_job_and_preserve_all_originals(store, phase):
    before = originals(store)
    identifier, folder = make_job(store, phase=phase, kind='generated')
    other, other_folder = make_job(store, 2)
    other_result = (other_folder / 'result.json').read_bytes()
    result = jobs.delete_selected([identifier], **store[2])
    assert result == {'ok': True, 'deleted': [identifier], 'errors': []}
    assert not folder.exists() and other_folder.is_dir()
    assert (other_folder / 'result.json').read_bytes() == other_result
    assert all(path.read_bytes() == value for path, value in before.items())


def test_legacy_result_deletion_never_follows_capture_paths(store):
    before = originals(store)
    identifier, folder = make_job(store, legacy=True)
    assert jobs.delete_selected([identifier], **store[2])['ok']
    assert not folder.exists()
    assert all(path.read_bytes() == value for path, value in before.items())


@pytest.mark.parametrize('phase', ['planning', 'plan', 'starting', 'run', 'confirm'])
def test_nonterminal_jobs_cannot_be_deleted_even_with_result_file(store, phase):
    identifier, folder = make_job(store, phase=phase)
    result = jobs.delete_selected([identifier], **store[2])
    assert not result['ok'] and not result['deleted']
    assert folder.is_dir() and (folder / 'result.json').is_file()


@pytest.mark.parametrize('owner', ['supervisor', 'process'])
@pytest.mark.parametrize('unknown', [False, True])
def test_terminal_result_does_not_hide_a_live_or_unknown_process(store, monkeypatch, owner, unknown):
    identifier, folder = make_job(store)
    record = jobs._read(folder)
    record[owner] = {'pid': 123, 'created': 'owned'}
    jobs._write(folder, record)
    def identity(*a, **k):
        if unknown: raise OSError('isolated identity unavailable')
        return {'pid': 123, 'created': 'owned'}
    monkeypatch.setattr(jobs, 'process_identity', identity)
    assert not jobs.delete_selected([identifier], **store[2])['deleted']
    assert folder.is_dir()


@pytest.mark.parametrize('invalid', [None, [], 'a' * 32, ['../captures'], ['/captures'], ['C:/captures'], [True]])
def test_invalid_delete_request_never_removes_records(store, invalid):
    identifier, folder = make_job(store)
    with pytest.raises(ValueError):
        jobs.delete_selected(invalid, **store[2])
    assert folder.is_dir()


def test_all_identifiers_are_validated_before_deletion(store):
    identifier, folder = make_job(store)
    with pytest.raises(ValueError):
        jobs.delete_selected([identifier, '../captures'], **store[2])
    assert folder.is_dir()


def test_duplicate_selections_are_deleted_once(store):
    identifier, folder = make_job(store)
    assert jobs.delete_selected([identifier, identifier], **store[2])['deleted'] == [identifier]


def test_mixed_selection_reports_blocked_jobs_and_only_deletes_finished(store):
    ended, ended_folder = make_job(store)
    running, running_folder = make_job(store, 2, phase='run')
    result = jobs.delete_selected([ended, running], **store[2])
    assert result['deleted'] == [ended] and result['errors'][0]['job_id'] == running
    assert not ended_folder.exists() and running_folder.exists()


def test_shutdown_gate_blocks_delete_and_keeps_results(store):
    identifier, folder = make_job(store)
    jobs.begin_shutdown()
    with pytest.raises(ValueError, match='종료'):
        jobs.delete_selected([identifier], **store[2])
    assert folder.is_dir()


def test_delete_failure_is_reported_and_originals_remain(store, monkeypatch):
    before = originals(store)
    identifier, folder = make_job(store)
    monkeypatch.setattr(jobs.shutil, 'rmtree', Mock(side_effect=PermissionError('isolated delete denied')))
    result = jobs.delete_selected([identifier], **store[2])
    assert not result['ok'] and not result['deleted'] and result['errors']
    assert folder.is_dir() and all(path.read_bytes() == value for path, value in before.items())


@pytest.mark.skipif(os.name != 'nt', reason='Windows junction boundary')
@pytest.mark.parametrize('location', ['runs', 'job', 'nested'])
def test_junction_cannot_turn_result_delete_into_capture_delete(store, location):
    before = originals(store)
    warehouse, _, context = store
    identifier = 'a' * 32
    target = warehouse / 'captures/GOLDm'
    runs = warehouse / 'runs'
    if location == 'runs':
        link = runs
        (target / identifier).mkdir()
    elif location == 'job':
        runs.mkdir(); link = runs / identifier
    else:
        identifier, folder = make_job(store)
        link = folder / 'capture-link'
    created = subprocess.run(['cmd', '/c', 'mklink', '/J', str(link), str(target)],
                             capture_output=True, timeout=10)
    assert created.returncode == 0, created.stdout + created.stderr
    result = jobs.delete_selected([identifier], **context)
    assert not result['ok'] and not result['deleted']
    assert not (target / '.job.lock').exists()
    assert all(path.read_bytes() == value for path, value in before.items())


def test_delete_rechecks_process_under_existing_job_lock(store, monkeypatch):
    identifier, folder = make_job(store)
    result = []
    with jobs.job_lock(folder):
        thread = threading.Thread(target=lambda: result.append(jobs.delete_selected([identifier], **store[2])))
        thread.start()
        record = jobs._read(folder)
        record.update(process={'pid': 123, 'created': 'active'})
        jobs._write(folder, record)
        monkeypatch.setattr(jobs, 'process_identity', lambda *a, **k: {'pid': 123, 'created': 'active'})
    thread.join(5)
    assert not thread.is_alive() and not result[0]['deleted'] and folder.is_dir()


@pytest.mark.parametrize('token,origin', [('', None), ('wrong', None), ('test-token', 'https://external.invalid')])
def test_delete_api_requires_authorized_ui(monkeypatch, token, origin):
    delete = Mock(side_effect=AssertionError('unauthorized delete'))
    monkeypatch.setattr(unified_backtest, 'delete_selected', delete)
    request = handler('/api/mo/backtest/delete', {'job_ids': ['a' * 32]}, token, origin)
    request.do_POST()
    assert request.replies[-1][0] == 403
    delete.assert_not_called()


def test_delete_api_uses_only_selected_ids(monkeypatch):
    delete = Mock(return_value={'ok': True, 'deleted': ['a' * 32], 'errors': []})
    monkeypatch.setattr(unified_backtest, 'delete_selected', delete)
    request = handler('/api/mo/backtest/delete', {'job_ids': ['a' * 32], 'capture_path': '../captures'})
    request.do_POST()
    assert request.replies[-1][0] == 200
    delete.assert_called_once_with(['a' * 32])


def test_symbol_choices_use_actual_folders_without_catalog(store):
    warehouse, _, _ = store
    for symbol in ('USTEC', 'GOLDm', 'EURUSD'):
        (warehouse / 'captures' / symbol).mkdir(parents=True)
    (warehouse / 'captures/BTCUSD').write_text('not a directory', encoding='utf-8')
    (warehouse / 'captures/invalid space').mkdir()
    assert unified_backtest.options()['symbols'] == ['EURUSD', 'GOLDm', 'USTEC']
    assert not (warehouse / 'captures.duckdb').exists()


def test_stale_catalog_symbol_is_excluded_and_folder_only_symbol_is_included(store):
    warehouse, _, _ = store
    db = duckdb.connect(str(warehouse / 'captures.duckdb'))
    db.execute('CREATE TABLE captures(symbol VARCHAR)')
    db.execute("INSERT INTO captures VALUES ('BTCUSD')")
    db.close()
    before = (warehouse / 'captures.duckdb').read_bytes()
    (warehouse / 'captures/NAS100').mkdir(parents=True)
    assert unified_backtest.options()['symbols'] == ['NAS100']
    assert (warehouse / 'captures.duckdb').read_bytes() == before


def test_symbol_folders_refresh_after_project_and_warehouse_move(store, monkeypatch):
    warehouse, project, _ = store
    (warehouse / 'captures/US30.cash').mkdir(parents=True)
    moved = warehouse.parent / '이동한 창고'
    shutil.copytree(warehouse, moved)
    monkeypatch.chdir(project)
    monkeypatch.setattr(unified_backtest, 'warehouse', lambda: moved)
    assert unified_backtest.options()['symbols'] == ['US30.cash']
    shutil.rmtree(moved / 'captures/US30.cash')
    assert unified_backtest.options()['symbols'] == []


def test_result_delete_after_warehouse_move_keeps_original_capture(store, monkeypatch):
    before = originals(store)
    identifier, folder = make_job(store)
    warehouse, project, _ = store
    moved = warehouse.parent / '다른 창고 위치'
    shutil.copytree(warehouse, moved)
    monkeypatch.setattr(jobs, '_context', lambda *a, **k: (moved, project, sys.executable))
    monkeypatch.chdir(project)
    assert jobs.delete_selected([identifier])['ok']
    assert folder.is_dir() and not (moved / 'runs' / identifier).exists()
    assert (moved / 'captures/GOLDm/BAR/2026/09/原本/capture.delta2').read_bytes() == b'ORIGINAL CAPTURE NEVER DELETE'
    assert all(path.read_bytes() == value for path, value in before.items())
