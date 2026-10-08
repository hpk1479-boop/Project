"""Moved-install discovery uses mock HKCU only, never the user's registry."""
from __future__ import annotations

import hashlib
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from releasekit import install_location, license_runtime, runtime_entry


class Registry:
    HKEY_CURRENT_USER = 'HKCU'
    REG_SZ = 1
    KEY_READ = 2
    KEY_SET_VALUE = 3

    def __init__(self, denied=None):
        self.values = {}
        self.writes = []
        self.creates = []
        self.denied = denied

    class Key:
        def __init__(self, name):
            self.name = name

        def __enter__(self):
            return self

        def __exit__(self, *unused):
            return False

    def OpenKey(self, hive, name, reserved, access):
        assert hive == self.HKEY_CURRENT_USER and access == self.KEY_READ
        if self.denied == 'read':
            raise PermissionError('fixture registry denied')
        if name not in self.values:
            raise FileNotFoundError(name)
        return self.Key(name)

    def QueryValueEx(self, key, name):
        try:
            return self.values[key.name][name]
        except KeyError:
            raise FileNotFoundError(name)

    def CreateKeyEx(self, hive, name, reserved, access):
        assert hive == self.HKEY_CURRENT_USER and access == self.KEY_SET_VALUE
        if self.denied == 'write':
            raise PermissionError('fixture registry denied')
        self.creates.append(name)
        self.values.setdefault(name, {})
        return self.Key(name)

    def SetValueEx(self, key, name, reserved, kind, value):
        self.writes.append((key.name, name, kind, value))
        self.values[key.name][name] = value, kind


@pytest.fixture
def installed(tmp_path):
    def make(name='모세 A'):
        root = tmp_path / name
        for relative in install_location.REQUIRED_FILES:
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'fixture distribution')
        return root
    return make


def register(root, registry, argv=(), **options):
    return install_location.register_gui_installation(
        root, argv, executable=root / 'MOSES.exe', frozen=True, registry=registry, **options)


def identity(root):
    return hashlib.sha256(str(root.resolve()).lower().encode('utf-8')).hexdigest()


def test_registration_is_canonical_and_repeated_start_has_no_writes(installed):
    root = installed()
    registry = Registry()
    assert register(root / 'runtime' / '..', registry)
    locations = registry.values[install_location.INSTALLATIONS_PATH]
    assert locations == {identity(root): (str(root.resolve()), registry.REG_SZ)}
    assert registry.values[install_location.REGISTRY_PATH]['InstallPath'][0] == str(root.resolve())
    assert len(registry.writes) == 2
    assert register(root, registry)
    assert len(registry.writes) == len(registry.creates) == 2


def test_multiple_installations_and_move_preserve_other_paths(installed):
    first, other = installed('모세 A'), installed('모세 B')
    registry = Registry()
    assert register(first, registry)
    assert register(other, registry)
    moved = first.with_name('Moved 모세 A')
    first.rename(moved)
    assert register(moved, registry)
    locations = registry.values[install_location.INSTALLATIONS_PATH]
    assert set(locations) == {identity(first), identity(other), identity(moved)}
    assert locations[identity(other)][0] == str(other.resolve())
    assert locations[identity(moved)][0] == str(moved.resolve())
    assert registry.values[install_location.REGISTRY_PATH]['InstallPath'][0] == str(moved.resolve())


@pytest.mark.parametrize('argv', [
    ['-m', 'module'], ['-c', 'pass'], ['--multiprocessing-fork'],
    ['START_MOSES.pyw'], ['validation_probe.py'], ['--version'], ['-B'], ['--'],
])
def test_workers_do_not_register_even_through_moses_alias(installed, argv):
    registry = Registry()
    assert not register(installed(), registry, argv)
    assert not registry.values and not registry.writes


def test_developer_and_python_worker_executables_do_not_register(installed):
    root = installed()
    registry = Registry()
    assert not install_location.register_gui_installation(
        root, [], executable=root / 'MOSES.exe', frozen=False, registry=registry)
    assert not install_location.register_gui_installation(
        root, [], executable=root / 'python.exe', frozen=True, registry=registry)
    assert not install_location.register_gui_installation(
        root, [], executable=root.parent / 'MOSES.exe', frozen=True, registry=registry)
    assert not registry.values


@pytest.mark.parametrize('missing', install_location.REQUIRED_FILES)
def test_partial_installations_are_not_registered(installed, missing):
    root = installed()
    (root / missing).unlink()
    registry = Registry()
    assert not register(root, registry)
    assert not registry.values


@pytest.mark.parametrize('denied', ['read', 'write'])
def test_registry_access_denied_is_nonfatal(installed, denied):
    registry = Registry(denied)
    assert not register(installed(), registry)
    assert not registry.writes


def test_missing_winreg_is_nonfatal(installed, monkeypatch):
    monkeypatch.setitem(sys.modules, 'winreg', None)
    root = installed()
    assert not install_location.register_gui_installation(
        root, [], executable=root / 'MOSES.exe', frozen=True)


@pytest.mark.parametrize('allowed', [True, False])
def test_runtime_records_only_after_license_and_keeps_launch_order(installed, monkeypatch, allowed):
    root = installed()
    order = []
    monkeypatch.setattr(license_runtime, 'check_license', lambda path: order.append('license') or allowed)
    monkeypatch.setattr(install_location, 'register_gui_installation',
                        lambda path, args: order.append(('location', path, args)) or False)
    monkeypatch.setattr(runtime_entry, 'prepare_python_path', lambda: order.append('path'))
    monkeypatch.setattr(runtime_entry, 'RuntimeBundle',
                        lambda path: SimpleNamespace(install=lambda: order.append('bundle') or 'bundle'))
    monkeypatch.setattr(runtime_entry, 'prepare_multiprocessing',
                        lambda: SimpleNamespace(freeze_support=lambda: order.append('spawn')))
    monkeypatch.setattr(runtime_entry, 'dispatch', lambda args, bundle: order.append('dispatch') or 0)
    monkeypatch.setattr(sys, 'executable', str(root / 'MOSES.exe'))
    monkeypatch.setattr(sys, 'dont_write_bytecode', sys.dont_write_bytecode)
    monkeypatch.setenv('PYTHONDONTWRITEBYTECODE', '1')
    assert runtime_entry.main([], root=root) == 0
    if allowed:
        assert order == ['license', ('location', root.resolve(), []), 'path', 'bundle', 'spawn', 'dispatch']
    else:
        assert order == ['license']
