"""Edge: the base frame is the one frame a test chooses; every other frame field only shows its frame."""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'Part2'), str(ROOT / 'Part1/program')]


def test_frames_follow_the_base_frame_in_the_real_page():
    runtime = Path.home() / '.cache/codex-runtimes/codex-primary-runtime/dependencies/node'
    node = os.environ.get('MOSES_TEST_NODE') or shutil.which('node') or str(runtime / 'bin/node.exe')
    playwright = os.environ.get('MOSES_TEST_PLAYWRIGHT') or str(runtime / 'node_modules/playwright')
    if not Path(node).is_file() or not Path(playwright).is_dir():
        pytest.skip('Headless browser test needs Node.js and Playwright')
    from event_backtest.base_frames import LADDER
    from event_backtest.virtual_defaults import recipe_on_base, strategy_on_base, strategy_profile
    names = ('SPECIAL1', 'SPECIAL7', 'SPECIAL8', 'SPECIAL9')
    profiles = {name: strategy_profile(name) for name in names}
    expected = {name: {tf: {'policy': recipe_on_base(profiles[name], tf), 'strategy': strategy_on_base(profiles[name], tf)}
                       for tf in ('2m', '3m', '5m')} for name in ('SPECIAL8', 'SPECIAL9')}
    saved_strategy = strategy_on_base(profiles['SPECIAL8'], '2m')
    saved_strategy['steps'][1]['slow_period'] = 60
    engine = {'profiles': profiles, 'expected': expected,
              'saved': {'policy': recipe_on_base(profiles['SPECIAL8'], '2m'), 'target': 'SPECIAL8', 'strategy': saved_strategy},
              'ladder': {base: list(row) for base, row in LADDER.items()}}
    evidence = Path(os.environ.get('MOSES_EVIDENCE_ROOT') or ROOT / '검증결과') / 'revision146/virtual_ui'
    completed = subprocess.run([node, str(Path(__file__).with_suffix('.cjs')), playwright, str(evidence)],
                               input=json.dumps(engine, ensure_ascii=False), capture_output=True, text=True,
                               encoding='utf-8', timeout=180)
    assert completed.returncode == 0, completed.stdout + completed.stderr
