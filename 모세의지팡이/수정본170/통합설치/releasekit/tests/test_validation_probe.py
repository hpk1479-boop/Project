"""Validation fails closed for missing assets, bad imports and broken UI startup."""
import json
import io
from pathlib import Path

import pytest

from releasekit import validation_probe as probe


def test_assets_follow_actual_local_html_resources():
    assets = probe.Assets()
    assets.feed('<link href="new.css"><script src="/extra.js"></script>'
                '<script src="https://remote.example/x.js"></script><a href="unused">link</a>')
    assert assets.paths == {'/new.css', '/extra.js'}


def test_external_import_candidates_reject_app_entrypoint_and_empty_list():
    assert probe.import_names({'modules': ['logging.handlers', 'json', 'json']}) == ['logging.handlers', 'json']
    for value in ([], ['lab.server'], ['common_ai.service'], ['bad/name']):
        with pytest.raises(ValueError):
            probe.import_names(value)


def test_import_failure_produces_failed_check(tmp_path):
    (tmp_path / 'runtime').mkdir()
    (tmp_path / 'runtime/validation_candidates.json').write_text('["json", "missing_release_dependency_xyz"]')
    checks = []
    probe.probe_imports(tmp_path, checks)
    assert checks[0]['passed'] and not checks[1]['passed']


def test_atomic_report_requires_checks_and_copies_nonce(tmp_path, monkeypatch):
    monkeypatch.setenv('MOSES_VALIDATION_NONCE', 'nonce-123')
    report = probe.save_report(tmp_path, 'http', [], [])
    assert not report['passed'] and report['nonce'] == 'nonce-123'
    report = probe.save_report(tmp_path, 'http', [{'label': '/', 'passed': True}], [])
    assert report['passed']
    assert json.loads((tmp_path / 'runtime/validation_http.json').read_text('utf-8')) == report
    assert not list((tmp_path / 'runtime').glob('*.tmp'))


def test_four_rendered_views_and_bootstrap_errors():
    class Window:
        view = 'live'
        def evaluate_js(self, script):
            if script.startswith('moView('):
                self.view = json.loads(script.split('(', 1)[1].split(')', 1)[0])
                return True
            if script != probe.JS_STATE:
                return True
            return {'bootstrap': True, 'view': self.view, 'cards': 8,
                    'backtest': True, 'settings': True, 'strategy': True, 'errors': [], 'setup_error': ''}
    rows = probe.rendered_checks(Window(), deadline=10, clock=lambda: 0)
    assert [row['label'] for row in rows] == ['live', 'backtest', 'settings', 'strategy']
    assert all(row['passed'] for row in rows)
    class Broken(Window):
        def evaluate_js(self, script):
            return {'bootstrap': False, 'view': '', 'cards': 0, 'errors': ['bootstrap failure']}
    assert not probe.rendered_checks(Broken(), deadline=10, clock=lambda: 0)[0]['passed']


def test_strategy_detail_failure_blocks_release():
    class Window:
        view = 'live'
        def evaluate_js(self, script):
            if script.startswith('moView('):
                self.view = json.loads(script.split('(', 1)[1].split(')', 1)[0])
                return True
            if script == probe.JS_STRATEGY_DETAIL:
                return False
            if script != probe.JS_STATE:
                return True
            return {'bootstrap': True, 'view': self.view, 'cards': 8,
                    'backtest': True, 'settings': True, 'strategy': True, 'errors': [], 'setup_error': ''}
    rows = probe.rendered_checks(Window(), deadline=10, clock=lambda: 0)
    assert all(row['passed'] for row in rows[:3])
    assert rows[-1]['label'] == 'strategy' and not rows[-1]['passed']
    assert rows[-1]['state']['strategy_detail'] is False


def test_render_deadline_does_not_wait_forever():
    class Window:
        def evaluate_js(self, script):
            return {'bootstrap': False, 'cards': 0, 'view': ''}
    ticks = iter([0, 1, 2, 3, 4])
    rows = probe.rendered_checks(Window(), deadline=2, clock=lambda: next(ticks), sleep=lambda _: None)
    assert len(rows) == 1 and not rows[0]['passed'] and rows[0]['deadline_reached']


def test_first_generated_folder_open_fails_until_directory_is_packaged(tmp_path):
    folder = tmp_path / 'Part3/TEST_SPECIAL'
    with pytest.raises(FileNotFoundError):
        probe.checked_folder(folder, folder)
    folder.mkdir(parents=True)
    assert probe.checked_folder(folder, folder) == folder
    with pytest.raises(FileNotFoundError):
        probe.checked_folder(folder, folder.parent)


