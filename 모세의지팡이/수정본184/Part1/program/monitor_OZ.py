# -*- coding: utf-8 -*-
"""
Shared OZ parts used by the event engine
========================================

OZ candidate and trigger decisions live in oz_engine (profile.py, controllers.py).
The engine receives this module as an object and uses only
GenericWatchController/GenericWatchSpec, OZCommandHandler, DomainEventSender,
OZFactMemo, TF_LABELS, PERCENTILES and finite_number.
"""

from __future__ import annotations
from watch_ma import parse_ma_expression
import oz_profiles
from durable_protocol import Records, identity, atomic_json, read_json
import domain_memory
from domain_clock import uuid

import json
import logging
import os
import sys
import threading
from domain_clock import time
import weakref
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional

import pandas as pd


# -----------------------------------------------------------------------------
# Constants
# -----------------------------------------------------------------------------

PERCENTILES = ("RSI", "STO", "DI", "PRICE")


# -----------------------------------------------------------------------------
# Logging / config
# -----------------------------------------------------------------------------
def script_dir() -> Path:
    try:
        return Path(__file__).resolve().parent
    except NameError:
        return Path.cwd()


LOG_DIR = Path(os.environ["MOSES_LOG_DIRECTORY"]) if os.environ.get("MOSES_LOG_DIRECTORY") else script_dir() / "logs"


def _atomic_write_json(path: Path, payload) -> None:
    atomic_json(path, payload, default=None, allow_nan=True, indent=2)


def load_config(file_name: str = "config.txt") -> dict[str, str]:
    path = script_dir() / file_name
    cfg: dict[str, str] = {}
    with path.open("r", encoding="utf-8-sig") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            cfg[k.strip()] = v.strip()
    return cfg


def finite_number(v) -> bool:
    try:
        x = float(v)
        return pd.notna(x) and x != float("inf") and x != float("-inf")
    except (TypeError, ValueError):
        return False


# -----------------------------------------------------------------------------
# On-demand OZ Watch command
# -----------------------------------------------------------------------------
TF_LABELS = {
    "1m": "1분", "2m": "2분", "3m": "3분", "4m": "4분", "5m": "5분", "6m": "6분",
    "10m": "10분", "12m": "12분", "15m": "15분", "20m": "20분", "30m": "30분",
    "1h": "1시간", "2h": "2시간", "3h": "3시간", "4h": "4시간",
}


WATCH_EVALUATION_MODES = {"LIVE", "CLOSE"}
GENERIC_WATCH_LEGACY_CONTRACTS: dict[str, tuple[str, str]] = {
    "WONBI_TOUCH_CLOSE": ("WONBI_TOUCH", "CLOSE"),
    "BAR_CLOSE": ("BAR", "CLOSE"),
}
GENERIC_WATCH_DEFAULT_EVALUATION: dict[str, str] = {
    "EMA_CROSS": "CLOSE",
    "HMA_CROSS": "CLOSE",
    "BAR": "CLOSE",
    "WONBI_TOUCH": "LIVE",
    "PREV_DAY_TOUCH": "LIVE",
    "PERCENTILE_OUT": "LIVE",
    "PERCENTILE_OUT_IN": "LIVE",
}


def normalize_generic_watch_contract(watch_type: object, evaluation_mode: object = None) -> tuple[str, str]:
    raw_type = str(watch_type or "").strip().upper()
    raw_mode = str(evaluation_mode or "").strip().upper()
    legacy = GENERIC_WATCH_LEGACY_CONTRACTS.get(raw_type)
    if legacy is not None:
        condition_type, legacy_mode = legacy
        mode = raw_mode if raw_mode in WATCH_EVALUATION_MODES else legacy_mode
        return condition_type, mode
    mode = raw_mode if raw_mode in WATCH_EVALUATION_MODES else GENERIC_WATCH_DEFAULT_EVALUATION.get(raw_type, "LIVE")
    return raw_type, mode


