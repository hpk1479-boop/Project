"""Optimization regression: INDICATOR split must not change SPECIAL/Watch results.

Same synthetic day, same STAFF input, same SPECIAL trigger slot, same Watches, two runs:

  BEFORE : the pre-split engine code (Part1/audit/fixtures/legacy_strategy_TREND.py):
           full indicator score on every TREND loop and every metric push, exactly
           as before. Only its trend/direction output is replaced by an independent
           reference of the new user-defined trend rule (SMA20(open) 1-bar slope and
           HMA50 2-bar slope agree), because that rule change is the only intended
           behavior change.
  AFTER  : Part1/program/strategy_INDICATOR.py (Fact cache, on-demand score).

Everything else (manager_KIM, SPECIAL7, monitor_OZ, Watches) is the unmodified Part1
program. SPECIAL alerts, every final alert, every Telegram text and every value
the engines sent to manager_KIM must be identical; the AFTER run must compute
far fewer indicator values.
"""
from __future__ import annotations

import datetime as dt
import importlib.util
import json
import logging
import math
import sys
import time
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from part1_host import capture, runtime
from part1_host.synthetic import SyntheticMarket, TIMEFRAMES

LEGACY = ROOT.parent / 'Part1' / 'audit' / 'fixtures' / 'legacy_strategy_TREND.py'
SYMBOL = 'XAUUSD+'
START = int(dt.datetime(2026, 9, 22, 0, 0, tzinfo=dt.timezone.utc).timestamp())   # 09:00 KST
PATTERN = ((0, 0), (180, -0.5), (360, 3.0), (420, 3.2), (540, 1.0), (600, 0.8), (720, 3.1), (780, 2.5), (900, 1.0))
WINDOW = 240
SEED = 0          # 15m strategy trend DOWN -> SPECIAL7 SHORT (see test_part1_host_parity)
SPECIALS = ['SPECIAL7']
TRIGGERS = {'SPECIAL7': '무지성 올존'}
WATCHES = (
    ('PART2_BACKTEST_OFFICIAL', '골드 15분 추세점수 0 이상일때 1분 매도 무지성 올존 알려줘'),   # score on demand + TREND
    ('777001', '골드 15분 ADX 0 이상일때 1분 매도 무지성 올존 알려줘'),                        # metric Fact
    ('777002', '골드 15분 하락추세일때 1분 매도 무지성 올존 알려줘'),                          # TREND Fact
)
QUERIES = ((START + 30, '골드 15분 추세 알려줘'), (START + 60, '골드 15분 추세점수 몇 점?'),
           (START + 90, '골드 1분 추세점수 알려줘'))


def reference_trend(df):
    """Independent reference of the user-defined trend rule (not the production code)."""
    o = pd.to_numeric(df['open'], errors='coerce')
    s20 = o.rolling(20).mean()
    h50 = pd.to_numeric(df['hma_50'], errors='coerce')
    a = float(s20.iloc[-1] - s20.iloc[-2])
    b = float(h50.iloc[-1] - h50.iloc[-3])
    if not (math.isfinite(a) and math.isfinite(b)):
        return None
    if a > 0 and b > 0:
        return 'UP', 'LONG', a, b
    if a < 0 and b < 0:
        return 'DOWN', 'SHORT', a, b
    return 'NEUTRAL', 'NEUTRAL', a, b


def legacy_engine(rt):
    """BEFORE engine: the pre-split class bound to the runtime's clients/clock."""
    spec = importlib.util.spec_from_file_location('legacy_strategy_TREND_regression', LEGACY)
    legacy = importlib.util.module_from_spec(spec)
    saved = sys.modules.get('zmq')
    sys.modules['zmq'] = rt.bus.module()
    sys.path.insert(0, str(rt.program))
    try:
        spec.loader.exec_module(legacy)
    finally:
        sys.path.remove(str(rt.program))
        if saved is None:
            sys.modules.pop('zmq', None)
        else:
            sys.modules['zmq'] = saved
    rt._inject_clock(legacy)
    counter = {'full_score_evaluations': 0}

    class Before(legacy.TrendEngine):
        def evaluate(self, symbol, df, tf):
            # Pre-split cost: every TREND loop (and every score metric) ran the full score.
            full = legacy.TrendEngine.evaluate(self, symbol, df, tf)
            counter['full_score_evaluations'] += 1
            if symbol == '_metric_' or full is None:
                return full
            decision = reference_trend(df)
            if decision is None:
                return None
            full.update(trend=decision[0], direction=decision[1],
                        trend_basis='SMA20(open) slope vs 1 bar ago + HMA50 slope vs 2 bars ago',
                        sma20_slope=decision[2], hma50_slope=decision[3])
            return full

        def metric_snapshot(self, df, tf, requested_fields):
            counter['metric_snapshots'] = counter.get('metric_snapshots', 0) + 1
            return legacy.TrendEngine.metric_snapshot(self, df, tf, requested_fields)

    new = rt.engines['TREND'].engine
    before = Before.__new__(Before)
    for name in ('staff', 'manager', 'threshold', '_last_watch_state', '_pending_states', '_last_metric_push'):
        setattr(before, name, getattr(new, name))
    return before, counter


