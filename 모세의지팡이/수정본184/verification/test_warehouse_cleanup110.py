"""Portable warehouse maintenance never removes recordings, results or recovery."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import uuid

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'Part2'), str(ROOT / 'Part1/program')]
from event_backtest import warehouse_cleanup as cleanup


@pytest.fixture
def tmp_path(tmp_path_factory):
    # SHA256 + UUID + tester filenames are long even without a long test name.
    return tmp_path_factory.mktemp('w')


@pytest.fixture
def warehouse(tmp_path):
    root = tmp_path / 'warehouse'
    root.mkdir()
    for name in ('captures.duckdb', 'event_backtest.duckdb', 'results.duckdb'):
        (root / name).write_bytes(b'precious database')
    return root


def abandoned_build(root):
    folder = root / 'build_runs' / uuid.uuid4().hex
    folder.mkdir(parents=True)
    (folder / 'original_0').write_bytes(b'restored original backup')
    work = folder / ('a' * 64)
    work.mkdir()
    (work / ('native_' + uuid.uuid4().hex + '.ini')).write_bytes(b'generated tester configuration')
    return folder


def job(root, *, phase='cancelled', owner=None):
    folder = root / 'runs' / uuid.uuid4().hex
    folder.mkdir(parents=True)
    (folder / 'job.json').write_text(json.dumps({'phase': phase, 'supervisor': owner, 'process': None}), 'utf-8')
    (folder / '.job.lock').write_bytes(b'0')
    (folder / 'result.json').write_bytes(b'saved result')
    (folder / 'alerts.csv').write_bytes(b'past alerts')
    (folder / 'console.log').write_bytes(b'saved diagnostic')
    return folder


def snapshot(root):
    return {p.relative_to(root).as_posix(): p.read_bytes()
            for p in root.rglob('*') if p.is_file() and not p.name.endswith('.lock')}


def test_copied_warehouse_cleans_work_and_preserves_databases_recordings_results(warehouse, tmp_path):
    stale = abandoned_build(warehouse)
    published = warehouse / 'captures/XAUUSD+/BAR/2026/09/market-piece'
    published.mkdir(parents=True)
    (published / 'capture.delta2').write_bytes(b'the actual market recording')
    (published / 'complete.txt').write_bytes(b'VERIFIED')
    result = job(warehouse)
    (result / '.job-stale123.tmp').write_bytes(b'atomic write remainder')
    (result / 'stop.request').write_bytes(b'STOP')
    ready = warehouse / 'builds' / ('b' * 64)
    ready.mkdir(parents=True)
    (ready / 'ready.json').write_bytes(b'compiled current build metadata')
    (ready / 'THE_STAFF_OF_MOSES.ex5').write_bytes(b'current compatible build')
    original = snapshot(warehouse)
    copied = tmp_path / 'moved' / 'warehouse'
    shutil.copytree(warehouse, copied)
    report = cleanup.cleanup_warehouse(copied)
    assert not report['busy']
    assert not (copied / stale.relative_to(warehouse)).exists()
    assert (copied / ready.relative_to(warehouse) / 'THE_STAFF_OF_MOSES.ex5').read_bytes() == b'current compatible build'
    after = snapshot(copied)
    for name, value in original.items():
        if name.startswith('build_runs/') or name.endswith(('.tmp', 'stop.request')):
            continue
        assert after[name] == value
    assert snapshot(warehouse) == original
    assert report['removed_bytes'] > 0
    assert all(not Path(path).is_absolute() and '..' not in Path(path).parts for path in report['removed'])


@pytest.mark.parametrize('journal', ['restore_pending.json', 'restore_pending.json.partial', 'history_missing.json'])
def test_restore_and_history_diagnosis_preserve_whole_work(warehouse, journal):
    folder = abandoned_build(warehouse)
    (folder / journal).write_bytes(b'pending or diagnostic record')
    before = snapshot(warehouse)
    report = cleanup.cleanup_warehouse(warehouse)
    assert snapshot(warehouse) == before
    assert report['skipped'][0]['reason'] == 'recovery_or_diagnosis'


def test_incomplete_only_known_mql_cache_is_removed_ready_and_unknown_kept(warehouse):
    incomplete = warehouse / 'builds' / ('c' * 64)
    incomplete.mkdir(parents=True)
    (incomplete / 'PRICE_of_Moses.ex5').write_bytes(b'incomplete compilation')
    unknown = warehouse / 'builds' / ('d' * 64)
    unknown.mkdir()
    (unknown / 'my_strategy.py').write_bytes(b'user file')
    current = warehouse / 'builds' / ('e' * 64)
    current.mkdir()
    (current / 'THE_STAFF_OF_MOSES.ex5').write_bytes(b'current work')
    cleanup.cleanup_warehouse(warehouse, keep_builds={current.name})
    assert not incomplete.exists()
    assert (unknown / 'my_strategy.py').read_bytes() == b'user file'
    assert current.exists()


def test_known_capture_staging_only_incomplete_is_removed(warehouse):
    parent = warehouse / 'captures/XAUUSD+/BAR/2026/09'
    parent.mkdir(parents=True)
    incomplete, complete, unknown = [parent / ('.partial_' + uuid.uuid4().hex) for _ in range(3)]
    for folder in (incomplete, complete, unknown):
        folder.mkdir()
        (folder / 'capture.delta2').write_bytes(b'recording')
    (complete / 'complete.txt').write_bytes(b'VERIFIED')
    (unknown / 'my_notes.txt').write_bytes(b'user notes')
    quarantine = parent / ('a' * 64 + '.quarantine_' + uuid.uuid4().hex)
    quarantine.mkdir()
    (quarantine / 'capture.delta2').write_bytes(b'recoverable recording')
    cleanup.cleanup_warehouse(warehouse)
    assert not incomplete.exists()
    assert (complete / 'capture.delta2').read_bytes() == b'recording'
    assert (unknown / 'my_notes.txt').read_bytes() == b'user notes'
    assert (quarantine / 'capture.delta2').read_bytes() == b'recoverable recording'


def test_live_activity_lease_preserves_all_work_and_does_not_serialize_jobs(warehouse):
    stale = abandoned_build(warehouse)
    with cleanup.warehouse_activity(warehouse):
        with cleanup.warehouse_activity(warehouse):
            assert len(list((warehouse / cleanup._ACTIVITY).iterdir())) == 2
            report = cleanup.cleanup_warehouse(warehouse)
            assert report['busy'] and stale.exists() and not report['removed']
    assert not list((warehouse / cleanup._ACTIVITY).iterdir())
    assert not cleanup.cleanup_warehouse(warehouse)['busy']
    assert not stale.exists()


def test_crash_or_copied_lease_has_no_live_os_lock(warehouse):
    stale = abandoned_build(warehouse)
    directory = warehouse / cleanup._ACTIVITY
    directory.mkdir()
    lease = directory / (uuid.uuid4().hex + '.lock')
    lease.write_bytes(b'0')
    assert not cleanup.cleanup_warehouse(warehouse)['busy']
    assert not stale.exists() and not lease.exists()


def test_existing_job_os_lock_preserves_work_even_with_dead_saved_pid(warehouse):
    stale = abandoned_build(warehouse)
    folder = job(warehouse, owner={'pid': 321, 'created': '123'})
    with cleanup._file_lock(folder / '.job.lock'):
        report = cleanup.cleanup_warehouse(warehouse, job_alive=lambda _: False)
    assert report['busy'] and stale.exists()


@pytest.mark.parametrize('alive', [True, None])
def test_existing_live_or_uncertain_process_preserves_work(warehouse, alive):
    stale = abandoned_build(warehouse)
    job(warehouse, owner={'pid': 321, 'created': '123'})
    report = cleanup.cleanup_warehouse(warehouse, job_alive=lambda _: alive)
    assert report['busy'] and stale.exists()


def test_copied_stale_process_record_and_reused_pid_do_not_block_cleanup(warehouse, monkeypatch):
    if os.name != 'nt':
        pytest.skip('Windows process identity records')
    import event_lifecycle
    stale = abandoned_build(warehouse)
    job(warehouse, phase='run', owner={'pid': 321, 'created': '12345670'})
    monkeypatch.setattr(event_lifecycle, 'process_identity', lambda pid: '87654320')
    assert not cleanup.cleanup_warehouse(warehouse)['busy']
    assert not stale.exists()


def test_active_phase_without_published_owner_is_uncertain(warehouse):
    stale = abandoned_build(warehouse)
    job(warehouse, phase='starting')
    assert cleanup.cleanup_warehouse(warehouse)['busy']
    assert stale.exists()


def test_unknown_work_or_user_folders_are_preserved(warehouse):
    folder = abandoned_build(warehouse)
    (folder / 'manual_note.txt').write_bytes(b'user data')
    user = warehouse / 'build_runs/my_saved_backup'
    user.mkdir()
    (user / 'original_0').write_bytes(b'user original')
    before = snapshot(warehouse)
    cleanup.cleanup_warehouse(warehouse)
    assert snapshot(warehouse) == before


def test_open_windows_work_file_preflight_preserves_entire_candidate(warehouse):
    if os.name != 'nt':
        pytest.skip('Windows sharing rules')
    folder = abandoned_build(warehouse)
    before = snapshot(warehouse)
    with (folder / 'original_0').open('rb'):
        report = cleanup.cleanup_warehouse(warehouse)
    assert snapshot(warehouse) == before
    assert report['skipped'][0]['reason'] == 'unrecognised_or_in_use'


def test_changed_work_file_is_not_partially_deleted(warehouse, monkeypatch):
    folder = abandoned_build(warehouse)
    original = cleanup._unused
    @contextmanager
    def changed(path):
        if path.name == 'original_0':
            path.write_bytes(b'changed during preflight')
        with original(path):
            yield
    monkeypatch.setattr(cleanup, '_unused', changed)
    report = cleanup.cleanup_warehouse(warehouse)
    assert folder.exists()
    assert len(list(folder.rglob('*.ini'))) == 1
    assert not report['removed'] and report['removed_bytes'] == 0


def test_junction_or_symlink_never_follows_or_deletes_external_files(warehouse, tmp_path):
    outside = tmp_path / 'external'
    outside.mkdir()
    (outside / ('native_' + uuid.uuid4().hex + '.ini')).write_bytes(b'external file')
    folder = abandoned_build(warehouse)
    linked = folder / ('f' * 64)
    if os.name == 'nt':
        completed = subprocess.run(['cmd', '/c', 'mklink', '/J', str(linked), str(outside)], capture_output=True)
        if completed.returncode:
            pytest.skip('junction creation unavailable')
    else:
        linked.symlink_to(outside, target_is_directory=True)
    report = cleanup.cleanup_warehouse(warehouse)
    assert folder.exists() and list(outside.glob('*.ini'))[0].read_bytes() == b'external file'
    assert not report['removed']


def test_root_junction_is_not_resolved_before_safety_check(warehouse, tmp_path):
    linked = tmp_path / 'linked-warehouse'
    if os.name == 'nt':
        completed = subprocess.run(['cmd', '/c', 'mklink', '/J', str(linked), str(warehouse)], capture_output=True)
        if completed.returncode:
            pytest.skip('junction creation unavailable')
    else:
        linked.symlink_to(warehouse, target_is_directory=True)
    folder = abandoned_build(warehouse)
    report = cleanup.cleanup_warehouse(linked)
    assert folder.exists() and report['skipped'][0]['reason'] == 'unavailable_or_unsafe'


def test_missing_warehouse_does_not_create_files(tmp_path):
    missing = tmp_path / 'not-installed'
    report = cleanup.cleanup_warehouse(missing)
    assert not missing.exists() and not report['removed']


def test_report_emits_relative_paths_once(warehouse):
    abandoned_build(warehouse)
    emitted = []
    report = cleanup.cleanup_warehouse(warehouse, emit=lambda kind, value: emitted.append((kind, value)))
    assert emitted == [('WAREHOUSE_CLEANUP', report)]
    assert str(warehouse) not in json.dumps(report)


def test_independent_process_lease_blocks_cleanup(warehouse):
    stale = abandoned_build(warehouse)
    script = ("import sys;sys.path.insert(0,sys.argv[1]);"
              "from event_backtest.warehouse_cleanup import warehouse_activity;"
              "with_lease=warehouse_activity(sys.argv[2]);with_lease.__enter__();"
              "print('ready',flush=True);sys.stdin.readline();with_lease.__exit__(None,None,None)")
    child = subprocess.Popen([sys.executable, '-B', '-c', script, str(ROOT / 'Part2'), str(warehouse)],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                             creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    try:
        assert child.stdout.readline().strip() == 'ready'
        assert cleanup.cleanup_warehouse(warehouse)['busy'] and stale.exists()
        child.stdin.write('\n')
        child.stdin.flush()
        assert child.wait(timeout=10) == 0
        assert not cleanup.cleanup_warehouse(warehouse)['busy'] and not stale.exists()
    finally:
        if child.poll() is None:
            child.terminate()
            child.wait(timeout=10)
        child.stdin.close()
        child.stdout.close()
        child.stderr.close()