@dataclass
class GenericWatchSpec:
    watch_id: str
    watch_type: str
    timeframes: tuple[str, ...]
    symbol: Optional[str] = None
    direction: Optional[str] = None
    level_side: Optional[str] = None
    ma_family: Optional[str] = None
    fast_period: Optional[int] = None
    slow_period: Optional[int] = None
    persistent: bool = False
    request_chat_id: Optional[str] = None
    # 시간연쇄 내부 Watch는 사용자에게 직접 알리지 않고 김매니저로 이벤트를 콜백합니다.
    chain_id: Optional[str] = None
    chain_stage: Optional[int] = None
    silent: bool = False
    evaluation_mode: str = "LIVE"
    watch_owner: str = "OZ"
    ma_expression: Optional[str] = None


class GenericWatchController:
    """Telegram 자연어로 등록된 단순 조건 Watch를 관리합니다."""

    def __init__(self, telegram: "DomainEventSender", *, event_state=None):
        self.telegram = telegram
        self._lock = threading.RLock()
        state = {} if event_state is None else event_state
        self._watches = state.setdefault('watches', {})
        self._revision = state.get('revision', 0)
        self._state_path = LOG_DIR / "oz_generic_watch_state.json"
        if event_state is None:self._load_state()

    def _load_state(self) -> None:
        if not domain_memory.exists(self._state_path):
            return
        try:
            raw = read_json(self._state_path)
            items = raw.get("watches", []) if isinstance(raw, dict) else []
            restored: dict[str, GenericWatchSpec] = {}
            allowed_types = {
                "EMA_CROSS", "HMA_CROSS", "PREV_DAY_TOUCH", "BAR",
                "WONBI_TOUCH", "PERCENTILE_OUT", "PERCENTILE_OUT_IN", "MA_EXPRESSION",
            }
            for item in items:
                if not isinstance(item, dict):
                    continue
                watch_id = str(item.get("watch_id") or "").strip()
                watch_type, evaluation_mode = normalize_generic_watch_contract(
                    item.get("watch_type"), item.get("evaluation_mode")
                )
                ordered = tuple(str(x).strip().lower() for x in item.get("timeframes", []) if str(x).strip())
                symbol = str(item.get("symbol") or "").strip() or None
                direction = str(item.get("direction") or "").upper() or None
                level_side = str(item.get("level_side") or "").upper() or None
                ma_family = str(item.get("ma_family") or "").upper() or None
                request_chat_id = str(item.get("request_chat_id") or "").strip() or None
                chain_id = str(item.get("chain_id") or "").strip() or None
                try:
                    fast_period = int(item["fast_period"]) if item.get("fast_period") is not None else None
                    slow_period = int(item["slow_period"]) if item.get("slow_period") is not None else None
                    chain_stage = int(item["chain_stage"]) if item.get("chain_stage") is not None else None
                except (TypeError, ValueError):
                    continue
                if not watch_id or watch_type not in allowed_types or not ordered:
                    continue
                if direction not in {None, "LONG", "SHORT"} or level_side not in {None, "HIGH", "LOW", "BOTH"}:
                    continue
                ma_expression = None
                if watch_type == "MA_EXPRESSION":
                    ma_expression = parse_ma_expression(item.get("ma_expression")).canonical
                restored[watch_id] = GenericWatchSpec(
                    watch_id, watch_type, ordered, symbol=symbol, direction=direction,
                    level_side=level_side, ma_family=ma_family, fast_period=fast_period,
                    slow_period=slow_period, persistent=bool(item.get("persistent", False)),
                    request_chat_id=request_chat_id, chain_id=chain_id, chain_stage=chain_stage,
                    silent=bool(item.get("silent", False)), evaluation_mode=evaluation_mode,
                    watch_owner=str(item.get("watch_owner") or ("KIM" if chain_id else "OZ")),
                    ma_expression=ma_expression,
                )
            with self._lock:
                self._watches = restored
                if restored:
                    self._revision += 1
            if restored:
                if logging.getLogger().isEnabledFor(logging.INFO):
                    logging.info("♻️ [조건 Watch 복원] %d건 | %s", len(restored), self._state_path)
        except Exception:
            logging.exception("[조건 Watch] 상태 복원 실패 | %s", self._state_path)

    def _save_state_locked(self) -> None:
        payload = {
            "version": 2,
            "watches": [
                {
                    "watch_id": w.watch_id, "watch_type": w.watch_type, "timeframes": list(w.timeframes),
                    "symbol": w.symbol, "direction": w.direction, "level_side": w.level_side,
                    "ma_family": w.ma_family, "fast_period": w.fast_period, "slow_period": w.slow_period,
                    "persistent": w.persistent, "request_chat_id": w.request_chat_id,
                    "chain_id": w.chain_id, "chain_stage": w.chain_stage, "silent": w.silent,
                    "evaluation_mode": w.evaluation_mode,
                    "watch_owner": w.watch_owner,
                    **({"ma_expression": w.ma_expression} if w.ma_expression is not None else {}),
                }
                for w in self._watches.values()
            ],
        }
        try:
            _atomic_write_json(self._state_path, payload)
        except Exception:
            logging.exception("[조건 Watch] 상태 저장 실패 | %s", self._state_path)

    def _bump(self):
        self._revision += 1

    def add(self, payload: dict) -> None:
        watch_type, evaluation_mode = normalize_generic_watch_contract(
            payload.get("watch_type"), payload.get("evaluation_mode")
        )
        ordered = tuple(str(x).strip().lower() for x in payload.get("timeframes", []) if str(x).strip())
        symbol = str(payload.get("symbol") or "").strip() or None
        direction = str(payload.get("direction") or "").upper() or None
        level_side = str(payload.get("level_side") or "").upper() or None
        ma_family = str(payload.get("ma_family") or "").upper() or None
        fast_period = payload.get("fast_period")
        slow_period = payload.get("slow_period")
        if fast_period is not None:
            try: fast_period = int(fast_period)
            except (TypeError, ValueError): return
        if slow_period is not None:
            try: slow_period = int(slow_period)
            except (TypeError, ValueError): return
        persistent = bool(payload.get("persistent", False))
        request_chat_id = str(payload.get("request_chat_id") or "").strip() or None
        requested_watch_id = str(payload.get("watch_id") or "").strip() or None
        chain_id = str(payload.get("chain_id") or "").strip() or None
        try:
            chain_stage = int(payload["chain_stage"]) if payload.get("chain_stage") is not None else None
        except (TypeError, ValueError):
            return
        silent = bool(payload.get("silent", False))
        ma_expression = None
        if watch_type == "MA_EXPRESSION":
            expression = parse_ma_expression(payload.get("ma_expression"))
            ma_expression = expression.canonical
            if not payload.get("evaluation_mode"):
                evaluation_mode = expression.default_evaluation_mode
        if watch_type not in {
            "EMA_CROSS", "HMA_CROSS", "PREV_DAY_TOUCH", "BAR",
            "WONBI_TOUCH", "PERCENTILE_OUT", "PERCENTILE_OUT_IN", "MA_EXPRESSION",
        } or not ordered:
            return
        if direction not in {None, "LONG", "SHORT"}:
            return
        if level_side not in {None, "HIGH", "LOW", "BOTH"}:
            return

        with self._lock:
            # 시간연쇄는 고유 watch_id를 재전송하여 프로세스 재시작/큐 시작 순서에도 복구할 수 있습니다.
            if requested_watch_id and requested_watch_id in self._watches:
                return
            # 기존 일반 Watch의 중복 방지 의미는 그대로 유지합니다. 시간연쇄 고유 ID Watch끼리는 별개로 취급합니다.
            if not requested_watch_id:
                for w in self._watches.values():
                    if (
                        w.watch_type == watch_type
                        and w.evaluation_mode == evaluation_mode
                        and w.timeframes == ordered
                        and w.symbol == symbol
                        and w.direction == direction
                        and w.level_side == level_side
                        and w.ma_family == ma_family
                        and w.fast_period == fast_period
                        and w.slow_period == slow_period
                        and w.ma_expression == ma_expression
                        and w.persistent == persistent
                        and w.request_chat_id == request_chat_id
                    ):
                        return
            wid = requested_watch_id or f"GEN:{watch_type}:{time.time_ns()}"
            self._watches[wid] = GenericWatchSpec(
                wid, watch_type, ordered, symbol=symbol, direction=direction,
                level_side=level_side, ma_family=ma_family, fast_period=fast_period,
                slow_period=slow_period, persistent=persistent, request_chat_id=request_chat_id,
                chain_id=chain_id, chain_stage=chain_stage, silent=silent,
                evaluation_mode=evaluation_mode,
                watch_owner=str(payload.get("watch_owner") or ("KIM" if chain_id else "OZ")),
                ma_expression=ma_expression,
            )
            self._bump()
            self._save_state_locked()

        tf_label = "·".join(TF_LABELS.get(tf, tf) for tf in ordered)
        scope = symbol or "전체 종목"
        mode = " 지속" if persistent else ""
        if watch_type in {"EMA_CROSS", "HMA_CROSS"}:
            family = ma_family or ("EMA" if watch_type == "EMA_CROSS" else "HMA")
            pair = f"{family}{fast_period}/{slow_period}"
            cond = f"{pair} 골크" if direction == "LONG" else f"{pair} 데크" if direction == "SHORT" else f"{pair} 크로스"
        elif watch_type == "WONBI_TOUCH":
            cond = "상단 원비 터치" if level_side == "HIGH" else "하단 원비 터치" if level_side == "LOW" else "원비 터치"
            if evaluation_mode == "CLOSE":
                cond += " 봉마감"
        elif watch_type == "MA_EXPRESSION":
            cond = ma_expression + (" 봉마감" if evaluation_mode == "CLOSE" else "")
        elif watch_type == "BAR":
            cond = "봉마감"
        elif watch_type == "PERCENTILE_OUT":
            cond = "상단 OUT" if level_side == "HIGH" else "하단 OUT" if level_side == "LOW" else "OUT"
        elif watch_type == "PERCENTILE_OUT_IN":
            cond = "상단 OUT→IN" if level_side == "HIGH" else "하단 OUT→IN" if level_side == "LOW" else "OUT→IN"
        else:
            cond = "전일 고가 터치" if level_side == "HIGH" else "전일 저가 터치" if level_side == "LOW" else "전일 고가/저가 터치"
        if not silent:
            self.telegram.send(
                f"✅ {scope} · {tf_label} {cond}{mode} 감시",
                kind="CONTROL_ACK", require_delivery=True, request_chat_id=request_chat_id,
                watch_id=wid, watch_type=watch_type, watch_event="REGISTERED",
            )
        if logging.getLogger().isEnabledFor(logging.INFO):
            logging.info(
                "🟦 [조건 Watch] 시작 | %s/%s | %s | %s | persistent=%s chain=%s stage=%s",
                watch_type, evaluation_mode, scope, tf_label, persistent, chain_id or "-", chain_stage if chain_stage is not None else "-",
            )

    def cancel(self, payload: dict) -> None:
        watch_id = str(payload.get("watch_id") or "").strip() or None
        raw_watch_type = str(payload.get("watch_type") or "").strip()
        raw_evaluation_mode = str(payload.get("evaluation_mode") or "").strip().upper()
        if raw_watch_type:
            watch_type, evaluation_mode = normalize_generic_watch_contract(
                raw_watch_type, raw_evaluation_mode or None
            )
        else:
            watch_type = None
            evaluation_mode = raw_evaluation_mode if raw_evaluation_mode in WATCH_EVALUATION_MODES else None
        tf_filter = {str(x).strip().lower() for x in payload.get("timeframes", []) if str(x).strip()}
        symbol = str(payload.get("symbol") or "").strip() or None
        direction = str(payload.get("direction") or "").upper() or None
        level_side = str(payload.get("level_side") or "").upper() or None
        ma_family = str(payload.get("ma_family") or "").upper() or None
        fast_period = payload.get("fast_period")
        slow_period = payload.get("slow_period")
        try:
            fast_period = int(fast_period) if fast_period is not None else None
            slow_period = int(slow_period) if slow_period is not None else None
        except (TypeError, ValueError):
            return
        persistent_only = bool(payload.get("persistent_only", False))
        request_chat_id = str(payload.get("request_chat_id") or "").strip() or None
        with self._lock:
            keys = []
            for k, w in self._watches.items():
                if watch_id is not None and k != watch_id:
                    continue
                if request_chat_id is not None and w.request_chat_id != request_chat_id:
                    continue
                if watch_type and w.watch_type != watch_type:
                    continue
                if evaluation_mode and w.evaluation_mode != evaluation_mode:
                    continue
                if tf_filter and not (tf_filter & set(w.timeframes)):
                    continue
                if symbol is not None and w.symbol != symbol:
                    continue
                if direction is not None and w.direction != direction:
                    continue
                if level_side is not None and w.level_side != level_side:
                    continue
                if ma_family is not None and w.ma_family != ma_family:
                    continue
                if fast_period is not None and w.fast_period != fast_period:
                    continue
                if slow_period is not None and w.slow_period != slow_period:
                    continue
                if persistent_only and not w.persistent:
                    continue
                keys.append(k)
            for k in keys:
                self._watches.pop(k, None)
            if keys:
                self._bump()
                self._save_state_locked()
        if logging.getLogger().isEnabledFor(logging.INFO):
            logging.info(
                "🛑 [조건 Watch] 취소 | type=%s mode=%s | %d건",
                watch_type or "*", evaluation_mode or "*", len(keys),
            )
        if keys or (payload.get("reply_cancel") and watch_id and request_chat_id):
            ack_watch_id = watch_id or (keys[0] if len(keys) == 1 else None)
            self.telegram.send(
                f"✅ 조건 감시 중지 · {len(keys)}건",
                kind="CONTROL_ACK", require_delivery=True,
                request_chat_id=request_chat_id, watch_id=ack_watch_id,
                watch_event="CANCELLED", removed_count=len(keys),
                reply_cancel=bool(payload.get("reply_cancel")),
            )

    def reset_all(self, request_chat_id: Optional[str] = None) -> None:
        request_chat_id = str(request_chat_id or "").strip() or None
        with self._lock:
            if request_chat_id is None:
                count = len(self._watches)
                self._watches.clear()
            else:
                keys = [k for k, w in self._watches.items() if w.request_chat_id == request_chat_id]
                count = len(keys)
                for k in keys:
                    self._watches.pop(k, None)
            if count:
                self._bump()
                self._save_state_locked()
        if logging.getLogger().isEnabledFor(logging.INFO):
            logging.info("♻️ [조건 Watch] 전체 취소 | %d건 | request_chat_id=%s", count, request_chat_id or "*")

    def snapshot_for_symbol(self, symbol: str) -> tuple[int, tuple[GenericWatchSpec, ...]]:
        with self._lock:
            items = tuple(w for w in self._watches.values() if w.symbol is None or w.symbol == symbol)
            return self._revision, items

    def fire(self, watch_id: str, message: str, **event_meta) -> bool:
        with self._lock:
            w = self._watches.get(watch_id)
            if w is None:
                return False

            if w.chain_id:
                direction = event_meta.get("direction") or w.direction
                ok = self.telegram.send(
                    message, direction=direction, kind="GENERIC_TRIGGER", require_delivery=False,
                    request_chat_id=w.request_chat_id, watch_id=w.watch_id, chain_id=w.chain_id,
                    chain_stage=w.chain_stage, watch_type=w.watch_type,
                    evaluation_mode=w.evaluation_mode, symbol=w.symbol,
                    source_tf=event_meta.get("source_tf"), level_side=event_meta.get("level_side") or w.level_side,
                    ma_family=w.ma_family, fast_period=w.fast_period, slow_period=w.slow_period,
                    event_time=event_meta.get("event_time"), event_id=event_meta.get("event_id"),
                )
            else:
                ok = self.telegram.send(
                    message, kind="CONTROL_ACK", require_delivery=True, request_chat_id=w.request_chat_id,
                    watch_id=w.watch_id, watch_type=w.watch_type, watch_event="ALERT",
                    event_id=event_meta.get("event_id"),
                )

            if not ok:
                return False
            if not w.persistent:
                self._watches.pop(watch_id, None)
                self._bump()
                self._save_state_locked()
            return True




