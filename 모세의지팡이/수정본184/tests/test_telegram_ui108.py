"""The real Telegram controls use mocked APIs and synthetic values only."""
import json
import os
from pathlib import Path
import shutil
import subprocess


def test_telegram_confirmation_feedback_and_settings_isolation():
    root = Path(__file__).resolve().parents[1]
    runtime = Path.home() / '.cache/codex-runtimes/codex-primary-runtime/dependencies/node'
    node = os.environ.get('MOSES_TEST_NODE') or shutil.which('node') or str(runtime / 'bin/node.exe')
    playwright = os.environ.get('MOSES_TEST_PLAYWRIGHT') or str(runtime / 'node_modules/playwright')
    assert Path(node).is_file() and Path(playwright).is_dir(), 'UI 검증에 필요한 Node.js/Playwright가 없습니다.'
    result = subprocess.run([node, str(Path(__file__).with_suffix('.cjs')), playwright],
        cwd=root, text=True, encoding='utf-8', capture_output=True, timeout=90)
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(result.stdout)
    assert report['passed'] == 12
    assert not report['errors']
