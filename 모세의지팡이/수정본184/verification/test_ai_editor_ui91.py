"""Slot edits preserve canonical meaning and show errors at the affected cell."""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess

from Part3.lab.ai.schema import output_schema
from Part3.lab.ai.research_backtest import plan_schema
from Part3.lab.ai.research_editor import contract as editor_contract

ROOT = Path(__file__).resolve().parents[1]


def test_editable_interpretation_dom_preserves_values_and_scopes():
    executable = shutil.which('node')
    assert executable, 'Part3 JS 검증에 필요한 Node.js를 찾을 수 없습니다.'
    schema = output_schema()
    meaning = next(item for item in schema['properties']['interpretation']['anyOf'] if item.get('type') == 'object')
    symbols = meaning['properties']['symbols']['items']['enum']
    fixture = {'schema': schema, 'plan_schema': plan_schema(), 'symbols': symbols,
        'contract': editor_contract({'options': {'symbols': symbols}, 'today': '2026-10-03'})}
    result = subprocess.run([executable, str(ROOT / 'verification' / 'ai_editor91_check.js'),
        str(ROOT / 'Part3' / 'web' / 'ai_editor.js')], input=json.dumps(fixture, ensure_ascii=False),
        text=True, encoding='utf-8', capture_output=True, timeout=30, cwd=ROOT)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'DOM regression PASS' in result.stdout
