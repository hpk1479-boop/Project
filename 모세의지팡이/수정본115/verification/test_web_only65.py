"""Web-only controls preserve engine contracts without opening GUIs or engines.

All settings, logs and machine-local roots used by these tests are temporary.
Browser lifecycle checks replace engine shutdown; JavaScript uses a fake DOM.
"""
from __future__ import annotations

import ast
import copy
import hashlib
import importlib.util
import io
import json
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / name) for name in ('Part3', 'Part2', 'Part1/program')]
from lab import integration, server, unified_backtest, unified_live, unified_settings
from lab import storage
from lab import backtest_jobs, catalog
from lab.ai.model_runtime import RUNTIME
from common_ai import client as common_client
import machine_roots
from event_backtest import settings as part2_settings, ui_model


GUI_FILES = (
    'Part1/OZ_SYSTEM CONTROL.pyw',
    'Part1/program/module_status_ui.py',
    'Part1/program/strategy_settings_view.py',
    'Part1/program/special_ui.py',
    'Part1/program/live_record_folder_ui.py',
    'Part1/program/modern_widgets.py',
    'Part1/program/ui_theme.py',
    'Part2/BACKTEST CONTROL.pyw',
    'Part2/LIVE_REPLAY.pyw',
    'Part2/event_backtest/gui.py',
    'Part2/event_backtest/detail_log_window.py',
    'Part2/event_backtest/result_ui.py',
    'Part2/event_backtest/segment_control.py',
)
GUI_MODULES = {Path(path).stem for path in GUI_FILES if path.endswith('.py')}


def test_obsolete_gui_files_are_removed_and_web_launchers_remain():
    remaining = [name for name in GUI_FILES if (ROOT / name).exists()]
    assert remaining == [], 'Obsolete GUI files remain: ' + ', '.join(remaining)
    for name in ('START_MOSES.pyw', 'Part3/run.py', 'Part3/START_PART3.pyw',
                 'Part1/live_control.py', 'Part1/program/event_host.py',
                 'Part2/event_backtest/__main__.py', 'Part2/START_BACKTEST.cmd'):
        assert (ROOT / name).is_file(), name


def test_production_modules_do_not_import_deleted_gui_or_tk():
    # Historical references, diagnostic tests and past build/patch scripts are
    # deliberately outside these production packages.
    paths = [ROOT / 'Part1/live_control.py', ROOT / 'Part2/bootstrap.py', ROOT / 'Part3/run.py']
    for package in ('Part1/program', 'Part2/event_backtest', 'Part3/lab'):
        paths.extend((ROOT / package).rglob('*.py'))
    violations = []
    for path in paths:
        if not path.is_file():
            continue
        tree = ast.parse(path.read_text('utf-8-sig'), filename=str(path))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [item.name for item in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or '']
                names += [((node.module + '.') if node.module else '') + item.name for item in node.names]
            for name in names:
                if name.split('.')[0] == 'tkinter' or name.split('.')[-1] in GUI_MODULES:
                    violations.append(f'{path.relative_to(ROOT).as_posix()}:{node.lineno}: {name}')
    assert violations == [], '\n'.join(violations)


@pytest.fixture
def temporary_settings(tmp_path, monkeypatch):
    project = tmp_path / 'project'
    part3 = project / 'Part3'
    program = project / 'Part1/program'
    program.mkdir(parents=True)
    (project / 'Part2').mkdir()
    (part3 / 'projects').mkdir(parents=True)
    config = program / 'config.txt'
    config.write_text('TELEGRAM_TOKEN=secret\nWONBI_SIGMA=3.0\n', encoding='utf-8')
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path / 'computer'))
    monkeypatch.setattr(storage, 'ROOT', part3)
    monkeypatch.setattr(unified_settings, 'ROOT', project)
    monkeypatch.setattr(unified_settings, 'LIVE_CONFIG', config)
    monkeypatch.setattr(unified_settings, 'PART2_CONFIG', project / 'Part2/event_backtest.json')
    monkeypatch.setattr(server, 'AI_SETTINGS', part3 / 'projects/ai_settings.json')
    fake_settings = SimpleNamespace(settings=lambda: {
        'cores': None, 'work_size': 'MONTH', 'capture_start': 'keyframe',
        'oz_evaluation': 'selected', 'overlap_trading_days': 3, 'broker_symbols': {}})
    monkeypatch.setattr(unified_backtest, '_part2', lambda: (fake_settings, None, None))
    return project


