"""SPECIAL LIVE <-> backtest parity on a synthetic trading day (same input).

LIVE path     : every second, every feed publishes its full 45-column payload to
                Part1 (what the MT5 EA sends over the Named Pipe LIVE).
BACKTEST path : the same seconds are written as the EA's STAFF_PIPE_V1 capture
                (FULL on new bar / ROW inside a bar) and run by
                ``part1_host.engine.run_capture_backtest`` - the BACKTEST CONTROL path.

Both paths execute the unmodified Part1 program (SPECIAL, manager_KIM, monitor_OZ,
TREND/FVG/SWEEP, THE STAFF OF MOSES) with the same SPECIAL trigger slot input.
The alert time and count must be identical and non-empty.

The synthetic numbers are not MT5 indicator values; they only exercise the paths.
The window starts 2026-09-22 09:00 KST (MAIN_ASIA). With SEED the 15m strategy trend
(SMA20(open) and HMA50 slopes, strategy_INDICATOR) is DOWN/SHORT for SPECIAL7 and the
shaped 1m double top completes a SHORT OZ about three minutes in. (Seed 7 was used before
수정본5; under the new trend rule its 15m trend is NEUTRAL, so SPECIAL7 would stay silent.)
PART1_HOST_PARITY_FULL=1 runs a 12-minute window instead of 4 minutes.
"""
from __future__ import annotations

import ast
import datetime as dt
import json
import logging
import os
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from part1_host import capture, engine, runtime
from part1_host.synthetic import SyntheticMarket, TIMEFRAMES

SYMBOL = 'XAUUSD+'
START = int(dt.datetime(2026, 9, 22, 0, 0, tzinfo=dt.timezone.utc).timestamp())   # 09:00 KST, MAIN_ASIA
PATTERN = ((0, 0), (180, -0.5), (360, 3.0), (420, 3.2), (540, 1.0), (600, 0.8), (720, 3.1), (780, 2.5), (900, 1.0))
WINDOW = 720 if os.environ.get('PART1_HOST_PARITY_FULL') == '1' else 240
SEED = 0
SPECIALS = ['SPECIAL7']
# Same trigger slot input for both paths (OZ_SYSTEM CONTROL [전략 설정] style text).
TRIGGERS = {'SPECIAL7': '무지성 올존'}


@pytest.fixture(scope='module')
def market():
    return SyntheticMarket(SYMBOL, START, START + WINDOW, history_days=30, seed=SEED, pattern=PATTERN)


@pytest.fixture(scope='module')
def export(market, tmp_path_factory):
    root = tmp_path_factory.mktemp('ea-capture')
    writer = capture.CaptureWriter(root, SYMBOL, TIMEFRAMES)
    for second in range(START, START + WINDOW):
        for index, tf in enumerate(TIMEFRAMES):
            times, volumes, values = market.payload(tf, second)
            writer.write(index, second, times, volumes, values)
    writer.close()
    return root


def run_live_path(market, start, end):
    rt = runtime.Part1Runtime(symbols=[SYMBOL], start_epoch=start - 1, specials=SPECIALS,
                              trigger_overrides=TRIGGERS)
    try:
        logging.disable(logging.CRITICAL)
        rt.start_services()
        for second in range(start, end):
            rt.clock.set(second)
            for tf in TIMEFRAMES:
                rt.publish(capture.pack_wire(SYMBOL, tf, *market.payload(tf, second)))
            rt.run_until(second + 0.999)
        return [a.to_json() for a in rt.special_alerts()], rt.metadata()
    finally:
        logging.disable(logging.NOTSET)
        rt.close()


def test_capture_rebuilds_the_live_payload_bytes(market, export):
    """Every rebuilt STAFF pipe message equals the LIVE payload of that second."""
    feed = engine.SecondFeed(export, SYMBOL)
    checked = 0
    for second in range(START, START + WINDOW):
        feed.advance_to(second)
        for (tf, wire), expected_tf in zip(feed.wires(), TIMEFRAMES):
            assert tf == expected_tf
            live = capture.pack_wire(SYMBOL, tf, *market.payload(tf, second), snapshot=0)
            rebuilt = wire[:8] + (0).to_bytes(8, 'little') + wire[16:]
            assert rebuilt == live, (second, tf)
            checked += 1
    assert checked == WINDOW * len(TIMEFRAMES)


