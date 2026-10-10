# -*- coding: utf-8 -*-
"""
MOSES COMPOSER
=============
Composer 2.0: TREND/FVG/SWEEP/WONBI/PERCENTILE 조건 조합, OZ 라우팅, Telegram 명령/알림 게이트웨이를 담당합니다.
MT5 Named Pipe 수신과 전략용 ZMQ 데이터 서버는 독립 Python `THE STAFF OF MOSES`로 분리되었습니다.
문장 해석·명령 분기는 `kim_secretary`, 개인 시간연쇄 런타임은 `watch_orchestrator`가 담당합니다.
전략 알림 인터페이스(tcp://127.0.0.1:5556)는 기존과 동일합니다.
"""

from __future__ import annotations
import domain_memory
from contextlib import contextmanager
from copy import deepcopy

import json
import re
from functools import lru_cache
from domain_clock import datetime as dt
import logging
import os
import sys
import shutil
from pathlib import Path
import threading
from domain_clock import time
from typing import Dict, Iterable, Optional
from zoneinfo import ZoneInfo
from sweep_selectors import normalize_level_selectors
from watch_ma import parse_ma_expression, parse_ma_name
from durable_protocol import Records, identity, receipt_key, fact_scope, source_health, atomic_json, read_json
import oz_profiles
from oz_profile_loader import load_saved_oz

import pandas as pd

from command_interpreter import (
    resolve_command_language_path, DEFAULT_COMMAND_GOLD_SYMBOL, DEFAULT_COMMAND_LANGUAGE,
    DEFAULT_COMMAND_TF, MT5_TIMEFRAMES, OZ_BASE_TFS, OZ_TF_MAP, WATCH_TF_MAP,
    CommandInterpreter, normalize_tf, tf_seconds,
)
from command_models import (
    SecretaryCommand,
    COMBINATIONS,
    _oz_trigger_mode,
    _canonical_trigger_mode,
    _oz_profile_label,
    DESTINATIONS,
    CONDITION_KINDS,
    WATCH_EVALUATION_MODES,
    WATCH_LEGACY_CONTRACTS,
    WATCH_DEFAULT_EVALUATION,
    normalize_watch_contract,
    legacy_chain_watch_type,
    trigger_watch_contract,
    is_filter_watch_trigger,
    make_chain_trigger,
    canonical_watch_payload,
    TREND_METRIC_FIELDS,
    TREND_METRIC_OPERATORS,
    DEFAULT_COMPOSER_POLL_SEC,
    DEFAULT_MA_STATE_STALE_SEC,
    DEFAULT_TREND_METRIC_STALE_SEC,
    DEFAULT_ENGINE_REFRESH_SEC,
    DEFAULT_SWEEP_LEVELS,
    DEFAULT_SWEEP_ATR_PERIOD,
    DEFAULT_SWEEP_ATR_MULT,
    TREND_SUPPORTED_TFS,
    CONDITION_DATA_TFS,
    WONBI_SOURCE,
    split_csv,
    parse_bool,
    _event_time_token,
    ConditionSpec,
    condition_from_descriptor,
    FVGCreatedWatchSpec,
    ConfigTimedChainSpec,
    StrategySpec,
    parse_condition_token
)
from kim_secretary import KimSecretary

from watch_orchestrator import (
    CHAIN_FINAL_ACTIONS, CHAIN_TRIGGER_TYPES, DEFAULT_CHAIN_REFRESH_SEC,
    TRIGGER_MODES, VALIDATION_MODES, ChainTriggerSpec, TimedChainSpec,
    UnorderedConditionLatch, WatchOrchestrator, format_duration_ko, stable_id,
)


# Only pure identity strings are cached. Complete primitive keys are derived
# from the CURRENT configuration at every call; no health, epoch, binding or
# usable decision is retained. typed=True preserves numeric type distinctions.
@lru_cache(maxsize=4096, typed=True)
def _cached_sweep_watch_id(symbol, tf, levels, atr_period, atr_mult, london, newyork):
    return stable_id("CMP:SWEEP", symbol, tf, levels,
                     atr_period, atr_mult, london, newyork)


@lru_cache(maxsize=4096, typed=True)
def _cached_condition_scope(family, symbol, tf, watch_id):
    return fact_scope({'strategy': family, 'symbol': symbol,
                       'source_tf': tf, 'watch_id': watch_id})


# ---------------------------------------------------------------------
# Logging / config
# ---------------------------------------------------------------------
LOG_DIR = Path(__file__).resolve().parent / "logs"
def _atomic_write_json(path: Path, payload) -> None:
    atomic_json(path, payload, default=None, allow_nan=True, indent=2)


def load_config(file_path: str = "config.txt") -> dict[str, str]:
    """KEY=VALUE 형식 설정파일을 읽습니다. 시스템 기본 파일은 config.txt 입니다."""
    config: dict[str, str] = {}
    script_dir = Path(__file__).resolve().parent

    requested = Path(file_path)
    path = requested if requested.is_absolute() else script_dir / requested
    try:
        with path.open("r", encoding="utf-8-sig") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                config[k.strip()] = v.strip()
        if logging.getLogger().isEnabledFor(logging.INFO):
            logging.info("김비서 설정파일 로드: %s", path.resolve())
    except FileNotFoundError:
        logging.warning("[%s] 김비서 설정파일이 없습니다.", path)
    return config



# ---------------------------------------------------------------------
# Economy worker (isolated from data server)
# ---------------------------------------------------------------------
KST = dt.timezone(dt.timedelta(hours=9))












# ---------------------------------------------------------------------
# KIM COMPOSER 2.0
# ---------------------------------------------------------------------
# 전략 엔진(TREND/FVG/SWEEP)은 사실만 계산합니다.
# 김매니저는 공식 Pipeline과 개인 Watch를 동일한 StrategySpec으로 변환하여
# 조건을 조합하고, setup 성립 시 monitor_OZ에 최종 트리거 감시를 arm합니다.
# ---------------------------------------------------------------------

from dataclasses import dataclass, field, asdict
import hashlib
import math



class TimePolicy:
    """The one trading-time rule (special_time_slot), with this Composer's config."""
    def __init__(self, config: dict[str, str]):
        self.config = dict(config)

    def allows(self, filters) -> bool:
        from special_time_slot import trading_time_allowed
        return trading_time_allowed(filters, self.config.get)




class SpecialPluginAPI:
    """SPECIAL 모듈이 Composer의 private state를 직접 만지지 않도록 하는 공식 확장 경계.

    전략 계산은 SPECIAL 모듈이 소유하고, 이 객체는 기존 OZ watch lifecycle / Staff /
    알림 core에 대한 원자적 접근만 제공합니다.
    """

    def __init__(self, manager: "ComposerManager") -> None:
        self._manager = manager

    def config_get(self, key: str, default=None):
        return self._manager.config.get(str(key), default)

    def time_allowed(self, filters) -> bool:
        # Any trading-time value form (session list, ranges, per-session settings) as given.
        return bool(self._manager._time_policy.allows(filters))

    def staff_request(
        self,
        symbol: str,
        tfs: Iterable[str],
        indicators: Iterable[str],
        *,
        lane: str = "maintenance",
    ):
        # ZMQ REQ 소켓은 스레드간 공유하지 않습니다. FINAL_ALERT hook은 event 전용 client를 사용합니다.
        client = (
            self._manager._special_event_staff
            if str(lane or "").strip().lower() == "event"
            else self._manager.staff
        )
        return client.request(symbol, tfs, indicators)

    def register_watch_handler(self, watch_type: str, handler: object) -> None:
        self._manager.register_special_watch_handler(watch_type, handler)

    def register_oz_event_handler(self, spec_id: str, handler: object) -> None:
        self._manager.register_special_oz_event_handler(spec_id, handler)

    def register_oz_handler(self, spec_id: str, handler: object) -> None:
        """Public OZ connection; existing register_oz_event_handler stays supported."""
        self.register_oz_event_handler(spec_id, handler)

    def register_subscription_provider(self, owner: str, provider) -> None:
        """Register/replace an owner's callable returning family -> watch_id -> payload.

        The canonical subscription reconciliation still owns validation, commands
        and cancellation. Providers declare dependencies; they do not run engines.
        """
        key = str(owner or "").strip()
        if not key or not callable(provider):
            raise ValueError("subscription provider requires an owner and callable")
        with self._manager._lock:
            self._manager._special_subscription_providers[key] = provider
            self._manager._subscription_dirty = True

    def register_fact_observer(self, owner: str, observer) -> None:
        """Observe owned copies of accepted, non-stale Facts after canonical processing.

        Observer return values never replace the canonical ACK. Re-registering
        the same owner replaces its callback rather than stacking method wrappers.
        """
        key = str(owner or "").strip()
        if not key or not callable(observer):
            raise ValueError("fact observer requires an owner and callable")
        with self._manager._lock:
            self._manager._special_fact_observers[key] = observer

    def register_strategy_state_provider(self, owner: str, provider) -> None:
        """Engine-owned checkpoints/deadlines; a strategy never opens files."""
        key = str(owner or '').strip()
        if not key or not all(callable(getattr(provider, method, None)) for method in ('checkpoint','restore','deadlines')):
            raise ValueError('strategy state provider requires checkpoint/restore/deadlines')
        self._manager._strategy_state_providers[key] = provider
        kernel = getattr(getattr(self._manager, 'event_services', None), 'kernel', None)
        if kernel is not None:
            saved = json.loads(kernel.initial_files.get('strategy_recipe_state.json', '{}'))
            if key in saved: provider.restore(saved[key])

    def shared_resource(self, name: str, factory):
        """Manager-local shared Fact/cache resource, rebuilt with the engine lifecycle."""
        key = str(name or "").strip()
        if not key or not callable(factory):
            raise ValueError("shared resource requires a name and factory")
        with self._manager._lock:
            resources = self._manager._special_shared_resources
            if key not in resources:
                resources[key] = factory()
            return resources[key]

    def market_context(self):
        """Current immutable Board, this kernel's symbol and event time in seconds."""
        kernel = getattr(getattr(self._manager, "event_services", None), "kernel", None)
        if kernel is None:
            raise RuntimeError("이 전략은 현재 Part1 이벤트 실행 계층이 필요합니다.")
        return kernel.board, kernel.symbol, kernel.timestamp / 1000

    def request_watch(self, payload: dict) -> None:
        """Send a dependency through the existing canonical command boundary."""
        self._manager._push(dict(payload))

    def notify(self, text: str, *, event: Optional[dict] = None,
               event_id: Optional[str] = None, chat_id: Optional[str] = None) -> bool:
        """Existing output/dedup policy with scoped strategy event metadata."""
        context = self._manager._delivery_context
        previous = getattr(context, "oz_event", None)
        context.oz_event = dict(event) if event is not None else previous
        try:
            return self._manager.send_telegram(text, chat_id=chat_id, event_id=event_id)
        finally:
            context.oz_event = previous

    @contextmanager
    def capture_oz_handlers(self):
        """Capture newly registered OZ handlers in this registration scope only.

        Used to decorate an inherited strategy's handlers without inspecting the
        manager registry or replacing any Part1 method. Existing owners are excluded.
        """
        manager = self._manager
        with manager._lock:
            scope = (frozenset(manager._special_oz_event_handlers), {})
            manager._special_oz_registration_scopes.append(scope)
            try:
                yield scope[1]
            finally:
                manager._special_oz_registration_scopes.pop()

    def deliver_oz_event_core(self, event: dict) -> dict:
        # SPECIAL hook 재진입 없이 김매니저의 기존 OZ lifecycle만 실행합니다.
        context=self._manager._delivery_context
        previous=getattr(context,'oz_event',None)
        context.oz_event=dict(event)
        try:return self._manager._handle_oz_event_core(dict(event))
        finally:context.oz_event=previous

    @staticmethod
    def _matches(
        watch_id: str,
        payload: dict,
        *,
        source_spec_id: Optional[str] = None,
        symbol: Optional[str] = None,
        direction: Optional[str] = None,
        watch_id_prefix: Optional[str] = None,
    ) -> bool:
        if source_spec_id is not None:
            ids: list[str] = []
            raw_ids = payload.get("source_spec_ids")
            if isinstance(raw_ids, (list, tuple, set)):
                ids.extend(str(x).strip() for x in raw_ids if str(x).strip())
            single = str(payload.get("source_spec_id") or "").strip()
            if single:
                ids.append(single)
            if str(source_spec_id) not in set(ids):
                return False
        if symbol is not None and str(payload.get("symbol") or "") != str(symbol):
            return False
        if direction is not None and str(payload.get("direction") or "").upper() != str(direction).upper():
            return False
        if watch_id_prefix is not None and not str(watch_id).startswith(str(watch_id_prefix)):
            return False
        return True

    def snapshot_oz_watches(
        self,
        *,
        source_spec_id: Optional[str] = None,
        symbol: Optional[str] = None,
        direction: Optional[str] = None,
        watch_id_prefix: Optional[str] = None,
    ) -> list[tuple[str, dict]]:
        with self._manager._lock:
            return [
                (str(wid), dict(payload))
                for wid, payload in self._manager._active_children.items()
                if isinstance(payload, dict) and self._matches(
                    str(wid), payload,
                    source_spec_id=source_spec_id, symbol=symbol, direction=direction,
                    watch_id_prefix=watch_id_prefix,
                )
            ]

    def ensure_oz_watch(
        self,
        payload: dict,
        *,
        compare_keys: Iterable[str] = (),
        push_if_changed: bool = True,
    ) -> bool:
        item = oz_profiles.normalize_watch_payload(payload)
        wid = str(item.get("watch_id") or "").strip()
        if not wid or str(item.get("action") or "").upper() != "MANUAL_WATCH":
            raise ValueError("SPECIAL OZ watch payload 오류")
        keys = tuple(str(x) for x in compare_keys)
        changed = False
        with self._manager._lock:
            current = self._manager._active_children.get(wid)
            changed = (
                not isinstance(current, dict)
                or not keys
                or any(current.get(key) != item.get(key) for key in keys)
            )
            if changed:
                self._manager._active_children[wid] = dict(item)
                self._manager._save_active_children_state_locked()
        if changed and push_if_changed:
            self._manager._push(dict(item))
        return changed

    def cancel_oz_watches(self, watch_ids: Iterable[str]) -> int:
        ids = tuple(dict.fromkeys(str(x).strip() for x in watch_ids if str(x).strip()))
        if not ids:
            return 0
        transaction = getattr(self._manager, '_oz_dispatch_transaction', None)
        deferred = set(ids).intersection(transaction['ids']) if transaction else set()
        if deferred:
            transaction['pending'].update(deferred)
            ids = tuple(wid for wid in ids if wid not in deferred)
        cancels: list[dict] = []
        with self._manager._lock:
            for wid in ids:
                item = self._manager._active_children.pop(wid, None)
                if not isinstance(item, dict):
                    continue
                cancels.append({
                    "action": "CANCEL_MANUAL",
                    "watch_id": wid,
                    "request_chat_id": item.get("request_chat_id"),
                })
            if cancels:
                self._manager._save_active_children_state_locked()
        for cancel in cancels:
            self._manager._push(cancel)
        return len(cancels) + len(deferred)


