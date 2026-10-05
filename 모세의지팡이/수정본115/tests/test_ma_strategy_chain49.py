"""Revision49: arbitrary MA StrategySpecs and the existing MA_EXPRESSION chain path.

All prices are synthetic. Real Composer, GenericWatchController, WatchMonitor,
WatchMAStore and OZ watch registration run without broker/Telegram/network I/O.
"""
from __future__ import annotations

import json
import socket
import sys
import threading
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "Part1/program"))

from domain_clock import event_scope
from domain_memory import memory_scope
from event_application import create_event_engine
from event_composer_domain import ConditionSpec, StrategySpec, make_chain_trigger
from event_engine import FeedSnapshot, Kind
from event_engine.domain_support import plain
from event_engine.market import COLUMNS
from event_engine.watch_runtime import WatchMonitor
from event_startup import export_engine_state
from monitor_OZ import GenericWatchController
from watch_array_facts import WatchMAStore
from watch_ma import parse_ma_expression, parse_ma_name
from watch_orchestrator import CHAIN_TRIGGER_TYPES, ChainTriggerSpec, TimedChainSpec, WatchOrchestrator

SYMBOL = "XAUUSD+"
BASE = 1790380000
CONFIG = {
    "WONBI_SIGMA": "3", "STAFF_ALLOWED_SYMBOLS": SYMBOL, "TARGET_SYMBOLS": SYMBOL,
    "TELEGRAM_TOKEN": "OFFLINE", "TELEGRAM_CHAT_ID": "offline",
}
CASES = [
    ("골든크로스(WMA17,SMA20)", 150., "LONG", 3600.),
    ("데드크로스(HMA90,HMA270)", 90., "SHORT", 1800.),
    ("골든크로스(EMA37,WMA73)", 150., "LONG", 900.),
]


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError("Revision49 tests must not access network/MT5/Telegram")
    monkeypatch.setattr(socket, "create_connection", denied)
    for name in ("connect", "connect_ex", "sendto"):
        monkeypatch.setattr(socket.socket, name, denied)


def condition(kind="MA_STATE", family="HMA", period=270, fast=90, side=None):
    return ConditionSpec(
        kind, "1m", ma_family=family, fast_period=fast if kind == "MA_STATE" else 0,
        slow_period=period, side=side or ("UP" if kind == "MA_SLOPE_STATE" else "ABOVE"),
    )


def strategy(*conditions):
    result = StrategySpec(
        "MA49:strategy", "MA49", SYMBOL, tuple(conditions), ("1m",),
        final_direction="LONG", source="PRIVATE", destination="PRIVATE", owner_chat_id="offline",
    )
    result.validate()
    return result


def trigger(expression, **kwargs):
    return ChainTriggerSpec("MA_EXPRESSION", "1m", ma_expression=expression, **kwargs)


def chain(*triggers, **kwargs):
    result = TimedChainSpec(
        "CHAIN:MA49", "offline", SYMBOL, tuple(triggers), oz_tfs=("1m",),
        created_at=BASE, **kwargs,
    )
    result.validate()
    return result


