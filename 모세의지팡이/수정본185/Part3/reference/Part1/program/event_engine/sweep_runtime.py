"""Resident SWEEP state; original predicates, direct Snapshot inputs."""
from __future__ import annotations
import logging
import numpy as np
from strategy_SWEEP import SweepSpec, as_epoch, filter_external_levels, REQUIRED_INDS
from .market import select, health
from .sweep_levels import LevelStore

class LiquidityDetector:
    def __init__(self, spec: SweepSpec, restored: dict | None = None):
        self.spec = spec
        self.last_closed_time = None
        self.states: dict[str, dict] = {}
        self._dirty = False
        if restored:
            self._restore(restored)

    def _new_state(self, level: dict) -> dict:
        return {
            "id": level["id"],
            "direction": level["direction"],
            "level_code": level["level_code"],
            "level_name": level["level_name"],
            "level_price": float(level["price"]),
            "touched": False,
            "consumed": False,
            "touch_time": None,
            "touch_high": None,
            "touch_low": None,
            "touch_close": None,
        }

    def _restore(self, payload: dict) -> None:
        restored_states: dict[str, dict] = {}
        for level_id, raw in (payload.get("states", {}) if isinstance(payload, dict) else {}).items():
            if not isinstance(raw, dict):
                continue
            direction = str(raw.get("direction") or "").upper()
            if direction not in {"LONG", "SHORT"}:
                continue
            try:
                level_price = float(raw.get("level_price"))
            except (TypeError, ValueError):
                continue
            if not np.isfinite(level_price):
                continue
            touched = bool(raw.get("touched", False))
            consumed = bool(raw.get("consumed", False)) and touched
            touch_time = as_epoch(raw.get("touch_time"))
            def optional_float(value):
                try:
                    number = float(value)
                except (TypeError, ValueError):
                    return None
                return number if np.isfinite(number) else None

            restored_states[str(level_id)] = {
                "id": str(level_id),
                "direction": direction,
                "level_code": str(raw.get("level_code") or ""),
                "level_name": str(raw.get("level_name") or ""),
                "level_price": level_price,
                "touched": touched,
                "consumed": consumed,
                "touch_time": touch_time,
                "touch_high": optional_float(raw.get("touch_high")),
                "touch_low": optional_float(raw.get("touch_low")),
                "touch_close": optional_float(raw.get("touch_close")),
            }
        self.states = restored_states
        self.last_closed_time = as_epoch(payload.get("last_closed_time")) if isinstance(payload, dict) else None
        self._dirty = False

    def export_state(self) -> dict:
        return {
            "last_closed_time": self.last_closed_time,
            "states": {
                level_id: {
                    "direction": state["direction"],
                    "level_code": state["level_code"],
                    "level_name": state["level_name"],
                    "level_price": float(state["level_price"]),
                    "touched": bool(state["touched"]),
                    "consumed": bool(state["consumed"]),
                    "touch_time": state.get("touch_time"),
                    "touch_high": state.get("touch_high"),
                    "touch_low": state.get("touch_low"),
                    "touch_close": state.get("touch_close"),
                }
                for level_id, state in self.states.items()
            },
        }

    def consume_dirty(self) -> bool:
        dirty = self._dirty
        self._dirty = False
        return dirty

    def _sync_levels(self, levels: list[dict]) -> list[dict]:
        active_ids = {lv["id"] for lv in levels}
        removed_states = [
            state for key, state in self.states.items()
            if key not in active_ids
        ]
        if removed_states:
            self._dirty = True
        self.states = {
            key: state for key, state in self.states.items()
            if key in active_ids
        }
        for level in levels:
            if level["id"] not in self.states:
                self.states[level["id"]] = self._new_state(level)
                self._dirty = True
        return removed_states

    def _context(self, state: dict) -> dict:
        return {
            "watch_id": self.spec.watch_id,
            "symbol": self.spec.symbol,
            "source_tf": self.spec.source_tf,
            "direction": state["direction"],
            "level_code": state["level_code"],
            "level_name": state["level_name"],
            "level_id": state["id"],
            "level_price": float(state["level_price"]),
            "touch_time": state.get("touch_time"),
        }

    def _touch_event(self, state: dict, restored: bool = False) -> dict:
        event = {
            "kind": "SWEEP_TOUCH",
            **self._context(state),
            "event_time": float(state["touch_time"]),
            "touch_high": state.get("touch_high"),
            "touch_low": state.get("touch_low"),
            "touch_close": state.get("touch_close"),
        }
        if restored:
            event["restored"] = True
        return event

    def active_touch_events(self, restored: bool = False) -> list[dict]:
        return [
            self._touch_event(state, restored=restored)
            for state in self.states.values()
            if state["touched"] and not state["consumed"] and state.get("touch_time") is not None
        ]

    def _invalidation_event(self, state: dict, event_time: float) -> dict:
        return {
            "kind": "SWEEP_INVALIDATED",
            **self._context(state),
            "event_time": float(event_time),
        }

    def invalidate_all(self, event_time: float | None = None) -> list[dict]:
        when = (
            float(event_time)
            if event_time is not None
            else 0.0
        )
        return [
            self._invalidation_event(state, when)
            for state in self.states.values()
            if state["touched"] and not state["consumed"]
        ]

    def process(self, df_base, levels: list[dict]) -> list[dict]:
        events: list[dict] = []
        if df_base is None or len(df_base) < 3:
            return events
        if not {"time", "high", "low", "close"}.issubset(("time","high","low","close")):
            return events

        closed = df_base.row(-2)
        closed_time = as_epoch(closed.get("time"))
        if closed_time is None:
            return events

        removed_states = self._sync_levels(levels)
        for state in removed_states:
            # KIM에 SWEEP_TOUCH를 실제로 전달했던 대표 레벨만 해제합니다.
            # 동일 봉에서 함께 닿아 consumed 처리된 비대표 레벨은
            # 애초 KIM에 전달되지 않았으므로 해제 이벤트도 만들지 않습니다.
            if state["touched"] and not state["consumed"]:
                events.append(self._invalidation_event(state, float(closed_time)))

        if not self.states:
            return events
        if self.last_closed_time is not None and closed_time <= float(self.last_closed_time):
            return events
        self.last_closed_time = float(closed_time)
        self._dirty = True

        candidates = {"LONG": [], "SHORT": []}
        for state in self.states.values():
            if state["consumed"] or state["touched"]:
                continue
            level = float(state["level_price"])
            touched = (
                float(closed["high"]) >= level
                if state["direction"] == "SHORT"
                else float(closed["low"]) <= level
            )
            if touched:
                candidates[state["direction"]].append(state)

        selected: dict[str, dict] = {}
        if candidates["LONG"]:
            selected["LONG"] = min(candidates["LONG"], key=lambda x: float(x["level_price"]))
        if candidates["SHORT"]:
            selected["SHORT"] = max(candidates["SHORT"], key=lambda x: float(x["level_price"]))

        # 기존 동일봉 다중레벨 대표선정 규칙은 유지합니다.
        for direction, group in candidates.items():
            winner = selected.get(direction)
            if winner is None:
                continue
            for state in group:
                state["touched"] = True
                state["touch_time"] = float(closed_time)
                state["touch_high"] = float(closed["high"])
                state["touch_low"] = float(closed["low"])
                state["touch_close"] = float(closed["close"])
                if state is not winner:
                    state["consumed"] = True
                self._dirty = True

        for state in selected.values():
            events.append(self._touch_event(state))

        return events

    def snapshot(self, levels: list[dict]) -> dict:
        self._sync_levels(levels)
        states = []
        for state in self.states.values():
            item = self._context(state)
            item.update({
                "touched": bool(state["touched"]),
                "consumed": bool(state["consumed"]),
            })
            states.append(item)
        states.sort(key=lambda x: (x["direction"], x["level_price"]))
        return {"levels": states}

