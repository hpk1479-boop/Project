"""The AI research editor names every generated file of a lanes request, in a real headless Edge (수정본184)."""
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from test_virtual_ai113 import plan, research_editor, value

ROOT = Path(__file__).resolve().parents[1]


def test_the_editor_names_every_file_of_a_lanes_request_and_keeps_its_lists():
    runtime = Path.home() / '.cache/codex-runtimes/codex-primary-runtime/dependencies/node'
    node = os.environ.get('MOSES_TEST_NODE') or shutil.which('node') or str(runtime / 'bin/node.exe')
    playwright = os.environ.get('MOSES_TEST_PLAYWRIGHT') or str(runtime / 'node_modules/playwright')
    if not Path(node).is_file() or not Path(playwright).is_dir():
        pytest.skip('Headless browser test needs Node.js and Playwright')
    snapshot = {'today': '2026-10-10', 'options': {'symbols': ['XAUUSD+'], 'specials': []}}
    contract = research_editor.contract(snapshot, None)

    def step(**request):
        command = value(None, 'GENERATED')
        command['request'].update(request, result_mode='ALERT_ONLY', triggers=['올존', '무지성 올존'])
        return plan(None) | {'steps': [{'draft': False, 'command': command}]}
    plans = {'files': step(filename=None, filenames=['Test_SPECIAL001.py', 'Test_SPECIAL002.py']),
             'file': step(filename='Test_SPECIAL001.py', filenames=None),
             'none': step(filename=None, filenames=[])}
    evidence = Path(os.environ.get('MOSES_EVIDENCE_ROOT') or ROOT / '검증결과') / 'revision184/ai'
    completed = subprocess.run([node, str(Path(__file__).with_suffix('.cjs')), playwright, str(evidence)],
                               input=json.dumps({'contract': contract, 'plans': plans}, ensure_ascii=False),
                               capture_output=True, text=True, encoding='utf-8', timeout=180)
    assert completed.returncode == 0, completed.stdout + completed.stderr
