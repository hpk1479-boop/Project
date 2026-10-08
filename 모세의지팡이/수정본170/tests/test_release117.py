"""The release built from this revision, source-free and moved, has this revision's changes.

It checks what the user asked to carry into a release: the LIVE record folder made automatically inside
the installed folder (and following it when moved), the 사용 / 사용 안 함 setting, the analysis-ready
result status, and the web files with the lower-right version label.
"""
import json
import os
from pathlib import Path
import re
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / '통합설치'
sys.path.insert(0, str(TOOLS))
from releasekit import builder, resources
from releasekit.runtime_bundle import build_code_bundle

REVISION = int(re.fullmatch(r'수정본([1-9][0-9]*)', ROOT.name)[1])
WEB = ('ai_editor.js', 'ai_editor.css', 'unified.js', 'style.css', 'backtest_dashboard.js', 'index.html')
CODE = {'Part1/program/live_alert_recording.py', 'Part1/program/event_host.py', 'Part1/program/special_time_slot.py',
        'Part1/program/special_settings_model.py', 'Part1/program/strategy_recipe/registry.py',
        'Part3/lab/backtest_jobs.py', 'Part3/lab/unified_settings.py', 'Part3/lab/unified_live.py'}

PROBE = r'''
import datetime as dt, json, os, shutil, sys
from pathlib import Path
from types import SimpleNamespace as NS
root, tooling, work = Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3])
physical = [name for _, _, names in os.walk(root) for name in names if name.endswith(('.py', '.pyw'))]
sys.path[:0] = [tooling, str(root), str(root/'Part3'), str(root/'Part2'), str(root/'Part1/program')]
from releasekit.runtime_bundle import RuntimeBundle
bundle = RuntimeBundle(root).install()
try:
    from event_engine.model import Kind
    import live_alert_recording as rec
    from lab import backtest_jobs, unified_settings

    def event(key):
        return NS(kind=Kind.SIGNAL, source_time=int(dt.datetime(2026, 9, 29, 12, tzinfo=dt.timezone.utc).timestamp() * 1000),
                  payload={'signal_id': key, 'strategy': 'SPECIAL1', 'symbol': 'XAUUSD+', 'content': {
                      'type': 'NOTIFICATION', 'message': 'probe', 'recipients': ['T'],
                      'event': {'source_tf': '1m', 'direction': 'LONG', 'grade': 'A'}}})

    # 1. The record folder: made at start, inside the program folder, nothing chosen.
    program = root / 'Part1' / 'program'
    existed = (root / 'LIVE_기록').exists()
    recorder = rec.LiveAlertRecorder({}, program=program)
    recorder.record(event('a'), 'T', 'sent')
    folder = root / 'LIVE_기록' / 'alerts'
    files = sorted(path.name for path in folder.glob('*.csv'))
    count = sum(len(path.read_text('utf-8').splitlines()) - 1 for path in folder.glob('*.csv'))
    # 2. Off: no folder, no file.
    off_root = work / 'off_probe'
    (off_root / 'Part1' / 'program').mkdir(parents=True)
    rec.LiveAlertRecorder({'LIVE_RECORD_ENABLED': 'false'}, program=off_root / 'Part1' / 'program').record(event('b'), 'T', 'sent')
    # 3. The settings write: the user's older config.txt gets the line when it is first saved.
    config = work / 'config_copy.txt'
    older = [line for line in (root / 'Part1/program/config.txt').read_text('utf-8').splitlines(keepends=True)
             if not line.startswith('LIVE_RECORD_ENABLED')]
    config.write_text(''.join(older), encoding='utf-8')
    unified_settings.LIVE_CONFIG = config
    unified_settings._save_live({'LIVE_RECORD_ENABLED': 'false'})
    saved = unified_settings._live_values(unified_settings._live_lines()[0])['LIVE_RECORD_ENABLED']
    # 4. A virtual-entry result opens once its request has ended (수정본170: no analysis file follows it).
    run = work / 'run'
    run.mkdir(exist_ok=True)
    (run / 'result.json').write_text(json.dumps({'status': 'COMPLETE', 'result_mode': 'VIRTUAL_ENTRY'}))
    waiting = backtest_jobs._result_ready({'folder': run, 'active': True})
    ready = backtest_jobs._result_ready({'folder': run, 'active': False})
    print(json.dumps({'existed_before': existed, 'files': files, 'rows': count, 'off_folder': (off_root / 'LIVE_기록').exists(),
                      'saved': saved, 'waiting': waiting, 'ready': ready,
                      'older_had_line': any(line.startswith('LIVE_RECORD_ENABLED') for line in older),
                      'physical_source_count': len(physical)}, ensure_ascii=False, sort_keys=True))
finally:
    bundle.uninstall()
'''