class EngineRun:
    def __init__(self, *, spec=None, timed=None, files=None, backtest=False):
        files = dict(files or {})
        if spec is not None:
            files["composer_private_watches.json"] = json.dumps({"version": 3, "watches": [spec.to_json()]})
        if timed is not None:
            files["composer_timed_chains.json"] = json.dumps({"version": 2, "chains": [timed.to_json()]})
        self.engine = create_event_engine(
            CONFIG, symbols=(SYMBOL,), enabled_specials=(), state_files=files, backtest=backtest,
            symbol_state_files=json.loads(files.get("event_composer_memory.json", "{}")),
        )
        self.sequence = 0

    def feed(self, prices, *, native=None):
        self.sequence += 1
        n = len(prices)
        values = np.full((n, len(COLUMNS)), 100., dtype="float64")
        for key in ("open", "close"):
            values[:, COLUMNS[key]] = prices
        values[:, COLUMNS["high"]] = np.asarray(prices) + 1
        values[:, COLUMNS["low"]] = np.asarray(prices) - 1
        for name, value in (native or {}).items():
            values[:, COLUMNS[name]] = value
        snap = FeedSnapshot(
            np.arange(n, dtype="int64") * 60 + BASE, np.ones(n, dtype="int64"),
            values, self.sequence, "MA49", {},
        )
        self.engine.ingress.post(
            Kind.MARKET_BUNDLE, source="staff", source_seq=self.sequence,
            source_time=int(snap.time[-1]) * 1000,
            payload={"symbol": SYMBOL, "feeds": {"1m": snap}},
        )
        self.engine.run()
        assert not self.engine.error_log, self.engine.error_log

    @property
    def kernel(self):
        return self.engine.strategy_state["COMPOSER"]["kernels"][SYMBOL]

    @property
    def timed(self):
        return self.kernel.manager.timed_chains["CHAIN:MA49"]

    @property
    def watch_runtime(self):
        return self.engine.strategy_state["WATCH_CONDITIONS"]["runtime"]

    def exported(self):
        return export_engine_state(self.engine)

    def oz_watches(self):
        return json.loads(self.exported()["oz_manual_watch_state.json"])["watches"]

    def events(self, family="WATCH"):
        result = []
        for signal in self.engine.signals:
            content = plain(signal.payload).get("content", {})
            if content.get("family") == family and content.get("event", {}).get("kind") == "GENERIC_TRIGGER":
                result.append(content["event"])
        return result

    def signature(self):
        return [(s.source_time, plain(s.payload)) for s in self.engine.signals]


@pytest.mark.parametrize("family", ["SMA", "WMA", "EMA", "HMA"])
@pytest.mark.parametrize("kind", ["MA_STATE", "MA_PRICE_STATE", "MA_SLOPE_STATE"])
@pytest.mark.parametrize("period", [1, 37, 73, 90, 270, 701, 1000003])
def test_arbitrary_positive_integer_period_and_strategy_json(family, kind, period):
    cond = condition(kind, family, period, fast=period + 1)
    assert cond.slow_period == period
    spec = strategy(cond)
    restored = StrategySpec.from_json(json.loads(json.dumps(spec.to_json())))
    assert restored.to_json() == spec.to_json()
    assert restored.conditions == (cond,)


@pytest.mark.parametrize("kind", ["MA_STATE", "MA_PRICE_STATE", "MA_SLOPE_STATE"])
@pytest.mark.parametrize("value", [0, -1, 0.5, 37.5, True, None, "no", "37.5", float("inf"), float("nan")])
def test_invalid_period_never_silently_truncated(kind, value):
    with pytest.raises(ValueError):
        condition(kind, period=value)
    if kind == "MA_STATE":
        with pytest.raises(ValueError):
            condition(kind, fast=value)


@pytest.mark.parametrize("kind", ["MA_STATE", "MA_PRICE_STATE", "MA_SLOPE_STATE"])
def test_family_side_and_existing_constraints_still_validated(kind):
    with pytest.raises(ValueError):
        condition(kind, family="SMMA")
    with pytest.raises(ValueError):
        condition(kind, side="SIDEWAYS")
    if kind == "MA_STATE":
        with pytest.raises(ValueError, match="같습니다"):
            condition(kind, period=90, fast=90)
    else:
        with pytest.raises(ValueError, match="fast_period"):
            ConditionSpec(kind, "1m", ma_family="WMA", fast_period=1, slow_period=37,
                          side="UP" if kind == "MA_SLOPE_STATE" else "ABOVE")


def test_canonical_ma_alias_and_integer_text_use_existing_contract():
    # Do not introduce a second interpretation of the existing EMA21 -> EMA20 alias.
    cond = condition("MA_PRICE_STATE", "EMA", "21")
    assert cond.slow_period == parse_ma_name("EMA21")[1] == 20
    assert condition(period="270", fast="90").slow_period == 270


