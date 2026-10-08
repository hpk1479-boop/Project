"""Dependency collection uses source fixtures and does not launch an app."""
from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest

from releasekit import dependencies


def write(root, relative, text):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding='utf-8')
    return path


@pytest.fixture
def project(tmp_path, monkeypatch):
    root = tmp_path / 'synthetic project'
    root.mkdir()
    monkeypatch.setattr(dependencies, 'RUNTIME_IMPORTS', frozenset())
    return root


def installed(monkeypatch, names):
    monkeypatch.setattr(dependencies, '_find_spec', lambda name, search_paths=None: object() if name in names else None)


def test_qualified_import_and_submodule_are_kept(project, monkeypatch):
    source = 'import logging.handlers\nfrom concurrent import futures\nfrom pathlib import Path\n'
    write(project, 'app.py', source)
    installed(monkeypatch, {'logging.handlers', 'concurrent', 'concurrent.futures', 'pathlib'})
    result = dependencies.collect_dependencies(project, ['app.py'])
    assert result['required_imports'] == ['concurrent', 'concurrent.futures', 'logging.handlers', 'pathlib']
    assert result['unavailable_required'] == []
    assert 'pathlib.Path' not in result['required_imports']
    assert result['import_references']['logging.handlers'] == ['app.py:1']


def test_parent_packages_are_not_executed_when_finding_submodules(project, tmp_path, monkeypatch):
    external = tmp_path / 'fixture external packages'
    write(external, 'fixture_vendor_unique/__init__.py', 'raise RuntimeError("package must not be executed")\n')
    write(external, 'fixture_vendor_unique/child.py', 'raise RuntimeError("submodule must not be executed")\n')
    monkeypatch.setattr(sys, 'path', [str(external), *sys.path])
    write(project, 'app.py', 'from fixture_vendor_unique import child, VALUE\n')
    result = dependencies.collect_dependencies(project, ['app.py'])
    assert result['required_imports'] == ['fixture_vendor_unique', 'fixture_vendor_unique.child']
    assert result['unavailable_required'] == []
    assert 'fixture_vendor_unique' not in sys.modules
    assert 'fixture_vendor_unique.child' not in sys.modules


def test_unknown_required_import_is_reported_and_stops_build(project, monkeypatch):
    write(project, 'app.py', 'import fixture_uninstalled\nimport installed_pkg.missing_child\n')
    installed(monkeypatch, {'installed_pkg'})
    result = dependencies.collect_dependencies(project, ['app.py'])
    assert [item['module'] for item in result['unavailable_required']] == ['fixture_uninstalled', 'installed_pkg.missing_child']
    assert result['unavailable_required'][0]['references'] == ['app.py:1']
    with pytest.raises(ValueError, match='fixture_uninstalled'):
        dependencies.build_hidden_imports(project, ['app.py'])
    assert str(project) not in json.dumps(result)


def test_windows_branch_selection_and_aliases_are_classified(project, monkeypatch):
    write(project, 'app.py', '''import os as host
import sys as interpreter
import platform as machine
if host.name == 'nt':
    import msvcrt
else:
    import fcntl
if interpreter.platform.startswith('linux'):
    import resource
if machine.system() == 'Windows':
    import ctypes.wintypes
if 0:
    import unreachable_pkg
''')
    installed(monkeypatch, {'os', 'sys', 'platform', 'msvcrt', 'ctypes.wintypes'})
    result = dependencies.collect_dependencies(project, ['app.py'])
    assert set(result['required_imports']) == {'os', 'sys', 'platform', 'msvcrt', 'ctypes.wintypes'}
    assert {item['module'] for item in result['excluded_platform']} == {'fcntl', 'resource', 'unreachable_pkg'}
    assert result['unavailable_required'] == []


def test_unconditional_nonwindows_module_cannot_be_silently_skipped(project, monkeypatch):
    write(project, 'app.py', 'import fcntl\n')
    installed(monkeypatch, set())
    with pytest.raises(ValueError, match='fcntl'):
        dependencies.build_hidden_imports(project, ['app.py'])


def test_unknown_runtime_condition_still_requires_import(project, monkeypatch):
    write(project, 'app.py', 'if feature_enabled:\n    import fixture_required\n')
    installed(monkeypatch, set())
    assert dependencies.collect_dependencies(project, ['app.py'])['unavailable_required'][0]['module'] == 'fixture_required'


def test_known_optional_ai_build_tools_and_explicit_fallback_are_recorded(project, monkeypatch):
    write(project, 'common_ai/fixture_worker.py', '''import torch
from lmformatenforcer import JsonSchemaParser
import pytest
try:
    import fixture_optional
except ImportError:
    pass
try:
    import fixture_required
except RuntimeError:
    pass
''')
    installed(monkeypatch, set())
    result = dependencies.collect_dependencies(project, ['common_ai/fixture_worker.py'])
    assert {item['module'] for item in result['excluded_optional']} == {'torch', 'lmformatenforcer', 'pytest', 'fixture_optional'}
    assert [item['module'] for item in result['unavailable_required']] == ['fixture_required']


def test_non_ai_torch_import_is_required_and_missing_module_blocks_build(project, monkeypatch):
    write(project, 'Part2/new_feature.py', 'import torch\n')
    installed(monkeypatch, set())
    result = dependencies.collect_dependencies(project, ['Part2/new_feature.py'])
    assert result['excluded_optional'] == []
    assert result['unavailable_required'][0]['module'] == 'torch'
    assert result['unavailable_required'][0]['references'] == ['Part2/new_feature.py:1']
    with pytest.raises(ValueError, match='torch'):
        dependencies.build_hidden_imports(project, ['Part2/new_feature.py'])


