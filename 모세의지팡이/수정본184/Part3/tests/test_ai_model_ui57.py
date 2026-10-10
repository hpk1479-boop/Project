"""The Ollama model field saves a string, keeps the model on a timeout-only save and refreshes the chat, in a real headless Edge.

수정본180: no Python test ran this browser script before.
"""
import os
import shutil
import subprocess
from pathlib import Path

import pytest


def test_ollama_model_settings_in_the_real_page():
    runtime = Path.home() / '.cache/codex-runtimes/codex-primary-runtime/dependencies/node'
    node = os.environ.get('MOSES_TEST_NODE') or shutil.which('node') or str(runtime / 'bin/node.exe')
    playwright = os.environ.get('MOSES_TEST_PLAYWRIGHT') or str(runtime / 'node_modules/playwright')
    if not Path(node).is_file() or not Path(playwright).is_dir():
        pytest.skip('Headless browser test needs Node.js and Playwright')
    completed = subprocess.run([node, str(Path(__file__).with_suffix('.cjs')), playwright],
                               capture_output=True, text=True, encoding='utf-8', timeout=180)
    assert completed.returncode == 0, completed.stdout + completed.stderr