@pytest.mark.parametrize("expression,default", [(case[0], "CLOSE") for case in CASES] + [
    ("HMA90 > HMA270", "LIVE"),
    ("골든크로스(WMA17,SMA20) AND 기울기(HMA270) > 0", "CLOSE"),
])
@pytest.mark.parametrize("mode", [None, "CLOSE", "LIVE"])
def test_expression_defaults_explicit_modes_factory_and_json(expression, default, mode):
    expected = mode or default
    for trig in (trigger(expression, evaluation_mode=mode),
                 make_chain_trigger("MA_EXPRESSION", "1m", ma_expression=expression, evaluation_mode=mode),
                 ChainTriggerSpec.from_json(dict(watch_type="MA_EXPRESSION", tf="1m", ma_expression=expression,
                                                 **({"evaluation_mode": mode} if mode else {})))):
        trig.validate()
        assert trig.evaluation_mode == expected
        assert trig.ma_expression == parse_ma_expression(expression).canonical
        restored = ChainTriggerSpec.from_json(json.loads(json.dumps(trig.to_json())))
        assert restored.to_json() == trig.to_json()
        assert trig.ma_expression in trig.label()
        payload = WatchOrchestrator.trigger_payload_locked(chain(trig))
        assert payload["action"] == "GENERIC_WATCH"
        assert (payload["ma_expression"], payload["evaluation_mode"]) == (trig.ma_expression, expected)


@pytest.mark.parametrize("expression", [None, "", "골든크로스(WMA0,SMA20)", "골든크로스(HMA90)", "SMMA20 > EMA37"])
def test_expression_validation_reuses_existing_parser_errors(expression):
    with pytest.raises(ValueError):
        trigger(expression).validate()


def test_no_new_family_specific_cross_types():
    assert {"EMA_CROSS", "HMA_CROSS", "MA_EXPRESSION"} <= CHAIN_TRIGGER_TYPES
    assert not {"SMA_CROSS", "WMA_CROSS"} & CHAIN_TRIGGER_TYPES
    for name in ("SMA_CROSS", "WMA_CROSS"):
        with pytest.raises(ValueError):
            ChainTriggerSpec(name, "1m").validate()


@pytest.mark.parametrize("watch_type,family,fast,slow", [
    ("EMA_CROSS", "EMA", 20, 50), ("EMA_CROSS", "EMA", 50, 200),
    ("HMA_CROSS", "HMA", 6, 17), ("HMA_CROSS", "HMA", 50, 168),
])
def test_legacy_cross_spec_payload_and_roundtrip(watch_type, family, fast, slow):
    trig = ChainTriggerSpec(watch_type, "1m", ma_family=family, fast_period=fast, slow_period=slow)
    trig.validate()
    assert (trig.ma_family, trig.fast_period, trig.slow_period, trig.evaluation_mode) == (family, fast, slow, "LIVE")
    raw = trig.to_json()
    raw.pop("ma_expression")  # Unmodified old save files have no expression field.
    raw.pop("evaluation_mode")  # Legacy omitted mode still resolves to LIVE.
    restored = ChainTriggerSpec.from_json(raw)
    assert restored.to_json() == trig.to_json()
    payload = WatchOrchestrator.trigger_payload_locked(chain(restored))
    assert payload["watch_type"] == watch_type and "ma_expression" not in payload


@pytest.mark.parametrize("family", ["SMA", "WMA", "EMA", "HMA"])
@pytest.mark.parametrize("kind", ["MA_STATE", "MA_PRICE_STATE", "MA_SLOPE_STATE"])
def test_strategy_dynamic_ma_family_actual_store_to_oz(kind, family):
    spec = strategy(condition(kind, family, 73, fast=37))
    run = EngineRun(spec=spec)
    run.feed([100.] * 400)
    assert not run.oz_watches()
    run.feed([100.] * 399 + [150.])
    watches = run.oz_watches()
    assert len(watches) == 1 and watches[0]["source_spec_id"] == spec.spec_id
    assert isinstance(run.kernel.inputs.ma, WatchMAStore)
    assert (SYMBOL, "1m", family + "73") in run.kernel.inputs.ma.entries


