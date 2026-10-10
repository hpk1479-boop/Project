"""No real application windows or engines: isolated OS leases and launch guards."""
from pathlib import Path
import os
import shutil
import subprocess
import sys
from types import SimpleNamespace
import uuid

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'Part3'))
from lab import desktop_instance as single, desktop_window


@pytest.fixture
def instance(monkeypatch):
    monkeypatch.setattr(single, 'existing_window', lambda: None)
    return single.DesktopInstance('Global\\MOSES.Verify.' + uuid.uuid4().hex)


def test_same_process_duplicate_is_denied_and_normal_exit_releases(instance):
    other = single.DesktopInstance(instance.name)
    try:
        assert instance.acquire()
        assert not other.acquire()
        instance.release()
        assert other.acquire()
    finally:
        instance.release()
        other.release()


def test_existing_older_window_blocks_server_and_releases_lease(instance, monkeypatch):
    monkeypatch.setattr(single, 'existing_window', lambda: 123)
    assert not instance.acquire() and instance.window == 123
    monkeypatch.setattr(single, 'existing_window', lambda: None)
    assert instance.acquire()
    instance.release()


def test_duplicate_run_warns_without_starting_or_closing_anything(monkeypatch):
    notified = []
    monkeypatch.setattr(desktop_window, 'notify_duplicate', notified.append)
    blocked = SimpleNamespace(acquire=lambda: False, window=123)
    monkeypatch.setattr(desktop_window, '_run_window', lambda **_: pytest.fail('duplicate reached server or GUI'))
    desktop_window.run(instance_factory=lambda: blocked)
    assert notified == [123]


@pytest.mark.parametrize('fails', [False, True])
def test_lease_held_until_original_window_cleanup_finishes(instance, monkeypatch, fails):
    def run(**kwargs):
        assert instance.owned
        assert not single.DesktopInstance(instance.name).acquire()
        if fails:
            raise RuntimeError('fixture failure')
    monkeypatch.setattr(desktop_window, '_run_window', run)
    if fails:
        with pytest.raises(RuntimeError, match='fixture failure'):
            desktop_window.run(instance_factory=lambda: instance)
    else:
        desktop_window.run(instance_factory=lambda: instance)
    assert not instance.owned
    assert instance.acquire()
    instance.release()


def child(folder, name):
    code = """import sys
from desktop_instance import DesktopInstance
import desktop_instance
desktop_instance.existing_window=lambda:None
lease=DesktopInstance(sys.argv[1])
ok=lease.acquire()
print('OWNED' if ok else 'DENIED',flush=True)
if ok:sys.stdin.readline()
lease.release()
"""
    return subprocess.Popen([sys.executable, '-B', '-u', '-c', code, name], cwd=folder,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding='utf-8', creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))


def finish(process):
    if process.poll() is None:
        process.communicate('\n', timeout=5)
    else:
        process.communicate(timeout=5)
    assert process.returncode == 0


def module_folder(folder):
    folder.mkdir()
    shutil.copyfile(ROOT / 'Part3/lab/desktop_instance.py', folder / 'desktop_instance.py')
    return folder


def test_simultaneous_four_processes_have_one_owner(tmp_path, instance):
    folder = module_folder(tmp_path / '실행 위치')
    children = [child(folder, instance.name) for _ in range(4)]
    try:
        results = [process.stdout.readline().strip() for process in children]
        assert results.count('OWNED') == 1 and results.count('DENIED') == 3, results
    finally:
        for process in children:
            finish(process)


def test_different_project_locations_share_same_lease(tmp_path, instance):
    first = child(module_folder(tmp_path / '수정본 A'), instance.name)
    second = None
    try:
        assert first.stdout.readline().strip() == 'OWNED'
        second = child(module_folder(tmp_path / '옮긴 수정본 B'), instance.name)
        assert second.stdout.readline().strip() == 'DENIED'
        assert first.poll() is None
    finally:
        if second:
            finish(second)
        finish(first)


def test_abrupt_owner_exit_does_not_leave_stale_lock(tmp_path, instance):
    folder = module_folder(tmp_path / '종료 검사')
    owner, restart = child(folder, instance.name), None
    try:
        assert owner.stdout.readline().strip() == 'OWNED'
        owner.kill()
        owner.communicate(timeout=5)
        restart = child(folder, instance.name)
        assert restart.stdout.readline().strip() == 'OWNED'
    finally:
        if restart:
            finish(restart)
        if owner.poll() is None:
            finish(owner)
