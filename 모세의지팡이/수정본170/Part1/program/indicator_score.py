# -*- coding: utf-8 -*-
"""Full-indicator trend score (former TREND 2.0 ensemble) constants, on-demand only.

The score itself is computed by indicator_array_score.score_from_facts.
This score is NOT part of the regular LIVE/SPECIAL evaluation any more. It is
computed only when a Watch explicitly asks for it, e.g. "골드 15분 추세점수 몇 점?"
or a condition on 추세점수/롱점수/숏점수. Conditions, weights, denominator and
threshold are unchanged from strategy_TREND.py; the difference is that LONG and
SHORT now read the same memoized Facts instead of computing every indicator twice.
"""
from __future__ import annotations

# 기존 DOUBLEB 앙상블에서 FVG 보너스를 제외한 순수 추세 조건의 총 가중치 = 30점.
TREND_DENOMINATOR = 30.0
TREND_MAX_SCORE = 100.0
DEFAULT_TREND_THRESHOLD = 40.0

# Facts read by the score (prefetch/ordering documentation only).
SCORE_FACTS = (
    "o", "h", "l", "c", "v", "e10", "e50", "s20", "w17", "h50", "st", "sar", "plus", "minus", "adx",
    "lr", "rv", "cv", "macd", "sig", "aup", "adn", "vip", "vim", "vw", "bop", "bullp", "bearp",
    "cmfv", "mfiv", "vol_surge", "vol_state", "safe", "chop", "hv", "hvma", "mss",
    "ten", "kij", "sa", "sb", "cloud_top", "cloud_bot",
)