def test_non_ai_installed_torch_and_actual_submodule_are_kept(project, monkeypatch):
    write(project, 'Part2/new_feature.py', 'from torch import optim\n')
    installed(monkeypatch, {'torch', 'torch.optim'})
    result = dependencies.collect_dependencies(project, ['Part2/new_feature.py'])
    assert result['required_imports'] == ['torch', 'torch.optim']
    assert result['probe_imports'] == ['torch', 'torch.optim']


def test_same_ai_library_may_be_optional_in_ai_and_required_in_normal_feature(project, monkeypatch):
    write(project, 'common_ai/fixture_worker.py', 'import torch\n')
    write(project, 'Part3/lab/new_feature.py', 'import torch\n')
    installed(monkeypatch, {'torch'})
    result = dependencies.collect_dependencies(project, ['common_ai/fixture_worker.py', 'Part3/lab/new_feature.py'])
    assert result['required_imports'] == ['torch']
    assert result['import_references']['torch'] == ['Part3/lab/new_feature.py:1']
    assert result['excluded_optional'][0]['references'] == ['common_ai/fixture_worker.py:1']


def test_ai_scope_requires_exact_directory_prefix(project, monkeypatch):
    write(project, 'common_ai_other/feature.py', 'import torch\n')
    installed(monkeypatch, set())
    with pytest.raises(ValueError, match='torch'):
        dependencies.build_hidden_imports(project, ['common_ai_other/feature.py'])


def test_installed_optional_fallback_is_bundled(project, monkeypatch):
    write(project, 'app.py', 'try:\n    import fixture_optional\nexcept ModuleNotFoundError:\n    pass\n')
    installed(monkeypatch, {'fixture_optional'})
    assert dependencies.build_hidden_imports(project, ['app.py']) == ['fixture_optional']


def test_project_namespaces_and_legacy_internal_imports_are_excluded(project, monkeypatch):
    write(project, 'app.py', 'import new_pkg.child\nimport pit.features.percentile.old\nfrom replay.operational_baseline import old\nimport local_helper\n')
    write(project, 'Part2/new_pkg/child.py', 'VALUE=1\n')
    write(project, 'Part3/local_helper.py', 'VALUE=1\n')
    installed(monkeypatch, set())
    result = dependencies.collect_dependencies(project, ['app.py', 'Part2/new_pkg/child.py', 'Part3/local_helper.py'])
    assert result['required_imports'] == []
    assert result['unavailable_required'] == []
    assert {item['module'] for item in result['excluded_internal']} == {'new_pkg.child', 'pit.features.percentile.old', 'replay.operational_baseline', 'local_helper'}


def test_stdlib_name_wins_over_a_nested_project_module_stem(project, monkeypatch):
    write(project, 'app.py', 'import calendar\n')
    write(project, 'Part2/event_backtest/calendar.py', 'VALUE=1\n')
    installed(monkeypatch, {'calendar'})
    result = dependencies.collect_dependencies(project, ['app.py', 'Part2/event_backtest/calendar.py'])
    assert result['required_imports'] == ['calendar']
    assert result['excluded_internal'] == []


def test_literal_dynamic_import_is_required_and_nonliteral_is_reported(project, monkeypatch):
    write(project, 'app.py', '''import importlib as loader
loader.import_module('logging.handlers')
__import__('json')
loader.import_module(selected_strategy)
''')
    installed(monkeypatch, {'importlib', 'logging.handlers', 'json'})
    result = dependencies.collect_dependencies(project, ['app.py'])
    assert set(result['required_imports']) == {'importlib', 'logging.handlers', 'json'}
    assert [item['module'] for item in result['dynamic_imports']] == ['logging.handlers', 'json', None]


def test_managed_gui_imports_are_required_but_have_separate_probe(project, monkeypatch):
    write(project, 'app.py', 'import webview\nimport clr\nimport json\n')
    installed(monkeypatch, {'webview', 'clr', 'json'})
    result = dependencies.collect_dependencies(project, ['app.py'])
    assert result['required_imports'] == ['clr', 'json', 'webview']
    assert result['probe_imports'] == ['json']
    assert {item['module'] for item in result['probe_excluded']} == {'clr', 'webview'}


def test_required_runtime_defaults_are_also_checked(project, monkeypatch):
    write(project, 'app.py', 'VALUE=1\n')
    monkeypatch.setattr(dependencies, 'RUNTIME_IMPORTS', frozenset({'numpy', 'clr_loader'}))
    installed(monkeypatch, {'numpy'})
    with pytest.raises(ValueError, match='clr_loader'):
        dependencies.build_hidden_imports(project, ['app.py'])


@pytest.mark.parametrize('relative', ['../outside.py', '/outside.py', 'app.txt'])
def test_invalid_source_paths_are_rejected(project, relative):
    with pytest.raises(ValueError, match='상대 Python'):
        dependencies.collect_dependencies(project, [relative])


def test_collection_is_portable_after_project_move(project, tmp_path, monkeypatch):
    write(project, 'Part3/app.py', 'import logging.handlers\n')
    installed(monkeypatch, {'logging.handlers'})
    before = dependencies.collect_dependencies(project, ['Part3/app.py'])
    moved = tmp_path / 'different disk folder' / 'moved source'
    moved.parent.mkdir()
    project.rename(moved)
    after = dependencies.collect_dependencies(moved, ['Part3/app.py'])
    assert before == after
    assert str(project) not in json.dumps(after)
    assert str(moved) not in json.dumps(after)
