"""strategy_TREND.py -> strategy_INDICATOR.py split.

* 정식 경로: 실행/import는 strategy_INDICATOR / indicator_facts / indicator_score, strategy_TREND.py는 호환 래퍼.
* 회귀: 전체 추세점수·evaluate·Watch metric·지표 함수가 분리 전 코드(fixtures/legacy_strategy_TREND.py)와 동일.
* 추세 Fact: SMA20(시가) 1봉 기울기와 HMA50 2봉 기울기가 같은 방향일 때만 상승/하락, 다르면 중립.
* 재사용/증분: 같은 입력 재사용, 관련 입력 변경 시에만 재계산, LONG/SHORT 공유.
* on-demand: 평상시 LIVE 경로는 전체 지표를 계산하지 않고 '추세점수 몇 점?' 요청 때만 계산.
* 성능: 계산량과 실행시간을 측정하고 results/indicator_perf.json에 남깁니다.
"""
from __future__ import annotations

import ast
import importlib
import importlib.util
import json
import logging
import sys
import time
import types
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from harness import FIXTURES, ROOT, World

PROGRAM = ROOT / "program"
TFS = ("1m", "15m", "1h", "4h")
FREQ = {"1m": "1min", "15m": "15min", "1h": "1h", "4h": "4h"}


def _zmq_stub():
    mod = types.ModuleType("zmq")
    mod.Context = types.SimpleNamespace(instance=lambda: None)
    mod.REQ = mod.RCVTIMEO = mod.SNDTIMEO = mod.LINGER = 0
    mod.Again = mod.ZMQError = type("ZMQError", (Exception,), {})
    return mod


def _load_modules():
    """Legacy oracle + current modules, without leaking them into other tests."""
    names = ("zmq", "durable_protocol", "indicator_facts", "indicator_score", "strategy_INDICATOR",
             "strategy_TREND", "legacy_strategy_TREND")
    saved = {name: sys.modules.get(name) for name in names}
    sys.path.insert(0, str(PROGRAM))
    try:
        for name in names:
            sys.modules.pop(name, None)
        sys.modules["zmq"] = _zmq_stub()
        spec = importlib.util.spec_from_file_location("legacy_strategy_TREND", FIXTURES / "legacy_strategy_TREND.py")
        legacy = importlib.util.module_from_spec(spec)
        sys.modules["legacy_strategy_TREND"] = legacy
        spec.loader.exec_module(legacy)
        facts = importlib.import_module("indicator_facts")
        score = importlib.import_module("indicator_score")
        indicator = importlib.import_module("strategy_INDICATOR")
        wrapper = importlib.import_module("strategy_TREND")
        return types.SimpleNamespace(legacy=legacy, facts=facts, score=score, indicator=indicator, wrapper=wrapper)
    finally:
        sys.path.remove(str(PROGRAM))
        for name, old in saved.items():
            if old is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = old


def market(seed, n=650, tf="1m", nan=False, start="2026-09-01"):
    r = np.random.default_rng(seed)
    p = 100 + np.cumsum(r.normal(0, 0.3, n))
    o = p + r.normal(0, .1, n)
    c = p + r.normal(0, .1, n)
    h = np.maximum(o, c) + abs(r.normal(0, .2, n))
    l = np.minimum(o, c) - abs(r.normal(0, .2, n))
    v = r.integers(0, 500, n).astype(float)
    if seed % 3 == 0:
        c[r.integers(0, n, 5)] = o[r.integers(0, n, 5)]      # equality ties
    if nan:
        h[:5] = np.nan
        v[10] = np.nan
    t = pd.date_range(start, periods=n, freq=FREQ[tf])
    hma50 = pd.Series(o).rolling(9).mean().to_numpy()
    return pd.DataFrame({"time": t, "open": o, "high": h, "low": l, "close": c, "volume": v, "hma_50": hma50})


def bare(cls, threshold=40.0):
    obj = cls.__new__(cls)
    obj.threshold = threshold
    return obj


