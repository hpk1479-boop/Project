"""Persistent Watch/external-liquidity objects; unchanged judgment/try_fire.
State encoding occurs on explicit host/engine save. Input is an OZMarketView.
"""
from __future__ import annotations
import logging,threading
from typing import Optional,Iterable
from durable_protocol import identity
import oz_profiles
from oz_profile_loader import load_saved_oz
from .common import *
from .common import _profile_key,_resolve_oz_modes,_oz_profile_label
from .market import OZMarketView

class ExternalLiquidityController:
    """External-liquidity qualification owned by monitor_OZ.

    SWEEP contributes only fact events. ATR14 is read from Staff's ``atr_14`` column.
    The fixed 1.5 multiplier and all setup qualification state live here.
    """

    def __init__(self,clock,*,sweep_registry,initial=None):
        self.clock=clock;self._lock=threading.RLock();self._event_registry=sweep_registry
        self._specs={};self._states={};self._invalidated={};self.revision=0;self._fingerprint=None
        self._state_path='oz_external_liquidity_state.json';self._initial=initial

    @staticmethod
    def _norm_tf(value) -> str:
        return str(value or "").strip().lower()

    @staticmethod
    def _event_epoch(value):
        try:
            if isinstance(value,(int,float)) and not isinstance(value,bool):return float(value)
            return epoch(value)
        except (TypeError,ValueError,OverflowError):return None

    @staticmethod
    def _state_key(watch_id: str, direction: str, level_id: str) -> str:
        return "|".join((
            str(watch_id or "").strip(),
            str(direction or "").strip().upper(),
            str(level_id or "").strip(),
        ))

    def _states_for_watch_locked(
        self, watch_id: str, direction: Optional[str] = None
    ) -> list[ExternalLiquidityState]:
        wid = str(watch_id or "").strip()
        wanted_direction = str(direction or "").strip().upper()
        return [
            st for st in self._states.values()
            if st.watch_id == wid
            and st.direction in {"LONG", "SHORT"}
            and (wanted_direction not in {"LONG", "SHORT"} or st.direction == wanted_direction)
        ]

    def _state_for_direction_locked(self, watch_id: str, direction: str) -> Optional[ExternalLiquidityState]:
        direction = str(direction or "").strip().upper()
        if direction not in {"LONG", "SHORT"}:
            return None
        states = [
            st for st in self._states_for_watch_locked(watch_id, direction)
            if st.level_price is not None
        ]
        if not states:
            return None
        if direction == "LONG":
            # 실제 터치된 하단 유동성 중 가장 낮은 가격을 사용합니다.
            return min(states, key=lambda st: (float(st.level_price), -float(st.event_time or 0.0)))
        # 실제 터치된 상단 유동성 중 가장 높은 가격을 사용합니다.
        return max(states, key=lambda st: (float(st.level_price), float(st.event_time or 0.0)))

    def _load_state(self) -> None:
        if self._initial is None:
            return
        try:
            raw = self._initial
            specs: dict[str, ExternalLiquiditySpec] = {}
            states: dict[str, ExternalLiquidityState] = {}
            for wid, item in (raw.get("specs", {}) if isinstance(raw, dict) else {}).items():
                if not isinstance(item, dict):
                    continue
                symbol = str(item.get("symbol") or "").strip()
                source_tf = self._norm_tf(item.get("source_tf"))
                if wid and symbol and source_tf:
                    source_kind = str(item.get("source_kind") or "SWEEP").strip().upper()
                    manual_direction = str(item.get("manual_direction") or "").strip().upper() or None
                    if manual_direction not in {None, "LONG", "SHORT"}:
                        manual_direction = None
                    atr_period, atr_mult = atr_rule(item.get("atr_period"), item.get("atr_mult"))
                    specs[str(wid)] = ExternalLiquiditySpec(
                        str(wid), symbol, source_tf,
                        source_kind=source_kind,
                        manual_level_price=(
                            float(item["manual_level_price"])
                            if finite_number(item.get("manual_level_price")) else None
                        ),
                        manual_direction=manual_direction,
                        registered_at=self._event_epoch(item.get("registered_at")),
                        atr_period=atr_period, atr_mult=atr_mult,
                    )
            for stored_key, item in (raw.get("states", {}) if isinstance(raw, dict) else {}).items():
                if not isinstance(item, dict):
                    continue
                direction = str(item.get("direction") or "").upper() or None
                if direction not in {"LONG", "SHORT"}:
                    continue
                base_watch_id = str(item.get("watch_id") or "").strip()
                if not base_watch_id:
                    # 이전 저장 형식은 상태 사전의 키 자체가 감시 번호였습니다.
                    base_watch_id = str(stored_key).split("|", 1)[0]
                level_id = str(item.get("level_id") or "").strip()
                if not level_id:
                    # 이전 저장본에는 level_id가 없으므로 기존 상태를 잃지 않도록 임시 식별자를 만듭니다.
                    level_id = f"LEGACY:{item.get('level_code') or ''}:{item.get('level_price') or ''}"
                state_key = self._state_key(base_watch_id, direction, level_id)
                states[state_key] = ExternalLiquidityState(
                    watch_id=base_watch_id,
                    status=str(item.get("status") or "WAIT_SWEEP"),
                    direction=direction,
                    level_id=level_id,
                    level_code=str(item.get("level_code") or "") or None,
                    level_name=str(item.get("level_name") or "") or None,
                    level_price=float(item["level_price"]) if finite_number(item.get("level_price")) else None,
                    event_time=self._event_epoch(item.get("event_time")),
                    touch_high=float(item["touch_high"]) if finite_number(item.get("touch_high")) else None,
                    touch_low=float(item["touch_low"]) if finite_number(item.get("touch_low")) else None,
                    atr_snapshot=float(item["atr_snapshot"]) if finite_number(item.get("atr_snapshot")) else None,
                    max_distance=float(item["max_distance"]) if finite_number(item.get("max_distance")) else None,
                    reason=str(item.get("reason") or "") or None,
                )
            with self._lock:
                self._specs = specs
                self._states = states
                self._invalidated = {str(k): float(v) for k,v in raw.get('invalidated', {}).items()}
        except Exception:
            logging.exception("[OZ 외부유동성] 상태 복원 실패 | %s", self._state_path)

    def _current_sweep_registry(self,force=False):return dict(self._event_registry)

    def _bootstrap_sweep_registry(self) -> None:
        """재시작 시 OZ의 SWEEP spec을 현재 strategy_SWEEP registry와 정확히 동기화합니다."""
        current = self._current_sweep_registry(force=True)
        changed = False
        with self._lock:
            current_ids = set(current)

            # 이전 실행에서 남은 SWEEP spec/state는 현재 registry에 없으면 제거합니다.
            stale_ids = {
                wid for wid, spec in self._specs.items()
                if spec.source_kind != "MANUAL_LEVEL" and wid not in current_ids
            }
            if stale_ids:
                for wid in sorted(stale_ids):
                    self._specs.pop(wid, None)
                self._states = {
                    key: st for key, st in self._states.items()
                    if st.watch_id not in stale_ids
                }
                changed = True

            for wid, spec in current.items():
                if self._specs.get(wid) != spec:
                    self._specs[wid] = spec
                    changed = True

            if changed:
                self._save_locked()

    def export_payload(self):
        payload = {
            "version": 5,
            "invalidated": self._invalidated,
            "atr_period": EXTERNAL_ATR_PERIOD,
            "atr_multiplier": EXTERNAL_ATR_MULT,
            "specs": {
                wid: {
                    "symbol": spec.symbol,
                    "source_tf": spec.source_tf,
                    "source_kind": spec.source_kind,
                    "manual_level_price": spec.manual_level_price,
                    "manual_direction": spec.manual_direction,
                    "registered_at": spec.registered_at,
                    "atr_period": spec.atr_period,
                    "atr_mult": spec.atr_mult,
                }
                for wid, spec in self._specs.items()
            },
            "states": {
                state_key: {
                    "watch_id": st.watch_id,
                    "status": st.status,
                    "direction": st.direction,
                    "level_id": st.level_id,
                    "level_code": st.level_code,
                    "level_name": st.level_name,
                    "level_price": st.level_price,
                    "event_time": st.event_time,
                    "touch_high": st.touch_high,
                    "touch_low": st.touch_low,
                    "atr_snapshot": st.atr_snapshot,
                    "max_distance": st.max_distance,
                    "reason": st.reason,
                }
                for state_key, st in self._states.items()
            },
        }
        return payload

    def register(self, payload: dict) -> None:
        wid = str(payload.get("watch_id") or "").strip()
        symbol = str(payload.get("symbol") or "").strip()
        source_tf = self._norm_tf(
            payload.get("source_tf") or payload.get("setup_tf") or payload.get("external_source_tf")
        )
        if not wid or not symbol or not source_tf:
            return

        source_kind = str(payload.get("external_source_kind") or "").strip().upper()
        source_kind = "MANUAL_LEVEL" if source_kind == "MANUAL_LEVEL" else "SWEEP"
        manual_level = (
            float(payload.get("external_level_price"))
            if source_kind == "MANUAL_LEVEL" and finite_number(payload.get("external_level_price"))
            else None
        )
        if source_kind == "MANUAL_LEVEL" and manual_level is None:
            return

        manual_direction = str(payload.get("direction") or "").strip().upper() or None
        if manual_direction not in {None, "LONG", "SHORT"}:
            manual_direction = None
        registered_at = None
        if source_kind == "MANUAL_LEVEL":
            # command queue의 issued_at은 time_ns()이므로 수동 레벨의 epoch seconds로 사용하지 않습니다.
            registered_at = self._event_epoch(payload.get("external_registered_at"))
            if registered_at is None:
                registered_at = self.clock.seconds

        with self._lock:
            previous = self._specs.get(wid)
            if (
                previous is not None
                and previous.source_kind == "MANUAL_LEVEL"
                and source_kind == "MANUAL_LEVEL"
                and previous.symbol == symbol
                and previous.source_tf == source_tf
                and previous.manual_level_price == manual_level
            ):
                # 동일 수동 레벨의 중복 등록은 기존 시작시각/자동 판정 방향을 보존합니다.
                registered_at = previous.registered_at or registered_at
                if manual_direction is None:
                    manual_direction = previous.manual_direction

            # A repeated SWEEP_WATCH keeps the ATR rule its strategy declared.
            atr_period, atr_mult = atr_rule(payload.get("external_atr_period"), payload.get("external_atr_mult"))
            spec = ExternalLiquiditySpec(
                wid, symbol, source_tf,
                source_kind=source_kind,
                manual_level_price=manual_level,
                manual_direction=manual_direction,
                registered_at=registered_at,
                atr_period=atr_period, atr_mult=atr_mult,
            )
            self._specs[wid] = spec

            identity_changed = (
                previous is not None
                and (
                    previous.symbol != spec.symbol
                    or previous.source_tf != spec.source_tf
                    or previous.source_kind != spec.source_kind
                    or previous.manual_level_price != spec.manual_level_price
                )
            )
            if identity_changed:
                self._states = {
                    key: st for key, st in self._states.items()
                    if st.watch_id != wid
                }
            self._save_locked()

        if source_kind == "MANUAL_LEVEL":
            if logging.getLogger().isEnabledFor(logging.INFO):
                logging.info(
                    "🌐 [OZ 수동 외부유동성 Gate 등록] %s | %s %s | level=%.6f | direction=%s",
                    wid, symbol, source_tf, float(manual_level), manual_direction or "AUTO",
                )
        else:
            if logging.getLogger().isEnabledFor(logging.INFO):
                logging.info("🌐 [OZ 외부유동성 Gate 등록] %s | %s %s", wid, symbol, source_tf)

    def is_manual_level(self, watch_id: str) -> bool:
        with self._lock:
            spec = self._specs.get(str(watch_id or "").strip())
            return bool(spec is not None and spec.source_kind == "MANUAL_LEVEL")

    def cancel(self, watch_id: str) -> None:
        wid = str(watch_id or "").strip()
        if not wid:
            return
        with self._lock:
            removed = self._specs.pop(wid, None)
            before = len(self._states)
            self._states = {
                key: st for key, st in self._states.items()
                if st.watch_id != wid
            }
            if removed is not None or len(self._states) != before:
                self._save_locked()

    def reset(self) -> None:
        with self._lock:
            if not self._specs and not self._states:
                return
            self._specs.clear()
            self._states.clear()
            self._save_locked()

    def has_spec(self, watch_id: str) -> bool:
        with self._lock:
            return str(watch_id or "") in self._specs

    def spec(self, watch_id: str) -> Optional[ExternalLiquiditySpec]:
        with self._lock:
            return self._specs.get(str(watch_id or ""))

    def status(self, watch_id: str, direction: Optional[str] = None) -> str:
        with self._lock:
            if direction in {"LONG", "SHORT"}:
                st = self._state_for_direction_locked(watch_id, direction)
                return st.status if st is not None else "WAIT_SWEEP"
            states = self._states_for_watch_locked(watch_id)
            if not states:
                return "WAIT_SWEEP"
            latest = max(states, key=lambda x: float(x.event_time or 0.0))
            return latest.status

    def state(self, watch_id: str, direction: Optional[str] = None) -> Optional[ExternalLiquidityState]:
        with self._lock:
            if direction in {"LONG", "SHORT"}:
                st = self._state_for_direction_locked(watch_id, direction)
            else:
                states = self._states_for_watch_locked(watch_id)
                st = max(states, key=lambda x: float(x.event_time or 0.0)) if states else None
            if st is None:
                return None
            return ExternalLiquidityState(**st.__dict__)

    def matching_ids(self, symbol: Optional[str], source_tf: Optional[str] = None) -> list[str]:
        symbol = str(symbol or "").strip()
        source_tf = self._norm_tf(source_tf)
        with self._lock:
            out = []
            for wid, spec in self._specs.items():
                if symbol and spec.symbol != symbol:
                    continue
                if source_tf and spec.source_tf != source_tf:
                    continue
                out.append(wid)
            return out

    def apply_event(self, payload: dict) -> None:
        kind = str(payload.get("kind") or "").upper()
        if kind not in {"SWEEP_TOUCH", "SWEEP_INVALIDATED"}:
            return
        wid = str(payload.get("watch_id") or "").strip()
        symbol = str(payload.get("symbol") or "").strip()
        source_tf = self._norm_tf(payload.get("source_tf"))
        direction = str(payload.get("direction") or "").strip().upper()
        level_id = str(payload.get("level_id") or "").strip()
        event_time = self._event_epoch(payload.get("event_time") or payload.get("touch_time"))
        if (
            not wid or not symbol or not source_tf or not level_id
            or direction not in {"LONG", "SHORT"} or event_time is None
        ):
            return

        # SWEEP event는 현재 strategy_SWEEP registry에 살아 있는 watch만 처리합니다.
        # 과거 sweep_event.jsonl replay가 이미 끝난 watch를 OZ에 부활시키지 못하게 합니다.
        current = self._current_sweep_registry()
        current_spec = current.get(wid)
        if current_spec is None:
            with self._lock:
                stale = self._specs.get(wid)
                if stale is not None and stale.source_kind != "MANUAL_LEVEL":
                    self._specs.pop(wid, None)
                    self._states = {
                        key: st for key, st in self._states.items()
                        if st.watch_id != wid
                    }
                    self._save_locked()
            return
        if current_spec.symbol != symbol or current_spec.source_tf != source_tf:
            return

        with self._lock:
            existing = self._specs.get(wid)
            if existing is not None and existing.source_kind == "MANUAL_LEVEL":
                return
            if existing != current_spec:
                self._specs[wid] = current_spec
            state_key = self._state_key(wid, direction, level_id)

            if kind == "SWEEP_INVALIDATED":
                prev = self._states.get(state_key)
                if prev is not None and prev.event_time is not None and event_time < prev.event_time:
                    return
                self._invalidated[state_key] = max(event_time, self._invalidated.get(state_key, float('-inf')))
                self._states.pop(state_key, None)
                self._save_locked()
                if logging.getLogger().isEnabledFor(logging.INFO):
                    logging.info(
                        "🧹 [OZ 외부유동성 해제] %s | %s %s | %s | %s",
                        wid, symbol, source_tf, direction, level_id,
                    )
                return

            if not finite_number(payload.get("level_price")):
                return
            if event_time <= self._invalidated.get(state_key, float('-inf')):
                return
            prev = self._states.get(state_key)
            if prev is not None and prev.event_time is not None and event_time <= prev.event_time:
                return
            self._states[state_key] = ExternalLiquidityState(
                watch_id=wid,
                status="PENDING_ATR",
                direction=direction,
                level_id=level_id,
                level_code=str(payload.get("level_code") or "") or None,
                level_name=str(payload.get("level_name") or "") or None,
                level_price=float(payload["level_price"]),
                event_time=event_time,
                touch_high=float(payload["touch_high"]) if finite_number(payload.get("touch_high")) else None,
                touch_low=float(payload["touch_low"]) if finite_number(payload.get("touch_low")) else None,
            )
            self._save_locked()
        if logging.getLogger().isEnabledFor(logging.INFO):
            logging.info(
                "📥 [OZ 외부유동성 SWEEP 수신] %s | %s %s | %s %.6f | %s | %s",
                wid, symbol, source_tf, direction, float(payload["level_price"]), level_id, event_time,
            )

    @staticmethod
    def _row_epochs(view):return view.time

    def _register_manual_touch_locked(self, spec: ExternalLiquiditySpec, df: OZMarketView) -> bool:
        """Create the same PENDING_ATR state that a SWEEP_TOUCH event would create.

        A user supplied price is a synthetic external-liquidity level. It never enters
        strategy_SWEEP; monitor_OZ observes the source TF and feeds the existing ATR gate.
        """
        if spec.source_kind != "MANUAL_LEVEL" or not finite_number(spec.manual_level_price):
            return False
        if self._states_for_watch_locked(spec.watch_id):
            return False
        if df is None or df.empty:
            return False

        row = df.row(-1)
        level = float(spec.manual_level_price)
        changed = False

        # live bar의 high/low는 봉 시작 이후 누적값이므로, Watch 등록이 현재 봉 시작보다
        # 늦었다면 그 봉 안에서 "등록 전 터치"와 "등록 후 터치"를 구분할 수 없습니다.
        # 소급 오탐을 막기 위해 등록 이전에 이미 시작된 봉 전체를 제외하고,
        # 등록시각 이후에 새로 시작한 첫 봉부터 수동 레벨 터치를 인정합니다.
        event_time = self._event_epoch(row.get("time"))
        if event_time is None:
            return False
        if spec.registered_at is not None and event_time < float(spec.registered_at):
            return False

        direction = spec.manual_direction if spec.manual_direction in {"LONG", "SHORT"} else None
        if direction is None:
            # 접근 방향은 직전 종가를 우선 사용합니다. 현재 봉에서 이미 레벨을 관통한 경우에도
            # 봉 시작 전 어느 쪽에서 접근했는지 보존하기 위해서입니다.
            refs = []
            if len(df) >= 2:
                refs.append(df.row(-2).get("close"))
            refs.extend((row.get("open"), row.get("close")))
            for raw in refs:
                if not finite_number(raw):
                    continue
                ref = float(raw)
                if ref > level:
                    direction = "LONG"
                    break
                if ref < level:
                    direction = "SHORT"
                    break
            if direction is None:
                return False
            spec.manual_direction = direction
            changed = True

        row_low = float(row.get("low")) if finite_number(row.get("low")) else None
        row_high = float(row.get("high")) if finite_number(row.get("high")) else None

        touched = (
            direction == "LONG" and row_low is not None and row_low <= level
        ) or (
            direction == "SHORT" and row_high is not None and row_high >= level
        )
        if not touched:
            return changed

        level_id = f"MANUAL:{level:.12g}"
        state_key = self._state_key(spec.watch_id, direction, level_id)
        self._states[state_key] = ExternalLiquidityState(
            watch_id=spec.watch_id,
            status="PENDING_ATR",
            direction=direction,
            level_id=level_id,
            level_code="MANUAL_PRICE",
            level_name=f"수동 가격 {level:g}",
            level_price=level,
            event_time=event_time,
            touch_high=row_high,
            touch_low=row_low,
        )
        if logging.getLogger().isEnabledFor(logging.INFO):
            logging.info(
                "📥 [OZ 수동 외부유동성 터치] %s | %s %s | %s %.6f | %s",
                spec.watch_id, spec.symbol, spec.source_tf, direction, level, event_time,
            )
        return True

    def source_timeframes(self, watch_ids: Iterable[str]) -> set[str]:
        ids = {str(x) for x in watch_ids if str(x)}
        with self._lock:
            out: set[str] = set()
            for wid in sorted(ids):
                spec = self._specs.get(wid)
                if spec is None:
                    continue
                states = self._states_for_watch_locked(wid)
                # 수동 가격 레벨은 SWEEP 이벤트가 따로 오지 않으므로 최초 터치를 잡기 전부터
                # source TF를 계속 받아야 합니다. 터치 후에는 기존 PENDING/ACTIVE ATR 감시를 그대로 사용합니다.
                if spec.source_kind == "MANUAL_LEVEL" and not states:
                    out.add(spec.source_tf)
                    continue
                if any(st.status in {"PENDING_ATR", "ACTIVE"} for st in states):
                    out.add(spec.source_tf)
            return out

    def update_market(self, symbol: str, data: dict[str, OZMarketView], watch_ids: Iterable[str]) -> None:
        ids = {str(x) for x in watch_ids if str(x)}
        changed = False
        with self._lock:
            for wid in sorted(ids):
                spec = self._specs.get(wid)
                if spec is None or spec.symbol != symbol:
                    continue
                df = data.get(spec.source_tf)
                if df is None or df.empty or "time" not in df.columns or EXTERNAL_ATR_COLUMN not in df.columns:
                    continue

                if self._register_manual_touch_locked(spec, df):
                    changed = True

                for st in self._states_for_watch_locked(wid):
                    if st.status not in {"PENDING_ATR", "ACTIVE"}:
                        continue

                    if st.status == "PENDING_ATR":
                        if st.event_time is None or st.level_price is None or st.direction not in {"LONG", "SHORT"}:
                            continue
                        idx = df.nearest_index(float(st.event_time),tolerance=.5)
                        if idx is None:
                            continue
                        row = df.row(idx)
                        # The strategy's own ATR rule; ATR14 x 1.5 unless it asked otherwise.
                        atr = row.get(EXTERNAL_ATR_COLUMN) if spec.atr_period == EXTERNAL_ATR_PERIOD else float(df.atr_series(spec.atr_period)[idx])
                        if not finite_number(atr) or float(atr) <= 0.0:
                            continue
                        st.atr_snapshot = float(atr)
                        st.max_distance = float(atr) * spec.atr_mult
                        level = float(st.level_price)
                        row_low = float(row.get("low")) if finite_number(row.get("low")) else None
                        row_high = float(row.get("high")) if finite_number(row.get("high")) else None
                        touch_low = min(x for x in (row_low, st.touch_low) if x is not None) if (row_low is not None or st.touch_low is not None) else None
                        touch_high = max(x for x in (row_high, st.touch_high) if x is not None) if (row_high is not None or st.touch_high is not None) else None
                        exceeded = (
                            st.direction == "LONG" and touch_low is not None and touch_low < level - st.max_distance
                        ) or (
                            st.direction == "SHORT" and touch_high is not None and touch_high > level + st.max_distance
                        )
                        if exceeded:
                            st.status = "INVALID"
                            st.reason = "ATR_1P5_FIRST_CHECK"
                            if logging.getLogger().isEnabledFor(logging.INFO):
                                logging.info(
                                    "🛑 [외부유동성 1차 탈락] %s | %s %s | level=%.6f ATR%d=%.6f x%g",
                                    wid, spec.symbol, spec.source_tf, level, spec.atr_period, st.atr_snapshot, spec.atr_mult,
                                )
                        else:
                            st.status = "ACTIVE"
                            st.reason = None
                            if logging.getLogger().isEnabledFor(logging.INFO):
                                logging.info(
                                    "✅ [외부유동성 1차 통과] %s | %s %s | %s %.6f | ATR%d=%.6f x%g",
                                    wid, spec.symbol, spec.source_tf, st.direction, level, spec.atr_period, st.atr_snapshot, spec.atr_mult,
                                )
                        changed = True

                    if st.status == "ACTIVE":
                        if st.max_distance is None or st.level_price is None:
                            continue
                        level = float(st.level_price)
                        if st.direction == "LONG":
                            low = df.extreme_since(float(st.event_time or 0.0),'low')
                            breached = low is not None and low < level - float(st.max_distance)
                        else:
                            high = df.extreme_since(float(st.event_time or 0.0),'high')
                            breached = high is not None and high > level + float(st.max_distance)
                        if breached:
                            st.status = "INVALID"
                            st.reason = "ATR_1P5_SURVIVAL"
                            changed = True
                            if logging.getLogger().isEnabledFor(logging.INFO):
                                logging.info(
                                    "🛑 [외부유동성 생존 탈락] %s | %s %s | %s %.6f | ATR%d=%.6f x%g",
                                    wid, spec.symbol, spec.source_tf, st.direction, level, spec.atr_period, float(st.atr_snapshot or 0.0), spec.atr_mult,
                                )
            if changed:
                self._save_locked()

    def validate_true_b0(self, watch_id: str, direction: str, true_b0_price: float) -> bool:
        wid = str(watch_id or "").strip()
        direction = str(direction or "").upper()
        if direction not in {"LONG", "SHORT"} or not finite_number(true_b0_price):
            return False
        with self._lock:
            st = self._state_for_direction_locked(wid, direction)
            spec = self._specs.get(wid)
            if st is None or spec is None or st.direction != direction:
                return False
            if st.status not in {"ACTIVE", "CONFIRMED"} or st.level_price is None or st.max_distance is None:
                return False
            distance = abs(float(true_b0_price) - float(st.level_price))
            if distance <= float(st.max_distance):
                st.status = "CONFIRMED"
                st.reason = None
                self._save_locked()
                if logging.getLogger().isEnabledFor(logging.INFO):
                    logging.info(
                        "✅ [외부유동성 2차 통과] %s | %s %s | level=%.6f TRUE_B0=%.6f dist=%.6f <= %.6f",
                        wid, spec.symbol, spec.source_tf, float(st.level_price), float(true_b0_price),
                        distance, float(st.max_distance),
                    )
                return True
            st.status = "INVALID"
            st.reason = "ATR_1P5_TRUE_B0_DISTANCE"
            self._save_locked()
            if logging.getLogger().isEnabledFor(logging.INFO):
                logging.info(
                    "🛑 [외부유동성 2차 탈락] %s | %s %s | level=%.6f TRUE_B0=%.6f dist=%.6f > %.6f",
                    wid, spec.symbol, spec.source_tf, float(st.level_price), float(true_b0_price),
                    distance, float(st.max_distance),
                )
            return False

    def watch_signature(self,wid):
        spec=self._specs.get(wid)
        states=tuple(sorted((key,tuple(vars(value).items())) for key,value in self._states.items() if value.watch_id==wid))
        return (None if spec is None else tuple(vars(spec).items()),states,self._invalidated.get(wid))

    def _save_locked(self):
        # Dirty bookkeeping uses scalar tuples, not JSON/profile serialization.
        signature=(tuple(sorted((key,tuple(vars(value).items())) for key,value in self._specs.items())),
                   tuple(sorted((key,tuple(vars(value).items())) for key,value in self._states.items())),tuple(sorted(self._invalidated.items())))
        if signature!=self._fingerprint:self.revision+=1;self._fingerprint=signature


