"""수정본173: the strategy lab speaks the window language — within, recent and OPPOSITE — from the AI to the screens.

The four examples the user asked for are written once here and pass the model schema, the common contract and
the strategy file generator; the editor shows each window as one cell (none / a time / a count of bars).
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'Part3'), str(ROOT / 'Part1/program')]
from common_ai import security
from lab.ai import research_editor, research_presets, schema
from lab.ai.tools import ReadOnlyWorkspace

SYMBOL = 'XAUUSD+'
OZ_1M = {'kind': 'OZ_ALERT', 'tfs': ['1m'], 'validation_mode': 'BLIND', 'trigger_mode': 'OZ'}
OUT_IN_3M = {'kind': 'PERCENTILE_OUT_IN', 'tfs': ['3m'], 'families': ['RSI']}
OPPOSITE_OZ_1H = {'kind': 'OZ_ALERT', 'tfs': ['1h'], 'validation_mode': 'BLIND', 'trigger_mode': 'OZ',
                  'direction': 'OPPOSITE', 'recent': {'bars': 6, 'tf': '1h'}}
TOUCH_1H = {'kind': 'WONBI_TOUCH', 'tfs': ['1h']}
EXAMPLES = {
    '1m OZ, then a 3m out-in within 30 minutes': {
        'direction': 'LONG', 'symbols': [SYMBOL], 'order_mode': 'SEQUENTIAL', 'within': {'seconds': 1800},
        'steps': [OZ_1M, OUT_IN_3M], 'final': {'kind': 'NOTIFY'}},
    '1m OZ, then a 3m out-in within ten 1m bars': {
        'direction': 'LONG', 'symbols': [SYMBOL], 'order_mode': 'SEQUENTIAL', 'within': {'bars': 10, 'tf': '1m'},
        'steps': [OZ_1M, OUT_IN_3M], 'final': {'kind': 'NOTIFY'}},
    'Wonbi touches cancelled for six 1h bars after an opposite BLIND OZ': {
        'direction': 'BOTH', 'symbols': [SYMBOL], 'order_mode': 'SIMULTANEOUS', 'steps': [TOUCH_1H],
        'cancel_conditions': [OPPOSITE_OZ_1H], 'final': {'kind': 'NOTIFY'}},
    'Wonbi touches only without a recent opposite BLIND OZ': {
        'direction': 'BOTH', 'symbols': [SYMBOL], 'order_mode': 'SIMULTANEOUS',
        'steps': [TOUCH_1H, dict(OPPOSITE_OZ_1H, negated=True)], 'final': {'kind': 'NOTIFY'}},
}


def intent(meaning):
    return {'supported': True, 'intent': 'CREATE_STRATEGY', 'interpretation': copy.deepcopy(meaning),
            'needs_clarification': False, 'clarification_question': None, 'message_ko': '사건 뒤 기간 전략'}


@pytest.mark.parametrize('name', EXAMPLES)
def test_the_examples_pass_the_model_schema_the_contract_and_the_generator(name):
    Draft202012Validator(schema.output_schema()).validate(intent(EXAMPLES[name]))
    recipe = schema.recipe_from_intent(intent(EXAMPLES[name]))
    assert recipe['strategy_intent'] == schema.validate_intent(intent(EXAMPLES[name]))['interpretation']


def variants(output):
    return {item['properties']['kind']['const']: item for item in output['$defs']['step']['anyOf']}


def test_the_schema_offers_a_window_only_where_it_means_something():
    output = schema.output_schema()
    steps = variants(output)
    assert 'recent' in steps['OZ_ALERT']['properties'] and 'recent' in steps['PERCENTILE_OUT_IN']['properties']
    assert 'recent' not in steps['TREND']['properties'] and 'recent' not in steps['MA_STATE']['properties']
    assert 'OPPOSITE' in steps['OZ_ALERT']['properties']['direction']['enum']
    meaning = next(item for item in output['properties']['interpretation']['anyOf'] if item.get('type') == 'object')
    assert 'within' in meaning['properties']
    assert 'OPPOSITE' not in meaning['properties']['final']['properties']['direction']['enum']


@pytest.mark.parametrize('change', [
    lambda m: m.update(within={'seconds': 60, 'bars': 2, 'tf': '1m'}),
    lambda m: m.update(within={'bars': 0, 'tf': '1m'}),
    lambda m: m.update(within={'bars': 501, 'tf': '1m'}),
    lambda m: m.update(within={'bars': 3, 'tf': 'SOURCE'}),
    lambda m: m['steps'].append({'kind': 'TREND', 'tfs': ['1h'], 'recent': {'seconds': 60}}),
])
def test_the_model_schema_rejects_malformed_windows(change):
    meaning = copy.deepcopy(EXAMPLES['1m OZ, then a 3m out-in within ten 1m bars'])
    change(meaning)
    assert list(Draft202012Validator(schema.output_schema()).iter_errors(intent(meaning)))


def test_the_ai_vocabulary_reaches_the_model():
    clean = security.safe_tool_result('vocabulary', ReadOnlyWorkspace().vocabulary())
    assert 'within' in clean['sequential'] and 'within_sec' in clean['sequential']
    assert 'OZ engine completed' in clean['recent']
    assert 'OPPOSITE' in clean['step_directions']
    prompt = (ROOT / 'Part3/ai_context/MOSES_LANGUAGE.md').read_text(encoding='utf-8')
    assert 'within={"bars":10,"tf":"1m"}' in prompt and 'recent={"bars":6,"tf":"1h"}' in prompt
    assert 'direction=OPPOSITE' in prompt


def test_a_research_example_names_the_windows():
    text = research_presets.example(EXAMPLES['1m OZ, then a 3m out-in within ten 1m bars'], '시험')
    assert '사건 사이 기간: 봉 수: 10; 시간봉: 1분봉' in text
    text = research_presets.example(EXAMPLES['Wonbi touches cancelled for six 1h bars after an opposite BLIND OZ'], '시험')
    assert '최근 기간: 봉 수: 6; 시간봉: 1시간봉' in text and '방향: 반대 방향' in text


def test_the_editor_cells_and_their_wording():
    node = shutil.which('node')
    assert node, 'Part3 JS 검증에 필요한 Node.js를 찾을 수 없습니다.'
    output = schema.output_schema()
    meaning = next(item for item in output['properties']['interpretation']['anyOf'] if item.get('type') == 'object')
    symbols = meaning['properties']['symbols']['items']['enum']
    fixture = {'symbols': symbols, 'contract': research_editor.contract({'options': {'symbols': symbols}, 'today': '2026-10-09'})}
    result = subprocess.run([node, str(Path(__file__).with_name('event_window173_check.js')),
                             str(ROOT / 'Part3/web/ai_editor.js'), str(ROOT / 'Part3/web/ai_display.js')],
                            input=json.dumps(fixture, ensure_ascii=False), text=True, encoding='utf-8',
                            capture_output=True, timeout=60, cwd=ROOT)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'WINDOW PASS' in result.stdout
