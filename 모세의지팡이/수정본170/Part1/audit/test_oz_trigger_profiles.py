"""Regression contracts for the user-authorized OZ profile / trigger changes.

- 무지성·브레이커·레짐·슈퍼 free combination (order-free), incl. 무지성 레짐 / 슈퍼 올존
- OZ trigger family: touch only, accumulated while the candidate is alive,
  TRUE B0 touch passes immediately, otherwise 2+ distinct conditions
- 슈퍼: base-TF Regime Basis crossing (first low below / alert above; SHORT mirror)
- 브레이커 keeps the strict BO break (unchanged)
- Watch text and SPECIAL trigger slots use the same profile vocabulary
- OZ_SYSTEM CONTROL [전략 설정] saves/loads per-SPECIAL enabled + trigger text
"""
import importlib.machinery
import importlib.util
import json
import logging
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path

import pandas as pd

from harness import ROOT, World


def _cross_frame(w, n_after=3):
    """LONG structure: GC at bar 78->79 of the fixture, then n_after extra closed bars."""
    frame = w.market("IN")
    for i in range(n_after):
        row = frame.iloc[[-1]].copy()
        row["time"] += pd.Timedelta(minutes=1)
        frame = pd.concat([frame, row], ignore_index=True)
    return frame


class OZTriggerProfiles(unittest.TestCase):
    def setUp(self):
        logging.disable(logging.CRITICAL)
        self.w = World()
        self.addCleanup(self.w.close)
        self.w.isolate_specs()
        self.p = self.w.modules["oz_profiles"]

    # ------------------------------------------------------------------ vocabulary
    def test_profile_vocabulary_is_order_free_and_keeps_legacy_labels(self):
        p = self.p
        self.assertEqual(len(p.PROFILE_KEYS), 16)
        self.assertEqual(p.PROFILE_KEYS[:6], (
            ("NORMAL", "OZ"), ("BLIND", "OZ"), ("NORMAL", "BREAKER"),
            ("BLIND", "BREAKER"), ("NORMAL", "REGIME"), ("NORMAL", "BREAKER_REGIME")))
        legacy = {("NORMAL", "OZ"): "올존", ("BLIND", "OZ"): "무지성 올존",
                  ("NORMAL", "BREAKER"): "브레이커 올존", ("BLIND", "BREAKER"): "무지성 브레이커 올존",
                  ("NORMAL", "REGIME"): "레짐 올존", ("NORMAL", "BREAKER_REGIME"): "브레이커 레짐 올존"}
        for key, label in legacy.items():
            self.assertEqual(p.profile_label(*key), label)
        self.assertEqual(p.parse_profile_text("레짐 무지성 올존"), ("BLIND", "REGIME"))
        self.assertEqual(p.parse_profile_text("무지성레짐올존"), ("BLIND", "REGIME"))
        self.assertEqual(p.parse_profile_text("슈퍼 올존"), ("NORMAL", "SUPER"))
        self.assertEqual(p.parse_profile_text("슈퍼 레짐 브레이커 무지성 올존"), ("BLIND", "BREAKER_REGIME_SUPER"))
        self.assertEqual(p.parse_profile_text("일반 올존"), ("NORMAL", "OZ"))
        for bad in ("일반 무지성 올존", "레짐 레짐 올존", "슈퍼", "하이퍼 올존"):
            with self.assertRaises(ValueError):
                p.parse_profile_text(bad)
        # REGIME is no longer forced to NORMAL.
        self.assertEqual(self.w.oz._resolve_oz_modes("BLIND", "REGIME"), ("BLIND", "REGIME"))
        self.assertEqual(self.w.oz._resolve_oz_modes("BLIND", "SUPER_REGIME"), ("BLIND", "REGIME_SUPER"))
        # v1 payload compatibility unchanged.
        self.assertEqual(self.w.oz._resolve_oz_modes(None, None, "BLIND_BREAKER"), ("BLIND", "BREAKER"))
        self.assertEqual(self.w.oz._resolve_oz_modes(None, None, "BREAKER_REGIME"), ("NORMAL", "BREAKER_REGIME"))

    # ------------------------------------------------------------------ trigger family
    def _monitor(self, vm="BLIND", tm="OZ"):
        w = self.w
        mon = w.oz.OZMonitor("BTCUSD", w.config, w.sender, w.watch, validation_mode=vm, trigger_mode=tm)
        for phase in ("OUT", "IN"):
            frame = w.market(phase)
            mon._process_hma_cross("1m", frame)
            mon._process_out_in("1m", frame)
        cand = mon.candidates[("1m", "LONG")]
        self.assertIsNotNone(cand)
        mon._refresh_true_b0(cand)
        return mon, cand

    def test_touch_only_no_near(self):
        oz = self.w.oz.OZMonitor
        df = pd.DataFrame([{"low": 100.0, "high": 101.0}])
        self.assertEqual(oz._trigger_state(df, "LONG", 100.0, "B0"), "TOUCH")
        self.assertEqual(oz._trigger_state(df, "LONG", 99.99, "B0"), "MISS")   # previously NEAR
        self.assertEqual(oz._trigger_state(df, "SHORT", 101.01, "B0"), "MISS")

    def _quiet_bar(self, frame, cand, *, low=None, high=None, hma6=None, hma17=None):
        """Next live bar that touches nothing unless asked; HMA6 rising (no color turn)."""
        row = frame.iloc[[-1]].copy()
        row["time"] += pd.Timedelta(minutes=1)
        base = float(cand.true_b0_price) + 10.0
        row["open"], row["close"] = base + .2, base + .5
        row["low"] = base if low is None else low
        row["high"] = base + 1 if high is None else high
        row["hma_6"] = float(frame.iloc[-1]["hma_6"]) + .1 if hma6 is None else hma6
        row["hma_17"] = base - 5 if hma17 is None else hma17
        row["wonbi_lower"], row["wonbi_upper"] = base - 20, base + 20
        return pd.concat([frame, row], ignore_index=True)

    def test_single_non_b0_condition_does_not_fire_but_two_accumulated_do(self):
        mon, cand = self._monitor()
        frame = _cross_frame(self.w, 0)
        # bar 1: HMA17 touch only
        frame = self._quiet_bar(frame, cand)
        level = float(frame.iloc[-1]["low"]) + .5
        frame.loc[len(frame) - 1, "hma_17"] = level
        mon._accumulate_trigger_hits("1m", frame, "LONG", cand, 1)
        self.assertEqual(set(cand.trigger_hits), {"HMA17"})
        quiet = self._quiet_bar(frame, cand)
        self.assertIsNone(mon._final_trigger_decision(quiet, "LONG", cand))
        # later bars: HMA6 color turn on closed candles (hull vs hull[2]: green -> red)
        for _ in range(4):
            frame = self._quiet_bar(frame, cand)
        frame.loc[len(frame) - 2, "hma_6"] = float(frame.iloc[-4]["hma_6"]) - 1
        mon._accumulate_trigger_hits("1m", frame, "LONG", cand, 5)
        self.assertEqual(set(cand.trigger_hits), {"HMA17", "HMA6_TURN"})
        quiet = self._quiet_bar(frame, cand)
        decision = mon._final_trigger_decision(quiet, "LONG", cand)
        self.assertIsNotNone(decision)
        self.assertEqual(decision.trigger_name, "HMA17, HMA6 하방꺾임")

    def test_true_b0_touch_passes_immediately(self):
        mon, cand = self._monitor()
        # Exactly touching TRUE B0 (low == B0) is a touch but not a strict break.
        frame = self._quiet_bar(_cross_frame(self.w, 0), cand, low=float(cand.true_b0_price))
        decision = mon._final_trigger_decision(frame, "LONG", cand)
        self.assertIsNotNone(decision)
        self.assertEqual(decision.trigger_name, "B0")
        # Breaker keeps the strict BO break: a touch that does not go strictly below is not a break.
        div, dcand = self._monitor(tm="BREAKER")
        self.assertIsNone(div._final_trigger_decision(frame, "LONG", dcand))

    def test_b0_is_not_accumulated_on_the_b0_bar_itself(self):
        mon, cand = self._monitor()
        frame = self._quiet_bar(_cross_frame(self.w, 0), cand, low=float(cand.true_b0_price) - .1)
        mon._accumulate_trigger_hits("1m", frame, "LONG", cand, 0)   # bars_since < MIN_BARS
        self.assertNotIn("B0", cand.trigger_hits)
        mon._accumulate_trigger_hits("1m", frame, "LONG", cand, 1)
        self.assertIn("B0", cand.trigger_hits)

    def test_accumulation_resets_when_every_registration_expired(self):
        mon, cand = self._monitor()
        cand.trigger_hits = {"HMA17": "HMA17"}
        for reg in mon._active_percentile_candidates("1m", "LONG"):
            reg.invalidated = True
        mon._maintain_base_candidate("1m", "LONG", _cross_frame(self.w, 1))
        self.assertEqual(cand.trigger_hits, {})

    def test_trigger_hits_survive_restart(self):
        w = self.w
        mon, cand = self._monitor(vm="BLIND", tm="REGIME_SUPER")
        cand.trigger_hits = {"WONBI": "WONBI"}
        mon._checkpoint()
        restored = w.oz.OZMonitor("BTCUSD", w.config, w.sender, w.watch, validation_mode="BLIND", trigger_mode="REGIME_SUPER")
        self.assertEqual(restored.candidates[("1m", "LONG")].trigger_hits, {"WONBI": "WONBI"})

    # ------------------------------------------------------------------ SUPER / BLIND REGIME
    def test_super_filter_regime_basis_crossing(self):
        oz = self.w.oz
        rows = [{"time": pd.Timestamp("2026-01-01 00:00"), "RSI_val": 40, "RSI_basis": 50},
                {"time": pd.Timestamp("2026-01-01 00:01"), "RSI_val": 45, "RSI_basis": 50},
                {"time": pd.Timestamp("2026-01-01 00:02"), "RSI_val": 55, "RSI_basis": 50}]
        df = pd.DataFrame(rows)
        first = pd.Timestamp("2026-01-01 00:00")
        self.assertTrue(oz.super_filter(df, "RSI", "LONG", first))
        self.assertFalse(oz.super_filter(df, "RSI", "SHORT", first))
        df.loc[2, "RSI_val"] = 49          # alert-time value still below basis
        self.assertFalse(oz.super_filter(df, "RSI", "LONG", first))
        short = pd.DataFrame([{**r, "RSI_val": 100 - r["RSI_val"]} for r in rows])
        self.assertTrue(oz.super_filter(short, "RSI", "SHORT", first))
        price = pd.DataFrame([{"time": first, "price_hma_6": 1.0, "price_regime_basis": 2.0},
                              {"time": first + pd.Timedelta(minutes=1), "price_hma_6": 3.0, "price_regime_basis": 2.0}])
        self.assertTrue(oz.super_filter(price, "PRICE", "LONG", first))
        self.assertFalse(oz.super_filter(price, "PRICE", "LONG", None))

    def test_blind_regime_and_super_completion(self):
        w = self.w
        df = w.compat_module.apply_requested_features(w.market("NEXT_BAR"), w.oz.REQUIRED_INDS)
        upper = df.copy()
        data = {"1m": df, "6m": upper}
        mon, cand = self._monitor(vm="BLIND", tm="REGIME")
        for col in ("RSI_regime_slope", "STO_regime_slope", "DI_regime_slope", "price_regime_slope"):
            upper.loc[len(upper) - 1, col] = .01
        self.assertIsNotNone(mon._candidate_completion_decision(data, "1m", "LONG", require_external=False, commit_validation=False))
        for col in ("RSI_regime_slope", "STO_regime_slope", "DI_regime_slope", "price_regime_slope"):
            upper.loc[len(upper) - 1, col] = -1
        self.assertIsNone(mon._candidate_completion_decision(data, "1m", "LONG", require_external=False, commit_validation=False))
        # 무지성 레짐 needs the upper TF feed only.
        self.assertIsNone(mon._candidate_completion_decision({"1m": df}, "1m", "LONG", require_external=False, commit_validation=False))

        sup, scand = self._monitor(vm="BLIND", tm="SUPER")
        for val, basis in w.oz.SUPER_COLUMNS.values():
            df[val] = df[val].astype(float)
            df[basis] = df[basis].astype(float)
        # Fixture: every Percentile value 30 on the OUT bar, 50 afterwards; basis ~50.
        first_time = sup._family_true_b0_time(scand, sup._active_percentile_candidates("1m", "LONG")[0])
        row = df.index[pd.to_datetime(df["time"]) == first_time][-1]
        for fam, (val, basis) in w.oz.SUPER_COLUMNS.items():
            df.loc[row, val] = df.loc[row, basis] - 5
            df.loc[len(df) - 1, val] = df.loc[len(df) - 1, basis] - 1
        self.assertIsNone(sup._candidate_completion_decision({"1m": df}, "1m", "LONG", require_external=False, commit_validation=False))
        for fam, (val, basis) in w.oz.SUPER_COLUMNS.items():
            df.loc[len(df) - 1, val] = df.loc[len(df) - 1, basis] + 1
        result = sup._candidate_completion_decision({"1m": df}, "1m", "LONG", require_external=False, commit_validation=False)
        self.assertIsNotNone(result)
        self.assertEqual(result.grade, "S")

    # ------------------------------------------------------------------ Watch / SPECIAL
    def test_watch_command_accepts_free_combinations(self):
        w = self.w
        cases = {
            "BTCUSD 1분 무지성 레짐 올존 알려줘": ("BLIND", "REGIME"),
            "BTCUSD 2분 슈퍼 올존 알려줘": ("NORMAL", "SUPER"),
            "BTCUSD 3분 레짐 브레이커 무지성 올존 알려줘": ("BLIND", "BREAKER_REGIME"),
            "BTCUSD 5분 매수 슈퍼 레짐 올존 알려줘": ("NORMAL", "REGIME_SUPER"),
        }
        for text, profile in cases.items():
            before = set(w.watch._watches)
            w.clock.advance(.01)
            w.manager.handle_command(text, "123")
            w.commands()
            added = [w.watch._watches[k] for k in set(w.watch._watches) - before]
            self.assertEqual(len(added), 1, text)
            self.assertEqual((added[0].validation_mode, added[0].trigger_mode), profile, text)
        self.assertEqual(w.watch._watches[[k for k in w.watch._watches if w.watch._watches[k].timeframes == ("5m",)][0]].direction, "LONG")
        self.assertIn("무지성 레짐 올존", " ".join(x["text"] for x in w.http.deliveries))
        trig = w.chain.ChainTriggerSpec("OZ_ALERT", "1m", validation_mode="BLIND", trigger_mode="SUPER_REGIME")
        trig.validate()
        self.assertEqual(trig.trigger_mode, "REGIME_SUPER")
        self.assertEqual(trig.label(), "1분 무지성 레짐 슈퍼 올존")

    def test_special_trigger_slot_overrides_only_when_set(self):
        w = self.w
        w.restart_manager()
        default = {k: (s.validation_mode, s.trigger_mode) for k, s in w.manager.official_specs.items()}
        self.assertEqual(default["PIPELINE_1@1h"], ("NORMAL", "BREAKER"))
        old = os.environ.get("OZ_SPECIAL_TRIGGERS")
        os.environ["OZ_SPECIAL_TRIGGERS"] = json.dumps({"SPECIAL1": "슈퍼 무지성 올존", "SPECIAL6": "잘못된 올존"})
        try:
            w.restart_manager()
        finally:
            if old is None: os.environ.pop("OZ_SPECIAL_TRIGGERS", None)
            else: os.environ["OZ_SPECIAL_TRIGGERS"] = old
        specs = w.manager.official_specs
        self.assertEqual((specs["PIPELINE_1@1h"].validation_mode, specs["PIPELINE_1@1h"].trigger_mode), ("BLIND", "SUPER"))
        self.assertIn("무지성 슈퍼 올존", specs["PIPELINE_1@1h"].alert_template)
        # Invalid slot text is ignored -> SPECIAL6 keeps its code default.
        six = [s for k, s in specs.items() if k.startswith("PIPELINE_6")]
        self.assertTrue(six)
        self.assertTrue(all((s.validation_mode, s.trigger_mode) == ("NORMAL", "BREAKER") for s in six))
        # Every other SPECIAL keeps its code default.
        for key, profile in default.items():
            if not key.startswith("PIPELINE_1"):
                self.assertEqual((specs[key].validation_mode, specs[key].trigger_mode), profile, key)
        w.restart_manager()   # no env -> back to defaults
        self.assertEqual({k: (s.validation_mode, s.trigger_mode) for k, s in w.manager.official_specs.items()}, default)


