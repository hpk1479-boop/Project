"""Source revision follows the current source folder and survives release moves."""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import threading
from types import SimpleNamespace
import urllib.request

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / 'Part3'), str(ROOT / '통합설치')]
from lab import server, storage, unified_backtest, unified_settings
from releasekit import builder


def minimal_source(root):
    for tree in ('Part3/web', 'Part3/docs'):
        (root / tree).mkdir(parents=True, exist_ok=True)


@pytest.mark.parametrize('revision', [112, 113, 999])
def test_developer_next_revision_uses_its_current_folder(tmp_path, revision):
    assert unified_settings.app_revision(tmp_path / ('수정본' + str(revision))) == revision


def test_release_revision_survives_folder_move_and_rename(tmp_path):
    source = tmp_path / '수정본112'
    minimal_source(source)
    payload = tmp_path / 'MOSES'
    builder.prepare_data(source, payload, [])
    assert json.loads((payload / 'runtime/app_version.json').read_bytes()) == {'schema': 1, 'revision': 112}
    assert not (source / 'runtime/app_version.json').exists()
    assert unified_settings.app_revision(payload) == 112
    moved = tmp_path / '다른 위치' / '수정본999'
    moved.parent.mkdir()
    shutil.move(payload, moved)
    assert unified_settings.app_revision(moved) == 112


def test_next_source_revision_replaces_previous_release_metadata(tmp_path):
    source = tmp_path / '수정본112'
    minimal_source(source)
    payload = tmp_path / '모세'
    builder.prepare_data(source, payload, [])
    assert unified_settings.app_revision(payload) == 112
    next_source = tmp_path / '수정본113'
    shutil.copytree(source, next_source)
    builder.prepare_data(next_source, payload, [])
    assert unified_settings.app_revision(payload) == 113


@pytest.mark.parametrize('value', ['broken json', '[]', '{"schema":2,"revision":111}',
                                  '{"schema":1,"revision":true}', '{"schema":1,"revision":-1}',
                                  '{"schema":1,"revision":112.0}'])
def test_invalid_metadata_does_not_break_settings(tmp_path, value):
    root = tmp_path / '모세'
    (root / 'runtime').mkdir(parents=True)
    (root / 'runtime/app_version.json').write_text(value, encoding='utf-8')
    assert unified_settings.app_revision(root) is None


def test_current_release_data_contains_source_revision_and_no_original_code(tmp_path):
    selected = list(builder.source_paths(ROOT))
    payload = tmp_path / '모세트레이딩시스템'
    code = builder.prepare_data(ROOT, payload, selected)
    assert 'Part3/lab/unified_settings.py' in code
    assert not (payload / 'Part3/lab/unified_settings.py').exists()
    # The label is the number of the folder the release was made from, whichever revision that is.
    assert unified_settings.app_revision(payload) == int(re.fullmatch(r'수정본([1-9][0-9]*)', ROOT.name)[1])
    metadata = (payload / 'runtime/app_version.json').read_text('utf-8')
    assert str(ROOT) not in metadata and ROOT.name not in metadata
    assert 'v1.0' not in metadata


def test_real_authenticated_settings_api_reports_developer_and_moved_release(tmp_path, monkeypatch):
    source = tmp_path / '수정본112'
    minimal_source(source)
    config = source / 'Part1/program/config.txt'
    config.parent.mkdir(parents=True)
    config.write_text('SYMBOLS=XAUUSD+\nECONOMY_ENABLED=false\n', encoding='utf-8')
    monkeypatch.setattr(unified_settings, 'LIVE_CONFIG', config)
    monkeypatch.setattr(unified_settings, 'ROOT', source)
    monkeypatch.setattr(unified_backtest, '_part2', lambda: (SimpleNamespace(settings=lambda: {}), None))
    monkeypatch.setattr(storage, 'connections', lambda: {})
    monkeypatch.setattr(server, 'ai_settings', lambda: {'provider': 'disabled'})
    host = server.LabServer(port=0)
    worker = threading.Thread(target=host.serve_forever, daemon=True)
    worker.start()
    try:
        url = 'http://127.0.0.1:' + str(host.server_port) + '/api/mo/settings'
        request = urllib.request.Request(url, headers={'X-Lab-Token': host.token})
        with urllib.request.urlopen(request) as response:
            assert json.load(response)['app_revision'] == 112
        payload = tmp_path / 'install'
        builder.prepare_data(source, payload, [])
        moved = tmp_path / '옮긴 모세'
        payload.rename(moved)
        monkeypatch.setattr(unified_settings, 'ROOT', moved)
        with urllib.request.urlopen(request) as response:
            assert json.load(response)['app_revision'] == 112
    finally:
        host.shutdown()
        host.server_close()
        worker.join(timeout=5)


def test_settings_version_footer_in_real_browser(tmp_path):
    runtime = Path.home() / '.cache/codex-runtimes/codex-primary-runtime/dependencies/node'
    node = os.environ.get('MOSES_TEST_NODE') or shutil.which('node') or str(runtime / 'bin/node.exe')
    playwright = os.environ.get('MOSES_TEST_PLAYWRIGHT') or str(runtime / 'node_modules/playwright')
    if not Path(node).is_file() or not Path(playwright).is_dir():
        pytest.skip('Headless browser test needs Node.js and Playwright')
    evidence = Path(os.environ.get('MOSES_EVIDENCE_ROOT') or ROOT / '검증결과') / 'revision112/version'
    evidence.mkdir(parents=True, exist_ok=True)
    completed = subprocess.run([node, str(Path(__file__).with_suffix('.cjs')), playwright, str(evidence)],
                               capture_output=True, text=True, encoding='utf-8', timeout=60)
    (evidence / 'browser_output.txt').write_text(completed.stdout + completed.stderr, encoding='utf-8')
    assert completed.returncode == 0, completed.stdout + completed.stderr