class ComposerManager:
    """2.0 중앙 Composer + 전략 이벤트 관문."""

    @property
    def official_specs(self):
        return self._official_specs

    @official_specs.setter
    def official_specs(self, values):
        from composer_fact_index import SpecRegistry
        self._official_specs = SpecRegistry(values)

    @property
    def manual_specs(self):
        return self._manual_specs

    @manual_specs.setter
    def manual_specs(self, values):
        from composer_fact_index import SpecRegistry
        self._manual_specs = SpecRegistry(values)

    def __init__(
        self,
        config: dict[str, str],
        stop_event: threading.Event,
        oz_queue: OZCommandQueue,
        wonbi_state: WonbiState,
        notifier: Optional[NotificationService] = None,
        *, event_services=None,
    ):
        if event_services is None or notifier is None:
            raise ValueError("event services and output port are required")
        self.event_services = event_services
        # config.txt는 시스템/공통 설정만 소유합니다.
        # 공식 preset은 Recipe registry에서 공통 연결 API에 등록합니다.
        self.system_config = dict(config)
        self.config = dict(self.system_config)
        # SPECIAL은 공식 메인전략과 전략별 Watch 판정기의 단일 확장 슬롯입니다.
        # 첫 스캔에서 로드된 전략이 없으면 "스페셜 전략이 없습니다" 로그를 1회 남깁니다.
        self._special_selection_active = os.environ.get("OZ_SPECIAL_SELECTION_ACTIVE", "").strip() == "1"
        self._enabled_special_names = {
            item.strip().casefold()
            for item in os.environ.get("OZ_ENABLED_SPECIALS", "").split(",")
            if item.strip()
        }
        # [전략 설정] SPECIAL별 최종 OZ 트리거 슬롯.
        # OZ_SYSTEM CONTROL -> 환경변수 OZ_SPECIAL_TRIGGERS(JSON) -> 공용 oz_profiles 등록 -> 각 SPECIAL 조회.
        # SPECIAL 로드 전에 등록해야 SPECIAL이 import 시점에 자기 최종 트리거를 확정할 수 있습니다.
        profile_api = oz_profiles if event_services is None else event_services.profiles
        try:
            special_trigger_map = dict(event_services.trigger_map) if event_services is not None else oz_profiles.parse_special_trigger_json(
                os.environ.get(oz_profiles.SPECIAL_TRIGGERS_ENV, "")
            )
        except ValueError as exc:
            raise ValueError(f"SPECIAL 트리거 설정 실행 차단: {exc}") from exc
        profile_errors = profile_api.set_special_trigger_overrides(special_trigger_map)
        if profile_errors:
            raise ValueError(f"SPECIAL 트리거 설정 실행 차단: {profile_errors}")
        for special_name, (slot_vm, slot_tm, _slot_text) in sorted(profile_api.special_trigger_overrides().items()):
            if logging.getLogger().isEnabledFor(logging.INFO):
                logging.info("🟢 [SPECIAL] 트리거 슬롯 | %s | %s", special_name, oz_profiles.profile_label(slot_vm, slot_tm))
        self.special_modules: dict[str, object] = {}
        self._special_initial_scan_done = False
        # SPECIAL은 전략별 Watch 판정기를 이 레지스트리에 등록합니다.
        # 김매니저 본체는 condition type을 알 필요 없이 공용 Watch lifecycle만 관리합니다.
        self._special_watch_handlers: dict[str, object] = {}
        self._special_watch_event_handlers: list[object] = []
        # OZ FINAL_ALERT 전용 SPECIAL handler. spec_id로 직접 라우팅하여 전략간 예외 전파를 차단합니다.
        self._special_oz_event_handlers: dict[str, object] = {}
        # Public plugin connections. Empty registries preserve native SPECIAL behavior.
        self._special_subscription_providers: dict[str, object] = {}
        self._special_fact_observers: dict[str, object] = {}
        self._special_shared_resources: dict[str, object] = {}
        self._strategy_state_providers: dict[str, object] = {}
        self._special_oz_registration_scopes: list[tuple] = []
        self.command_aliases_path = (
            resolve_command_language_path(config, Path(__file__).parent)
            if event_services is None else event_services.alias_path
        )
        self.stop_event = stop_event
        self.oz_queue = oz_queue
        self.wonbi_state = wonbi_state
        self.notifier = notifier
        self.token = str(config.get("TELEGRAM_TOKEN", "")).strip()
        self.chat_id = str(config.get("TELEGRAM_CHAT_ID", "")).strip()
        self._receipts = Records(LOG_DIR / 'event_receipts.json', resident=event_services is not None, retention_seconds=3*86400)
        self._fact_revisions = Records(LOG_DIR / 'fact_revisions.json', resident=event_services is not None)
        self._signature_records = Records(LOG_DIR / 'composer_signatures.json', resident=event_services is not None)
        self._source_bindings = {}
        self.source_status = {}
        self._delivery_context = threading.local()
        self._command_context = threading.local()
        # Stored atomically alongside the existing private WATCH state, not a new engine.
        self._watch_message_links: dict[str, dict] = {}
        self._lock = threading.RLock()

        self.official_specs: dict[str, StrategySpec] = {}
        self.official_chain_specs: dict[str, ConfigTimedChainSpec] = {}
        self.manual_specs: dict[str, StrategySpec] = {}
        self._last_signatures: dict[tuple[str, str], str] = {}
        for item in self._signature_records.all().values():
            self._last_signatures[(item['spec_id'], item['direction'])] = item['signature']
        self._active_children: dict[str, dict] = {}
        self._rejected_profile_children: list[dict] = []
        self._rejected_private_specs: list[dict] = []
        self._rejected_fvg_watches: list[dict] = []
        self.command_interpreter = (CommandInterpreter(
            self.system_config,
            self.command_aliases_path,
            allowed_symbols_provider=self._allowed_symbols,
        ) if event_services is None else event_services.interpreter(self))
        # 기존 외부 참조 호환용 읽기 별칭. 실제 소유권은 CommandInterpreter에 있습니다.
        self.command_aliases = self.command_interpreter.language()
        # 공식 SPECIAL TIMED_CHAIN은 개인 timed_chains와 완전히 분리합니다.
        self._config_chain_state: dict[str, dict] = {}
        self._config_chain_active: dict[str, dict] = {}
        self._last_config_chain_refresh = 0.0
        self._last_config_chain_bar_poll = 0.0
        self._config_chain_bar_times_cache: dict[tuple[str, str], tuple[float, ...]] = {}

        # Fact store
        self.trend_facts: dict[tuple[str, str], dict] = {}
        # strategy_FVG가 FVG lifecycle의 단일 사실 공급원입니다.
        # active zone 전체 상태와 현재 touch 상태를 Composer가 직접 유지합니다.
        self.fvg_zones: dict[tuple[str, str, str], dict] = {}
        self.fvg_touches: dict[tuple[str, str, str], dict] = {}
        self.fvg_created_watches: dict[str, FVGCreatedWatchSpec] = {}
        self.sweep_touches: dict[tuple[str, str, str, str], dict] = {}
        self.wonbi_facts: dict[tuple[str, str, str], dict] = {}
        self.percentile_facts: dict[tuple[str, str, str], dict] = {}
        # 현재 MA 배열 상태 Gate. CROSS 이벤트가 아니라 STAFF 최신 진행봉의 현재 배열을 추적합니다.
        self.ma_state_facts: dict[tuple[str, str, str, int, int], dict] = {}
        # 현재 봉 가격과 STAFF MA의 상대 위치. MA_STATE와 분리된 정식 fact입니다.
        self.ma_price_state_facts: dict[tuple[str, str, str, int], dict] = {}
        # STAFF MA의 현재값과 2봉 전 값을 비교한 방향 상태. 이평을 manager/TREND에서 재계산하지 않습니다.
        self.ma_slope_state_facts: dict[tuple[str, str, str, int], dict] = {}
        # strategy_INDICATOR가 구독된 필드만 push하는 지표값 cache.
        # key=(symbol, tf, metric), value={value, bar_time, received_mono}.
        self.trend_metric_facts: dict[tuple[str, str, str], dict] = {}
        self._trend_metric_gate_state: dict[tuple[str, str, str], dict] = {}
        # 사전 기반 복합조건 Watch의 edge/봉마감 상태. 특정 전략명은 저장하지 않습니다.
        self._compound_watch_state: dict[str, dict] = {}
        # 전략별 FILTER 판정 상태는 SPECIAL handler가 소유합니다.
        self._wonbi_touch_seq = 0
        self._percentile_touch_seq = 0
        self._ma_state_seq = 0
        self._ma_price_state_seq = 0
        self._ma_slope_state_seq = 0
        self._trend_metric_gate_seq = 0

        self._state_path = LOG_DIR / "composer_private_watches.json"
        self._chain_state_path = LOG_DIR / "composer_timed_chains.json"
        self._fvg_created_watch_state_path = LOG_DIR / "composer_fvg_created_watches.json"
        # KIM이 만든 현재 OZ 감시 목록을 logs 폴더에 별도 보존합니다.
        # KIM만 재시작되어도 감시 소유권을 잃지 않도록 하기 위한 상태 파일입니다.
        self._active_children_state_path = LOG_DIR / "composer_active_oz_watches.json"
        # 공식 SPECIAL TIMED_CHAIN의 stage 상태를 재시작 뒤에도 이어가기 위한 상태 파일입니다.
        self._config_chain_state_path = LOG_DIR / "composer_config_timed_chain_state.json"
        self._engine_subscriptions: dict[str, dict[str, dict]] = {"TREND": {}, "FVG": {}, "SWEEP": {}}
        self._subscription_dirty = True
        self.watch_orchestrator = WatchOrchestrator(
            lock=self._lock,
            state_path=self._chain_state_path,
            config=self.config,
            push_callback=self._push,
            notify_callback=self.send_telegram,
            mark_dirty_callback=lambda: setattr(self, "_subscription_dirty", True),
            signal_context_callback=self._chain_signal_context,
        )
        # manager_KIM 내부 기존 참조는 같은 dict 객체를 바라보게 유지합니다.
        self.timed_chains = self.watch_orchestrator.chains
        self._last_engine_refresh = 0.0
        self._last_wonbi_poll = 0.0
        self._last_wonbi_sigma: Optional[float] = None
        self._time_policy = TimePolicy(self.config)
        self.staff = event_services.staff
        self._config_chain_event_staff = event_services.staff
        self._special_event_staff = event_services.staff
        self.special_api = SpecialPluginAPI(self)

        if event_services is None or event_services.restore_state:
            self._load_private_state()
            self._load_timed_chain_state()
            self._load_fvg_created_watch_state()
            self._load_active_children_state()
        self._scan_special_strategies()

    def send_telegram(self, text: str, chat_id: Optional[str] = None, event_id: Optional[str] = None,
                      *, watch_id: Optional[str] = None, watch_registration: bool = False,
                      reply_to_message_id: Optional[int] = None,
                      signal_context: Optional[dict] = None) -> bool:
        target = str(chat_id or self.chat_id or "").strip()
        links = self._watch_links_for_ids([watch_id], target) if watch_id else []
        if watch_registration and links:
            link = links[0]
            # Persist text before HTTP so an ACK retry can recover the same confirmation ID.
            with self._lock:
                link["confirmation_text"] = str(text)
                self._save_private_state_locked()
            ids: list[int] = []
            delivered = self.notifier.send(
                text, chat_id=target,
                event_id=identity("WATCH_REGISTER", target, link["watch_id"]),
                sent_message_ids=ids,
                signal_context={},
            )
            if not delivered or not ids:
                logging.error("[WATCH Reply] 등록 확인 message_id를 받지 못했습니다 | %s", watch_id)
                return False
            with self._lock:
                link["confirmation_message_id"] = ids[0]
                self._save_private_state_locked()
            return True
        if links and reply_to_message_id is None:
            return self._send_watch_notification(text, target, links, event_id=event_id,
                                                 signal_context=signal_context)
        parent = event_id or getattr(self._delivery_context, 'event_id', None)
        token = identity(parent, target, text) if parent else None
        kwargs = {"reply_to_message_id": reply_to_message_id} if reply_to_message_id else {}
        return self.notifier.send(text, chat_id=target or None, event_id=token,
                                  signal_context={} if watch_registration else signal_context, **kwargs)

    def _chain_signal_context(self, chain, event: dict) -> dict:
        """Capture market input only after a personal chain's NOTIFY completes."""
        from event_signal_context import market_signal_context
        kernel = getattr(getattr(self, 'event_services', None), 'kernel', None)
        timeframes = {trigger.tf for trigger in chain.triggers}
        timeframes.update(condition.get('tf') for trigger in chain.triggers
                          for condition in trigger.condition_specs if condition.get('tf'))
        timeframes.update(condition.get('price_tf') for trigger in chain.triggers
                          for condition in trigger.condition_specs if condition.get('price_tf'))
        context = market_signal_context(getattr(kernel, 'board', None), chain.symbol,
            event.get('direction') or chain.active_direction or chain.direction,
            event.get('event_time', kernel.timestamp / 1000 if kernel is not None else None), timeframes)
        context['signal_strategy'] = 'WATCH'
        return context

    @staticmethod
    def _telegram_message_id(value: object) -> Optional[int]:
        # bool is an int in Python, but is never a Telegram message identifier.
        if isinstance(value, bool):
            return None
        try:
            number = int(value)
        except (TypeError, ValueError, OverflowError):
            return None
        return number if number > 0 and str(value).strip() == str(number) else None

    def _watch_links_for_ids(self, watch_ids: Iterable[object], owner: str) -> list[dict]:
        ids = {str(x) for x in watch_ids if x}
        if not ids:
            return []
        with self._lock:
            return [link for wid, link in self._watch_message_links.items()
                    if link.get("owner_chat_id") == str(owner)
                    and (wid in ids or ids.intersection(link.get("members", {})))]

    def _remember_watch_command(self, watch_id: str, owner: str, watch_type: str,
                                cancel_action: str) -> Optional[dict]:
        context = getattr(self._command_context, "current", None)
        if not context or context["owner_chat_id"] != str(owner) or not context["message_id"]:
            return None  # Legacy/programmatic callers keep their previous behaviour.
        with self._lock:
            link = self._watch_message_links.get(watch_id)
            if link is None:
                link = {
                    "watch_id": watch_id, "owner_chat_id": str(owner),
                    "command_message_id": context["message_id"],
                    "confirmation_message_id": None,
                    "original_command": context["original_text"],
                    "watch_type": watch_type, "cancel_action": cancel_action,
                    "confirmation_text": "", "members": {}, "status": "active",
                }
                self._watch_message_links[watch_id] = link
                self._save_private_state_locked()
            return link

    def _track_watch_payload(self, item: dict) -> bool:
        actions = {"MANUAL_WATCH": "CANCEL_MANUAL", "GENERIC_WATCH": "CANCEL_GENERIC",
                   "LOCAL_EVENT_WATCH": "CANCEL_LOCAL_EVENT", "FVG_EVENT_WATCH": "CANCEL_FVG_EVENT"}
        action = str(item.get("action") or "").upper()
        wid = str(item.get("watch_id") or "")
        owner = str(item.get("request_chat_id") or "")
        if action not in actions or not wid or not owner:
            return True
        parents = [item.get("chain_id"), item.get("source_chain_id"), item.get("source_spec_id"),
                   item.get("reply_parent_watch_id")]
        parents.extend(item.get("source_spec_ids") or [])
        links = self._watch_links_for_ids([wid, *parents], owner)
        if not links:
            link = self._remember_watch_command(wid, owner, str(item.get("watch_type") or "OZ"), actions[action])
            links = [link] if link else []
        with self._lock:
            if links and all(link.get("status") in {"cancelled", "cancel_requested", "inactive"} for link in links):
                return False  # A queued/stale parent snapshot must not re-arm a cancelled WATCH.
            changed = False
            for link in links:
                cancel_action = actions[action]
                if action == "LOCAL_EVENT_WATCH" and item.get("watch_type") in {"BAR", "WONBI_TOUCH"}:
                    cancel_action = "CANCEL_GENERIC"
                member = {"action": cancel_action, "watch_type": item.get("watch_type")}
                if link["members"].get(wid) != member:
                    link["members"][wid] = member
                    changed = True
            if changed:
                self._save_private_state_locked()
        return True

    def _retry_watch_confirmations(self) -> None:
        # The sendMessage receipt also stores the sent message_id. A restart between
        # HTTP success and binding can therefore recover without a second confirmation.
        with self._lock:
            pending = [dict(link) for link in self._watch_message_links.values()
                       if not link.get("confirmation_message_id") and link.get("confirmation_text")
                       and link.get("status") == "active"]
        for link in pending:
            self.send_telegram(link["confirmation_text"], link["owner_chat_id"],
                               watch_id=link["watch_id"], watch_registration=True)
        with self._lock:
            cancelling = [dict(link) for link in self._watch_message_links.values()
                          if link.get("status") == "cancel_requested"]
            undelivered = [link for link in self._watch_message_links.values()
                           if link.get("cancel_confirmation_pending")]
        for link in cancelling:
            # Reuse the exact idempotent engine command after a queue/ACK interruption.
            self._push({"action": link["cancel_action"], "watch_id": link["watch_id"],
                        "request_chat_id": link["owner_chat_id"], "reply_cancel": True})
        for link in undelivered:
            self._send_watch_cancel_result(link, link["cancel_result_text"])

    def _send_watch_cancel_result(self, link: dict, text: str) -> bool:
        with self._lock:
            # Replayed engine cancellation ACKs must not turn an earlier success into
            # a second, contradictory 'not found' message.
            link.setdefault("cancel_result_text", text)
            link["cancel_confirmation_pending"] = True
            self._save_private_state_locked()
        delivered = self.send_telegram(link["cancel_result_text"], link["owner_chat_id"],
                                       event_id=identity("WATCH_CANCEL", link["owner_chat_id"], link["watch_id"]),
                                       signal_context={})
        if delivered:
            with self._lock:
                link["cancel_confirmation_pending"] = False
                self._save_private_state_locked()
        return bool(delivered)

    def _send_watch_notification(self, text: str, owner: str, links: list[dict],
                                 *, event_id: Optional[str] = None,
                                 signal_context: Optional[dict] = None) -> bool:
        delivered = True
        seen: set[int] = set()
        for link in links:
            # Late engine events after an explicit cancellation must not leak through.
            if link.get("status") in {"cancelled", "cancel_requested", "inactive"}:
                continue
            mid = self._telegram_message_id(link.get("command_message_id"))
            if mid is None or mid in seen:
                continue
            seen.add(mid)
            ok = self.send_telegram(text, owner, event_id=event_id, reply_to_message_id=mid,
                                    signal_context=signal_context)
            delivered = bool(ok) and delivered
        return delivered

    def _cancel_watch_reply(self, owner: str, reply_to_message_id: Optional[int]) -> None:
        guidance = "❓ 취소할 감시 등록 메시지에 답장으로 '취소'라고 보내주세요."
        with self._lock:
            matches = [link for link in self._watch_message_links.values()
                       if reply_to_message_id is not None
                       and link.get("owner_chat_id") == owner
                       and link.get("confirmation_message_id") == reply_to_message_id]
            if len(matches) != 1:
                self.send_telegram(guidance, owner)
                return
            link = matches[0]
            if link.get("status") in {"cancelled", "inactive"}:
                self.send_telegram("ℹ️ 이미 취소되었거나 종료된 감시입니다.", owner)
                return
            if link.get("status") == "cancel_requested":
                self.send_telegram("ℹ️ 해당 감시의 취소 처리가 진행 중입니다.", owner)
                return
            wid, action = link["watch_id"], link["cancel_action"]
            if action in {"CANCEL_MANUAL", "CANCEL_GENERIC"}:
                link["status"] = "cancel_requested"
                self._save_private_state_locked()
                try:
                    # No TF/profile filters: the existing engine removes this whole WATCH.
                    self._push({"action": action, "watch_id": wid,
                                "request_chat_id": owner, "reply_cancel": True})
                except Exception:
                    link["status"] = "active"
                    self._save_private_state_locked()
                    logging.exception("[WATCH Reply] 취소 명령 전달 실패 | %s", wid)
                    self.send_telegram("❌ 감시 취소 명령을 전달하지 못했습니다. 다시 시도해주세요.", owner)
                return
            persisted_ids = self._persisted_watch_ids(LOG_DIR / "oz_manual_watch_state.json", "watches", False)
            persisted_ids.update(self._persisted_watch_ids(LOG_DIR / "oz_generic_watch_state.json", "watches", False))
            alive = (wid in self.manual_specs or wid in self.fvg_created_watches or wid in self.timed_chains
                     or any(child_id in self._active_children or child_id in persisted_ids
                            for child_id in link["members"]))
            if not alive:
                link["status"] = "inactive"
                self._save_private_state_locked()
                self.send_telegram("ℹ️ 이미 취소되었거나 종료된 감시입니다.", owner)
                return
            # Manager-owned roots live in KIM, so remove only that exact parent record.
            # Child WATCH cancellation itself must go through the existing CANCEL_* paths.
            if action == "PRIVATE":
                self.manual_specs.pop(wid, None)
            elif action == "FVG_NEW":
                self.fvg_created_watches.pop(wid, None)
            elif action == "TIMED_CHAIN":
                self.timed_chains.pop(wid, None)
            else:
                logging.error("[WATCH Reply] 알 수 없는 manager-owned 취소 유형 | %s | %s", wid, action)
                self.send_telegram(guidance, owner)
                return
            for direction in ("LONG", "SHORT"):
                self._last_signatures.pop((wid, direction), None)

            active_children_to_cancel: list[str] = []
            for child_id, member in link["members"].items():
                shared = [other for other in self._watch_links_for_ids([child_id], owner)
                          if other is not link and other.get("status") == "active"]
                active = self._active_children.get(child_id)
                if active and wid in (active.get("source_spec_ids") or []):
                    active["source_spec_ids"] = [x for x in active["source_spec_ids"] if x != wid]
                    if active.get("source_spec_id") == wid:
                        active["source_spec_id"] = next(iter(active["source_spec_ids"]), None)
                if shared or (active and active.get("source_spec_ids")):
                    continue

                # KIM-owned active OZ children already have an established watch_id cancel API.
                # Use it instead of deleting _active_children directly.
                if member["action"] == "CANCEL_MANUAL" and child_id in self._active_children:
                    active_children_to_cancel.append(child_id)
                    continue

                # Generic/local-event/OZ-owned children are cancelled through their existing engine action.
                cancel_payload = {"action": member["action"], "watch_id": child_id}
                if member["action"] not in {"CANCEL_MANUAL", "CANCEL_GENERIC"}:
                    cancel_payload["watch_type"] = member.get("watch_type")
                self._push(cancel_payload)

            if active_children_to_cancel:
                self.special_api.cancel_oz_watches(active_children_to_cancel)
            link["status"] = "cancelled"
            self._save_private_state_locked()
            self._save_timed_chain_state_locked()
            self._save_fvg_created_watch_state_locked()
            self._save_active_children_state_locked()
            self._subscription_dirty = True
        self._send_watch_cancel_result(link, self._watch_cancel_text(link))

    @staticmethod
    def _watch_cancel_text(link: dict) -> str:
        text = str(link.get("confirmation_text") or link.get("original_command") or "감시").strip()
        if text.startswith("✅"):
            text = text[1:].strip()
        return f"🗑 {text} 취소"

    # -------------------------------
    # SPECIAL official strategy registry / loader
    # -------------------------------
    def register_special_watch_handler(self, watch_type: str, handler: object) -> None:
        """SPECIAL 전략의 Watch 판정기를 canonical condition type으로 등록합니다.

        handler는 필요에 따라 poll(targets), handle_event(event),
        engine_requirements(chain, trigger, evaluation_mode),
        needs_live_wonbi(chain, trigger, evaluation_mode)를 구현할 수 있습니다.
        """
        condition_type, _ = normalize_watch_contract(watch_type)
        if not condition_type or handler is None:
            raise ValueError("SPECIAL Watch handler 등록값이 비어 있습니다")
        previous = self._special_watch_handlers.get(condition_type)
        if previous is not None and previous in self._special_watch_event_handlers:
            self._special_watch_event_handlers.remove(previous)
        self._special_watch_handlers[condition_type] = handler
        event_handler = getattr(handler, "handle_event", None)
        if callable(event_handler) and handler not in self._special_watch_event_handlers:
            self._special_watch_event_handlers.append(handler)
        self._subscription_dirty = True

    @staticmethod
    def _special_strategy_from_definition(definition: StrategySpec | dict) -> StrategySpec:
        if isinstance(definition, StrategySpec):
            spec = definition
        elif isinstance(definition, dict):
            item = dict(definition)
            raw_conditions = item.get("conditions") or ()
            conditions: list[ConditionSpec] = []
            default_tf = normalize_tf(item.pop("default_tf", ""))
            for raw in raw_conditions:
                if isinstance(raw, ConditionSpec):
                    conditions.append(raw)
                elif isinstance(raw, dict):
                    conditions.append(ConditionSpec(**raw))
                else:
                    conditions.append(parse_condition_token(str(raw), default_tf))
            item["conditions"] = tuple(conditions)
            item["oz_tfs"] = tuple(item.get("oz_tfs") or ())
            item["time_filters"] = tuple(item.get("time_filters") or ())
            item["sweep_levels"] = tuple(item.get("sweep_levels") or DEFAULT_SWEEP_LEVELS)
            item["destination"] = "OFFICIAL"
            item["owner_chat_id"] = None
            item["persistent"] = True
            item["source"] = "SPECIAL"
            spec = StrategySpec(**item)
        else:
            raise TypeError("SPECIAL 전략 정의는 StrategySpec 또는 dict여야 합니다")
        spec.destination = "OFFICIAL"
        spec.owner_chat_id = None
        spec.persistent = True
        spec.source = "SPECIAL"
        spec.validate()
        return spec

    @staticmethod
    def _special_timed_chain_from_definition(
        definition: ConfigTimedChainSpec | dict,
    ) -> ConfigTimedChainSpec:
        if isinstance(definition, ConfigTimedChainSpec):
            spec = definition
        elif isinstance(definition, dict):
            item = dict(definition)
            for key in (
                "fvg_tfs", "oz_tfs", "final_fvg_touch_tfs", "time_filters",
                "final_time_filters", "cancel_on_opposite_fvg_tfs",
            ):
                if key in item:
                    item[key] = tuple(item.get(key) or ())
            spec = ConfigTimedChainSpec(**item)
        else:
            raise TypeError("SPECIAL 시간연쇄 정의는 ConfigTimedChainSpec 또는 dict여야 합니다")
        spec.validate()
        return spec

    def register_special_bundle(
        self,
        *,
        strategies: Iterable[StrategySpec | dict] = (),
        timed_chains: Iterable[ConfigTimedChainSpec | dict] = (),
    ) -> None:
        """SPECIAL 모듈의 공식 전략 정의를 검증 후 한 번에 등록합니다."""
        new_specs = [self._special_strategy_from_definition(x) for x in strategies]
        new_chains = [self._special_timed_chain_from_definition(x) for x in timed_chains]

        new_ids = [x.spec_id for x in (*new_specs, *new_chains)]
        if len(new_ids) != len(set(new_ids)):
            raise ValueError("SPECIAL bundle 안에 중복 spec_id가 있습니다")

        arm_payloads: list[dict] = []
        with self._lock:
            existing = set(self.official_specs) | set(self.official_chain_specs)
            duplicated = [x for x in new_ids if x in existing]
            if duplicated:
                raise ValueError(f"SPECIAL spec_id 중복: {','.join(duplicated)}")

            for spec in new_specs:
                self.official_specs[spec.spec_id] = spec
            for spec in new_chains:
                self.official_chain_specs[spec.spec_id] = spec

            if new_chains:
                restored = self._load_config_chain_state_for_specs_locked(
                    {x.spec_id: x for x in new_chains}
                )
                self._config_chain_state.update(restored)
                self._save_config_chain_state_locked()
                for spec in new_chains:
                    arm_payloads.extend(self._config_chain_trigger_payloads_locked(spec))

            self._subscription_dirty = True

        for payload in arm_payloads:
            self._push(payload)

        if new_specs or new_chains:
            if logging.getLogger().isEnabledFor(logging.INFO):
                logging.info(
                    "🟢 [SPECIAL] 공식 전략 등록 | StrategySpec=%d TIMED_CHAIN=%d | %s",
                    len(new_specs), len(new_chains), ", ".join(new_ids),
                )

    def _dispatch_special_watch_event(self, event: dict) -> Optional[dict]:
        for handler in tuple(self._special_watch_event_handlers):
            callback = getattr(handler, "handle_event", None)
            if not callable(callback):
                continue
            result = callback(event)
            if result is not None:
                return result
        return None

    def register_special_oz_event_handler(self, spec_id: str, handler: object) -> None:
        """OZ FINAL_ALERT를 특정 SPECIAL spec_id로 안전하게 라우팅합니다.

        handler는 ``handle_oz_event(event) -> Optional[dict]`` 를 구현합니다.
        None은 기존 김매니저 core로 계속 진행, dict는 해당 이벤트의 최종 ACK입니다.
        """
        key = str(spec_id or "").strip()
        if not key or handler is None:
            raise ValueError("SPECIAL OZ event handler 등록값이 비어 있습니다")
        with self._lock:
            self._special_oz_event_handlers[key] = handler
            for previous, captured in self._special_oz_registration_scopes:
                if key not in previous:
                    captured[key] = handler

    def _special_oz_source_ids(self, event: dict) -> list[str]:
        candidates: list[str] = []
        raw_ids = event.get("source_spec_ids")
        if isinstance(raw_ids, (list, tuple, set)):
            candidates.extend(str(x).strip() for x in raw_ids if str(x).strip())
        single = str(event.get("source_spec_id") or "").strip()
        if single:
            candidates.append(single)

        watch_ids = [str(x) for x in (event.get("watch_ids") or []) if str(x)]
        one = str(event.get("watch_id") or "").strip()
        if one and one not in watch_ids:
            watch_ids.append(one)

        with self._lock:
            for wid in watch_ids:
                payload = self._active_children.get(wid)
                if not isinstance(payload, dict):
                    continue
                child_ids = payload.get("source_spec_ids")
                if isinstance(child_ids, (list, tuple, set)):
                    candidates.extend(str(x).strip() for x in child_ids if str(x).strip())
                child_single = str(payload.get("source_spec_id") or "").strip()
                if child_single:
                    candidates.append(child_single)
        return list(dict.fromkeys(candidates))

    def _dispatch_special_oz_event(self, event: dict) -> Optional[dict]:
        from composer_oz_dispatch import dispatch
        return dispatch(self, event)

    @staticmethod
    def _special_engine_requirement_payload(family: str, symbol: str, tf: str) -> Optional[tuple[str, str, dict]]:
        family = str(family or "").strip().upper()
        tf = normalize_tf(tf)
        symbol = str(symbol or "").strip()
        if not symbol or not tf:
            return None
        if family == "TREND":
            watch_id = stable_id("CMP:TREND", symbol, tf)
            return family, watch_id, {
                "action": "TREND_WATCH", "watch_id": watch_id,
                "symbol": symbol, "source_tf": tf,
            }
        if family == "FVG":
            watch_id = stable_id("CMP:FVG", symbol, tf)
            return family, watch_id, {
                "action": "FVG_WATCH", "watch_id": watch_id,
                "symbol": symbol, "source_tf": tf,
            }
        return None

    def _scan_special_strategies(self) -> None:
        """Register common Recipe presets once at host construction."""
        if self._special_initial_scan_done:
            return
        from strategy_recipe.registry import load_plugins
        selected = tuple(self._enabled_special_names) if self._special_selection_active else None
        if selected is not None:
            from strategy_recipe.registry import list_presets
            selected = tuple(name for name in list_presets('Part1') if name.casefold() in selected)
        import special_time_slot
        plugins = load_plugins(self.system_config, selected,
                               oz_profiles.parse_special_trigger_json(os.environ.get(oz_profiles.SPECIAL_TRIGGERS_ENV, '')),
                               special_time_slot.parse_special_time_json(os.environ.get(special_time_slot.SPECIAL_TIME_ENV, '')), part='Part1')
        for name, plugin in plugins.items():
            plugin.register(self)
            self.special_modules[name] = plugin
        self._special_initial_scan_done = True

    @staticmethod
    def _decode_alert_template(template: str) -> str:
        # SPECIAL 템플릿에 escape 문자열이 들어온 경우 실제 개행/탭으로 정규화합니다.
        return str(template or "").replace("\\n", "\n").replace("\\t", "\t")

    def _official_spec_for_oz_event_locked(self, event: dict) -> Optional[StrategySpec]:
        candidates: list[str] = []
        if event.get('signal_strategy') and event.get('source_spec_id'):
            key = str(event['source_spec_id'])
            return self.official_specs.get(key) or self.official_chain_specs.get(key)

        raw_ids = event.get("source_spec_ids")
        if isinstance(raw_ids, (list, tuple, set)):
            candidates.extend(str(x).strip() for x in raw_ids if str(x).strip())
        source_spec_id = str(event.get("source_spec_id") or "").strip()
        if source_spec_id:
            candidates.append(source_spec_id)

        # 현재 프로세스에서 Composer가 arm한 child는 SPECIAL 공식 spec_id로 다시 연결합니다.
        for wid in (event.get("watch_ids") or []):
            payload = self._active_children.get(str(wid))
            if payload:
                child_ids = payload.get("source_spec_ids")
                if isinstance(child_ids, (list, tuple, set)):
                    candidates.extend(str(x).strip() for x in child_ids if str(x).strip())
                child_id = str(payload.get("source_spec_id") or "").strip()
                if child_id:
                    candidates.append(child_id)

            chain_payload = self._config_chain_active.get(str(wid))
            if chain_payload:
                child_ids = chain_payload.get("source_spec_ids")
                if isinstance(child_ids, (list, tuple, set)):
                    candidates.extend(str(x).strip() for x in child_ids if str(x).strip())
                child_id = str(chain_payload.get("source_spec_id") or "").strip()
                if child_id:
                    candidates.append(child_id)

        for spec_id in dict.fromkeys(candidates):
            spec = self.official_specs.get(spec_id)
            if spec is not None:
                return spec
            chain_spec = self.official_chain_specs.get(spec_id)
            if chain_spec is not None:
                return chain_spec
        return None

    def _render_official_oz_alert(self, spec: StrategySpec, event: dict, fallback: str) -> str:
        template = self._decode_alert_template(spec.alert_template)
        if not template:
            return fallback

        direction = str(event.get("direction") or "").upper()
        side_icon = "🟢" if direction == "LONG" else "🔴" if direction == "SHORT" else "⚪"
        side_text = "매수" if direction == "LONG" else "매도" if direction == "SHORT" else direction
        tf = normalize_tf(event.get("source_tf") or event.get("tf")) or str(event.get("source_tf") or event.get("tf") or "")
        tf_label = OZ_TF_MAP.get(tf, tf)
        vm = str(event.get("validation_mode") or spec.validation_mode or "NORMAL").upper()
        tm = str(event.get("trigger_mode") or spec.trigger_mode or "OZ").upper()
        oz_label = _oz_profile_label(vm, tm)

        price = event.get("current_price")
        try:
            current_price = float(price)
        except (TypeError, ValueError):
            current_price = price

        values = {
            "side_icon": side_icon,
            "side_text": side_text,
            "direction": direction,
            "symbol": str(event.get("symbol") or spec.symbol),
            "sym": str(event.get("symbol") or spec.symbol),
            "tf": tf,
            "b_tf": tf,
            "tf_label": tf_label,
            "grade": str(event.get("grade") or ""),
            "indicators_text": str(event.get("indicators_text") or ""),
            "current_price": current_price,
            "trigger_name": str(event.get("trigger_name") or ""),
            "strategy_name": spec.name,
            "strategy_id": spec.spec_id,
            "validation_mode": vm,
            "trigger_mode": tm,
            "oz_label": oz_label,
        }
        try:
            return template.format_map(values)
        except Exception:
            logging.exception(
                "[Composer Strategy Alert] 템플릿 렌더링 실패 - 기존 알림 사용 | %s",
                spec.spec_id,
            )
            return fallback

    def _chain_watch_context(self, watch_id: object) -> Optional[dict]:
        """orchestrator의 LOCAL_EVENT payload가 최소형이어도 공용 Watch 계약으로 보강합니다."""
        wid = str(watch_id or "").strip()
        if not wid:
            return None
        with self._lock:
            for chain in getattr(self, "timed_chains", {}).values():
                for raw_index, current_id in chain.current_watch_ids.items():
                    if str(current_id or "") != wid:
                        continue
                    try:
                        index = int(raw_index)
                    except (TypeError, ValueError):
                        continue
                    if index < 0 or index >= len(chain.triggers):
                        continue
                    trig = chain.triggers[index]
                    condition_type, evaluation_mode = trigger_watch_contract(trig)
                    return {
                        "watch_id": wid,
                        "chain_id": chain.chain_id,
                        "chain_stage": index,
                        "watch_type": condition_type,
                        "evaluation_mode": evaluation_mode,
                        "timeframes": [trig.tf],
                        "symbol": chain.symbol,
                        "direction": trig.direction,
                        "level_side": trig.level_side,
                        "ma_family": trig.ma_family,
                        "fast_period": trig.fast_period,
                        "slow_period": trig.slow_period,
                        **({"ma_expression": trig.ma_expression} if trig.ma_expression is not None else {}),
                        "condition_name": getattr(trig, "condition_name", None),
                        "condition_combination": getattr(trig, "condition_combination", "ALL"),
                        "condition_specs": [dict(x) for x in (getattr(trig, "condition_specs", ()) or ())],
                        "request_chat_id": chain.owner_chat_id,
                        "silent": True,
                    }
        return None

    def _push(self, payload: dict) -> None:
        # 모든 Generic Watch 경계에서 condition type + evaluation_mode 계약으로 정규화합니다.
        item = canonical_watch_payload(payload)
        action = str(item.get("action") or "").upper()
        if not self._track_watch_payload(item):
            return
        if action in {"MANUAL_WATCH", "GENERIC_WATCH", "LOCAL_EVENT_WATCH"}:
            wid = str(item.get("watch_id") or "")
            managed = bool(item.get("chain_id") or item.get("source_spec_id")
                           or wid in getattr(self, "_active_children", {})
                           or wid in getattr(self, "_special_watch_handlers", {})
                           or self._chain_watch_context(wid))
            item.setdefault("watch_owner", "KIM" if managed else "OZ")

        # FVG_NEW는 monitor_OZ로 보내지 않습니다. strategy_FVG의 FVG_CREATED를
        # Composer가 직접 소비하므로 이 payload는 시간연쇄 상태/구독 갱신용 marker입니다.
        if action in {"FVG_EVENT_WATCH", "CANCEL_FVG_EVENT"}:
            with self._lock:
                self._subscription_dirty = True
            return

        # LOCAL_EVENT는 김매니저 내부 복합조건/SPECIAL handler와
        # monitor_OZ의 BAR/WONBI 공용 Generic Watch를 같은 lifecycle로 관리합니다.
        if action in {"LOCAL_EVENT_WATCH", "CANCEL_LOCAL_EVENT"}:
            context = self._chain_watch_context(item.get("watch_id"))
            if context:
                merged = dict(context)
                merged.update({k: v for k, v in item.items() if v is not None})
                item = canonical_watch_payload(merged)
            condition_type = str(item.get("watch_type") or "").upper()
            if action == "CANCEL_LOCAL_EVENT":
                self._compound_watch_state.pop(str(item.get("watch_id") or ""), None)
                if condition_type in {"BAR", "WONBI_TOUCH"}:
                    item["action"] = "CANCEL_GENERIC"
                    self.oz_queue.push(item)
                return
            if condition_type in {"BAR", "WONBI_TOUCH"}:
                item["action"] = "GENERIC_WATCH"
                self.oz_queue.push(item)
            return

        self.oz_queue.push(item)

    # -------------------------------
    # persistence
    # -------------------------------
    def _load_private_state(self) -> None:
        if not domain_memory.exists(self._state_path):
            return
        try:
            raw = read_json(self._state_path)
            self._rejected_private_specs = list(raw.get("rejected_watches", []))
            restored = {}
            for item in raw.get("watches", []) if isinstance(raw, dict) else []:
                try:
                    spec = StrategySpec.from_json(load_saved_oz(item, path=f"strategies.{item.get('spec_id')}"))
                except ValueError as exc:
                    logging.error("[Composer] saved strategy rejected | spec_id=%s | %s", item.get("spec_id"), exc)
                    self._rejected_private_specs.append({"spec_id":item.get("spec_id"),"reason":str(exc),"original":dict(item)})
                    continue
                if spec.source == "PRIVATE" and spec.owner_chat_id:
                    restored[spec.spec_id] = spec
            with self._lock:
                self.manual_specs = restored
                # Older files have no Telegram metadata and are valid as-is.
                links = raw.get("watch_message_links", []) if isinstance(raw, dict) else []
                self._watch_message_links = {
                    str(x["watch_id"]): x for x in links
                    if isinstance(x, dict) and x.get("watch_id") and x.get("owner_chat_id")
                    and self._telegram_message_id(x.get("command_message_id")) is not None
                    and isinstance(x.get("members"), dict)
                    and x.get("cancel_action") in {
                        "CANCEL_MANUAL", "CANCEL_GENERIC", "PRIVATE", "FVG_NEW", "TIMED_CHAIN"}
                }
            if restored:
                if logging.getLogger().isEnabledFor(logging.INFO):
                    logging.info("♻️ [Composer] 개인전략 복원 %d건", len(restored))
        except Exception:
            logging.exception("[Composer] 개인전략 상태 복원 실패")

    def _save_private_state_locked(self) -> None:
        payload = {
            "version": 3,
            "watches": [x.to_json() for x in self.manual_specs.values()],
            "rejected_watches": list(getattr(self, "_rejected_private_specs", [])),
            "watch_message_links": list(self._watch_message_links.values()),
        }
        _atomic_write_json(self._state_path, payload)

    def _load_timed_chain_state(self) -> None:
        self.watch_orchestrator.load_state()
        if self.timed_chains:
            if logging.getLogger().isEnabledFor(logging.INFO):
                logging.info("♻️ [Composer 시간연쇄] %d건 복원", len(self.timed_chains))

    def _save_timed_chain_state_locked(self) -> None:
        self.watch_orchestrator.save_state_locked()

    @staticmethod
    def _default_config_chain_state() -> dict:
        return {
            "stage": 0,
            "latest_cross": None,
            "latest_fvg": None,
            "stage_deadline": None,
            "unordered_latch": UnorderedConditionLatch.new_state(),
            "post_touch_armed": None,
            "last_pair_signature": None,
            "spec_signature": None,
        }

    @staticmethod
    def _config_chain_spec_signature(spec: ConfigTimedChainSpec) -> str:
        parts: list[object] = [
            "CFGCHAINSTATE", spec.spec_id, spec.symbol, spec.cross_tf, spec.ma_family,
            spec.fast_period, spec.slow_period, ",".join(spec.fvg_tfs), spec.max_gap_sec,
        ]
        if spec.order_mode != "SEQUENTIAL":
            parts.extend(("ORDER_MODE", spec.order_mode))
        # MAX_GAP_BARS=0(기존 동작)에서는 이전 순서형 기준본과 같은 signature를 유지합니다.
        if spec.max_gap_bars > 0:
            parts.extend(("MAX_GAP_BARS", spec.max_gap_bars))
        if spec.final_fvg_touch_tfs:
            parts.extend(("FINAL_FVG_TOUCH_TFS", ",".join(spec.final_fvg_touch_tfs)))
        if spec.final_time_filters:
            parts.extend(("FINAL_TIME_FILTER", ",".join(spec.final_time_filters)))
        if spec.cancel_on_opposite_cross:
            parts.extend(("CANCEL_ON_OPPOSITE_CROSS", True))
        if spec.cancel_on_opposite_fvg_tfs:
            parts.extend(("CANCEL_ON_OPPOSITE_FVG_TFS", ",".join(spec.cancel_on_opposite_fvg_tfs)))
        return stable_id(*parts, length=24)

    @staticmethod
    def _config_chain_value_epoch(value) -> Optional[float]:
        if value in {None, ""}:
            return None
        try:
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                ts = float(value)
            else:
                stamp = pd.Timestamp(value)
                if pd.isna(stamp):
                    return None
                if stamp.tzinfo is None:
                    stamp = stamp.tz_localize("UTC")
                else:
                    stamp = stamp.tz_convert("UTC")
                ts = float(stamp.timestamp())
            return ts if math.isfinite(ts) else None
        except Exception:
            return None

    def _config_chain_closed_bar_times(
        self, spec: ConfigTimedChainSpec, client: StaffClientV2
    ) -> tuple[float, ...]:
        """CROSS_TF의 실제 확정봉 시각을 오름차순 epoch로 반환합니다."""
        try:
            data = client.request(spec.symbol, [spec.cross_tf], [])
            df = data.get(spec.cross_tf) if isinstance(data, dict) else None
            if df is None or len(df) < 2 or "time" not in df.columns:
                return ()
            # STAFF 계약상 마지막 행은 live bar이고 -2가 최신 확정봉입니다.
            values = df.iloc[:-1]["time"].tolist()
            epochs = [self._config_chain_value_epoch(x) for x in values]
            return tuple(sorted({float(x) for x in epochs if x is not None}))
        except Exception:
            logging.exception(
                "[Composer SPECIAL TIMED_CHAIN] 봉 수 expiry용 STAFF 조회 실패 | %s %s",
                spec.symbol, spec.cross_tf,
            )
            return ()

    @staticmethod
    def _config_chain_bar_age_from_times(anchor: Optional[float], bar_times: tuple[float, ...]) -> Optional[int]:
        if anchor is None or not bar_times:
            return None
        try:
            anchor_f = float(anchor)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(anchor_f):
            return None
        return sum(1 for ts in bar_times if float(ts) > anchor_f + 1e-6)

    @staticmethod
    def _config_chain_expiry_status(
        spec: ConfigTimedChainSpec, state: dict, now: float, bar_age: Optional[int]
    ) -> tuple[bool, Optional[str]]:
        deadline = state.get("stage_deadline")
        if spec.max_gap_sec > 0:
            try:
                if deadline is None or not math.isfinite(float(deadline)) or float(now) > float(deadline):
                    return True, "time"
            except (TypeError, ValueError):
                return True, "time"
        if spec.max_gap_bars > 0 and bar_age is not None and int(bar_age) > int(spec.max_gap_bars):
            # MAX_GAP_BARS=N은 A 다음 N개 확정봉까지 B를 허용합니다. N+1번째 확정봉부터 만료입니다.
            return True, "bars"
        return False, None

    def _load_config_chain_state_for_specs_locked(
        self, specs: dict[str, ConfigTimedChainSpec]
    ) -> dict[str, dict]:
        """공식 TIMED_CHAIN의 A→B 진행상태를 현재 config에 맞춰 복원합니다."""
        # SPECIAL modules register separately. Keep the unread startup records
        # until their owner registers, even if an earlier module saves first.
        if not hasattr(self, '_config_chain_restore_records'):
            self._config_chain_restore_records = {'states': {}, 'active': {}}
        if not getattr(self, '_config_chain_restore_loaded', False) and domain_memory.exists(self._config_chain_state_path):
            try:
                raw = read_json(self._config_chain_state_path)
                if isinstance(raw, dict) and isinstance(raw.get("states"), dict):
                    self._config_chain_restore_records = {
                        'states': raw['states'], 'active': raw.get('active', {})}
            except Exception:
                logging.exception(
                    "[Composer SPECIAL TIMED_CHAIN] 상태 복원 실패 | %s",
                    self._config_chain_state_path,
                )

        self._config_chain_restore_loaded = True
        raw_states = self._config_chain_restore_records['states']
        restored: dict[str, dict] = {}
        for spec_id, spec in specs.items():
            state = self._default_config_chain_state()
            expected_signature = self._config_chain_spec_signature(spec)
            state["spec_signature"] = expected_signature
            item = raw_states.get(spec_id)
            if isinstance(item, dict) and item.get("spec_signature") == expected_signature:
                state["last_pair_signature"] = item.get("last_pair_signature")
                post_touch = item.get("post_touch_armed") if isinstance(item.get("post_touch_armed"), dict) else None
                if post_touch is not None and spec.final_fvg_touch_tfs:
                    try:
                        expires_at = float(post_touch.get("expires_at"))
                        direction = str(post_touch.get("direction") or "").upper()
                        if math.isfinite(expires_at) and direction in {"LONG", "SHORT"}:
                            state["post_touch_armed"] = dict(post_touch)
                    except (TypeError, ValueError):
                        pass
                if spec.order_mode == "UNORDERED":
                    state["unordered_latch"] = UnorderedConditionLatch.normalize_state(
                        item.get("unordered_latch")
                    )
                    restored[spec_id] = state
                    continue
                try:
                    stage = int(item.get("stage", 0))
                except (TypeError, ValueError):
                    stage = 0
                cross = item.get("latest_cross") if isinstance(item.get("latest_cross"), dict) else None
                deadline = item.get("stage_deadline")
                try:
                    deadline = float(deadline) if deadline is not None else None
                except (TypeError, ValueError):
                    deadline = None

                # 유한한 deadline을 보존하여 지연 도착한 완료 시각을 판정합니다.
                # 봉 수 제한은 기존 실제 확정봉 기준으로 callback에서 재검사합니다.
                time_alive = spec.max_gap_sec <= 0 or (deadline is not None and math.isfinite(deadline))
                if stage == 1 and cross is not None and time_alive:
                    try:
                        cross_ts = float(cross.get("ts"))
                        bar_anchor = cross.get("bar_anchor_ts")
                        if spec.max_gap_bars > 0:
                            bar_anchor = float(bar_anchor)
                        if math.isfinite(cross_ts) and (
                            spec.max_gap_bars <= 0 or math.isfinite(float(bar_anchor))
                        ):
                            state["stage"] = 1
                            state["latest_cross"] = dict(cross)
                            state["stage_deadline"] = deadline
                    except (TypeError, ValueError):
                        pass
            restored[spec_id] = state

        raw_active = self._config_chain_restore_records['active']
        for wid, item in list(raw_active.items()):
            spec = specs.get(item.get('spec_id')) if isinstance(item, dict) else None
            if spec is None:
                continue
            if (item.get('spec_signature') == self._config_chain_spec_signature(spec)
                    and self._config_chain_value_epoch(item.get('expires_at')) is not None):
                self._config_chain_active[wid] = dict(item)
            raw_active.pop(wid, None)
        for spec_id in specs:
            raw_states.pop(spec_id, None)
        waiting_b = sum(1 for x in restored.values() if int(x.get("stage", 0)) == 1)
        if waiting_b:
            if logging.getLogger().isEnabledFor(logging.INFO):
                logging.info("♻️ [Composer SPECIAL TIMED_CHAIN] B 대기 상태 %d건 복원", waiting_b)
        return restored

    def _save_config_chain_state_locked(self) -> None:
        unread = getattr(self, '_config_chain_restore_records', {})
        payload = {
            "version": 4,
            "states": dict(unread.get('states', {}), **self._config_chain_state),
            "active": dict(unread.get('active', {}), **{
                wid: dict(item, spec_signature=self._config_chain_spec_signature(
                    self.official_chain_specs[item['spec_id']]))
                for wid, item in self._config_chain_active.items()
                if item.get('spec_id') in self.official_chain_specs}),
        }
        try:
            _atomic_write_json(self._config_chain_state_path, payload)
        except Exception:
            logging.exception(
                "[Composer SPECIAL TIMED_CHAIN] 상태 저장 실패 | %s",
                self._config_chain_state_path,
            )

    def _load_fvg_created_watch_state(self) -> None:
        restored: dict[str, FVGCreatedWatchSpec] = {}
        if domain_memory.exists(self._fvg_created_watch_state_path):
            try:
                raw = read_json(self._fvg_created_watch_state_path)
                self._rejected_fvg_watches = list(raw.get("rejected_watches", []))
                for item in raw.get("watches", []) if isinstance(raw, dict) else []:
                    try:spec = FVGCreatedWatchSpec.from_json(load_saved_oz(item, path=f"fvg.{item.get('watch_id')}"))
                    except ValueError as exc:
                        self._rejected_fvg_watches.append({'watch_id':item.get('watch_id'),'reason':str(exc),'original':dict(item)})
                        logging.error('[Composer/FVG 복원 격리] watch_id=%s | %s', item.get('watch_id'), exc)
                        continue
                    restored[spec.watch_id] = spec
            except Exception:
                logging.exception("[Composer/FVG 생성 Watch] 상태 복원 실패 | %s", self._fvg_created_watch_state_path)

        self.fvg_created_watches = restored
        if restored:
            if logging.getLogger().isEnabledFor(logging.INFO):
                logging.info("♻️ [Composer/FVG 생성 Watch] %d건 복원", len(restored))

    def _save_fvg_created_watch_state_locked(self) -> None:
        payload = {
            "version": 1,
            "watches": [w.to_json() for w in self.fvg_created_watches.values()],
            "rejected_watches": list(getattr(self,"_rejected_fvg_watches",[])),
        }
        try:
            _atomic_write_json(self._fvg_created_watch_state_path, payload)
        except Exception:
            logging.exception("[Composer/FVG 생성 Watch] 상태 저장 실패 | %s", self._fvg_created_watch_state_path)

    def _add_fvg_created_watch(self, payload: dict) -> None:
        watch = FVGCreatedWatchSpec(
            watch_id=payload.get("watch_id"), symbol=payload.get("symbol"),
            timeframes=tuple(payload.get("timeframes", [])), direction=payload.get("direction"),
            persistent=bool(payload.get("persistent", False)),
            request_chat_id=payload.get("request_chat_id"), silent=bool(payload.get("silent", False)),
            final_action=payload.get("final_action", "NOTIFY"),
            oz_tfs=tuple(payload.get("oz_tfs", [])), oz_direction=payload.get("oz_direction"),
            validation_mode=payload.get("validation_mode", "NORMAL"),
            trigger_mode=payload.get("trigger_mode", "OZ"),
        )
        watch.validate()
        with self._lock:
            if watch.watch_id in self.fvg_created_watches:
                return
            self._remember_watch_command(watch.watch_id, watch.request_chat_id, "FVG_NEW", "FVG_NEW")
            self.fvg_created_watches[watch.watch_id] = watch
            self._save_fvg_created_watch_state_locked()
            self._subscription_dirty = True
        if not watch.silent:
            mode = " 지속" if watch.persistent else ""
            self.send_telegram(f"✅ {watch.symbol} · {watch.label()}{mode} 감시", watch.request_chat_id,
                               watch_id=watch.watch_id, watch_registration=True)
        if logging.getLogger().isEnabledFor(logging.INFO):
            logging.info("🟦 [Composer/FVG 생성 Watch] 시작 | %s | %s | persistent=%s", watch.watch_id, watch.label(), watch.persistent)

    def _load_active_children_state(self) -> None:
        if not domain_memory.exists(self._active_children_state_path):
            with self._lock:
                self._save_active_children_state_locked()
            return
        try:
            raw = read_json(self._active_children_state_path)
            self._rejected_profile_children = list(raw.get("rejected_watches", [])) if isinstance(raw, dict) else []
            restored: dict[str, dict] = {}
            for item in raw.get("watches", []) if isinstance(raw, dict) else []:
                if not isinstance(item, dict):
                    continue
                watch_id = str(item.get("watch_id") or "").strip()
                action = str(item.get("action") or "").strip().upper()
                if not watch_id.startswith("OZARM:") or action != "MANUAL_WATCH":
                    continue
                try:
                    payload = load_saved_oz(item, path=f"children.{watch_id}")
                except oz_profiles.ProfileError as exc:
                    self._rejected_profile_children.append({'watch_id': watch_id, 'reason': str(exc), 'original': dict(item)})
                    logging.error('[Composer OZ 복원 격리] watch_id=%s | %s', watch_id, exc)
                    continue
                payload["watch_id"] = watch_id
                payload["action"] = "MANUAL_WATCH"
                restored[watch_id] = payload
            with self._lock:
                self._active_children = restored
            if restored:
                if logging.getLogger().isEnabledFor(logging.INFO):
                    logging.info("♻️ [Composer→OZ] 재시작 감시 소유권 복원 %d건", len(restored))
        except Exception:
            logging.exception("[Composer→OZ] 현재 감시 상태 복원 실패")

    def _save_active_children_state_locked(self) -> None:
        payload = {
            "version": 1,
            "watches": [dict(x) for x in self._active_children.values()],
            "rejected_watches": list(getattr(self, "_rejected_profile_children", [])),
        }
        _atomic_write_json(self._active_children_state_path, payload)

    @staticmethod
    def _persisted_watch_ids(path: Path, key: str, mapping: bool) -> set[str]:
        """저장된 감시 번호만 읽습니다. 읽기 실패 시 빈 집합으로 처리합니다."""
        if not domain_memory.exists(path):
            return set()
        try:
            raw = read_json(path)
            items = raw.get(key, {} if mapping else []) if isinstance(raw, dict) else ({} if mapping else [])
            if mapping:
                if not isinstance(items, dict):
                    return set()
                return {str(x).strip() for x in items if str(x).strip()}
            if not isinstance(items, list):
                return set()
            return {
                str(item.get("watch_id") or "").strip()
                for item in items
                if isinstance(item, dict) and str(item.get("watch_id") or "").strip()
            }
        except Exception:
            logging.exception("[Composer 시작 동기화] 상태 파일 읽기 실패 | %s", path)
            return set()

    def _official_child_matches_spec_locked(self, payload: dict, spec: StrategySpec) -> bool:
        if str(payload.get("symbol") or "") != spec.symbol:
            return False
        child_tfs = tuple(normalize_tf(x) for x in (payload.get("timeframes") or ()) if normalize_tf(x))
        if child_tfs != tuple(spec.oz_tfs):
            return False
        if str(payload.get("validation_mode") or "NORMAL").upper() != spec.validation_mode:
            return False
        if _canonical_trigger_mode(payload.get("trigger_mode")) != spec.trigger_mode:
            return False

        external_watch_id = str(payload.get("external_watch_id") or "").strip()
        current_sweep_ids = {
            self._sweep_subscription_payload(spec, cond)["watch_id"]
            for cond in spec.conditions
            if cond.kind == "SWEEP"
        }
        if external_watch_id:
            return external_watch_id in current_sweep_ids
        return not current_sweep_ids

    def _prune_stale_official_children_locked(self) -> list[dict]:
        """현재 등록된 전략 소유자와 맞지 않는 OZ 감시만 제거합니다."""
        cancel_payloads: list[dict] = []
        changed = False
        for child_id, payload in list(self._active_children.items()):
            # 개인 감시는 해당 사용자의 저장 상태가 기준이므로 여기서 제거하지 않습니다.
            if str(payload.get("request_chat_id") or "").strip() and payload.get("watch_owner") != "KIM":
                continue

            source_ids = []
            raw_ids = payload.get("source_spec_ids")
            if isinstance(raw_ids, (list, tuple, set)):
                source_ids.extend(str(x).strip() for x in raw_ids if str(x).strip())
            source_id = str(payload.get("source_spec_id") or "").strip()
            if source_id:
                source_ids.append(source_id)

            live_ids = []
            for spec_id in dict.fromkeys(source_ids):
                if spec_id in self._special_oz_event_handlers:
                    live_ids.append(spec_id)
                    continue
                spec = self.official_specs.get(spec_id)
                if spec is not None and spec.enabled and self._official_child_matches_spec_locked(payload, spec):
                    live_ids.append(spec_id)

            if live_ids:
                normalized = list(dict.fromkeys(live_ids))
                if payload.get("source_spec_ids") != normalized:
                    payload["source_spec_ids"] = normalized
                    changed = True
                primary = normalized[0]
                if str(payload.get("source_spec_id") or "") != primary:
                    payload["source_spec_id"] = primary
                    changed = True
                spec = self.official_specs.get(primary)
                if spec is not None and str(payload.get("source_name") or "") != spec.name:
                    payload["source_name"] = spec.name
                    changed = True
                continue

            self._active_children.pop(child_id, None)
            cancel_payloads.append({
                "action": "CANCEL_MANUAL",
                "watch_id": child_id,
                "request_chat_id": None,
                "validation_mode": payload.get("validation_mode"),
                "trigger_mode": payload.get("trigger_mode"),
            })
            changed = True

        if changed:
            self._save_active_children_state_locked()
        return cancel_payloads

    # -------------------------------
    # config pipelines / hot reload
    # -------------------------------
    def _config_chain_trigger_payloads_locked(self, spec: ConfigTimedChainSpec) -> list[dict]:
        chain_id = f"CFGCHAIN:{spec.spec_id}"
        payloads = [{
            "action": "GENERIC_WATCH",
            "watch_id": stable_id("CFGCHAINTRG", spec.spec_id, "CROSS", length=20),
            "watch_type": f"{spec.ma_family}_CROSS",
            "timeframes": [spec.cross_tf],
            "symbol": spec.symbol,
            "direction": None,
            "ma_family": spec.ma_family,
            "fast_period": spec.fast_period,
            "slow_period": spec.slow_period,
            "persistent": True,
            "request_chat_id": None,
            "chain_id": chain_id,
            "chain_stage": 0,
            "silent": True,
            "source_spec_id": spec.spec_id,
        }]
        for idx, tf in enumerate(spec.fvg_tfs, start=1):
            payloads.append({
                "action": "FVG_EVENT_WATCH",
                "watch_id": stable_id("CFGCHAINTRG", spec.spec_id, "FVG", tf, length=20),
                "watch_type": "FVG_NEW",
                "timeframes": [tf],
                "symbol": spec.symbol,
                "direction": None,
                "persistent": True,
                "request_chat_id": None,
                "chain_id": chain_id,
                "chain_stage": idx,
                "silent": True,
                "source_spec_id": spec.spec_id,
            })
        return payloads

    # -------------------------------
    # engine subscriptions
    # -------------------------------
    def _all_specs_locked(self) -> tuple[StrategySpec, ...]:
        return tuple(x for x in (*self.official_specs.values(), *self.manual_specs.values()) if x.enabled)

    def _compound_conditions_for_trigger(self, trig: ChainTriggerSpec) -> tuple[ConditionSpec, ...]:
        condition_type, _evaluation_mode = trigger_watch_contract(trig)
        if condition_type != "COMPOUND_CONDITION":
            return ()
        out: list[ConditionSpec] = []
        for raw in getattr(trig, "condition_specs", ()) or ():
            try:
                cond = self._condition_from_descriptor(dict(raw), trig.tf)
            except Exception:
                logging.exception("[Composer 복합조건] primitive 조건 변환 실패 | %s", raw)
                continue
            if cond not in out:
                out.append(cond)
        return tuple(out)

    def _compound_spec_for_trigger(self, chain: TimedChainSpec, trig: ChainTriggerSpec) -> Optional[StrategySpec]:
        conditions = self._compound_conditions_for_trigger(trig)
        if not conditions:
            return None
        return StrategySpec(
            spec_id=f"COMPOUND:{chain.chain_id}:{getattr(trig, 'condition_name', None) or 'condition'}:{trig.tf}",
            name=str(getattr(trig, "condition_name", None) or "복합조건"),
            symbol=chain.symbol,
            conditions=conditions,
            oz_tfs=("1m",),
            final_action="NOTIFY",
            combination=str(getattr(trig, "condition_combination", "ALL") or "ALL").upper(),
            destination="PRIVATE",
            owner_chat_id=chain.owner_chat_id,
            persistent=True,
            enabled=True,
            source="PRIVATE",
        )

    def _sweep_subscription_payload(self, spec: StrategySpec, cond: ConditionSpec) -> dict:
        london = str(self.config.get("LONDON") or self.config.get("MAIN_LONDON") or "").strip()
        newyork = str(self.config.get("NEWYORK") or self.config.get("MAIN_NEWYORK") or "").strip()
        levels = (cond.side,) if cond.side else spec.sweep_levels
        watch_id = _cached_sweep_watch_id(
            spec.symbol, cond.tf, ",".join(levels),
            spec.sweep_atr_period, spec.sweep_atr_mult, london, newyork,
        )
        return {
            "action": "SWEEP_WATCH",
            "watch_id": watch_id,
            "symbol": spec.symbol,
            "source_tf": cond.tf,
            "levels": list(levels),
            "atr_period": spec.sweep_atr_period,
            "atr_mult": spec.sweep_atr_mult,
            "session_london": london,
            "session_newyork": newyork,
        }

    def _desired_subscriptions_locked(self) -> dict[str, dict[str, dict]]:
        desired: dict[str, dict[str, dict]] = {"TREND": {}, "FVG": {}, "SWEEP": {}}

        def add_trend(symbol: str, tf: str, requested_fields=()) -> None:
            symbol = str(symbol or "").strip()
            tf = normalize_tf(tf)
            if not symbol or not tf:
                return
            wid = stable_id("CMP:TREND", symbol, tf)
            payload = desired["TREND"].setdefault(wid, {
                "action": "TREND_WATCH", "watch_id": wid,
                "symbol": symbol, "source_tf": tf,
            })
            fields = {
                str(x or "").strip().lower()
                for x in requested_fields
                if str(x or "").strip().lower() in TREND_METRIC_FIELDS
            }
            if fields:
                merged = set(payload.get("requested_fields") or ()) | fields
                payload["requested_fields"] = sorted(merged)

        for spec in self._all_specs_locked():
            for cond in spec.conditions:
                if cond.kind == "TREND":
                    add_trend(spec.symbol, cond.tf)
                elif cond.kind == "TREND_METRIC":
                    fields = [cond.metric]
                    if cond.metric_rhs:
                        fields.append(cond.metric_rhs)
                    add_trend(spec.symbol, cond.tf, fields)
                elif cond.kind == "FVG":
                    wid = stable_id("CMP:FVG", spec.symbol, cond.tf)
                    desired["FVG"][wid] = {
                        "action": "FVG_WATCH", "watch_id": wid,
                        "symbol": spec.symbol, "source_tf": cond.tf,
                    }
                elif cond.kind == "SWEEP":
                    payload = self._sweep_subscription_payload(spec, cond)
                    desired["SWEEP"][payload["watch_id"]] = payload

        # SPECIAL 전략은 필요한 사실 엔진 구독만 선언합니다. 김매니저는 전략명을 분기하지 않습니다.
        for chain in self.timed_chains.values():
            if not chain.enabled:
                continue
            for trig in chain.triggers:
                condition_type, evaluation_mode = trigger_watch_contract(trig)
                if condition_type == "COMPOUND_CONDITION":
                    compound_spec = self._compound_spec_for_trigger(chain, trig)
                    if compound_spec is not None:
                        for cond in compound_spec.conditions:
                            if cond.kind == "TREND":
                                add_trend(compound_spec.symbol, cond.tf)
                            elif cond.kind == "TREND_METRIC":
                                fields = [cond.metric]
                                if cond.metric_rhs:
                                    fields.append(cond.metric_rhs)
                                add_trend(compound_spec.symbol, cond.tf, fields)
                            elif cond.kind == "FVG":
                                add_fvg_id = stable_id("CMP:FVG", compound_spec.symbol, cond.tf)
                                desired["FVG"][add_fvg_id] = {
                                    "action": "FVG_WATCH", "watch_id": add_fvg_id,
                                    "symbol": compound_spec.symbol, "source_tf": cond.tf,
                                }
                            elif cond.kind == "SWEEP":
                                payload = self._sweep_subscription_payload(compound_spec, cond)
                                desired["SWEEP"][payload["watch_id"]] = payload
                    continue
                handler = self._special_watch_handlers.get(condition_type)
                requirement_fn = getattr(handler, "engine_requirements", None) if handler is not None else None
                if not callable(requirement_fn):
                    continue
                try:
                    requirements = requirement_fn(chain, trig, evaluation_mode) or ()
                except Exception:
                    logging.exception("[SPECIAL Watch] engine requirement 오류 | %s", condition_type)
                    continue
                for requirement in requirements:
                    try:
                        family, symbol, tf = requirement
                    except (TypeError, ValueError):
                        continue
                    resolved = self._special_engine_requirement_payload(family, symbol, tf)
                    if resolved is None:
                        continue
                    family, wid, payload = resolved
                    if family == "TREND":
                        add_trend(symbol, tf, payload.get("requested_fields") or ())
                    elif family in desired:
                        desired[family][wid] = payload

        # 신규 FVG 생성 알림/시간연쇄 역시 strategy_FVG 한 곳만 구독합니다.
        def add_fvg(symbol: str, tf: str) -> None:
            tf = normalize_tf(tf)
            if not symbol or not tf:
                return
            wid = stable_id("CMP:FVG", symbol, tf)
            desired["FVG"][wid] = {
                "action": "FVG_WATCH", "watch_id": wid,
                "symbol": symbol, "source_tf": tf,
            }

        for watch in self.fvg_created_watches.values():
            for tf in watch.timeframes:
                add_fvg(watch.symbol, tf)

        for chain in self.timed_chains.values():
            if not chain.enabled:
                continue
            if not chain.active_child_id or chain.order_mode == "FILTER":
                if chain.order_mode in {"UNORDERED", "FILTER"}:
                    for trig in chain.triggers:
                        if trig.watch_type == "FVG_NEW":
                            add_fvg(chain.symbol, trig.tf)
                elif chain.stage < len(chain.triggers):
                    trig = chain.triggers[chain.stage]
                    if trig.watch_type == "FVG_NEW":
                        add_fvg(chain.symbol, trig.tf)
            # CANCEL_ON FVG는 후보가 생겨 실제 invalidation watch_id가 arm된 동안에만 구독합니다.
            for raw_idx in tuple(chain.invalidation_watch_ids):
                try:
                    idx = int(raw_idx)
                except (TypeError, ValueError):
                    continue
                if 0 <= idx < len(chain.invalidation_triggers):
                    cancel_trig = chain.invalidation_triggers[idx]
                    if cancel_trig.watch_type == "FVG_NEW":
                        add_fvg(chain.symbol, cancel_trig.tf)

        for chain_spec in self.official_chain_specs.values():
            if not chain_spec.enabled:
                continue
            for tf in dict.fromkeys((*chain_spec.fvg_tfs, *chain_spec.final_fvg_touch_tfs)):
                add_fvg(chain_spec.symbol, tf)
        for provider in tuple(self._special_subscription_providers.values()):
            requirements = provider()
            if not isinstance(requirements, dict):
                raise TypeError("subscription provider must return a dict")
            for family, watches in requirements.items():
                if family not in desired or not isinstance(watches, dict):
                    raise ValueError("invalid subscription family or watch mapping")
                for wid, payload in watches.items():
                    if not isinstance(payload, dict) or not wid or payload.get("watch_id") != wid:
                        raise ValueError("invalid subscription watch payload")
                    desired[family][wid] = dict(payload)
        return desired

    def _sync_engine_subscriptions(self, force_refresh: bool = False) -> None:
        with self._lock:
            desired = self._desired_subscriptions_locked()
            current = {k: dict(v) for k, v in self._engine_subscriptions.items()}

        cancel_actions = {"TREND": "CANCEL_TREND", "FVG": "CANCEL_FVG", "SWEEP": "CANCEL_SWEEP"}
        for family in ("TREND", "FVG", "SWEEP"):
            removed = set(current[family]) - set(desired[family])
            for wid in sorted(removed):
                self._push({"action": cancel_actions[family], "watch_id": wid})
            for wid, payload in desired[family].items():
                if force_refresh or wid not in current[family] or current[family][wid] != payload:
                    self._push(payload)

        with self._lock:
            self._engine_subscriptions = desired
            self._subscription_dirty = False

    def _refresh_engine_family_after_ping(self, family: str) -> int:
        """전략 프로그램 시작 시 저장 상태와 현재 KIM 설정을 맞춘 뒤 현재 감시를 재전달합니다."""
        family = str(family or "").upper()
        if family not in {"TREND", "FVG", "SWEEP"}:
            return 0

        state_files = {
            "TREND": (LOG_DIR / "trend_watch_state.json", "CANCEL_TREND"),
            "FVG": (LOG_DIR / "fvg_watch_state.json", "CANCEL_FVG"),
            "SWEEP": (LOG_DIR / "sweep_watch_state.json", "CANCEL_SWEEP"),
        }
        with self._lock:
            desired_all = self._desired_subscriptions_locked()
            desired = {k: dict(v) for k, v in desired_all.get(family, {}).items()}

        state_path, cancel_action = state_files[family]
        persisted = self._persisted_watch_ids(state_path, "watches", mapping=True)
        stale = sorted(persisted - set(desired))
        for wid in stale:
            self._push({"action": cancel_action, "watch_id": wid})
        for payload in desired.values():
            self._push(dict(payload))

        with self._lock:
            self._engine_subscriptions[family] = desired

        if logging.getLogger().isEnabledFor(logging.INFO):
            logging.info(
                "♻️ [Composer 시작 동기화] %s | 옛 감시 %d건 정리 · 현재 감시 %d건 재전달",
                family, len(stale), len(desired),
            )
        return len(stale) + len(desired)

    def _refresh_oz_after_ping(self) -> int:
        """OZ 시작 시 옛 감시만 취소하고 현재 KIM이 소유한 감시는 그대로 복구합니다."""
        now = time.time()
        payloads: list[dict] = []
        with self._lock:
            # 현재 공식 설정과 맞지 않는 재시작 잔여 감시를 먼저 KIM 상태에서 제거합니다.
            stale_child_cancels = self._prune_stale_official_children_locked()

            # 일반 Composer 전략에서 만들어진 현재 OZ 감시
            payloads.extend(dict(x) for x in self._active_children.values())

            # 개인 시간연쇄의 현재 단계 또는 기간제 OZ 감시
            for chain in self.timed_chains.values():
                if not chain.enabled:
                    continue
                if chain.active_child_id:
                    payload = self._chain_final_payload_locked(chain)
                    if payload:
                        payloads.append(dict(payload))
                else:
                    payloads.extend(dict(x) for x in self._chain_trigger_payloads_locked(chain))
                payloads.extend(dict(x) for x in self.watch_orchestrator.invalidation_payloads_locked(chain))

            # 설정 파일의 상시 시간연쇄 조건 감시
            for spec in self.official_chain_specs.values():
                if spec.enabled:
                    payloads.extend(dict(x) for x in self._config_chain_trigger_payloads_locked(spec))

            # 설정 파일 시간연쇄에서 이미 성립해 기간제로 살아 있는 OZ 감시
            for item in self._config_chain_active.values():
                payloads.append({k: v for k, v in item.items()
                                 if k not in {"expires_at", "spec_id", "completion_deadline", "spec_signature"}})

            # 외부유동성 Gate의 현재 정답 목록도 KIM의 현재 전략 조건에서 계산합니다.
            desired_external = self._desired_subscriptions_locked().get("SWEEP", {})

        # 같은 감시가 여러 경로에서 잡혀도 한 번만 사용합니다.
        unique: dict[tuple[str, str], dict] = {}
        for payload in payloads:
            item = canonical_watch_payload(payload)
            action = str(item.get("action") or "").upper()
            if action == "LOCAL_EVENT_WATCH":
                context = self._chain_watch_context(item.get("watch_id"))
                if context:
                    merged = dict(context)
                    merged.update({k: v for k, v in item.items() if v is not None})
                    item = canonical_watch_payload(merged)
                if str(item.get("watch_type") or "").upper() in {"BAR", "WONBI_TOUCH"}:
                    item["action"] = "GENERIC_WATCH"
                    action = "GENERIC_WATCH"
            key = (action, str(item.get("watch_id") or ""))
            if key[1]:
                unique[key] = item

        desired_manual_ids = {wid for (action, wid) in unique if action == "MANUAL_WATCH"}
        desired_generic_ids = {wid for (action, wid) in unique if action == "GENERIC_WATCH"}
        desired_external_ids = set(desired_external)
        # Direct Telegram WATCH roots live in OZ's existing persisted state. Their
        # reply mapping keeps startup cleanup from treating them as orphaned KIM children.
        with self._lock:
            for link in self._watch_message_links.values():
                if link.get("cancel_action") == "CANCEL_MANUAL" and link.get("status") == "active":
                    desired_manual_ids.add(link["watch_id"])
                    desired_external_ids.add(link["watch_id"])

        persisted_manual = self._persisted_owned_oz_ids(LOG_DIR / "oz_manual_watch_state.json")
        persisted_generic = self._persisted_owned_oz_ids(LOG_DIR / "oz_generic_watch_state.json")
        persisted_external = self._persisted_watch_ids(LOG_DIR / "oz_external_liquidity_state.json", "specs", mapping=True)

        cancel_payloads: list[dict] = list(stale_child_cancels)
        cancel_payloads.extend(
            {"action": "CANCEL_MANUAL", "watch_id": wid}
            for wid in sorted(persisted_manual - desired_manual_ids)
        )
        cancel_payloads.extend(
            {"action": "CANCEL_GENERIC", "watch_id": wid}
            for wid in sorted(persisted_generic - desired_generic_ids)
        )
        cancel_payloads.extend(
            {"action": "CANCEL_SWEEP", "watch_id": wid}
            for wid in sorted(persisted_external - desired_external_ids)
        )

        # 옛것만 먼저 지운 뒤 현재 감시를 재전달합니다. 전체 초기화는 하지 않으므로
        # 살아 있는 외부유동성 터치/ATR 상태는 보존됩니다.
        seen_cancel: set[tuple[str, str]] = set()
        cancel_count = 0
        for payload in cancel_payloads:
            key = (str(payload.get("action") or ""), str(payload.get("watch_id") or ""))
            if not key[1] or key in seen_cancel:
                continue
            seen_cancel.add(key)
            self._push(payload)
            cancel_count += 1

        for payload in unique.values():
            self._push(payload)
        # SWEEP_WATCH는 전략 프로그램뿐 아니라 OZ의 외부유동성 Gate 등록에도 사용됩니다.
        for payload in desired_external.values():
            self._push(dict(payload))

        if logging.getLogger().isEnabledFor(logging.INFO):
            logging.info(
                "♻️ [Composer 시작 동기화] OZ | 옛 감시 %d건 정리 · 현재 감시 %d건 재전달 · 외부유동성 %d건 확인",
                cancel_count, len(unique), len(desired_external),
            )
        return cancel_count + len(unique) + len(desired_external)

    @staticmethod
    def _persisted_owned_oz_ids(path: Path) -> set[str]:
        """Cancel only KIM-owned watches; preserve unclassified legacy standalone watches."""
        try:
            raw = read_json(path)
            return {
                str(item["watch_id"]) for item in raw.get("watches", [])
                if isinstance(item, dict) and item.get("watch_id")
                and (item.get("watch_owner") == "KIM" or
                     (not item.get("watch_owner") and (item.get("chain_id") or item.get("source_spec_id"))))
            }
        except FileNotFoundError:
            return set()
        except Exception:
            logging.exception("[Composer] owned watch state read failed | %s", path)
            return set()

    # -------------------------------
    # fact matching
    # -------------------------------
    @staticmethod
    def _condition_direction_allowed(cond: ConditionSpec, direction: str) -> bool:
        return cond.direction in {"AUTO", direction}

    @staticmethod
    def _condition_eval_direction(spec: StrategySpec, cond: ConditionSpec, final_direction: str) -> str:
        """PRIVATE에서 최종 OZ 방향을 명시한 경우 조건 고유 방향과 최종 방향을 분리합니다.

        예: 상승 FVG가 조건이지만 사용자가 `매도 올존`을 명시하면
        FVG 조건은 BULL/LONG 사실을 그대로 검사하고 최종 OZ만 SHORT로 arm합니다.
        """
        if (
            spec.source == "PRIVATE"
            and spec.final_direction_explicit
            and cond.direction in {"LONG", "SHORT"}
        ):
            return str(cond.direction)
        return str(final_direction)

    @staticmethod
    def _trend_metric_compare(left: float, operator: str, right: float) -> bool:
        op = str(operator or "").upper()
        if op == "GT":
            return left > right
        if op == "GTE":
            return left >= right
        if op == "LT":
            return left < right
        if op == "LTE":
            return left <= right
        if op == "EQ":
            return math.isclose(left, right, rel_tol=1e-9, abs_tol=1e-12)
        if op == "NE":
            return not math.isclose(left, right, rel_tol=1e-9, abs_tol=1e-12)
        return False

    def _trend_metric_fact_fresh_locked(self, symbol: str, tf: str, metric: str) -> Optional[dict]:
        fact = self.trend_metric_facts.get((symbol, tf, metric))
        if not fact:
            return None
        try:
            stale_sec = float(self.config.get("TREND_METRIC_STALE_SEC", DEFAULT_TREND_METRIC_STALE_SEC))
        except (TypeError, ValueError):
            stale_sec = DEFAULT_TREND_METRIC_STALE_SEC
        received = float(fact.get("received_mono") or 0.0)
        if received <= 0 or time.monotonic() - received > max(1.0, stale_sec):
            return None
        try:
            value = float(fact.get("value"))
        except (TypeError, ValueError):
            return None
        if not math.isfinite(value):
            return None
        return fact

    def _condition_status_locked(self, spec: StrategySpec, cond: ConditionSpec, direction: str, health_batch=None) -> tuple[bool, str]:
        if not self._condition_source_usable(spec, cond, health_batch):
            return None, 'SUSPENDED:SOURCE_HEALTH'
        condition_direction = self._condition_eval_direction(spec, cond, direction)
        if not self._condition_direction_allowed(cond, condition_direction):
            return False, ""

        if cond.kind == "TREND":
            event = self.trend_facts.get((spec.symbol, cond.tf))
            if not event:
                return False, ""
            fact_dir = str(event.get("direction") or "").upper()
            if fact_dir != condition_direction:
                return False, ""
            token = f"TREND:{fact_dir}:{_event_time_token(event.get('bar_time'))}"
            return True, token

        if cond.kind == "WONBI":
            expected_side = cond.side or ("LOWER" if condition_direction == "LONG" else "UPPER")
            if expected_side == "LOWER" and condition_direction != "LONG":
                return False, ""
            if expected_side == "UPPER" and condition_direction != "SHORT":
                return False, ""
            event = self.wonbi_facts.get((spec.symbol, cond.tf, expected_side))
            if not event or not event.get("active"):
                return False, ""
            return True, f"WONBI:{expected_side}:{event.get('touch_id')}"

        if cond.kind == "PERCENTILE":
            expected_side = cond.side or ("LOWER" if condition_direction == "LONG" else "UPPER")
            if expected_side == "LOWER" and condition_direction != "LONG":
                return False, ""
            if expected_side == "UPPER" and condition_direction != "SHORT":
                return False, ""
            event = self.percentile_facts.get((spec.symbol, cond.tf, expected_side))
            if not event or not event.get("active"):
                return False, ""
            families = ",".join(event.get("families") or ())
            return True, f"OUT:{expected_side}:{families}:{event.get('touch_id')}"

        if cond.kind == "MA_STATE":
            key = (spec.symbol, cond.tf, cond.ma_family, cond.fast_period, cond.slow_period)
            fact = self.ma_state_facts.get(key)
            if not fact:
                return False, ""
            try:
                poll_sec = max(0.2, float(self.config.get("COMPOSER_POLL_SEC", DEFAULT_COMPOSER_POLL_SEC)))
            except (TypeError, ValueError):
                poll_sec = DEFAULT_COMPOSER_POLL_SEC
            try:
                stale_sec = float(self.config.get(
                    "MA_STATE_STALE_SEC", max(DEFAULT_MA_STATE_STALE_SEC, poll_sec * 3.0)
                ))
            except (TypeError, ValueError):
                stale_sec = max(DEFAULT_MA_STATE_STALE_SEC, poll_sec * 3.0)
            observed_at = float(fact.get("observed_at") or 0.0)
            if observed_at <= 0 or time.time() - observed_at > max(1.0, stale_sec):
                return False, ""
            relation = str(fact.get("relation") or "").upper()
            if relation != cond.side:
                return False, ""
            state_id = fact.get("state_id") or "-"
            return True, (
                f"MA_STATE:{cond.ma_family}{cond.fast_period}/{cond.slow_period}:"
                f"{relation}:{state_id}"
            )

        if cond.kind == "MA_PRICE_STATE":
            key = (spec.symbol, cond.tf, cond.ma_family, cond.slow_period)
            fact = self.ma_price_state_facts.get(key)
            if not fact:
                return False, ""
            try:
                poll_sec = max(0.2, float(self.config.get("COMPOSER_POLL_SEC", DEFAULT_COMPOSER_POLL_SEC)))
            except (TypeError, ValueError):
                poll_sec = DEFAULT_COMPOSER_POLL_SEC
            try:
                stale_sec = float(self.config.get(
                    "MA_STATE_STALE_SEC", max(DEFAULT_MA_STATE_STALE_SEC, poll_sec * 3.0)
                ))
            except (TypeError, ValueError):
                stale_sec = max(DEFAULT_MA_STATE_STALE_SEC, poll_sec * 3.0)
            observed_at = float(fact.get("observed_at") or 0.0)
            if observed_at <= 0 or time.time() - observed_at > max(1.0, stale_sec):
                return False, ""
            relation = str(fact.get("relation") or "").upper()
            if relation != cond.side:
                return False, ""
            state_id = fact.get("state_id") or "-"
            return True, (
                f"MA_PRICE_STATE:PRICE/{cond.ma_family}{cond.slow_period}:"
                f"{relation}:{state_id}"
            )

        if cond.kind == "MA_SLOPE_STATE":
            key = (spec.symbol, cond.tf, cond.ma_family, cond.slow_period)
            fact = self.ma_slope_state_facts.get(key)
            if not fact:
                return False, ""
            try:
                poll_sec = max(0.2, float(self.config.get("COMPOSER_POLL_SEC", DEFAULT_COMPOSER_POLL_SEC)))
            except (TypeError, ValueError):
                poll_sec = DEFAULT_COMPOSER_POLL_SEC
            try:
                stale_sec = float(self.config.get(
                    "MA_STATE_STALE_SEC", max(DEFAULT_MA_STATE_STALE_SEC, poll_sec * 3.0)
                ))
            except (TypeError, ValueError):
                stale_sec = max(DEFAULT_MA_STATE_STALE_SEC, poll_sec * 3.0)
            observed_at = float(fact.get("observed_at") or 0.0)
            if observed_at <= 0 or time.time() - observed_at > max(1.0, stale_sec):
                return False, ""
            relation = str(fact.get("relation") or "").upper()
            if relation != cond.side:
                return False, ""
            state_id = fact.get("state_id") or "-"
            return True, (
                f"MA_SLOPE_STATE:{cond.ma_family}{cond.slow_period}:"
                f"{relation}:CURRENT_VS_2BARS:{state_id}"
            )

        if cond.kind == "TREND_METRIC":
            left_fact = self._trend_metric_fact_fresh_locked(spec.symbol, cond.tf, cond.metric)
            gate_key = (spec.spec_id, str(direction), cond.label())
            if left_fact is None:
                state = self._trend_metric_gate_state.get(gate_key)
                if state is not None:
                    state["active"] = False
                return False, ""
            try:
                left = float(left_fact.get("value"))
            except (TypeError, ValueError):
                return False, ""

            if cond.metric_rhs:
                right_fact = self._trend_metric_fact_fresh_locked(spec.symbol, cond.tf, cond.metric_rhs)
                if right_fact is None:
                    state = self._trend_metric_gate_state.get(gate_key)
                    if state is not None:
                        state["active"] = False
                    return False, ""
                try:
                    right = float(right_fact.get("value"))
                except (TypeError, ValueError):
                    return False, ""
            else:
                right = float(cond.metric_value)

            passed = self._trend_metric_compare(left, cond.metric_operator, right)
            state = self._trend_metric_gate_state.get(gate_key)
            if not passed:
                if state is not None:
                    state["active"] = False
                return False, ""

            if state is None or not state.get("active"):
                self._trend_metric_gate_seq += 1
                state = {"active": True, "episode": self._trend_metric_gate_seq}
                self._trend_metric_gate_state[gate_key] = state
            episode = int(state.get("episode") or 0)
            rhs_label = cond.metric_rhs or f"{right:g}"
            return True, (
                f"TREND_METRIC:{cond.metric}:{cond.metric_operator}:{rhs_label}:EP{episode}"
            )

        if cond.kind == "FVG":
            expected_side = cond.side or ("BULL" if condition_direction == "LONG" else "BEAR")
            if expected_side == "BULL" and condition_direction != "LONG":
                return False, ""
            if expected_side == "BEAR" and condition_direction != "SHORT":
                return False, ""
            matches = [
                ev for (symbol, tf, _), ev in self.fvg_touches.items()
                if symbol == spec.symbol and tf == cond.tf
                and str(ev.get("fvg_side") or "").upper() == expected_side
            ]
            if not matches:
                return False, ""
            tokens = sorted(
                f"{ev.get('zone_id')}:{_event_time_token(ev.get('event_time'))}" for ev in matches
            )
            return True, "FVG:" + ",".join(tokens)

        if cond.kind == "SWEEP":
            sub = self._sweep_subscription_payload(spec, cond)
            wid = sub["watch_id"]
            matches = [
                ev for (symbol, tf, watch_id, _), ev in self.sweep_touches.items()
                if symbol == spec.symbol and tf == cond.tf and watch_id == wid
                and str(ev.get("direction") or "").upper() == condition_direction
            ]
            if not matches:
                return False, ""
            tokens = sorted(
                f"{ev.get('level_id')}:{_event_time_token(ev.get('touch_time') or ev.get('event_time'))}" for ev in matches
            )
            return True, "SWEEP:" + ",".join(tokens)

        return False, ""

    def _evaluate_spec_direction_locked(self, spec: StrategySpec, direction: str, health_batch=None) -> Optional[str]:
        states = [self._condition_status_locked(spec, cond, direction, health_batch) for cond in spec.conditions]
        if spec.combination == "ALL":
            passed = all(ok for ok, _ in states)
        else:
            passed = any(ok for ok, _ in states)
        if logging.getLogger().isEnabledFor(logging.INFO):
            if logging.getLogger().isEnabledFor(logging.INFO):
                logging.info("조건 판정 · %s %s · %s · 통과=%s · 조건별=%s",spec.spec_id,spec.symbol,direction,passed,
                             [(cond.kind,cond.tf,ok,token) for cond,(ok,token) in zip(spec.conditions,states)],
                             extra={'trace_module':spec.spec_id})
        if not passed:
            return None
        active_tokens = [token for ok, token in states if ok and token]
        raw = f"{spec.spec_id}|{direction}|" + "|".join(active_tokens)
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()

    def _selected_sweep_touch_locked(self, spec: StrategySpec, direction: str) -> Optional[dict]:
        """현재 실제 터치된 외부유동성 중 방향별 최외곽 레벨 하나를 고릅니다."""
        direction = str(direction or "").upper()
        if direction not in {"LONG", "SHORT"}:
            return None

        candidates: dict[tuple[str, str], dict] = {}
        for cond in spec.conditions:
            condition_direction = self._condition_eval_direction(spec, cond, direction)
            if cond.kind != "SWEEP" or not self._condition_direction_allowed(cond, condition_direction):
                continue
            sub = self._sweep_subscription_payload(spec, cond)
            wid = str(sub.get("watch_id") or "")
            for (symbol, tf, watch_id, level_id), ev in self.sweep_touches.items():
                if symbol != spec.symbol or tf != cond.tf or watch_id != wid:
                    continue
                if str(ev.get("direction") or "").upper() != condition_direction:
                    continue
                try:
                    level_price = float(ev.get("level_price"))
                except (TypeError, ValueError):
                    continue
                if not math.isfinite(level_price):
                    continue
                item = dict(ev)
                item["_external_watch_id"] = wid
                item["_external_source_tf"] = cond.tf
                item["_level_price"] = level_price
                candidates[(wid, str(level_id))] = item

        if not candidates:
            return None

        values = list(candidates.values())
        if direction == "LONG":
            # 실제 터치된 하단 유동성 중 가장 낮은 가격을 사용합니다.
            return min(values, key=lambda x: (x["_level_price"], -float(x.get("event_time") or x.get("touch_time") or 0.0)))
        # 실제 터치된 상단 유동성 중 가장 높은 가격을 사용합니다.
        return max(values, key=lambda x: (x["_level_price"], float(x.get("event_time") or x.get("touch_time") or 0.0)))

    @staticmethod
    def _chain_trading_time(spec) -> list:
        """A configured chain's trading time: its final time if set, otherwise its setup time."""
        return list(spec.final_time_filters or spec.time_filters)

    def _trading_time_blocked_locked(self, watch_ids) -> set[str]:
        """Composer-armed OZ watches whose every source strategy is outside its trading time now."""
        blocked = set()
        for wid in watch_ids:
            payload = self._active_children.get(wid) or self._config_chain_active.get(wid)
            times = payload.get("trading_times") if isinstance(payload, dict) else None
            if isinstance(times, dict) and times and not any(self._time_policy.allows(v) for v in times.values()):
                blocked.add(wid)
        return blocked

    def _arm_oz_locked(self, spec: StrategySpec, direction: str, signature: str) -> None:
        last_key = (spec.spec_id, direction)
        if self._last_signatures.get(last_key) == signature:
            return
        # Trading time is checked when the final OZ alert arrives, not when the setup arms it.

        child_id = stable_id("OZARM", spec.spec_id, direction, signature, length=20)
        request_chat_id = spec.owner_chat_id if spec.destination == "PRIVATE" else None
        selected_sweep = self._selected_sweep_touch_locked(spec, direction)
        external_watch_id = None
        external_source_tf = None
        external_level_id = None
        external_level_price = None
        if selected_sweep is not None:
            external_watch_id = str(selected_sweep.get("_external_watch_id") or "") or None
            external_source_tf = str(selected_sweep.get("_external_source_tf") or "").lower() or None
            external_level_id = str(selected_sweep.get("level_id") or "") or None
            external_level_price = selected_sweep.get("_level_price")

        payload = {
            "action": "MANUAL_WATCH",
            "watch_id": child_id,
            "timeframes": list(spec.oz_tfs),
            "symbol": spec.symbol,
            "direction": direction,
            # Composer가 final alert 후 watch_id로 직접 취소합니다.
            # monitor_OZ의 legacy one-shot replacement가 서로 다른 전략을 지우지 않도록 persistent로 등록합니다.
            "persistent": True,
            "request_chat_id": request_chat_id,
            "validation_mode": spec.validation_mode,
            "trigger_mode": spec.trigger_mode,
            "source_spec_id": spec.spec_id,
            "source_spec_ids": [spec.spec_id],
            "source_name": spec.name,
            # Each strategy's own trading time, applied at the final alert.
            "trading_times": {spec.spec_id: list(spec.time_filters)},
        }
        if external_watch_id is not None:
            payload.update({
                "external_watch_id": external_watch_id,
                "external_source_tf": external_source_tf,
                "external_liquidity_required": True,
                "external_source_kind": "SWEEP",
                # 아래 두 값은 판정용이 아니라 선택 결과 추적/로그용입니다.
                "external_level_id": external_level_id,
                "external_level_price": external_level_price,
            })

        profile = (
            request_chat_id or "OFFICIAL", spec.symbol, direction, tuple(spec.oz_tfs),
            spec.validation_mode, spec.trigger_mode, external_watch_id, external_source_tf,
        )
        for active in self._active_children.values():
            active_profile = (
                str(active.get("request_chat_id") or "OFFICIAL"), str(active.get("symbol") or ""),
                str(active.get("direction") or ""), tuple(active.get("timeframes") or ()),
                str(active.get("validation_mode") or ""), str(active.get("trigger_mode") or ""),
                str(active.get("external_watch_id") or "") or None,
                str(active.get("external_source_tf") or "").lower() or None,
            )
            if active_profile == profile:
                # 기존 setup 병합 동작은 유지하되, 어떤 SPECIAL 공식 전략들이 이 child를
                # 공유하는지는 보존하여 최종 알림 템플릿을 spec_id로 찾을 수 있게 합니다.
                source_ids = active.setdefault("source_spec_ids", [])
                times = active.get("trading_times")
                if isinstance(times, dict) and spec.spec_id not in times:
                    times[spec.spec_id] = list(spec.time_filters)
                    self._save_active_children_state_locked()
                if spec.spec_id not in source_ids:
                    source_ids.append(spec.spec_id)
                    self._track_watch_payload(active)
                    self._save_active_children_state_locked()
                self._last_signatures[last_key] = signature
                self._remember_signature(spec.spec_id, direction, signature)
                if logging.getLogger().isEnabledFor(logging.INFO):
                    logging.info("ℹ️ [Composer→OZ] 동일 OZ profile 이미 arm - setup 병합 | %s %s %s", spec.name, spec.symbol, direction)
                return
        self._push(payload)
        self._active_children[child_id] = payload
        self._save_active_children_state_locked()
        self._last_signatures[last_key] = signature
        self._remember_signature(spec.spec_id, direction, signature)
        if logging.getLogger().isEnabledFor(logging.INFO):
            logging.info(
                "🎯 [Composer→OZ] %s | %s | %s | TF=%s | validation=%s trigger=%s | child=%s | 외부유동성=%s %s",
                spec.name, spec.symbol, direction, ",".join(spec.oz_tfs),
                spec.validation_mode, spec.trigger_mode, child_id,
                external_level_id or "없음",
                f"@ {float(external_level_price):.6f}" if external_level_price is not None else "",
            )

        # 개인 1회성 전략은 setup을 한 번 OZ에 넘긴 시점에서 Composer 등록을 종료합니다.
        if spec.source == "PRIVATE" and not spec.persistent:
            self.manual_specs.pop(spec.spec_id, None)
            self._save_private_state_locked()
            self._subscription_dirty = True

    def _notify_spec_locked(self, spec: StrategySpec, direction: str, signature: str) -> None:
        last_key = (spec.spec_id, direction)
        if self._last_signatures.get(last_key) == signature:
            return
        if not self._time_policy.allows(spec.time_filters):
            # This completion is the alert's trigger; outside trading time it is dropped,
            # not sent later. A one-shot strategy stays registered for its next trigger.
            self._last_signatures[last_key] = signature
            self._remember_signature(spec.spec_id, direction, signature)
            return

        side_text = "매수" if direction == "LONG" else "매도"
        condition_text = " + ".join(c.label() for c in spec.conditions)
        message = f"🔔 {spec.name} 조건 성립\n{spec.symbol} · {side_text}\n{condition_text}"
        from event_signal_context import market_signal_context
        kernel = getattr(getattr(self, 'event_services', None), 'kernel', None)
        timeframes = {cond.tf for cond in spec.conditions}
        for cond in spec.conditions:
            _scope, binding = self._condition_source_binding(spec, cond)
            timeframes.update(binding.get('sources') or {})
        context = self._delivery_context
        previous = getattr(context, 'oz_event', None)
        context.oz_event = market_signal_context(getattr(kernel, 'board', None),
            spec.symbol, direction, kernel.timestamp / 1000 if kernel is not None else None,
            timeframes)
        context.oz_event['signal_strategy'] = 'WATCH' if spec.source == 'PRIVATE' else spec.spec_id
        try:
            delivered = self.send_telegram(message, spec.owner_chat_id if spec.destination == "PRIVATE" else None,
                                           watch_id=spec.spec_id)
        finally:
            context.oz_event = previous
        if not delivered:
            return

        self._last_signatures[last_key] = signature
        self._remember_signature(spec.spec_id, direction, signature)
        if logging.getLogger().isEnabledFor(logging.INFO):
            logging.info("🔔 [Composer 조건알림] %s | %s | %s", spec.name, spec.symbol, direction)

        # 일반 '알려줘'는 1회성입니다. 계속/지속/항상을 붙인 경우에만 다음 새 조건 성립도 기다립니다.
        if spec.source == "PRIVATE" and not spec.persistent:
            self.manual_specs.pop(spec.spec_id, None)
            self._save_private_state_locked()
            self._subscription_dirty = True

    def _evaluate_symbol_locked(self, symbol: str) -> None:
        specs = [s for s in self._all_specs_locked() if s.symbol == symbol]
        self._evaluate_specs_locked(symbol, specs)

    def _evaluate_fact_dependents_locked(self, symbol, tf, family):
        specs = (*self.official_specs.affected(symbol, tf, family),
                 *self.manual_specs.affected(symbol, tf, family))
        self._evaluate_specs_locked(symbol, specs)

    def _evaluate_specs_locked(self, symbol, specs):
        if not specs:
            return
        sources = set()
        for spec in specs:
            for cond in spec.conditions:
                _, binding = self._condition_source_binding(spec, cond)
                sources.update(binding.get('sources') or {})
        # One fresh health snapshot per evaluation, never a cross-event/time-based cache.
        checked_at = time.monotonic()
        health_batch = (self.staff.health(symbol, sorted(sources)) if sources else {}, checked_at)
        for spec in specs:
            directions = (spec.final_direction,) if spec.final_direction in {"LONG", "SHORT"} else ("LONG", "SHORT")
            for direction in directions:
                signature = self._evaluate_spec_direction_locked(spec, direction, health_batch)
                if not signature:
                    continue
                if spec.final_action == "NOTIFY":
                    self._notify_spec_locked(spec, direction, signature)
                else:
                    self._arm_oz_locked(spec, direction, signature)

    # -------------------------------
    # facts from TREND / FVG / SWEEP / OZ
    # -------------------------------
    def _handle_fact_event(self, event: dict) -> dict:
        revision = event.get('fact_revision')
        scope = fact_scope(event)
        if revision:
            previous = self._fact_revisions.get(scope)
            if previous and tuple(revision) <= tuple(previous['revision']):
                return {'ok': True, 'delivered': False, 'stale': True}
        if event.get('kind') == 'FACT_SNAPSHOT':
            return self._reconcile_facts(event)
        if event.get('source_health'):
            self._source_bindings[scope] = event['source_health']
        result = self._apply_fact_event(event)
        if revision and result.get('ok'):
            self._fact_revisions.put(scope, {'revision': revision})
        return result

    def _reconcile_facts(self, event: dict) -> dict:
        family, symbol, tf = event.get('strategy'), event.get('symbol'), normalize_tf(event.get('source_tf'))
        facts = event.get('facts')
        if event.get('complete') is not True or not isinstance(facts, list) or not symbol or not tf:
            return {'ok': False, 'error': 'incomplete_snapshot'}
        if any(not isinstance(f, dict) or f.get('symbol') != symbol or f.get('source_tf') != tf for f in facts):
            return {'ok': False, 'error': 'snapshot_scope_mismatch'}
        with self._lock:
            if family == 'TREND':
                if len(facts) != 1 or facts[0].get('trend') not in {'UP','DOWN','NEUTRAL'}:
                    return {'ok': False, 'error': 'invalid_trend_snapshot'}
                # A failed durable write must leave the previous facts intact.
                self._fact_revisions.put(fact_scope(event), {'revision': event['fact_revision']})
                self.trend_facts[(symbol,tf)] = dict(facts[0])
            elif family == 'FVG':
                if any(not f.get('zone_id') for f in facts): return {'ok':False,'error':'invalid_zone'}
                # A failed durable write must leave the previous facts intact.
                self._fact_revisions.put(fact_scope(event), {'revision': event['fact_revision']})
                for store in (self.fvg_zones, self.fvg_touches):
                    for key in list(store):
                        if key[:2] == (symbol,tf): store.pop(key)
                for fact in facts:
                    key = (symbol,tf,fact['zone_id'])
                    self.fvg_zones[key] = dict(fact)
                    if fact.get('touched_now'): self.fvg_touches[key] = dict(fact)
            elif family == 'SWEEP':
                wid = event.get('watch_id')
                if not wid or any(f.get('watch_id') != wid or not f.get('level_id') for f in facts):
                    return {'ok':False,'error':'invalid_sweep_scope'}
                # A failed durable write must leave the previous facts intact.
                self._fact_revisions.put(fact_scope(event), {'revision': event['fact_revision']})
                for key in list(self.sweep_touches):
                    if key[:3] == (symbol,tf,wid): self.sweep_touches.pop(key)
                for fact in facts: self.sweep_touches[(symbol,tf,wid,fact['level_id'])] = dict(fact)
            else:
                return {'ok':False,'error':'invalid_snapshot_family'}
            self._source_bindings[fact_scope(event)] = event.get('source_health') or {}
            self._evaluate_fact_dependents_locked(symbol, tf, family)
        return {'ok': True, 'delivered':False,'reconciled':True}

    def _remember_signature(self, spec_id, direction, signature):
        self._signature_records.put(identity(spec_id, direction),
                                    {'spec_id':spec_id, 'direction':direction,'signature':signature})

    def _condition_source_binding(self, spec, cond):
        family = 'TREND' if cond.kind == 'TREND_METRIC' else 'MA' if cond.kind.startswith('MA_') else cond.kind
        wid = self._sweep_subscription_payload(spec, cond)['watch_id'] if family == 'SWEEP' else None
        scope = _cached_condition_scope(family, spec.symbol, cond.tf, wid)
        binding = self._source_bindings.get(scope) or {}
        return scope, binding

    def _condition_source_usable(self, spec, cond, health_batch=None):
        scope, binding = self._condition_source_binding(spec, cond)
        sources = binding.get('sources') or {}
        if not sources or any(not epoch for epoch in sources.values()):
            self.source_status[scope] = 'UNKNOWN'
            return False
        elapsed = 0.0
        if health_batch is None:
            health = self.staff.health(spec.symbol, sources)
        else:
            health, checked_at = health_batch
            elapsed = max(0.0, time.monotonic() - checked_at)
            # Older STAFFs do not describe their freshness window: ask them directly.
            if any(health.get(tf, {}).get('status') == 'FRESH' and
                   ('age_seconds' not in health[tf] or 'stale_seconds' not in health[tf]) for tf in sources):
                health = self.staff.health(spec.symbol, sources)
                elapsed = 0.0
        usable = all(health.get(tf, {}).get('status') == 'FRESH'
                     and health[tf].get('source_epoch') == epoch
                     and (health[tf].get('age_seconds') is None or
                          float(health[tf]['age_seconds']) + elapsed <= float(health[tf]['stale_seconds']))
                     and all(health[tf].get('indicators', {}).get(ind, False) for ind in binding.get('indicators', []))
                     for tf, epoch in sources.items())
        self.source_status[scope] = 'READY' if usable else 'SUSPENDED'
        return usable

    def _observe_local_source(self, family, symbol, tf, df, indicators):
        self._source_bindings[fact_scope({'strategy':family,'symbol':symbol,'source_tf':tf})] = source_health({tf:df}, indicators)

    def _apply_fact_event(self, event: dict) -> dict:
        kind = str(event.get("kind") or "").upper()
        symbol = str(event.get("symbol") or "").strip()
        tf = normalize_tf(event.get("source_tf") or event.get("tf"))
        changed = False
        direct_fvg_watches: list[FVGCreatedWatchSpec] = []
        chain_events: list[dict] = []

        with self._lock:
            if kind == "TREND_STATE" and symbol and tf:
                self.trend_facts[(symbol, tf)] = dict(event)
                changed = True

            elif kind == "TREND_METRIC_STATE" and symbol and tf:
                metrics = event.get("metrics") if isinstance(event.get("metrics"), dict) else {}
                received_mono = time.monotonic()
                bar_time = event.get("bar_time")
                for metric, raw_value in metrics.items():
                    metric_name = str(metric or "").strip().lower()
                    if metric_name not in TREND_METRIC_FIELDS:
                        continue
                    try:
                        value = float(raw_value)
                    except (TypeError, ValueError):
                        continue
                    if not math.isfinite(value):
                        continue
                    self.trend_metric_facts[(symbol, tf, metric_name)] = {
                        "value": value,
                        "bar_time": bar_time,
                        "received_mono": received_mono,
                    }
                    changed = True

            elif kind == "FVG_CREATED" and symbol and tf:
                zone_id = str(event.get("zone_id") or "").strip()
                direction = str(event.get("direction") or "").upper()
                if direction not in {"LONG", "SHORT"}:
                    side = str(event.get("fvg_side") or "").upper()
                    direction = "LONG" if side == "BULL" else "SHORT" if side == "BEAR" else ""
                if zone_id:
                    self.fvg_zones[(symbol, tf, zone_id)] = dict(event)
                    changed = True
                raw_fvg_event_ts = self._local_chain_epoch(event.get("event_time") or event.get("fvg_time"))
                filter_fvg_event_ts = (
                    float(raw_fvg_event_ts) + float(tf_seconds(tf))
                    if raw_fvg_event_ts is not None else time.time()
                )

                # 개인 "신규 FVG 생성" Watch: monitor_OZ 로컬 FVG 계산 없이 직접 처리합니다.
                for watch in self.fvg_created_watches.values():
                    if watch.symbol != symbol or tf not in watch.timeframes:
                        continue
                    if watch.direction is not None and watch.direction != direction:
                        continue
                    direct_fvg_watches.append(watch)

                # 개인 시간연쇄/순서무관의 FVG_NEW 조건.
                for chain in self.timed_chains.values():
                    if not chain.enabled or chain.symbol != symbol:
                        continue

                    # 후보가 살아 있는 동안 arm된 CANCEL_ON FVG는 정상 stage와 별도 음수 stage로 전달합니다.
                    for raw_idx, cancel_watch_id in tuple(chain.invalidation_watch_ids.items()):
                        try:
                            cancel_idx = int(raw_idx)
                        except (TypeError, ValueError):
                            continue
                        if cancel_idx < 0 or cancel_idx >= len(chain.invalidation_triggers):
                            continue
                        cancel_trig = chain.invalidation_triggers[cancel_idx]
                        if cancel_trig.watch_type != "FVG_NEW" or cancel_trig.tf != tf:
                            continue
                        if cancel_trig.direction is not None and cancel_trig.direction != direction:
                            continue
                        chain_events.append({
                            "kind": "GENERIC_TRIGGER", "watch_id": cancel_watch_id,
                            "chain_id": chain.chain_id,
                            "chain_stage": self.watch_orchestrator._invalidation_stage(cancel_idx),
                            "watch_type": "FVG_NEW", "symbol": symbol, "source_tf": tf,
                            "direction": direction, "zone_id": zone_id,
                            "event_id": zone_id, "event_time": event.get("event_time") or event.get("fvg_time"),
                            "message": f"↩️ [CANCEL_ON FVG] {symbol} · {tf}",
                        })

                    if chain.active_child_id and chain.order_mode != "FILTER":
                        continue
                    if chain.order_mode in {"UNORDERED", "FILTER"}:
                        for idx, trig in enumerate(chain.triggers):
                            if trig.watch_type != "FVG_NEW" or trig.tf != tf:
                                continue
                            if trig.direction is not None and trig.direction != direction:
                                continue
                            key = str(idx)
                            watch_id = chain.current_watch_ids.get(key) or stable_id(
                                "CHAINFILTER" if chain.order_mode == "FILTER" else "CHAINUNORD",
                                chain.chain_id, idx, length=20
                            )
                            chain.current_watch_ids[key] = watch_id
                            chain_events.append({
                                "kind": "GENERIC_TRIGGER", "watch_id": watch_id,
                                "chain_id": chain.chain_id, "chain_stage": idx,
                                "watch_type": "FVG_NEW", "symbol": symbol, "source_tf": tf,
                                "direction": direction, "zone_id": zone_id,
                                "event_id": zone_id,
                                "event_time": (
                                    filter_fvg_event_ts if chain.order_mode == "FILTER"
                                    else event.get("event_time") or event.get("fvg_time")
                                ),
                                "message": (
                                    f"🟢 [상승 FVG 생성]\n{symbol} · {tf}"
                                    if direction == "LONG" else f"🔴 [하락 FVG 생성]\n{symbol} · {tf}"
                                ),
                            })
                        continue

                    if chain.stage >= len(chain.triggers):
                        continue
                    trig = chain.triggers[chain.stage]
                    if trig.watch_type != "FVG_NEW" or trig.tf != tf:
                        continue
                    if trig.direction is not None and trig.direction != direction:
                        continue
                    watch_id = chain.current_watch_id or stable_id("CHAINTRG", chain.chain_id, chain.stage, length=20)
                    chain.current_watch_id = watch_id
                    chain_events.append({
                        "kind": "GENERIC_TRIGGER", "watch_id": watch_id,
                        "chain_id": chain.chain_id, "chain_stage": chain.stage,
                        "watch_type": "FVG_NEW", "symbol": symbol, "source_tf": tf,
                        "direction": direction, "zone_id": zone_id,
                        "event_id": zone_id, "event_time": event.get("event_time") or event.get("fvg_time"),
                        "message": (
                            f"🟢 [상승 FVG 생성]\n{symbol} · {tf}"
                            if direction == "LONG" else f"🔴 [하락 FVG 생성]\n{symbol} · {tf}"
                        ),
                    })

                # SPECIAL TIMED_CHAIN의 FVG 축. Cross는 기존 monitor_OZ, FVG는 Composer가 직접 받습니다.
                for chain_spec in self.official_chain_specs.values():
                    if not chain_spec.enabled or chain_spec.symbol != symbol or tf not in chain_spec.fvg_tfs:
                        continue
                    watch_id = stable_id("CFGCHAINTRG", chain_spec.spec_id, "FVG", tf, length=20)
                    chain_events.append({
                        "kind": "GENERIC_TRIGGER", "watch_id": watch_id,
                        "chain_id": f"CFGCHAIN:{chain_spec.spec_id}",
                        "chain_stage": list(chain_spec.fvg_tfs).index(tf) + 1,
                        "watch_type": "FVG_NEW", "symbol": symbol, "source_tf": tf,
                        "direction": direction, "zone_id": zone_id,
                        "event_id": zone_id, "event_time": event.get("event_time") or event.get("fvg_time"),
                    })

            elif kind == "FVG_TOUCH" and symbol and tf:
                zone_id = str(event.get("zone_id") or "").strip()
                if zone_id:
                    key = (symbol, tf, zone_id)
                    zone = dict(self.fvg_zones.get(key) or {})
                    zone.update(event)
                    zone["touched_now"] = True
                    self.fvg_zones[key] = zone
                    self.fvg_touches[key] = dict(event)
                    changed = True

            elif kind == "FVG_TOUCH_END" and symbol and tf:
                zone_id = str(event.get("zone_id") or "").strip()
                if zone_id:
                    key = (symbol, tf, zone_id)
                    self.fvg_touches.pop(key, None)
                    if key in self.fvg_zones:
                        self.fvg_zones[key]["touched_now"] = False
                    changed = True

            elif kind in {"FVG_FILLED", "FVG_EXPIRED"} and symbol and tf:
                zone_id = str(event.get("zone_id") or "").strip()
                if zone_id:
                    key = (symbol, tf, zone_id)
                    self.fvg_touches.pop(key, None)
                    self.fvg_zones.pop(key, None)
                    changed = True

            elif kind == "SWEEP_TOUCH" and symbol and tf:
                watch_id = str(event.get("watch_id") or "").strip()
                level_id = str(event.get("level_id") or "").strip()
                if watch_id and level_id:
                    self.sweep_touches[(symbol, tf, watch_id, level_id)] = dict(event)
                    changed = True

            elif kind == "SWEEP_INVALIDATED" and symbol and tf:
                watch_id = str(event.get("watch_id") or "").strip()
                level_id = str(event.get("level_id") or "").strip()
                if watch_id and level_id:
                    self.sweep_touches.pop((symbol, tf, watch_id, level_id), None)
                    changed = True

            if changed:
                self._evaluate_symbol_locked(symbol)

        # 공식 시간연쇄도 fact-store 갱신과 분리된 공용 event boundary에서 진행합니다.
        # FVG fact 수신 분기는 전략 단계/OZ arm을 직접 알지 않습니다.
        self._dispatch_official_timed_chain_event(event)

        removed_watch = False
        followup_oz_payloads: list[dict] = []
        for watch in direct_fvg_watches:
            side = str(event.get("direction") or "").upper()
            if side not in {"LONG", "SHORT"}:
                fvg_side = str(event.get("fvg_side") or "").upper()
                side = "LONG" if fvg_side == "BULL" else "SHORT"

            consumed = False
            if watch.final_action == "OZ":
                zone_id = str(event.get("zone_id") or event.get("fvg_time") or time.time_ns())
                child_id = stable_id("OZARM:FVGCREATED", watch.watch_id, zone_id, length=20)
                followup_oz_payloads.append({
                    "action": "MANUAL_WATCH", "watch_id": child_id,
                    "timeframes": list(watch.oz_tfs), "symbol": watch.symbol,
                    "direction": watch.oz_direction, "persistent": False,
                    "request_chat_id": watch.request_chat_id,
                    "validation_mode": watch.validation_mode, "trigger_mode": watch.trigger_mode,
                    "source_name": "FVG 생성→OZ", "source_fvg_zone_id": zone_id,
                    "reply_parent_watch_id": watch.watch_id,
                })
                oz_side = "하단 " if watch.oz_direction == "LONG" else "상단 " if watch.oz_direction == "SHORT" else ""
                oz_label = "·".join(WATCH_TF_MAP.get(x, x) for x in watch.oz_tfs)
                self.send_telegram(
                    f"✅ {WATCH_TF_MAP.get(tf, tf)} FVG 생성\n{oz_label} {oz_side}올존 감시 시작",
                    watch.request_chat_id, watch_id=watch.watch_id, signal_context={},
                )
                consumed = True
            else:
                message = (
                    f"🟢 [상승 FVG 생성]\n{symbol} · {tf}"
                    if side == "LONG" else f"🔴 [하락 FVG 생성]\n{symbol} · {tf}"
                )
                from event_signal_context import market_signal_context
                kernel = getattr(getattr(self, 'event_services', None), 'kernel', None)
                context = market_signal_context(getattr(kernel, 'board', None), symbol, side,
                    event.get('event_time', kernel.timestamp / 1000 if kernel is not None else None), (tf,))
                context['signal_strategy'] = 'WATCH'
                consumed = self.send_telegram(message, watch.request_chat_id,
                    watch_id=watch.watch_id, signal_context=context)

            if consumed and not watch.persistent:
                with self._lock:
                    self.fvg_created_watches.pop(watch.watch_id, None)
                    self._save_fvg_created_watch_state_locked()
                    self._subscription_dirty = True
                removed_watch = True

        for payload in followup_oz_payloads:
            self._push(payload)

        for chain_event in chain_events:
            self._handle_generic_trigger(chain_event)

        return {
            "ok": True, "delivered": False, "composed": changed,
            "fvg_created_watches_fired": len(direct_fvg_watches),
            "fvg_chain_events": len(chain_events), "subscription_changed": removed_watch,
        }

    @staticmethod
    def _trend_query_result_text(event: dict, symbol: str, tf: str) -> str:
        """추세(경량 Fact) 조회와 추세점수(전체 지표, 요청 시 계산) 조회 결과 문구."""
        metrics = event.get("metrics") if isinstance(event.get("metrics"), dict) else {}
        score_query = str(event.get("purpose") or "").upper() == "TREND_SCORE_QUERY" or bool(
            set(metrics) & {"trend_score", "long_score", "short_score"})
        if not event.get("ok"):
            return f"❌ {symbol} {tf} {'추세점수' if score_query else '추세'} 조회 실패"

        def number(value, fmt):
            try:
                x = float(value)
            except (TypeError, ValueError):
                return "-"
            return format(x, fmt) if math.isfinite(x) else "-"

        if score_query:
            return (
                f"📊 {symbol} {tf} 추세점수 · {number(metrics.get('trend_score'), '.1f')}점\n"
                f"LONG {number(metrics.get('long_score'), '.1f')} / SHORT {number(metrics.get('short_score'), '.1f')}"
                f" · 전체 지표 기준"
            )
        return (
            f"📈 {symbol} {tf} 추세 · {event.get('trend')}\n"
            f"SMA20(시가) 기울기 {number(event.get('sma20_slope'), '+.6g')} / "
            f"HMA50 기울기 {number(event.get('hma50_slope'), '+.6g')}"
        )

    def _handle_query_result(self, event: dict) -> dict:
        kind = str(event.get("kind") or "").upper()
        target = str(event.get("request_chat_id") or "").strip() or self.chat_id
        symbol = str(event.get("symbol") or "").strip()
        tf = str(event.get("source_tf") or "").strip()

        special_result = self._dispatch_special_watch_event(event)
        if special_result is not None:
            return special_result

        if kind == "TREND_QUERY_RESULT":
            text = self._trend_query_result_text(event, symbol, tf)
            delivered = self.send_telegram(text, target)
            return {"ok": delivered, "delivered": delivered}

        if kind == "FVG_QUERY_RESULT":
            if not event.get("ok"):
                text = f"❌ {symbol} {tf} FVG 조회 실패"
            else:
                zones = [x for x in (event.get("zones") or []) if isinstance(x, dict)]
                if not zones:
                    text = f"📦 {symbol} {tf} FVG · 현재 추적 중인 유효 FVG 없음"
                else:
                    rows = [f"📦 {symbol} {tf} FVG · 유효 {len(zones)}개"]
                    for z in zones:
                        side = str(z.get("fvg_side") or "-")
                        try:
                            bot = f"{float(z.get('zone_bot')):.6f}"
                            top = f"{float(z.get('zone_top')):.6f}"
                        except (TypeError, ValueError):
                            bot, top = "-", "-"
                        touched = " · 현재터치" if z.get("touched_now") else ""
                        age = z.get("age_bars")
                        age_text = f" · {age}봉 전" if age is not None else ""
                        rows.append(f"• {side} {bot} ~ {top}{age_text}{touched}")
                    text = "\n".join(rows)
            delivered = self.send_telegram(text, target)
            return {"ok": delivered, "delivered": delivered}

        if kind == "SWEEP_QUERY_RESULT":
            if not event.get("ok"):
                text = f"❌ {symbol} {tf} 외부유동성 조회 실패"
            else:
                levels = [x for x in (event.get("levels") or []) if isinstance(x, dict)]
                if not levels:
                    text = f"🌐 {symbol} {tf} 외부유동성 · 현재 레벨 없음"
                else:
                    rows = [f"🌐 {symbol} {tf} 외부유동성 · {len(levels)}개"]
                    for lv in levels:
                        name = str(lv.get("level_name") or lv.get("level_code") or "-")
                        direction = str(lv.get("direction") or "-")
                        try:
                            price = f"{float(lv.get('level_price')):.6f}"
                        except (TypeError, ValueError):
                            price = "-"
                        state = " · 터치" if lv.get("touched") else ""
                        if lv.get("consumed"):
                            state += " · 소진"
                        rows.append(f"• {name} · {direction} · {price}{state}")
                    text = "\n".join(rows)
            delivered = self.send_telegram(text, target)
            return {"ok": delivered, "delivered": delivered}

        return {"ok": True, "delivered": False}

    def _handle_oz_event(self, event: dict) -> dict:
        special_result = self._dispatch_special_oz_event(event)
        if special_result is not None:
            return special_result
        if str(event.get('kind', '')).upper() == 'FINAL_ALERT':
            event, expired = self._filter_config_chain_deadline_event(event)
            if expired:
                return {'ok':True,'delivered':True,'filtered':True,'expired':True}
            event, expired = self.watch_orchestrator.filter_deadline_event(event)
            if expired:
                return {'ok':True,'delivered':True,'filtered':True,'expired':True}
        return self._handle_oz_event_core(event)

    def _handle_oz_event_core(self, event: dict) -> dict:
        kind = str(event.get("kind") or "FINAL_ALERT").upper()
        watch_id = str(event.get("watch_id") or "").strip()

        # OZ 자체가 시간연쇄의 선행 단계인 경우 FINAL_ALERT를 내부 단계 이벤트로 소비합니다.
        # 같은 alert 묶음에 일반 Watch가 함께 있으면 그 일반 알림은 그대로 전달합니다.
        oz_stage = {"handled": False, "all_internal": False}
        if kind == "FINAL_ALERT":
            oz_stage = self.watch_orchestrator.handle_oz_stage_event(event)
            if oz_stage.get("handled") and oz_stage.get("all_internal"):
                return {
                    "ok": True, "delivered": True, "suppressed": True,
                    "chain_stage": True, "internal_watch_ids": oz_stage.get("internal_ids", []),
                }

        # Composer가 자동 arm한 OZ의 등록 ACK는 사용자/공식채널에 노출하지 않습니다.
        if kind == "CONTROL_ACK" and watch_id.startswith(("OZARM:", "CFGCHAINTRG:", "CHAINTRG:")):
            return {"ok": True, "delivered": True, "suppressed": True}

        # The final OZ alert is the trigger: a strategy's trading time is checked here, at this moment.
        # Outside it the alert is dropped (not sent later); the OZ watch stays for its next trigger.
        blocked: set[str] = set()
        if kind == "FINAL_ALERT":
            event_ids = [str(x) for x in (event.get("watch_ids") or []) if x]
            if watch_id:
                event_ids.append(watch_id)
            with self._lock:
                blocked = self._trading_time_blocked_locked(event_ids)
            if blocked and blocked >= set(event_ids):
                if logging.getLogger().isEnabledFor(logging.INFO):
                    logging.info("⏰ [OZ 최종 알림] 거래시간 밖 - 보내지 않음 | %s", ",".join(sorted(blocked)))
                return {"ok": True, "delivered": True, "suppressed": True, "outside_trading_time": True}

        message = str(event.get("message") or "").strip()
        if not message:
            return {"ok": False, "delivered": False, "error": "empty_message"}
        request_chat_id = str(event.get("request_chat_id") or "").strip() or None

        # 재시작 동기화/내부 정리에서 발생한 CONTROL_ACK는 사용자 명령 응답이 아닙니다.
        # 목적지가 없는 CONTROL_ACK를 공식 채널 기본값으로 보내지 않습니다.
        if kind == "CONTROL_ACK" and request_chat_id is None:
            if logging.getLogger().isEnabledFor(logging.INFO):
                logging.info("🔇 [OZ CONTROL_ACK 억제] 내부/재시작 정리 ACK | watch_id=%s | %s", watch_id or "-", message)
            return {"ok": True, "delivered": False, "suppressed": True}

        # 개인전략(request_chat_id 있음)은 monitor_OZ의 기존 심플 알림을 그대로 유지합니다.
        # 공식 메인전략만 SPECIAL에 등록된 템플릿으로 렌더링합니다.
        if kind == "FINAL_ALERT" and request_chat_id is None:
            with self._lock:
                spec = self._official_spec_for_oz_event_locked(event)
                if spec is not None:
                    message = self._render_official_oz_alert(spec, event, message)

        target = request_chat_id or self.chat_id
        role = str(event.get("watch_event") or "")
        links = self._watch_links_for_ids([watch_id], target)
        if kind == "CONTROL_ACK" and role == "REGISTERED" and links:
            delivered = self.send_telegram(message, target, watch_id=watch_id, watch_registration=True)
        elif kind == "CONTROL_ACK" and role == "CANCELLED" and event.get("reply_cancel") and links:
            link = links[0]
            removed = int(event.get("removed_count") or 0)
            with self._lock:
                if not link.get("cancel_result_text"):
                    link["status"] = "cancelled" if removed else "inactive"
                    self._save_private_state_locked()
            result_text = self._watch_cancel_text(link) if removed else "ℹ️ 이미 취소되었거나 종료된 감시입니다."
            delivered = self._send_watch_cancel_result(link, result_text)
        elif kind == "FINAL_ALERT" or role == "ALERT":
            ids = [str(x) for x in (event.get("watch_ids") or []) if x]
            if watch_id:
                ids.append(watch_id)
            # Internal OZ stage watches have already been consumed by the orchestrator.
            ids = [x for x in ids if x not in oz_stage.get("internal_ids", []) and x not in blocked]
            links = self._watch_links_for_ids(ids, target)
            delivered = self._send_watch_notification(message, target, links) if links else self.send_telegram(message, target)
            # Preserve legacy/unmapped watches sharing the same engine event.
            known = {wid for link in links for wid in [link["watch_id"], *link["members"]]}
            if links and any(wid not in known for wid in ids):
                delivered = self.send_telegram(message, target) and delivered
        else:
            delivered = self.send_telegram(message, target)
        if delivered and kind == "FINAL_ALERT":
            watch_ids = [str(x) for x in (event.get("watch_ids") or []) if str(x) and str(x) not in blocked]
            self.special_api.cancel_oz_watches(watch_ids)
            transaction = getattr(self, '_oz_dispatch_transaction', None)
            if transaction is not None:
                transaction['completions'].append(dict(event))
            else:
                self.watch_orchestrator.complete_final_oz(event, delivered=True)
        return {
            "ok": bool(delivered),
            "delivered": bool(delivered),
            "error": None if delivered else "telegram_send_failed",
        }

    def _handle_event(self, event: object) -> dict:
        key = receipt_key(event) if isinstance(event, dict) else None
        if not key:
            return self._dispatch_event(event)
        with self._receipts.lock:
            previous = self._receipts.get(key)
            if previous and previous.get('status') == 'done':
                return dict(previous['reply'], duplicate=True)
            self._receipts.put(key, {'status': 'processing', 'event': event})
            self._delivery_context.event_id = key
            try:
                reply = self._dispatch_event(event)
                if reply.get('ok'):
                    self._receipts.put(key, {'status': 'done', 'reply': reply})
                return dict(reply, event_id=event['event_id'], accepted=bool(reply.get('ok')))
            finally:
                self._delivery_context.event_id = None

    def _dispatch_event(self, event: object) -> dict:
        if not isinstance(event, dict):
            return {"ok": False, "delivered": False, "error": "invalid_event"}
        kind = str(event.get("kind") or "FINAL_ALERT").upper()
        strategy = str(event.get("strategy") or "").upper()
        if kind == "PING":
            # 각 프로그램은 명령 수신 준비를 끝낸 뒤 PING을 보냅니다.
            # 그 시점에 현재 살아 있어야 할 감시만 다시 전달하여 시작 직후 명령 유실을 막습니다.
            if strategy in {"TREND", "FVG", "SWEEP"}:
                self._refresh_engine_family_after_ping(strategy)
            elif strategy == "OZ":
                self._refresh_oz_after_ping()
            return {"ok": True, "delivered": False, "pong": True, "service": "manager_KIM Composer 2.0"}
        if kind.endswith("QUERY_RESULT"):
            return self._handle_query_result(event)
        if kind == "GENERIC_TRIGGER":
            return self._handle_generic_trigger(event)
        if strategy in {"TREND", "FVG", "SWEEP"}:
            result = self._handle_fact_event(event)
            if result.get("ok") and not result.get("stale"):
                for observer in tuple(self._special_fact_observers.values()):
                    observer(deepcopy(event))
            return result
        if strategy == "OZ" or kind == "CONTROL_ACK":
            return self._handle_oz_event(event)
        if kind == "FINAL_ALERT":
            message = str(event.get("message") or "").strip()
            delivered = self.send_telegram(message) if message else False
            return {"ok": bool(delivered), "delivered": bool(delivered)}
        return {"ok": False, "delivered": False, "error": f"unknown_kind:{kind}"}

    # -------------------------------
    # WONBI polling (Composer condition)
    # -------------------------------
    def _wonbi_requirements_locked(self) -> dict[str, set[str]]:
        out: dict[str, set[str]] = {}
        for spec in self._all_specs_locked():
            for cond in spec.conditions:
                if cond.kind == "WONBI":
                    out.setdefault(spec.symbol, set()).add(cond.tf)
        # SPECIAL 전략이 LIVE 판정에 Composer 원비 fact를 쓰는 경우만 공용 요구사항으로 추가합니다.
        for chain in self.timed_chains.values():
            if not chain.enabled:
                continue
            for trig in chain.triggers:
                condition_type, evaluation_mode = trigger_watch_contract(trig)
                if condition_type == "COMPOUND_CONDITION":
                    for cond in self._compound_conditions_for_trigger(trig):
                        if cond.kind == "WONBI":
                            out.setdefault(chain.symbol, set()).add(cond.tf)
                    continue
                handler = self._special_watch_handlers.get(condition_type)
                requirement_fn = getattr(handler, "needs_live_wonbi", None) if handler is not None else None
                if not callable(requirement_fn):
                    continue
                try:
                    required = bool(requirement_fn(chain, trig, evaluation_mode))
                except Exception:
                    logging.exception("[SPECIAL Watch] WONBI requirement 오류 | %s", condition_type)
                    required = False
                if required:
                    out.setdefault(chain.symbol, set()).add(trig.tf)
        return out

    def _condition_market(self, symbol, tfs, indicators, *, ma_names=None):
        if self.event_services is None:
            return self.staff.request(symbol, tfs, indicators)
        kernel=self.event_services.kernel
        return kernel.inputs.read(kernel,symbol,tfs,indicators,ma_names=ma_names)

    @staticmethod
    def _condition_row(frame,index):
        return frame.row(index) if hasattr(frame,'row') else frame.iloc[index]

    def _update_wonbi(self) -> None:
        with self._lock:
            required = self._wonbi_requirements_locked()
        for symbol, tf_set in sorted(required.items()):
            data = self._condition_market(symbol, sorted(tf_set, key=tf_seconds), ["WONBI"])
            if not data:
                continue
            for tf in sorted(tf_set, key=tf_seconds):
                df = data.get(tf)
                if df is not None and not df.empty:
                    self._observe_local_source('WONBI', symbol, tf, df, ['WONBI'])
                if df is None or len(df) < 1:
                    continue
                row = self._condition_row(df,-1)
                try:
                    low = float(row.get("low"))
                    high = float(row.get("high"))
                    lower = float(row.get("wonbi_lower"))
                    upper = float(row.get("wonbi_upper"))
                except (TypeError, ValueError):
                    continue
                if not all(math.isfinite(x) for x in (low, high, lower, upper)):
                    continue
                event_time = row.get("time")
                changed = False
                with self._lock:
                    for side, active in (("LOWER", low <= lower), ("UPPER", high >= upper)):
                        key = (symbol, tf, side)
                        before = bool(self.wonbi_facts.get(key, {}).get("active"))
                        if active and not before:
                            self._wonbi_touch_seq += 1
                            self.wonbi_facts[key] = {
                                "active": True,
                                "touch_id": f"{_event_time_token(event_time)}:{self._wonbi_touch_seq}",
                                "event_time": event_time,
                                "price_low": low,
                                "price_high": high,
                                "level": lower if side == "LOWER" else upper,
                            }
                            changed = True
                            if logging.getLogger().isEnabledFor(logging.INFO):
                                logging.info("🟠 [Composer WONBI] %s %s %s TOUCH", symbol, tf, side)
                        elif not active and before:
                            self.wonbi_facts[key] = {"active": False, "touch_id": None, "event_time": event_time}
                            changed = True
                            if logging.getLogger().isEnabledFor(logging.INFO):
                                logging.info("↩️ [Composer WONBI] %s %s %s TOUCH END", symbol, tf, side)
                    if changed:
                        self._evaluate_symbol_locked(symbol)

    def _percentile_requirements_locked(self) -> dict[str, set[str]]:
        out: dict[str, set[str]] = {}
        for spec in self._all_specs_locked():
            for cond in spec.conditions:
                if cond.kind == "PERCENTILE":
                    out.setdefault(spec.symbol, set()).add(cond.tf)
        for chain in self.timed_chains.values():
            if not chain.enabled:
                continue
            for trig in chain.triggers:
                for cond in self._compound_conditions_for_trigger(trig):
                    if cond.kind == "PERCENTILE":
                        out.setdefault(chain.symbol, set()).add(cond.tf)
        return out

    @staticmethod
    def _percentile_out_families(row: pd.Series) -> tuple[tuple[str, ...], tuple[str, ...]]:
        specs = {
            "RSI": ("RSI_val", "RSI_db", "RSI_ub"),
            "STO": ("STO_val", "STO_db", "STO_ub"),
            "DI": ("DI_val", "DI_db", "DI_ub"),
            "PRICE": ("price_hma_6", "price_band_lower", "price_band_upper"),
        }
        lower, upper = [], []
        for family, (v_col, lo_col, hi_col) in specs.items():
            try:
                value = float(row.get(v_col))
                lo = float(row.get(lo_col))
                hi = float(row.get(hi_col))
            except (TypeError, ValueError):
                continue
            if not all(math.isfinite(x) for x in (value, lo, hi)):
                continue
            if getattr(row,'native_out',False):
                prefix='price' if family=='PRICE' else family
                if math.isfinite(float(row.get(prefix+'_lower_out'))):lower.append(family)
                elif math.isfinite(float(row.get(prefix+'_upper_out'))):upper.append(family)
            elif value < lo:
                lower.append(family)
            elif value > hi:
                upper.append(family)
        return tuple(lower), tuple(upper)

    def _update_percentile(self) -> None:
        with self._lock:
            required = self._percentile_requirements_locked()
        for symbol, tf_set in sorted(required.items()):
            data = self._condition_market(symbol, sorted(tf_set, key=tf_seconds), ["RSI", "STO", "DI", "PRICE"])
            if not data:
                continue
            for tf in sorted(tf_set, key=tf_seconds):
                df = data.get(tf)
                if df is not None and not df.empty:
                    self._observe_local_source('PERCENTILE', symbol, tf, df, ['RSI', 'STO', 'DI', 'PRICE'])
                if df is None or len(df) < 1:
                    continue
                row = self._condition_row(df,-1)
                lower, upper = self._percentile_out_families(row)
                event_time = row.get("time")
                changed = False
                with self._lock:
                    for side, families in (("LOWER", lower), ("UPPER", upper)):
                        key = (symbol, tf, side)
                        before = self.percentile_facts.get(key, {})
                        before_active = bool(before.get("active"))
                        before_families = tuple(before.get("families") or ())
                        active = bool(families)
                        if active and (not before_active or before_families != families):
                            self._percentile_touch_seq += 1
                            self.percentile_facts[key] = {
                                "active": True,
                                "touch_id": f"{_event_time_token(event_time)}:{self._percentile_touch_seq}",
                                "event_time": event_time,
                                "families": families,
                            }
                            changed = True
                            if logging.getLogger().isEnabledFor(logging.INFO):
                                logging.info("🟣 [Composer OUT] %s %s %s | %s", symbol, tf, side, ",".join(families))
                        elif not active and before_active:
                            self.percentile_facts[key] = {
                                "active": False, "touch_id": None, "event_time": event_time, "families": (),
                            }
                            changed = True
                            if logging.getLogger().isEnabledFor(logging.INFO):
                                logging.info("↩️ [Composer OUT] %s %s %s END", symbol, tf, side)
                    if changed:
                        self._evaluate_symbol_locked(symbol)

    def _ma_state_requirements_locked(self) -> dict[str, dict[str, set[tuple[str, int, int]]]]:
        out: dict[str, dict[str, set[tuple[str, int, int]]]] = {}
        for spec in self._all_specs_locked():
            for cond in spec.conditions:
                if cond.kind != "MA_STATE":
                    continue
                out.setdefault(spec.symbol, {}).setdefault(cond.tf, set()).add(
                    (cond.ma_family, int(cond.fast_period), int(cond.slow_period))
                )
        for chain in self.timed_chains.values():
            if not chain.enabled:
                continue
            for trig in chain.triggers:
                for cond in self._compound_conditions_for_trigger(trig):
                    if cond.kind != "MA_STATE":
                        continue
                    out.setdefault(chain.symbol, {}).setdefault(cond.tf, set()).add(
                        (cond.ma_family, int(cond.fast_period), int(cond.slow_period))
                    )
        return out

    def _ma_price_state_requirements_locked(self) -> dict[str, dict[str, set[tuple[str, int]]]]:
        """현재 봉 가격 vs STAFF MA 조건이 필요한 종목/TF/MA만 수집합니다."""
        out: dict[str, dict[str, set[tuple[str, int]]]] = {}
        for spec in self._all_specs_locked():
            for cond in spec.conditions:
                if cond.kind != "MA_PRICE_STATE":
                    continue
                out.setdefault(spec.symbol, {}).setdefault(cond.tf, set()).add(
                    (cond.ma_family, int(cond.slow_period))
                )
        for chain in self.timed_chains.values():
            if not chain.enabled:
                continue
            for trig in chain.triggers:
                for cond in self._compound_conditions_for_trigger(trig):
                    if cond.kind != "MA_PRICE_STATE":
                        continue
                    out.setdefault(chain.symbol, {}).setdefault(cond.tf, set()).add(
                        (cond.ma_family, int(cond.slow_period))
                    )
        return out

    def _ma_slope_state_requirements_locked(self) -> dict[str, dict[str, set[tuple[str, int]]]]:
        """현재 STAFF MA와 2봉 전 STAFF MA 비교가 필요한 종목/TF/MA만 수집합니다."""
        out: dict[str, dict[str, set[tuple[str, int]]]] = {}
        for spec in self._all_specs_locked():
            for cond in spec.conditions:
                if cond.kind != "MA_SLOPE_STATE":
                    continue
                out.setdefault(spec.symbol, {}).setdefault(cond.tf, set()).add(
                    (cond.ma_family, int(cond.slow_period))
                )
        for chain in self.timed_chains.values():
            if not chain.enabled:
                continue
            for trig in chain.triggers:
                for cond in self._compound_conditions_for_trigger(trig):
                    if cond.kind != "MA_SLOPE_STATE":
                        continue
                    out.setdefault(chain.symbol, {}).setdefault(cond.tf, set()).add(
                        (cond.ma_family, int(cond.slow_period))
                    )
        return out

    def _update_ma_state(self) -> None:
        """STAFF 최신 진행봉에서 MA 배열/가격 상대위치/방향 상태를 한 요청으로 갱신합니다.

        MA_STATE는 MA 대 MA 현재 배열, MA_PRICE_STATE는 현재 봉 가격 대 MA 상태,
        MA_SLOPE_STATE는 STAFF가 전달한 같은 MA의 현재값과 2봉 전 값을 비교합니다.
        manager_KIM과 strategy_INDICATOR에서는 HMA를 재계산하지 않습니다.
        """
        with self._lock:
            pair_required = self._ma_state_requirements_locked()
            price_required = self._ma_price_state_requirements_locked()
            slope_required = self._ma_slope_state_requirements_locked()

        symbols = set(pair_required) | set(price_required) | set(slope_required)
        for symbol in sorted(symbols):
            pair_tf_map = pair_required.get(symbol, {})
            price_tf_map = price_required.get(symbol, {})
            slope_tf_map = slope_required.get(symbol, {})
            tf_names = set(pair_tf_map) | set(price_tf_map) | set(slope_tf_map)
            tfs = sorted(tf_names, key=tf_seconds)
            indicators = sorted({
                family
                for tf in tf_names
                for family in (
                    *(x[0] for x in pair_tf_map.get(tf, set())),
                    *(x[0] for x in price_tf_map.get(tf, set())),
                    *(x[0] for x in slope_tf_map.get(tf, set())),
                )
            })
            if not tfs:
                continue
            ma_names = {tf: {f'{family}{period}' for family,period in (
                *((family,p) for family,fast,slow in pair_tf_map.get(tf,()) for p in (fast,slow)),
                *price_tf_map.get(tf,()), *slope_tf_map.get(tf,()))} for tf in tfs}
            data = self._condition_market(symbol, tfs, indicators, ma_names=ma_names)
            if not data:
                continue
            observed_at = time.time()

            for tf in tfs:
                pair_reqs = pair_tf_map.get(tf, set())
                price_reqs = price_tf_map.get(tf, set())
                slope_reqs = slope_tf_map.get(tf, set())
                df = data.get(tf)
                if df is not None and not df.empty:
                    self._observe_local_source('MA', symbol, tf, df, [])
                if df is None or len(df) < 1:
                    continue
                row = self._condition_row(df,-1)
                two_bars_ago = self._condition_row(df,-3) if len(df) >= 3 else None
                event_time = row.get("time")

                with self._lock:
                    for family, fast, slow in sorted(pair_reqs):
                        prefix = family.lower()
                        fast_col = f"{prefix}_{int(fast)}"
                        slow_col = f"{prefix}_{int(slow)}"
                        try:
                            fast_value = float(row.get(fast_col))
                            slow_value = float(row.get(slow_col))
                        except (TypeError, ValueError):
                            fast_value = slow_value = float("nan")

                        if not (math.isfinite(fast_value) and math.isfinite(slow_value)):
                            relation = "UNKNOWN"
                        elif fast_value > slow_value:
                            relation = "ABOVE"
                        elif fast_value < slow_value:
                            relation = "BELOW"
                        else:
                            relation = "EQUAL"

                        key = (symbol, tf, family, int(fast), int(slow))
                        before = self.ma_state_facts.get(key, {})
                        before_relation = str(before.get("relation") or "")
                        if relation != before_relation:
                            self._ma_state_seq += 1
                            state_id = f"{_event_time_token(event_time)}:{self._ma_state_seq}"
                            if logging.getLogger().isEnabledFor(logging.INFO):
                                logging.info(
                                    "📐 [Composer MA상태] %s %s %s%d/%d | %s → %s",
                                    symbol, tf, family, fast, slow, before_relation or "NONE", relation,
                                )
                        else:
                            state_id = before.get("state_id") or f"{_event_time_token(event_time)}:{self._ma_state_seq}"

                        self.ma_state_facts[key] = {
                            "relation": relation,
                            "state_id": state_id,
                            "event_time": event_time,
                            "observed_at": observed_at,
                            "fast_value": fast_value if math.isfinite(fast_value) else None,
                            "slow_value": slow_value if math.isfinite(slow_value) else None,
                        }

                    for family, period in sorted(price_reqs):
                        ma_col = f"{family.lower()}_{int(period)}"
                        try:
                            price_value = float(row.get("close"))
                            ma_value = float(row.get(ma_col))
                        except (TypeError, ValueError):
                            price_value = ma_value = float("nan")

                        if not (math.isfinite(price_value) and math.isfinite(ma_value)):
                            relation = "UNKNOWN"
                        elif price_value > ma_value:
                            relation = "ABOVE"
                        elif price_value < ma_value:
                            relation = "BELOW"
                        else:
                            relation = "EQUAL"

                        key = (symbol, tf, family, int(period))
                        before = self.ma_price_state_facts.get(key, {})
                        before_relation = str(before.get("relation") or "")
                        if relation != before_relation:
                            self._ma_price_state_seq += 1
                            state_id = f"{_event_time_token(event_time)}:{self._ma_price_state_seq}"
                            if logging.getLogger().isEnabledFor(logging.INFO):
                                logging.info(
                                    "📐 [Composer 봉/MA상태] %s %s PRICE/%s%d | %s → %s",
                                    symbol, tf, family, period, before_relation or "NONE", relation,
                                )
                        else:
                            state_id = before.get("state_id") or f"{_event_time_token(event_time)}:{self._ma_price_state_seq}"

                        self.ma_price_state_facts[key] = {
                            "relation": relation,
                            "state_id": state_id,
                            "event_time": event_time,
                            "observed_at": observed_at,
                            "price_value": price_value if math.isfinite(price_value) else None,
                            "ma_value": ma_value if math.isfinite(ma_value) else None,
                        }

                    for family, period in sorted(slope_reqs):
                        ma_col = f"{family.lower()}_{int(period)}"
                        try:
                            current_value = float(row.get(ma_col))
                            two_bars_value = float(two_bars_ago.get(ma_col)) if two_bars_ago is not None else float("nan")
                        except (TypeError, ValueError):
                            current_value = two_bars_value = float("nan")

                        if not (math.isfinite(current_value) and math.isfinite(two_bars_value)):
                            relation = "UNKNOWN"
                        elif current_value > two_bars_value:
                            relation = "UP"
                        elif current_value < two_bars_value:
                            relation = "DOWN"
                        else:
                            relation = "FLAT"

                        key = (symbol, tf, family, int(period))
                        before = self.ma_slope_state_facts.get(key, {})
                        before_relation = str(before.get("relation") or "")
                        if relation != before_relation:
                            self._ma_slope_state_seq += 1
                            state_id = f"{_event_time_token(event_time)}:{self._ma_slope_state_seq}"
                            if logging.getLogger().isEnabledFor(logging.INFO):
                                logging.info(
                                    "📐 [Composer MA방향] %s %s %s%d 현재/2봉전 | %s → %s",
                                    symbol, tf, family, period, before_relation or "NONE", relation,
                                )
                        else:
                            state_id = before.get("state_id") or f"{_event_time_token(event_time)}:{self._ma_slope_state_seq}"

                        self.ma_slope_state_facts[key] = {
                            "relation": relation,
                            "state_id": state_id,
                            "event_time": event_time,
                            "observed_at": observed_at,
                            "current_value": current_value if math.isfinite(current_value) else None,
                            "two_bars_ago_value": two_bars_value if math.isfinite(two_bars_value) else None,
                        }


    # -------------------------------
    # timed trigger-chain engine
    # -------------------------------









    def _chain_trigger_payloads_locked(self, chain: TimedChainSpec) -> list[dict]:
        return self.watch_orchestrator.trigger_payloads_locked(chain)

    def _chain_final_payload_locked(self, chain: TimedChainSpec) -> Optional[dict]:
        return self.watch_orchestrator.final_payload_locked(chain)

    def _add_timed_chain(self, chain: TimedChainSpec) -> None:
        chain.validate()
        self._remember_watch_command(chain.chain_id, chain.owner_chat_id, "TIMED_CHAIN", "TIMED_CHAIN")
        self.watch_orchestrator.add_chain(chain)

    @staticmethod
    def _config_chain_event_ts(event: dict, fallback: float) -> float:
        for key in ("completion_time", "event_time", "trigger_time"):
            value = event.get(key)
            if value in {None, ""}:
                continue
            try:
                if isinstance(value, (int, float)):
                    ts = float(value)
                    if math.isfinite(ts):
                        return ts
                ts = float(pd.Timestamp(value).timestamp())
                if math.isfinite(ts):
                    return ts
            except Exception:
                continue
        return float(fallback)

    @staticmethod
    def _config_chain_completion_deadline(item: dict) -> float:
        try:
            limits = [float(item[key]) for key in ('expires_at', 'completion_deadline')
                      if item.get(key) is not None]
        except (TypeError, ValueError):
            return float('-inf')
        return min(limits) if limits and all(math.isfinite(x) for x in limits) else float('-inf')

    def _filter_config_chain_deadline_event(self, event: dict) -> tuple[dict, bool]:
        ids = list(event.get('watch_ids') or [])
        if event.get('watch_id') and event['watch_id'] not in ids:
            ids.append(event['watch_id'])
        completed = self._config_chain_event_ts(event, float('nan'))
        rejected = set()
        with self._lock:
            for wid in ids:
                if not str(wid).startswith('OZARM:CFGCHAIN'):
                    continue
                item = self._config_chain_active.get(wid)
                if (item is None or not math.isfinite(completed)
                        or completed > self._config_chain_completion_deadline(item)):
                    rejected.add(wid)
        if not rejected:
            return event, False
        remaining = [wid for wid in ids if wid not in rejected]
        event = dict(event, watch_ids=remaining)
        if event.get('watch_id') in rejected:
            event.pop('watch_id')
        return event, not remaining

    @staticmethod
    def _config_chain_event_direction(event: dict) -> Optional[str]:
        direction = str(event.get("direction") or event.get("signal_direction") or "").upper()
        if direction in {"LONG", "SHORT"}:
            return direction
        text = str(event.get("message") or event.get("trigger_name") or "").lower()
        compact = re.sub(r"\s+", "", text)
        if any(x in compact for x in ("골크", "골든크로스", "goldencross")):
            return "LONG"
        if any(x in compact for x in ("데크", "데드크로스", "deadcross")):
            return "SHORT"
        return None

    @staticmethod
    def _opposite_direction(direction: str) -> Optional[str]:
        direction = str(direction or "").upper()
        if direction == "LONG":
            return "SHORT"
        if direction == "SHORT":
            return "LONG"
        return None

    def _config_chain_direction_active_locked(
        self, spec: ConfigTimedChainSpec, state: dict, direction: str
    ) -> bool:
        """공식 TIMED_CHAIN에서 특정 방향의 현재 후보/최종 OZ가 살아 있는지 확인합니다."""
        direction = str(direction or "").upper()
        if direction not in {"LONG", "SHORT"}:
            return False

        if spec.order_mode == "UNORDERED":
            latch = state.get("unordered_latch") if isinstance(state, dict) else None
            groups = latch.get("groups") if isinstance(latch, dict) else {}
            hits = groups.get(direction) if isinstance(groups, dict) else None
            if isinstance(hits, dict) and bool(hits):
                return True
        else:
            cross = state.get("latest_cross") if isinstance(state, dict) else None
            if int(state.get("stage", 0) or 0) == 1 and isinstance(cross, dict):
                if str(cross.get("direction") or "").upper() == direction:
                    return True

        now = time.time()
        post_touch = state.get("post_touch_armed") if isinstance(state, dict) else None
        if isinstance(post_touch, dict) and str(post_touch.get("direction") or "").upper() == direction:
            try:
                if now < float(post_touch.get("expires_at") or 0.0):
                    return True
            except (TypeError, ValueError):
                pass

        for item in self._config_chain_active.values():
            if str(item.get("spec_id") or "") != spec.spec_id:
                continue
            if str(item.get("direction") or "").upper() != direction:
                continue
            try:
                if now < float(item.get("expires_at") or float("inf")):
                    return True
            except (TypeError, ValueError):
                return True
        return False

    def _cancel_config_chain_direction_locked(
        self, spec: ConfigTimedChainSpec, state: dict, direction: str, reason: str, pushes: list[dict]
    ) -> bool:
        """현재 공식 TIMED_CHAIN의 한 방향 사이클만 폐기하고 전략 등록 자체는 유지합니다."""
        direction = str(direction or "").upper()
        if direction not in {"LONG", "SHORT"}:
            return False
        changed = False

        if spec.order_mode == "UNORDERED":
            latch = state.get("unordered_latch")
            if isinstance(latch, dict) and UnorderedConditionLatch.clear_group(latch, direction):
                changed = True
        else:
            cross = state.get("latest_cross")
            if (
                int(state.get("stage", 0) or 0) == 1
                and isinstance(cross, dict)
                and str(cross.get("direction") or "").upper() == direction
            ):
                state["stage"] = 0
                state["latest_cross"] = None
                state["latest_fvg"] = None
                state["stage_deadline"] = None
                changed = True

        post_touch = state.get("post_touch_armed")
        if isinstance(post_touch, dict) and str(post_touch.get("direction") or "").upper() == direction:
            state["post_touch_armed"] = None
            changed = True

        for child_id, item in list(self._config_chain_active.items()):
            if str(item.get("spec_id") or "") != spec.spec_id:
                continue
            if str(item.get("direction") or "").upper() != direction:
                continue
            pushes.append({
                "action": "CANCEL_MANUAL", "watch_id": child_id,
                "request_chat_id": None,
                "validation_mode": item.get("validation_mode"),
                "trigger_mode": item.get("trigger_mode"),
            })
            self._config_chain_active.pop(child_id, None)
            changed = True

        if changed:
            state["spec_signature"] = self._config_chain_spec_signature(spec)
            self._save_config_chain_state_locked()
            if logging.getLogger().isEnabledFor(logging.INFO):
                logging.info(
                    "↩️ [Composer SPECIAL TIMED_CHAIN] CANCEL_ON | %s | %s 취소 | %s",
                    spec.name, direction, reason,
                )
        return changed

    def _maybe_cancel_config_chain_on_opposite_locked(
        self, spec: ConfigTimedChainSpec, state: dict, *, condition_key: str, tf: str,
        event_direction: str, pushes: list[dict]
    ) -> Optional[str]:
        """반대 Cross/FVG가 현재 반대방향 사이클을 무효화하면 취소 사유를 반환합니다."""
        event_direction = str(event_direction or "").upper()
        if event_direction not in {"LONG", "SHORT"}:
            return None
        condition_key = str(condition_key or "").upper()
        tf = normalize_tf(tf)

        if condition_key == "CROSS":
            if not spec.cancel_on_opposite_cross:
                return None
            reason = f"반대 {spec.cross_tf} {spec.ma_family}{spec.fast_period}/{spec.slow_period} 크로스"
        elif condition_key == "FVG":
            if tf not in spec.cancel_on_opposite_fvg_tfs:
                return None
            reason = f"반대 {tf} 신규 FVG"
        else:
            return None

        target_direction = self._opposite_direction(event_direction)
        if target_direction is None or not self._config_chain_direction_active_locked(spec, state, target_direction):
            return None
        if self._cancel_config_chain_direction_locked(spec, state, target_direction, reason, pushes):
            return "cancel_on_opposite_cross" if condition_key == "CROSS" else "cancel_on_opposite_fvg"
        return None

    def _prune_config_chain_unordered_bars_locked(
        self, spec: ConfigTimedChainSpec, latch_state: dict, bar_times: tuple[float, ...]
    ) -> bool:
        """UNORDERED hit를 CROSS_TF 실제 확정봉 수 기준으로 독립 만료합니다."""
        if spec.max_gap_bars <= 0 or not bar_times:
            return False

        def _expired(_group: str, _key: str, hit: dict) -> bool:
            meta = hit.get("meta") if isinstance(hit.get("meta"), dict) else {}
            anchor = meta.get("bar_anchor_ts")
            age = self._config_chain_bar_age_from_times(anchor, bar_times)
            # bar data를 확보한 상태에서 anchor가 손상된 hit는 안전하게 폐기합니다.
            return age is None or int(age) > int(spec.max_gap_bars)

        return UnorderedConditionLatch.remove_where(latch_state, _expired)

    def _handle_config_chain_unordered_trigger(self, event: dict) -> dict:
        """공식 TIMED_CHAIN의 CROSS/FVG 순서무관 누적 경로."""
        chain_id = str(event.get("chain_id") or "").strip()
        spec_id = chain_id.split(":", 1)[1] if chain_id.startswith("CFGCHAIN:") else ""
        watch_id = str(event.get("watch_id") or "").strip()
        now = time.time()
        event_ts = self._config_chain_event_ts(event, float('nan'))
        if not math.isfinite(event_ts):
            return {'ok': False, 'delivered': False, 'error': 'missing_completion_timestamp'}
        pushes: list[dict] = []

        with self._lock:
            pre_spec = self.official_chain_specs.get(spec_id)
        fresh_bar_times: tuple[float, ...] = ()
        if (
            pre_spec is not None and pre_spec.enabled
            and pre_spec.order_mode == "UNORDERED" and pre_spec.max_gap_bars > 0
        ):
            fresh_bar_times = self._config_chain_closed_bar_times(pre_spec, self._config_chain_event_staff)

        with self._lock:
            spec = self.official_chain_specs.get(spec_id)
            state = self._config_chain_state.get(spec_id)
            if spec is None or state is None or not spec.enabled or spec.order_mode != "UNORDERED":
                return {"ok": True, "delivered": False, "ignored": True}

            if pre_spec is None or (
                pre_spec.symbol != spec.symbol or pre_spec.cross_tf != spec.cross_tf
                or pre_spec.max_gap_bars != spec.max_gap_bars
                or pre_spec.order_mode != spec.order_mode
            ):
                fresh_bar_times = ()

            bar_times = fresh_bar_times
            if spec.max_gap_bars > 0 and not bar_times:
                bar_times = self._config_chain_bar_times_cache.get((spec.symbol, spec.cross_tf), ())

            trigger_payloads = self._config_chain_trigger_payloads_locked(spec)
            by_id = {str(x["watch_id"]): x for x in trigger_payloads}
            fired_payload = by_id.get(watch_id)
            if fired_payload is None:
                return {"ok": True, "delivered": False, "stale": True}

            incoming_stage = int(fired_payload.get("chain_stage", -1))
            condition_key = "CROSS" if incoming_stage == 0 else "FVG" if incoming_stage > 0 else ""
            if not condition_key:
                return {"ok": True, "delivered": False, "stale": True, "chain_id": chain_id}

            direction = self._config_chain_event_direction(event)
            if direction not in {"LONG", "SHORT"}:
                pushes.append(fired_payload)
                result = {
                    "ok": True, "delivered": False, "ignored": True,
                    "reason": "direction_missing", "chain_id": chain_id,
                }
            else:
                tf = normalize_tf((fired_payload.get("timeframes") or [""])[0])
                cancel_reason = self._maybe_cancel_config_chain_on_opposite_locked(
                    spec, state, condition_key=condition_key, tf=tf,
                    event_direction=direction, pushes=pushes,
                )
                if cancel_reason:
                    pushes.append(fired_payload)
                    result = {
                        "ok": True, "delivered": False, "chain_id": chain_id,
                        "config_timed_chain": True, "cancelled": True,
                        "reason": cancel_reason, "cancel_event_direction": direction,
                    }
                    for payload in pushes:
                        self._push(payload)
                    return result

                latch_state = state.get("unordered_latch")
                if not isinstance(latch_state, dict):
                    latch_state = UnorderedConditionLatch.new_state()
                    state["unordered_latch"] = latch_state

                changed = UnorderedConditionLatch.prune(latch_state, spec.max_gap_sec, event_ts)
                if spec.max_gap_bars > 0 and bar_times:
                    changed = self._prune_config_chain_unordered_bars_locked(spec, latch_state, bar_times) or changed

                event_token = str(
                    event.get("event_id") or event.get("trigger_id") or event.get("event_time")
                    or event.get("completion_time") or event.get("bar_time") or event.get("time") or f"{event_ts:.6f}"
                )
                bar_anchor_ts: Optional[float] = None
                if spec.max_gap_bars > 0:
                    if fresh_bar_times:
                        bar_anchor_ts = float(fresh_bar_times[-1])
                    else:
                        # 이벤트 순간 fresh STAFF 조회 실패 시 기존 순서형과 동일한 보수적 fallback을 사용합니다.
                        bar_anchor_ts = float(event_ts) - float(tf_seconds(spec.cross_tf))

                latch_result = UnorderedConditionLatch.register(
                    latch_state,
                    correlation_key=direction,
                    condition_key=condition_key,
                    event_ts=float(event_ts),
                    token=event_token,
                    required_count=2,
                    window_sec=spec.max_gap_sec,
                    now=event_ts,
                    meta={
                        "tf": tf, "watch_id": watch_id,
                        "chain_stage": incoming_stage, "bar_anchor_ts": bar_anchor_ts,
                    },
                )
                changed = True

                # 봉 수 expiry가 켜진 경우 현재 실제 확정봉으로 다시 정리한 뒤 match를 재평가합니다.
                if spec.max_gap_bars > 0 and bar_times:
                    if self._prune_config_chain_unordered_bars_locked(spec, latch_state, bar_times):
                        changed = True
                    groups = latch_state.get("groups") if isinstance(latch_state, dict) else {}
                    group_hits = groups.get(direction, {}) if isinstance(groups, dict) else {}
                    if len(group_hits) < 2:
                        latch_result = dict(latch_result)
                        latch_result["matched"] = False
                        latch_result["count"] = len(group_hits)

                groups = latch_state.get("groups") if isinstance(latch_state, dict) else {}
                group_hits = groups.get(direction, {}) if isinstance(groups, dict) else {}
                cross_hit = group_hits.get("CROSS") if isinstance(group_hits, dict) else None
                fvg_hit = group_hits.get("FVG") if isinstance(group_hits, dict) else None
                matched = bool(latch_result.get("matched")) and isinstance(cross_hit, dict) and isinstance(fvg_hit, dict)

                bar_gap = None
                if matched and spec.max_gap_bars > 0:
                    if not bar_times:
                        matched = False
                    else:
                        anchors = []
                        for hit in (cross_hit, fvg_hit):
                            meta = hit.get("meta") if isinstance(hit.get("meta"), dict) else {}
                            anchor = self._config_chain_value_epoch(meta.get("bar_anchor_ts"))
                            if anchor is not None:
                                anchors.append(anchor)
                        if len(anchors) != 2:
                            matched = False
                        else:
                            first_anchor = min(anchors)
                            bar_gap = self._config_chain_bar_age_from_times(first_anchor, bar_times)
                            if bar_gap is None or int(bar_gap) > int(spec.max_gap_bars):
                                matched = False

                if matched:
                    pair_completed = max(float(cross_hit['ts']), float(fvg_hit['ts']))
                    pair_deadline = (min(float(cross_hit['ts']), float(fvg_hit['ts'])) + spec.max_gap_sec
                                     if spec.max_gap_sec > 0 else None)
                    final_deadline = pair_completed + spec.final_window_sec
                    if pair_deadline is not None:
                        final_deadline = min(final_deadline, pair_deadline)
                    signature = stable_id(
                        "CFGPAIR", spec.spec_id, direction, latch_result.get("signature"), length=24,
                    )
                    duplicate = state.get("last_pair_signature") == signature
                    # Trading time applies at the final alert; the chain advances at any time.
                    should_advance = not duplicate

                    if should_advance:
                        # 같은 메인 TIMED_CHAIN의 이전 기간제 OZ 감시는 새 setup이 오면 교체합니다.
                        for child_id, item in list(self._config_chain_active.items()):
                            if str(item.get("spec_id") or "") != spec.spec_id:
                                continue
                            pushes.append({
                                "action": "CANCEL_MANUAL", "watch_id": child_id,
                                "request_chat_id": None,
                                "validation_mode": item.get("validation_mode"),
                                "trigger_mode": item.get("trigger_mode"),
                            })
                            self._config_chain_active.pop(child_id, None)

                        state["last_pair_signature"] = signature
                        UnorderedConditionLatch.clear_group(latch_state, direction)
                        if spec.final_fvg_touch_tfs:
                            state["post_touch_armed"] = {
                                "direction": direction,
                                "expires_at": pair_completed + spec.final_window_sec,
                                "completion_deadline": final_deadline,
                                "pair_signature": signature,
                            }
                            self._save_config_chain_state_locked()
                        else:
                            child_id = stable_id("OZARM:CFGCHAIN", spec.spec_id, direction, signature, length=20)
                            child_payload = {
                                "action": "MANUAL_WATCH", "watch_id": child_id,
                                "timeframes": list(spec.oz_tfs), "symbol": spec.symbol,
                                "direction": direction, "persistent": True,
                                "request_chat_id": None,
                                "validation_mode": spec.validation_mode,
                                "trigger_mode": spec.trigger_mode,
                                "source_spec_id": spec.spec_id,
                                "source_spec_ids": [spec.spec_id],
                                "source_name": spec.name,
                                "trading_times": {spec.spec_id: self._chain_trading_time(spec)},
                            }
                            active_item = dict(child_payload)
                            active_item.update({
                                "spec_id": spec.spec_id,
                                "expires_at": pair_completed + spec.final_window_sec,
                                "completion_deadline": final_deadline,
                            })
                            self._config_chain_active[child_id] = active_item
                            pushes.append(child_payload)

                        cross_ts = float(cross_hit.get("ts"))
                        fvg_ts = float(fvg_hit.get("ts"))
                        fvg_meta = fvg_hit.get("meta") if isinstance(fvg_hit.get("meta"), dict) else {}
                        if logging.getLogger().isEnabledFor(logging.INFO):
                            logging.info(
                                "⏱️ [Composer SPECIAL TIMED_CHAIN] 순서무관 성립→NEXT | %s | %s | "
                                "CROSS=%s %s%d/%d · FVG=%s | gap=%.1fs%s | %s",
                                spec.name, direction, spec.cross_tf, spec.ma_family, spec.fast_period, spec.slow_period,
                                fvg_meta.get("tf") or "-", abs(fvg_ts - cross_ts),
                                "" if bar_gap is None else f"/{bar_gap}봉",
                                (
                                    f"{format_duration_ko(spec.final_window_sec)} 안에 "
                                    f"{'/'.join(spec.final_fvg_touch_tfs)} 동방향 FVG TOUCH 대기"
                                    if spec.final_fvg_touch_tfs
                                    else f"{format_duration_ko(spec.final_window_sec)} 동안 OZ"
                                ),
                            )
                        result = {
                            "ok": True, "delivered": False, "chain_id": chain_id,
                            "config_timed_chain": True,
                            "stage": "WAIT_FVG_TOUCH" if spec.final_fvg_touch_tfs else "WAIT_ANY",
                            "advanced": True, "order_mode": "UNORDERED",
                        }
                    else:
                        result = {
                            "ok": True, "delivered": False, "chain_id": chain_id,
                            "config_timed_chain": True, "stage": "WAIT_ANY",
                            "advanced": False, "order_mode": "UNORDERED",
                            "reason": "duplicate_pair",
                            "latched": len(group_hits),
                        }
                else:
                    result = {
                        "ok": True, "delivered": False, "chain_id": chain_id,
                        "config_timed_chain": True, "stage": "WAIT_ANY",
                        "advanced": False, "order_mode": "UNORDERED",
                        "latched": len(group_hits), "required": 2,
                        "reason": (
                            "bar_expiry_unavailable"
                            if spec.max_gap_bars > 0 and not bar_times and len(group_hits) >= 2
                            else "waiting_for_pair"
                        ),
                    }

                state["spec_signature"] = self._config_chain_spec_signature(spec)
                if changed:
                    self._save_config_chain_state_locked()
                pushes.append(fired_payload)

        for payload in pushes:
            self._push(payload)
        return result

    def _handle_config_chain_trigger(self, event: dict) -> dict:
        """공식 TIMED_CHAIN: SEQUENTIAL은 기존 A→B, UNORDERED는 별도 latch 경로로 처리합니다."""
        chain_id = str(event.get("chain_id") or "").strip()
        spec_id = chain_id.split(":", 1)[1] if chain_id.startswith("CFGCHAIN:") else ""
        watch_id = str(event.get("watch_id") or "").strip()
        now = time.time()
        event_ts = self._config_chain_event_ts(event, float('nan'))
        if not math.isfinite(event_ts):
            return {'ok': False, 'delivered': False, 'error': 'missing_completion_timestamp'}
        pushes: list[dict] = []

        # 봉 수 expiry가 켜진 체인은 이벤트 스레드 전용 STAFF client로 현재 실제 확정봉을 먼저 읽습니다.
        # manager lock을 잡은 채 ZMQ I/O를 하지 않아 maintenance worker와의 교착을 피합니다.
        with self._lock:
            pre_spec = self.official_chain_specs.get(spec_id)
        if pre_spec is not None and pre_spec.enabled and pre_spec.order_mode == "UNORDERED":
            return self._handle_config_chain_unordered_trigger(event)
        fresh_bar_times: tuple[float, ...] = ()
        if pre_spec is not None and pre_spec.enabled and pre_spec.max_gap_bars > 0:
            fresh_bar_times = self._config_chain_closed_bar_times(pre_spec, self._config_chain_event_staff)

        with self._lock:
            spec = self.official_chain_specs.get(spec_id)
            state = self._config_chain_state.get(spec_id)
            if spec is None or state is None or not spec.enabled:
                return {"ok": True, "delivered": False, "ignored": True}
            if spec.order_mode != "SEQUENTIAL":
                # hot reload가 이벤트 I/O 사이에 mode를 바꾼 경우 이번 옛 경로 이벤트는 폐기합니다.
                return {"ok": True, "delivered": False, "stale": True, "chain_id": chain_id}

            # 조회 도중 hot reload로 체인 핵심축이 바뀌었으면 오래된 bar snapshot은 사용하지 않습니다.
            if pre_spec is None or (
                pre_spec.symbol != spec.symbol or pre_spec.cross_tf != spec.cross_tf
                or pre_spec.max_gap_bars != spec.max_gap_bars
            ):
                fresh_bar_times = ()

            # 이벤트 시점의 fresh 조회가 실패하면 WAIT_B 검증에는 maintenance의 마지막 정상 cache를 사용합니다.
            # A 기준봉을 잡을 때는 stale cache를 쓰지 않고 아래에서 event_ts 기반 fallback을 사용합니다.
            bar_times = fresh_bar_times
            if spec.max_gap_bars > 0 and not bar_times:
                bar_times = self._config_chain_bar_times_cache.get((spec.symbol, spec.cross_tf), ())

            trigger_payloads = self._config_chain_trigger_payloads_locked(spec)
            by_id = {str(x["watch_id"]): x for x in trigger_payloads}
            fired_payload = by_id.get(watch_id)
            if fired_payload is None:
                return {"ok": True, "delivered": False, "stale": True}

            incoming_stage = int(fired_payload.get("chain_stage", -1))
            current_stage = int(state.get("stage", 0))
            event_token = str(
                event.get("event_id") or event.get("trigger_id") or event.get("event_time")
                or event.get("completion_time") or event.get("bar_time") or event.get("time") or f"{event_ts:.6f}"
            )
            condition_key = "CROSS" if incoming_stage == 0 else "FVG" if incoming_stage > 0 else ""
            event_direction = self._config_chain_event_direction(event)
            event_tf = normalize_tf((fired_payload.get("timeframes") or [""])[0])
            if condition_key and event_direction in {"LONG", "SHORT"}:
                cancel_reason = self._maybe_cancel_config_chain_on_opposite_locked(
                    spec, state, condition_key=condition_key, tf=event_tf,
                    event_direction=event_direction, pushes=pushes,
                )
                if cancel_reason:
                    pushes.append(fired_payload)
                    for payload in pushes:
                        self._push(payload)
                    return {
                        "ok": True, "delivered": False, "chain_id": chain_id,
                        "config_timed_chain": True, "cancelled": True,
                        "reason": cancel_reason, "cancel_event_direction": event_direction,
                    }

            # 완료 시각으로 유효성을 판정합니다. 만료 뒤의 새 A는 다음 cycle을
            # 시작하지만, 늦은 B는 지연 수신 증거인 기존 A를 삭제하지 않습니다.
            if current_stage == 1:
                cross_for_expiry = state.get("latest_cross")
                bar_age = None
                if spec.max_gap_bars > 0 and isinstance(cross_for_expiry, dict):
                    bar_age = self._config_chain_bar_age_from_times(
                        cross_for_expiry.get("bar_anchor_ts"), bar_times,
                    )
                expired, expiry_reason = self._config_chain_expiry_status(spec, state, event_ts, bar_age)
                if expired:
                    # A late B cannot destroy the evidence for another delayed,
                    # on-time B. New A still starts a new cycle after expiry.
                    if incoming_stage != 0:
                        return {'ok': True, 'delivered': False, 'expired': True,
                                'reason': f'a_to_b_expired_{expiry_reason}', 'chain_id': chain_id}
                    last_signature = state.get("last_pair_signature")
                    spec_signature = state.get("spec_signature") or self._config_chain_spec_signature(spec)
                    state.clear()
                    state.update(self._default_config_chain_state())
                    state["last_pair_signature"] = last_signature
                    state["spec_signature"] = spec_signature
                    self._save_config_chain_state_locked()
                    current_stage = 0
                    if logging.getLogger().isEnabledFor(logging.INFO):
                        logging.info(
                            "⌛ [Composer SPECIAL TIMED_CHAIN] A→B 조건 만료 | %s | 기준=%s%s | 다음 A 대기",
                            spec.spec_id, expiry_reason or "unknown",
                            "" if bar_age is None else f" | 경과={bar_age}봉",
                        )
                    # 만료시킨 이벤트가 B라면 과거 A와 결합하지 않고 폐기합니다.
                    if incoming_stage != 0:
                        return {
                            "ok": True, "delivered": False, "expired": True,
                            "reason": f"a_to_b_expired_{expiry_reason or 'unknown'}",
                            "chain_id": chain_id,
                        }

            # stage 0 = WAIT_A. B가 먼저 와도 저장하거나 조합하지 않습니다.
            if current_stage == 0:
                if incoming_stage != 0:
                    return {
                        "ok": True, "delivered": False, "ignored": True,
                        "reason": "waiting_for_a", "chain_id": chain_id,
                    }

                direction = self._config_chain_event_direction(event)
                if direction not in {"LONG", "SHORT"}:
                    pushes.append(fired_payload)
                    for payload in pushes:
                        self._push(payload)
                    return {
                        "ok": True, "delivered": False, "ignored": True,
                        "reason": "cross_direction_missing", "chain_id": chain_id,
                    }

                bar_anchor_ts: Optional[float] = None
                if spec.max_gap_bars > 0:
                    if fresh_bar_times:
                        # A 이벤트 수신 시점의 최신 확정 CROSS_TF 봉이 A가 발생한 기준봉입니다.
                        bar_anchor_ts = float(fresh_bar_times[-1])
                    else:
                        # STAFF 일시 실패 시에도 bar-only 체인이 영구 정지하지 않도록 보수적 fallback을 저장합니다.
                        bar_anchor_ts = float(event_ts) - float(tf_seconds(spec.cross_tf))

                # A를 먼저 영속 저장한 뒤에만 B 대기 stage로 전환합니다.
                state["spec_signature"] = self._config_chain_spec_signature(spec)
                state["latest_cross"] = {
                    "ts": float(event_ts),
                    "direction": direction,
                    "token": event_token,
                    "bar_anchor_ts": bar_anchor_ts,
                }
                state["latest_fvg"] = None
                state["stage"] = 1
                state["stage_deadline"] = (
                    float(event_ts) + float(spec.max_gap_sec) if spec.max_gap_sec > 0 else None
                )
                self._save_config_chain_state_locked()
                if logging.getLogger().isEnabledFor(logging.INFO):
                    logging.info(
                        "⏱️ [Composer SPECIAL TIMED_CHAIN] A 저장 | %s | %s | %s %s%d/%d | B 유효=%s",
                        spec.name, direction, spec.cross_tf, spec.ma_family, spec.fast_period, spec.slow_period,
                        spec.expiry_label(),
                    )

                # Cross Watch는 persistent이지만 재전송해 프로세스 시작 순서/큐 유실에도 기존 복구성을 유지합니다.
                pushes.append(fired_payload)
                result = {
                    "ok": True, "delivered": False, "chain_id": chain_id,
                    "config_timed_chain": True, "stage": "WAIT_B",
                }

            # stage 1 = WAIT_B. A가 다시 와도 현재 저장된 A를 바꾸지 않고 B만 기다립니다.
            else:
                if incoming_stage == 0:
                    return {
                        "ok": True, "delivered": False, "ignored": True,
                        "reason": "waiting_for_b", "chain_id": chain_id,
                    }
                if incoming_stage < 1:
                    return {"ok": True, "delivered": False, "stale": True, "chain_id": chain_id}

                cross = state.get("latest_cross")
                deadline = state.get("stage_deadline")
                if not isinstance(cross, dict):
                    last_signature = state.get("last_pair_signature")
                    spec_signature = self._config_chain_spec_signature(spec)
                    state.clear()
                    state.update(self._default_config_chain_state())
                    state["last_pair_signature"] = last_signature
                    state["spec_signature"] = spec_signature
                    self._save_config_chain_state_locked()
                    return {
                        "ok": True, "delivered": False, "ignored": True,
                        "reason": "missing_a_state", "chain_id": chain_id,
                    }

                try:
                    cross_ts = float(cross.get("ts"))
                    if not math.isfinite(cross_ts):
                        raise ValueError("cross ts")
                    if spec.max_gap_sec > 0:
                        deadline = float(deadline)
                        if not math.isfinite(deadline):
                            raise ValueError("deadline")
                    if spec.max_gap_bars > 0:
                        bar_anchor = float(cross.get("bar_anchor_ts"))
                        if not math.isfinite(bar_anchor):
                            raise ValueError("bar anchor")
                except (TypeError, ValueError):
                    last_signature = state.get("last_pair_signature")
                    spec_signature = self._config_chain_spec_signature(spec)
                    state.clear()
                    state.update(self._default_config_chain_state())
                    state["last_pair_signature"] = last_signature
                    state["spec_signature"] = spec_signature
                    self._save_config_chain_state_locked()
                    return {
                        "ok": True, "delivered": False, "ignored": True,
                        "reason": "invalid_a_state", "chain_id": chain_id,
                    }

                bar_age = None
                if spec.max_gap_bars > 0:
                    bar_age = self._config_chain_bar_age_from_times(cross.get("bar_anchor_ts"), bar_times)
                    if bar_age is None:
                        return {
                            "ok": True, "delivered": False, "ignored": True,
                            "reason": "bar_expiry_unavailable", "chain_id": chain_id,
                        }

                # 과거에 이미 존재하던 B는 A 이후 사건이 아니므로 절대 결합하지 않습니다.
                if float(event_ts) < cross_ts:
                    return {
                        "ok": True, "delivered": False, "ignored": True,
                        "reason": "b_before_a", "chain_id": chain_id,
                    }

                # 이벤트 자체의 발생시각도 시간 expiry를 넘었으면 B로 인정하지 않습니다.
                if spec.max_gap_sec > 0 and float(event_ts) > float(deadline):
                    last_signature = state.get("last_pair_signature")
                    spec_signature = state.get("spec_signature") or self._config_chain_spec_signature(spec)
                    state.clear()
                    state.update(self._default_config_chain_state())
                    state["last_pair_signature"] = last_signature
                    state["spec_signature"] = spec_signature
                    self._save_config_chain_state_locked()
                    return {
                        "ok": True, "delivered": False, "expired": True,
                        "reason": "a_to_b_expired_time", "chain_id": chain_id,
                    }

                tf = normalize_tf((fired_payload.get("timeframes") or [""])[0])
                fvg = {"ts": float(event_ts), "tf": tf, "token": event_token}
                state["latest_fvg"] = fvg
                signature = stable_id(
                    "CFGPAIR", spec.spec_id, cross.get("direction"), cross.get("token"),
                    fvg.get("tf"), fvg.get("token"), length=24,
                )
                direction = str(cross.get("direction") or "").upper()
                duplicate = state.get("last_pair_signature") == signature
                # Trading time applies at the final alert; B advances the chain at any time.
                should_advance = direction in {"LONG", "SHORT"} and not duplicate

                # 중복 B는 A를 소비하지 않고 같은 expiry 범위 안에서 다음 B를 계속 기다립니다.
                if not should_advance:
                    self._save_config_chain_state_locked()
                    pushes.append(fired_payload)
                    result = {
                        "ok": True, "delivered": False, "chain_id": chain_id,
                        "config_timed_chain": True, "stage": "WAIT_B", "advanced": False,
                        "reason": "duplicate_b" if duplicate else "invalid_direction",
                    }
                else:
                    # 같은 메인 TIMED_CHAIN의 이전 기간제 OZ 감시는 새 setup이 오면 교체합니다.
                    for child_id, item in list(self._config_chain_active.items()):
                        if str(item.get("spec_id") or "") != spec.spec_id:
                            continue
                        pushes.append({
                            "action": "CANCEL_MANUAL", "watch_id": child_id,
                            "request_chat_id": None,
                            "validation_mode": item.get("validation_mode"),
                            "trigger_mode": item.get("trigger_mode"),
                        })
                        self._config_chain_active.pop(child_id, None)

                    final_deadline = event_ts + spec.final_window_sec
                    if spec.max_gap_sec > 0:
                        final_deadline = min(final_deadline, float(deadline))
                    state["last_pair_signature"] = signature
                    if spec.final_fvg_touch_tfs:
                        state["post_touch_armed"] = {
                            "direction": direction,
                            "expires_at": event_ts + spec.final_window_sec,
                            "completion_deadline": final_deadline,
                            "pair_signature": signature,
                        }
                        self._save_config_chain_state_locked()
                    else:
                        child_id = stable_id("OZARM:CFGCHAIN", spec.spec_id, direction, signature, length=20)
                        child_payload = {
                            "action": "MANUAL_WATCH", "watch_id": child_id,
                            "timeframes": list(spec.oz_tfs), "symbol": spec.symbol,
                            "direction": direction, "persistent": True,
                            "request_chat_id": None,
                            "validation_mode": spec.validation_mode,
                            "trigger_mode": spec.trigger_mode,
                            "source_spec_id": spec.spec_id,
                            "source_spec_ids": [spec.spec_id],
                            "source_name": spec.name,
                            "trading_times": {spec.spec_id: self._chain_trading_time(spec)},
                        }
                        active_item = dict(child_payload)
                        active_item.update({
                            "spec_id": spec.spec_id,
                            "expires_at": event_ts + spec.final_window_sec,
                            "completion_deadline": final_deadline,
                        })
                        self._config_chain_active[child_id] = active_item
                        pushes.append(child_payload)
                    if logging.getLogger().isEnabledFor(logging.INFO):
                        logging.info(
                            "⏱️ [Composer SPECIAL TIMED_CHAIN] B 성립→NEXT | %s | %s | A=%s %s%d/%d → B=%s FVG | gap=%.1fs%s | %s",
                            spec.name, direction, spec.cross_tf, spec.ma_family, spec.fast_period, spec.slow_period,
                            fvg.get("tf"), float(fvg["ts"]) - cross_ts,
                            "" if bar_age is None else f"/{bar_age}봉",
                            (
                                f"{format_duration_ko(spec.final_window_sec)} 안에 "
                                f"{'/'.join(spec.final_fvg_touch_tfs)} 동방향 FVG TOUCH 대기"
                                if spec.final_fvg_touch_tfs
                                else f"{format_duration_ko(spec.final_window_sec)} 동안 OZ"
                            ),
                        )

                    # B가 성립해 다음 단계로 넘겼으므로 이번 A→B 사이클을 소비하고 새 A를 기다립니다.
                    last_signature = state.get("last_pair_signature")
                    post_touch_armed = state.get('post_touch_armed')
                    spec_signature = state.get("spec_signature") or self._config_chain_spec_signature(spec)
                    state.clear()
                    state.update(self._default_config_chain_state())
                    state["last_pair_signature"] = last_signature
                    state['post_touch_armed'] = post_touch_armed
                    state["spec_signature"] = spec_signature
                    self._save_config_chain_state_locked()
                    pushes.append(fired_payload)
                    result = {
                        "ok": True, "delivered": False, "chain_id": chain_id,
                        "config_timed_chain": True, "stage": "WAIT_A", "advanced": True,
                    }

        for payload in pushes:
            self._push(payload)
        return result

    def _dispatch_official_timed_chain_event(self, event: dict) -> Optional[dict]:
        """공식 시간연쇄에 들어오는 런타임 이벤트의 단일 진입점입니다.

        fact-store는 시장 사실만 저장하고, 공식 시간연쇄의 stage 진행/OZ arm은
        이 경계 아래에서 처리합니다.
        """
        kind = str(event.get("kind") or "").upper()
        chain_id = str(event.get("chain_id") or "").strip()

        if kind == "GENERIC_TRIGGER" and chain_id.startswith("CFGCHAIN:"):
            return self._handle_config_chain_trigger(event)
        if kind == "FVG_TOUCH":
            return self._handle_official_timed_chain_fvg_touch(event)
        return None

    def _handle_official_timed_chain_fvg_touch(self, event: dict) -> dict:
        """pair 성립 후 지정된 동방향 FVG TOUCH를 공식 시간연쇄 다음 단계로 처리합니다."""
        completed = self._config_chain_event_ts(event, float('nan'))
        if not math.isfinite(completed):
            return {'ok': False, 'delivered': False, 'error': 'missing_completion_timestamp'}
        symbol = str(event.get("symbol") or "").strip()
        tf = normalize_tf(event.get("source_tf") or event.get("tf"))
        zone_id = str(event.get("zone_id") or "").strip()
        fvg_side = str(event.get("fvg_side") or "").upper()
        direction = str(event.get("direction") or "").upper()
        if direction not in {"LONG", "SHORT"}:
            direction = "LONG" if fvg_side == "BULL" else "SHORT" if fvg_side == "BEAR" else ""
        if not symbol or not tf or direction not in {"LONG", "SHORT"}:
            return {"ok": True, "delivered": False, "ignored": True}

        now = time.time()
        pushes: list[dict] = []
        state_changed = False
        matched_specs = 0
        armed_children = 0

        with self._lock:
            for spec_id, spec in self.official_chain_specs.items():
                if (
                    not spec.enabled or not spec.final_fvg_touch_tfs
                    or spec.symbol != symbol or tf not in spec.final_fvg_touch_tfs
                ):
                    continue
                matched_specs += 1
                state = self._config_chain_state.get(spec_id)
                armed = state.get("post_touch_armed") if isinstance(state, dict) else None
                if not isinstance(armed, dict):
                    continue
                try:
                    expires_at = float(armed.get("expires_at") or 0.0)
                except (TypeError, ValueError):
                    expires_at = 0.0
                armed_direction = str(armed.get("direction") or "").upper()
                if completed > self._config_chain_completion_deadline(armed):
                    continue
                if armed_direction != direction:
                    continue
                # The FVG touch only arms the final OZ; trading time applies when that OZ alerts.

                # 같은 spec의 이전 최종 OZ 감시는 새 유효 TOUCH가 오면 교체합니다.
                for child_id, item in list(self._config_chain_active.items()):
                    if str(item.get("spec_id") or "") != spec.spec_id:
                        continue
                    pushes.append({
                        "action": "CANCEL_MANUAL", "watch_id": child_id,
                        "request_chat_id": None,
                        "validation_mode": item.get("validation_mode"),
                        "trigger_mode": item.get("trigger_mode"),
                    })
                    self._config_chain_active.pop(child_id, None)

                pair_signature = str(armed.get("pair_signature") or "")
                child_id = stable_id(
                    "OZARM:CFGCHAIN:FVGTOUCH", spec.spec_id, direction, pair_signature, zone_id or tf, length=20
                )
                child_payload = {
                    "action": "MANUAL_WATCH", "watch_id": child_id,
                    "timeframes": list(spec.oz_tfs), "symbol": spec.symbol,
                    "direction": direction, "persistent": True,
                    "request_chat_id": None,
                    "validation_mode": spec.validation_mode,
                    "trigger_mode": spec.trigger_mode,
                    "source_spec_id": spec.spec_id,
                    "source_spec_ids": [spec.spec_id],
                    "source_name": spec.name,
                    "source_fvg_zone_id": zone_id,
                    "source_fvg_tf": tf,
                    "trading_times": {spec.spec_id: self._chain_trading_time(spec)},
                }
                active_item = dict(child_payload)
                active_item.update({
                    "spec_id": spec.spec_id,
                    # pair 자격의 남은 시간만 최종 OZ 감시에 사용합니다.
                    "expires_at": expires_at,
                    "completion_deadline": self._config_chain_completion_deadline(armed),
                })
                self._config_chain_active[child_id] = active_item
                state["post_touch_armed"] = None
                state_changed = True
                armed_children += 1
                pushes.append(child_payload)
                if logging.getLogger().isEnabledFor(logging.INFO):
                    logging.info(
                        "🎯 [Composer SPECIAL TIMED_CHAIN] 동방향 FVG TOUCH→OZ | %s | %s | FVG=%s %s | OZ=%s",
                        spec.name, direction, tf, zone_id or "-", ",".join(spec.oz_tfs),
                    )

            if state_changed:
                self._save_config_chain_state_locked()

        # OZ queue I/O는 Composer state lock 밖에서 수행합니다.
        for payload in pushes:
            self._push(payload)

        return {
            "ok": True, "delivered": False,
            "official_timed_chain": True,
            "matched_specs": matched_specs,
            "armed_children": armed_children,
        }

    def _handle_generic_trigger(self, event: dict) -> dict:
        item = canonical_watch_payload(event)
        chain_id = str(item.get("chain_id") or "").strip()
        if chain_id.startswith("CFGCHAIN:"):
            result = self._dispatch_official_timed_chain_event(item)
            return result or {"ok": True, "delivered": False, "ignored": True}

        # 저장 호환이 필요한 BAR/WONBI CLOSE suffix만 legacy 형태로 변환합니다.
        # 사전 기반 복합조건은 COMPOUND_CONDITION 공용 계약을 그대로 사용합니다.
        forwarded = dict(item)
        if chain_id:
            forwarded["watch_type"] = legacy_chain_watch_type(
                item.get("watch_type"), item.get("evaluation_mode")
            )
        return self.watch_orchestrator.handle_generic_trigger(forwarded)

    @staticmethod
    def _local_chain_epoch(value: object) -> Optional[float]:
        if value in {None, ""}:
            return None
        try:
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                out = float(value)
            else:
                stamp = pd.Timestamp(value)
                if pd.isna(stamp):
                    return None
                if stamp.tzinfo is None:
                    stamp = stamp.tz_localize("UTC")
                else:
                    stamp = stamp.tz_convert("UTC")
                out = float(stamp.timestamp())
            return out if math.isfinite(out) else None
        except Exception:
            return None

    def _compound_close_bar_times(self, targets: Iterable[dict]) -> dict[tuple[str, str], str]:
        """CLOSE 복합조건의 봉 전환 판정용 현재 진행봉 시각을 STAFF에서 읽습니다."""
        by_symbol: dict[str, set[str]] = {}
        for target in targets:
            if str(target.get("evaluation_mode") or "LIVE").upper() != "CLOSE":
                continue
            symbol = str(target.get("symbol") or "").strip()
            tf = normalize_tf(target.get("tf"))
            if symbol and tf:
                by_symbol.setdefault(symbol, set()).add(tf)
        out: dict[tuple[str, str], str] = {}
        for symbol, tf_set in by_symbol.items():
            data = self._condition_market(symbol, sorted(tf_set, key=tf_seconds), [])
            if not data:
                continue
            for tf in sorted(tf_set, key=tf_seconds):
                df = data.get(tf)
                if df is None or len(df) < 1:
                    continue
                raw_time = self._condition_row(df,-1).get("time")
                try:
                    stamp = pd.Timestamp(raw_time)
                    if pd.isna(stamp):
                        continue
                    if stamp.tzinfo is None:
                        stamp = stamp.tz_localize("UTC")
                    else:
                        stamp = stamp.tz_convert("UTC")
                    out[(symbol, tf)] = stamp.isoformat()
                except Exception:
                    continue
        return out

    def _compound_events_for_target(
        self, target: dict, close_bar_times: dict[tuple[str, str], str]
    ) -> list[dict]:
        chain_id = str(target.get("chain_id") or "").strip()
        watch_id = str(target.get("watch_id") or "").strip()
        try:
            index = int(target.get("index"))
        except (TypeError, ValueError):
            return []
        events: list[dict] = []
        with self._lock:
            chain = self.timed_chains.get(chain_id)
            if (
                chain is None or not chain.enabled or not chain.started
                or chain.order_mode not in {"FILTER", "SEQUENTIAL"}
                or index < 0 or index >= len(chain.triggers)
                or (chain.order_mode == "SEQUENTIAL" and index != int(chain.stage))
                or chain.current_watch_ids.get(str(index)) != watch_id
            ):
                self._compound_watch_state.pop(watch_id, None)
                return []
            trig = chain.triggers[index]
            condition_type, evaluation_mode = trigger_watch_contract(trig)
            if condition_type != "COMPOUND_CONDITION":
                return []
            spec = self._compound_spec_for_trigger(chain, trig)
            if spec is None:
                return []

            directions = (trig.direction,) if trig.direction in {"LONG", "SHORT"} else ("LONG", "SHORT")
            signatures = {
                direction: self._evaluate_spec_direction_locked(spec, direction)
                for direction in directions
            }
            state = self._compound_watch_state.setdefault(watch_id, {})

            if evaluation_mode == "CLOSE":
                bar_token = close_bar_times.get((chain.symbol, trig.tf))
                if not bar_token:
                    return []
                previous_bar = state.get("bar_token")
                previous_signatures = dict(state.get("bar_signatures") or {})
                if previous_bar is None:
                    state["bar_token"] = bar_token
                    state["bar_signatures"] = dict(signatures)
                    return []
                if previous_bar == bar_token:
                    state["bar_signatures"] = dict(signatures)
                    return []

                # 새 진행봉으로 넘어온 순간, 직전 봉의 마지막 관측 상태를 봉마감 결과로 확정합니다.
                for direction, signature in previous_signatures.items():
                    if not signature:
                        continue
                    events.append({
                        "kind": "GENERIC_TRIGGER",
                        "strategy": "COMPOSER",
                        "chain_id": chain.chain_id,
                        "chain_stage": index,
                        "watch_id": watch_id,
                        "watch_type": "COMPOUND_CONDITION",
                        "evaluation_mode": "CLOSE",
                        "direction": direction,
                        # 봉마감 이벤트 시각은 새 봉 시작시각(=직전 봉 마감시각)을 사용합니다.
                        "event_time": bar_token,
                        "event_id": f"COMPOUND:{watch_id}:{direction}:{previous_bar}",
                    })
                state["bar_token"] = bar_token
                state["bar_signatures"] = dict(signatures)
                return events

            active = dict(state.get("live_active") or {})
            for direction, signature in signatures.items():
                now_active = bool(signature)
                was_active = bool(active.get(direction))
                if now_active and not was_active:
                    events.append({
                        "kind": "GENERIC_TRIGGER",
                        "strategy": "COMPOSER",
                        "chain_id": chain.chain_id,
                        "chain_stage": index,
                        "watch_id": watch_id,
                        "watch_type": "COMPOUND_CONDITION",
                        "evaluation_mode": "LIVE",
                        "direction": direction,
                        "event_time": time.time(),
                        "event_id": f"COMPOUND:{watch_id}:{direction}:{time.time_ns()}",
                    })
                active[direction] = now_active
            state["live_active"] = active
        return events

    def _update_local_chain_events(self) -> None:
        """LOCAL_EVENT를 공용 복합조건 실행기 또는 SPECIAL handler에 위임합니다."""
        grouped: dict[str, list[dict]] = {}
        compound_targets: list[dict] = []
        with self._lock:
            for chain in self.timed_chains.values():
                if not chain.enabled or not chain.started:
                    continue
                if chain.order_mode == "FILTER":
                    indices = range(len(chain.triggers))
                elif chain.order_mode == "SEQUENTIAL":
                    stage = int(chain.stage)
                    indices = (stage,) if 0 <= stage < len(chain.triggers) else ()
                else:
                    continue

                for index in indices:
                    trig = chain.triggers[index]
                    condition_type, evaluation_mode = trigger_watch_contract(trig)
                    watch_id = chain.current_watch_ids.get(str(index))
                    if not watch_id:
                        continue
                    target = {
                        "chain_id": chain.chain_id,
                        "index": index,
                        "watch_id": watch_id,
                        "symbol": chain.symbol,
                        "tf": trig.tf,
                        "watch_type": condition_type,
                        "evaluation_mode": evaluation_mode,
                        "direction": trig.direction,
                        "level_side": trig.level_side,
                    }
                    if condition_type == "COMPOUND_CONDITION":
                        compound_targets.append(target)
                        continue
                    # SPECIAL local poller는 기존 FILTER 의미만 유지합니다.
                    if chain.order_mode == "FILTER":
                        handler = self._special_watch_handlers.get(condition_type)
                        if handler is not None:
                            grouped.setdefault(condition_type, []).append(target)

        close_bar_times = self._compound_close_bar_times(compound_targets)
        for target in compound_targets:
            try:
                events = self._compound_events_for_target(target, close_bar_times)
            except Exception:
                logging.exception("[Composer 복합조건] poll 실패 | %s", target.get("watch_id"))
                continue
            for event in events:
                try:
                    self._handle_generic_trigger(event)
                except Exception:
                    logging.exception("[Composer 복합조건] trigger 전달 실패 | %s", target.get("watch_id"))

        self._poll_strategy_handlers(grouped)

    def _poll_strategy_handlers(self, grouped=None) -> None:
        """Poll registered common ports; no numbered strategy dispatch."""
        grouped = grouped or {}
        for condition_type, handler in tuple(self._special_watch_handlers.items()):
            targets = grouped.get(condition_type, [])
            poll = getattr(handler, "poll", None)
            if not callable(poll):
                continue
            try:
                poll(tuple(targets))
                if logging.getLogger().isEnabledFor(logging.INFO):
                    logging.info("연쇄 상태 처리 · %s · targets=%s",condition_type,targets,
                        extra={'trace_module':condition_type})
            except Exception:
                logging.exception(
                    "[SPECIAL Watch] poll 실패 | %s | targets=%d",
                    condition_type, len(targets),
                )

    def _maintain_timed_chains(self) -> None:
        self.watch_orchestrator.maintenance()

    def _maintain_config_timed_chains(self) -> None:
        now = time.time()
        mono = time.monotonic()
        pushes: list[dict] = []
        chain_state_changed = False

        # 봉 수 expiry가 필요한 CROSS_TF만 maintenance worker의 STAFF client로 주기 조회합니다.
        with self._lock:
            bar_specs: dict[tuple[str, str], ConfigTimedChainSpec] = {}
            for spec_id, state in self._config_chain_state.items():
                spec = self.official_chain_specs.get(spec_id)
                if spec is None or spec.max_gap_bars <= 0:
                    continue
                if spec.order_mode == "UNORDERED":
                    latch = state.get("unordered_latch")
                    groups = latch.get("groups") if isinstance(latch, dict) else {}
                    has_hits = any(bool(x) for x in groups.values()) if isinstance(groups, dict) else False
                    if not has_hits:
                        continue
                elif int(state.get("stage", 0)) != 1:
                    continue
                bar_specs[(spec.symbol, spec.cross_tf)] = spec

        bar_poll_sec = float(self.config.get("CHAIN_BAR_EXPIRY_POLL_SEC", "1.0"))
        if bar_specs and mono - self._last_config_chain_bar_poll >= max(0.25, bar_poll_sec):
            refreshed: dict[tuple[str, str], tuple[float, ...]] = {}
            for key, spec in bar_specs.items():
                refreshed[key] = self._config_chain_closed_bar_times(spec, self.staff)
            with self._lock:
                for key, times in refreshed.items():
                    if times:
                        self._config_chain_bar_times_cache[key] = times
                self._last_config_chain_bar_poll = mono

        with self._lock:
            # SEQUENTIAL은 A 저장 후 B 대기를, UNORDERED는 각 latch hit를 독립 expiry합니다.
            for spec_id, state in self._config_chain_state.items():
                spec = self.official_chain_specs.get(spec_id)
                if spec is None:
                    continue
                post_touch = state.get("post_touch_armed")
                if isinstance(post_touch, dict):
                    try:
                        touch_expires = float(post_touch.get("expires_at") or 0.0)
                    except (TypeError, ValueError):
                        touch_expires = 0.0
                    if now > touch_expires and not post_touch.get('deadline_elapsed'):
                        post_touch['deadline_elapsed'] = True
                        chain_state_changed = True
                        if logging.getLogger().isEnabledFor(logging.INFO):
                            logging.info("⌛ [Composer SPECIAL TIMED_CHAIN] FVG TOUCH 시간창 경과 · 완료시각 판정 상태 보존 | %s", spec_id)
                if spec.order_mode == "UNORDERED":
                    latch = state.get("unordered_latch")
                    if not isinstance(latch, dict):
                        latch = UnorderedConditionLatch.new_state()
                        state["unordered_latch"] = latch
                    # Event-time pruning belongs to the callback. Wall time
                    # cannot prove that an on-time completion will not arrive.
                    changed = False
                    if spec.max_gap_bars > 0:
                        times = self._config_chain_bar_times_cache.get((spec.symbol, spec.cross_tf), ())
                        if times:
                            changed = self._prune_config_chain_unordered_bars_locked(spec, latch, times) or changed
                    if changed:
                        chain_state_changed = True
                        if logging.getLogger().isEnabledFor(logging.INFO):
                            logging.info(
                                "⌛ [Composer SPECIAL TIMED_CHAIN] 순서무관 조건 expiry 정리 | %s | 남은=%s",
                                spec_id, {k: sorted(v) for k, v in (latch.get("groups") or {}).items()},
                            )
                    continue
                if int(state.get("stage", 0)) != 1:
                    continue
                cross = state.get("latest_cross")
                bar_age = None
                if spec.max_gap_bars > 0 and isinstance(cross, dict):
                    times = self._config_chain_bar_times_cache.get((spec.symbol, spec.cross_tf), ())
                    bar_age = self._config_chain_bar_age_from_times(cross.get("bar_anchor_ts"), times)
                expired, expiry_reason = self._config_chain_expiry_status(spec, state, now, bar_age)
                if not expired:
                    continue

                if not state.get('deadline_elapsed'):
                    state['deadline_elapsed'] = True
                    chain_state_changed = True

            if chain_state_changed:
                self._save_config_chain_state_locked()

            # Keep official child identities/deadlines for delayed FINAL_ALERTs.
            # Completion timestamps, not maintenance scheduling, decide expiry.

            refresh_sec = float(self.config.get("CHAIN_REFRESH_SEC", DEFAULT_CHAIN_REFRESH_SEC))
            if mono - self._last_config_chain_refresh >= max(5.0, refresh_sec):
                for spec in self.official_chain_specs.values():
                    pushes.extend(self._config_chain_trigger_payloads_locked(spec))
                for item in self._config_chain_active.values():
                    payload = {k: v for k, v in item.items()
                               if k not in {"expires_at", "spec_id", "completion_deadline", "spec_signature"}}
                    pushes.append(payload)
                self._last_config_chain_refresh = mono

        for payload in pushes:
            self._push(payload)

    # -------------------------------
    # external natural-language aliases
    # -------------------------------













    # -------------------------------
    # natural-language private strategy parser
    # -------------------------------
    def _allowed_symbols(self) -> list[str]:
        from symbol_settings import configured_symbols
        values = list(configured_symbols(self.config))
        values.extend(split_csv(self.config.get("ACTIVE_SYMBOLS")))
        with self._lock:
            values.extend(s.symbol for s in self.official_specs.values())
            values.extend(s.symbol for s in self.official_chain_specs.values())
            values.extend(s.symbol for s in self.manual_specs.values())
        return list(dict.fromkeys(x for x in values if x))

















    def _add_private_spec(self, spec: StrategySpec) -> None:
        with self._lock:
            self._remember_watch_command(spec.spec_id, spec.owner_chat_id, "PRIVATE", "PRIVATE")
            self.manual_specs[spec.spec_id] = spec
            self._save_private_state_locked()
            self._subscription_dirty = True
        title = "✅ 조건알림 등록" if spec.final_action == "NOTIFY" else "✅ 개인전략 등록"
        self.send_telegram(title + "\n" + spec.summary(), spec.owner_chat_id,
                           watch_id=spec.spec_id, watch_registration=True)
        if logging.getLogger().isEnabledFor(logging.INFO):
            logging.info("🟣 [Composer 개인전략] 등록 | %s | %s", spec.spec_id, spec.summary())

    def _reset_owner(self, owner_chat_id: str) -> int:
        with self._lock:
            keys = [k for k, s in self.manual_specs.items() if s.owner_chat_id == owner_chat_id]
            chain_keys = [k for k, c in self.timed_chains.items() if c.owner_chat_id == owner_chat_id]
            fvg_watch_keys = [
                k for k, w in self.fvg_created_watches.items() if w.request_chat_id == owner_chat_id
            ]
            for key in keys:
                self.manual_specs.pop(key, None)
                for direction in ("LONG", "SHORT"):
                    self._last_signatures.pop((key, direction), None)
            active_changed = False
            for child_id, payload in list(self._active_children.items()):
                if str(payload.get("request_chat_id") or "") == owner_chat_id:
                    self._active_children.pop(child_id, None)
                    active_changed = True
            if active_changed:
                self._save_active_children_state_locked()
            for key in chain_keys:
                self.timed_chains.pop(key, None)
            for key in fvg_watch_keys:
                self.fvg_created_watches.pop(key, None)
            self._save_private_state_locked()
            self._save_timed_chain_state_locked()
            self._save_fvg_created_watch_state_locked()
            self._subscription_dirty = True
        with self._lock:
            for link in self._watch_message_links.values():
                if link.get("owner_chat_id") == owner_chat_id:
                    link["status"] = "cancelled"
            self._save_private_state_locked()
        # monitor_OZ의 owner scoped reset. 공식/다른 사용자에는 영향 없음.
        self._push({"action": "RESET_ALL", "request_chat_id": owner_chat_id})
        return len(keys) + len(chain_keys) + len(fvg_watch_keys)

    def _list_owner(self, owner_chat_id: str) -> None:
        with self._lock:
            specs = [s for s in self.manual_specs.values() if s.owner_chat_id == owner_chat_id]
            chains = [c for c in self.timed_chains.values() if c.owner_chat_id == owner_chat_id]
            fvg_watches = [w for w in self.fvg_created_watches.values() if w.request_chat_id == owner_chat_id]
        if not specs and not chains and not fvg_watches:
            self.send_telegram("ℹ️ 등록된 개인전략/시간연쇄가 없습니다.", owner_chat_id)
            return
        rows = [f"• 전략 {idx}. {s.summary()}" for idx, s in enumerate(specs, 1)]
        rows.extend(f"• 시간연쇄 {idx}. {c.summary()}" for idx, c in enumerate(chains, 1))
        rows.extend(f"• FVG 생성감시 {idx}. {w.symbol} · {w.label()}" for idx, w in enumerate(fvg_watches, 1))
        self.send_telegram("📋 개인전략/시간연쇄\n" + "\n".join(rows), owner_chat_id)
















    def _secretary(self) -> KimSecretary:
        secretary = getattr(self, '_language_service', None)
        if secretary is None or secretary.command_interpreter is not self.command_interpreter:
            secretary = self._language_service = KimSecretary(self.command_interpreter, self.config)
        else:
            secretary.update_config(self.config)
        return secretary

    @staticmethod
    def _condition_from_descriptor(raw: dict, default_tf: str) -> ConditionSpec:
        # Runtime conversion uses the same contract as language interpretation.
        return condition_from_descriptor(raw, default_tf)

    def _parse_canonical_ma_watch(self, text: str, owner_chat_id: str) -> Optional[dict]:
        # Compatibility entry point for the existing MA integration boundary.
        return self._secretary()._parse_canonical_ma_watch(text, owner_chat_id)

    def _apply_secretary_command(self, command: SecretaryCommand, owner: str,
                                 reply_to_message_id: Optional[int] = None) -> None:
        if command.kind == 'IGNORE':
            return
        if command.kind == 'CANCEL_REPLY':
            if owner:
                self._cancel_watch_reply(owner, self._telegram_message_id(reply_to_message_id))
        elif command.kind == 'RESET':
            count = self._reset_owner(owner)
            self.send_telegram(f'♻️ 개인전략 리셋 완료 · {count}건', owner)
        elif command.kind == 'LIST':
            self._list_owner(owner)
        elif command.kind in {'WATCH', 'QUERY'}:
            self._push(command.value)
        elif command.kind == 'REGISTER_STRATEGY':
            self._add_private_spec(command.value)
        elif command.kind == 'REGISTER_CHAIN':
            self._add_timed_chain(command.value)
        elif command.kind == 'FVG_WATCH':
            self._add_fvg_created_watch(command.value)
        elif command.kind == 'ERROR':
            self.send_telegram(command.message, owner)
        elif command.kind == 'EXTERNAL_NORMALIZE':
            # The host alone calls AI; its reply re-enters this same parser.
            from event_commands import ExternalCommandPending
            raise ExternalCommandPending(command.value)
        else:
            raise ValueError(f'알 수 없는 김비서 명령: {command.kind}')

    def handle_command(
        self, text: str, incoming_chat_id: Optional[str] = None, _gemini_retry: bool = False,
        *, message_id: Optional[int] = None, reply_to_message_id: Optional[int] = None,
    ) -> None:
        owner = str(incoming_chat_id or self.chat_id or '').strip()
        previous = getattr(self._command_context, 'current', None)
        context = previous if _gemini_retry and previous else {
            'owner_chat_id': owner, 'message_id': self._telegram_message_id(message_id),
            'original_text': str(text or ''),
        }
        self._command_context.current = context
        try:
            command = self._secretary().parse(text, owner, external_retry=_gemini_retry)
            self._apply_secretary_command(command, owner, reply_to_message_id)
        finally:
            self._command_context.current = previous

    # -------------------------------
    # maintenance / server loop
    # -------------------------------







# ---------------------------------------------------------------------

# ---------------------------------------------------------------------


