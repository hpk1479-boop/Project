"""The AI editor shows a virtual-entry policy with real timeframes, in a real headless Edge."""
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from test_virtual_ai113 import plan, policy, research_editor, snapshot

ROOT = Path(__file__).resolve().parents[1]


def written_frames_policy():
    """policy() with its frames written out, as a recipe writes them; the editor shows any written frame as it is."""
    value = policy()
    value['tf'] = '5m'
    for row, tf in zip(value['conditions'][:3], ('15m', '5m', '1m')):
        row['tf'] = tf
    value['atr']['tf'] = value['stop']['tf'] = '15m'
    value['filters'][2]['tf'] = '5m'
    return value


def test_ai_editor_virtual_entry_summary_in_the_real_page():
    runtime = Path.home() / '.cache/codex-runtimes/codex-primary-runtime/dependencies/node'
    node = os.environ.get('MOSES_TEST_NODE') or shutil.which('node') or str(runtime / 'bin/node.exe')
    playwright = os.environ.get('MOSES_TEST_PLAYWRIGHT') or str(runtime / 'node_modules/playwright')
    if not Path(node).is_file() or not Path(playwright).is_dir():
        pytest.skip('Headless browser test needs Node.js and Playwright')
    fixture = {'contract': research_editor.contract(snapshot()), 'plan': plan(written_frames_policy()),
               'policy': written_frames_policy()}
    evidence = Path(os.environ.get('MOSES_EVIDENCE_ROOT') or ROOT / '검증결과') / 'revision137/ai'
    completed = subprocess.run([node, str(Path(__file__).with_suffix('.cjs')), playwright, str(evidence)],
                               input=json.dumps(fixture, ensure_ascii=False), capture_output=True, text=True,
                               encoding='utf-8', timeout=180)
    assert completed.returncode == 0, completed.stdout + completed.stderr
