# -*- coding: utf-8 -*-
"""Personal watch-chain orchestration for manager_KIM.

This module owns the runtime state machine for personal timed chains. It does not
parse Telegram text and it does not evaluate market/OZ conditions. Those remain
outside this layer; the orchestrator only arms/cancels child watches when stage
events arrive.
"""
from __future__ import annotations
import domain_memory
from durable_protocol import identity, atomic_json, read_json
import oz_profiles
from oz_profile_loader import load_saved_oz
from watch_ma import parse_ma_expression, parse_ma_name

from domain_clock import datetime as dt
from copy import deepcopy
import hashlib
import json
import logging
import math
import os
import threading
from domain_clock import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Optional
from zoneinfo import ZoneInfo

from command_interpreter import OZ_BASE_TFS, WATCH_TF_MAP, normalize_tf

# OZ 프로필 어휘(무지성/브레이커 자유 조합)는 oz_profiles.py 단일 정의를 사용합니다.
VALIDATION_MODES = oz_profiles.VALIDATION_MODES
TRIGGER_MODES = oz_profiles.TRIGGER_MODES


def _canonical_trigger_mode(value: object) -> str:
    """현재 트리거를 정규화합니다. 알 수 없는 값은 검증에서 거부합니다."""
    raw = str(value or "OZ").upper()
    flags = oz_profiles.trigger_flags(raw)
    return oz_profiles.canonical_trigger_mode(flags) if flags is not None else raw

CHAIN_TRIGGER_TYPES = {
    "WONBI_TOUCH", "FVG_NEW", "EMA_CROSS", "HMA_CROSS", "MA_EXPRESSION",
    "PERCENTILE_OUT", "PERCENTILE_OUT_IN", "PREV_DAY_TOUCH",
    # 조건별 유효시간 필터 전용 이벤트. 복합조건은 사전에서 primitive 조건 조합으로 주입됩니다.
    "BAR_CLOSE", "WONBI_TOUCH_CLOSE", "COMPOUND_CONDITION",
    # OZ 자체가 다음 단계의 선행 트리거가 될 수 있습니다.
    "OZ_ALERT",
}

LOCAL_CHAIN_TRIGGER_TYPES = {
    "BAR_CLOSE", "WONBI_TOUCH_CLOSE", "COMPOUND_CONDITION",
}
CHAIN_FINAL_ACTIONS = {"OZ", "NOTIFY"}
DEFAULT_CHAIN_REFRESH_SEC = 30.0


def stable_id(prefix: str, *parts: object, length: int = 16) -> str:
    raw = "|".join(str(x) for x in parts)
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:length]
    return f"{prefix}:{digest}"


def format_duration_ko(seconds: float) -> str:
    seconds = max(0, int(round(float(seconds))))
    if seconds % 86400 == 0 and seconds >= 86400:
        return f"{seconds // 86400}일"
    if seconds % 3600 == 0 and seconds >= 3600:
        return f"{seconds // 3600}시간"
    if seconds % 60 == 0 and seconds >= 60:
        return f"{seconds // 60}분"
    return f"{seconds}초"


def _atomic_write_json(path: Path, payload) -> None:
    atomic_json(path, payload, default=None, allow_nan=True, indent=2)


class UnorderedConditionLatch:
    """
    순서와 무관하게 서로 다른 조건 이벤트를 누적하는 범용 상태 컨테이너입니다.

    - condition_key별 최신 이벤트 1개만 유지합니다.
    - direction/correlation_key별 상태를 독립적으로 보관합니다.
    - window_sec가 0보다 크면 최신 시각 기준으로 유효창 밖의 이벤트를 자동 폐기합니다.
    - required_count를 사용해 ALL뿐 아니라 K-of-N 판정에도 재사용할 수 있습니다.
    - 실제 시장조건 판독/알림/OZ arm은 하지 않습니다.
    """

    VERSION = 1

    @classmethod
    def new_state(cls) -> dict:
        return {"version": cls.VERSION, "groups": {}}

    @staticmethod
    def _finite_ts(value) -> Optional[float]:
        try:
            value = float(value)
        except (TypeError, ValueError):
            return None
        return value if math.isfinite(value) else None

    @classmethod
    def normalize_state(cls, raw) -> dict:
        out = cls.new_state()
        if not isinstance(raw, dict):
            return out
        groups = raw.get("groups")
        if not isinstance(groups, dict):
            return out
        for raw_group, raw_hits in groups.items():
            group = str(raw_group or "").strip().upper()
            if not group or not isinstance(raw_hits, dict):
                continue
            hits: dict[str, dict] = {}
            for raw_key, raw_hit in raw_hits.items():
                key = str(raw_key or "").strip().upper()
                if not key or not isinstance(raw_hit, dict):
                    continue
                ts = cls._finite_ts(raw_hit.get("ts"))
                if ts is None:
                    continue
                token = str(raw_hit.get("token") or "").strip()
                meta = raw_hit.get("meta") if isinstance(raw_hit.get("meta"), dict) else {}
                hits[key] = {"ts": ts, "token": token, "meta": dict(meta)}
            if hits:
                out["groups"][group] = hits
        return out

    @classmethod
    def _ensure_state(cls, state: dict) -> dict:
        normalized = cls.normalize_state(state)
        state.clear()
        state.update(normalized)
        return state

    @classmethod
    def prune(cls, state: dict, window_sec: float = 0.0, reference_ts: Optional[float] = None) -> bool:
        """유효시간 밖의 hit를 제거합니다. 반환값은 상태 변경 여부입니다."""
        cls._ensure_state(state)
        try:
            window = float(window_sec)
        except (TypeError, ValueError):
            window = 0.0
        if not math.isfinite(window) or window <= 0:
            return False
        ref = cls._finite_ts(reference_ts)
        if ref is None:
            ref = time.time()
        cutoff = ref - window
        changed = False
        for group, hits in list(state["groups"].items()):
            for key, hit in list(hits.items()):
                ts = cls._finite_ts(hit.get("ts"))
                if ts is None or ts < cutoff:
                    hits.pop(key, None)
                    changed = True
            if not hits:
                state["groups"].pop(group, None)
        return changed

    @classmethod
    def remove_where(cls, state: dict, predicate: Callable[[str, str, dict], bool]) -> bool:
        """봉 수 expiry처럼 외부 기준으로 hit를 제거할 때 사용하는 범용 hook입니다."""
        cls._ensure_state(state)
        changed = False
        for group, hits in list(state["groups"].items()):
            for key, hit in list(hits.items()):
                try:
                    remove = bool(predicate(group, key, hit))
                except Exception:
                    remove = False
                if remove:
                    hits.pop(key, None)
                    changed = True
            if not hits:
                state["groups"].pop(group, None)
        return changed

    @classmethod
    def clear_group(cls, state: dict, correlation_key: str) -> bool:
        cls._ensure_state(state)
        group = str(correlation_key or "").strip().upper()
        if not group:
            return False
        return state["groups"].pop(group, None) is not None

    @classmethod
    def register(
        cls,
        state: dict,
        *,
        correlation_key: str,
        condition_key: str,
        event_ts: float,
        token: object,
        required_count: int,
        window_sec: float = 0.0,
        now: Optional[float] = None,
        meta: Optional[dict] = None,
    ) -> dict:
        """
        한 조건 이벤트를 latch하고 현재 K-of-N 성립 여부를 반환합니다.
        같은 condition_key가 다시 오면 최신 이벤트로 교체하므로 rolling window로 동작합니다.
        """
        cls._ensure_state(state)
        group = str(correlation_key or "").strip().upper()
        key = str(condition_key or "").strip().upper()
        ts = cls._finite_ts(event_ts)
        if not group or not key or ts is None:
            raise ValueError("unordered latch group/key/event_ts 오류")
        required = int(required_count)
        if required <= 0:
            raise ValueError("unordered latch required_count 오류")
        ref = cls._finite_ts(now)
        if ref is None:
            ref = ts
        try:
            window = float(window_sec)
        except (TypeError, ValueError):
            window = 0.0
        if not math.isfinite(window) or window <= 0:
            window = 0.0

        # 실제 처리시각 기준으로 이미 너무 오래된 replay 이벤트는 latch하지 않습니다.
        cls.prune(state, window, ref)
        if window > 0 and ts < ref - window:
            hits = state["groups"].get(group, {})
            ordered = sorted(
                ((cond, hit) for cond, hit in hits.items()),
                key=lambda item: (-(cls._finite_ts(item[1].get("ts")) or 0.0), item[0]),
            )
            selected = ordered[:required]
            return {
                "matched": len(selected) >= required,
                "count": len(hits),
                "required_count": required,
                "correlation_key": group,
                "condition_key": key,
                "selected": [dict({"condition_key": cond}, **hit) for cond, hit in selected],
                "signature": None,
                "replaced": False,
                "expired_event": True,
            }

        hits = state["groups"].setdefault(group, {})
        previous = hits.get(key)
        previous_ts = cls._finite_ts(previous.get("ts")) if isinstance(previous, dict) else None
        if previous_ts is None or ts >= previous_ts:
            hits[key] = {
                "ts": ts,
                "token": str(token or ""),
                "meta": dict(meta or {}),
            }

        # 늦게 도착한 이벤트까지 포함해도 최신 이벤트와 window를 벗어난 hit는 남기지 않습니다.
        newest = max((cls._finite_ts(x.get("ts")) or ts) for x in hits.values())
        cls.prune(state, window, newest)
        hits = state["groups"].get(group, {})
        ordered = sorted(
            ((cond, hit) for cond, hit in hits.items()),
            key=lambda item: (-(cls._finite_ts(item[1].get("ts")) or 0.0), item[0]),
        )
        selected = ordered[:required]
        matched = len(selected) >= required
        signature = None
        if matched:
            signature_parts: list[object] = [group]
            for cond, hit in sorted(selected, key=lambda item: item[0]):
                signature_parts.extend((cond, hit.get("token"), hit.get("ts")))
            signature = stable_id("LATCH", *signature_parts, length=24)

        return {
            "matched": matched,
            "count": len(hits),
            "required_count": required,
            "correlation_key": group,
            "condition_key": key,
            "selected": [dict({"condition_key": cond}, **hit) for cond, hit in selected],
            "signature": signature,
            "replaced": previous is not None,
        }


