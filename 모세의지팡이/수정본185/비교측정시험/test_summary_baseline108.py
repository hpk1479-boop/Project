"""The AI summary card against 수정본108's screen, run only on request (moved out of the regular tests in 수정본162).

MOSES_COMPARE_ROOT names the folder holding 수정본108. Without it the comparison is skipped.
"""
import importlib.util
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_summary_card_is_smaller_than_revision108():
    base = os.environ.get('MOSES_COMPARE_ROOT')
    if not base:
        pytest.skip('MOSES_COMPARE_ROOT를 지정하면 수정본108 화면과 비교합니다.')
    web = Path(base) / '수정본108' / 'Part3' / 'web'
    if not web.is_dir():
        pytest.skip('수정본108 화면 파일이 없습니다.')
    spec = importlib.util.spec_from_file_location('summary109_fixture', ROOT / 'tests' / 'test_summary109.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    fixture = module._fixture(ROOT)
    fixture['baseline'] = {name: (web / name).read_text(encoding='utf-8')
                           for name in ('ai_editor.js', 'ai_editor.css', 'ai_display.js')}
    runtime = Path.home() / '.cache/codex-runtimes/codex-primary-runtime/dependencies/node'
    node = os.environ.get('MOSES_TEST_NODE') or shutil.which('node') or str(runtime / 'bin/node.exe')
    playwright = os.environ.get('MOSES_TEST_PLAYWRIGHT') or str(runtime / 'node_modules/playwright')
    result = subprocess.run([node, str(ROOT / 'tests' / 'test_summary109.cjs'), playwright], cwd=ROOT,
                            input=json.dumps(fixture, ensure_ascii=False), text=True, encoding='utf-8',
                            capture_output=True, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(result.stdout)
    assert 'previous' in report['measurements'], 'the 수정본108 comparison did not run'