class IndicatorSplit(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.m = _load_modules()

    # ------------------------------------------------------------------
    # 정식 경로
    # ------------------------------------------------------------------
    def test_canonical_paths_use_strategy_indicator(self):
        control = ast.parse((ROOT / "OZ_SYSTEM CONTROL.pyw").read_text(encoding="utf-8"))
        programs = next(ast.literal_eval(n.value) for n in control.body if isinstance(n, ast.Assign)
                        and any(isinstance(t, ast.Name) and t.id == "PROGRAMS" for t in n.targets))
        scripts = [p[1] for p in programs]
        self.assertIn("strategy_INDICATOR.py", scripts)
        self.assertNotIn("strategy_TREND.py", scripts)
        for path in PROGRAM.rglob("*.py"):
            if path.name == "strategy_TREND.py":
                continue
            tree = ast.parse(path.read_text(encoding="utf-8-sig"))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    self.assertNotEqual(node.module, "strategy_TREND", path.name)
                elif isinstance(node, ast.Import):
                    self.assertNotIn("strategy_TREND", [a.name for a in node.names], path.name)
        features = (PROGRAM / "watch_ma_features.py").read_text(encoding="utf-8")
        self.assertIn("from indicator_facts import sma, wma, ema, hma", features)

    def test_compat_wrapper_only_reexports(self):
        w, ind, f = self.m.wrapper, self.m.indicator, self.m.facts
        self.assertTrue(issubclass(w.TrendEngine, ind.IndicatorEngine))
        self.assertIs(w.TrendEngine.evaluate, ind.IndicatorEngine.evaluate_score)
        self.assertIs(w.TrendWatchRegistry, ind.IndicatorWatchRegistry)
        self.assertIs(w.TrendCommandWorker, ind.IndicatorCommandWorker)
        for name in ("ema", "sma", "wma", "hma", "dmi", "psar", "supertrend_dir", "mss_state", "atr_trend_state"):
            self.assertIs(getattr(w, name), getattr(f, name))
        self.assertEqual(w.TREND_METRIC_FIELDS, self.m.legacy.TREND_METRIC_FIELDS)

    # ------------------------------------------------------------------
    # 분리 전 코드와 동일한 계산 결과
    # ------------------------------------------------------------------
    def test_score_metrics_and_functions_identical_to_pre_split_code(self):
        legacy, ind, f = self.m.legacy, self.m.indicator, self.m.facts
        old = bare(legacy.TrendEngine)
        new = bare(ind.IndicatorEngine)
        fields = sorted(ind.TREND_METRIC_FIELDS)
        checked = 0
        for seed in range(12):
            tf = TFS[seed % 4]
            df = market(seed, tf=tf, nan=seed % 5 == 0)
            with self.subTest(seed=seed, tf=tf):
                self.assertEqual(old.evaluate("X", df, tf), new.evaluate_score("X", df, tf))
                for direction in ("LONG", "SHORT"):
                    self.assertEqual(old.score(df, tf, direction), new.score(df, tf, direction))
                self.assertEqual(old.metric_snapshot(df, tf, fields), new.metric_snapshot(df, tf, fields))
                closed = df.iloc[:-1].copy()
                self.assertEqual(old.metric_snapshot(closed, tf, ["adx", "macd", "rsi14"]),
                                 new.metric_snapshot(closed, tf, ["adx", "macd", "rsi14"]))
                calls = [(name, (df,)) for name in ("true_range", "dmi", "cci", "psar", "supertrend_dir",
                                                   "atr_trend_state", "mss_state", "mfi", "cmf")]
                calls += [("rsi", (df.close,)), ("linreg", (df.close,)), ("rma", (df.close, 14))]
                calls += [(name, (df.open, 17)) for name in ("ema", "sma", "wma", "hma")]
                for name, args in calls:
                    a, b = getattr(legacy, name)(*args), getattr(f, name)(*args)
                    pairs = zip(a, b) if isinstance(a, tuple) else ((a, b),)
                    for x, y in pairs:
                        pd.testing.assert_series_equal(x, y, check_exact=True)
                pd.testing.assert_series_equal(legacy.anchored_vwap(df, tf), f.anchored_vwap(df, tf), check_exact=True)
                checked += 1
        self.assertEqual(checked, 12)

    def test_audit_fixture_score_is_unchanged_but_only_on_demand(self):
        w = World()
        try:
            w.feed(w.market())
            df = w.trend_engine.staff.request("BTCUSD", ["1m"], ["HMA"])["1m"]
            result = w.trend_engine.evaluate_score("BTCUSD", df, "1m")
            # 분리 전 test_baseline이 TREND_STATE로 받던 값과 동일.
            self.assertAlmostEqual(result["long_score"], 86.66666666666667)
            self.assertAlmostEqual(result["short_score"], 16.666666666666668)
            self.assertEqual((result["trend"], result["direction"]), ("UP", "LONG"))
        finally:
            w.close()

    # ------------------------------------------------------------------
    # 전략 추세 Fact
    # ------------------------------------------------------------------
    def _trend_frame(self, open_step, hma_steps):
        n = 80
        o = 100 + open_step * np.arange(n, dtype=float)
        h50 = np.full(n, 50.0)
        h50[-3:] = 50 + np.cumsum([0.0, *hma_steps])
        return pd.DataFrame({"time": pd.date_range("2026-09-01", periods=n, freq="1min"),
                             "open": o, "high": o + 1, "low": o - 1, "close": o + .5,
                             "volume": np.full(n, 10.0), "hma_50": h50})

    def test_trend_requires_sma20_open_and_hma50_slopes_to_agree(self):
        new = bare(self.m.indicator.IndicatorEngine)
        cases = [
            ((+0.2, (+0.1, +0.1)), ("UP", "LONG")),
            ((-0.2, (-0.1, -0.1)), ("DOWN", "SHORT")),
            ((+0.2, (-0.1, -0.1)), ("NEUTRAL", "NEUTRAL")),   # 방향 불일치
            ((-0.2, (+0.1, +0.1)), ("NEUTRAL", "NEUTRAL")),
            ((0.0, (+0.1, +0.1)), ("NEUTRAL", "NEUTRAL")),    # SMA20 평탄
            ((+0.2, (+0.1, -0.1)), ("NEUTRAL", "NEUTRAL")),   # HMA50[-1] == HMA50[-3]
            ((+0.2, (-0.3, +0.1)), ("NEUTRAL", "NEUTRAL")),   # HMA50 2봉 기울기 음수
        ]
        for (open_step, hma_steps), expected in cases:
            df = self._trend_frame(open_step, hma_steps)
            result = new.evaluate_trend("X", df, "1m", frame=self.m.facts.standalone_frame(df, "1m"))
            with self.subTest(open_step=open_step, hma_steps=hma_steps):
                self.assertEqual((result["trend"], result["direction"]), expected)
                s20 = df["open"].rolling(20).mean()
                self.assertEqual(result["sma20_slope"], float(s20.iloc[-1] - s20.iloc[-2]))
                self.assertEqual(result["hma50_slope"], float(df["hma_50"].iloc[-1] - df["hma_50"].iloc[-3]))
                self.assertNotIn("long_score", result)

    def test_trend_is_unavailable_without_enough_open_history(self):
        new = bare(self.m.indicator.IndicatorEngine)
        df = self._trend_frame(0.2, (0.1, 0.1)).tail(20).reset_index(drop=True)
        self.assertIsNone(new.evaluate_trend("X", df, "1m", frame=self.m.facts.standalone_frame(df, "1m")))

    # ------------------------------------------------------------------
    # 재사용 / 증분 / 공유
    # ------------------------------------------------------------------
    def test_fact_store_reuses_unchanged_inputs_and_recomputes_changed_ones(self):
        f = self.m.facts
        store = f.FactStore()
        df = market(1)
        frame = store.frame("X", "1m", df)
        frame["trend"], frame["dmi"], frame["vw"]
        first = dict(store.stats.computed)

        same = store.frame("X", "1m", df.copy())
        same["trend"], same["dmi"], same["vw"]
        self.assertEqual(store.stats.computed, first)                  # 같은 입력 -> 계산 0회
        self.assertEqual(store.stats.identical_frames, 1)

        tick = df.copy()
        tick.loc[tick.index[-1], ["close", "high", "volume"]] += np.array([0.05, 0.3, 7.0])
        ticked = store.frame("X", "1m", tick)
        ticked["trend"], ticked["dmi"], ticked["e10"]
        self.assertEqual(store.stats.computed["trend"], 1)            # OPEN/HMA50 불변 -> 재사용
        self.assertEqual(store.stats.computed["s20"], 1)
        self.assertEqual(store.stats.computed["o"], 1)
        self.assertEqual(store.stats.computed["dmi"], 2)              # high/close 변경 -> 재계산
        self.assertEqual(store.stats.computed["tr"], 2)
        # 재사용한 값과 새로 계산한 값 모두 단독 계산과 같습니다.
        alone = f.standalone_frame(tick, "1m")
        self.assertEqual(ticked["trend"], alone["trend"])
        pd.testing.assert_series_equal(ticked["plus"], alone["plus"], check_exact=True)

        bar = market(1, start="2026-09-01 00:01")                     # 새 봉 -> 시간축 변경
        store.frame("X", "1m", bar)["trend"]
        self.assertEqual(store.stats.computed["trend"], 2)

    def test_long_short_and_metric_fields_share_each_fact_once(self):
        f, score = self.m.facts, self.m.score
        stats = f.FactStats()
        frame = f.FactFrame(market(4), "1m", stats=stats)
        score.evaluate_score("X", frame)
        self.assertTrue(stats.computed)
        self.assertEqual(set(stats.computed.values()), {1})           # LONG/SHORT 공유, 중복 0회
        self.assertEqual(stats.computed["tr"], 1)                      # TR을 쓰는 6개 지표가 공유

        stats = f.FactStats()
        new = bare(self.m.indicator.IndicatorEngine)
        new.metric_snapshot(market(4), "1m", ["plus_di", "minus_di", "adx", "macd", "macd_signal"],
                            frame=f.FactFrame(market(4), "1m", stats=stats))
        self.assertEqual(stats.computed["dmi"], 1)
        self.assertEqual(stats.computed["e12"], 1)
        self.assertNotIn("st", stats.computed)                         # 요청하지 않은 지표는 계산 안 함
        self.assertNotIn("vw", stats.computed)

    # ------------------------------------------------------------------
    # on-demand: 평상시 LIVE 경로 vs 명시적 추세점수 요청
    # ------------------------------------------------------------------
    def test_regular_live_trend_watch_never_computes_the_full_score(self):
        logging.disable(logging.CRITICAL)
        w = World()
        try:
            w.isolate_specs()
            w.feed(w.market())
            w.trend_engine.run_watch_once({"BTCUSD": {"1m": ()}})
            computed = set(w.trend_engine.facts.stats.computed)
            self.assertEqual(computed, {"o", "s20", "h50", "trend"})
            fact = w.manager.trend_facts[("BTCUSD", "1m")]
            self.assertEqual(fact["trend"], "UP")
            self.assertNotIn("long_score", fact)
        finally:
            w.close()
            logging.disable(logging.NOTSET)

    def test_trend_score_query_is_computed_only_when_asked(self):
        logging.disable(logging.CRITICAL)
        w = World()
        try:
            w.isolate_specs()
            w.feed(w.market())
            ci = w.modules["command_interpreter"].CommandInterpreter
            self.assertTrue(ci.is_trend_score_query("BTCUSD 1분 추세점수 몇 점?"))
            self.assertTrue(ci.is_trend_score_query("비트 15분 추세점수 알려줘"))
            self.assertFalse(ci.is_trend_score_query("15분 추세점수 40 이상일때 1분 매수 올존 알려줘"))
            self.assertFalse(ci.is_trend_score_query("15분 추세점수 40점 이상이면 알려줘"))
            self.assertFalse(ci.is_trend_score_query("BTCUSD 1분 추세 알려줘"))

            def ask(text):
                before = len(w.http.deliveries)
                w.manager.handle_command(text, "123")
                w.commands()
                q = w.trend_worker.query_queue
                while not q.empty():
                    w.trend_engine.run_query(q.get_nowait())
                return [d["text"] for d in w.http.deliveries[before:]]

            texts = ask("BTCUSD 1분 추세 알려줘")
            self.assertTrue(any("추세 · UP" in t and "SMA20(시가) 기울기" in t for t in texts), texts)
            self.assertNotIn("dmi", w.trend_engine.facts.stats.computed)

            texts = ask("BTCUSD 1분 추세점수 몇 점?")
            self.assertTrue(any("추세점수 · 86.7점" in t and "LONG 86.7 / SHORT 16.7" in t for t in texts), texts)
        finally:
            w.close()
            logging.disable(logging.NOTSET)

    # ------------------------------------------------------------------
    # 성능
    # ------------------------------------------------------------------
    def test_performance_regular_path_and_on_demand_score(self):
        legacy, ind, f = self.m.legacy, self.m.indicator, self.m.facts
        base = market(7, n=653)
        ticks = []
        for bar in range(3):                                     # 3개 봉 x 봉당 4틱
            frame = base.iloc[bar:bar + 650].reset_index(drop=True)
            for k in range(4):
                tick = frame.copy()
                tick.loc[649, "close"] += 0.01 * k
                tick.loc[649, "high"] += 0.02 * k
                tick.loc[649, "volume"] += k
                ticks.append(tick)

        old = bare(legacy.TrendEngine)
        t0 = time.perf_counter()
        for tick in ticks:
            old.evaluate("X", tick, "1m")                         # 분리 전: 매 루프 전체 지표 x LONG/SHORT
        old_seconds = time.perf_counter() - t0

        new = bare(ind.IndicatorEngine)
        t0 = time.perf_counter()
        for tick in ticks:
            new.evaluate_trend("X", tick, "1m")
        new_seconds = time.perf_counter() - t0
        stats = new.facts.stats
        self.assertEqual(stats.computed["trend"], 3)              # 새 봉 때만 재계산
        self.assertEqual(stats.total_computed(), 12)              # o, s20, h50, trend x 3봉

        df = ticks[-1]
        t0 = time.perf_counter()
        old_result = old.evaluate("X", df, "1m")
        old_score_seconds = time.perf_counter() - t0
        score_stats = f.FactStats()
        t0 = time.perf_counter()
        new_result = self.m.score.evaluate_score("X", f.FactFrame(df, "1m", stats=score_stats))
        new_score_seconds = time.perf_counter() - t0
        self.assertEqual(old_result, new_result)

        report = {
            "regular_live_path": {
                "loops": len(ticks), "bars": 3,
                "before_seconds": round(old_seconds, 4), "after_seconds": round(new_seconds, 4),
                "speedup": round(old_seconds / max(new_seconds, 1e-9), 1),
                "before_full_score_evaluations": len(ticks) * 2,
                "after_fact_computations": stats.total_computed(),
                "after_full_score_evaluations": 0,
            },
            "on_demand_full_score": {
                "before_seconds": round(old_score_seconds, 4), "after_seconds": round(new_score_seconds, 4),
                "speedup": round(old_score_seconds / max(new_score_seconds, 1e-9), 1),
                "after_fact_computations": score_stats.total_computed(),
                "after_duplicate_computations": sum(v - 1 for v in score_stats.computed.values()),
            },
        }
        out = Path(__file__).resolve().parent / "results" / "indicator_perf.json"
        out.parent.mkdir(exist_ok=True)
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        self.assertLess(new_seconds * 20, old_seconds, report)
        self.assertLess(new_score_seconds, old_score_seconds, report)


if __name__ == "__main__":
    unittest.main()