@dataclass
class ChainTriggerSpec:
    """시간연쇄의 한 단계. FVG_NEW는 Composer, 나머지는 monitor_OZ가 발생 판독합니다."""

    watch_type: str
    tf: str
    direction: Optional[str] = None
    level_side: Optional[str] = None
    ma_family: Optional[str] = None
    fast_period: Optional[int] = None
    slow_period: Optional[int] = None
    next_window_sec: Optional[float] = None
    # FILTER 모드에서 이 이벤트가 성립한 뒤 TRUE로 유지되는 개별 유효시간.
    valid_sec: Optional[float] = None
    validation_mode: str = "NORMAL"
    trigger_mode: str = "OZ"
    # 공용 조건 매크로/복합조건 계약. watch_orchestrator는 의미를 해석하지 않고 보존/전달만 합니다.
    evaluation_mode: Optional[str] = None  # MA_EXPRESSION infers its existing LIVE/CLOSE default.
    condition_name: Optional[str] = None
    condition_combination: str = "ALL"
    condition_specs: tuple[dict, ...] = ()
    ma_expression: Optional[str] = None

    def validate(self) -> None:
        self.watch_type = str(self.watch_type or "").upper()
        self.tf = normalize_tf(self.tf)
        self.direction = str(self.direction or "").upper() or None
        self.level_side = str(self.level_side or "").upper() or None
        self.ma_family = str(self.ma_family or "").upper() or None
        self.validation_mode = str(self.validation_mode or "NORMAL").upper()
        self.trigger_mode = _canonical_trigger_mode(self.trigger_mode)
        default_evaluation_mode = "LIVE"
        if self.watch_type == "MA_EXPRESSION":
            expression = parse_ma_expression(self.ma_expression)
            self.ma_expression = expression.canonical
            default_evaluation_mode = expression.default_evaluation_mode
        self.evaluation_mode = str(self.evaluation_mode or default_evaluation_mode).upper()
        self.condition_name = str(self.condition_name or "").strip() or None
        self.condition_combination = str(self.condition_combination or "ALL").upper()
        self.condition_specs = tuple(dict(x) for x in (self.condition_specs or ()) if isinstance(x, dict))
        if self.watch_type not in CHAIN_TRIGGER_TYPES:
            raise ValueError(f"지원하지 않는 시간연쇄 트리거: {self.watch_type}")
        if self.evaluation_mode not in {"LIVE", "CLOSE"}:
            raise ValueError(f"잘못된 판정시점: {self.evaluation_mode}")
        if self.condition_combination not in {"ALL", "ANY"}:
            raise ValueError(f"복합조건 combination 오류: {self.condition_combination}")
        if self.watch_type == "COMPOUND_CONDITION" and not self.condition_specs:
            raise ValueError("복합조건 primitive 조건이 비어 있습니다")
        if not self.tf:
            raise ValueError("시간연쇄 트리거 TF 누락")
        if self.direction not in {None, "LONG", "SHORT"}:
            raise ValueError(f"잘못된 시간연쇄 방향: {self.direction}")
        if self.level_side not in {None, "HIGH", "LOW", "BOTH"}:
            raise ValueError(f"잘못된 시간연쇄 level_side: {self.level_side}")
        if self.ma_family not in {None, "EMA", "HMA"}:
            raise ValueError(f"잘못된 MA family: {self.ma_family}")
        if self.watch_type == "OZ_ALERT":
            if self.tf not in OZ_BASE_TFS:
                raise ValueError(
                    f"OZ 연쇄 트리거가 지원하지 않는 시간봉: {self.tf} "
                    f"(지원: {','.join(OZ_BASE_TFS)})"
                )
            if self.validation_mode not in VALIDATION_MODES or self.trigger_mode not in TRIGGER_MODES:
                raise ValueError("OZ 연쇄 트리거 mode 오류")
        if self.fast_period is not None:
            self.fast_period = int(self.fast_period)
        if self.slow_period is not None:
            self.slow_period = int(self.slow_period)

        if self.watch_type in {"EMA_CROSS", "HMA_CROSS"}:
            expected_family = "EMA" if self.watch_type == "EMA_CROSS" else "HMA"
            family = self.ma_family or expected_family
            if family != expected_family:
                raise ValueError(f"{self.watch_type}와 MA family가 맞지 않습니다: {family}")
            default_fast, default_slow = ((50, 200) if family == "EMA" else (6, 17))
            fast = int(self.fast_period if self.fast_period is not None else default_fast)
            slow = int(self.slow_period if self.slow_period is not None else default_slow)
            # Any period goes through the shared MA path; the MA name grammar is the only rule.
            fast = parse_ma_name(f"{family}{fast}")[1]
            slow = parse_ma_name(f"{family}{slow}")[1]
            if fast == slow:
                raise ValueError(f"{family} Cross의 fast/slow period가 같습니다: {fast}")
            self.ma_family = family
            self.fast_period = fast
            self.slow_period = slow

        if self.next_window_sec is not None:
            self.next_window_sec = float(self.next_window_sec)
            if not math.isfinite(self.next_window_sec) or self.next_window_sec <= 0:
                raise ValueError("시간연쇄 next_window_sec 오류")
        if self.valid_sec is not None:
            self.valid_sec = float(self.valid_sec)
            if not math.isfinite(self.valid_sec) or self.valid_sec <= 0:
                raise ValueError("조건 유효시간 valid_sec 오류")

    def label(self) -> str:
        tf_label = WATCH_TF_MAP.get(self.tf, self.tf)
        if self.watch_type == "MA_EXPRESSION":
            return f"{tf_label} {self.ma_expression}"
        if self.watch_type in {"EMA_CROSS", "HMA_CROSS"}:
            family = self.ma_family or ("EMA" if self.watch_type == "EMA_CROSS" else "HMA")
            fast = self.fast_period or (50 if family == "EMA" else 6)
            slow = self.slow_period or (200 if family == "EMA" else 17)
            cross = "골크" if self.direction == "LONG" else "데크" if self.direction == "SHORT" else "크로스"
            return f"{tf_label} {family}{fast}/{slow} {cross}"
        if self.watch_type == "FVG_NEW":
            side = "상승 " if self.direction == "LONG" else "하락 " if self.direction == "SHORT" else ""
            return f"{tf_label} {side}FVG 생성"
        if self.watch_type == "BAR_CLOSE":
            return f"{tf_label} 봉마감"
        if self.watch_type == "WONBI_TOUCH_CLOSE":
            side = "상단 " if self.level_side == "HIGH" else "하단 " if self.level_side == "LOW" else ""
            return f"{tf_label} {side}원비 터치 봉마감"
        if self.watch_type == "COMPOUND_CONDITION":
            side = "매수 " if self.direction == "LONG" else "매도 " if self.direction == "SHORT" else ""
            name = self.condition_name or "복합조건"
            suffix = " 봉마감" if self.evaluation_mode == "CLOSE" else " 성립"
            return f"{tf_label} {side}{name}{suffix}"
        if self.watch_type == "WONBI_TOUCH":
            side = "상단 " if self.level_side == "HIGH" else "하단 " if self.level_side == "LOW" else ""
            return f"{tf_label} {side}원비 터치"
        if self.watch_type == "PERCENTILE_OUT":
            side = "상단 " if self.level_side == "HIGH" else "하단 " if self.level_side == "LOW" else ""
            return f"{tf_label} {side}OUT"
        if self.watch_type == "PERCENTILE_OUT_IN":
            side = "상단 " if self.level_side == "HIGH" else "하단 " if self.level_side == "LOW" else ""
            return f"{tf_label} {side}OUT→IN"
        if self.watch_type == "PREV_DAY_TOUCH":
            side = "전일고가" if self.level_side == "HIGH" else "전일저가" if self.level_side == "LOW" else "전일고저"
            return f"{tf_label} {side} 터치"
        if self.watch_type == "OZ_ALERT":
            side = "매수 " if self.direction == "LONG" else "매도 " if self.direction == "SHORT" else ""
            return f"{tf_label} {side}{oz_profiles.profile_label(self.validation_mode, self.trigger_mode)}"
        return f"{tf_label} {self.watch_type}"

    def to_json(self) -> dict:
        oz_profiles.normalize_profile(self.validation_mode, self.trigger_mode)
        return asdict(self)

    @staticmethod
    def from_json(item: dict) -> "ChainTriggerSpec":
        item = oz_profiles.normalize_watch_payload(item)
        obj = ChainTriggerSpec(
            watch_type=item.get("watch_type"), tf=item.get("tf"),
            direction=item.get("direction"), level_side=item.get("level_side"),
            ma_family=item.get("ma_family"), fast_period=item.get("fast_period"),
            slow_period=item.get("slow_period"), next_window_sec=item.get("next_window_sec"),
            valid_sec=item.get("valid_sec"),
            validation_mode=item.get("validation_mode", "NORMAL"),
            trigger_mode=item.get("trigger_mode", "OZ"),
            evaluation_mode=item.get("evaluation_mode"),
            ma_expression=item.get("ma_expression"),
            condition_name=item.get("condition_name"),
            condition_combination=item.get("condition_combination", "ALL"),
            condition_specs=tuple(item.get("condition_specs") or ()),
        )
        obj.validate()
        return obj


