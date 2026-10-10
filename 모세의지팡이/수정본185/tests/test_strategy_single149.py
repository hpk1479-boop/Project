"""Actual Edge: Part2 chooses the latest checked strategy; LIVE remains a multiple selection."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'Part2'), str(ROOT / 'Part1/program')]


def test_backtest_single_selection_and_live_multiple_selection_in_the_real_page():
    runtime = Path.home() / '.cache/codex-runtimes/codex-primary-runtime/dependencies/node'
    node = os.environ.get('MOSES_TEST_NODE') or shutil.which('node') or str(runtime / 'bin/node.exe')
    playwright = os.environ.get('MOSES_TEST_PLAYWRIGHT') or str(runtime / 'node_modules/playwright')
    if not Path(node).is_file() or not Path(playwright).is_dir():
        pytest.skip('Headless browser test needs Node.js and Playwright')
    from event_backtest.base_frames import LADDER
    from event_backtest.virtual_defaults import strategy_profile
    profiles = {name: strategy_profile(name) for name in ('SPECIAL1', 'SPECIAL8', 'SPECIAL9')}
    fixtures = {'profiles': profiles, 'ladder': {base: list(row) for base, row in LADDER.items()}}
    evidence = Path(os.environ.get('MOSES_EVIDENCE_ROOT') or ROOT / '검증결과') / 'revision149/strategy_single'
    scratch = evidence / 'scratch'
    scratch.mkdir(parents=True, exist_ok=True)
    completed = subprocess.run([node, str(Path(__file__).with_suffix('.cjs')), playwright, str(evidence)],
        input=json.dumps(fixtures, ensure_ascii=False), capture_output=True, text=True, encoding='utf-8',
        timeout=60, env=dict(os.environ, TEMP=str(scratch.resolve()), TMP=str(scratch.resolve())))
    assert completed.returncode == 0, completed.stdout + completed.stderr
