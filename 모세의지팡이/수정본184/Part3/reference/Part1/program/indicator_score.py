# -*- coding: utf-8 -*-
"""Full-indicator trend score (former TREND 2.0 ensemble), on-demand only.

This score is NOT part of the regular LIVE/SPECIAL evaluation any more. It is
computed only when a Watch explicitly asks for it, e.g. "골드 15분 추세점수 몇 점?"
or a condition on 추세점수/롱점수/숏점수. Conditions, weights, denominator and
threshold are unchanged from strategy_TREND.py; the difference is that LONG and
SHORT now read the same memoized Facts instead of computing every indicator twice.
"""
from __future__ import annotations

import pandas as pd

from indicator_facts import FactFrame, finite, normalize_tf

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


def score_from_facts(f: FactFrame, direction: str):
    """기존 DOUBLEB의 추세 앙상블 조건을 방향별 0~100점으로 계산합니다."""
    if f is None or len(f) < 60:
        return None
    direction = str(direction or "").upper()
    if direction not in {"LONG", "SHORT"}:
        return None

    c = f["c"]
    e10, e50 = f["e10"], f["e50"]
    s20 = f["s20"]
    w17 = f["w17"]
    h50 = f["h50"]
    st = f["st"]
    sar = f["sar"]
    plus, minus, adx = f["plus"], f["minus"], f["adx"]
    lr = f["lr"]
    rv = f["rv"]
    cv = f["cv"]
    macd = f["macd"]
    sig = f["sig"]
    aup = f["aup"]
    adn = f["adn"]
    vip, vim = f["vip"], f["vim"]
    vw = f["vw"]
    bop = f["bop"]
    bullp, bearp = f["bullp"], f["bearp"]
    cmfv = f["cmfv"]
    mfiv = f["mfiv"]
    vol_surge = f["vol_surge"]
    vol_state = f["vol_state"]
    safe = f["safe"]
    chop = f["chop"]
    hv = f["hv"]
    hvma = f["hvma"]
    mss = f["mss"]
    ten = f["ten"]
    kij = f["kij"]
    sa = f["sa"]
    sb = f["sb"]
    cloud_top = f["cloud_top"]
    cloud_bot = f["cloud_bot"]

    i = -1

    def b(x):
        return bool(x.iloc[i]) if hasattr(x, "iloc") and pd.notna(x.iloc[i]) else False

    if direction == "LONG":
        cond = [
            (e10.iloc[i] > e50.iloc[i], 1),
            (vol_state.iloc[i] == 1, 1),
            (plus.iloc[i] > minus.iloc[i] and adx.iloc[i] >= 25 and plus.iloc[i] > 15, 2),
            (h50.iloc[i] > h50.iloc[-3], 1),
            (rv.iloc[i] >= 50, 1),
            (cmfv.iloc[i] > 0, 1),
            (mfiv.iloc[i] >= 50, 1),
            (macd.iloc[i] > 0 and macd.iloc[i] > sig.iloc[i], 1),
            (s20.iloc[i] > s20.iloc[-2], 1),
            (c.iloc[i] > vw.iloc[i], 2),
            (w17.iloc[i] > s20.iloc[i], 1),
            (b(safe), 1),
            (ten.iloc[i] > kij.iloc[i] and sa.shift(13).iloc[i] > sb.shift(13).iloc[i] and c.iloc[i] > cloud_top.iloc[i], 1),
            (chop.iloc[i] < 45, 1),
            (cv.iloc[i] > 0, 1),
            (lr.iloc[i] > lr.iloc[-2], 1),
            (aup.iloc[i] > adn.iloc[i], 1),
            (vip.iloc[i] > vim.iloc[i], 1),
            (bop.iloc[i] > 0, 1),
            (bullp.iloc[i] > bullp.iloc[-2], 1),
            (mss.iloc[i] != -1, 2),
            (b(st), 2),
            (c.iloc[i] > sar.iloc[i], 1),
            (b(vol_surge), 2),
            (hv.iloc[i] > hvma.iloc[i], 1),
        ]
    else:
        cond = [
            (e10.iloc[i] < e50.iloc[i], 1),
            (vol_state.iloc[i] == -1, 1),
            (minus.iloc[i] > plus.iloc[i] and adx.iloc[i] >= 25 and minus.iloc[i] > 15, 2),
            (h50.iloc[i] < h50.iloc[-3], 1),
            (rv.iloc[i] <= 50, 1),
            (cmfv.iloc[i] < 0, 1),
            (mfiv.iloc[i] <= 50, 1),
            (macd.iloc[i] < 0 and macd.iloc[i] < sig.iloc[i], 1),
            (s20.iloc[i] < s20.iloc[-2], 1),
            (c.iloc[i] < vw.iloc[i], 2),
            (w17.iloc[i] < s20.iloc[i], 1),
            (b(safe), 1),
            (ten.iloc[i] < kij.iloc[i] and sa.shift(13).iloc[i] < sb.shift(13).iloc[i] and c.iloc[i] < cloud_bot.iloc[i], 1),
            (chop.iloc[i] < 45, 1),
            (cv.iloc[i] < 0, 1),
            (lr.iloc[i] < lr.iloc[-2], 1),
            (adn.iloc[i] > aup.iloc[i], 1),
            (vim.iloc[i] > vip.iloc[i], 1),
            (bop.iloc[i] < 0, 1),
            (bearp.iloc[i] < bearp.iloc[-2], 1),
            (mss.iloc[i] != 1, 2),
            (not b(st), 2),
            (c.iloc[i] < sar.iloc[i], 1),
            (b(vol_surge), 2),
            (hv.iloc[i] > hvma.iloc[i], 1),
        ]

    raw_score = sum(weight for ok, weight in cond if bool(ok))
    return min(TREND_MAX_SCORE, raw_score / TREND_DENOMINATOR * TREND_MAX_SCORE)


def evaluate_score(symbol: str, f: FactFrame, threshold: float = DEFAULT_TREND_THRESHOLD):
    """Full-score trend classification (the former TrendEngine.evaluate)."""
    if f is None or len(f) < 60:
        return None
    tf = normalize_tf(f.tf)
    if not tf:
        return None
    df = f.df
    live = df.iloc[-1]
    bar_time = pd.to_datetime(live.get("time"), errors="coerce")
    close = live.get("close")
    if pd.isna(bar_time) or not finite(close):
        return None

    long_score = score_from_facts(f, "LONG")
    short_score = score_from_facts(f, "SHORT")
    if long_score is None or short_score is None:
        return None

    threshold = float(threshold)
    if long_score >= threshold and long_score > short_score:
        trend, direction, score = "UP", "LONG", long_score
    elif short_score >= threshold and short_score > long_score:
        trend, direction, score = "DOWN", "SHORT", short_score
    else:
        trend, direction, score = "NEUTRAL", "NEUTRAL", max(long_score, short_score)

    return {
        "symbol": str(symbol),
        "source_tf": tf,
        "trend": trend,
        "direction": direction,
        "score": float(score),
        "long_score": float(long_score),
        "short_score": float(short_score),
        "threshold": threshold,
        "price": float(close),
        "bar_time": pd.Timestamp(bar_time),
    }
