"""ALLZONE/OZ rules sourced from the current Part1 LIVE monitor.

The historical runtime owns only replay/environment adapters.  All structural
OZ calculations and state-transition rules are the actual functions from
Part1/program/monitor_OZ.py, so LIVE and backtest cannot drift mathematically.
Operational checkpoint decorators are intentionally bypassed in historical
mode; their wrapped calculation bodies are used unchanged.
"""
from __future__ import annotations

from generic_backtest.live_source import load_live_program_module

_live = load_live_program_module("monitor_OZ")

PERCENTILES = _live.PERCENTILES
TF_MAP = _live.TF_MAP
finite_number = _live.finite_number
VALIDATION_MODES = _live.VALIDATION_MODES
TRIGGER_MODES = _live.TRIGGER_MODES
PROFILE_KEYS = _live.PROFILE_KEYS
_resolve_oz_modes = _live._resolve_oz_modes
EXTERNAL_ATR_COLUMN = _live.EXTERNAL_ATR_COLUMN

percentile_states = _live.percentile_states
same_side_out_reasons = _live.same_side_out_reasons
all_in = _live.all_in

IndicatorEpisode = _live.IndicatorEpisode
PercentileCandidate = _live.PercentileCandidate
Candidate = _live.Candidate
FinalTriggerDecision = _live.FinalTriggerDecision
CandidateCompletionDecision = _live.CandidateCompletionDecision
_observed_encode = _live._observed_encode
_observed_decode = _live._observed_decode

_CORE_METHODS = (
    "_row_time",
    "_update_episode_extreme",
    "_active_percentile_candidates",
    "_clear_percentile_candidates",
    "_sync_common_candidate",
    "_register_percentile_candidate",
    "_process_out_in",
    "_refresh_true_b0",
    "_apply_latest_hma_extreme",
    "_reconstruct_pre_cross_extreme",
    "_reset_direction_cycle",
    "_cancel_base_candidate",
    "_one_way_reason",
    "_check_base_invalidation",
    "_maintain_base_candidate",
    "_breaker_bo_break_trigger",
    "_process_hma_cross",
    "_bars_since_event",
    "_hma_slope",
    "_higher_tf_open_vs_hma6",
    "_hma_aligned",
    "_trigger_state",
    "_candle_pullback_trigger",
    "_hma6_turn_pullback_trigger",
    "_level_hma17",
    "_level_wonbi",
    "_final_trigger_decision",
    "_candidate_completion_decision",
    "_silent_consume_candidate",
    "_probe_silent_completion",
    # Profile modifiers and accumulated touch-trigger state (Part1 LIVE OZ profiles).
    "use_breaker",
    "_family_true_b0_time",
    "_current_trigger_hits",
    "_accumulate_trigger_hits",
    "_ordered_hit_keys",
    # Common OZ Fact helpers (Part1 수정본6). Without a Fact memo they compute directly.
    "_fact",
    "_live_row",
    "_live_states",
    "_bars_since",
    "_hma_cross_observation",
    "_compute_trigger_hits",
)

# LIVE checkpoint decorators are operational persistence, not calculation.
# Historical replay uses the exact wrapped calculation body without touching
# the LIVE checkpoint files/state.
_CHECKPOINT_WRAPPED = {
    "_process_out_in",
    "_maintain_base_candidate",
    "_process_hma_cross",
    "_silent_consume_candidate",
}


class _OZRules:
    """Transport-free shell populated with the exact LIVE OZ methods."""


for _name in _CORE_METHODS:
    _descriptor = vars(_live.OZMonitor)[_name]
    if _name in _CHECKPOINT_WRAPPED:
        _fn = getattr(_live.OZMonitor, _name)
        _wrapped = getattr(_fn, "__wrapped__", None)
        if _wrapped is None:
            raise RuntimeError(f"LIVE OZ method lost checkpoint wrapper contract: {_name}")
        setattr(_OZRules, _name, _wrapped)
    else:
        # vars() preserves @staticmethod descriptors exactly.
        setattr(_OZRules, _name, _descriptor)

# LIVE class constants used by the trigger-accumulation methods above.
for _name in ("TRIGGER_HIT_ORDER", "TRIGGER_MIN_OTHER_HITS"):
    setattr(_OZRules, _name, getattr(_live.OZMonitor, _name))

LIVE_OZ_SOURCE_FILE = str(_live.__file__)