class OZCommandHandler:
    """Tail the manager JSONL command queue. Existing lines are skipped on startup."""


    def _apply(self, payload: dict) -> None:
        action = str(payload.get("action", "")).upper()
        if action == "MANUAL_WATCH":
            payload = oz_profiles.normalize_watch_payload(payload)
            validation_mode = payload['validation_mode']
            trigger_mode = payload['trigger_mode']
            external_required = str(
                payload.get("external_liquidity_required", payload.get("external_required", ""))
            ).strip().lower() in {"1", "true", "yes", "y", "on"}
            # 수동 가격은 external_source_kind가 MANUAL_LEVEL로 명시된 경우에만 직접 등록합니다.
            # SWEEP이 넘긴 external_level_price는 추적/로그용 값이며 새 수동 Gate를 만들지 않습니다.
            external_source_kind = str(payload.get("external_source_kind") or "").strip().upper()
            if (
                external_required
                and external_source_kind == "MANUAL_LEVEL"
                and finite_number(payload.get("external_level_price"))
            ):
                self.controller.register_external(payload)
            self.controller.add_manual(
                payload.get("timeframes", []),
                payload.get("issued_at"),
                symbol=payload.get("symbol"),
                direction=payload.get("direction"),
                persistent=bool(payload.get("persistent", False)),
                request_chat_id=payload.get("request_chat_id"),
                validation_mode=validation_mode,
                trigger_mode=trigger_mode,
                oz_mode=payload.get("oz_mode"),
                watch_id=payload.get("watch_id"),
                external_watch_id=(
                    payload.get("external_watch_id")
                    or payload.get("sweep_watch_id")
                    or payload.get("parent_watch_id")
                    or payload.get("setup_watch_id")
                ),
                external_source_tf=(
                    payload.get("external_source_tf")
                    or payload.get("setup_tf")
                    or payload.get("source_tf")
                ),
                external_required=external_required,
                source_spec_id=payload.get("source_spec_id"),
                watch_owner=str(payload.get("watch_owner") or ("KIM" if payload.get("source_spec_id") else "OZ")),
                source_name=payload.get("source_name"),
            )
        elif action == "SWEEP_WATCH":
            self.controller.register_external(payload)
        elif action == "CANCEL_SWEEP":
            self.controller.cancel_external(str(payload.get("watch_id") or ""))
        elif action == "RESET_SWEEP":
            self.controller.reset_external()
        elif action == "CANCEL_MANUAL":
            self.controller.cancel_manual(
                payload.get("timeframes", []),
                symbol=payload.get("symbol"),
                direction=payload.get("direction"),
                persistent_only=bool(payload.get("persistent_only", False)),
                request_chat_id=payload.get("request_chat_id"),
                validation_mode=payload.get("validation_mode"),
                trigger_mode=payload.get("trigger_mode"),
                oz_mode=payload.get("oz_mode"),
                watch_id=payload.get("watch_id"),
                reply_cancel=bool(payload.get("reply_cancel")),
            )
        elif action == "GENERIC_WATCH":
            self.generic.add(payload)
        elif action == "CANCEL_GENERIC":
            self.generic.cancel(payload)
        elif action == "RESET_ALL":
            request_chat_id = payload.get("request_chat_id")
            self.controller.reset_all(request_chat_id=request_chat_id)
            self.generic.reset_all(request_chat_id=request_chat_id)
            if not request_chat_id:
                self.controller.reset_external()





