"""Cold preparation, proof validation, and cache lifetime regression checks.

Application code, downloads, and real compilation are never invoked here.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

from releasekit import cleanup, prerequisites, workflow
from releasekit.builder import BuildSettings
from releasekit.build_worker import parent_alive


SETTINGS = BuildSettings('v1.0', '2099-12-31', 10)
CACHES = ('.work', 'prerequisites', '.buildenv')


@pytest.fixture
def project(tmp_path):
    root = tmp_path / '수정본 97 검증'
    (root / '통합설치/releasekit').mkdir(parents=True)
    (root / '통합설치/.keys').mkdir()
    (root / '통합설치/.keys/preserve.json').write_text('private fixture', encoding='utf-8')
    (root / 'original.py').write_text('ORIGINAL = True\n', encoding='utf-8')
    return root


def create_caches(root):
    tools = root / '통합설치'
    for name in CACHES:
        path = tools / name / 'fixture'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'regenerable fixture')
    worker = tools / '.buildenv/Scripts/python.exe'
    worker.parent.mkdir(parents=True, exist_ok=True)
    worker.write_bytes(b'never execute')
    return worker


def write_result(root, args):
    token = args[args.index('--nonce') + 1]
    name = args[args.index('--result') + 1]
    final = root / '배포' / token / '모세의지팡이 통합설치.exe'
    final.parent.mkdir(parents=True)
    final.write_bytes(b'completed setup ' + token.encode('ascii'))
    evidence = root / '검증결과/license96' / token
    evidence.mkdir(parents=True)
    proof = {'schema': 1, 'passed': True, 'checks': [
        {'schema': 1, 'nonce': token, 'mode': mode, 'passed': True,
         'checks': [{'name': mode, 'passed': True}], 'errors': []}
        for mode in ('imports', 'http', 'gui')]}
    (evidence / 'release_validation.json').write_text(json.dumps(proof), encoding='utf-8')
    report = {'version': 'v1.0', 'expiry_date': '2099-12-31', 'install_minutes': 10,
              'source_unchanged': True, 'source_folder': root.name,
              'installer': final.relative_to(root).as_posix(),
              'installer_sha256': hashlib.sha256(final.read_bytes()).hexdigest(),
              'release_validation': {'passed': True, 'modes': ['imports', 'http', 'gui'],
                    'report': (evidence / 'release_validation.json').relative_to(root).as_posix()}}
    (evidence / 'build_summary.json').write_text(json.dumps(report), encoding='utf-8')
    record = {'schema': 1, 'nonce': token, 'passed': True,
              'installer': final.relative_to(root).as_posix(), 'report': report}
    result = root / '통합설치/.buildtmp' / name
    result.write_text(json.dumps(record), encoding='utf-8')
    return result, record, final


@pytest.fixture
def fake_pipeline(monkeypatch):
    calls = []
    artifacts = []
    def prepare(root, emit):
        calls.append('environment')
        emit('배포 도구 다운로드 중…')
        return create_caches(root)
    def webview(root, emit):
        calls.append('webview')
        emit('WebView2 다운로드 중…')
        return root / '통합설치/prerequisites/fixture'
    def child(args, root, emit, **kwargs):
        calls.append('worker')
        assert all((root / '통합설치' / name).is_dir() for name in CACHES)
        artifacts.append(write_result(root, args))
        return 0
    monkeypatch.setattr(workflow, 'prepare_environment', prepare)
    monkeypatch.setattr(prerequisites, 'ensure_webview2', webview)
    monkeypatch.setattr(workflow, 'stream_process', child)
    return calls, artifacts


def test_cold_build_prepares_then_builds_then_cleans(project, fake_pipeline):
    calls, artifacts = fake_pipeline
    logs = []
    final, report = workflow.run_release(project, SETTINGS, emit=logs.append)
    assert calls == ['environment', 'webview', 'worker']
    assert final == artifacts[0][2] and final.is_file()
    assert report['cleanup']['success'] is True
    assert all(not (project / '통합설치' / name).exists() for name in CACHES)
    assert (project / '통합설치/.keys/preserve.json').read_text() == 'private fixture'
    assert (project / 'original.py').read_text() == 'ORIGINAL = True\n'
    assert any('다운로드 중' in line for line in logs)
    assert any('Setup 생성 완료' in line for line in logs)
    assert logs[-1] == '임시 파일 정리 완료'
    evidence = project / report['release_validation']['report']
    saved = json.loads((evidence.parent / 'build_summary.json').read_text())
    assert saved['cleanup']['success'] is True
    assert not list((project / '통합설치/.buildtmp').glob('build_*.json'))


def test_repeat_build_reprepares_all_deleted_caches(project, fake_pipeline):
    calls, artifacts = fake_pipeline
    first, _ = workflow.run_release(project, SETTINGS, emit=lambda _: None)
    second, _ = workflow.run_release(project, SETTINGS, emit=lambda _: None)
    assert calls == ['environment', 'webview', 'worker'] * 2
    assert first != second and first.is_file() and second.is_file()
    assert all(not (project / '통합설치' / name).exists() for name in CACHES)


@pytest.mark.parametrize('problem', [
    'wrong_nonce', 'failed_record', 'wrong_schema', 'missing_result',
    'invalid_json', 'child_nonzero', 'partial_report', 'unpassed_validation',
    'source_changed', 'missing_installer', 'outside_installer',
    'missing_summary', 'summary_mismatch', 'installer_hash_mismatch',
    'missing_validation', 'unpassed_validation_file', 'partial_validation_file',
    'outside_evidence', 'report_not_dict', 'failed_nested_check',
    'missing_mode', 'duplicate_mode',
])
def test_worker_failure_or_incomplete_proof_rejected_and_cleaned(
        project, fake_pipeline, monkeypatch, problem, tmp_path):
    original_child = workflow.stream_process
    def bad_child(args, root, emit, **kwargs):
        if problem == 'child_nonzero':
            raise RuntimeError('fixture child returned nonzero')
        original_child(args, root, emit, **kwargs)
        path, record, final = fake_pipeline[1][-1]
        if problem == 'missing_result':
            path.unlink()
            return 0
        if problem == 'invalid_json':
            path.write_text('invalid json', encoding='utf-8')
            return 0
        proof = root / record['report']['release_validation']['report']
        if problem == 'wrong_nonce': record['nonce'] = '0' * 32
        elif problem == 'failed_record': record['passed'] = False
        elif problem == 'wrong_schema': record['schema'] = 2
        elif problem == 'partial_report': record['report'] = {'source_unchanged': True}
        elif problem == 'unpassed_validation': record['report']['release_validation']['passed'] = False
        elif problem == 'source_changed': record['report']['source_unchanged'] = False
        elif problem == 'missing_installer': final.unlink()
        elif problem == 'outside_installer': record['installer'] = '../outside.exe'
        elif problem == 'missing_summary': (proof.parent / 'build_summary.json').unlink()
        elif problem == 'summary_mismatch':
            saved = json.loads((proof.parent / 'build_summary.json').read_text())
            saved['version'] = 'v99.0'
            (proof.parent / 'build_summary.json').write_text(json.dumps(saved), encoding='utf-8')
        elif problem == 'installer_hash_mismatch': final.write_bytes(b'changed executable')
        elif problem == 'missing_validation': proof.unlink()
        elif problem == 'unpassed_validation_file': proof.write_text('{"schema":1,"passed":false}', encoding='utf-8')
        elif problem == 'partial_validation_file': proof.write_text('{"schema":1,"passed":true,"checks":[]}', encoding='utf-8')
        elif problem == 'outside_evidence':
            outside = tmp_path / 'outside-proof.json'
            outside.write_text('{"passed":true}', encoding='utf-8')
            record['report']['release_validation']['report'] = str(outside)
        elif problem == 'report_not_dict': record['report'] = 'partial report'
        elif problem in {'failed_nested_check', 'missing_mode', 'duplicate_mode'}:
            saved = json.loads(proof.read_text())
            if problem == 'failed_nested_check': saved['checks'][0]['checks'][0]['passed'] = False
            elif problem == 'missing_mode': saved['checks'].pop()
            else: saved['checks'][2]['mode'] = 'imports'
            proof.write_text(json.dumps(saved), encoding='utf-8')
        path.write_text(json.dumps(record), encoding='utf-8')
        return 0
    monkeypatch.setattr(workflow, 'stream_process', bad_child)
    with pytest.raises((RuntimeError, ValueError, OSError)):
        workflow.run_release(project, SETTINGS, emit=lambda _: None)
    assert all(not (project / '통합설치' / name).exists() for name in CACHES)
    assert (project / 'original.py').read_text() == 'ORIGINAL = True\n'
    assert not (tmp_path / 'build_summary.json').exists()
    assert not list((project / '통합설치/.buildtmp').glob('build_*.json'))


@pytest.mark.parametrize('phase', ['environment', 'webview'])
def test_preparation_failure_cleans_partial_caches(project, fake_pipeline, monkeypatch, phase):
    def failed(root, *args, **kwargs):
        create_caches(root)
        raise RuntimeError('fixture download failed')
    if phase == 'environment': monkeypatch.setattr(workflow, 'prepare_environment', failed)
    else: monkeypatch.setattr(prerequisites, 'ensure_webview2', failed)
    with pytest.raises(RuntimeError, match='fixture download failed'):
        workflow.run_release(project, SETTINGS, emit=lambda _: None)
    assert all(not (project / '통합설치' / name).exists() for name in CACHES)


def test_cleanup_failure_reports_successful_installer_separately(project, fake_pipeline, monkeypatch):
    def blocked(root, emit):
        emit('임시 파일 정리 실패: fixture locked file')
        return {'schema': 1, 'success': False, 'targets': [{'status': 'failed'}]}
    monkeypatch.setattr(cleanup, 'cleanup_build_files', blocked)
    logs = []
    final, report = workflow.run_release(project, SETTINGS, emit=logs.append)
    assert final.is_file()
    assert report['release_validation']['passed'] is True
    assert report['cleanup']['success'] is False
    assert any('Setup 생성 완료' in line for line in logs)
    assert any('정리 실패' in line for line in logs)


@pytest.mark.parametrize('held_lock', ['release', 'worker'])
def test_concurrent_or_alive_worker_cannot_prepare_or_delete(project, fake_pipeline, held_lock):
    create_caches(project)
    before = {p.relative_to(project): p.read_bytes() for p in project.rglob('*') if p.is_file()}
    with workflow.release_lock(project, held_lock):
        with pytest.raises(RuntimeError, match='이미 진행 중'):
            workflow.run_release(project, SETTINGS, emit=lambda _: None)
    assert fake_pipeline[0] == []
    for rel, content in before.items(): assert (project / rel).read_bytes() == content
    assert all((project / '통합설치' / name).is_dir() for name in CACHES)


@pytest.mark.parametrize('settings', [
    BuildSettings('../bad', '2099-12-31', 10),
    BuildSettings('v1.0', '2000-01-01', 10),
    BuildSettings('v1.0', '2099-12-31', 0),
])
def test_invalid_settings_create_no_files(project, fake_pipeline, settings):
    before = sorted(p.relative_to(project).as_posix() for p in project.rglob('*'))
    with pytest.raises(ValueError): workflow.run_release(project, settings, emit=lambda _: None)
    after = sorted(p.relative_to(project).as_posix() for p in project.rglob('*'))
    assert before == after and fake_pipeline[0] == []


def test_prepare_environment_cold_sequence_without_real_download(project, monkeypatch):
    calls = []
    tools = project / '통합설치'
    worker = tools / '.buildenv/Scripts/python.exe'
    checks = iter([1, 0])
    def process(args, root, emit, **kwargs):
        args = list(map(str, args))
        if 'venv' in args:
            calls.append('venv')
            worker.parent.mkdir(parents=True)
            worker.write_bytes(b'fixture')
            return 0
        if 'pip' in args:
            calls.append('pip')
            assert '--no-cache-dir' in args
            emit('fixture wheel download')
            return 0
        calls.append('check')
        return next(checks)
    monkeypatch.setattr(workflow, 'stream_process', process)
    logs = []
    assert workflow.prepare_environment(project, logs.append) == worker
    assert calls == ['venv', 'check', 'pip', 'check']
    assert any('다운로드 중' in line for line in logs)


def test_prepare_environment_verified_existing_cache_avoids_pip(project, monkeypatch):
    create_caches(project)
    calls = []
    def process(args, root, emit, **kwargs):
        calls.append(list(map(str, args)))
        return 0
    monkeypatch.setattr(workflow, 'stream_process', process)
    workflow.prepare_environment(project, emit=lambda _: None)
    assert len(calls) == 1 and calls[0][-1].endswith('check_build_environment.py')


def test_expiry_and_cold_launcher_need_no_tzdata(project):
    # -S prevents all third party/site packages, including tzdata and pip.
    base = getattr(sys, '_base_executable', sys.executable)
    tools = Path(workflow.__file__).resolve().parents[1]
    code = ('import sys; sys.path.insert(0, sys.argv[1]); '
            'from releasekit.builder import BuildSettings; '
            'BuildSettings("v1.0", "2099-12-31", 10).validate(); '
            'from releasekit.check_launcher import main; main()')
    result = subprocess.run([base, '-B', '-S', '-c', code, str(tools)],
                            capture_output=True, text=True, timeout=30,
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    assert result.returncode == 0, result.stdout + result.stderr
    assert '"ready": true' in result.stdout


@pytest.mark.skipif(os.name != 'nt', reason='Windows job object')
@pytest.mark.parametrize('interpreter', ['base', 'venv'])
def test_suspended_job_close_ends_actual_parent_and_descendant(project, interpreter):
    gate = project / 'go'
    pid_file = project / 'child_pid'
    base = getattr(sys, '_base_executable', sys.executable)
    executable = base
    if interpreter == 'venv':
        venv = project / '통합설치/.buildenv'
        result = subprocess.run([base, '-B', '-m', 'venv', '--without-pip', str(venv)],
                                capture_output=True, text=True, timeout=30,
                                creationflags=subprocess.CREATE_NO_WINDOW)
        assert result.returncode == 0, result.stderr
        executable = str(venv / 'Scripts/python.exe')
    code = ('import pathlib, subprocess, sys, time; '
            'gate=pathlib.Path(sys.argv[1]); target=pathlib.Path(sys.argv[2]); '
            '\nwhile not gate.exists(): time.sleep(.02)\n'
            'child=subprocess.Popen([sys.executable,"-c","import time; time.sleep(30)"]); '
            'target.write_text(str(child.pid)); time.sleep(30)')
    process = subprocess.Popen([executable, '-B', '-c', code, str(gate), str(pid_file)],
                               creationflags=subprocess.CREATE_NO_WINDOW | 4)
    tree = None
    descendant = None
    try:
        tree = workflow.ProcessTree(process, suspended=True)
        gate.write_bytes(b'go')
        deadline = time.monotonic() + 10
        while not pid_file.exists() and time.monotonic() < deadline: time.sleep(.02)
        assert pid_file.exists(), 'child did not start'
        descendant = int(pid_file.read_text())
        assert parent_alive(descendant)
        tree.close()
        process.wait(timeout=10)
        deadline = time.monotonic() + 5
        while parent_alive(descendant) and time.monotonic() < deadline: time.sleep(.02)
        assert not parent_alive(descendant), 'job left descendant alive'
    finally:
        if tree: tree.close()
        if process.poll() is None:
            process.kill()
            process.wait(timeout=10)
        if descendant and parent_alive(descendant):
            subprocess.run(['taskkill', '/PID', str(descendant), '/T', '/F'],
                           capture_output=True, timeout=10,
                           creationflags=subprocess.CREATE_NO_WINDOW)


def test_real_stream_nonzero_does_not_report_success(project):
    base = getattr(sys, '_base_executable', sys.executable)
    logs = []
    with pytest.raises(RuntimeError, match='FIXTURE_FAILURE'):
        workflow.stream_process([base, '-B', '-c', 'print("FIXTURE_FAILURE"); raise SystemExit(7)'],
                                project, logs.append, timeout=10)
    assert logs == ['FIXTURE_FAILURE']


@pytest.mark.skipif(os.name != 'nt', reason='Windows process tree')
def test_real_stream_timeout_ends_worker_and_descendant(project):
    base = getattr(sys, '_base_executable', sys.executable)
    pid_file = project / 'timeout_child_pid'
    code = ('import pathlib, subprocess, sys, time; '
            'child=subprocess.Popen([sys.executable,"-c","import time; time.sleep(30)"]); '
            'pathlib.Path(sys.argv[1]).write_text(str(child.pid)); '
            'print("TIMEOUT_FIXTURE_STARTED",flush=True); time.sleep(30)')
    with pytest.raises(RuntimeError, match='시간이 초과'):
        workflow.stream_process([base, '-B', '-c', code, str(pid_file)],
                                project, lambda _: None, timeout=.5)
    assert pid_file.exists()
    descendant = int(pid_file.read_text())
    deadline = time.monotonic() + 5
    while parent_alive(descendant) and time.monotonic() < deadline: time.sleep(.02)
    assert not parent_alive(descendant), 'timeout left child alive'
