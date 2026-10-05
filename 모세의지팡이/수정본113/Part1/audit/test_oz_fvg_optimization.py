"""monitor_OZ / strategy_FVG optimization contracts (results unchanged).

* FVG: build_fvg_state equals the pre-optimization code (fixtures/legacy_strategy_FVG.py)
  over intra-bar ticks, new bars and closed-bar corrections; the structure (ATR,
  creation, fill, expiry) is rebuilt only when closed bars change, and the live touch
  is re-evaluated every call. _wilder_atr is identical including NaN/inf gaps.
* OZ: the common Fact memo returns the stored value only for the very same STAFF
  DataFrame object, is dropped with that object, and never shares mutable results.
  The full 16-profile before/after and LIVE/backtest parity runs in
  Part2/validation_suite/test_oz_fvg_optimization.py.
"""
from __future__ import annotations

import gc
import logging
import importlib.util
import shutil
import sys
import tempfile
import types
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from harness import FIXTURES, ROOT

PROGRAM = ROOT / "program"


_TMP = tempfile.TemporaryDirectory(prefix="oz-fvg-opt-")
_COPY = Path(_TMP.name) / "program"


def _program_copy() -> Path:
    """Private copy so import-time logging never touches Part1/program/logs."""
    if not _COPY.is_dir():
        _COPY.mkdir(parents=True)
        for path in PROGRAM.glob("*.py"):
            shutil.copyfile(path, _COPY / path.name)
        for path in FIXTURES.glob("legacy_*.py"):
            shutil.copyfile(path, _COPY / path.name)
    return _COPY


def _load(name, filename):
    folder = _program_copy()
    saved = {key: sys.modules.get(key) for key in ("zmq", name)}
    sys.modules["zmq"] = types.SimpleNamespace(Context=types.SimpleNamespace(instance=lambda: None),
                                               REQ=0, RCVTIMEO=0, SNDTIMEO=0, LINGER=0,
                                               Again=Exception, ZMQError=Exception)
    sys.path.insert(0, str(folder))
    try:
        spec = importlib.util.spec_from_file_location(name, folder / filename)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
        return mod
    finally:
        sys.path.remove(str(folder))
        for key, value in saved.items():
            if value is None:
                sys.modules.pop(key, None)
            else:
                sys.modules[key] = value


def market(seed, n=700):
    r = np.random.default_rng(seed)
    p = 100 + np.cumsum(r.normal(0, .4, n))
    o = p + r.normal(0, .2, n)
    c = p + r.normal(0, .2, n)
    h = np.maximum(o, c) + abs(r.normal(0, .5, n))
    l = np.minimum(o, c) - abs(r.normal(0, .5, n))
    return pd.DataFrame({"time": pd.date_range("2026-09-01", periods=n, freq="1min"),
                         "open": o, "high": h, "low": l, "close": c, "volume": r.integers(1, 50, n)})


