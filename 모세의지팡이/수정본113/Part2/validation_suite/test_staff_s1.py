"""S1 must move ownership, not formulas or the frozen S0 expectations."""
from __future__ import annotations
import ast
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

PART2 = Path(__file__).resolve().parents[1]
ROOT = PART2.parent
sys.path.insert(0, str(PART2))
from part1_host.runtime import Part1Runtime
from staff_golden.contracts import Recorder, read_frame

PROGRAM = ROOT / 'Part1/program'
FROZEN = ROOT / '검증결과/staff_s0/baseline_input/Part1/program'
MOVES = json.loads((ROOT / '검증결과/staff_s1/extraction.json').read_text('utf-8'))


def nodes(path):
    return {n.name: n for n in ast.parse(path.read_bytes()).body
            if isinstance(n, (ast.FunctionDef, ast.ClassDef))}


@pytest.mark.parametrize('move', MOVES, ids=lambda m: m['function'])
def test_moved_function_ast_is_exact_s0(move):
    name = move['function']
    assert name not in nodes(PROGRAM / move['from'])
    assert ast.dump(nodes(FROZEN / move['from'])[name]) == ast.dump(nodes(PROGRAM / move['to'])[name])


def test_fvg_atr_body_is_unchanged_and_separate():
    before = nodes(FROZEN / 'strategy_FVG.py')['_wilder_atr']
    after = nodes(PROGRAM / 'strategy_FVG.py')['FVG_WILDER_ATR']
    after.name = before.name
    assert ast.dump(before) == ast.dump(after)
    for filename in ('monitor_OZ.py', 'manager_KIM.py', *('SPECIAL/SPECIAL%d.py' % n for n in range(1, 8))):
        assert not {'FVG_WILDER_ATR', '_wilder_atr'}.intersection(
            n.id for n in ast.walk(ast.parse((PROGRAM / filename).read_bytes())) if isinstance(n, ast.Name))
    assert 'ATR14_GENERAL' not in (PROGRAM / 'strategy_FVG.py').read_text('utf-8')


def test_owners_never_import_staff_or_other_parts():
    for filename in {m['to'] for m in MOVES} | {'watch_ma.py', 'watch_ma_features.py'}:
        for node in ast.walk(ast.parse((PROGRAM / filename).read_bytes())):
            names = ([a.name for a in node.names] if isinstance(node, ast.Import) else
                     [node.module or ''] if isinstance(node, ast.ImportFrom) else [])
            assert not any(('staff' in n.lower() and n not in {'staff_snapshot', 'staff_compat'}) or n.lower().split('.')[0] in ('part2', 'part3') for n in names)


def test_wonbi_and_watch_history_are_unchanged():
    for name in ('WonbiState', 'add_wonbi_features', 'apply_requested_features'):
        assert ast.dump(nodes(FROZEN / 'THE STAFF OF MOSES.py')[name]) == ast.dump(nodes(PROGRAM / ('THE STAFF OF MOSES.py' if name == 'WonbiState' else 'staff_compat.py'))[name])
    for name in ('watch_ma.py', 'watch_ma_features.py', 'manager_KIM.py', 'MT5/THE_STAFF_OF_MOSES.mq5'):
        if name == 'manager_KIM.py':
            before, after = (ast.parse((base / name).read_bytes()) for base in (FROZEN, PROGRAM))
            for tree in (before, after):
                tree.body = [n for n in tree.body if getattr(n, 'name', None) != 'StaffClientV2']
            assert ast.dump(before) == ast.dump(after)
        else:
            assert (FROZEN / name).read_bytes() == (PROGRAM / name).read_bytes()
    for part in ('Part2',):
        before = ROOT / '검증결과/staff_s0/baseline_input' / part / 'calculations/common.py'
        after = ROOT / part / 'calculations/common.py'
        assert ast.dump(nodes(before)['add_wonbi_features']) == ast.dump(nodes(after)['add_wonbi_features'])


def test_staff_binds_owner_objects_and_atr_fact_matches_frozen_values():
    with Part1Runtime(symbols=['XAUUSD+'], specials=['SPECIAL7'], start_epoch=1790035200) as rt:
        staff = rt.modules['staff_compat']
        assert not hasattr(rt.modules['the_staff_of_moses'], 'apply_requested_features')
        for move in MOVES:
            assert getattr(staff, move['function']) is getattr(rt.modules[Path(move['to']).stem], move['function'])
        facts, fvg = rt.modules['indicator_facts'], rt.modules['strategy_FVG']
        assert 'ATR14_GENERAL' in facts.FACTS
        assert 'FVG_WILDER_ATR' not in facts.FACTS
        assert fvg._wilder_atr is fvg.FVG_WILDER_ATR
        for source in ('golden_run1', 'actual_run1'):
            path = ROOT / '검증결과/staff_s0' / source / 'golden.sqlite'
            with sqlite3.connect(path.as_uri() + '?mode=ro', uri=True) as db:
                frame = read_frame(db, db.execute('SELECT data FROM frames LIMIT 1').fetchone()[0])
            got = facts.standalone_frame(frame, '1m').get('ATR14_GENERAL')
            pd.testing.assert_series_equal(got.rename('atr_14'), frame['atr_14'], check_exact=True)
        frame = pd.DataFrame({'high': np.arange(60.) ** 1.2 + 5,
                              'low': np.arange(60.) ** 1.1,
                              'close': np.arange(60.) ** 1.15 + 1})
        general = facts.standalone_frame(frame, '1m').get('ATR14_GENERAL')
        internal = fvg.FVG_WILDER_ATR(frame.high, frame.low, frame.close, 14)
        assert np.any(general.dropna().to_numpy() != internal.dropna().to_numpy())


@pytest.mark.parametrize('part', ['Part2'])
def test_common_reexports_owner_functions_in_standalone_process(part):
    script = ('from calculations import common; from generic_backtest.live_source import load_live_program_module as load; '
              'assert common.add_atr14_feature is load("indicator_facts").add_atr14_feature; '
              'assert common.add_price_band_state_features is load("monitor_OZ").add_price_band_state_features')
    run = subprocess.run([sys.executable, '-B', '-c', script], cwd=ROOT / part, capture_output=True, text=True)
    assert run.returncode == 0, run.stderr


def test_recorder_refuses_s0_destination():
    with pytest.raises(PermissionError, match='frozen'):
        Recorder(ROOT / '검증결과/staff_s0/never_create.sqlite')
