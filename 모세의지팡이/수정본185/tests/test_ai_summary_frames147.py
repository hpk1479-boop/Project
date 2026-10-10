"""The AI research editor's core summary names a virtual entry's real frames, in a real headless Edge."""
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from test_virtual_ai113 import plan, research_editor, value

ROOT = Path(__file__).resolve().parents[1]


def test_ai_summary_names_the_recipes_frames_in_the_real_page():
    runtime = Path.home() / '.cache/codex-runtimes/codex-primary-runtime/dependencies/node'
    node = os.environ.get('MOSES_TEST_NODE') or shutil.which('node') or str(runtime / 'bin/node.exe')
    playwright = os.environ.get('MOSES_TEST_PLAYWRIGHT') or str(runtime / 'node_modules/playwright')
    if not Path(node).is_file() or not Path(playwright).is_dir():
        pytest.skip('Headless browser test needs Node.js and Playwright')
    from event_backtest.virtual_contract import immediate_virtual_entry
    from event_backtest.virtual_defaults import recipe_on_base, strategy_profile
    profiles = {name: strategy_profile(name) for name in ('SPECIAL1', 'SPECIAL9')}
    snapshot = {'today': '2026-10-07', 'options': {'symbols': ['XAUUSD+'], 'specials': list(profiles),
                                                   'virtual_profiles': profiles}}
    # The strategy being written in the research screen (its draft backtest has no file yet).
    contract = research_editor.contract(snapshot, {'interpretation': profiles['SPECIAL9']['strategy']})

    def step(policy, target='SPECIAL', **request):
        command = value(policy, target)
        command['request'].update(request)
        return plan(None) | {'steps': [{'draft': target == 'GENERATED' and not request.get('filename'),
                                        'command': command}]}
    plans = {'special9_on_2m': step(recipe_on_base(profiles['SPECIAL9'], '2m'), specials=['SPECIAL9']),
             'special1': step(profiles['SPECIAL1']['default'], specials=['SPECIAL1']),
             'draft': step(immediate_virtual_entry(), 'GENERATED', filename=None),
             'unknown': step(immediate_virtual_entry(), 'GENERATED', filename='Test_SPECIAL001.py')}
    evidence = Path(os.environ.get('MOSES_EVIDENCE_ROOT') or ROOT / '검증결과') / 'revision147/ai'
    completed = subprocess.run([node, str(Path(__file__).with_suffix('.cjs')), playwright, str(evidence)],
                               input=json.dumps({'contract': contract, 'plans': plans}, ensure_ascii=False),
                               capture_output=True, text=True, encoding='utf-8', timeout=180)
    assert completed.returncode == 0, completed.stdout + completed.stderr
