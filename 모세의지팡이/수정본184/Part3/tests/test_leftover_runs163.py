"""163: run folders left with neither a record nor a result are one cleanup offer, not one warning
each, and a deletion either takes a run out of the list at once or leaves it whole."""
import json
import os
import shutil
import sys
from pathlib import Path

import pytest

PART3 = Path(__file__).resolve().parents[1]
ROOT = PART3.parent
sys.path[:0] = [str(PART3), str(ROOT / 'Part2'), str(ROOT / 'Part1/program')]
from lab import backtest_jobs as jobs
from event_backtest.warehouse_cleanup import warehouse_activity

SCENARIO = {'symbol': 'GOLDm', 'start': '2026-09-01', 'end': '2026-10-01', 'strategies': ['SPECIAL2']}


@pytest.fixture
def store(tmp_path, monkeypatch):
    warehouse = tmp_path / '창고'
    (warehouse / 'runs').mkdir(parents=True)
    monkeypatch.setattr(jobs, '_closing', False)
    monkeypatch.setattr(jobs, 'process_identity', lambda *a, **k: None)  # every recorded process has ended
    return warehouse, {'warehouse': warehouse, 'project_root': ROOT, 'python_executable': sys.executable}


def run_folder(warehouse, index, *, result=False, job=None, lead=None, extra=True):
    folder = warehouse / 'runs' / f'{index:032x}'
    folder.mkdir()
    if extra:  # what an interrupted run leaves behind
        (folder / 'progress.jsonl').write_text('{"event":"RUN_START"}\n', encoding='utf-8')
        (folder / 'chunk_000').mkdir()
        (folder / 'chunk_000' / 'alerts.csv').write_text('header\n', encoding='utf-8')
    if lead:
        (folder / 'lane.json').write_text(json.dumps({'lead_run_id': lead}), encoding='utf-8')
    if result:
        jobs._atomic(folder / 'result.json', {'status': 'COMPLETE', 'scenario': SCENARIO})
    if job:
        jobs._atomic(folder / 'web_scenario.json', SCENARIO)
        jobs._write(folder, {'version': jobs.VERSION, 'id': folder.name, 'kind': 'normal', 'phase': job,
            'scenario': SCENARIO, 'scenario_file': 'web_scenario.json', 'adapter': {},
            'result_path': 'runs/' + folder.name + '/result.json', 'cancel_requested': False,
            'supervisor': None, 'process': None, 'created_at': 1, 'updated_at': 1})
    return folder


def files(folder):
    """The run's own files; reading a record leaves its lock file behind."""
    return {path.relative_to(folder).as_posix(): path.read_bytes() for path in folder.rglob('*')
            if path.is_file() and path.name != '.job.lock'}


def test_folders_without_record_or_result_are_one_offer_and_damaged_records_still_warn(store):
    warehouse, context = store
    lead = run_folder(warehouse, 1, result=True, job='complete')            # an ended request
    stopped = run_folder(warehouse, 2)                                      # stopped before any record
    empty = run_folder(warehouse, 3, extra=False)
    orphan_lane = run_folder(warehouse, 4, lead=lead.name)                  # its request ended without it
    finished = run_folder(warehouse, 5, result=True)
    damaged = run_folder(warehouse, 6)
    (damaged / 'job.json').write_text('{broken', encoding='utf-8')
    listed = jobs.recent(20, **context)
    assert sorted(listed['leftovers']) == sorted([stopped.name, empty.name, orphan_lane.name])
    assert listed['warnings'] == [damaged.name + ': 실행 기록을 읽지 못했습니다.']
    assert {item['job_id'] for item in listed['items']} == {lead.name, finished.name}


def test_leftovers_beyond_the_list_limit_are_still_counted(store):
    warehouse, context = store
    for index in range(1, 4):
        run_folder(warehouse, index, result=True)
    stopped = run_folder(warehouse, 9)
    os.utime(stopped, (1, 1))  # the oldest folder, after the listed ones
    listed = jobs.recent(2, **context)
    assert len(listed['items']) == 2 and listed['leftovers'] == [stopped.name]


def test_nothing_is_offered_or_removed_while_work_holds_the_warehouse(store):
    warehouse, context = store
    stopped = run_folder(warehouse, 2)
    with warehouse_activity(warehouse):  # planning, build or replay: such a folder may still be written
        assert jobs.recent(20, **context) == {'items': [], 'warnings': [], 'leftovers': []}
        with pytest.raises(ValueError, match='진행 중인 백테스트'):
            jobs.delete_leftovers([stopped.name], **context)
        assert stopped.is_dir()
    assert jobs.recent(20, **context)['leftovers'] == [stopped.name]
    assert jobs.delete_leftovers([stopped.name], **context) == {'ok': True, 'deleted': [stopped.name], 'errors': []}
    assert not stopped.exists() and jobs.recent(20, **context)['leftovers'] == []