def test_live_record_root_is_visible_but_saved_only_on_this_computer(temporary_settings, tmp_path):
    project = temporary_settings
    assert unified_settings.read()['connections']['live_records'] == ''
    folder = tmp_path / 'live_records'
    folder.mkdir()
    machine_file = machine_roots.location_file()
    machine_file.parent.mkdir(parents=True)
    machine_file.write_text('{"another_setting": "keep"}', encoding='utf-8')
    before = (project / 'Part1/program/config.txt').read_bytes()
    assert unified_settings.save_connections({'live_records': str(folder)})['ok']
    assert unified_settings.read()['connections']['live_records'] == str(folder.resolve())
    local = json.loads(machine_file.read_text('utf-8'))
    assert local['another_setting'] == 'keep'
    assert local['live_records']['path'] == str(folder.resolve())
    assert local['live_records']['selection_id']
    for path in (project / 'Part3/projects').glob('*.json'):
        assert 'live_records' not in json.loads(path.read_text('utf-8'))
        assert str(folder.resolve()) not in path.read_text('utf-8')
    assert (project / 'Part1/program/config.txt').read_bytes() == before


def test_live_record_root_survives_project_move_without_absolute_project_path(
        temporary_settings, tmp_path, monkeypatch):
    project = temporary_settings
    records = tmp_path / 'records'
    records.mkdir()
    unified_settings.save_connections({'live_records': str(records)})
    moved = tmp_path / 'different_location/project'
    shutil.copytree(project, moved)
    monkeypatch.setattr(storage, 'ROOT', moved / 'Part3')
    monkeypatch.setattr(unified_settings, 'ROOT', moved)
    monkeypatch.setattr(unified_settings, 'LIVE_CONFIG', moved / 'Part1/program/config.txt')
    monkeypatch.setattr(server, 'AI_SETTINGS', moved / 'Part3/projects/ai_settings.json')
    assert storage.project_path() == str(moved.resolve())
    assert unified_settings.read()['connections']['live_records'] == str(records.resolve())
    for path in (moved / 'Part3/projects').glob('*.json'):
        assert str(project.resolve()) not in path.read_text('utf-8')
        assert str(moved.resolve()) not in path.read_text('utf-8')


def test_invalid_live_record_folder_does_not_modify_saved_roots(temporary_settings, tmp_path):
    records = tmp_path / 'records'
    records.mkdir()
    unified_settings.save_connections({'live_records': str(records)})
    machine_file = machine_roots.location_file()
    before = machine_file.read_bytes()
    with pytest.raises((ValueError, OSError)):
        unified_settings.save_connections({'live_records': str(tmp_path / 'does_not_exist')})
    assert machine_file.read_bytes() == before


@pytest.mark.parametrize('chat_id, expected', [('-10012345', '-10012345'), ('desk-owner', 'desk-owner'), ('', 'BACKTEST')])
def test_watch_chat_id_reaches_existing_scenario_contract(tmp_path, monkeypatch, chat_id, expected):
    config = {'cores': None, 'work_size': 'MONTH', 'capture_start': 'keyframe',
              'oz_evaluation': 'selected', 'overlap_trading_days': 3}
    saved = {'target_mode': 'SPECIAL', 'specials': {}, 'watch': {'text': '', 'chat_id': 'old'}}
    monkeypatch.setattr(ui_model, 'load', lambda: copy.deepcopy(saved))
    monkeypatch.setattr(ui_model, 'settings', lambda: dict(config))
    monkeypatch.setattr(part2_settings, 'ROOT', tmp_path)
    request = {'symbol': 'XAUUSD+', 'start': '2025-09-01', 'end': '2025-09-08',
               'mode': 'BAR', 'target_mode': 'WATCH', 'specials': [],
               'watch_text': '15분 상승추세 계속 감시', 'watch_chat_id': chat_id}
    result = unified_backtest._scenario(request)
    assert result['strategies'] == ['WATCH']
    assert result['commands'] == [{'strategy': 'WATCH', 'text': request['watch_text'], 'chat_id': expected}]
    assert saved['watch']['chat_id'] == 'old'
    assert list(tmp_path.rglob('*')) == []


class _CountedBinary(io.BytesIO):
    def __init__(self, content):
        super().__init__(content)
        self.read_requests = []

    def read(self, size=-1):
        self.read_requests.append(size)
        assert size >= 0, 'A log tail must not read the entire file'
        return super().read(size)

    def __exit__(self, *args):
        # Retain the read-request evidence for the assertion after close.
        return False