def run(market, *, legacy: bool):
    rt = runtime.Part1Runtime(symbols=[SYMBOL], start_epoch=START - 1, specials=SPECIALS,
                              trigger_overrides=TRIGGERS, log_level=logging.CRITICAL)
    try:
        logging.disable(logging.CRITICAL)
        counter = None
        if legacy:
            rt.engines['TREND'].engine, counter = legacy_engine(rt)
        for chat, text in WATCHES:
            rt.manager.handle_command(text, chat)
        rt.start_services()
        queries = dict(QUERIES)
        wall = time.perf_counter()
        for second in range(START, START + WINDOW):
            rt.clock.set(second)
            for tf in TIMEFRAMES:
                rt.publish(capture.pack_wire(SYMBOL, tf, *market.payload(tf, second)))
            if second in queries:
                rt.manager.handle_command(queries[second], 'PART2_BACKTEST_OFFICIAL')
            rt.run_until(second + 0.999)
        wall = time.perf_counter() - wall
        engine = rt.engines['TREND'].engine
        stats = getattr(getattr(engine, 'facts', None), 'stats', None)
        return {
            'special': [a.to_json() for a in rt.special_alerts()],
            'finals': [a.to_json() for a in rt.alerts],
            'telegram': [(d['virtual_time'], str(d['data'].get('chat_id')), str(d['data'].get('text', '')))
                         for d in rt.http.deliveries],
            'trend_facts': {k: (v.get('trend'), v.get('direction'), v.get('sma20_slope'), v.get('hma50_slope'))
                            for k, v in rt.manager.trend_facts.items()},
            'metric_facts': {k: v.get('value') for k, v in rt.manager.trend_metric_facts.items()},
            'counter': counter, 'fact_stats': dict(stats.computed) if stats else None, 'wall': wall,
        }
    finally:
        logging.disable(logging.NOTSET)
        rt.close()


def _stable(alert):
    return {k: v for k, v in alert.items() if k != 'event_id'}


@pytest.fixture(scope='module')
def results():
    market = SyntheticMarket(SYMBOL, START, START + WINDOW, history_days=30, seed=SEED, pattern=PATTERN)
    return run(market, legacy=True), run(market, legacy=False)


def test_special_alerts_and_watch_results_identical_before_and_after(results):
    before, after = results
    out = ROOT / 'generic_runs' / 'indicator_regression'
    out.mkdir(parents=True, exist_ok=True)
    (out / 'result.json').write_text(json.dumps({
        'window_s': WINDOW, 'watches': WATCHES, 'queries': QUERIES,
        'before': {k: v for k, v in before.items() if k not in ('trend_facts', 'metric_facts')},
        'after': {k: v for k, v in after.items() if k not in ('trend_facts', 'metric_facts')},
    }, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
    assert [_stable(a) for a in before['special']] == [_stable(a) for a in after['special']]
    assert [_stable(a) for a in before['finals']] == [_stable(a) for a in after['finals']]
    assert before['telegram'] == after['telegram']
    assert before['trend_facts'] == after['trend_facts']
    assert before['metric_facts'] == after['metric_facts']
    # The comparison is not vacuous.
    assert after['special'], 'SPECIAL7 must alert on the synthetic day'
    texts = [t for _, _, t in after['telegram']]
    assert any('추세점수 ·' in t for t in texts), texts
    assert any('SMA20(시가) 기울기' in t for t in texts), texts
    assert any(k[2] == 'trend_score' for k in after['metric_facts'])
    assert any(k[2] == 'adx' for k in after['metric_facts'])


def test_after_computes_far_less(results):
    before, after = results
    stats = after['fact_stats']
    # BEFORE: the full score ran on every TREND loop (~2/s) plus every score metric push.
    assert before['counter']['full_score_evaluations'] >= WINDOW
    # AFTER: the full score only for explicit requests (score metric Watch / 추세점수 query),
    # never more often than BEFORE computed the metric itself.
    assert stats.get('vol_state', 0) <= before['counter'].get('metric_snapshots', 0) + 1
    # AFTER: trend Facts once per new bar, OPEN-based inputs reused inside the bar.
    assert stats['trend'] <= 2 * (WINDOW // 60 + 2)
    assert stats['s20'] == stats['trend']
    report = {'window_s': WINDOW,
              'before': {'wall_s': round(before['wall'], 1), **before['counter']},
              'after': {'wall_s': round(after['wall'], 1), 'fact_computations': stats,
                        'total_fact_computations': sum(stats.values())}}
    out = ROOT / 'generic_runs' / 'indicator_regression'
    out.mkdir(parents=True, exist_ok=True)
    (out / 'performance.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    assert after['wall'] < before['wall'], report
