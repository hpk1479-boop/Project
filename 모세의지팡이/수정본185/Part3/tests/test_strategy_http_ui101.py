"""Real web files + authenticated strategy APIs, with all mutable state in tmp_path."""
import copy
import functools
import json
import os
from pathlib import Path
import runpy
import shutil
import subprocess
import sys
import threading

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / 'Part1/program'), str(ROOT / 'Part2'), str(ROOT / 'Part3')]


def test_real_http_strategy_library_buttons(tmp_path, monkeypatch):
    from strategy_recipe import registry
    from lab import storage, server, unified_live, unified_backtest, unified_settings, backtest_jobs
    from event_backtest import ui_model
    import oz_profiles

    runtime = Path.home() / '.cache/codex-runtimes/codex-primary-runtime/dependencies/node'
    node = os.environ.get('MOSES_TEST_NODE') or shutil.which('node') or str(runtime / 'bin/node.exe')
    playwright = os.environ.get('MOSES_TEST_PLAYWRIGHT') or str(runtime / 'node_modules/playwright')
    if not Path(node).is_file() or not Path(playwright).is_dir():
        pytest.skip('Headless browser test needs Node.js and Playwright')
    for part in ('Part1/program', 'Part2', 'Part3', 'settings'):
        (tmp_path / part).mkdir(parents=True)
    target = tmp_path / 'settings/strategy_registry.json'
    target.write_bytes(registry.REGISTRY.read_bytes())
    monkeypatch.setattr(registry, 'REGISTRY', target)
    monkeypatch.setattr(storage, 'ROOT', tmp_path / 'Part3')
    monkeypatch.setattr(server, 'ROOT', tmp_path / 'Part3')
    monkeypatch.setattr(server, 'AI_SETTINGS', tmp_path / 'settings/ai_settings.json')
    server.AI_SETTINGS.write_text('{"provider":"disabled"}', encoding='utf-8')
    shutil.copytree(ROOT / 'Part3/web', tmp_path / 'Part3/web')
    config = tmp_path / 'Part1/program/config.txt'
    config.write_text('MAIN_ASIA=0800-1200\nMAIN_LONDON=1400-1800\nMAIN_NEWYORK=2100-2400\n', encoding='utf-8')
    monkeypatch.setattr(unified_settings, 'LIVE_CONFIG', config)
    monkeypatch.setattr(unified_settings, 'ROOT', tmp_path)
    monkeypatch.setattr(unified_live, 'PART1', tmp_path / 'Part1')
    monkeypatch.setattr(unified_live, '_modules', lambda: (None, None))
    monkeypatch.setattr(unified_live, 'engines', lambda: {'engines': []})
    monkeypatch.setattr(backtest_jobs, 'active_jobs', lambda: [])
    monkeypatch.setattr(unified_live, 'status', lambda *_args, **_kwargs: {
        'modules': {'STAFF': {'name': 'STAFF', 'state': '연결 대기'},
                    'ENGINE': {'name': 'ENGINE', 'state': '연결 대기'}}, 'lines': []})

    # The actual settings loader/saver and metadata run, but their files are isolated.
    owner = runpy.run_path(str(ROOT / 'Part1/live_control.py'), run_name='strategy_http_fixture')
    context = owner['load_special_settings'].__globals__
    context['BASE_DIR'] = tmp_path / 'Part1'
    context['SPECIAL_SETTINGS_PATH'] = tmp_path / 'Part1/special_settings.json'
    context['_OZ_PROFILES'] = oz_profiles
    monkeypatch.setattr(unified_live, 'control', lambda: owner)
    monkeypatch.setattr(unified_backtest, 'ROOT', tmp_path)
    monkeypatch.setattr(unified_backtest, 'PART2', tmp_path / 'Part2')
    monkeypatch.setattr(unified_backtest, 'warehouse', lambda: tmp_path / 'warehouse')
    monkeypatch.setattr(ui_model, 'load', functools.partial(ui_model.load, path=tmp_path / 'Part2/backtest_ui.json'))

    recipe = copy.deepcopy(next(iter(registry.builtin_entries().values()))['recipe'])
    recipe['name'] = '실제 HTTP 테스트 전략'
    recipe['symbols'] = ['XAUUSD+']
    recipe['strategy_intent']['symbols'] = ['XAUUSD+']
    host = server.LabServer(port=0)
    worker = threading.Thread(target=host.serve_forever, daemon=True)
    worker.start()
    fixture = tmp_path / 'http_ui_fixture.json'
    fixture.write_text(json.dumps({'url': 'http://127.0.0.1:' + str(host.server_port),
                                  'token': host.token, 'recipe': recipe,
                                  'evidence': str(Path(os.environ.get('MOSES_EVIDENCE_ROOT') or ROOT / '검증결과') / 'strategy101/http_ui')}, ensure_ascii=False), encoding='utf-8')
    try:
        completed = subprocess.run([node, str(Path(__file__).with_suffix('.cjs')), playwright, str(fixture)],
                                   capture_output=True, text=True, encoding='utf-8', timeout=90)
        assert completed.returncode == 0, completed.stdout + completed.stderr
        source = list((tmp_path / 'Part3/TEST_SPECIAL').glob('*.py'))
        assert len(source) == 1, 'Only the final unpromoted AI-confirmed strategy remains in the fixture'
        assert all('실제 AI 확인 전략' in file.read_text('utf-8') for file in source)
    finally:
        host.shutdown()
        host.server_close()
        worker.join(timeout=5)
