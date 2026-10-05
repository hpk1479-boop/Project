"""Behavior checks for source-free loading, isolation and compiled dispatch."""
from __future__ import annotations

import ast
import hashlib
import importlib
import importlib.util
import json
import os
from pathlib import Path
import runpy
import shutil
import sys
import types
import uuid
import zipfile

import pytest

from releasekit.runtime_bundle import RuntimeBundle, build_code_bundle
from releasekit import runtime_entry


@pytest.fixture
def bundled(tmp_path):
    created = []

    def make(sources, *, folder='installed', references=None):
        source = tmp_path / ('sources' + str(len(created)))
        root = tmp_path / folder
        for relative, text in sources.items():
            path = source / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding='utf-8')
        for relative, raw in (references or {}).items():
            path = source / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
        build_code_bundle(source, root / 'runtime/code.bundle', paths=sources)
        bundle = RuntimeBundle(root).install()
        created.append(bundle)
        return root, bundle

    yield make
    for bundle in reversed(created):
        bundle.uninstall()
    for name, module in tuple(sys.modules.items()):
        filename = getattr(module, '__file__', None)
        if filename and any(str(bundle.root) in str(filename) for bundle in created):
            sys.modules.pop(name, None)


def test_import_package_and_nonidentifier_path_without_sources(bundled, monkeypatch):
    root, bundle = bundled({'app_pkg/__init__.py': 'from .component import value\n',
                            'app_pkg/component.py': 'value = 37\n',
                            'Part1/program/THE STAFF OF MOSES.py': 'answer = 41\n'})
    monkeypatch.syspath_prepend(str(root))
    assert importlib.import_module('app_pkg').value == 37
    path = root / 'Part1/program/THE STAFF OF MOSES.py'
    spec = importlib.util.spec_from_file_location('staff_runtime_test', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.answer == 41
    assert module.__file__ == str(path)
    assert not os.path.exists(path)


def test_fresh_profiles_from_path_loader_and_read_text_exec(bundled):
    root, bundle = bundled({'program/profile.py':
                            'count = 0\ndef increment():\n    global count\n    count += 1\n    return count\n'})
    path = root / 'program/profile.py'
    first = bundle.run_path(path, run_name='profile_first')
    second = bundle.run_path(path, run_name='profile_second')
    assert first['increment']() == 1
    assert first['increment']() == 2
    assert second['increment']() == 1
    third = types.ModuleType('profile_third')
    exec(compile(path.read_text('utf-8-sig'), str(path), 'exec'), third.__dict__)
    assert third.increment() == 1
    assert third.increment.__code__.co_filename == str(path)


def test_ast_literal_contract_is_available_without_original_source(bundled):
    source = ('BASE = 3\nVALUE = BASE * 5\nTFS = ("1m", "1h")\n'
              'WORDS = {"first": TFS}\ndef private_logic():\n    return VALUE + 200\n')
    root, bundle = bundled({'program/contract.py': source})
    facade = (root / 'program/contract.py').read_text('utf-8')
    literal = {node.targets[0].id: ast.literal_eval(node.value)
               for node in ast.parse(facade).body}
    assert literal == {'BASE': 3, 'VALUE': 15, 'TFS': ('1m', '1h'),
                       'WORDS': {'first': ('1m', '1h')}}
    assert 'def private_logic' not in facade
    assert 'return VALUE + 200' not in facade
    assert bundle.run_path(root / 'program/contract.py')['private_logic']() == 215


def test_virtual_globs_and_provenance_hash_real_compiled_bytes(bundled):
    root, bundle = bundled({'program/a.py': 'answer = 10\n',
                            'program/nested/b.py': 'answer = 20\n'})
    folder = root / 'program'
    assert folder.is_dir()
    assert {path.name for path in folder.glob('*.py')} == {'a.py'}
    assert {path.relative_to(folder).as_posix() for path in folder.rglob('*.py')} == {
        'a.py', 'nested/b.py'}
    assert {path.name for path in folder.iterdir()} == {'a.py', 'nested'}
    for path in folder.rglob('*.py'):
        relative = path.relative_to(root).as_posix()
        raw = path.read_bytes()
        with path.open('rb') as stream:
            assert stream.read() == raw
        assert hashlib.sha256(raw).hexdigest() == bundle.entries[relative]['sha256']
        assert path.stat().st_size == len(raw)


def test_physical_user_files_and_generated_code_are_unchanged(bundled, tmp_path):
    root, bundle = bundled({'program/fixed.py': 'answer = 1\n'})
    generated = root / 'generated/test.py'
    generated.parent.mkdir(parents=True)
    generated.write_text('answer = 98\n', encoding='utf-8')
    assert generated.read_text() == 'answer = 98\n'
    assert runpy.run_path(str(generated))['answer'] == 98
    settings = root / 'settings.json'
    settings.write_text('{"value": 7}', encoding='utf-8')
    assert json.loads(settings.read_text()) == {'value': 7}
    with pytest.raises(PermissionError):
        (root / 'program/fixed.py').write_text('answer = 0')
    with pytest.raises(FileNotFoundError):
        list((tmp_path / 'missing').iterdir())


def test_moving_install_root_preserves_file_paths_and_imports(tmp_path, monkeypatch):
    source = tmp_path / 'source'
    source.mkdir()
    (source / 'relocated.py').write_text('from pathlib import Path\nROOT = Path(__file__).parent\n', encoding='utf-8')
    original = tmp_path / 'first/runtime/code.bundle'
    build_code_bundle(source, original, paths=['relocated.py'])
    relocated = tmp_path / 'different location'
    (relocated / 'runtime').mkdir(parents=True)
    original.replace(relocated / 'runtime/code.bundle')
    bundle = RuntimeBundle(relocated).install()
    try:
        monkeypatch.syspath_prepend(str(relocated))
        assert importlib.import_module('relocated').ROOT == relocated
    finally:
        sys.modules.pop('relocated', None)
        bundle.uninstall()
    assert (source / 'relocated.py').read_text().startswith('from pathlib')


def test_dispatch_handles_script_module_and_command_without_logic_changes(bundled, monkeypatch):
    root, bundle = bundled({
        'app.py': 'import sys\nprint("script", sys.argv[1:])\n',
        'dispatch_package/__init__.py': '',
        'dispatch_package/__main__.py': 'import sys\nprint("module", sys.argv[1:])\n',
    })
    monkeypatch.syspath_prepend(str(root))
    monkeypatch.setattr(sys, 'argv', ['worker'])
    assert runtime_entry.dispatch(['-B', '-X', 'utf8', str(root / 'app.py'), '--flag', 'value'], bundle) == 0
    assert sys.argv == [str(root / 'app.py'), '--flag', 'value']
    assert runtime_entry.dispatch(['-u', '-m', 'dispatch_package', 'run'], bundle) == 0
    assert runtime_entry.dispatch(['-c', 'assert __name__ == "__main__"', 'rest'], bundle) == 0
    assert sys.argv == ['-c', 'rest']


def test_failed_license_loads_no_archive_or_application(tmp_path, monkeypatch):
    from releasekit import license_runtime
    monkeypatch.setattr(license_runtime, 'check_license', lambda root: False)
    monkeypatch.setattr(runtime_entry, 'RuntimeBundle', lambda *a: pytest.fail('bundle loaded before gate'))
    assert runtime_entry.main([], root=tmp_path) == 0


def test_frozen_spawn_reconstructs_dispatched_script_main(tmp_path, monkeypatch):
    from multiprocessing import spawn
    script = tmp_path / 'user_worker.py'
    module = types.ModuleType('__main__')
    module.__file__ = str(script)
    module.__spec__ = None
    monkeypatch.setitem(sys.modules, '__main__', module)
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    monkeypatch.setattr(spawn, 'WINEXE', True)
    monkeypatch.setattr(spawn, '_python_exe', spawn.get_executable())
    runtime_entry.prepare_multiprocessing()
    prepared = spawn.get_preparation_data('compiled-worker')
    assert prepared['init_main_from_path'] == str(script)
    command = spawn.get_command_line(pipe_handle=1)
    assert command[0] == sys.executable and command[1] == '--multiprocessing-fork'


def test_frozen_child_honors_application_pythonpath(bundled, monkeypatch):
    root, bundle = bundled({'helpers/nested/manager_helper.py': 'value = 29\n'})
    helper = root / 'helpers/nested'
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    monkeypatch.setattr(sys, 'path', list(sys.path))
    monkeypatch.setenv('PYTHONPATH', str(helper))
    runtime_entry.prepare_python_path()
    assert importlib.import_module('manager_helper').value == 29
    assert sys.path[0] == str(helper)


def test_native_adapter_uses_exact_ex5_and_detects_build_changes(bundled):
    root, bundle = bundled({
        'Part2/generic_backtest/native_mt5.py':
            'def ensure_native_mql_current(*a, **k):\n    raise AssertionError("source compilation")\n',
        'Part2/event_backtest/recording.py':
            'import hashlib, json\nfrom pathlib import Path\n'
            'def digest(data):\n    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()\n'
            'def file_hash(path):\n    return hashlib.sha256(Path(path).read_bytes()).hexdigest()\n'
            'def source_hash():\n    return "wrong-empty-mql-source-cache"\n',
    })
    names = bundle._ex5_names()
    folder = root / 'Part1/program/MT5'
    folder.mkdir(parents=True)
    for index, name in enumerate(names):
        (folder / name).write_bytes(b'licensed-ex5-' + bytes([index]))
    spec = importlib.util.spec_from_file_location('native_adapter_test', root / 'Part2/generic_backtest/native_mt5.py')
    native = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(native)
    events = []
    terminal = root / 'terminal'
    expert = native.ensure_native_mql_current({'data_root': str(terminal)}, root / 'work',
                                              emit=lambda *args: events.append(args))
    assert expert == terminal / 'MQL5/Experts/THE_STAFF_OF_MOSES.ex5'
    for name in names:
        destination = terminal / 'MQL5' / ('Experts' if name == names[-1] else 'Indicators') / name
        assert destination.read_bytes() == (folder / name).read_bytes()
    assert len(events) == 2
    spec = importlib.util.spec_from_file_location('recording_adapter_test', root / 'Part2/event_backtest/recording.py')
    recording = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(recording)
    before = recording.source_hash()
    (folder / names[-1]).write_bytes(b'new-expiry-same-code')
    assert recording.source_hash() != before


def test_archive_rejects_path_traversal_and_changed_code(tmp_path):
    source = tmp_path / 'source'
    source.mkdir()
    (source / 'a.py').write_text('answer = 5\n', encoding='utf-8')
    with pytest.raises(ValueError):
        build_code_bundle(source, tmp_path / 'bad.bundle', paths=['../a.py'])
    output = tmp_path / 'runtime/code.bundle'
    manifest = build_code_bundle(source, output, paths=['a.py'])
    with zipfile.ZipFile(output) as archive:
        original = archive.read('manifest.json')
    with zipfile.ZipFile(output, 'w') as archive:
        archive.writestr('manifest.json', original)
        archive.writestr(manifest['entries']['a.py']['member'], b'tampered')
    bundle = RuntimeBundle(tmp_path).install()
    try:
        with pytest.raises(ValueError, match='integrity'):
            bundle.run_path(tmp_path / 'a.py')
    finally:
        bundle.uninstall()


def test_reference_contract_hashes_work_without_mql_and_reject_tampering(bundled):
    raw = b'original reference, not distributed'
    digest = hashlib.sha256(raw).hexdigest()
    relative = 'Part2/generic_backtest/reference_sources/PRICE_of_Moses.mq5'
    root, bundle = bundled({
        'Part2/calculations/percentile/profiles.py':
            'SOURCE_HASHES = {"PRICE": ' + repr(digest) + '}\n'
            'class PercentileError(ValueError):\n    pass\n'
            'def verify_source_hashes(project_root):\n    raise AssertionError("raw source read")\n',
        'Part2/generic_backtest/canonical.py':
            'from pathlib import Path\nimport hashlib\n'
            'def file_hash(path):\n    return hashlib.sha256(Path(path).read_bytes()).hexdigest()\n',
    }, references={relative: raw})
    assert not (root / relative).is_file()
    spec = importlib.util.spec_from_file_location('reference_profiles_test', root / 'Part2/calculations/percentile/profiles.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.verify_source_hashes(root / 'Part2') == {'PRICE': digest}
    spec = importlib.util.spec_from_file_location('reference_canonical_test', root / 'Part2/generic_backtest/canonical.py')
    canonical = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(canonical)
    assert canonical.file_hash(root / relative) == digest
    # If a physical source is placed over the missing reference, the existing
    # expected SHA comparison must detect the replacement.
    path = root / relative
    path.parent.mkdir(parents=True)
    path.write_bytes(b'tampered reference')
    with pytest.raises(module.PercentileError, match='E_SOURCE_DRIFT'):
        module.verify_source_hashes(root / 'Part2')


def test_build_rejects_reference_drift_before_creating_distribution(tmp_path):
    profile = tmp_path / 'Part2/calculations/percentile/profiles.py'
    profile.parent.mkdir(parents=True)
    profile.write_text('SOURCE_HASHES = {"PRICE": "' + '0' * 64 + '"}\n', encoding='utf-8')
    source = tmp_path / 'Part2/generic_backtest/reference_sources/PRICE_of_Moses.mq5'
    source.parent.mkdir(parents=True)
    source.write_bytes(b'incorrect source')
    output = tmp_path / 'runtime/code.bundle'
    with pytest.raises(ValueError, match='E_SOURCE_DRIFT'):
        build_code_bundle(tmp_path, output, paths=['Part2/calculations/percentile/profiles.py'])
    assert not output.exists()


def test_actual_source_free_catalog_profiles_and_desktop_dry_run(tmp_path, monkeypatch):
    """Use current application code with fake UI/I/O, never existing services."""
    from releasekit.builder import prepare_data, source_paths
    # Let third-party packages do their normal OS version probes before the
    # application's service/process boundary is forbidden below.
    import pandas
    import requests
    project = Path(__file__).resolve().parents[3]
    selected = list(source_paths(project))
    before = {rel.as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
              for rel, path in selected}
    installed = tmp_path / 'actual installed'
    paths = prepare_data(project, installed, selected)
    build_code_bundle(project, installed / 'runtime/code.bundle', paths=paths)
    bundle = RuntimeBundle(installed).install()
    try:
        monkeypatch.syspath_prepend(str(installed))
        monkeypatch.syspath_prepend(str(installed / 'Part2'))
        monkeypatch.syspath_prepend(str(installed / 'Part1/program'))
        monkeypatch.syspath_prepend(str(installed / 'Part3'))
        # Fail if the dry run attempts any real network or child process.
        import socket
        import subprocess
        monkeypatch.setattr(socket.socket, 'connect', lambda *a, **k: pytest.fail('network during UI dry run'))
        monkeypatch.setattr(socket.socket, 'bind', lambda *a, **k: pytest.fail('server bind during UI dry run'))
        monkeypatch.setattr(subprocess, 'Popen', lambda *a, **k: pytest.fail('child process during UI dry run'))
        catalog = importlib.import_module('lab.catalog')
        assert catalog.TF_LABELS['1m'] == '1분'
        assert catalog.MA_PERIODS
        assert catalog.PROFILES.__file__ == str(installed / 'Part1/program/oz_profiles.py')
        application = importlib.import_module('event_application')
        first, _, plugins = application.load_strategy_inputs({}, selected=(), config={})
        first._runtime_test = True
        second, _, _ = application.load_strategy_inputs({}, selected=(), config={})
        assert first is not second and not hasattr(second, '_runtime_test')
        assert not plugins
        window_module = importlib.import_module('lab.desktop_window')
        monkeypatch.setattr(window_module.runtime_lifecycle, 'shutdown', lambda: {'warnings': []})

        class Event:
            def __iadd__(self, callback):
                self.callback = callback
                return self

        class Window:
            events = types.SimpleNamespace(closing=Event())

        class Server:
            def __init__(self, port):
                self.server_port, self.token = 12345, 'dry-run-token'
                self.closed = False
            def serve_forever(self):
                pass
            def shutdown(self):
                pass
            def server_close(self):
                self.closed = True

        calls = []
        fake_webview = types.SimpleNamespace(
            create_window=lambda *a, **k: (calls.append((a, k)) or Window()),
            start=lambda **k: calls.append(k))
        window_module._run_window(port=0, webview=fake_webview, server_factory=Server)
        assert calls[0][0][0] == 'THE STAFF OF MOSES'
        assert calls[1]['gui'] == 'edgechromium'
        native = importlib.import_module('generic_backtest.native_mt5')
        assert native.ensure_native_mql_current.__self__ is bundle
        # Load the reference validator as an isolated package. Its historical
        # package __init__ imports a provider from an older namespace, which is
        # unrelated to the current event engine and is not changed here.
        reference_package = types.ModuleType('_runtime_percentile_reference')
        reference_package.__path__ = [str(installed / 'Part2/calculations/percentile')]
        reference_package.__file__ = str(installed / 'Part2/calculations/percentile/__init__.py')
        sys.modules[reference_package.__name__] = reference_package
        profiles = importlib.import_module('_runtime_percentile_reference.profiles')
        assert profiles.verify_source_hashes(installed / 'Part2') == profiles.SOURCE_HASHES
        pit_contracts = importlib.import_module('pit.contracts')
        hma = installed / 'Part2/generic_backtest/reference_sources/THE_STAFF_OF_MOSES.mq5'
        actual_reference = hashlib.sha256((project / hma.relative_to(installed)).read_bytes()).hexdigest()
        assert pit_contracts.file_hash(hma) == actual_reference
        # Preserve the original pre-existing reference mismatch; licensing
        # must not silently turn this reference into an approved source.
        assert pit_contracts.file_hash(hma) != pit_contracts.HMA_SOURCE_SHA
        # The legacy verifier imports a historical percentile namespace that
        # is absent in the current application. Supply that dependency only
        # in the fixture to exercise its unchanged HMA comparison and error.
        monkeypatch.setitem(sys.modules, 'pit.features.percentile.profiles', profiles)
        with pytest.raises(pit_contracts.PitError, match='E_SOURCE_DRIFT'):
            pit_contracts.verify_sources()

        # Exercise the unchanged recording context manager and restore journal
        # with local fake EX5 and a fake terminal lifecycle only.
        recording = importlib.import_module('event_backtest.recording')
        terminal_lifecycle = importlib.import_module('event_backtest.terminal_lifecycle')
        monkeypatch.setattr(native, '_stop_selected_terminal', lambda *a, **k: None)
        monkeypatch.setattr(native, '_restart_selected_terminal', lambda profile, **k: profile)
        monkeypatch.setattr(terminal_lifecycle, 'wait_closed', lambda *a, **k: None)
        mt5_folder = installed / 'Part1/program/MT5'
        mt5_folder.mkdir(parents=True, exist_ok=True)
        for index, name in enumerate(bundle._ex5_names()):
            (mt5_folder / name).write_bytes(b'licensed-fixture-' + bytes([index]))
        terminal = installed / 'test-terminal'
        user_ea = terminal / 'MQL5/Experts/THE_STAFF_OF_MOSES.ex5'
        user_source = user_ea.with_suffix('.mq5')
        user_ea.parent.mkdir(parents=True)
        user_ea.write_bytes(b'user-original-ex5')
        user_source.write_bytes(b'user-original-source')
        profile = {'data_root': str(terminal), 'executable': str(terminal / 'terminal64.exe')}
        warehouse = installed / 'test-warehouse'
        with pytest.raises(RuntimeError, match='synthetic recording failure'):
            with recording.installed_build(profile, warehouse / 'build_runs/job1',
                                           lambda *a: None, lambda: None,
                                           build_root=warehouse / 'builds') as build_hash:
                assert build_hash == hashlib.sha256((mt5_folder / bundle._ex5_names()[-1]).read_bytes()).hexdigest()
                assert user_ea.read_bytes().startswith(b'licensed-fixture')
                raise RuntimeError('synthetic recording failure')
        assert user_ea.read_bytes() == b'user-original-ex5'
        assert user_source.read_bytes() == b'user-original-source'
        assert not (terminal / 'MQL5/Indicators/PRICE_of_Moses.ex5').exists()
        assert not (warehouse / 'build_runs/job1/restore_pending.json').exists()
        # First data construction must populate a cache from EX5 only. The
        # unchanged prepare path formerly required absent MQ5/MQH targets.
        plan_module = importlib.import_module('event_backtest.build_plan')
        piece = {'start': '2026-09-01', 'end': '2026-09-02', 'unit': 'DAY'}
        ea_hash = hashlib.sha256((mt5_folder / bundle._ex5_names()[-1]).read_bytes()).hexdigest()
        plan = {'record': [piece], 'reuse': [], 'convert': [], 'mode': 'BAR',
                'approval_token': 'fixture-token', 'ea_build_hash': ea_hash,
                'schema_id': 'fixture-schema'}
        monkeypatch.setattr(plan_module, 'make_plan', lambda *a, **k: plan)
        monkeypatch.setattr(terminal_lifecycle, 'launch_once_retry', lambda action, *a, **k: action())
        def fake_tester(*a, **k):
            assert user_ea.read_bytes().startswith(b'licensed-fixture')
            raise RuntimeError('synthetic first capture failure')
        monkeypatch.setattr(native, 'run_native_tester', fake_tester)
        # The unchanged capture writer correctly rejects MAX_PATH-sized roots.
        # Keep this fixture short enough to test deployment rather than that
        # independent path-length gate.
        first_warehouse = project / '검증결과' / ('rf' + uuid.uuid4().hex[:6])
        scenario = {'symbol': 'XAUUSD+', 'timer_ms': 1000}
        try:
            with pytest.raises(RuntimeError, match='synthetic first capture failure'):
                recording.prepare(scenario, first_warehouse, profile=profile,
                                  approved_token='fixture-token')
            cache = first_warehouse / 'builds' / recording.source_hash()
            cached = json.loads((cache / 'ready.json').read_text())
            assert set(cached['files']) == set(bundle._ex5_names())
            assert user_ea.read_bytes() == b'user-original-ex5'
            assert user_source.read_bytes() == b'user-original-source'
            assert not list((first_warehouse / 'build_runs').glob('*/restore_pending.json'))
        finally:
            assert first_warehouse.resolve().is_relative_to((project / '검증결과').resolve())
            if first_warehouse.is_dir():
                shutil.rmtree(first_warehouse)
    finally:
        for name, module in tuple(sys.modules.items()):
            if str(getattr(module, '__file__', '')).startswith(str(installed)):
                sys.modules.pop(name, None)
        bundle.uninstall()
    after = {rel.as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
             for rel, path in selected}
    assert after == before