@pytest.mark.parametrize("extra", [False, True])
def test_required_hma90_hma270_strategy_to_oz_and_same_live_backtest(extra):
    conditions = [condition()]
    if extra:
        conditions += [condition("MA_PRICE_STATE", "WMA", 37), condition("MA_SLOPE_STATE", "HMA", 270)]
    spec = strategy(*conditions)
    runs = [EngineRun(spec=spec, backtest=value) for value in (False, True)]
    for run in runs:
        run.feed([100.] * 400)
        assert not run.oz_watches()
        run.feed([100.] * 399 + [150.])
        assert len(run.oz_watches()) == 1
        fact = run.kernel.manager.ma_state_facts[SYMBOL, "1m", "HMA", 90, 270]
        assert fact["relation"] == "ABOVE" and fact["fast_value"] > fact["slow_value"]
        assert isinstance(run.kernel.inputs.ma, WatchMAStore)
        old_ids = [w["watch_id"] for w in run.oz_watches()]
        run.feed([100.] * 400)  # No new setup when the MA state returns to EQUAL.
        assert run.kernel.manager.ma_state_facts[SYMBOL, "1m", "HMA", 90, 270]["relation"] == "EQUAL"
        assert [w["watch_id"] for w in run.oz_watches()] == old_ids
    assert runs[0].signature() == runs[1].signature()


@pytest.mark.parametrize("expression,step,direction,window", CASES)
def test_required_crosses_actual_monitor_chain_to_oz_close_default_and_parity(expression, step, direction, window):
    timed = chain(trigger(expression), final_window_sec=window)
    runs = [EngineRun(timed=timed, backtest=value) for value in (False, True)]
    for run in runs:
        run.feed([100.] * 400)
        run.feed([100.] * 400)  # Registration/bootstrap must not replay an old close.
        assert not run.oz_watches() and not run.events()
        registered = run.watch_runtime.controller._watches[run.timed.current_watch_id]
        assert registered.evaluation_mode == "CLOSE"
        assert registered.ma_expression == parse_ma_expression(expression).canonical
        assert isinstance(run.watch_runtime.controller, GenericWatchController)
        assert isinstance(run.watch_runtime.monitors[SYMBOL], WatchMonitor)
        assert isinstance(run.watch_runtime.monitors[SYMBOL].ma, WatchMAStore)
        run.feed([100.] * 400 + [step])  # Cross is still in the forming row.
        assert not run.oz_watches() and run.timed.stage == 0
        run.feed([100.] * 400 + [step, 100.])
        assert run.timed.stage == 1 and run.timed.active_child_id
        expected_time = BASE + 401 * 60
        assert run.timed.active_until == expected_time + window
        assert len(run.oz_watches()) == 1
        events = run.events()
        assert len(events) == 1
        assert (events[0]["direction"], events[0]["evaluation_mode"], events[0]["event_time"]) == (direction, "CLOSE", expected_time)
        assert events[0]["watch_type"] == "MA_EXPRESSION"
        run.feed([100.] * 400 + [step, 100.])
        assert len(run.events()) == 1 and len(run.oz_watches()) == 1
    assert runs[0].signature() == runs[1].signature()


@pytest.mark.parametrize("expression,step,direction,window", CASES)
@pytest.mark.parametrize("restore", ["checkpoint", "files"])
def test_cross_save_restore_before_advance_and_active_oz(expression, step, direction, window, restore):
    timed = chain(trigger(expression), final_window_sec=window)
    original = EngineRun(timed=timed)
    original.feed([100.] * 400)
    original.feed([100.] * 400)
    resumed = EngineRun(files=original.exported() if restore == "files" else None)
    if restore == "checkpoint":
        resumed.engine.restore(original.engine.checkpoint())
    resumed.sequence = original.sequence
    for run in (original, resumed):
        run.feed([100.] * 400 + [step])
        assert not run.oz_watches()
        run.feed([100.] * 400 + [step, 100.])
        assert run.timed.stage == 1
        assert run.timed.triggers[0].ma_expression == parse_ma_expression(expression).canonical
        assert run.timed.triggers[0].evaluation_mode == "CLOSE"
        assert len(run.oz_watches()) == 1
    assert original.timed.to_json() == resumed.timed.to_json()
    assert original.oz_watches() == resumed.oz_watches()
    active = EngineRun(files=resumed.exported())
    active.feed([100.] * 400 + [step, 100.])
    assert active.timed.active_child_id == resumed.timed.active_child_id
    assert active.timed.triggers[0].ma_expression == resumed.timed.triggers[0].ma_expression