@dataclass
class TimedChainSpec:
    """개인 Watch 연쇄. SEQUENTIAL과 UNORDERED(K-of-N)를 같은 저장 계약으로 관리합니다."""

    chain_id: str
    owner_chat_id: str
    symbol: str
    triggers: tuple[ChainTriggerSpec, ...]
    final_action: str = "OZ"
    final_window_sec: Optional[float] = None
    oz_tfs: tuple[str, ...] = ()
    direction: Optional[str] = None
    validation_mode: str = "NORMAL"
    trigger_mode: str = "OZ"
    # SEQUENTIAL=기존 직렬 연쇄, UNORDERED=공통 유효창 K-of-N, FILTER=조건별 독립 유효시간.
    order_mode: str = "SEQUENTIAL"
    required_count: Optional[int] = None
    unordered_window_sec: Optional[float] = None
    # FILTER는 AND-of-OR 그룹입니다. 예: ((0,), (1, 2)) = A AND (B OR C).
    filter_groups: tuple[tuple[int, ...], ...] = ()
    unordered_latch: dict = field(default_factory=UnorderedConditionLatch.new_state)
    current_watch_ids: dict[str, str] = field(default_factory=dict)
    # 현재 후보가 살아 있는 동안 발생하면 그 후보만 무효화하고 처음부터 다시 대기하는 범용 CANCEL_ON 조건.
    invalidation_triggers: tuple[ChainTriggerSpec, ...] = ()
    invalidation_watch_ids: dict[str, str] = field(default_factory=dict)
    # final Watch가 기간제 반복인지, 최초 1회 알림으로 끝나는지 분리합니다.
    final_watch_persistent: bool = True
    # FILTER에서 '계속'을 쓴 경우 한 겹침 구간 종료 뒤 다시 조건 대기로 돌아갑니다.
    repeat_filter: bool = False
    # 절대 시작시각. None이면 즉시 시작합니다.
    start_at: Optional[float] = None
    started: bool = True
    created_at: float = field(default_factory=time.time)
    stage: int = 0
    stage_deadline: Optional[float] = None
    deadline_elapsed: bool = False
    active_until: Optional[float] = None
    current_watch_id: Optional[str] = None
    active_child_id: Optional[str] = None
    # direction=None인 FILTER가 실제 LONG/SHORT 이벤트로 성립했을 때 최종 OZ 방향을 보존합니다.
    active_direction: Optional[str] = None
    enabled: bool = True

    def validate(self) -> None:
        self.chain_id = str(self.chain_id or "").strip()
        self.owner_chat_id = str(self.owner_chat_id or "").strip()
        self.symbol = str(self.symbol or "").strip()
        self.final_action = str(self.final_action or "OZ").upper()
        self.direction = str(self.direction or "").upper() or None
        self.validation_mode = str(self.validation_mode or "NORMAL").upper()
        self.trigger_mode = _canonical_trigger_mode(self.trigger_mode)
        raw_order = str(self.order_mode or "SEQUENTIAL").strip().upper().replace("-", "_")
        self.order_mode = {
            "ORDERED": "SEQUENTIAL", "SEQ": "SEQUENTIAL", "SEQUENTIAL": "SEQUENTIAL",
            "ANY": "UNORDERED", "ANY_ORDER": "UNORDERED", "UNORDERED": "UNORDERED",
            "LATCH": "UNORDERED", "FILTER": "FILTER", "VALIDITY_FILTER": "FILTER",
        }.get(raw_order, raw_order)
        if self.order_mode not in {"SEQUENTIAL", "UNORDERED", "FILTER"}:
            raise ValueError(f"시간연쇄 order_mode 오류: {self.order_mode}")
        self.active_direction = str(self.active_direction or "").upper() or None
        if self.active_direction not in {None, "LONG", "SHORT"}:
            raise ValueError(f"시간연쇄 active_direction 오류: {self.active_direction}")
        self.final_watch_persistent = bool(self.final_watch_persistent)
        self.repeat_filter = bool(self.repeat_filter)
        self.started = bool(self.started)
        if self.start_at is not None:
            self.start_at = float(self.start_at)
            if not math.isfinite(self.start_at):
                raise ValueError("시간연쇄 start_at 오류")
        self.oz_tfs = tuple(dict.fromkeys(normalize_tf(x) for x in self.oz_tfs if normalize_tf(x)))
        self.triggers = tuple(self.triggers)
        for trig in self.triggers:
            trig.validate()
        self.invalidation_triggers = tuple(self.invalidation_triggers or ())
        trigger_keys = {
            (t.watch_type, t.tf, t.direction, t.level_side, t.ma_family, t.fast_period, t.slow_period, t.ma_expression)
            for t in self.triggers
        }
        for trig in self.invalidation_triggers:
            trig.validate()
            if trig.watch_type == "OZ_ALERT":
                raise ValueError("CANCEL_ON 조건에는 OZ_ALERT를 사용할 수 없습니다")
            cancel_key = (
                trig.watch_type, trig.tf, trig.direction, trig.level_side,
                trig.ma_family, trig.fast_period, trig.slow_period, trig.ma_expression,
            )
            if cancel_key in trigger_keys:
                raise ValueError("CANCEL_ON 조건과 본 연쇄 조건이 동일하여 충돌합니다")
        raw_invalidation_ids = self.invalidation_watch_ids if isinstance(self.invalidation_watch_ids, dict) else {}
        self.invalidation_watch_ids = {
            str(k): str(v) for k, v in raw_invalidation_ids.items() if str(k).strip() and str(v).strip()
        }
        if not self.chain_id or not self.owner_chat_id or not self.symbol:
            raise ValueError("시간연쇄 chain_id/owner/symbol 누락")
        if self.final_action not in CHAIN_FINAL_ACTIONS:
            raise ValueError(f"시간연쇄 final_action 오류: {self.final_action}")
        if self.direction not in {None, "LONG", "SHORT"}:
            raise ValueError(f"시간연쇄 direction 오류: {self.direction}")
        if self.validation_mode not in VALIDATION_MODES or self.trigger_mode not in TRIGGER_MODES:
            raise ValueError("시간연쇄 OZ mode 오류")
        if self.stage < 0 or self.stage > len(self.triggers):
            raise ValueError("시간연쇄 stage 오류")

        if self.order_mode == "FILTER":
            if not self.triggers:
                raise ValueError("조건 유효시간 FILTER는 조건이 1개 이상 필요합니다")
            if any(t.watch_type == "OZ_ALERT" for t in self.triggers):
                raise ValueError("FILTER 조건 묶음 안에는 OZ_ALERT 단계를 사용할 수 없습니다")
            missing = [idx for idx, trig in enumerate(self.triggers) if trig.valid_sec is None]
            if missing:
                raise ValueError("FILTER 조건은 각 조건마다 'N분/시간 동안' 유효시간이 필요합니다")
            raw_groups = self.filter_groups or tuple((idx,) for idx in range(len(self.triggers)))
            normalized_groups: list[tuple[int, ...]] = []
            seen_indexes: set[int] = set()
            for raw_group in raw_groups:
                group = tuple(dict.fromkeys(int(x) for x in raw_group))
                if not group or any(x < 0 or x >= len(self.triggers) for x in group):
                    raise ValueError("FILTER AND/OR 그룹 인덱스 오류")
                normalized_groups.append(group)
                seen_indexes.update(group)
            if seen_indexes != set(range(len(self.triggers))):
                raise ValueError("FILTER AND/OR 그룹에 모든 조건이 정확히 포함되어야 합니다")
            self.filter_groups = tuple(normalized_groups)
            self.required_count = None
            self.unordered_window_sec = None
            self.unordered_latch = UnorderedConditionLatch.normalize_state(self.unordered_latch)
            raw_ids = self.current_watch_ids if isinstance(self.current_watch_ids, dict) else {}
            self.current_watch_ids = {
                str(k): str(v) for k, v in raw_ids.items() if str(k).strip() and str(v).strip()
            }
            if self.active_child_id is None:
                self.stage = 0
            self.stage_deadline = None
            self.current_watch_id = None
        elif self.order_mode == "UNORDERED":
            if len(self.triggers) < 2:
                raise ValueError("순서무관 Watch는 조건이 2개 이상 필요합니다")
            if any(t.watch_type == "OZ_ALERT" for t in self.triggers):
                raise ValueError("순서무관 조건 묶음 안에는 OZ_ALERT 단계를 사용할 수 없습니다")
            required = len(self.triggers) if self.required_count is None else int(self.required_count)
            if required <= 0 or required > len(self.triggers):
                raise ValueError(
                    f"순서무관 required_count 오류: {required}/{len(self.triggers)}"
                )
            self.required_count = required
            if self.unordered_window_sec is not None:
                self.unordered_window_sec = float(self.unordered_window_sec)
                if not math.isfinite(self.unordered_window_sec) or self.unordered_window_sec <= 0:
                    raise ValueError("순서무관 유효기간 오류")
            self.unordered_latch = UnorderedConditionLatch.normalize_state(self.unordered_latch)
            raw_ids = self.current_watch_ids if isinstance(self.current_watch_ids, dict) else {}
            self.current_watch_ids = {
                str(k): str(v) for k, v in raw_ids.items() if str(k).strip() and str(v).strip()
            }
            # UNORDERED는 직렬 stage/deadline을 사용하지 않습니다.
            if self.active_child_id is None:
                self.stage = 0
            self.stage_deadline = None
            self.current_watch_id = None
        else:
            self.required_count = None
            self.unordered_window_sec = None
            self.filter_groups = ()
            self.unordered_latch = UnorderedConditionLatch.new_state()
            self.current_watch_ids = {}

        if self.final_action == "OZ":
            if not self.oz_tfs:
                raise ValueError("시간연쇄 OZ TF 누락")
            invalid_oz_tfs = [tf for tf in self.oz_tfs if tf not in OZ_BASE_TFS]
            if invalid_oz_tfs:
                raise ValueError(
                    f"시간연쇄 OZ가 지원하지 않는 시간봉: {','.join(invalid_oz_tfs)} "
                    f"(지원: {','.join(OZ_BASE_TFS)})"
                )
            if self.final_window_sec is not None:
                self.final_window_sec = float(self.final_window_sec)
                if not math.isfinite(self.final_window_sec) or self.final_window_sec <= 0:
                    raise ValueError("시간연쇄 OZ 기간 오류")

    def summary(self) -> str:
        parts = []
        if self.start_at is not None and not self.started:
            try:
                when = dt.datetime.fromtimestamp(float(self.start_at), tz=ZoneInfo("Asia/Seoul"))
                parts.append(f"{when:%m-%d %H:%M}부터")
            except Exception:
                parts.append("예약시각부터")

        if self.order_mode == "FILTER":
            rendered_groups = []
            for group in self.filter_groups:
                options = [
                    f"{self.triggers[idx].label()}({format_duration_ko(self.triggers[idx].valid_sec or 0)} 유효)"
                    for idx in group
                ]
                rendered_groups.append("(" + " OR ".join(options) + ")")
            filter_text = " AND ".join(rendered_groups)
            if self.repeat_filter:
                filter_text += " · 계속 반복"
            parts.append(filter_text)
        elif self.order_mode == "UNORDERED":
            labels = " ↔ ".join(t.label() for t in self.triggers)
            count_text = f"{self.required_count}/{len(self.triggers)}"
            window_text = (
                f" · {format_duration_ko(self.unordered_window_sec)} 유효"
                if self.unordered_window_sec is not None else ""
            )
            parts.append(f"[{labels}] 순서무관 {count_text}{window_text}")
        else:
            for idx, trig in enumerate(self.triggers):
                parts.append(trig.label())
                if idx < len(self.triggers) - 1 and trig.next_window_sec:
                    parts.append(f"→ {format_duration_ko(trig.next_window_sec)} 안에")

        if self.invalidation_triggers:
            cancel_text = " / ".join(t.label() for t in self.invalidation_triggers)
            parts.append(f"· 취소조건: {cancel_text}")

        if self.final_action == "OZ":
            side = "매수 " if self.direction == "LONG" else "매도 " if self.direction == "SHORT" else ""
            profile_text = oz_profiles.profile_label(self.validation_mode, self.trigger_mode)
            if self.order_mode == "FILTER":
                prefix = "조건 겹침 동안 "
            elif self.final_window_sec is not None:
                prefix = f"{format_duration_ko(self.final_window_sec)} 동안 "
            elif self.final_watch_persistent:
                prefix = "계속 "
            else:
                prefix = ""
            tf_text = ",".join(WATCH_TF_MAP.get(tf, tf) for tf in self.oz_tfs)
            parts.append(f"→ {prefix}{tf_text} {side}{profile_text}")
        else:
            parts.append("→ 마지막 조건 알림")
        return " ".join(parts)

    def to_json(self) -> dict:
        oz_profiles.normalize_profile(self.validation_mode, self.trigger_mode)
        return {
            "chain_id": self.chain_id, "owner_chat_id": self.owner_chat_id, "symbol": self.symbol,
            "triggers": [x.to_json() for x in self.triggers], "final_action": self.final_action,
            "final_window_sec": self.final_window_sec, "oz_tfs": list(self.oz_tfs),
            "direction": self.direction, "validation_mode": self.validation_mode,
            "trigger_mode": self.trigger_mode, "order_mode": self.order_mode,
            "required_count": self.required_count, "unordered_window_sec": self.unordered_window_sec,
            "filter_groups": [list(group) for group in self.filter_groups],
            "unordered_latch": self.unordered_latch, "current_watch_ids": dict(self.current_watch_ids),
            "invalidation_triggers": [x.to_json() for x in self.invalidation_triggers],
            "invalidation_watch_ids": dict(self.invalidation_watch_ids),
            "final_watch_persistent": self.final_watch_persistent,
            "repeat_filter": self.repeat_filter,
            "start_at": self.start_at, "started": self.started,
            "created_at": self.created_at, "stage": self.stage,
            "stage_deadline": self.stage_deadline, "active_until": self.active_until,
            "deadline_elapsed": self.deadline_elapsed,
            "current_watch_id": self.current_watch_id, "active_child_id": self.active_child_id,
            "active_direction": self.active_direction, "enabled": self.enabled,
        }

    @staticmethod
    def from_json(item: dict) -> "TimedChainSpec":
        item = oz_profiles.normalize_watch_payload(item)
        spec = TimedChainSpec(
            chain_id=item.get("chain_id"), owner_chat_id=item.get("owner_chat_id"), symbol=item.get("symbol"),
            triggers=tuple(ChainTriggerSpec.from_json(x) for x in item.get("triggers", [])),
            final_action=item.get("final_action", "OZ"), final_window_sec=item.get("final_window_sec"),
            oz_tfs=tuple(item.get("oz_tfs", [])), direction=item.get("direction"),
            validation_mode=item.get("validation_mode", "NORMAL"), trigger_mode=item.get("trigger_mode", "OZ"),
            order_mode=item.get("order_mode", "SEQUENTIAL"), required_count=item.get("required_count"),
            unordered_window_sec=item.get("unordered_window_sec"),
            filter_groups=tuple(tuple(x) for x in (item.get("filter_groups") or ())),
            unordered_latch=item.get("unordered_latch") or {},
            current_watch_ids=item.get("current_watch_ids") or {},
            invalidation_triggers=tuple(
                ChainTriggerSpec.from_json(x) for x in item.get("invalidation_triggers", []) if isinstance(x, dict)
            ),
            invalidation_watch_ids=item.get("invalidation_watch_ids") or {},
            final_watch_persistent=bool(item.get("final_watch_persistent", True)),
            repeat_filter=bool(item.get("repeat_filter", False)),
            start_at=item.get("start_at"), started=bool(item.get("started", True)),
            created_at=float(item.get("created_at", time.time())), stage=int(item.get("stage", 0)),
            stage_deadline=item.get("stage_deadline"), active_until=item.get("active_until"),
            current_watch_id=item.get("current_watch_id"), active_child_id=item.get("active_child_id"),
            active_direction=item.get("active_direction"), enabled=bool(item.get("enabled", True)),
        )
        if spec.stage_deadline is not None:
            spec.stage_deadline = float(spec.stage_deadline)
        if spec.active_until is not None:
            spec.active_until = float(spec.active_until)
        spec.deadline_elapsed = bool(item.get('deadline_elapsed', False))
        spec.validate()
        return spec