def test_host_runs_part1_sources_unmodified():
    part1 = ROOT.parent / 'Part1' / 'program'
    with runtime.Part1Runtime(symbols=[SYMBOL], start_epoch=START, specials=SPECIALS,
                              trigger_overrides=TRIGGERS) as rt:
        for rel, sha in rt.source_hashes.items():
            path = ROOT.parent / rel
            assert runtime._sha(path) == sha
        assert rt.loaded_specials == SPECIALS
        # The SPECIAL trigger slot reached Part1 through the official route (oz_profiles).
        spec = rt.manager.official_specs['PIPELINE_7']
        assert (spec.validation_mode, spec.trigger_mode) == ('BLIND', 'OZ')
        assert '무지성 올존' in spec.alert_template
    assert (part1 / 'SPECIAL' / 'SPECIAL7.py').is_file()


def _wait_literals(path: Path, function: str) -> set:
    tree = ast.parse(path.read_text(encoding='utf-8-sig'))
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef,)) and node.name == function:
            for call in ast.walk(node):
                if (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
                        and call.func.attr == 'wait' and call.args):
                    arg = call.args[0]
                    if isinstance(arg, ast.Constant):
                        found.add(float(arg.value))
                    elif isinstance(arg, ast.IfExp):
                        for part in (arg.body, arg.orelse):
                            if isinstance(part, ast.Constant):
                                found.add(float(part.value))
                            elif isinstance(part, ast.Name):
                                found.add(part.id)
    return found


def test_service_periods_are_the_part1_loop_literals():
    program = ROOT.parent / 'Part1' / 'program'
    periods = runtime.SERVICE_PERIODS
    kim = ast.parse((program / 'manager_KIM.py').read_text(encoding='utf-8-sig'))
    maint = next(n for n in ast.walk(kim) if isinstance(n, ast.ClassDef) and n.name == 'ComposerMaintenance')
    waits = [c.args[0].value for c in ast.walk(maint) if isinstance(c, ast.Call)
             and isinstance(c.func, ast.Attribute) and c.func.attr == 'wait']
    assert waits == [periods['manager_maintenance']]
    for name in ('strategy_INDICATOR.py', 'strategy_FVG.py'):
        assert _wait_literals(program / name, 'main') == {'LOOP_SLEEP_SEC', periods['engine_idle']}
        assert _wait_literals(program / name, 'run') == {periods['command_worker']}
    assert _wait_literals(program / 'strategy_SWEEP.py', 'main') == {'LOOP_SLEEP_SEC', periods['engine_idle']}
    for name in ('strategy_INDICATOR.py', 'strategy_FVG.py', 'strategy_SWEEP.py'):
        tree = ast.parse((program / name).read_text(encoding='utf-8-sig'))
        value = next(n.value.value for n in tree.body if isinstance(n, ast.Assign)
                     and any(isinstance(t, ast.Name) and t.id == 'LOOP_SLEEP_SEC' for t in n.targets))
        assert value == periods['engine_active']


def test_live_and_backtest_alerts_identical(market, export):
    live, meta = run_live_path(market, START, START + WINDOW)
    result = engine.run_capture_backtest(export, start_s=START, end_s=START + WINDOW, symbol=SYMBOL,
                                         specials=SPECIALS, trigger_overrides=TRIGGERS,
                                         log_level=logging.CRITICAL)
    backtest = result['alerts']
    report = engine.compare_alerts(live, backtest)
    out = ROOT / 'generic_runs' / 'part1_host_parity'
    out.mkdir(parents=True, exist_ok=True)
    (out / f'parity_{WINDOW}s.json').write_text(json.dumps(
        {'window_s': WINDOW, 'start': START, 'specials': SPECIALS, 'triggers': TRIGGERS,
         'report': report, 'live': live, 'backtest': backtest}, ensure_ascii=False, indent=2), encoding='utf-8')
    assert report['live_count'] > 0, 'synthetic day must produce at least one SPECIAL alert'
    assert report['match'], report
    assert all(a['strategy'] == 'SPECIAL7' and a['delivered'] for a in backtest)
    # The slot text reached the LIVE SPECIAL7 alert template.
    assert any('무지성 올존' in m['text'] for m in result['telegram'])
