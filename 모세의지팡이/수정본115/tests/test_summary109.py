"""Actual Edge checks compact summaries and the unchanged canonical editor draft."""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import shutil
import subprocess

from Part3.lab.ai.research_editor import contract as editor_contract, schema_errors
from Part3.lab.ai.schema import output_schema


def _fixture(root: Path):
    schema = output_schema()
    meaning_schema = next(row for row in schema['properties']['interpretation']['anyOf'] if row.get('type') == 'object')
    symbols = meaning_schema['properties']['symbols']['items']['enum']
    ordinary = {'supported': True, 'intent': 'CREATE_STRATEGY', 'needs_clarification': False,
        'clarification_question': None, 'message_ko': '해석한 조건을 확인해 주세요.', 'interpretation': {
            'direction': 'LONG', 'symbols': [symbols[0]], 'order_mode': 'SEQUENTIAL', 'global_combine': 'ALL',
            'within_sec': 120, 'final_window_sec': 900, 'persistent': False,
            'steps': [
                {'kind': 'MA_CROSS', 'tfs': ['15m'], 'ma_left': 'EMA50', 'ma_right': 'WMA200',
                    'direction': 'LONG', 'bar_state': 'CLOSED', 'capture': 'cross'},
                {'kind': 'FVG_NEW', 'tfs': ['5m'], 'side': 'BULL', 'bar_state': 'FORMING', 'capture': 'gap'}],
            'final': {'kind': 'OZ', 'tfs': ['1m'], 'validation_mode': 'NORMAL', 'trigger_mode': 'BREAKER',
                'direction': 'LONG', 'scope_ref': 'gap', 'tf_combine': 'ALL'}}}
    advanced = copy.deepcopy(ordinary)
    advanced['interpretation'].update({
        'steps': [*advanced['interpretation']['steps'],
            {'kind': 'FVG_TOUCH', 'tfs': ['1m'], 'ref': 'gap', 'scope_ref': 'cross', 'direction': 'SAME_AS_PREVIOUS_DIRECTION'}],
        'after_conditions': [{'kind': 'PRICE_LEVEL', 'tfs': ['5m'], 'level': 'PDH', 'relation': 'ABOVE'}],
        'final_conditions': [{'kind': 'CANDLE_STATE', 'tfs': ['1h'], 'side': 'BULL', 'bar_state': 'CLOSED'}],
        'cancel_conditions': [{'kind': 'TREND', 'tfs': ['30m'], 'direction': 'SHORT'}],
        'branches': [
            {'steps': [{'kind': 'BAR_CLOSE', 'tfs': ['30m'], 'bar_state': 'CLOSED'}], 'within_sec': 60,
                'final': {'kind': 'NOTIFY', 'direction': 'LONG'}},
            {'steps': [{'kind': 'CANDLE_STATE', 'tfs': ['1h'], 'side': 'BEAR', 'bar_state': 'CLOSED'}]}],
        'lifecycle': {'expires': {'bars': 5, 'tf': '30m'}, 'snapshots': {
            'entry': {'tf': '15m', 'field': 'close', 'bar_state': 'CLOSED'}, 'atr': {'tf': '15m', 'field': 'ATR14'}},
            'excursion': {'tf': '15m', 'anchor': 'entry', 'snapshot': 'atr', 'multiplier': 2, 'direction': 'ADVERSE'},
            'replace': {'scope': 'SYMBOL_DIRECTION'}, 'first_success': True, 'invalidate_refs': True,
            'restart_on': [{'kind': 'BAR_CLOSE', 'tfs': ['5m'], 'bar_state': 'CLOSED'}]},
        'time_filters': ['MAIN_ASIA'], 'final_time_filters': {'MAIN_LONDON': {'enabled': True, 'start': '08:00', 'end': '12:00'}}})
    ambiguous = copy.deepcopy(ordinary)
    ambiguous.update(needs_clarification=True, clarification_question='매수 조건으로 확정할까요?')
    contract = editor_contract({'options': {'symbols': symbols}, 'today': '2026-10-05'}, advanced)
    for strategy in (ordinary, advanced, ambiguous):
        assert not schema_errors(strategy, contract['schema'], ['strategy'])

    def command(**changes):
        request = {'target_mode': 'SPECIAL', 'specials': ['SPECIAL1'], 'filename': None, 'watch_text': None,
            'symbol': symbols[0], 'start': '2026-01-01', 'end': '2026-02-01', 'mode': 'BAR',
            'result_mode': 'VIRTUAL_ENTRY', 'spread_points': 3, 'available_only': False,
            'build_only': False, 'rebuild': False, **changes}
        return {'draft': False, 'command': {'supported': True, 'action': 'START', 'request': request,
            'job_id': None, 'needs_clarification': False, 'clarification_question': None, 'message_ko': '기존 실행 대상'}}

    plan = {'strategy_text': None, 'steps': [command(), command(target_mode='WATCH', specials=[],
        watch_text='골드 기존 감시 명령', symbol=symbols[1], mode='TICK', available_only=True)],
        'needs_clarification': False, 'clarification_question': None}
    generated_plan = copy.deepcopy(plan)
    generated_plan['steps'][0]['draft'] = True
    generated_plan['steps'][0]['command']['request'].update(target_mode='GENERATED', specials=[], filename=None)
    assert not schema_errors(plan, contract['plan_schema'], ['plan'])
    assert not schema_errors(generated_plan, contract['plan_schema'], ['plan'])
    fixture = {'contract': contract, 'symbols': symbols, 'ordinary': ordinary, 'advanced': advanced,
        'ambiguous': ambiguous, 'plan': plan, 'generated_plan': generated_plan}
    baseline = root.parent / '수정본108' / 'Part3' / 'web'
    if baseline.is_dir():
        fixture['baseline'] = {name: (baseline / name).read_text(encoding='utf-8')
            for name in ('ai_editor.js', 'ai_editor.css', 'ai_display.js')}
    return fixture


def test_summary_is_compact_and_all_detailed_edits_preserve_canonical_draft():
    root = Path(__file__).resolve().parents[1]
    runtime = Path.home() / '.cache/codex-runtimes/codex-primary-runtime/dependencies/node'
    node = os.environ.get('MOSES_TEST_NODE') or shutil.which('node') or str(runtime / 'bin/node.exe')
    playwright = os.environ.get('MOSES_TEST_PLAYWRIGHT') or str(runtime / 'node_modules/playwright')
    assert Path(node).is_file() and Path(playwright).is_dir(), 'UI 검증에 필요한 Node.js/Playwright가 없습니다.'
    result = subprocess.run([node, str(Path(__file__).with_suffix('.cjs')), playwright], cwd=root,
        input=json.dumps(_fixture(root), ensure_ascii=False), text=True, encoding='utf-8', capture_output=True, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(result.stdout)
    assert report['passed'] >= 15
    assert not report['errors'] and not report['network_calls']