@pytest.mark.parametrize("expression,step,direction,window", CASES)
@pytest.mark.parametrize("case", ["warmup", "equal", "opposite"])
def test_cross_no_false_oz_for_warmup_equality_or_opposite(expression, step, direction, window, case):
    run = EngineRun(timed=chain(trigger(expression), final_window_sec=window))
    n = 3 if case == "warmup" else 400
    change = step if case == "warmup" else 100. if case == "equal" else 200. - step
    run.feed([100.] * n)
    run.feed([100.] * n)
    run.feed([100.] * n + [change])
    run.feed([100.] * n + [change, 100.])
    assert not run.events() and not run.oz_watches() and run.timed.stage == 0


@pytest.mark.parametrize("expression,step,direction,window", CASES)
def test_explicit_live_expression_override_is_retained(expression, step, direction, window):
    run = EngineRun(timed=chain(trigger(expression, evaluation_mode="LIVE"), final_window_sec=window))
    run.feed([100.] * 400)
    run.feed([100.] * 400)
    run.feed([100.] * 400 + [step])
    assert run.timed.stage == 1 and len(run.oz_watches()) == 1
    assert run.events()[0]["evaluation_mode"] == "LIVE"


class OrchestrationRun:
    """Exercise state-machine events/JSON, with commands recorded at its boundary."""
    def __init__(self, tmp_path, memory=None):
        self.memory = dict(memory or {})
        self.clock = {}
        self.pushes = []
        self.state = WatchOrchestrator(
            lock=threading.RLock(), state_path=tmp_path / "chains.json", config={},
            push_callback=self.pushes.append, notify_callback=lambda *a, **k: True,
            mark_dirty_callback=lambda: None,
        )

    def call(self, method, *args):
        with event_scope((BASE + 24000) * 1000, "ma49-test", self.clock), memory_scope(self.memory):
            return getattr(self.state, method)(*args)

    def hit(self, timed, *, cancel=False):
        index = -1 if cancel else timed.stage
        wid = timed.invalidation_watch_ids["0"] if cancel else timed.current_watch_id
        return self.call("handle_generic_trigger", {
            "kind": "GENERIC_TRIGGER", "chain_id": timed.chain_id, "watch_id": wid,
            "chain_stage": index, "event_time": BASE + 24000, "event_id": f"event:{index}",
        })


def test_advance_restore_invalidation_cancel_and_rearm_preserve_expression(tmp_path):
    first = trigger("골든크로스(WMA17,SMA20)", next_window_sec=600)
    second = trigger("골든크로스(EMA37,WMA73)")
    cancel = trigger("데드크로스(HMA90,HMA270)")
    timed = chain(first, second, final_window_sec=1800, invalidation_triggers=(cancel,))
    run = OrchestrationRun(tmp_path)
    run.call("add_chain", timed)
    assert run.pushes[-1]["ma_expression"] == first.ma_expression
    run.hit(timed)
    assert timed.stage == 1
    assert timed.stage_deadline == BASE + 24000 + 600
    payloads = {p["chain_stage"]: p for p in run.pushes if p.get("action") == "GENERIC_WATCH"}
    assert payloads[1]["ma_expression"] == second.ma_expression
    assert payloads[-1]["ma_expression"] == cancel.ma_expression
    assert payloads[1]["evaluation_mode"] == payloads[-1]["evaluation_mode"] == "CLOSE"
    resumed = OrchestrationRun(tmp_path, run.memory)
    resumed.call("load_state")
    restored = resumed.state.chains[timed.chain_id]
    assert restored.to_json() == timed.to_json()
    result = resumed.hit(restored, cancel=True)
    assert result["invalidated"] and restored.stage == 0
    assert restored.triggers[0].ma_expression == first.ma_expression
    assert restored.invalidation_triggers[0].ma_expression == cancel.ma_expression
    assert resumed.pushes[-1]["ma_expression"] == first.ma_expression
    assert any(p["action"] == "CANCEL_GENERIC" and p["watch_id"] == payloads[1]["watch_id"] for p in resumed.pushes)
    # Re-advance through both expression stages and cancel an active OZ candidate.
    resumed.hit(restored)
    resumed.hit(restored)
    child = restored.active_child_id
    assert child and WatchOrchestrator.final_payload_locked(restored)["action"] == "MANUAL_WATCH"
    resumed.hit(restored, cancel=True)
    assert any(p["action"] == "CANCEL_MANUAL" and p["watch_id"] == child for p in resumed.pushes)
    assert restored.active_child_id is None and restored.stage == 0
    assert resumed.pushes[-1]["ma_expression"] == first.ma_expression