def test_log_tail_reads_only_requested_end_bytes_and_preserves_recent_utf8(tmp_path):
    from lab.log_tail import read_tail
    path = tmp_path / 'process.log'
    payload = b'old lines\n' * 100000 + '가나다 최근 실행 로그\n'.encode('utf-8')
    path.write_bytes(payload)
    handle = _CountedBinary(payload)
    original_open = Path.open

    def counted_open(item, *args, **kwargs):
        return handle if item == path else original_open(item, *args, **kwargs)

    with patch.object(Path, 'open', counted_open):
        text = read_tail(path, max_bytes=32)
    assert text.endswith('최근 실행 로그\n')
    assert 'old lines' not in text
    assert handle.read_requests and sum(handle.read_requests) <= 32
    assert path.read_bytes() == payload


def test_log_tail_handles_cut_utf8_and_missing_files(tmp_path):
    from lab.log_tail import read_tail
    path = tmp_path / 'utf8.log'
    path.write_bytes('가나다라 끝\n'.encode('utf-8'))
    assert read_tail(path, max_bytes=10).endswith('라 끝\n')
    assert read_tail(tmp_path / 'missing.log') == ''
    empty = tmp_path / 'empty.log'
    empty.touch()
    assert read_tail(empty) == ''


def test_backtest_log_api_is_read_only_and_handles_absent_journal(tmp_path):
    identifier = 'b' * 32
    journal = tmp_path / 'progress.jsonl'
    content = '{"event":"PROGRESS","message":"구축 중"}\n'
    job = {'id': identifier, 'folder': tmp_path}
    with patch.dict(unified_backtest.JOBS, {identifier: job}, clear=True):
        assert unified_backtest.log(identifier) == {'text': '', 'available': False}
        assert not journal.exists()
        journal.write_bytes(content.encode('utf-8'))
        before = hashlib.sha256(journal.read_bytes()).hexdigest()
        assert unified_backtest.log(identifier) == {'text': content, 'available': True}
        assert hashlib.sha256(journal.read_bytes()).hexdigest() == before
        with pytest.raises(ValueError):
            unified_backtest.log('../outside')
        with pytest.raises(ValueError):
            unified_backtest.log('c' * 32)


def test_backtest_log_prefers_original_console_output_to_progress_journal(tmp_path):
    identifier = 'e' * 32
    console = tmp_path / 'console.log'
    console.write_bytes('Python engine message\n파이썬 진단 원문\n'.encode('utf-8'))
    (tmp_path / 'progress.jsonl').write_bytes(b'{"event":"BUILD_PROGRESS"}\n')
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    with patch.dict(unified_backtest.JOBS, {identifier: {'id': identifier, 'folder': tmp_path}}, clear=True):
        assert unified_backtest.log(identifier) == {
            'text': 'Python engine message\n파이썬 진단 원문\n', 'available': True}
    assert {p.name: p.read_bytes() for p in tmp_path.iterdir()} == before


def test_subprocess_transport_preserves_non_json_diagnostics_and_appends_run_log(tmp_path, monkeypatch):
    from verification.test_backtest_jobs67 import make_job
    from lab import backtest_supervisor
    lines = 'plain engine diagnostic\n' + json.dumps(
        {'event': 'COMPLETE', 'result': {'status': 'COMPLETE', 'message': '완료'}},
        ensure_ascii=False) + '\n'

    class Process:
        pid = 42
        def __init__(self):
            self.stdout = io.StringIO(lines)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def wait(self, timeout=None):
            return 0

        def poll(self):
            return 0

    owner, _context = make_job(tmp_path, monkeypatch)
    monkeypatch.setattr(backtest_supervisor.subprocess, 'Popen', lambda *args, **kwargs: Process())
    assert owner.execute_action('plan') == 0
    plan = owner.last_result
    assert owner.execute_action('run') == 0
    assert (owner.folder / 'console.log').read_text('utf-8') == lines * 2
    assert plan == owner.last_result == {'status': 'COMPLETE', 'message': '완료'}