def test_http_requires_all_eight_live_cards(monkeypatch):
    class Response(io.BytesIO):
        status = 200
        headers = {'Content-Type': 'application/json'}
    count = [7]
    def response(*args, **kwargs):
        return Response(json.dumps({'modules': {str(i): {} for i in range(count[0])}}).encode())
    monkeypatch.setattr(probe, 'urlopen', response)
    row, _ = probe.request_row('http://127.0.0.1:1', 'fixture', '/api/mo/live/status?module=ALL')
    assert not row['passed'] and row['live_cards'] == 7
    count[0] = 8
    row, _ = probe.request_row('http://127.0.0.1:1', 'fixture', '/api/mo/live/status?module=ALL')
    assert row['passed'] and row['live_cards'] == 8


def test_service_guard_keeps_readonly_mt5_inventory():
    assert not probe.service_start_command(['powershell.exe', '-Command',
        'Get-CimInstance Win32_Process -Filter "Name=terminal64.exe"'])
    assert probe.service_start_command(['powershell.exe', '-Command',
        'Get-Process; Start-Process terminal64.exe'])
    assert probe.service_start_command(['terminal64.exe', '/portable'])
    assert probe.service_start_command(['python.exe', '-B', '-m', 'common_ai.service'])


@pytest.fixture
def strategy_probe_modules(monkeypatch):
    project = Path(__file__).resolve().parents[3]
    for relative in ('', 'Part1/program', 'Part2', 'Part3'):
        monkeypatch.syspath_prepend(str(project / relative))
    from Part1 import engine_processes
    from lab import strategy_library, unified_live, backtest_jobs
    monkeypatch.setattr(backtest_jobs, 'active_jobs', lambda: [])
    return engine_processes, strategy_library, unified_live


@pytest.mark.parametrize('inside', [False, True])
def test_lifecycle_inventory_is_scoped_and_real_idle_guard_remains(
        tmp_path, monkeypatch, strategy_probe_modules, inside):
    engine_api, library, live = strategy_probe_modules
    external = {'root': tmp_path / 'other-installation/Part1'}
    local = {'root': tmp_path / 'fixture/Part1/program/..'}
    inventory = [external, local] if inside else [external]
    original = lambda: list(inventory)
    monkeypatch.setattr(engine_api, 'find_engine_instances', original)
    monkeypatch.setattr(live, 'engines', lambda: {'engines': engine_api.find_engine_instances()})
    observed = []
    def initial_get(*args, **kwargs):
        observed.append(engine_api.find_engine_instances())
        library._idle()
        raise RuntimeError('fixture stopped after successful idle check')
    monkeypatch.setattr(probe, 'request_row', initial_get)
    checks = []
    probe.probe_user_strategies(tmp_path / 'fixture', 'http://fixture', 'token', checks)
    assert observed == [[local] if inside else []]
    assert engine_api.find_engine_instances is original
    assert engine_api.find_engine_instances() == inventory
    assert len(checks) == 1 and not checks[0]['passed']
    assert ('라이브 감시를 종료' if inside else 'successful idle check') in checks[0]['error']


@pytest.mark.parametrize('instance', [{}, {'root': None}, {'root': 123}, 'invalid'])
def test_untrusted_inventory_roots_cannot_be_silently_excluded(tmp_path, instance):
    with pytest.raises(ValueError, match='trustworthy root'):
        probe.fixture_engine_instances([instance], tmp_path)


@pytest.mark.parametrize('body,expected', [
    (b'not JSON', 'Unexpected lifecycle HTTP status: /api/generate:400'),
    ('{"error":"fixture diagnostic LOCATION"}', 'fixture diagnostic'),
])
def test_lifecycle_http_diagnostic_keeps_status_and_masks_paths(
        tmp_path, monkeypatch, strategy_probe_modules, body, expected):
    engine_api, _, _ = strategy_probe_modules
    original = lambda: []
    monkeypatch.setattr(engine_api, 'find_engine_instances', original)
    monkeypatch.setattr(probe, 'request_row', lambda *_, **__: ({'passed': True}, b'{"items":[]}'))
    class Response(io.BytesIO):
        status = 400
    location = json.dumps(str(tmp_path / 'private-location'))[1:-1]
    raw = body.replace('LOCATION', location).encode() if isinstance(body, str) else body
    monkeypatch.setattr(probe, 'urlopen', lambda *_, **__: Response(raw))
    checks = []
    probe.probe_user_strategies(tmp_path, 'http://fixture', 'token', checks)
    assert len(checks) == 1 and not checks[0]['passed']
    assert expected in checks[0]['error']
    assert str(tmp_path) not in checks[0]['error']
    assert engine_api.find_engine_instances is original
