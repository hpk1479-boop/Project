"""Lock that backtest FVG/TREND/OZ calculations are the current LIVE source itself."""
from __future__ import annotations

import sys
from pathlib import Path

PART2 = Path(__file__).resolve().parents[1]
if str(PART2) not in sys.path:
    sys.path.insert(0, str(PART2))

from generic_backtest.live_source import load_live_program_module
from generic_backtest.watch.engines import fvg_math, trend_math
from calculations import allzone


def test_fvg_uses_live_function_objects():
    live = load_live_program_module("strategy_FVG")
    assert fvg_math.build_fvg_state is live.build_fvg_state
    assert fvg_math.add_fvg_local is live.add_fvg_local
    assert fvg_math._wilder_atr is live._wilder_atr


def test_trend_uses_live_function_objects():
    live = load_live_program_module("strategy_INDICATOR")
    facts = load_live_program_module("indicator_facts")
    engine = live.IndicatorEngine
    assert trend_math.TrendCalculator.evaluate_trend is engine.evaluate_trend
    assert trend_math.TrendCalculator.evaluate_score is engine.evaluate_score
    assert trend_math.TrendCalculator.score is engine.score
    assert trend_math.TrendCalculator.metric_snapshot is engine.metric_snapshot
    assert trend_math.TrendCalculator.fact_frame is engine.fact_frame
    assert trend_math.ema is facts.ema is live.ema
    assert trend_math.supertrend_dir is facts.supertrend_dir
    assert trend_math.FactStore is facts.FactStore


def test_allzone_uses_live_core_methods():
    live = load_live_program_module("monitor_OZ")
    assert allzone._OZRules._candidate_completion_decision is live.OZMonitor._candidate_completion_decision
    assert allzone._OZRules._process_out_in is live.OZMonitor._process_out_in.__wrapped__
    assert allzone._OZRules._maintain_base_candidate is live.OZMonitor._maintain_base_candidate.__wrapped__
    assert allzone._OZRules._process_hma_cross is live.OZMonitor._process_hma_cross.__wrapped__
    assert allzone.percentile_states is live.percentile_states
    assert not hasattr(allzone, "regime_filter")
    assert not hasattr(live, "regime_filter")
    assert not hasattr(allzone._OZRules, "use_regime")
    assert not hasattr(allzone._OZRules, "use_super")