class _Var:
    def __init__(self, value=None): self.value = value
    def get(self): return self.value
    def set(self, value): self.value = value


class _Widget:
    created = []
    def __init__(self, *args, **kwargs):
        self.kwargs = kwargs
        _Widget.created.append(self)
    def __getattr__(self, name):
        return lambda *a, **k: 0


def _fake_tk():
    tk = types.ModuleType("tkinter")
    for name in ("Tk", "Toplevel", "Label", "Frame", "Button", "Checkbutton", "Entry"):
        setattr(tk, name, type(name, (_Widget,), {}))
    tk.BooleanVar = tk.StringVar = _Var
    box = types.ModuleType("tkinter.messagebox")
    box.errors = []
    box.showerror = lambda title, text, **k: box.errors.append(text)
    box.showinfo = lambda *a, **k: None
    box.askyesno = lambda *a, **k: True
    tk.messagebox = box
    return tk, box


class ControlTriggerSlots(unittest.TestCase):
    """OZ_SYSTEM CONTROL [전략 설정]: per-SPECIAL checkbox + trigger text, saved and reloaded."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="oz-control-")
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        (base / "program" / "SPECIAL").mkdir(parents=True)
        for rel in ("OZ_SYSTEM CONTROL.pyw", "program/oz_profiles.py", "program/manager_KIM.py",
                    "program/command_interpreter.py", "program/watch_orchestrator.py"):
            (base / rel).write_bytes((ROOT / rel).read_bytes())
        for path in (ROOT / "program" / "SPECIAL").glob("SPECIAL*.py"):
            (base / "program" / "SPECIAL" / path.name).write_bytes(path.read_bytes())
        self.base = base
        self.tk, self.box = _fake_tk()
        self.saved = {k: sys.modules.get(k) for k in ("tkinter", "tkinter.messagebox")}
        sys.modules["tkinter"], sys.modules["tkinter.messagebox"] = self.tk, self.box
        self.addCleanup(self._restore)
        spec = importlib.util.spec_from_file_location("oz_control_under_test", base / "OZ_SYSTEM CONTROL.pyw",
                                                      loader=importlib.machinery.SourceFileLoader(
                                                          "oz_control_under_test", str(base / "OZ_SYSTEM CONTROL.pyw")))
        self.ctl = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.ctl)
        self.ctl.MosesController.refresh_status = lambda self: None

    def _restore(self):
        for k, v in self.saved.items():
            if v is None: sys.modules.pop(k, None)
            else: sys.modules[k] = v

    def _controller(self):
        root = types.SimpleNamespace(
            **{name: (lambda *a, **k: 0) for name in (
                "title", "resizable", "iconbitmap", "update_idletasks", "minsize", "geometry",
                "after", "winfo_rootx", "winfo_rooty", "winfo_width")},
            winfo_reqwidth=lambda: 400)
        return self.ctl.MosesController(root)

    def _dialog_rows(self, ctl):
        _Widget.created.clear()
        ctl.open_special_settings()
        entries = [w for w in _Widget.created if type(w).__name__ == "Entry"]
        checks = [w for w in _Widget.created if type(w).__name__ == "Checkbutton"]
        save = next(w for w in _Widget.created if type(w).__name__ == "Button" and w.kwargs.get("text") == "저장")
        names = [c.kwargs["text"] for c in checks]
        return dict(zip(names, zip(checks, entries))), save

    def test_defaults_come_from_special_code(self):
        ctl = self._controller()
        self.assertIsNone(ctl.special_settings)
        self.assertIsNone(ctl.enabled_specials())
        rows, _save = self._dialog_rows(ctl)
        texts = {name: entry.kwargs["textvariable"].get() for name, (_c, entry) in rows.items()}
        self.assertEqual(texts["SPECIAL1"], "브레이커 올존")
        self.assertEqual(texts["SPECIAL3"], "올존")
        self.assertEqual(texts["SPECIAL5"], "무지성 올존")
        self.assertEqual(texts["SPECIAL7"], "브레이커 레짐 올존")

    def test_save_load_and_env(self):
        ctl = self._controller()
        rows, save = self._dialog_rows(ctl)
        rows["SPECIAL2"][1].kwargs["textvariable"].set("레짐 무지성 올존")
        rows["SPECIAL4"][0].kwargs["variable"].set(False)
        save.kwargs["command"]()
        self.assertEqual(self.box.errors, [])
        stored = json.loads((self.base / "special_settings.json").read_text(encoding="utf-8"))
        self.assertEqual(stored["specials"]["SPECIAL2"], {"enabled": True, "trigger": "무지성 레짐 올존"})
        self.assertEqual(stored["specials"]["SPECIAL1"], {"enabled": True, "trigger": None})
        self.assertFalse(stored["specials"]["SPECIAL4"]["enabled"])
        again = self._controller()
        self.assertEqual(again.special_settings, ctl.special_settings)
        self.assertNotIn("SPECIAL4", again.enabled_specials())
        self.assertIn("SPECIAL7", again.enabled_specials())
        self.assertEqual(json.loads(self.ctl.special_trigger_env(again.special_settings)), {"SPECIAL2": "무지성 레짐 올존"})
        rows, _save = self._dialog_rows(again)
        self.assertEqual(rows["SPECIAL2"][1].kwargs["textvariable"].get(), "무지성 레짐 올존")

    def test_invalid_trigger_is_not_saved(self):
        ctl = self._controller()
        rows, save = self._dialog_rows(ctl)
        rows["SPECIAL1"][1].kwargs["textvariable"].set("일반 무지성 올존")
        save.kwargs["command"]()
        self.assertTrue(self.box.errors and "SPECIAL1" in self.box.errors[0])
        self.assertFalse((self.base / "special_settings.json").exists())


if __name__ == "__main__":
    unittest.main()