class OZFVGOptimization(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.old_fvg = _load("legacy_strategy_FVG_audit", "legacy_strategy_FVG.py")
        cls.new_fvg = _load("strategy_FVG_audit", "strategy_FVG.py")
        cls.oz = _load("monitor_OZ_audit", "monitor_OZ.py")
        logging_handlers = [h for h in logging.getLogger().handlers if str(_COPY) in str(getattr(h, "baseFilename", ""))]
        for handler in logging_handlers:
            logging.getLogger().removeHandler(handler)
            handler.close()

    def test_fvg_state_identical_and_structure_only_on_closed_bar_change(self):
        old, new = self.old_fvg, self.new_fvg
        cache = new.FVGStructureCache()
        checked = 0
        for seed in range(4):
            base = market(seed)
            for bar in range(12):
                frame = base.iloc[bar:bar + 650].reset_index(drop=True)
                for k in range(4):
                    tick = frame.copy()
                    tick.loc[649, "close"] += 0.3 * k - .6
                    tick.loc[649, "high"] = max(tick.loc[649, "high"], tick.loc[649, "close"]) + .1 * k
                    tick.loc[649, "low"] = min(tick.loc[649, "low"], tick.loc[649, "close"]) - .1 * k
                    if seed == 3 and k == 2:
                        tick.loc[600, "high"] += 0.5            # closed-bar correction
                    self.assertEqual(old.build_fvg_state("X", "1m", tick),
                                     new.build_fvg_state("X", "1m", tick, cache=cache))
                    self.assertEqual(old.build_fvg_state("X", "1m", tick),
                                     new.build_fvg_state("X", "1m", tick, cache=None))
                    checked += 1
        self.assertEqual(checked, 4 * 12 * 4)
        # new bar or correction -> rebuild; ticks inside a bar -> touch only
        # 4 seeds x 12 new bars, plus seed 3's closed-bar correction (k=2) and revert (k=3) per bar.
        self.assertEqual(cache.structure_builds, 4 * 12 + 2 * 12)
        self.assertEqual(cache.structure_reuses, checked - cache.structure_builds)

    def test_fvg_results_are_fresh_objects(self):
        new = self.new_fvg
        cache = new.FVGStructureCache()
        df = market(5).iloc[:650].reset_index(drop=True)
        first = new.build_fvg_state("X", "1m", df, cache=cache)
        for zone in first["zones"] + first["filled_zones"]:
            zone["zone_bot"] = -1.0
        first["eligible_zone_ids"].append("mutated")
        self.assertEqual(new.build_fvg_state("X", "1m", df, cache=cache),
                         self.old_fvg.build_fvg_state("X", "1m", df))

    def test_wilder_atr_identical_with_gaps(self):
        df = market(7, 300)
        high, low, close = (pd.to_numeric(df[c]) for c in ("high", "low", "close"))
        high.iloc[50] = np.nan
        low.iloc[120] = np.inf
        for period in (1, 14, 30):
            pd.testing.assert_series_equal(self.old_fvg._wilder_atr(high, low, close, period),
                                           self.new_fvg._wilder_atr(high, low, close, period), check_exact=True)

    def test_oz_fact_memo_is_keyed_by_the_same_frame_object(self):
        memo = self.oz.OZFactMemo()
        a = market(1).iloc[:20].reset_index(drop=True)
        b = a.copy()
        calls = []

        def compute(df):
            calls.append(id(df))
            return float(df["close"].iloc[-1])

        self.assertEqual(memo.get(a, "x", lambda: compute(a)), memo.get(a, "x", lambda: compute(a)))
        self.assertEqual(len(calls), 1)
        memo.get(b, "x", lambda: compute(b))                # equal content, different object
        self.assertEqual(len(calls), 2)
        self.assertEqual((memo.computed, memo.reused), (2, 1))
        del a, b
        gc.collect()
        self.assertEqual(memo._frames, {})                   # dropped with the frame
        self.assertEqual(memo.get(None, "x", lambda: 3), 3)

    def test_oz_live_states_are_private_copies(self):
        oz = self.oz
        monitor = oz.OZMonitor.__new__(oz.OZMonitor)
        monitor._oz_facts = oz.OZFactMemo()
        monitor._in_cycle = True                              # memo is used only inside run_once
        df = market(2).iloc[:30].reset_index(drop=True)
        for col in ("PRICE", "RSI", "STO", "DI"):
            prefix = "price_hma_6" if col == "PRICE" else f"{col}_val"
            lo, hi = ("price_band_lower", "price_band_upper") if col == "PRICE" else (f"{col}_db", f"{col}_ub")
            df[prefix], df[lo], df[hi] = 50.0, 40.0, 60.0
        first = monitor._live_states(df)
        first["PRICE"] = "MUTATED"
        self.assertEqual(monitor._live_states(df), oz.percentile_states(df.iloc[-1]))
        self.assertEqual((monitor._oz_facts.computed, monitor._oz_facts.reused), (2, 1))  # row+states once, states reused

    def test_oz_fact_memo_only_inside_a_cycle(self):
        oz = self.oz
        monitor = oz.OZMonitor.__new__(oz.OZMonitor)
        monitor._oz_facts = oz.OZFactMemo()
        monitor._in_cycle = False
        df = market(3).iloc[:10].reset_index(drop=True)
        first = monitor._fact(df, "k", lambda: float(df["close"].iloc[-1]))
        df.loc[9, "close"] = first + 1.0                      # caller mutates the frame in place
        self.assertEqual(monitor._fact(df, "k", lambda: float(df["close"].iloc[-1])), first + 1.0)
        self.assertEqual(monitor._oz_facts.computed + monitor._oz_facts.reused, 0)

    def test_checkpoint_skips_identical_rewrites_only(self):
        oz = self.oz
        writes = []
        monitor = oz.OZMonitor.__new__(oz.OZMonitor)
        monitor.symbol, monitor.validation_mode, monitor.trigger_mode = "X", "BLIND", "OZ"
        for name in monitor._checkpoint_fields:
            setattr(monitor, name, {})
        monitor._checkpoint_path = "unused"
        original = oz.atomic_json
        oz.atomic_json = lambda path, payload: writes.append(payload)
        try:
            monitor._checkpoint()
            monitor._checkpoint()
            monitor.alert_keys = {("1m", "LONG", pd.Timestamp("2026-09-01"))}
            monitor._checkpoint()
        finally:
            oz.atomic_json = original
        self.assertEqual(len(writes), 2)


if __name__ == "__main__":
    unittest.main()