# -----------------------------------------------------------------------------
# Telegram
# -----------------------------------------------------------------------------
class DomainEventSender:
    """최종 알림을 김매니저에게 REQ로 전달하고 실제 처리 ACK를 확인합니다."""

    def __init__(self, *, transport=None):
        self._transport = transport
        if transport is None: raise ValueError("event output port required")
        self.local = threading.local()
        self._events = Records(LOG_DIR / 'oz_outgoing_events.json')



    def _request(self, event):
        return self._transport(event)



    def send(self, text, direction=None, kind="FINAL_ALERT", require_delivery=False, request_chat_id=None, **meta):
        event = {
            "kind": kind,
            "strategy": "OZ",
            "direction": str(direction or "").upper(),
            "message": text,
        }
        target = str(request_chat_id or "").strip()
        if target:
            event["request_chat_id"] = target
        event.update({k: v for k, v in meta.items() if v is not None})
        if kind == "GENERIC_TRIGGER":
            event.setdefault("event_time", time.time())
        if kind == 'FINAL_ALERT':
            event.setdefault('event_id', uuid.uuid4().hex)
            key = identity(event['event_id'], event.get('request_chat_id'))
            saved = self._events.get(key)
            if saved is None:
                self._events.put(key, event)
            else:
                event = saved
        reply = self._request(event)

        if not reply.get("ok"):
            logging.error(
                "[OZ] 김매니저 알림 처리 실패 | %s",
                reply.get("error", "unknown"),
            )
            return False

        if reply.get("filtered"):
            if logging.getLogger().isEnabledFor(logging.INFO):
                logging.info(
                    "[OZ] 김매니저 알림 필터로 미발송 | direction=%s",
                    str(direction or "").upper(),
                )

        if require_delivery:
            return bool(reply.get("delivered"))

        # 일반 전략은 '필터로 차단됨'도 김매니저가 정상 처리한 것으로 봅니다.
        return True


