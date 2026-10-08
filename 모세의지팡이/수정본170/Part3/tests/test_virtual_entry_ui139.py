"""Edge: the virtual-entry panel shows each recipe's entry limits (environment, N bars); WATCH has none."""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'Part2'), str(ROOT / 'Part1/program')]


def test_entry_limits_in_the_real_page():
    runtime = Path.home() / '.cache/codex-runtimes/codex-primary-runtime/dependencies/node'
    node = os.environ.get('MOSES_TEST_NODE') or shutil.which('node') or str(runtime / 'bin/node.exe')
    playwright = os.environ.get('MOSES_TEST_PLAYWRIGHT') or str(runtime / 'node_modules/playwright')
    if not Path(node).is_file() or not Path(playwright).is_dir():
        pytest.skip('Headless browser test needs Node.js and Playwright')
    from event_backtest.virtual_defaults import strategy_profile
    engine = {'profiles': {name: strategy_profile(name) for name in ('SPECIAL1', 'SPECIAL8')}}
    evidence = Path(os.environ.get('MOSES_EVIDENCE_ROOT') or ROOT / '검증결과') / 'revision139/virtual_ui'
    completed = subprocess.run([node, str(Path(__file__).with_suffix('.cjs')), playwright, str(evidence)],
                               input=json.dumps(engine, ensure_ascii=False), capture_output=True, text=True,
                               encoding='utf-8', timeout=180)
    assert completed.returncode == 0, completed.stdout + completed.stderr
