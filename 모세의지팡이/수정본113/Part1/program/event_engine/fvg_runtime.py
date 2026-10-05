"""Persistent FVG lifecycle with NumPy closed structure/current-row touch."""
from __future__ import annotations
import logging
from strategy_FVG import normalize_tf, FVG_MAX_AGE_BARS, REQUIRED_INDS
from .market import select, health
from .fvg_structure import StructureStore

class FVGRuntime:
    def __init__(self,state):
        self._last_closed_time=state.setdefault('last_closed_time',{})
        self._touch_state=state.setdefault('touch_state',{})
        self._active_zones=state.setdefault('active_zones',{})
        self._seen_created=state.setdefault('seen_created',set())
        self._initialized_keys=state.setdefault('initialized_keys',set())
        self.structures=StructureStore()
    def bind(self,board,manager):self.board=board;self.manager=manager
    def __deepcopy__(self,memo):
        from copy import deepcopy
        result=type(self).__new__(type(self));memo[id(self)]=result
        for key,value in vars(self).items():
            if key not in ('board','manager'):setattr(result,key,deepcopy(value,memo))
        return result
    def evaluate(self,symbol,tf,view):
        result=self.structures.evaluate(symbol,tf,view)
        if result is not None:result['source_health']=health({tf:view},REQUIRED_INDS)
        return result
    def _send_created_event(self, zone: dict) -> None:
        self.manager.send({
            "kind": "FVG_CREATED", "strategy": "FVG", **zone,
            "event_time": zone.get("fvg_time"),
        })
        if logging.getLogger().isEnabledFor(logging.INFO):
            logging.info(
                "📌 [FVG 생성] %s %s | %s | %.5f~%.5f",
                zone["symbol"], zone["source_tf"], zone["fvg_side"],
                zone["zone_bot"], zone["zone_top"],
            )

    def _send_touch_event(self, zone: dict, result: dict, touched: bool) -> None:
        event = {
            "kind": "FVG_TOUCH" if touched else "FVG_TOUCH_END",
            "strategy": "FVG",
            **zone,
            "price": result.get("live_price"),
            "event_time": result.get("live_time"),
        }
        self.manager.send(event)
        if logging.getLogger().isEnabledFor(logging.INFO):
            logging.info(
                "%s [FVG 터치%s] %s %s | %s | %.5f~%.5f | 가격=%s",
                "✅" if touched else "↩️",
                "" if touched else " 종료",
                zone["symbol"], zone["source_tf"], zone["fvg_side"],
                zone["zone_bot"], zone["zone_top"],
                "-" if result.get("live_price") is None else f"{result['live_price']:.5f}",
            )

    def _send_filled_event(self, zone: dict, fill_time: float | None) -> None:
        event = {
            "kind": "FVG_FILLED",
            "strategy": "FVG",
            **zone,
            "fill_time": fill_time,
            "event_time": fill_time,
        }
        self.manager.send(event)
        if logging.getLogger().isEnabledFor(logging.INFO):
            logging.info(
                "🗑️ [FVG 채움/삭제] %s %s | %s | %.5f~%.5f | fill_time=%s",
                zone["symbol"], zone["source_tf"], zone["fvg_side"],
                zone["zone_bot"], zone["zone_top"], fill_time,
            )

    def _send_expired_event(self, zone: dict, expired_at: float | None) -> None:
        event = {
            "kind": "FVG_EXPIRED",
            "strategy": "FVG",
            **zone,
            "expired_at": expired_at,
            "event_time": expired_at,
            "max_age_bars": int(FVG_MAX_AGE_BARS),
        }
        self.manager.send(event)
        if logging.getLogger().isEnabledFor(logging.INFO):
            logging.info(
                "⌛ [FVG 만료/삭제] %s %s | %s | %.5f~%.5f | 최근 %d봉 범위 이탈",
                zone["symbol"], zone["source_tf"], zone["fvg_side"],
                zone["zone_bot"], zone["zone_top"], int(FVG_MAX_AGE_BARS),
            )

    def process_watch_result(self, result: dict) -> None:
        if not result:
            return

        key = (result["symbol"], result["source_tf"])
        latest_closed_time = result.get("latest_closed_time")
        previous_closed_time = self._last_closed_time.get(key)
        is_new_closed_bar = (
            latest_closed_time is not None
            and (previous_closed_time is None or latest_closed_time > previous_closed_time)
        )
        if latest_closed_time is not None:
            self._last_closed_time[key] = float(latest_closed_time)

        current_active = {z["zone_id"]: z for z in result.get("zones", [])}
        filled_lookup = {z["zone_id"]: z for z in result.get("filled_zones", [])}
        eligible_zone_ids = set(result.get("eligible_zone_ids", []))
        oldest_allowed_time = result.get("oldest_allowed_time")
        previous_active = self._active_zones.get(key, {})
        first_snapshot = key not in self._initialized_keys

        # 시작 시 과거 상태를 이벤트로 재발송하지 않습니다.
        # 실행 이후 추적 중이던 영역이 사라졌을 때만 원인을 구분합니다.
        if not first_snapshot and is_new_closed_bar:
            for zone_id, old_zone in previous_active.items():
                if zone_id in current_active:
                    continue

                filled = filled_lookup.get(zone_id)
                if filled is not None:
                    self._send_filled_event(old_zone, filled.get("fill_time"))
                elif (
                    oldest_allowed_time is not None
                    and float(old_zone.get("fvg_time", 0.0)) < float(oldest_allowed_time)
                ):
                    self._send_expired_event(old_zone, latest_closed_time)
                else:
                    # 채움/만료가 아닌 사유로 추적 대상에서 빠졌다면,
                    # KIM에 남아 있을 수 있는 터치 기록부터 종료합니다.
                    # 여기에는 최신 3개 제한으로 밀려난 경우와 데이터 재구성으로
                    # 후보에서 빠지는 드문 경우가 포함됩니다.
                    if bool(self._touch_state.get(zone_id, False)):
                        self._send_touch_event(old_zone, result, False)
                self._touch_state.pop(zone_id, None)

        for zone_id, zone in current_active.items():
            # 새로운 확정봉에서 막 생성된 FVG만 생성 이벤트로 전달합니다.
            if (
                not first_snapshot
                and is_new_closed_bar
                and latest_closed_time is not None
                and zone["fvg_time"] == float(latest_closed_time)
                and zone_id not in self._seen_created
            ):
                self._seen_created.add(zone_id)
                self._send_created_event(zone)

            touched_now = bool(zone.get("touched_now", False))
            touched_before = bool(self._touch_state.get(zone_id, False))
            if touched_now != touched_before:
                self._touch_state[zone_id] = touched_now
                self._send_touch_event(zone, result, touched_now)
            elif zone_id not in self._touch_state:
                self._touch_state[zone_id] = touched_now

        # snapshot 역사 범위 밖으로 밀려난 영역도 내부 상태에서는 정리합니다.
        # FVG_FILLED는 현재 확정봉 데이터로 실제 채움이 확인된 경우에만 발생합니다.
        for zone_id in list(self._touch_state):
            if zone_id.startswith(f"{result['symbol']}|{result['source_tf']}|") and zone_id not in current_active:
                self._touch_state.pop(zone_id, None)

        self._active_zones[key] = current_active
        self._initialized_keys.add(key)
        self.manager.send(self.manager.stream.snapshot(result['symbol'], result['source_tf'],
            [dict(zone, kind='FVG_TOUCH' if zone.get('touched_now') else 'FVG_CURRENT', strategy='FVG',
                  event_time=result.get('live_time'), price=result.get('live_price')) for zone in current_active.values()],
            source_health=result.get('source_health')))

        if len(self._seen_created) > 10000:
            active_ids = {zone_id for zones in self._active_zones.values() for zone_id in zones}
            self._seen_created.intersection_update(active_ids)

    def run_query(self, payload: dict) -> None:
        symbol = str(payload.get("symbol") or "").strip()
        tf = normalize_tf(payload.get("source_tf"))
        if not symbol or not tf:
            return

        result = self.evaluate(symbol, tf, select(self.board, symbol, tf))

        event = {
            "kind": "FVG_QUERY_RESULT",
            "strategy": "FVG",
            "request_id": payload.get("request_id"),
            "request_chat_id": payload.get("request_chat_id"),
            "symbol": symbol,
            "source_tf": tf,
        }
        if result is None:
            event.update({"ok": False, "error": "fvg_data_unavailable", "zones": []})
        else:
            # 조회 응답에는 현재 살아 있는 FVG만 노출합니다.
            clean = dict(result)
            clean.pop("filled_zones", None)
            clean.pop("eligible_zone_ids", None)
            clean.pop("oldest_allowed_time", None)
            event.update({"ok": True, **clean})
        self.manager.send(event)