# -----------------------------------------------------------------------------
# Staff client (same ZMQ protocol as existing strategies)
# -----------------------------------------------------------------------------


class OZFactMemo:
    """공통 OZ Fact: 같은 STAFF 봉(DataFrame 객체)에서 나온 순수 계산값을 1회만 계산합니다.

    16개 OZ 프로필은 같은 SharedOZStaffClient 응답(같은 DataFrame 객체)을 받습니다.
    프로필 상태와 무관한 계산(마지막 봉 행, 퍼센타일 상태, HMA6/17 cross 관측,
    pre-cross 극값, 봉 수, 트리거 터치 등)은 이 메모에 한 번 저장하고
    프로필별 상태머신은 그 값을 조합만 합니다.

    * 키는 DataFrame 객체 자체입니다(weakref). 새 STAFF 응답은 새 객체이므로 새로 계산하고,
      객체가 사라지면 해당 Fact도 함께 사라집니다. STAFF 응답 DataFrame은 OZ에서 수정하지 않습니다.
    * weakref를 만들 수 없는 입력(백테스트 전용 배열 프레임 등)은 캐시 없이 그대로 계산합니다.
    """

    def __init__(self):
        self._frames: dict[int, tuple] = {}
        self._lock = threading.RLock()
        self.computed = 0
        self.reused = 0

    def _forget(self, ident: int, ref) -> None:
        with self._lock:
            entry = self._frames.get(ident)
            if entry is not None and entry[0] is ref:
                self._frames.pop(ident, None)

    def get(self, df, key, compute):
        if df is None:
            return compute()
        ident = id(df)
        with self._lock:
            entry = self._frames.get(ident)
            if entry is None or entry[0]() is not df:
                try:
                    ref = weakref.ref(df, lambda r, i=ident: self._forget(i, r))
                except TypeError:
                    ref = None
                if ref is None:
                    store = None
                else:
                    entry = (ref, {})
                    self._frames[ident] = entry
                    store = entry[1]
            else:
                store = entry[1]
            if store is not None and key in store:
                self.reused += 1
                return store[key]
        value = compute()
        if store is not None:
            with self._lock:
                store.setdefault(key, value)
                self.computed += 1
        return value