@pytest.mark.parametrize('ensure_ascii', [True, False])
def test_log_redacts_machine_roots_without_changing_parsed_events(tmp_path, monkeypatch, ensure_ascii):
    from verification.test_backtest_jobs67 import make_job
    from lab import backtest_supervisor
    warehouse = tmp_path / 'external_warehouse'
    event = {'event': 'COMPLETE', 'result': {
        'project_file': str(ROOT / 'Part2/metadata.json'),
        'warehouse_file': str(warehouse / 'runs/results.json')}}
    source = [f'project={ROOT} warehouse={warehouse}\n', json.dumps(event, ensure_ascii=ensure_ascii) + '\n']
    owner, _context = make_job(tmp_path, monkeypatch)
    owner.warehouse, owner.project_root = warehouse, ROOT
    class Process:
        pid = 42
        stdout = io.StringIO(''.join(source))
        def wait(self, timeout=None):return 0
        def poll(self):return 0
    monkeypatch.setattr(backtest_supervisor.subprocess, 'Popen', lambda *a, **k: Process())
    assert owner.execute_action('plan') == 0
    assert owner.last_result == event['result']
    path = owner.folder / 'console.log'
    stored = path.read_text('utf-8')
    for root in (ROOT, warehouse):
        assert str(root) not in stored
        assert json.dumps(str(root))[1:-1] not in stored
    assert 'project=<project> warehouse=<warehouse>\n' in stored
    stored_event = json.loads(stored.splitlines()[-1])
    assert stored_event['result']['project_file'].startswith('<project>')
    assert stored_event['result']['warehouse_file'].startswith('<warehouse>')
    assert json.loads(source[-1]) == event


def test_authenticated_http_log_route_uses_same_read_only_data(tmp_path):
    identifier = 'd' * 32
    content = '{"event":"COMPLETE"}\n'
    (tmp_path / 'progress.jsonl').write_bytes(content.encode('utf-8'))
    handler = object.__new__(server.Handler)
    handler.server = SimpleNamespace(token='test-token', server_port=8763, last_seen=None)
    handler.path = '/api/mo/backtest/log?id=' + identifier
    replies = []
    handler.send = lambda status, body: replies.append((status, body))
    with patch.dict(unified_backtest.JOBS, {identifier: {'id': identifier, 'folder': tmp_path}}, clear=True):
        handler.headers = {'X-Lab-Token': ''}
        handler.do_GET()
        assert replies[-1][0] == 403
        handler.headers = {'X-Lab-Token': 'test-token'}
        handler.do_GET()
        assert replies[-1] == (200, {'text': content, 'available': True})
    assert (tmp_path / 'progress.jsonl').read_text('utf-8') == content


def test_generated_strategy_job_status_uses_bounded_tail(tmp_path):
    from lab.log_tail import read_tail
    identifier = 'f' * 32
    folder = tmp_path / 'runs' / identifier
    folder.mkdir(parents=True)
    payload = 'earlier\n' * 100000 + '마지막 결과\n'
    path = folder / 'console.log'
    path.write_bytes(payload.encode('utf-8'))
    before = path.stat().st_size
    # Generated and native runs now read the same durable record and log.
    item = {'version': 1, 'id': identifier, 'kind': 'generated', 'phase': 'complete',
            'scenario': {}, 'adapter': {'filename': 'Test_SPECIAL123.py'},
            'scenario_file': 'web_scenario.json', 'result_path': 'runs/' + identifier + '/result.json',
            'cancel_requested': False, 'process': None, 'supervisor': None}
    (folder / 'job.json').write_text(json.dumps(item), encoding='utf-8')
    status = integration.job_status(identifier, warehouse=tmp_path, project_root=ROOT,
                                    python_executable=sys.executable)
    assert status['log'] == read_tail(path)
    assert status['log'].endswith('마지막 결과\n')
    assert len(status['log'].encode('utf-8')) <= 40000 + 3
    assert status['running'] is False and status['returncode'] == 0
    assert path.stat().st_size == before