class SweepRuntime:
    def __init__(self,state):
        self.detectors=state.setdefault('detectors',{})
        # Migration of legacy restored objects happens once, never per bundle.
        for key,value in tuple(self.detectors.items()):
            if not isinstance(value,LiquidityDetector):
                self.detectors[key]=LiquidityDetector(value.spec,value.export_state())
        self._fingerprints=state.setdefault('fingerprints',{})
        self._restored_pending_sync=state.setdefault('restored_pending_sync',set())
        self._source_health=state.setdefault('source_health',{})
        self._state_dirty=False
        self.levels=LevelStore()
    def bind(self,board,manager,events,source_time):
        self.board=board;self.manager=manager;self.events=events;self._source_time=source_time
    def __deepcopy__(self,memo):
        from copy import deepcopy
        result=type(self).__new__(type(self));memo[id(self)]=result
        for key,value in vars(self).items():
            if key not in ('board','manager','events'):setattr(result,key,deepcopy(value,memo))
        return result
    def _load(self,spec):
        views={tf:select(self.board,spec.symbol,tf,REQUIRED_INDS) for tf in self._required_tfs(spec)}
        if any(v is None for v in views.values()):return None,[]
        levels=self.levels.get(views,spec.session_london,spec.session_newyork,spec.symbol)
        self._source_health[spec.watch_id]=health(views,REQUIRED_INDS)
        return views[spec.source_tf],filter_external_levels(levels,spec.levels)
    @staticmethod
    def _fingerprint(spec: SweepSpec) -> tuple:
        return (
            spec.symbol, spec.source_tf, spec.levels,
            spec.session_london, spec.session_newyork,
        )

    @staticmethod
    def _spec_payload(spec: SweepSpec) -> dict:
        return {
            "symbol": spec.symbol,
            "source_tf": spec.source_tf,
            "levels": list(spec.levels),
            "session_london": spec.session_london,
            "session_newyork": spec.session_newyork,
        }

    def _publish_event(self, spec: SweepSpec, event: dict) -> None:
        payload = {"strategy": "SWEEP", **event}
        self.manager.stream.prepare(payload)
        self.events.publish(payload)
        self.manager.send(payload)
        if event.get("kind") == "SWEEP_INVALIDATED":
            logging.info(
                "🧹 [외부유동성 해제] %s %s | %s %.5f | %s | time=%s",
                spec.symbol, spec.source_tf, event["level_code"], event["level_price"],
                event["direction"], event["event_time"],
            )
        else:
            logging.info(
                "📌 [외부유동성 SWEEP] %s %s | %s %.5f | %s | time=%s",
                spec.symbol, spec.source_tf, event["level_code"], event["level_price"],
                event["direction"], event["event_time"],
            )

    def _sync_restored_facts(self, detector: LiquidityDetector) -> None:
        watch_id = detector.spec.watch_id
        if watch_id not in self._restored_pending_sync:
            return
        active_events = detector.active_touch_events(restored=True)
        if not active_events:
            self._restored_pending_sync.discard(watch_id)
            return

        delivered = 0
        for event in active_events:
            # 재시작 후 KIM의 사실 저장소만 원래 발생시각으로 복구합니다.
            # monitor_OZ 쪽 이벤트 버스에는 다시 쓰지 않아 새로운 터치로 만들지 않습니다.
            payload = {"strategy": "SWEEP", **event}
            reply = self.manager.send(payload)
            if not isinstance(reply, dict) or not reply.get("ok"):
                logging.warning(
                    "[SWEEP 터치사실 동기화] KIM 응답 실패 - 다음 주기에 재시도 | %s",
                    watch_id,
                )
                return
            delivered += 1

        self._restored_pending_sync.discard(watch_id)
        logging.info(
            "♻️ [SWEEP 터치사실 동기화] %s | 살아있는 터치 %d건",
            watch_id, delivered,
        )

    def _invalidate_detector(self, detector: LiquidityDetector) -> None:
        for event in detector.invalidate_all(event_time=getattr(self, '_source_time', None)):
            self._publish_event(detector.spec, event)

    def _detector(self, spec: SweepSpec) -> LiquidityDetector:
        fp = self._fingerprint(spec)
        old_detector = self.detectors.get(spec.watch_id)
        if old_detector is None or self._fingerprints.get(spec.watch_id) != fp:
            if old_detector is not None:
                self._invalidate_detector(old_detector)
            self.detectors[spec.watch_id] = LiquidityDetector(spec)
            self._fingerprints[spec.watch_id] = fp
            self._restored_pending_sync.discard(spec.watch_id)
            self._state_dirty = True
        return self.detectors[spec.watch_id]

    @staticmethod
    def _required_tfs(spec: SweepSpec) -> list[str]:
        # 외부유동성 레벨 산출에 필요한 원본 TF입니다. source_tf는 sweep 판독용입니다.
        return list(dict.fromkeys([spec.source_tf, "1d", "4h", "8h", "5m"]))

    def run_spec_once(self, spec: SweepSpec):
        df_base, levels = self._load(spec)
        if df_base is None:
            return
        detector = self._detector(spec)
        events = detector.process(df_base, levels)
        for event in events:
            self._publish_event(spec, event)
        if detector.consume_dirty():
            self._state_dirty = True
        # 현재 레벨과 한 번 대조된 뒤에만 과거 터치 사실을 KIM에 복구합니다.
        # 시스템이 꺼진 동안 사라진 레벨을 잘못 되살리는 것을 막기 위함입니다.
        self._sync_restored_facts(detector)
        self.manager.send(self.manager.stream.snapshot(spec.symbol, spec.source_tf,
            [dict(event, strategy='SWEEP') for event in detector.active_touch_events(restored=True)],
            watch_id=spec.watch_id, source_health=self._source_health.get(spec.watch_id)))

    def run_query(self, payload: dict):
        query_payload = dict(payload)
        query_payload["watch_id"] = str(payload.get("watch_id") or payload.get("request_id") or "QUERY")
        try:
            spec = SweepSpec.from_payload(query_payload)
        except ValueError as exc:
            self.manager.send({"kind":"SWEEP_QUERY_RESULT","strategy":"SWEEP","ok":False,
                               "request_id":payload.get("request_id"),"request_chat_id":payload.get("request_chat_id"),
                               "error":str(exc),"levels":[]})
            return
        if spec is None:
            return
        df_base, levels = self._load(spec)
        event = {
            "kind": "SWEEP_QUERY_RESULT",
            "strategy": "SWEEP",
            "request_id": payload.get("request_id"),
            "request_chat_id": payload.get("request_chat_id"),
            "symbol": spec.symbol,
            "source_tf": spec.source_tf,
        }
        if df_base is None:
            event.update({"ok": False, "error": "sweep_data_unavailable", "levels": []})
        else:
            detector = LiquidityDetector(spec)
            # 조회는 현재 외부유동성 레벨 컨텍스트만 반환하며 과거 sweep을 재생하지 않습니다.
            snapshot = detector.snapshot(levels)
            event.update({
                "ok": True,
                "levels": snapshot["levels"],
            })
        self.manager.send(event)

    def cleanup(self, active_watch_ids: set[str]):
        changed = False
        for watch_id in list(self.detectors):
            if watch_id not in active_watch_ids:
                detector = self.detectors.pop(watch_id, None)
                self._fingerprints.pop(watch_id, None)
                self._restored_pending_sync.discard(watch_id)
                if detector is not None:
                    self._invalidate_detector(detector)
                    changed = True
        if changed:
            self._state_dirty = True