def test_distinct_expression_cancellation_allowed_identical_canonical_conflict_rejected():
    first = trigger("골든크로스(WMA17,SMA20)")
    different = trigger("데드크로스(HMA90,HMA270)")
    chain(first, invalidation_triggers=(different,))  # Both have direction=None; still distinct.
    with pytest.raises(ValueError, match="충돌"):
        chain(first, invalidation_triggers=(trigger("골든크로스(wma17, sma20)"),))


@pytest.mark.parametrize("mode", ["UNORDERED", "FILTER"])
def test_multi_watch_payloads_context_and_json_preserve_expression(mode):
    from event_composer_domain import ComposerManager
    first = trigger(CASES[0][0], valid_sec=600)
    second = trigger(CASES[2][0], valid_sec=600)
    timed = chain(first, second, order_mode=mode, final_window_sec=600)
    payloads = WatchOrchestrator.trigger_payloads_locked(timed)
    assert [p["ma_expression"] for p in payloads] == [first.ma_expression, second.ma_expression]
    manager = ComposerManager.__new__(ComposerManager)
    manager._lock = threading.RLock()
    manager.timed_chains = {timed.chain_id: timed}
    for payload in payloads:
        context = manager._chain_watch_context(payload["watch_id"])
        assert context["ma_expression"] == payload["ma_expression"]
        assert context["evaluation_mode"] == "CLOSE"
    assert TimedChainSpec.from_json(json.loads(json.dumps(timed.to_json()))).to_json() == timed.to_json()


@pytest.mark.parametrize("watch_type,fast_column,slow_column", [
    ("EMA_CROSS", "ema_50", "ema_200"), ("HMA_CROSS", "hma_6", "hma_17"),
])
@pytest.mark.parametrize("direction,step", [("LONG", 110.), ("SHORT", 90.)])
def test_legacy_cross_actual_monitor_chain_to_oz_is_unchanged(watch_type, fast_column, slow_column, direction, step):
    timed = chain(ChainTriggerSpec(watch_type, "1m", direction=direction), final_window_sec=3600)
    runs = [EngineRun(timed=timed, backtest=value) for value in (False, True)]
    for run in runs:
        run.feed([100.] * 400, native={fast_column: 100., slow_column: 100.})
        run.feed([100.] * 400, native={fast_column: 100., slow_column: 100.})
        assert not run.events() and not run.oz_watches()
        run.feed([100.] * 401, native={fast_column: [100.] * 400 + [step], slow_column: 100.})
        assert not run.events() and run.timed.stage == 0
        run.feed([100.] * 402, native={fast_column: [100.] * 400 + [step, step], slow_column: 100.})
        assert run.timed.stage == 1 and len(run.oz_watches()) == 1
        assert (run.events()[0]["watch_type"], run.events()[0]["direction"]) == (watch_type, direction)
        run.feed([100.] * 402, native={fast_column: [100.] * 400 + [step, step], slow_column: 100.})
        assert len(run.events()) == 1 and len(run.oz_watches()) == 1
    assert runs[0].signature() == runs[1].signature()


def test_non_cross_expression_chain_reuses_live_default_and_actual_monitor():
    run = EngineRun(timed=chain(trigger("HMA90 > HMA270"), final_window_sec=900))
    run.feed([100.] * 400)
    run.feed([100.] * 400)
    assert not run.events()
    run.feed([100.] * 399 + [150.])
    assert run.timed.stage == 1 and len(run.oz_watches()) == 1
    assert run.events()[0]["evaluation_mode"] == "LIVE"
