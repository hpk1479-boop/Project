"""The common OZ Fact memo reuses a value only for the very same STAFF frame object
(moved from Part1/audit/test_oz_fvg_optimization.py in 수정본180).
"""
from pathlib import Path
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'Part1/program'))
import monitor_OZ


def market(seed, n=700):
    r = np.random.default_rng(seed)
    p = 100 + np.cumsum(r.normal(0, .4, n))
    o = p + r.normal(0, .2, n)
    c = p + r.normal(0, .2, n)
    h = np.maximum(o, c) + abs(r.normal(0, .5, n))
    l = np.minimum(o, c) - abs(r.normal(0, .5, n))
    return pd.DataFrame({"time": pd.date_range("2026-09-01", periods=n, freq="1min"),
                         "open": o, "high": h, "low": l, "close": c, "volume": r.integers(1, 50, n)})


def test_oz_fact_memo_is_keyed_by_the_same_frame_object():
    memo = monitor_OZ.OZFactMemo()
    a = market(1).iloc[:20].reset_index(drop=True)
    b = a.copy()
    calls = []

    def compute(df):
        calls.append(id(df))
        return float(df["close"].iloc[-1])

    assert memo.get(a, "x", lambda: compute(a)) == memo.get(a, "x", lambda: compute(a))
    assert len(calls) == 1
    memo.get(b, "x", lambda: compute(b))                # equal content, different object
    assert len(calls) == 2
    assert (memo.computed, memo.reused) == (2, 1)