class WatchOrchestrator:
    """Owns personal TimedChainSpec runtime state and child-watch transitions."""

    def __init__(
        self,
        lock: threading.RLock,
        state_path: Path,
        config: dict[str, str],
        push_callback: Callable[[dict], None],
        notify_callback: Callable[[str, Optional[str]], bool],
        mark_dirty_callback: Callable[[], None],
        signal_context_callback: Optional[Callable[[TimedChainSpec, dict], dict]] = None,
    ) -> None:
        self.lock = lock
        self.state_path = Path(state_path)
        self.config = config
        self.push_callback = push_callback
        self.notify_callback = notify_callback
        self.mark_dirty_callback = mark_dirty_callback
        self.signal_context_callback = signal_context_callback
        self.chains: dict[str, TimedChainSpec] = {}
        self.rejected_chains: list[dict] = []
        self.pending_notifications: dict[str, dict] = {}
        self._last_refresh = 0.0

    def load_state(self) -> None:
        if not domain_memory.exists(self.state_path):
            return
        try:
            raw = read_json(self.state_path)
            self.rejected_chains = list(raw.get("rejected_chains", []))
            restored: dict[str, TimedChainSpec] = {}
            for item in raw.get("chains", []) if isinstance(raw, dict) else []:
                try:spec = TimedChainSpec.from_json(load_saved_oz(item, path=f"chains.{item.get('chain_id')}"))
                except ValueError as exc:
                    self.rejected_chains.append({'chain_id':item.get('chain_id'),'reason':str(exc),'original':dict(item)})
                    logging.error('[OZ 시간연쇄 복원 격리] chain_id=%s | %s',item.get('chain_id'),exc)
                    continue
                if spec.owner_chat_id and spec.enabled:
                    restored[spec.chain_id] = spec
            with self.lock:
                self.chains.clear()
                self.chains.update(restored)
                self.pending_notifications = dict(raw.get('pending_notifications') or {})
        except Exception:
            logging.exception("[Composer 시간연쇄] 상태 복원 실패")

    def save_state_locked(self) -> None:
        payload = {"version": 2, "chains": [x.to_json() for x in self.chains.values()],
                   "pending_notifications": self.pending_notifications, "rejected_chains": self.rejected_chains}
        _atomic_write_json(self.state_path, payload)

    def _queue_completion_locked(self, chain, event, message):
        token = identity('CHAIN_COMPLETE', chain.chain_id, chain.created_at,
                         event.get('event_id') or event.get('event_time'), chain.stage)
        self.pending_notifications.setdefault(token, {'text': message, 'recipient': chain.owner_chat_id,
                                                       'chain_id': chain.chain_id,
                                                       'signal_context': self._completion_signal_context(chain, event)})
        self.chains.pop(chain.chain_id, None)

    def _completion_signal_context(self, chain: TimedChainSpec, event: dict) -> dict:
        # Only a completed OZ stage carries that pattern's B0 and neckline.
        # Earlier OZ stages must not leak into a later generic completion.
        final_oz = (chain.order_mode == 'SEQUENTIAL' and chain.stage == len(chain.triggers)
                    and bool(chain.triggers) and chain.triggers[-1].watch_type == 'OZ_ALERT')
        if final_oz:
            context = deepcopy(event)
            context['signal_source'] = 'OZ'
            context['signal_tf'] = context.get('signal_tf') or context.get('source_tf') or context.get('tf')
        else:
            context = deepcopy(self.signal_context_callback(chain, event)
                               if self.signal_context_callback is not None else event)
            context['signal_source'] = 'SIGNAL'
            for key in ('b0_price', 'b0_time', 'neckline_price', 'neckline_time_ms'):
                context.pop(key, None)
        context.setdefault('symbol', chain.symbol)
        context['direction'] = context.get('direction') or chain.direction or event.get('direction', '')
        context.setdefault('signal_strategy', 'WATCH')
        context.setdefault('source_spec_id', chain.chain_id)
        context.setdefault('event_time', event.get('event_time'))
        return context

    def retry_notifications(self):
        # The completion intent and removal of its chain share one atomic state write.
        # Delivery retry never advances the strategy a second time.
        with self.lock:
            for token, item in list(self.pending_notifications.items()):
                try:
                    delivered = self.notify_callback(item['text'], item['recipient'], event_id=token,
                                                     watch_id=item.get('chain_id'),
                                                     signal_context=deepcopy(item.get('signal_context') or {}))
                except Exception:
                    logging.exception('[Chain] pending notification failed | %s', token)
                    delivered = False
                if delivered:
                    self.pending_notifications.pop(token, None)
                    self.save_state_locked()

    @staticmethod
    def _unordered_condition_key(index: int) -> str:
        return f"IDX:{int(index)}"

    @staticmethod
    def _event_ts(event: dict, fallback: float) -> float:
        value = event.get("event_time")
        if value is None:
            value = event.get("fvg_time")
        try:
            ts = float(value)
            if math.isfinite(ts):
                return ts
        except (TypeError, ValueError):
            pass
        if value is not None:
            try:
                text = str(value).strip().replace("Z", "+00:00")
                parsed = dt.datetime.fromisoformat(text)
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=dt.timezone.utc)
                return parsed.timestamp()
            except Exception:
                pass
        return float(fallback)

    @staticmethod
    def _cancel_action_for_trigger(trig: ChainTriggerSpec) -> str:
        if trig.watch_type == "OZ_ALERT":
            return "CANCEL_MANUAL"
        if trig.watch_type == "FVG_NEW":
            return "CANCEL_FVG_EVENT"
        if trig.watch_type in LOCAL_CHAIN_TRIGGER_TYPES:
            return "CANCEL_LOCAL_EVENT"
        return "CANCEL_GENERIC"

    @staticmethod
    def _invalidation_stage(index: int) -> int:
        # 정상 chain_stage(0 이상)와 절대 충돌하지 않는 음수 namespace.
        return -(int(index) + 1)

    @staticmethod
    def _unordered_has_hits(chain: TimedChainSpec) -> bool:
        raw = chain.unordered_latch if isinstance(chain.unordered_latch, dict) else {}
        groups = raw.get("groups") if isinstance(raw, dict) else {}
        return bool(isinstance(groups, dict) and any(bool(hits) for hits in groups.values()))

    @classmethod
    def _candidate_active_locked(cls, chain: TimedChainSpec) -> bool:
        if chain.active_child_id:
            return True
        if chain.order_mode in {"UNORDERED", "FILTER"}:
            return cls._unordered_has_hits(chain)
        return chain.stage > 0

    @classmethod
    def _invalidation_payload_for_index_locked(cls, chain: TimedChainSpec, index: int) -> Optional[dict]:
        if not chain.started or index < 0 or index >= len(chain.invalidation_triggers):
            return None
        trig = chain.invalidation_triggers[index]
        key = str(index)
        wid = chain.invalidation_watch_ids.get(key) or stable_id(
            "CHAINCANCEL", chain.chain_id, index, length=20
        )
        chain.invalidation_watch_ids[key] = wid
        if trig.watch_type == "OZ_ALERT":
            return None
        action = (
            "FVG_EVENT_WATCH" if trig.watch_type == "FVG_NEW"
            else "LOCAL_EVENT_WATCH" if trig.watch_type in LOCAL_CHAIN_TRIGGER_TYPES
            else "GENERIC_WATCH"
        )
        return {
            "action": action,
            "watch_id": wid, "watch_type": trig.watch_type,
            "timeframes": [trig.tf], "symbol": chain.symbol, "direction": trig.direction,
            "level_side": trig.level_side, "ma_family": trig.ma_family,
            "fast_period": trig.fast_period, "slow_period": trig.slow_period,
            **({"ma_expression": trig.ma_expression} if trig.ma_expression is not None else {}),
            "evaluation_mode": trig.evaluation_mode,
            "condition_name": trig.condition_name,
            "condition_combination": trig.condition_combination,
            "condition_specs": [dict(x) for x in trig.condition_specs],
            "persistent": True, "request_chat_id": chain.owner_chat_id,
            "chain_id": chain.chain_id, "chain_stage": cls._invalidation_stage(index), "silent": True,
            "chain_cancel": True, "cancel_index": index,
        }

    @classmethod
    def invalidation_payloads_locked(cls, chain: TimedChainSpec) -> list[dict]:
        """현재 후보가 생긴 뒤에만 CANCEL_ON watcher를 arm합니다."""
        if not chain.invalidation_triggers or not cls._candidate_active_locked(chain):
            return []
        payloads: list[dict] = []
        for index in range(len(chain.invalidation_triggers)):
            payload = cls._invalidation_payload_for_index_locked(chain, index)
            if payload:
                payloads.append(payload)
        return payloads

    @classmethod
    def _cancel_invalidation_payloads_locked(cls, chain: TimedChainSpec) -> list[dict]:
        payloads: list[dict] = []
        for raw_index, wid in list(chain.invalidation_watch_ids.items()):
            try:
                index = int(raw_index)
            except (TypeError, ValueError):
                continue
            if index < 0 or index >= len(chain.invalidation_triggers):
                continue
            payloads.append({
                "action": cls._cancel_action_for_trigger(chain.invalidation_triggers[index]),
                "watch_id": wid, "request_chat_id": chain.owner_chat_id,
            })
        chain.invalidation_watch_ids = {}
        return payloads

    @classmethod
    def _cancel_filter_trigger_payloads_locked(cls, chain: TimedChainSpec) -> list[dict]:
        payloads: list[dict] = []
        for raw_index, wid in list(chain.current_watch_ids.items()):
            try:
                index = int(raw_index)
            except (TypeError, ValueError):
                continue
            if 0 <= index < len(chain.triggers):
                payloads.append({
                    "action": cls._cancel_action_for_trigger(chain.triggers[index]),
                    "watch_id": wid, "request_chat_id": chain.owner_chat_id,
                })
        chain.current_watch_ids = {}
        return payloads

    @classmethod
    def _reset_candidate_on_invalidation_locked(
        cls, chain: TimedChainSpec, fired: ChainTriggerSpec
    ) -> tuple[list[dict], str, str]:
        """현재 후보만 폐기하고 같은 Watch를 처음 조건부터 다시 arm합니다."""
        pushes: list[dict] = []
        if chain.order_mode in {"UNORDERED", "FILTER"}:
            for raw_index, wid in list(chain.current_watch_ids.items()):
                try:
                    index = int(raw_index)
                except (TypeError, ValueError):
                    continue
                if 0 <= index < len(chain.triggers):
                    pushes.append({
                        "action": cls._cancel_action_for_trigger(chain.triggers[index]),
                        "watch_id": wid, "request_chat_id": chain.owner_chat_id,
                    })
        elif chain.current_watch_id and chain.stage < len(chain.triggers):
            pushes.append({
                "action": cls._cancel_action_for_trigger(chain.triggers[chain.stage]),
                "watch_id": chain.current_watch_id, "request_chat_id": chain.owner_chat_id,
            })

        if chain.active_child_id:
            pushes.append({
                "action": "CANCEL_MANUAL", "watch_id": chain.active_child_id,
                "request_chat_id": chain.owner_chat_id,
            })

        pushes.extend(cls._cancel_invalidation_payloads_locked(chain))
        chain.stage = 0
        chain.stage_deadline = None
        chain.active_until = None
        chain.current_watch_id = None
        chain.current_watch_ids = {}
        chain.active_child_id = None
        chain.unordered_latch = UnorderedConditionLatch.new_state()
        pushes.extend(cls.trigger_payloads_locked(chain))
        return (
            pushes,
            f"↩️ 무효화 조건 성립 · {fired.label()}\n현재 후보 취소 · 처음 조건부터 다시 대기",
            chain.owner_chat_id,
        )

    @staticmethod
    def _trigger_payload_for_index_locked(
        chain: TimedChainSpec, index: int, *, persistent: bool
    ) -> Optional[dict]:
        if not chain.started or index < 0 or index >= len(chain.triggers):
            return None
        trig = chain.triggers[index]
        if chain.order_mode in {"UNORDERED", "FILTER"}:
            key = str(index)
            prefix = "CHAINFILTER" if chain.order_mode == "FILTER" else "CHAINUNORD"
            wid = chain.current_watch_ids.get(key) or stable_id(
                prefix, chain.chain_id, index, length=20
            )
            chain.current_watch_ids[key] = wid
        else:
            wid = chain.current_watch_id or stable_id("CHAINTRG", chain.chain_id, index, length=20)
            chain.current_watch_id = wid

        if trig.watch_type == "OZ_ALERT":
            return {
                "action": "MANUAL_WATCH", "watch_id": wid,
                "timeframes": [trig.tf], "symbol": chain.symbol,
                "direction": trig.direction, "persistent": bool(persistent),
                "request_chat_id": chain.owner_chat_id,
                "validation_mode": trig.validation_mode,
                "trigger_mode": trig.trigger_mode,
                "source_spec_id": chain.chain_id,
                "source_name": "시간연쇄 OZ 단계",
            }
        action = (
            "FVG_EVENT_WATCH" if trig.watch_type == "FVG_NEW"
            else "LOCAL_EVENT_WATCH" if trig.watch_type in LOCAL_CHAIN_TRIGGER_TYPES
            else "GENERIC_WATCH"
        )
        return {
            "action": action,
            "watch_id": wid, "watch_type": trig.watch_type,
            "timeframes": [trig.tf], "symbol": chain.symbol, "direction": trig.direction,
            "level_side": trig.level_side, "ma_family": trig.ma_family,
            "fast_period": trig.fast_period, "slow_period": trig.slow_period,
            **({"ma_expression": trig.ma_expression} if trig.ma_expression is not None else {}),
            "evaluation_mode": trig.evaluation_mode,
            "condition_name": trig.condition_name,
            "condition_combination": trig.condition_combination,
            "condition_specs": [dict(x) for x in trig.condition_specs],
            "persistent": bool(persistent), "request_chat_id": chain.owner_chat_id,
            "chain_id": chain.chain_id, "chain_stage": index, "silent": True,
        }

    @classmethod
    def trigger_payload_locked(cls, chain: TimedChainSpec) -> Optional[dict]:
        """기존 SEQUENTIAL 단일 단계 payload. 하위 호환을 위해 유지합니다."""
        if chain.order_mode in {"UNORDERED", "FILTER"}:
            payloads = cls.trigger_payloads_locked(chain)
            return payloads[0] if payloads else None
        if not chain.started or chain.stage >= len(chain.triggers):
            return None
        return cls._trigger_payload_for_index_locked(chain, chain.stage, persistent=False)

    @classmethod
    def trigger_payloads_locked(cls, chain: TimedChainSpec) -> list[dict]:
        """현재 연쇄가 필요로 하는 모든 선행조건 Watch payload를 반환합니다."""
        if not chain.started:
            return []
        if chain.active_child_id and chain.order_mode != "FILTER":
            return []
        if chain.order_mode not in {"UNORDERED", "FILTER"}:
            payload = cls.trigger_payload_locked(chain)
            return [payload] if payload else []
        payloads: list[dict] = []
        for index in range(len(chain.triggers)):
            payload = cls._trigger_payload_for_index_locked(chain, index, persistent=True)
            if payload:
                payloads.append(payload)
        return payloads

    @staticmethod
    def final_payload_locked(chain: TimedChainSpec) -> Optional[dict]:
        if not chain.started or chain.final_action != "OZ" or not chain.active_child_id:
            return None
        return {
            "action": "MANUAL_WATCH", "watch_id": chain.active_child_id,
            "timeframes": list(chain.oz_tfs), "symbol": chain.symbol,
            "direction": chain.active_direction or chain.direction, "persistent": bool(chain.final_watch_persistent),
            "request_chat_id": chain.owner_chat_id,
            "validation_mode": chain.validation_mode, "trigger_mode": chain.trigger_mode,
            "source_name": "시간연쇄", "source_chain_id": chain.chain_id,
            "source_spec_id": chain.chain_id,
        }

    def _activate_locked(self, chain: TimedChainSpec, now: float) -> list[dict]:
        chain.started = True
        if chain.triggers:
            return self.trigger_payloads_locked(chain)
        chain.stage = 0
        chain.active_until = (
            now + float(chain.final_window_sec)
            if chain.final_window_sec is not None else None
        )
        chain.active_child_id = stable_id("OZARM:CHAIN", chain.chain_id, int(now * 1000), length=20)
        payload = self.final_payload_locked(chain)
        return [payload] if payload else []

    def add_chain(self, chain: TimedChainSpec) -> None:
        immediate_payloads: list[dict] = []
        now = time.time()
        scheduled = chain.start_at is not None and float(chain.start_at) > now
        with self.lock:
            chain.validate()
            if scheduled:
                chain.started = False
                chain.current_watch_id = None
                chain.current_watch_ids = {}
                chain.invalidation_watch_ids = {}
                chain.active_child_id = None
                chain.active_until = None
            else:
                chain.started = True
            self.chains[chain.chain_id] = chain
            if not scheduled:
                immediate_payloads = self._activate_locked(chain, now)
            self.save_state_locked()
            self.mark_dirty_callback()
        for payload in immediate_payloads:
            self.push_callback(payload)
        if scheduled:
            try:
                when = dt.datetime.fromtimestamp(float(chain.start_at), tz=ZoneInfo("Asia/Seoul"))
                head = f"⏰ 예약 Watch 등록 · {when:%Y-%m-%d %H:%M}부터\n"
            except Exception:
                head = "⏰ 예약 Watch 등록\n"
            self.notify_callback(head + chain.summary(), chain.owner_chat_id,
                                 watch_id=chain.chain_id, watch_registration=True, signal_context={})
        else:
            title = (
                "🧩 조건 유효시간 필터 등록\n" if chain.order_mode == "FILTER"
                else "🔀 순서무관 Watch 등록\n" if chain.order_mode == "UNORDERED"
                else "⏱️ 시간연쇄 등록\n"
            )
            self.notify_callback(title + chain.summary(), chain.owner_chat_id,
                                 watch_id=chain.chain_id, watch_registration=True, signal_context={})
        if logging.getLogger().isEnabledFor(logging.INFO):
            logging.info("⏱️ [Composer 시간연쇄] 등록 | %s | %s", chain.chain_id, chain.summary())

    def _advance_locked(self, chain: TimedChainSpec, event: dict, now: float):
        """기존 SEQUENTIAL 전용 stage advance."""
        if chain.order_mode != "SEQUENTIAL" or chain.stage >= len(chain.triggers):
            return None, None, None
        fired = chain.triggers[chain.stage]
        chain.current_watch_id = None
        chain.stage += 1
        next_payload = None
        user_message = None
        target = None

        if chain.stage < len(chain.triggers):
            if fired.next_window_sec:
                next_deadline = now + float(fired.next_window_sec)
                chain.stage_deadline = min(chain.stage_deadline, next_deadline) if chain.stage_deadline is not None else next_deadline
            next_payload = self.trigger_payload_locked(chain)
            wait_label = f" · {format_duration_ko(fired.next_window_sec)} 제한" if fired.next_window_sec else ""
            user_message = f"✅ {fired.label()} 성립\n다음 조건 대기{wait_label}: {chain.triggers[chain.stage].label()}"
            target = chain.owner_chat_id
        elif chain.final_action == "NOTIFY":
            user_message = str(event.get("message") or fired.label()).strip()
            self._queue_completion_locked(chain, event, user_message)
            user_message = None
        else:
            chain.active_until = (
                now + float(chain.final_window_sec)
                if chain.final_window_sec is not None else None
            )
            chain.active_child_id = stable_id("OZARM:CHAIN", chain.chain_id, int(now * 1000), length=20)
            next_payload = self.final_payload_locked(chain)
            side = "매수 " if chain.direction == "LONG" else "매도 " if chain.direction == "SHORT" else ""
            if chain.final_window_sec is not None:
                window_text = f"{format_duration_ko(chain.final_window_sec)} 동안 "
            elif chain.final_watch_persistent:
                window_text = "계속 "
            else:
                window_text = ""
            tf_text = ",".join(WATCH_TF_MAP.get(tf, tf) for tf in chain.oz_tfs)
            user_message = (
                f"✅ {fired.label()} 성립\n"
                f"▶ 지금부터 {window_text}{side}{tf_text} 올존 감시"
            )
            target = chain.owner_chat_id
        return next_payload, user_message, target

    def _complete_unordered_locked(
        self, chain: TimedChainSpec, event: dict, now: float, latch_result: dict
    ) -> tuple[list[dict], Optional[str], Optional[str]]:
        pushes: list[dict] = []
        for raw_index, wid in list(chain.current_watch_ids.items()):
            try:
                index = int(raw_index)
            except (TypeError, ValueError):
                continue
            if index < 0 or index >= len(chain.triggers):
                continue
            pushes.append({
                "action": self._cancel_action_for_trigger(chain.triggers[index]),
                "watch_id": wid,
                "request_chat_id": chain.owner_chat_id,
            })
        chain.current_watch_ids = {}
        chain.unordered_latch = UnorderedConditionLatch.new_state()
        chain.stage = len(chain.triggers)

        matched = int(latch_result.get("required_count") or chain.required_count or len(chain.triggers))
        total = len(chain.triggers)
        if chain.final_action == "NOTIFY":
            pushes.extend(self._cancel_invalidation_payloads_locked(chain))
            self._queue_completion_locked(chain, event, f"✅ 순서무관 조건 성립 · {matched}/{total}")
            return pushes, None, None

        chain.active_until = (
            now + float(chain.final_window_sec)
            if chain.final_window_sec is not None else None
        )
        chain.active_child_id = stable_id("OZARM:CHAIN", chain.chain_id, int(now * 1000), length=20)
        final_payload = self.final_payload_locked(chain)
        if final_payload:
            pushes.append(final_payload)
        side = "매수 " if chain.direction == "LONG" else "매도 " if chain.direction == "SHORT" else ""
        tf_text = ",".join(WATCH_TF_MAP.get(tf, tf) for tf in chain.oz_tfs)
        if chain.final_window_sec is not None:
            window_text = f"{format_duration_ko(chain.final_window_sec)} 동안 "
        elif chain.final_watch_persistent:
            window_text = "계속 "
        else:
            window_text = ""
        message = (
            f"✅ 순서무관 조건 성립 · {matched}/{total}\n"
            f"▶ 지금부터 {window_text}{side}{tf_text} 올존 감시"
        )
        return pushes, message, chain.owner_chat_id

    @classmethod
    def _prune_filter_hits_locked(cls, chain: TimedChainSpec, now: float) -> bool:
        """FILTER hit를 각 trigger.valid_sec 기준으로 독립 만료합니다."""
        if chain.order_mode != "FILTER":
            return False

        def _expired(_group: str, key: str, hit: dict) -> bool:
            if not str(key).startswith("IDX:"):
                return True
            try:
                index = int(str(key).split(":", 1)[1])
            except (TypeError, ValueError):
                return True
            if index < 0 or index >= len(chain.triggers):
                return True
            valid_sec = chain.triggers[index].valid_sec
            if valid_sec is None:
                return True
            try:
                ts = float(hit.get("ts"))
            except (TypeError, ValueError):
                return True
            return (not math.isfinite(ts)) or float(now) >= ts + float(valid_sec)

        return UnorderedConditionLatch.remove_where(chain.unordered_latch, _expired)

    @classmethod
    def _filter_match_locked(cls, chain: TimedChainSpec, correlation_key: str, now: float) -> dict:
        """AND-of-OR FILTER를 판정하고 현재 겹치는 유효시간의 끝을 반환합니다."""
        cls._prune_filter_hits_locked(chain, now)
        raw = chain.unordered_latch if isinstance(chain.unordered_latch, dict) else {}
        groups = raw.get("groups") if isinstance(raw, dict) else {}
        key = str(correlation_key or "").upper()
        hits: dict[str, dict] = {}
        if isinstance(groups, dict):
            # 방향 없는 BAR_CLOSE 같은 공통 이벤트(AUTO)는 LONG/SHORT 어느 쪽과도 결합할 수 있습니다.
            auto_hits = groups.get("AUTO")
            if isinstance(auto_hits, dict):
                hits.update(auto_hits)
            directional_hits = groups.get(key)
            if isinstance(directional_hits, dict):
                hits.update(directional_hits)

        selected: list[dict] = []
        for group in chain.filter_groups:
            options: list[tuple[float, int, dict]] = []
            for index in group:
                hit = hits.get(cls._unordered_condition_key(index))
                if not isinstance(hit, dict):
                    continue
                try:
                    ts = float(hit.get("ts"))
                    valid_sec = float(chain.triggers[index].valid_sec or 0.0)
                except (TypeError, ValueError):
                    continue
                expires_at = ts + valid_sec
                if expires_at <= now:
                    continue
                options.append((expires_at, index, hit))
            if not options:
                return {"matched": False, "selected": [], "active_until": None}
            # OR 그룹에서는 가장 오래 살아남는 hit를 선택하여 실제 겹침 구간을 보존합니다.
            expires_at, index, hit = max(options, key=lambda x: (x[0], x[1]))
            selected.append({
                "condition_key": cls._unordered_condition_key(index),
                "index": index, "expires_at": expires_at, **dict(hit),
            })

        active_until = min(float(x["expires_at"]) for x in selected) if selected else None
        return {
            "matched": bool(selected) and active_until is not None and active_until > now,
            "selected": selected, "active_until": active_until,
        }

    def _complete_filter_locked(
        self, chain: TimedChainSpec, event: dict, now: float, correlation_key: str, match: dict
    ) -> tuple[list[dict], Optional[str], Optional[str]]:
        """FILTER 성립 시 OZ를 arm하거나 기존 겹침창을 rolling 연장합니다.

        선행 조건 Watch/latch는 유지합니다. 따라서 같은 조건이 다시 발생하면
        해당 trigger.valid_sec가 최신 이벤트 기준으로 갱신됩니다.
        """
        pushes: list[dict] = []
        if correlation_key in {"LONG", "SHORT"} and chain.direction is None:
            matched_direction = correlation_key
        else:
            matched_direction = chain.direction

        if chain.final_action == "NOTIFY":
            pushes.extend(self._cancel_filter_trigger_payloads_locked(chain))
            pushes.extend(self._cancel_invalidation_payloads_locked(chain))
            self._queue_completion_locked(chain, event, "✅ 조건 유효시간 필터 성립")
            return pushes, None, None

        active_until = match.get("active_until")
        try:
            active_until = float(active_until)
        except (TypeError, ValueError):
            active_until = now
        if chain.final_window_sec is not None:
            active_until = min(active_until, now + float(chain.final_window_sec))
        if active_until <= now:
            return pushes, None, None

        # 이미 반대 방향 FILTER OZ가 살아 있으면 그 방향의 hit는 저장만 하고 현재 child는 건드리지 않습니다.
        if (
            chain.active_child_id and chain.active_direction in {"LONG", "SHORT"}
            and matched_direction in {"LONG", "SHORT"}
            and chain.active_direction != matched_direction
        ):
            return pushes, None, None

        was_active = bool(chain.active_child_id)
        old_until = float(chain.active_until or 0.0)
        if was_active:
            chain.active_until = max(old_until, active_until)
        else:
            chain.active_direction = matched_direction
            chain.active_until = active_until
            chain.active_child_id = stable_id("OZARM:FILTER", chain.chain_id, int(now * 1000), length=20)
            chain.final_watch_persistent = True
            final_payload = self.final_payload_locked(chain)
            if final_payload:
                pushes.append(final_payload)

        direction = chain.active_direction or chain.direction
        side = "매수 " if direction == "LONG" else "매도 " if direction == "SHORT" else ""
        tf_text = ",".join(WATCH_TF_MAP.get(tf, tf) for tf in chain.oz_tfs)
        remain = max(0.0, float(chain.active_until or active_until) - now)
        if was_active:
            if float(chain.active_until or 0.0) > old_until + 1e-6:
                message = f"🔄 필터 유효시간 갱신 · 남은 {format_duration_ko(remain)}"
            else:
                message = None
        else:
            message = (
                f"✅ 조건 유효시간 필터 성립\n"
                f"▶ 조건이 겹치는 {format_duration_ko(remain)} 동안 {side}{tf_text} 올존 감시"
            )
        return pushes, message, chain.owner_chat_id if message else None

    def handle_generic_trigger(self, event: dict) -> dict:
        chain_id = str(event.get("chain_id") or "").strip()
        watch_id = str(event.get("watch_id") or "").strip()
        try:
            event_stage = int(event.get("chain_stage"))
        except (TypeError, ValueError):
            event_stage = -1
        now = time.time()
        pushes: list[dict] = []
        user_message = None
        target = None
        result_extra: dict = {}

        with self.lock:
            chain = self.chains.get(chain_id)
            if chain is None or not chain.enabled or not chain.started:
                return {"ok": True, "delivered": False, "ignored": True}

            # CANCEL_ON은 정상 stage와 별도 음수 stage를 사용합니다. 후보가 살아 있을 때만 유효합니다.
            cancel_index = -event_stage - 1 if event_stage < 0 else -1
            expected_cancel_id = chain.invalidation_watch_ids.get(str(cancel_index)) if cancel_index >= 0 else None
            if (
                cancel_index >= 0
                and cancel_index < len(chain.invalidation_triggers)
                and expected_cancel_id
                and expected_cancel_id == watch_id
            ):
                if not self._candidate_active_locked(chain):
                    return {"ok": True, "delivered": False, "ignored": True, "reason": "no_active_candidate"}
                fired_cancel = chain.invalidation_triggers[cancel_index]
                pushes, user_message, target = self._reset_candidate_on_invalidation_locked(
                    chain, fired_cancel
                )
                self.save_state_locked()
                self.mark_dirty_callback()
                result_extra = {
                    "invalidated": True, "cancel_index": cancel_index,
                    "cancel_watch_type": fired_cancel.watch_type,
                }
            elif chain.order_mode == "FILTER":
                if event_stage < 0 or event_stage >= len(chain.triggers):
                    return {"ok": True, "delivered": False, "stale": True}
                expected_watch_id = chain.current_watch_ids.get(str(event_stage))
                if not expected_watch_id or expected_watch_id != watch_id:
                    return {"ok": True, "delivered": False, "stale": True}
                trig = chain.triggers[event_stage]
                if trig.watch_type == "OZ_ALERT":
                    return {"ok": True, "delivered": False, "stale": True}

                event_direction = str(event.get("direction") or "").upper()
                if event_direction not in {"LONG", "SHORT"}:
                    event_direction = ""
                if trig.direction and event_direction and trig.direction != event_direction:
                    return {"ok": True, "delivered": False, "ignored": True, "reason": "direction_mismatch"}
                if chain.direction and event_direction and chain.direction != event_direction:
                    return {"ok": True, "delivered": False, "ignored": True, "reason": "final_direction_mismatch"}

                correlation_key = chain.direction or trig.direction or event_direction or "AUTO"
                event_ts = self._event_ts(event, now)
                valid_sec = float(trig.valid_sec or 0.0)
                if valid_sec <= 0 or event_ts + valid_sec <= now:
                    return {"ok": True, "delivered": False, "expired": True, "reason": "filter_event_expired"}
                token = (
                    event.get("event_id") or event.get("zone_id") or event.get("trigger_id")
                    or f"{watch_id}:{event_ts:.6f}"
                )
                UnorderedConditionLatch.register(
                    chain.unordered_latch,
                    correlation_key=correlation_key,
                    condition_key=self._unordered_condition_key(event_stage),
                    event_ts=event_ts, token=token, required_count=1, window_sec=0.0, now=now,
                    meta={
                        "stage": event_stage, "watch_type": trig.watch_type, "tf": trig.tf,
                        "direction": event_direction or trig.direction or chain.direction,
                        "valid_sec": valid_sec, "expires_at": event_ts + valid_sec,
                    },
                )
                candidate_keys = [correlation_key]
                if correlation_key == "AUTO" and chain.direction is None:
                    raw_groups = chain.unordered_latch.get("groups") if isinstance(chain.unordered_latch, dict) else {}
                    if isinstance(raw_groups, dict):
                        for candidate in ("LONG", "SHORT"):
                            if candidate in raw_groups:
                                candidate_keys.append(candidate)
                matched_key = correlation_key
                match = {"matched": False, "selected": [], "active_until": None}
                for candidate in candidate_keys:
                    current = self._filter_match_locked(chain, candidate, now)
                    if current.get("matched"):
                        matched_key, match = candidate, current
                        break
                result_extra = {
                    "order_mode": "FILTER", "matched": bool(match.get("matched")),
                    "active_until": match.get("active_until"),
                }
                if match.get("matched"):
                    pushes, user_message, target = self._complete_filter_locked(
                        chain, event, now, matched_key, match
                    )
                else:
                    user_message = (
                        f"✅ 필터 조건 저장 · {trig.label()} · "
                        f"{format_duration_ko(valid_sec)} 유효"
                    )
                    target = chain.owner_chat_id
                if chain.chain_id in self.chains:
                    pushes.extend(self.invalidation_payloads_locked(chain))
                self.save_state_locked()
                self.mark_dirty_callback()
            elif chain.order_mode == "UNORDERED":
                if chain.active_child_id or event_stage < 0 or event_stage >= len(chain.triggers):
                    return {"ok": True, "delivered": False, "stale": True}
                expected_watch_id = chain.current_watch_ids.get(str(event_stage))
                if not expected_watch_id or expected_watch_id != watch_id:
                    return {"ok": True, "delivered": False, "stale": True}
                trig = chain.triggers[event_stage]
                if trig.watch_type == "OZ_ALERT":
                    return {"ok": True, "delivered": False, "stale": True}

                event_direction = str(event.get("direction") or "").upper()
                if event_direction not in {"LONG", "SHORT"}:
                    event_direction = ""
                if trig.direction and event_direction and trig.direction != event_direction:
                    return {"ok": True, "delivered": False, "ignored": True, "reason": "direction_mismatch"}
                if chain.direction and event_direction and chain.direction != event_direction:
                    return {"ok": True, "delivered": False, "ignored": True, "reason": "final_direction_mismatch"}

                correlation_key = chain.direction or trig.direction or event_direction or "AUTO"
                event_ts = self._event_ts(event, now)
                token = (
                    event.get("event_id") or event.get("zone_id") or event.get("trigger_id")
                    or f"{watch_id}:{event_ts:.6f}"
                )
                latch_result = UnorderedConditionLatch.register(
                    chain.unordered_latch,
                    correlation_key=correlation_key,
                    condition_key=self._unordered_condition_key(event_stage),
                    event_ts=event_ts,
                    token=token,
                    required_count=int(chain.required_count or len(chain.triggers)),
                    window_sec=float(chain.unordered_window_sec or 0.0),
                    now=now,
                    meta={
                        "stage": event_stage,
                        "watch_type": trig.watch_type,
                        "tf": trig.tf,
                        "direction": event_direction or trig.direction or chain.direction,
                    },
                )
                result_extra = {
                    "order_mode": "UNORDERED",
                    "count": latch_result.get("count"),
                    "required_count": latch_result.get("required_count"),
                    "matched": bool(latch_result.get("matched")),
                }
                if latch_result.get("matched"):
                    pushes, user_message, target = self._complete_unordered_locked(
                        chain, event, now, latch_result
                    )
                elif not latch_result.get("replaced"):
                    user_message = (
                        f"✅ 순서무관 조건 저장 · {trig.label()}\n"
                        f"현재 {latch_result.get('count', 0)}/{latch_result.get('required_count', 0)}"
                    )
                    target = chain.owner_chat_id
                if chain.chain_id in self.chains:
                    pushes.extend(self.invalidation_payloads_locked(chain))
                self.save_state_locked()
                self.mark_dirty_callback()
            elif event_stage >= 0:
                if chain.stage != event_stage or chain.current_watch_id != watch_id:
                    return {"ok": True, "delivered": False, "stale": True}
                completed = self._event_ts(event, float("nan"))
                if not math.isfinite(completed):
                    return {"ok":False,"delivered":False,"error":"missing_completion_time"}
                if chain.stage_deadline is not None and completed > chain.stage_deadline:
                    return {"ok": True, "delivered": False, "expired": True}
                if chain.stage >= len(chain.triggers) or chain.triggers[chain.stage].watch_type == "OZ_ALERT":
                    return {"ok": True, "delivered": False, "stale": True}
                next_payload, user_message, target = self._advance_locked(chain, event, completed)
                if next_payload:
                    pushes.append(next_payload)
                if chain.chain_id in self.chains:
                    pushes.extend(self.invalidation_payloads_locked(chain))
                else:
                    pushes.extend(self._cancel_invalidation_payloads_locked(chain))
                self.save_state_locked()
                self.mark_dirty_callback()

        for payload in pushes:
            self.push_callback(payload)
        if user_message and target:
            self.notify_callback(user_message, target, watch_id=chain_id, signal_context={})
        self.retry_notifications()
        return {"ok": True, "delivered": False, "chain_id": chain_id, **result_extra}

    def handle_oz_stage_event(self, event: dict) -> dict:
        """OZ FINAL_ALERT를 사용자 알림으로 끝내지 않고 다음 Chain 단계로 승격합니다."""
        ids = [str(x) for x in (event.get("watch_ids") or []) if str(x)]
        single = str(event.get("watch_id") or "").strip()
        if single and single not in ids:
            ids.append(single)
        if not ids:
            return {"handled": False, "internal_ids": [], "all_internal": False}

        now = time.time()
        pushes: list[dict] = []
        notices: list[tuple[str, str, str]] = []
        internal_ids: list[str] = []
        changed = False
        with self.lock:
            for chain in list(self.chains.values()):
                if not chain.enabled or not chain.started or not chain.current_watch_id:
                    continue
                if chain.current_watch_id not in ids or chain.stage >= len(chain.triggers):
                    continue
                trig = chain.triggers[chain.stage]
                if trig.watch_type != "OZ_ALERT":
                    continue
                completed = self._event_ts(event, float('nan'))
                if not math.isfinite(completed):
                    internal_ids.append(chain.current_watch_id)
                    continue
                if chain.stage_deadline is not None and completed > chain.stage_deadline:
                    internal_ids.append(chain.current_watch_id)
                    continue
                internal_ids.append(chain.current_watch_id)
                payload, message, target = self._advance_locked(chain, event, completed)
                if payload:
                    pushes.append(payload)
                if chain.chain_id in self.chains:
                    pushes.extend(self.invalidation_payloads_locked(chain))
                else:
                    pushes.extend(self._cancel_invalidation_payloads_locked(chain))
                if message and target:
                    notices.append((target, message, chain.chain_id))
                changed = True
            if changed:
                self.save_state_locked()
                self.mark_dirty_callback()

        for payload in pushes:
            self.push_callback(payload)
        for target, message, chain_id in notices:
            self.notify_callback(message, target, watch_id=chain_id, signal_context={})
        self.retry_notifications()
        return {
            "handled": bool(internal_ids),
            "internal_ids": internal_ids,
            "all_internal": bool(internal_ids) and set(ids).issubset(set(internal_ids)),
        }

    @staticmethod
    def completion_deadline(chain):
        limits = [value for value in (chain.stage_deadline, chain.active_until) if value is not None]
        return min(limits) if limits else None

    def filter_deadline_event(self, event):
        """Exclude timed watches by producer completion time, never receive time."""
        ids = list(event.get('watch_ids') or [])
        if event.get('watch_id') and event['watch_id'] not in ids: ids.append(event['watch_id'])
        rejected = set()
        completed = self._event_ts(event, float('nan'))
        with self.lock:
            for chain in self.chains.values():
                wid = chain.active_child_id or chain.current_watch_id
                deadline = self.completion_deadline(chain)
                if wid in ids and deadline is not None and (not math.isfinite(completed) or completed > deadline):
                    rejected.add(wid)
        if not rejected: return event, False
        remaining = [wid for wid in ids if wid not in rejected]
        copy = dict(event, watch_ids=remaining)
        if copy.get('watch_id') in rejected: copy.pop('watch_id')
        return copy, not remaining

    def complete_final_oz(self, event: dict, delivered: bool) -> None:
        """1회성 Chain 최종 OZ가 전달되면 orchestration 상태도 종료합니다."""
        if not delivered:
            return
        ids = {str(x) for x in (event.get("watch_ids") or []) if str(x)}
        single = str(event.get("watch_id") or "").strip()
        if single:
            ids.add(single)
        if not ids:
            return
        changed = False
        cancel_payloads: list[dict] = []
        with self.lock:
            for chain_id, chain in list(self.chains.items()):
                if chain.active_child_id not in ids:
                    continue
                if chain.final_watch_persistent:
                    continue
                cancel_payloads.extend(self._cancel_invalidation_payloads_locked(chain))
                self.chains.pop(chain_id, None)
                changed = True
            if changed:
                self.save_state_locked()
                self.mark_dirty_callback()
        for payload in cancel_payloads:
            self.push_callback(payload)

    def maintenance(self) -> None:
        self.retry_notifications()
        now = time.time()
        cancel_payloads: list[dict] = []
        notices: list[tuple[str, str, str]] = []
        start_payloads: list[dict] = []
        refresh_payloads: list[dict] = []
        changed = False

        with self.lock:
            for chain_id, chain in list(self.chains.items()):
                if not chain.started:
                    if chain.start_at is not None and now >= float(chain.start_at):
                        payloads = self._activate_locked(chain, now)
                        if payloads:
                            start_payloads.extend(payloads)
                        notices.append((chain.owner_chat_id, f"▶ 예약 Watch 시작 · {chain.summary()}", chain.chain_id))
                        changed = True
                    continue

                deadline = self.completion_deadline(chain)
                if chain.order_mode != 'FILTER' and deadline is not None and now > deadline:
                    if not chain.deadline_elapsed:
                        chain.deadline_elapsed = True
                        changed = True
                    # Retain identity/state to adjudicate late completion timestamps.
                    # No finite lateness bound is part of this contract.
                    continue

                if chain.order_mode == 'FILTER' and chain.active_until is not None and now >= chain.active_until:
                    if chain.active_child_id:
                        cancel_payloads.append({
                            "action": "CANCEL_MANUAL", "watch_id": chain.active_child_id,
                            "request_chat_id": chain.owner_chat_id,
                        })
                    if chain.order_mode == "FILTER" and chain.repeat_filter:
                        chain.active_child_id = None
                        chain.active_until = None
                        chain.active_direction = None
                        chain.stage = 0
                        self._prune_filter_hits_locked(chain, now)
                        notices.append((chain.owner_chat_id, f"⌛ 필터 겹침 종료 · {chain.symbol} 다음 조건을 계속 대기합니다.", chain.chain_id))
                        changed = True
                        continue
                    if chain.order_mode == "FILTER":
                        cancel_payloads.extend(self._cancel_filter_trigger_payloads_locked(chain))
                    cancel_payloads.extend(self._cancel_invalidation_payloads_locked(chain))
                    notices.append((chain.owner_chat_id, f"⌛ 시간연쇄 종료 · {chain.symbol} 기간제 올존 감시가 끝났습니다.", chain.chain_id))
                    self.chains.pop(chain_id, None)
                    changed = True
                    continue
                if chain.order_mode == "FILTER" and not chain.active_child_id:
                    if self._prune_filter_hits_locked(chain, now):
                        changed = True
                        if not self._unordered_has_hits(chain):
                            cancel_payloads.extend(self._cancel_invalidation_payloads_locked(chain))
                    # FILTER는 개별 hit가 만료되어도 나머지 조건 Watch를 계속 유지합니다.
                elif chain.order_mode == "UNORDERED" and not chain.active_child_id:
                    if chain.unordered_window_sec is not None:
                        if UnorderedConditionLatch.prune(
                            chain.unordered_latch, float(chain.unordered_window_sec), now
                        ):
                            changed = True
                            if not self._unordered_has_hits(chain):
                                cancel_payloads.extend(self._cancel_invalidation_payloads_locked(chain))
                    # UNORDERED는 개별 hit expiry 후에도 남은 조건을 계속 기다립니다.
            refresh_sec = float(self.config.get("CHAIN_REFRESH_SEC", DEFAULT_CHAIN_REFRESH_SEC))
            mono = time.monotonic()
            if mono - self._last_refresh >= max(5.0, refresh_sec):
                for chain in self.chains.values():
                    if not chain.started:
                        continue
                    if chain.active_child_id:
                        payload = self.final_payload_locked(chain)
                        if payload:
                            refresh_payloads.append(payload)
                    else:
                        refresh_payloads.extend(self.trigger_payloads_locked(chain))
                    refresh_payloads.extend(self.invalidation_payloads_locked(chain))
                self._last_refresh = mono
            if changed:
                self.save_state_locked()
                self.mark_dirty_callback()

        for payload in cancel_payloads + start_payloads + refresh_payloads:
            self.push_callback(payload)
        for target, message, chain_id in notices:
            self.notify_callback(message, target, watch_id=chain_id, signal_context={})