class OZWatchController:
    """On-demand OZ watches keyed by validation mode and final-trigger mode."""

    def __init__(self,telegram,external,clock,*,initial=None):
        self.telegram=telegram;self.external=external;self.clock=clock;self._lock=threading.RLock()
        self._watches={};self._revision={_profile_key(*p):0 for p in PROFILE_KEYS}
        self._state_path='oz_manual_watch_state.json';self._initial=initial;self.dirty=False;self.rejected_watches=[]

    def _load_state(self) -> None:
        if self._initial is None:
            return
        try:
            raw = self._initial
            items = raw.get("watches", []) if isinstance(raw, dict) else []
            self.rejected_watches = list(raw.get("rejected_watches", [])) if isinstance(raw, dict) else []
            restored: dict[str, WatchSpec] = {}
            for item in items:
                if not isinstance(item, dict):
                    continue
                watch_id = str(item.get("watch_id") or "").strip()
                source = str(item.get("source") or "MANUAL").strip() or "MANUAL"
                requested = {str(x).strip().lower() for x in item.get("timeframes", [])}
                ordered = tuple(tf for tf in TF_MAP if tf in requested)
                symbol = str(item.get("symbol") or "").strip() or None
                direction = str(item.get("direction") or "").upper() or None
                request_chat_id = str(item.get("request_chat_id") or "").strip() or None
                external_watch_id = str(item.get("external_watch_id") or "").strip() or None
                external_source_tf = str(item.get("external_source_tf") or "").strip().lower() or None
                source_spec_id = str(item.get("source_spec_id") or "").strip() or None
                source_name = str(item.get("source_name") or "").strip() or None
                try:
                    canonical = load_saved_oz(item, path=f"watches.{watch_id}")
                    validation_mode, trigger_mode = canonical['validation_mode'], canonical['trigger_mode']
                except oz_profiles.ProfileError as exc:
                    if not hasattr(self, 'rejected_watches'):
                        self.rejected_watches = []
                    self.rejected_watches.append({'watch_id': watch_id, 'reason': str(exc), 'original': dict(item)})
                    logging.error('[OZ 복원 격리] watch_id=%s | %s', watch_id, exc)
                    continue
                if not watch_id or not ordered or direction not in {None, "LONG", "SHORT"}:
                    continue
                restored[watch_id] = WatchSpec(
                    watch_id=watch_id,
                    watch_owner=str(item.get("watch_owner") or ("KIM" if source_spec_id else "OZ")),
                    source=source,
                    timeframes=ordered,
                    symbol=symbol,
                    direction=direction,
                    persistent=bool(item.get("persistent", False)),
                    request_chat_id=request_chat_id,
                    external_watch_id=external_watch_id,
                    external_source_tf=external_source_tf,
                    source_spec_id=source_spec_id,
                    source_name=source_name,
                    validation_mode=validation_mode,
                    trigger_mode=trigger_mode,
                )
            with self._lock:
                self._watches = restored
                for vm, tm in PROFILE_KEYS:
                    if any(
                        w.validation_mode == vm and w.trigger_mode == tm
                        for w in restored.values()
                    ):
                        self._revision[_profile_key(vm, tm)] += 1
            if restored:
                if logging.getLogger().isEnabledFor(logging.INFO):
                    logging.info("♻️ [OZ Watch 복원] %d건 | %s", len(restored), self._state_path)
        except Exception:
            logging.exception("[OZ Watch] 상태 복원 실패 | %s", self._state_path)

    def export_payload(self):
        payload = {
            "version": 3,
            "rejected_watches": list(self.rejected_watches),
            "watches": [
                {
                    "watch_id": w.watch_id,
                    "source": w.source,
                    "watch_owner": w.watch_owner,
                    "timeframes": list(w.timeframes),
                    "symbol": w.symbol,
                    "direction": w.direction,
                    "persistent": w.persistent,
                    "request_chat_id": w.request_chat_id,
                    "external_watch_id": w.external_watch_id,
                    "external_source_tf": w.external_source_tf,
                    "source_spec_id": w.source_spec_id,
                    "source_name": w.source_name,
                    "validation_mode": w.validation_mode,
                    "trigger_mode": w.trigger_mode,
                }
                for w in self._watches.values()
            ],
        }
        return payload

    def _bump(self, validation_mode: str, trigger_mode: str) -> None:
        vm, tm = _resolve_oz_modes(validation_mode, trigger_mode)
        key = _profile_key(vm, tm)
        self._revision[key] = self._revision.get(key, 0) + 1

    def register_external(self, payload: dict) -> None:
        self.external.register(payload)

    def cancel_external(self, watch_id: str) -> None:
        self.external.cancel(watch_id)

    def reset_external(self) -> None:
        self.external.reset()

    def apply_external_event(self, payload: dict) -> None:
        self.external.apply_event(payload)

    def _external_id_for_watch_locked(self, w: WatchSpec) -> Optional[str]:
        if w.external_watch_id:
            return w.external_watch_id
        if self.external.has_spec(w.watch_id):
            return w.watch_id
        if w.external_source_tf:
            matches = self.external.matching_ids(w.symbol, w.external_source_tf)
            if len(matches) == 1:
                return matches[0]
        return None

    def _profile_watches_locked(
        self, symbol: str, validation_mode: str, trigger_mode: str, tf: Optional[str] = None
    ) -> list[WatchSpec]:
        vm, tm = _resolve_oz_modes(validation_mode, trigger_mode)
        out: list[WatchSpec] = []
        for w in self._watches.values():
            if w.validation_mode != vm or w.trigger_mode != tm:
                continue
            if w.symbol is not None and w.symbol != symbol:
                continue
            if tf is not None and tf not in w.timeframes:
                continue
            out.append(w)
        return out

    def external_watch_ids_for_profile(
        self, symbol: str, validation_mode: str, trigger_mode: str
    ) -> set[str]:
        with self._lock:
            out: set[str] = set()
            for w in self._profile_watches_locked(symbol, validation_mode, trigger_mode):
                ext_id = self._external_id_for_watch_locked(w)
                if ext_id:
                    out.add(ext_id)
            return out

    def external_source_tfs_for_profile(
        self, symbol: str, validation_mode: str, trigger_mode: str
    ) -> set[str]:
        ids = self.external_watch_ids_for_profile(symbol, validation_mode, trigger_mode)
        return self.external.source_timeframes(ids)

    def _remove_invalid_manual_one_shots(self, external_ids: Iterable[str]) -> None:
        """Remove dead one-shot personal watches after their manual liquidity gate is INVALID."""
        target_ids = {str(x) for x in external_ids if str(x)}
        if not target_ids:
            return

        removed_external_ids: set[str] = set()
        affected_profiles: set[tuple[str, str]] = set()
        removed_watch_ids: list[str] = []
        with self._lock:
            for key, w in list(self._watches.items()):
                if w.source != "MANUAL" or w.persistent:
                    continue
                ext_id = self._external_id_for_watch_locked(w)
                if ext_id is None or ext_id not in target_ids:
                    continue
                if not self.external.is_manual_level(ext_id):
                    continue
                status = self.external.status(
                    ext_id, w.direction if w.direction in {"LONG", "SHORT"} else None
                )
                if status != "INVALID":
                    continue
                self._watches.pop(key, None)
                removed_watch_ids.append(w.watch_id)
                removed_external_ids.add(ext_id)
                affected_profiles.add((w.validation_mode, w.trigger_mode))

            if removed_watch_ids:
                for vm, tm in sorted(affected_profiles):
                    self._bump(vm, tm)
                self._save_state_locked()

        for ext_id in removed_external_ids:
            self.external.cancel(ext_id)

        for watch_id in removed_watch_ids:
            if logging.getLogger().isEnabledFor(logging.INFO):
                logging.info(
                    "🧹 [OZ 수동 Watch 자동정리] %s | 수동 외부유동성 ATR 자격 탈락",
                    watch_id,
                )

    def update_external_market(
        self, symbol: str, validation_mode: str, trigger_mode: str, data: dict[str, OZMarketView]
    ) -> None:
        ids = self.external_watch_ids_for_profile(symbol, validation_mode, trigger_mode)
        if ids:
            self.external.update_market(symbol, data, ids)
            self._remove_invalid_manual_one_shots(ids)

    def allowed_directions(
        self, symbol: str, tf: str, validation_mode: str, trigger_mode: str
    ) -> set[str]:
        allowed: set[str] = set()
        with self._lock:
            watches = self._profile_watches_locked(symbol, validation_mode, trigger_mode, tf=tf)
            for w in watches:
                requested = {w.direction} if w.direction in {"LONG", "SHORT"} else {"LONG", "SHORT"}
                ext_id = self._external_id_for_watch_locked(w)
                if ext_id is None:
                    allowed.update(requested)
                    continue
                spec = self.external.spec(ext_id)
                if spec is None:
                    continue
                for requested_direction in requested:
                    st = self.external.state(ext_id, requested_direction)
                    if st is None or st.direction != requested_direction:
                        continue
                    # Source/setup TF may build OUT→IN + HMA structure after the 1st ATR pass.
                    # Derived lower-TF OZ watches stay blocked until the source setup passes
                    # the finalized TRUE B0 distance check and becomes CONFIRMED.
                    if st.status == "CONFIRMED":
                        allowed.add(requested_direction)
                    elif st.status == "ACTIVE" and tf == spec.source_tf:
                        allowed.add(requested_direction)
        return allowed

    def allowed_environment_identities(
        self, symbol: str, tf: str, direction: str, validation_mode: str, trigger_mode: str
    ) -> frozenset[tuple[str, Optional[str], Optional[str], Optional[float]]]:
        """Return identities for the environment states that currently make this direction eligible.

        Non-external watches keep a stable Watch identity. External watches include the exact
        liquidity state selected by the existing ``external.state()`` rule, so a new SWEEP cycle
        is treated as fresh only when that selected environment state itself changes.
        """
        direction = str(direction or "").upper()
        if direction not in {"LONG", "SHORT"}:
            return frozenset()

        eligible: set[tuple[str, Optional[str], Optional[str], Optional[float]]] = set()
        with self._lock:
            watches = self._profile_watches_locked(symbol, validation_mode, trigger_mode, tf=tf)
            for w in watches:
                if w.direction is not None and w.direction != direction:
                    continue
                ext_id = self._external_id_for_watch_locked(w)
                if ext_id is None:
                    eligible.add((w.watch_id, None, None, None))
                    continue
                spec = self.external.spec(ext_id)
                if spec is None:
                    continue
                st = self.external.state(ext_id, direction)
                if st is None or st.direction != direction:
                    continue
                if st.status == "CONFIRMED" or (st.status == "ACTIVE" and tf == spec.source_tf):
                    eligible.add((w.watch_id, ext_id, st.level_id, st.event_time))
        return frozenset(eligible)

    def validate_external_true_b0(
        self, symbol: str, tf: str, direction: str, validation_mode: str, trigger_mode: str, true_b0_price: float
    ) -> bool:
        """Second ATR qualification. Non-external/manual watches preserve existing behavior."""
        with self._lock:
            watches = self._profile_watches_locked(symbol, validation_mode, trigger_mode, tf=tf)
            relevant = [
                w for w in watches
                if w.direction is None or w.direction == direction
            ]
            if not relevant:
                return False
            linked: list[str] = []
            non_external_allowed = False
            for w in relevant:
                ext_id = self._external_id_for_watch_locked(w)
                if ext_id is None:
                    non_external_allowed = True
                    continue
                linked.append(ext_id)

        passed = non_external_allowed
        for ext_id in dict.fromkeys(linked):
            st = self.external.state(ext_id, direction)
            spec = self.external.spec(ext_id)
            if st is None or spec is None or st.direction != direction:
                continue
            # Only the external-liquidity source/setup TF is allowed to perform
            # the 2nd ATR qualification. Derived lower TFs can run only after it.
            if tf != spec.source_tf:
                if st.status == "CONFIRMED":
                    passed = True
                continue
            if st.status in {"ACTIVE", "CONFIRMED"} and self.external.validate_true_b0(
                ext_id, direction, true_b0_price
            ):
                passed = True
        return passed

    def add_manual(
        self,
        timeframes: Iterable[str],
        issued_at=None,
        symbol: Optional[str] = None,
        direction: Optional[str] = None,
        persistent: bool = False,
        request_chat_id: Optional[str] = None,
        validation_mode: Optional[str] = None,
        trigger_mode: Optional[str] = None,
        oz_mode: Optional[str] = None,
        watch_id: Optional[str] = None,
        external_watch_id: Optional[str] = None,
        external_source_tf: Optional[str] = None,
        external_required: bool = False,
        source_spec_id: Optional[str] = None,
        source_name: Optional[str] = None,
        watch_owner: str = "OZ",
    ) -> None:
        ordered = tuple(
            tf for tf in TF_MAP
            if tf in set(str(x).lower() for x in timeframes)
        )
        if not ordered:
            return

        symbol = str(symbol or "").strip() or None
        direction = str(direction or "").upper() or None
        request_chat_id = str(request_chat_id or "").strip() or None
        external_watch_id = str(external_watch_id or "").strip() or None
        external_source_tf = str(external_source_tf or "").strip().lower() or None
        source_spec_id = str(source_spec_id or "").strip() or None
        source_name = str(source_name or "").strip() or None
        validation_mode, trigger_mode = _resolve_oz_modes(
            validation_mode, trigger_mode, oz_mode
        )
        if direction not in {None, "LONG", "SHORT"}:
            return

        explicit_wid = str(watch_id or "").strip()
        chain_scoped_wid = explicit_wid.startswith("OZARM:CHAIN:")
        replaced_manual_external_ids: set[str] = set()

        with self._lock:
            if chain_scoped_wid and explicit_wid in self._watches:
                return
            # One-shot replacement is scoped to the same owner and same OZ profile only.
            if not persistent:
                for key in [
                    k for k, w in self._watches.items()
                    if (
                        w.source == "MANUAL"
                        and not w.persistent
                        and w.request_chat_id == request_chat_id
                        and w.validation_mode == validation_mode
                        and w.trigger_mode == trigger_mode
                    )
                ]:
                    old_watch = self._watches.pop(key, None)
                    if old_watch is not None and old_watch.external_watch_id:
                        replaced_manual_external_ids.add(old_watch.external_watch_id)

            if persistent and not chain_scoped_wid:
                for w in self._watches.values():
                    if (
                        w.source == "MANUAL"
                        and w.persistent
                        and (not explicit_wid or w.watch_id == explicit_wid)
                        and w.timeframes == ordered
                        and w.symbol == symbol
                        and w.direction == direction
                        and w.request_chat_id == request_chat_id
                        and w.validation_mode == validation_mode
                        and w.trigger_mode == trigger_mode
                        and w.external_watch_id == external_watch_id
                        and w.external_source_tf == external_source_tf
                    ):
                        if logging.getLogger().isEnabledFor(logging.INFO):
                            logging.info(
                                "ℹ️ [OZ 지속 Watch] 동일 감시 이미 존재 | validation=%s trigger=%s TF=%s symbol=%s direction=%s",
                                validation_mode, trigger_mode, ",".join(ordered), symbol or "*", direction or "BOTH",
                            )
                        return

            wid = explicit_wid
            if not wid:
                wid = (
                    f"MANUAL:{validation_mode}:{trigger_mode}:"
                    f"{'P' if persistent else 'ONE'}:{issued_at or self.clock.identity_ns()}"
                )
            if external_required and external_watch_id is None:
                external_watch_id = wid
            self._watches[wid] = WatchSpec(
                watch_id=wid,
                watch_owner=str(watch_owner or "OZ"),
                source="MANUAL",
                timeframes=ordered,
                symbol=symbol,
                direction=direction,
                persistent=bool(persistent),
                request_chat_id=request_chat_id,
                external_watch_id=external_watch_id,
                external_source_tf=external_source_tf,
                source_spec_id=source_spec_id,
                source_name=source_name,
                validation_mode=validation_mode,
                trigger_mode=trigger_mode,
            )
            self._bump(validation_mode, trigger_mode)
            self._save_state_locked()

        for ext_id in replaced_manual_external_ids:
            if ext_id != external_watch_id and self.external.is_manual_level(ext_id):
                self.external.cancel(ext_id)

        labels = "·".join(TF_LABELS.get(tf, tf) for tf in ordered)
        scope = symbol or "XAUUSD"
        side = " LONG" if direction == "LONG" else " SHORT" if direction == "SHORT" else ""
        persistence_label = " 지속" if persistent else ""
        oz_label = _oz_profile_label(validation_mode, trigger_mode)
        external_label = ""
        if external_watch_id:
            ext_spec = self.external.spec(external_watch_id)
            if (
                ext_spec is not None
                and ext_spec.source_kind == "MANUAL_LEVEL"
                and finite_number(ext_spec.manual_level_price)
            ):
                external_label = f" · 외부유동성 {float(ext_spec.manual_level_price):g} ATR×{EXTERNAL_ATR_MULT:g}"

        self.telegram.send(
            f"✅ {scope} · {labels} {oz_label}{side}{persistence_label}{external_label} 감시",
            kind="CONTROL_ACK",
            require_delivery=True,
            request_chat_id=request_chat_id,
            watch_id=wid, watch_event="REGISTERED",
            validation_mode=validation_mode,
            trigger_mode=trigger_mode,
        )
        if logging.getLogger().isEnabledFor(logging.INFO):
            logging.info(
                "🟣 [OZ 수동 Watch] validation=%s trigger=%s persistent=%s TF=%s symbol=%s direction=%s watch_id=%s",
                validation_mode, trigger_mode, persistent,
                ",".join(ordered), symbol or "*", direction or "BOTH", wid,
            )

    def cancel_manual(
        self,
        timeframes: Iterable[str] = (),
        symbol: Optional[str] = None,
        direction: Optional[str] = None,
        persistent_only: bool = False,
        request_chat_id: Optional[str] = None,
        validation_mode: Optional[str] = None,
        trigger_mode: Optional[str] = None,
        oz_mode: Optional[str] = None,
        watch_id: Optional[str] = None,
        reply_cancel: bool = False,
    ) -> None:
        tf_filter = {
            str(x).lower()
            for x in timeframes
            if str(x).lower() in TF_MAP
        }
        symbol = str(symbol or "").strip() or None
        direction = str(direction or "").upper() or None
        request_chat_id = str(request_chat_id or "").strip() or None
        watch_id = str(watch_id or "").strip() or None

        filter_vm = str(validation_mode or "").strip().upper() or None
        filter_tm = str(trigger_mode or "").strip().upper() or None
        legacy = str(oz_mode or "").strip().upper() or None
        if legacy and filter_vm is None and filter_tm is None:
            filter_vm, filter_tm = _resolve_oz_modes(None, None, legacy)
        if filter_vm is not None and filter_vm not in VALIDATION_MODES:
            return
        if filter_tm is not None:
            filter_flags = oz_profiles.trigger_flags(filter_tm)
            if filter_flags is None:
                return
            filter_tm = oz_profiles.canonical_trigger_mode(filter_flags)

        removed_manual_external_ids: set[str] = set()
        with self._lock:
            keys = []
            for key, w in self._watches.items():
                if w.source != "MANUAL":
                    continue
                if watch_id is not None and w.watch_id != watch_id:
                    continue
                if request_chat_id is not None and w.request_chat_id != request_chat_id:
                    continue
                if filter_vm is not None and w.validation_mode != filter_vm:
                    continue
                if filter_tm is not None and w.trigger_mode != filter_tm:
                    continue
                if persistent_only and not w.persistent:
                    continue
                if tf_filter and not (tf_filter & set(w.timeframes)):
                    continue
                if symbol is not None and w.symbol != symbol:
                    continue
                if direction is not None and w.direction != direction:
                    continue
                keys.append(key)

            removed_profiles = {
                (self._watches[key].validation_mode, self._watches[key].trigger_mode)
                for key in keys if key in self._watches
            }
            for key in keys:
                removed_watch = self._watches.pop(key, None)
                if removed_watch is not None and removed_watch.external_watch_id:
                    removed_manual_external_ids.add(removed_watch.external_watch_id)

            for vm, tm in sorted(removed_profiles):
                self._bump(vm, tm)
            if keys:
                self._save_state_locked()

        for ext_id in removed_manual_external_ids:
            if self.external.is_manual_level(ext_id):
                self.external.cancel(ext_id)

        if logging.getLogger().isEnabledFor(logging.INFO):
            logging.info(
                "🛑 [OZ 수동 Watch 취소] TF=%s symbol=%s direction=%s validation=%s trigger=%s persistent_only=%s | %d건",
                ",".join(sorted(tf_filter)) if tf_filter else "*",
                symbol or "*", direction or "*", filter_vm or "*", filter_tm or "*",
                persistent_only, len(keys),
            )

        if keys or (reply_cancel and watch_id and request_chat_id):
            labels = {_oz_profile_label(vm, tm) for vm, tm in removed_profiles}
            oz_label = "/".join(sorted(labels)) if labels else "OZ"
            ack_watch_id = watch_id or (keys[0] if len(keys) == 1 else None)
            self.telegram.send(
                f"✅ {oz_label} 감시 중지 · {len(keys)}건",
                kind="CONTROL_ACK",
                require_delivery=True,
                request_chat_id=request_chat_id,
                watch_id=ack_watch_id, watch_event="CANCELLED",
                removed_count=len(keys), reply_cancel=reply_cancel,
            )

    def reset_all(self, request_chat_id: Optional[str] = None) -> None:
        """Reset only the supplied owner's watches when request_chat_id is present."""
        request_chat_id = str(request_chat_id or "").strip() or None
        with self._lock:
            before = dict(self._watches)
            if request_chat_id is None:
                self._watches.clear()
            else:
                self._watches = {
                    k: w for k, w in self._watches.items()
                    if w.request_chat_id != request_chat_id
                }
            removed_watches = [
                w for key, w in before.items()
                if key not in self._watches
            ]
            affected = {
                (w.validation_mode, w.trigger_mode)
                for w in removed_watches
            }
            for vm, tm in sorted(affected):
                self._bump(vm, tm)
            self._save_state_locked()
        for w in removed_watches:
            if w.external_watch_id and self.external.is_manual_level(w.external_watch_id):
                self.external.cancel(w.external_watch_id)
        if logging.getLogger().isEnabledFor(logging.INFO):
            logging.info(
                "♻️ [OZ Watch 리셋] request_chat_id=%s | removed=%d",
                request_chat_id or "*", len(before) - len(self._watches),
            )

    def snapshot_for_symbol(
        self,
        symbol: str,
        validation_mode: str = "NORMAL",
        trigger_mode: str = "OZ",
    ) -> tuple[int, tuple[str, ...]]:
        vm, tm = _resolve_oz_modes(validation_mode, trigger_mode)
        with self._lock:
            tfs = set()
            for w in self._watches.values():
                if w.validation_mode != vm or w.trigger_mode != tm:
                    continue
                if w.symbol is not None and w.symbol != symbol:
                    continue
                ext_id = self._external_id_for_watch_locked(w)
                if ext_id is not None and w.direction in {"LONG", "SHORT"} and self.external.status(ext_id, w.direction) == "INVALID":
                    continue
                tfs.update(w.timeframes)
            return self._revision.get(_profile_key(vm, tm), 0), tuple(
                tf for tf in TF_MAP if tf in tfs
            )

    def active_symbols(self) -> set[str]:
        """현재 등록된 수동 OZ Watch의 명시 종목만 반환합니다."""
        with self._lock:
            return {w.symbol for w in self._watches.values() if w.symbol}

    def _matching(
        self,
        symbol: str,
        tf: str,
        direction: str,
        validation_mode: str = "NORMAL",
        trigger_mode: str = "OZ",
    ) -> list[str]:
        keys = []
        vm, tm = _resolve_oz_modes(validation_mode, trigger_mode)
        for key, w in self._watches.items():
            if w.validation_mode != vm or w.trigger_mode != tm:
                continue
            if tf not in w.timeframes:
                continue
            if w.symbol is not None and w.symbol != symbol:
                continue
            if w.direction is not None and w.direction != direction:
                continue
            ext_id = self._external_id_for_watch_locked(w)
            if ext_id is not None:
                st = self.external.state(ext_id, direction)
                if st is None or st.status != "CONFIRMED" or st.direction != direction:
                    continue
            keys.append(key)
        return keys

    def try_fire(
        self,
        symbol: str,
        tf: str,
        direction: str,
        grade: str,
        validation_mode: str = "NORMAL",
        trigger_mode: str = "OZ",
        trigger_name: Optional[str] = None,
        indicators_text: Optional[str] = None,
        current_price: Optional[float] = None,
        alert_identity: Optional[str] = None,
        completion_time: Optional[float] = None,
        b0_price: Optional[float] = None,
        b0_time: Optional[int] = None,
        oz_validity: Optional[dict] = None,
        neckline_price: Optional[float] = None,
        neckline_time_ms: Optional[int] = None,
    ) -> bool:
        vm, tm = _resolve_oz_modes(validation_mode, trigger_mode)
        with self._lock:
            keys = self._matching(symbol, tf, direction, vm, tm)
            if not keys:
                return False

            tf_label = TF_LABELS.get(tf, tf)
            oz_label = _oz_profile_label(vm, tm)
            trigger_text = f" · {trigger_name}" if trigger_name else ""
            message = f"🔔 {tf_label} {oz_label} {grade}급 {direction} · {symbol}{trigger_text}"
            by_recipient: dict[Optional[str], list[str]] = {}
            for key in keys:
                w = self._watches.get(key)
                if w is not None:
                    by_recipient.setdefault(w.request_chat_id, []).append(key)

            delivered_any = False
            delivered_all = True
            removed = 0
            persistent_count = 0
            removed_manual_external_ids: set[str] = set()
            for request_chat_id, recipient_keys in by_recipient.items():
                recipient_specs = [self._watches.get(key) for key in recipient_keys]
                source_spec_ids = list(dict.fromkeys(
                    w.source_spec_id for w in recipient_specs
                    if w is not None and w.source_spec_id
                ))
                source_names = list(dict.fromkeys(
                    w.source_name for w in recipient_specs
                    if w is not None and w.source_name
                ))
                ok = self.telegram.send(
                    message,
                    event_id=identity('OZ', alert_identity, request_chat_id, sorted(recipient_keys)) if alert_identity else None,
                    market_event_id=identity('OZ_MARKET', alert_identity) if alert_identity else None,
                    watch_sources={key: w.source_spec_id for key,w in zip(recipient_keys,recipient_specs) if w is not None},
                    event_time=completion_time,
                    direction=direction,
                    require_delivery=True,
                    request_chat_id=request_chat_id,
                    watch_ids=list(recipient_keys),
                    source_spec_id=source_spec_ids[0] if source_spec_ids else None,
                    source_spec_ids=source_spec_ids or None,
                    source_name=source_names[0] if source_names else None,
                    validation_mode=vm,
                    trigger_mode=tm,
                    trigger_name=trigger_name,
                    source_tf=tf,
                    symbol=symbol,
                    grade=grade,
                    indicators_text=str(indicators_text or ""),
                    current_price=current_price,
                    b0_price=b0_price,b0_time=b0_time,
                    signal_source='OZ',signal_tf=tf,oz_validity=oz_validity,
                    neckline_price=neckline_price,neckline_time_ms=neckline_time_ms,
                )
                if not ok:
                    delivered_all = False
                    logging.error(
                        "[OZ] 최종 알림 실패 - Watch 유지 | %s %s %s chat_id=%s",
                        symbol, tf, direction, request_chat_id or "official",
                    )
                    continue
                delivered_any = True
                for key in recipient_keys:
                    w = self._watches.get(key)
                    if w is None:
                        continue
                    if w.persistent:
                        persistent_count += 1
                    else:
                        removed_watch = self._watches.pop(key, None)
                        if removed_watch is not None and removed_watch.external_watch_id:
                            removed_manual_external_ids.add(removed_watch.external_watch_id)
                        removed += 1

            if removed:
                self._bump(vm, tm)
                self._save_state_locked()

        for ext_id in removed_manual_external_ids:
            if self.external.is_manual_level(ext_id):
                self.external.cancel(ext_id)

        if delivered_any:
            if logging.getLogger().isEnabledFor(logging.INFO):
                logging.info(
                    "✅ [OZ 알림] %s %s %s | %s급 | validation=%s trigger=%s/%s | matched=%d | 종료=%d | 지속=%d | recipients=%d",
                    symbol, tf, direction, grade, vm, tm, trigger_name or "-",
                    len(keys), removed, persistent_count, len(by_recipient),
                )
        return delivered_any and delivered_all

    def _save_state_locked(self):self.dirty=True

