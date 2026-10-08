"""161: other base frames tested together with the chosen one, in the real page (isolated Edge)."""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'Part2'), str(ROOT / 'Part1/program')]


def test_compared_base_frames_in_the_real_page():
    runtime = Path.home() / '.cache/codex-runtimes/codex-primary-runtime/dependencies/node'
    node = os.environ.get('MOSES_TEST_NODE') or shutil.which('node') or str(runtime / 'bin/node.exe')
    playwright = os.environ.get('MOSES_TEST_PLAYWRIGHT') or str(runtime / 'node_modules/playwright')
    if not Path(node).is_file() or not Path(playwright).is_dir():
        pytest.skip('Headless browser test needs Node.js and Playwright')
    from event_backtest.base_frames import LADDER
    from event_backtest.virtual_defaults import strategy_profile
    engine = {'profiles': {name: strategy_profile(name) for name in ('SPECIAL2', 'SPECIAL8', 'SPECIAL9')},
              'ladder': {base: list(row) for base, row in LADDER.items()}}
    evidence = Path(os.environ.get('MOSES_EVIDENCE_ROOT') or ROOT / '검증결과') / 'revision161/virtual_ui'
    completed = subprocess.run([node, str(Path(__file__).with_suffix('.cjs')), playwright, str(evidence)],
                               input=json.dumps(engine, ensure_ascii=False), capture_output=True, text=True,
                               encoding='utf-8', timeout=180)
    assert completed.returncode == 0, completed.stdout + completed.stderr