def _browser_runner():
    spec = importlib.util.spec_from_file_location('_web_only65_browser_runner', ROOT / 'Part3/run.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def isolated_browser_runtime(monkeypatch, tmp_path):
    monkeypatch.setattr(catalog, 'ROOT', tmp_path / 'project' / 'Part3')
    monkeypatch.setattr(common_client, 'Client', Mock(return_value=Mock(spec=['shutdown', 'close'])))
    monkeypatch.setattr(backtest_jobs, 'shutdown', lambda **_kwargs: {'ok': True, 'warnings': []})
    monkeypatch.setattr(RUNTIME, 'shutdown', lambda **_kwargs: None)
    monkeypatch.setattr(backtest_jobs, '_closing', False)
    monkeypatch.setattr(backtest_jobs, '_SESSION_CONTEXTS', {}, raising=False)
    monkeypatch.setattr(backtest_jobs, '_SHUTDOWN_PENDING', {}, raising=False)


def test_browser_console_shutdown_closes_engines_before_server_close(monkeypatch, isolated_browser_runtime):
    runner = _browser_runner()
    calls = []

    class Host:
        server_port = 8763
        token = 'fixture'

        def serve_forever(self):
            raise KeyboardInterrupt

        def server_close(self):
            calls.append('server-close')

    monkeypatch.setattr(runner, 'LabServer', lambda port: Host())
    monkeypatch.setattr(unified_live, 'shutdown', lambda: calls.append('engine-stop'))
    runner.main(['--no-browser'])
    assert calls == ['engine-stop', 'server-close']


def test_browser_page_close_and_idle_timeout_both_stop_engines(monkeypatch, isolated_browser_runtime):
    runner = _browser_runner()
    targets = []

    class FakeThread:
        def __init__(self, target, **kwargs):
            self.target = target

        def start(self):
            targets.append(self.target)

    monkeypatch.setattr(runner.threading, 'Thread', FakeThread)
    monkeypatch.setattr(runner.time, 'sleep', lambda seconds: None)
    monkeypatch.setattr(runner.time, 'monotonic', lambda: 100)
    calls = []
    monkeypatch.setattr(unified_live, 'shutdown', lambda: calls.append('engine-stop'))
    for closed in (20, None):
        host = SimpleNamespace(last_seen=20, closed_at=closed, close_seen=20,
                               shutdown=lambda: calls.append('server-shutdown'))
        runner.stop_when_idle(host, seconds=30)
        targets.pop()()
    assert calls == ['engine-stop', 'server-shutdown'] * 2


def test_browser_close_failure_keeps_server_running(monkeypatch, isolated_browser_runtime):
    runner = _browser_runner()
    calls = []
    host = SimpleNamespace(shutdown=lambda: calls.append('server-shutdown'))

    def refused():
        calls.append('engine-stop')
        raise RuntimeError('engine still running')

    monkeypatch.setattr(unified_live, 'shutdown', refused)
    with pytest.raises(RuntimeError, match='engine still running'):
        runner.shutdown_runtime(host)
    assert calls == ['engine-stop']
    assert not getattr(host, 'runtime_stopped', False)


def test_actual_web_script_pause_scroll_details_and_watch_request(tmp_path):
    """Execute the production JS and its handlers without a browser or engine."""
    node = shutil.which('node')
    assert node, 'Node.js is required by current web verification'
    script = r'''
const fs = require('fs');
const vm = require('vm');
const assert = require('assert/strict');
const root = process.argv[1];
class Element {
    constructor() {
        this.value = ''; this.checked = false; this.textContent = ''; this.dataset = {};
        this.scrollTop = 0; this.scrollHeight = 900; this.options = [{}, {}];
        this.classes = new Set();
        this.classList = {
            toggle: (name, on) => { if (on) this.classes.add(name); else this.classes.delete(name); },
            add: name => this.classes.add(name), remove: name => this.classes.delete(name),
        };
    }
    append() {}
    replaceChildren() {}
    setAttribute() {}
    addEventListener() {}
    querySelectorAll() { return []; }
}
const html = fs.readFileSync(root + '/index.html', 'utf8');
const elements = new Map([...html.matchAll(/\bid="([^"]+)"/g)].map(match => [match[1], new Element()]));
const get = id => { assert(elements.has(id), 'Missing control: ' + id); return elements.get(id); };
const calls = [];
let live = { modules: { STAFF: {name: 'STAFF', state: '연결중'}, ENGINE: {name: '엔진', state: '연결중'} },
             lines: ['first log'], error: '' };
let detailed = { text: 'raw engine log', available: true };
let delayed = null;
const context = vm.createContext({
    console,
    document: { body: { dataset: {} }, querySelector: selector => elements.get(selector.slice(1)) || null,
                querySelectorAll: () => [], createElement: () => new Element(), createTextNode: text => ({textContent: text}) },
    window: {dispatchEvent() {}}, Option: Element,
    setInterval() {}, setTimeout() { return 1; }, clearTimeout() {},
    api: path => {
        calls.push(path);
        if (path.startsWith('mo/live/status')) return Promise.resolve(live);
        if (path.startsWith('mo/backtest/log')) return delayed || Promise.resolve(detailed);
        throw Error('Unexpected API: ' + path);
    },
});
const run = expression => vm.runInContext(expression, context);
const settle = () => new Promise(resolve => setImmediate(resolve));
(async () => {
    vm.runInContext(fs.readFileSync(root + '/unified.js', 'utf8'), context);
    await settle();
    calls.length = 0;
    get('live-log').textContent = 'frozen log'; get('live-log').scrollTop = 11;
    get('live-log-pause').checked = true; get('live-log-scroll').checked = true;
    live.lines = ['new log']; live.modules.ENGINE.state = '감시중';
    get('live-log-pause').onchange(); await settle();
    assert.equal(get('live-log').textContent, 'frozen log');
    assert.equal(get('live-log').scrollTop, 11);
    assert(get('live-message').textContent.includes('감시중'), 'Pause should only pause the log display');
    get('live-log-pause').checked = false;
    get('live-log-pause').onchange(); await settle();
    assert.equal(get('live-log').textContent, 'new log');
    assert.equal(get('live-log').scrollTop, 900);
    get('live-log-scroll').checked = false; get('live-log').scrollTop = 12;
    live.lines = ['latest log']; await run('moLiveStatus()');
    assert.equal(get('live-log').textContent, 'latest log');
    assert.equal(get('live-log').scrollTop, 12);
    get('live-detail').checked = true; get('live-detail').onchange(); await settle();
    assert(calls.at(-1).endsWith('detail=true'));

    run('moState.job = "job-a"; moState.view = "backtest";');
    get('mo-bt-detail').checked = false;
    const previousCalls = calls.length; await run('moBacktestDetailLog()');
    assert.equal(calls.length, previousCalls, 'Hidden detailed logs should not be fetched');
    get('mo-bt-detail').checked = true; get('mo-bt-log-scroll').checked = true;
    get('mo-bt-detail').onchange(); await settle();
    assert.equal(get('mo-bt-detail-log').textContent, 'raw engine log');
    assert.equal(get('mo-bt-detail-log').scrollTop, 900);
    assert.equal(get('mo-bt-detail-log').classes.has('hidden'), false);
    assert.equal(get('mo-bt-log').classes.has('hidden'), true);
    get('mo-bt-log-scroll').checked = false; get('mo-bt-detail-log').scrollTop = 13;
    detailed = { text: 'latest raw log', available: true }; await run('moBacktestDetailLog()');
    assert.equal(get('mo-bt-detail-log').textContent, 'latest raw log');
    assert.equal(get('mo-bt-detail-log').scrollTop, 13);
    get('mo-bt-detail').checked = false; get('mo-bt-detail').onchange(); await settle();
    assert.equal(get('mo-bt-detail-log').classes.has('hidden'), true);
    assert.equal(get('mo-bt-log').classes.has('hidden'), false);

    get('mo-bt-detail').checked = true;
    let resolveOld;
    delayed = new Promise(resolve => { resolveOld = resolve; });
    const beforePending = calls.filter(path => path.includes('mo/backtest/log')).length;
    const a = run('moBacktestDetailLog()'); const b = run('moBacktestDetailLog()');
    const pendingCalls = calls.filter(path => path.includes('mo/backtest/log')).length;
    assert.equal(pendingCalls, beforePending + 1, 'Concurrent log refreshes should share one request');
    run('moState.job = "job-b";');
    resolveOld({text: 'old job must not replace new job log', available: true});
    await Promise.all([a, b]);
    assert.equal(calls.filter(path => path.includes('mo/backtest/log')).length, pendingCalls);
    assert.equal(get('mo-bt-detail-log').textContent, 'latest raw log');
    delayed = null; detailed = {text: 'new job log', available: true};
    await run('moBacktestDetailLog()');
    assert.equal(get('mo-bt-detail-log').textContent, 'new job log');
    assert(calls.at(-1).endsWith('id=job-b'));

    get('mo-bt-symbol').value = 'XAUUSD+'; get('mo-bt-watch-chat').value = '-10076543';
    const request = run('moBacktestRequest()');
    assert.equal(request.watch_chat_id, '-10076543');
    get('mo-bt-watch-chat').value = '';
    assert.equal(run('moBacktestRequest()').watch_chat_id, 'BACKTEST');
    console.log('web-only behavior PASS');
})().catch(error => { console.error(error); process.exitCode = 1; });
'''
    result = subprocess.run([node, '-e', script, str(ROOT / 'Part3/web')],
                            cwd=tmp_path, capture_output=True, text=True,
                            encoding='utf-8', timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'web-only behavior PASS' in result.stdout
