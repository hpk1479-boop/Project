"""Edge: the Gemini key field behaves as the bot token field, step by step (수정본177)."""
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[2]


def test_gemini_key_field_is_the_bot_token_field():
    runtime = Path.home() / '.cache/codex-runtimes/codex-primary-runtime/dependencies/node'
    node = os.environ.get('MOSES_TEST_NODE') or shutil.which('node') or str(runtime / 'bin/node.exe')
    playwright = os.environ.get('MOSES_TEST_PLAYWRIGHT') or str(runtime / 'node_modules/playwright')
    if not Path(node).is_file() or not Path(playwright).is_dir():
        pytest.skip('Headless browser test needs Node.js and Playwright')
    evidence = Path(os.environ.get('MOSES_EVIDENCE_ROOT') or ROOT / '검증결과') / 'revision177/secret_fields'
    completed = subprocess.run([node, str(Path(__file__).with_suffix('.cjs')), playwright, str(evidence)],
                               capture_output=True, text=True, encoding='utf-8', timeout=120)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert json.loads(completed.stdout.strip().splitlines()[-1]) == {'passed': True, 'steps': 9}