def test_cleanup_removes_only_folders_still_without_record_or_result(store, monkeypatch):
    warehouse, context = store
    stopped = run_folder(warehouse, 2)
    finished = run_folder(warehouse, 3, result=True)
    ended_job = run_folder(warehouse, 4, job='error')
    running = run_folder(warehouse, 5, job='run')
    record = jobs._read(running)
    record['supervisor'] = {'pid': 123, 'created': '5', 'run_id': 'f' * 32}
    jobs._write(running, record)
    monkeypatch.setattr(jobs, 'process_identity', lambda pid, *a, **k: {'pid': pid, 'created': '5'})
    lane = run_folder(warehouse, 6, lead=running.name)  # another base frame of the running request
    kept = {folder: files(folder) for folder in (finished, ended_job, running, lane)}
    assert jobs.recent(20, **context)['leftovers'] == [stopped.name]
    result = jobs.delete_leftovers([stopped.name, finished.name, ended_job.name, running.name, lane.name], **context)
    assert result['deleted'] == [stopped.name] and not stopped.exists()
    assert [error['job_id'] for error in result['errors']] == [finished.name, ended_job.name, running.name, lane.name]
    assert all(files(folder) == before for folder, before in kept.items())


@pytest.mark.skipif(os.name != 'nt', reason='Windows refuses to rename a folder holding an open file')
@pytest.mark.parametrize('kind', ['finished', 'leftover'])
def test_a_run_with_an_open_file_is_left_whole(store, kind):
    warehouse, context = store
    folder = run_folder(warehouse, 7, result=kind == 'finished', job='complete' if kind == 'finished' else None)
    remove = jobs.delete_selected if kind == 'finished' else jobs.delete_leftovers
    before = files(folder)
    with (folder / 'chunk_000' / 'alerts.csv').open('rb'):  # e.g. a viewer still reading it
        result = remove([folder.name], **context)
    assert not result['ok'] and not result['deleted'] and '사용 중인 파일' in result['errors'][0]['message']
    assert files(folder) == before
    assert not list((warehouse / 'runs').glob('.deleting-*'))
    assert remove([folder.name], **context)['deleted'] == [folder.name]
    assert not folder.exists() and not list((warehouse / 'runs').glob('.deleting-*'))


def test_a_removal_cut_short_after_the_rename_is_never_listed_and_is_finished_later(store, monkeypatch):
    warehouse, context = store
    first = run_folder(warehouse, 1, result=True, job='complete')
    second = run_folder(warehouse, 2, result=True, job='complete')
    real = shutil.rmtree
    monkeypatch.setattr(jobs.shutil, 'rmtree', lambda *a, **k: None)  # every file stayed open
    assert jobs.delete_selected([first.name], **context)['deleted'] == [first.name]
    [aside] = (warehouse / 'runs').glob('.deleting-*')
    assert aside.name.startswith('.deleting-' + first.name + '-') and (aside / 'result.json').is_file()
    listed = jobs.recent(20, **context)
    assert [item['job_id'] for item in listed['items']] == [second.name]
    assert listed['warnings'] == [] and listed['leftovers'] == []
    monkeypatch.setattr(jobs.shutil, 'rmtree', real)
    assert jobs.delete_selected([second.name], **context)['deleted'] == [second.name]
    assert sorted(path.name for path in (warehouse / 'runs').iterdir()) == []


def test_cleanup_after_moving_the_warehouse_touches_only_the_moved_copy(store):
    warehouse, context = store
    stopped = run_folder(warehouse, 2)
    moved = warehouse.parent / '옮긴 창고'
    shutil.copytree(warehouse, moved)
    moved_context = {**context, 'warehouse': moved}
    assert jobs.recent(20, **moved_context)['leftovers'] == [stopped.name]
    assert jobs.delete_leftovers([stopped.name], **moved_context)['deleted'] == [stopped.name]
    assert not (moved / 'runs' / stopped.name).exists() and stopped.is_dir()


@pytest.mark.parametrize('invalid', [None, [], ['../captures'], ['a' * 31], [1]])
def test_invalid_cleanup_requests_remove_nothing(store, invalid):
    warehouse, context = store
    stopped = run_folder(warehouse, 2)
    with pytest.raises(ValueError):
        jobs.delete_leftovers(invalid, **context)
    assert stopped.is_dir()


def test_the_shutdown_gate_blocks_cleanup(store):
    warehouse, context = store
    stopped = run_folder(warehouse, 2)
    jobs.begin_shutdown()
    with pytest.raises(ValueError, match='종료'):
        jobs.delete_leftovers([stopped.name], **context)
    assert stopped.is_dir()
