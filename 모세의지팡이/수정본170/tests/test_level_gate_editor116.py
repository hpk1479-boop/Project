"""Edge shows a strategy with a final level_gate (the shipped SPECIAL2) in the AI editor without a false error."""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'Part1/program'), str(ROOT / 'Part2'), str(ROOT / 'Part3')]


def _fixture():
    from Part3.lab.ai.research_editor import contract as editor_contract, schema_errors
    from Part3.lab.ai.schema import output_schema
    from strategy_recipe import registry
    schema = output_schema()
    meaning_schema = next(row for row in schema['properties']['interpretation']['anyOf'] if row.get('type') == 'object')
    symbols = meaning_schema['properties']['symbols']['items']['enum']
    meaning = copy.deepcopy(registry.builtin_entries()['SPECIAL2']['recipe']['strategy_intent'])
    meaning['symbols'] = [symbols[0]]
    strategy = {'supported': True, 'intent': 'CREATE_STRATEGY', 'needs_clarification': False,
                'clarification_question': None, 'message_ko': '해석한 조건을 확인해 주세요.', 'interpretation': meaning}
    contract = editor_contract({'options': {'symbols': symbols}, 'today': '2026-10-05'}, strategy)
    assert not schema_errors(strategy, contract['schema'], ['strategy'])
    gate = meaning['final']['level_gate']
    assert gate == {'ref': 'sweep', 'atr_period': 14, 'atr_mult': 1.5}
    return {'contract': contract, 'strategy': strategy, 'ref': gate['ref']}


def test_level_gate_is_editable_and_not_reported_as_unsupported():
    runtime = Path.home() / '.cache/codex-runtimes/codex-primary-runtime/dependencies/node'
    node = os.environ.get('MOSES_TEST_NODE') or shutil.which('node') or str(runtime / 'bin/node.exe')
    playwright = os.environ.get('MOSES_TEST_PLAYWRIGHT') or str(runtime / 'node_modules/playwright')
    if not Path(node).is_file() or not Path(playwright).is_dir():
        pytest.skip('Headless browser test needs Node.js and Playwright')
    result = subprocess.run([node, str(Path(__file__).with_suffix('.cjs')), playwright], cwd=ROOT,
                            input=json.dumps(_fixture(), ensure_ascii=False), text=True, encoding='utf-8',
                            capture_output=True, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(result.stdout.strip().splitlines()[-1])
    assert report['passed'] >= 6 and not report['errors'] and not report['network_calls']