def run_probe(root, work):
    work.mkdir(parents=True, exist_ok=True)
    result = subprocess.run([sys.executable, '-I', '-B', '-X', 'utf8', '-c', PROBE, str(root), str(TOOLS), str(work)],
                            cwd=work, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=180,
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    assert result.returncode == 0, result.stderr[-9000:] + result.stdout[-1000:]
    return json.loads(result.stdout.strip().splitlines()[-1])


@pytest.fixture(scope='module')
def evidence(tmp_path_factory):
    work = tmp_path_factory.mktemp('rel117')
    payload = work / 'MOSES'
    selected = list(builder.source_paths(ROOT))
    manifest = resources.discover_resources(ROOT, selected)
    code = builder.prepare_data(ROOT, payload, selected)
    compiled = build_code_bundle(ROOT, payload / 'runtime/code.bundle', paths=code)
    resources.validate_payload(payload, manifest, code)
    shipped_records = (payload / 'LIVE_기록').exists()
    first = run_probe(payload, work / 'probe_installed')
    moved_root = work / '다른 위치' / '모세 새이름'
    moved_root.parent.mkdir()
    payload.rename(moved_root)
    moved = run_probe(moved_root, work / 'probe_moved')
    return {'payload': moved_root, 'compiled': compiled, 'first': first, 'moved': moved, 'work': work,
            'shipped_records': shipped_records}


def test_the_release_carries_this_revisions_web_files(evidence):
    for name in WEB:
        assert (evidence['payload'] / 'Part3/web' / name).read_bytes() == (ROOT / 'Part3/web' / name).read_bytes(), name
    style = (evidence['payload'] / 'Part3/web/style.css').read_text('utf-8')
    assert re.search(r'\.moses-settings-version\{[^}]*text-align:right', style)
    assert 'text-align:center' not in re.search(r'\.moses-settings-version\{[^}]*\}', style)[0]
    editor = (evidence['payload'] / 'Part3/web/ai_editor.js').read_text('utf-8')
    assert "key==='level_gate'" in editor and '연결할 외부유동성 터치' in editor


def test_the_source_free_bundle_has_the_changed_code(evidence):
    entries = set(evidence['compiled']['entries'])
    assert CODE <= entries
    assert evidence['first']['physical_source_count'] == evidence['moved']['physical_source_count'] == 0


def test_the_release_config_has_the_record_setting_on_and_no_records_are_shipped(evidence):
    config = (evidence['payload'] / 'Part1/program/config.txt').read_text('utf-8')
    assert re.search(r'^LIVE_RECORD_ENABLED=true\s*$', config, re.M)
    assert evidence['shipped_records'] is False, 'the developer\'s own records never go into a release'


def test_the_folder_is_made_in_the_installed_folder_and_follows_it_when_moved(evidence):
    first, moved = evidence['first'], evidence['moved']
    assert first['existed_before'] is False, 'the folder is made by the program, not shipped'
    assert first['files'] == ['2026-09-29.csv'] and first['rows'] == 1
    # The moved copy is the installed folder renamed: the first run's record travelled with it, then one row more.
    assert moved['existed_before'] is True and moved['files'] == ['2026-09-29.csv'] and moved['rows'] == 2
    assert first['off_folder'] is False and moved['off_folder'] is False
    folder = evidence['payload'] / 'LIVE_기록' / 'alerts'
    assert sum(len(path.read_text('utf-8').splitlines()) - 1 for path in folder.glob('*.csv')) == 2
    assert not any(str(evidence['work']) in path.read_text('utf-8') for path in folder.glob('*.csv'))


def test_the_released_settings_toggle_works_on_an_older_config(evidence):
    for key in ('first', 'moved'):
        assert evidence[key]['older_had_line'] is False and evidence[key]['saved'] == 'false'


def test_the_released_result_status_waits_for_the_request_to_end(evidence):
    for key in ('first', 'moved'):
        assert evidence[key]['waiting'] is False and evidence[key]['ready'] is True


def test_the_release_label_is_this_revision(evidence):
    data = json.loads((evidence['payload'] / 'runtime/app_version.json').read_bytes())
    assert data == {'schema': 1, 'revision': REVISION}
