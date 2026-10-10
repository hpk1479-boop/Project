"""Kim Secretary: language interpretation without engine state or effects.

The existing deterministic parsers return command contracts. They cannot
register watches, read facts, send messages or call an external provider.
"""
from __future__ import annotations
from typing import Iterable, Optional
import math
import re
from domain_clock import time
from command_interpreter import CommandInterpreter, OZ_BASE_TFS, normalize_tf
from moses_language import syntax_literal
from command_models import (ConditionSpec, StrategySpec, CONDITION_DATA_TFS,
                            _oz_trigger_mode, make_chain_trigger,
                            is_filter_watch_trigger, trigger_watch_contract,
                            condition_from_descriptor)
from watch_orchestrator import ChainTriggerSpec, TimedChainSpec, stable_id
from secretary_routing import CommandRouter

class KimSecretary(CommandRouter):
    """Use the shared dictionary and a small set of command/session settings."""
    CONFIG_KEYS = ('CHAIN_DEFAULT_OZ_TF', 'LONDON', 'MAIN_LONDON',
                   'NEWYORK', 'MAIN_NEWYORK')

    def __init__(self, interpreter: CommandInterpreter, config=None):
        self.command_interpreter = interpreter
        self.update_config(config)

    def update_config(self, config=None):
        values = config or {}
        self.config = {key: values[key] for key in self.CONFIG_KEYS if key in values}

    @staticmethod
    def _duration_matches(text: str) -> list[tuple[int, int, float]]:
        return CommandInterpreter.duration_matches(text)


    def _parse_start_at(self, text: str) -> Optional[dict]:
        return self.command_interpreter.parse_start_at(text)


    @staticmethod
    def _overlaps_span(start: int, end: int, spans: Iterable[tuple[int, int, object]]) -> bool:
        return CommandInterpreter.overlaps_span(start, end, spans)


    def _trigger_tf_before(
        self,
        text: str,
        pos: int,
        duration_spans: list[tuple[int, int, float]],
        fallback: str = "",
    ) -> str:
        occ = [
            x for x in self._tf_occurrences(text)
            if x[1] <= pos and not self._overlaps_span(x[0], x[1], duration_spans)
        ]
        tf = occ[-1][2] if occ else normalize_tf(fallback)
        if not tf:
            tf = self._command_default_tf()
        return tf


    def _parse_chain_triggers(
        self,
        text: str,
        end_pos: int,
        durations: list[tuple[int, int, float]],
    ) -> list[tuple[int, int, ChainTriggerSpec]]:
        low = str(text or "").lower()
        candidates: list[tuple[int, int, ChainTriggerSpec]] = []

        # EMA/HMA Cross. family 생략 시 EMA이며, 자연어 family 별칭은 공통 언어사전에서 먼저 canonical로 정규화됩니다.
        # 예: EMA50/200 골크, 50 200지수 골크나면, 지수 50 200 골크.
        family_alias = syntax_literal('parse_chain_triggers_grammar_75')
        ma_re = re.compile(
            f"{syntax_literal('parse_chain_triggers_regex_part_77_0')}{family_alias}{syntax_literal('parse_chain_triggers_regex_part_77_2')}{family_alias}{syntax_literal('parse_chain_triggers_regex_part_77_4')}",
            re.I,
        )

        def _ma_family(value: Optional[str]) -> Optional[str]:
            if value is None:
                return None
            token = re.sub(syntax_literal('ma_family_pattern_1'), "", str(value).lower())
            if token in {"hma", syntax_literal('ma_family_term_1'), syntax_literal('ma_family_term_2')}:
                return "HMA"
            if token in {"ema", syntax_literal('ma_family_term_3'), syntax_literal('ma_family_term_4'), syntax_literal('ma_family_term_5')}:
                return "EMA"
            return None

        previous_tf = ""
        for m in ma_re.finditer(low, 0, end_pos):
            tf = self._trigger_tf_before(low, m.start(), durations, previous_tf)
            word = re.sub(syntax_literal('parse_chain_triggers_pattern_4'), "", m.group("cross").lower())
            direction = "LONG" if word == syntax_literal('parse_chain_triggers_term_7') else "SHORT" if word == syntax_literal('parse_chain_triggers_term_18') else None
            prefix_family = _ma_family(m.group("prefix"))
            suffix_family = _ma_family(m.group("suffix"))
            if prefix_family and suffix_family and prefix_family != suffix_family:
                raise ValueError("MA family 표현이 서로 충돌합니다")
            family = prefix_family or suffix_family or "EMA"
            trig = ChainTriggerSpec(
                watch_type=f"{family}_CROSS", tf=tf, direction=direction, ma_family=family,
                fast_period=int(m.group("fast")), slow_period=int(m.group("slow")),
            )
            trig.validate()
            candidates.append((m.start(), m.end(), trig))
            previous_tf = tf

        # 이평 family/period를 전혀 쓰지 않고 "골크/데크 나면 알려줘"라고 하면
        # 시스템 공통 기본값인 EMA50/200 Cross로 해석합니다.
        cross_only_re = re.compile(
            syntax_literal('parse_chain_triggers_pattern_1'),
            re.I,
        )
        existing_ma_spans = [(a, b) for a, b, t in candidates if t.watch_type in {"EMA_CROSS", "HMA_CROSS"}]
        for m in cross_only_re.finditer(low, 0, end_pos):
            if any(m.start() < b and m.end() > a for a, b in existing_ma_spans):
                continue
            prefix = low[max(0, m.start() - 32):m.start()]
            # 시간봉 숫자는 MA period가 아닙니다. TF 표현을 지운 뒤 남는 숫자/MA family가 있을 때만
            # 불완전한 명시로 보고 기본값 적용을 막습니다.
            ma_probe = re.sub(syntax_literal('parse_chain_triggers_pattern_5'), " ", prefix, flags=re.I)
            if re.search(syntax_literal('parse_chain_triggers_pattern_7'), ma_probe) or re.search(family_alias, ma_probe, re.I):
                continue
            tf = self._trigger_tf_before(low, m.start(), durations, previous_tf)
            word = re.sub(syntax_literal('parse_chain_triggers_pattern_6'), "", m.group(0).lower())
            direction = "LONG" if word == syntax_literal('parse_chain_triggers_term_8') else "SHORT" if word == syntax_literal('parse_chain_triggers_term_19') else None
            default_family = str(self._command_default("ma_family", "EMA")).upper()
            default_fast = int(self._command_default("ma_fast", 50))
            default_slow = int(self._command_default("ma_slow", 200))
            trig = ChainTriggerSpec(
                watch_type=f"{default_family}_CROSS", tf=tf, direction=direction, ma_family=default_family,
                fast_period=default_fast, slow_period=default_slow,
            )
            trig.validate()
            candidates.append((m.start(), m.end(), trig))
            previous_tf = tf

        # FVG는 '생성/신규' 의미가 명시된 경우에만 FVG_NEW로 취급합니다.
        # 공통 언어사전이 정상 로드되면 '신규'는 '생성'으로 정규화되지만,
        # 외부 alias 파일 누락/지연 시에도 같은 명령이 동작하도록 파서에서도 직접 허용합니다.
        # 기존 FVG=터치 의미는 건드리지 않습니다.
        for m in re.compile(r"fvg", re.I).finditer(low, 0, end_pos):
            local = low[max(0, m.start() - 16):min(end_pos, m.end() + 18)]
            if not any(word in local for word in (syntax_literal('parse_chain_triggers_term_23'), syntax_literal('parse_chain_triggers_term_24'))):
                continue
            tf = self._trigger_tf_before(low, m.start(), durations, previous_tf)
            direction = "LONG" if syntax_literal('parse_chain_triggers_term_9') in local else "SHORT" if syntax_literal('parse_chain_triggers_term_20') in local else None
            trig = ChainTriggerSpec("FVG_NEW", tf, direction=direction)
            trig.validate()
            candidates.append((m.start(), m.end(), trig))
            previous_tf = tf

        # 사전에 정의된 복합조건 매크로. 김매니저는 이름의 의미를 알지 않고
        # primitive ConditionSpec 조합만 보존/판정합니다.
        close_words = (syntax_literal('parse_chain_triggers_term_1'), syntax_literal('parse_chain_triggers_term_2'), syntax_literal('parse_chain_triggers_term_3'), syntax_literal('parse_chain_triggers_term_4'), syntax_literal('parse_chain_triggers_term_5'))
        for macro_hit in self._condition_macro_matches(low[:end_pos]):
            start, end = int(macro_hit["start"]), int(macro_hit["end"])
            tf = self._trigger_tf_before(low, start, durations, previous_tf)
            direction = self._macro_direction_near(low, start, end)
            close_local = low[max(0, start - 8):min(end_pos, end + 34)]
            evaluation_mode = "CLOSE" if any(x in close_local for x in close_words) else "LIVE"
            expanded = self._expand_condition_macro(
                str(macro_hit["name"]), tf, direction or "AUTO"
            )
            trig = make_chain_trigger(
                "COMPOUND_CONDITION", tf,
                evaluation_mode=evaluation_mode,
                direction=direction,
                condition_name=str(expanded["name"]),
                condition_combination=str(expanded.get("combination") or "ALL"),
                condition_specs=tuple(dict(x) for x in expanded.get("conditions") or ()),
            )
            trig.validate()
            candidates.append((start, end, trig))
            previous_tf = tf

        # STAFF MA 자연어도 공용 COMPOUND_CONDITION 계약으로 변환합니다.
        # LIVE/CLOSE는 조건 종류와 분리하며, 봉마감 표현이 없으면 기본 LIVE입니다.
        for hit in self._ma_watch_condition_matches(low[:end_pos]):
            start, end = int(hit["start"]), int(hit["end"])
            tf = self._trigger_tf_before(low, start, durations, previous_tf)
            close_local = low[max(0, start - 16):min(end_pos, end + 36)]
            evaluation_mode = "CLOSE" if any(x in close_local for x in close_words) else "LIVE"
            descriptor = dict(hit["descriptor"])
            descriptor["tf"] = tf
            trig = make_chain_trigger(
                "COMPOUND_CONDITION", tf, evaluation_mode=evaluation_mode,
                direction=str(hit.get("direction") or "") or None,
                condition_name=str(hit["name"]),
                condition_combination="ALL",
                condition_specs=(descriptor,),
            )
            trig.validate()
            candidates.append((start, end, trig))
            previous_tf = tf

        # 원비는 알림/연쇄 문맥에서 그 자체가 밴드 터치를 뜻합니다.
        # "하단 원비 알려줘"와 "하단 원비 터치 알려줘"를 동일하게 처리합니다.
        for m in re.compile(syntax_literal('parse_chain_triggers_term_10'), re.I).finditer(low, 0, end_pos):
            side_local = low[max(0, m.start() - 14):min(end_pos, m.end() + 8)]
            close_local = low[max(0, m.start() - 8):min(end_pos, m.end() + 34)]
            tf = self._trigger_tf_before(low, m.start(), durations, previous_tf)
            if syntax_literal('parse_chain_triggers_term_6') in side_local:
                level_side, direction = "LOW", "LONG"
            elif syntax_literal('parse_chain_triggers_term_11') in side_local:
                level_side, direction = "HIGH", "SHORT"
            else:
                level_side, direction = "BOTH", None
            evaluation_mode = "CLOSE" if any(x in close_local for x in close_words) else "LIVE"
            trig = make_chain_trigger(
                "WONBI_TOUCH", tf, evaluation_mode=evaluation_mode,
                direction=direction, level_side=level_side,
            )
            trig.validate()
            candidates.append((m.start(), m.end(), trig))
            previous_tf = tf

        # Percentile OUT→IN을 OUT보다 먼저 잡습니다.
        occupied: list[tuple[int, int]] = []
        out_in_re = re.compile(syntax_literal('parse_chain_triggers_pattern_2'), re.I)
        for m in out_in_re.finditer(low, 0, end_pos):
            local = low[max(0, m.start() - 12):min(end_pos, m.end() + 12)]
            tf = self._trigger_tf_before(low, m.start(), durations, previous_tf)
            side = "LOW" if syntax_literal('parse_chain_triggers_term_12') in local else "HIGH" if syntax_literal('parse_chain_triggers_term_21') in local else "BOTH"
            direction = "LONG" if side == "LOW" else "SHORT" if side == "HIGH" else None
            trig = ChainTriggerSpec("PERCENTILE_OUT_IN", tf, direction=direction, level_side=side)
            trig.validate()
            candidates.append((m.start(), m.end(), trig)); occupied.append((m.start(), m.end())); previous_tf = tf
        for m in re.compile(syntax_literal('parse_chain_triggers_pattern_8'), re.I).finditer(low, 0, end_pos):
            if any(m.start() < b and m.end() > a for a, b in occupied):
                continue
            local = low[max(0, m.start() - 12):min(end_pos, m.end() + 12)]
            tf = self._trigger_tf_before(low, m.start(), durations, previous_tf)
            side = "LOW" if syntax_literal('parse_chain_triggers_term_13') in local else "HIGH" if syntax_literal('parse_chain_triggers_term_22') in local else "BOTH"
            direction = "LONG" if side == "LOW" else "SHORT" if side == "HIGH" else None
            trig = ChainTriggerSpec("PERCENTILE_OUT", tf, direction=direction, level_side=side)
            trig.validate()
            candidates.append((m.start(), m.end(), trig)); previous_tf = tf

        # 전일 고가/저가 터치.
        for aliases, side, direction in (
            ((syntax_literal('parse_chain_triggers_term_14'),), "HIGH", "SHORT"),
            ((syntax_literal('parse_chain_triggers_term_15'),), "LOW", "LONG"),
        ):
            pos = self._keyword_pos(low[:end_pos], aliases)
            if pos >= 0:
                tail = low[pos:min(end_pos, pos + 30)]
                if syntax_literal('parse_chain_triggers_term_16') in tail:
                    tf = self._trigger_tf_before(low, pos, durations, previous_tf)
                    trig = ChainTriggerSpec("PREV_DAY_TOUCH", tf, direction=direction, level_side=side)
                    trig.validate(); candidates.append((pos, pos + 4, trig)); previous_tf = tf

        # 단독 봉마감 이벤트. 이미 CLOSE 복합조건이 같은 봉마감 표현을 소비했다면 BAR를 중복 생성하지 않습니다.
        close_re = re.compile(syntax_literal('parse_chain_triggers_pattern_3'), re.I)
        for m in close_re.finditer(low, 0, end_pos):
            context = low[max(0, m.start() - 36):m.start()]
            if syntax_literal('parse_chain_triggers_term_17') in context or self._condition_macro_matches(context):
                continue
            if any(
                trigger_watch_contract(existing) == ("COMPOUND_CONDITION", "CLOSE")
                and start - 16 <= m.start()
                and m.end() <= end + 36
                for start, end, existing in candidates
            ):
                continue
            tf = self._trigger_tf_before(low, m.start(), durations, previous_tf)
            trig = make_chain_trigger("BAR", tf, evaluation_mode="CLOSE")
            trig.validate()
            candidates.append((m.start(), m.end(), trig))
            previous_tf = tf

        # 동일 구간이 중복 해석되는 것을 막고 문장 순으로 정렬합니다.
        candidates.sort(key=lambda x: (x[0], x[1]))
        dedup: list[tuple[int, int, ChainTriggerSpec]] = []
        seen = set()
        for start, end, trig in candidates:
            condition_type, evaluation_mode = trigger_watch_contract(trig)
            key = (start, condition_type, evaluation_mode, trig.tf, trig.direction, trig.level_side, trig.fast_period, trig.slow_period)
            if key in seen:
                continue
            seen.add(key)
            dedup.append((start, end, trig))
        return dedup


    def _explicit_oz_tfs_for_chain(
        self,
        text: str,
        zone_start: int,
        oz_pos: int,
        durations: list[tuple[int, int, float]],
    ) -> tuple[str, ...]:
        low = str(text or "").lower()
        zone = low[zone_start:oz_pos]
        if syntax_literal('explicit_oz_tfs_for_chain_term_1') in zone:
            return OZ_BASE_TFS
        found = []
        for start, end, tf in self._tf_occurrences(low):
            if start < zone_start or end > oz_pos or tf not in OZ_BASE_TFS:
                continue
            if self._overlaps_span(start, end, durations):
                continue
            found.append(tf)
        return tuple(dict.fromkeys(found))


    def _parse_scheduled_generic_chain_local(self, text: str, owner_chat_id: str) -> Optional[TimedChainSpec]:
        """절대 시작시각이 붙은 단일 Generic Watch를 예약 Chain으로 변환합니다."""
        clean = str(text or "").strip()
        low = clean.lower()
        if syntax_literal('parse_scheduled_generic_chain_local_term_1') in low or syntax_literal('parse_scheduled_generic_chain_local_term_2') not in low:
            return None
        start_info = self._parse_start_at(clean)
        if start_info is None:
            return None
        durations = self._duration_matches(clean)
        triggers_raw = self._parse_chain_triggers(clean, len(clean), durations)
        if len(triggers_raw) != 1:
            return None
        trig = triggers_raw[0][2]
        trig.validate()
        symbol = self._parse_symbol(clean)
        if not symbol:
            return None
        chain = TimedChainSpec(
            chain_id=f"SCHEDULE:{owner_chat_id}:{time.time_ns()}",
            owner_chat_id=owner_chat_id,
            symbol=symbol,
            triggers=(trig,),
            final_action="NOTIFY",
            final_watch_persistent=False,
            start_at=float(start_info["timestamp"]),
            started=False,
        )
        chain.validate()
        return chain


    def _parse_oz_watch_chain_local(self, text: str, owner_chat_id: str) -> Optional[TimedChainSpec]:
        """OZ→OZ 직렬 Watch 또는 절대시각 예약 OZ를 해석합니다.

        예:
          - 15분 무지성 브레이커 올존에서 1분 올존 알려줘
          - 오후 2시부터 15분 무지성 브레이커 올존 감시해

        각 `올존` 앞의 TF/무지성/브레이커/방향은 그 단계에만 귀속합니다.
        """
        clean = str(text or "").strip()
        low = clean.lower()
        oz_matches = list(re.finditer(syntax_literal('parse_oz_watch_chain_local_term_1'), low, re.I))
        start_info = self._parse_start_at(clean)
        if len(oz_matches) < 2 and start_info is None:
            return None
        if not oz_matches:
            return None
        if syntax_literal('parse_oz_watch_chain_local_term_2') not in low and syntax_literal('parse_oz_watch_chain_local_term_3') not in low:
            return None

        symbol = self._parse_symbol(clean)
        if not symbol:
            return None

        durations = self._duration_matches(clean)
        occurrences = self._tf_occurrences(clean)

        def _tf_for_segment(seg_start: int, oz_start: int) -> str:
            found = [
                tf for a, b, tf in occurrences
                if a >= seg_start and b <= oz_start
                and not self._overlaps_span(a, b, durations)
            ]
            tf = found[-1] if found else self._command_default_tf()
            if tf not in OZ_BASE_TFS:
                raise ValueError(
                    f"지원하지 않는 올존 시간봉입니다: {tf} "
                    f"(지원: {','.join(OZ_BASE_TFS)})"
                )
            return tf

        def _profile(segment: str) -> tuple[Optional[str], str, str]:
            direction = self._parse_oz_direction(segment)
            vm = "BLIND" if syntax_literal('profile_term_1') in segment.lower() else "NORMAL"
            tm = _oz_trigger_mode(segment)
            return direction, vm, tm

        triggers: list[ChainTriggerSpec] = []
        prev_end = 0
        for m in oz_matches[:-1]:
            segment = clean[prev_end:m.end()]
            tf = _tf_for_segment(prev_end, m.start())
            direction, vm, tm = _profile(segment)
            trig = ChainTriggerSpec(
                "OZ_ALERT", tf, direction=direction,
                validation_mode=vm, trigger_mode=tm,
            )
            trig.validate()
            triggers.append(trig)
            prev_end = m.end()

        final_m = oz_matches[-1]
        final_segment = clean[prev_end:final_m.end()]
        final_tf = _tf_for_segment(prev_end, final_m.start())
        direction, validation_mode, trigger_mode = _profile(final_segment)
        if direction is None and triggers and triggers[-1].direction in {"LONG", "SHORT"}:
            direction = triggers[-1].direction

        # 마지막 OZ 앞에 명시된 `N분/시간 동안`은 최종 Watch의 유효시간입니다.
        final_durations = [
            sec for a, b, sec in durations
            if a >= prev_end and b <= final_m.start()
        ]
        final_window_sec = float(final_durations[-1]) if final_durations else None
        final_watch_persistent = bool(final_window_sec is not None or syntax_literal('parse_oz_watch_chain_local_term_4') in low)

        chain = TimedChainSpec(
            chain_id=f"OZCHAIN:{owner_chat_id}:{time.time_ns()}",
            owner_chat_id=owner_chat_id,
            symbol=symbol,
            triggers=tuple(triggers),
            final_action="OZ",
            final_window_sec=final_window_sec,
            oz_tfs=(final_tf,),
            direction=direction,
            validation_mode=validation_mode,
            trigger_mode=trigger_mode,
            final_watch_persistent=final_watch_persistent,
            start_at=(float(start_info["timestamp"]) if start_info else None),
            started=(start_info is None),
        )
        chain.validate()
        return chain


    def _parse_timed_chain_local(self, text: str, owner_chat_id: str) -> Optional[TimedChainSpec]:
        clean = str(text or "").strip()
        low = clean.lower()
        is_unordered = any(x in low for x in (
            syntax_literal('parse_timed_chain_local_term_6'), syntax_literal('parse_timed_chain_local_term_7'), syntax_literal('parse_timed_chain_local_term_8'), syntax_literal('parse_timed_chain_local_term_9'), syntax_literal('parse_timed_chain_local_term_10'), syntax_literal('parse_timed_chain_local_term_11'),
        ))

        # 범용 CANCEL_ON shorthand. `역크로스나면 패스/취소`는 본 조건으로 파싱하지 않고
        # 첫 방향성 MA Cross의 정확한 반대 Cross를 무효화 조건으로 자동 생성합니다.
        cancel_re = re.compile(
            syntax_literal('parse_timed_chain_local_pattern_1'),
            re.I,
        )
        cancel_matches = list(cancel_re.finditer(clean))
        has_opposite_cross_cancel = bool(cancel_matches)
        parse_clean_chars = list(clean)
        for mm in cancel_matches:
            for pos in range(mm.start(), mm.end()):
                parse_clean_chars[pos] = " "
        parse_clean = "".join(parse_clean_chars)

        durations = self._duration_matches(clean)
        oz_pos = low.rfind(syntax_literal('parse_timed_chain_local_term_1'))
        parse_end = oz_pos if oz_pos >= 0 else len(low)
        triggers_raw = self._parse_chain_triggers(parse_clean, parse_end, durations)
        now_mode = syntax_literal('parse_timed_chain_local_term_2') in low

        # duration 없이도 단독 COMPOUND_CONDITION 알림은 1단계 SEQUENTIAL Watch로 처리합니다.
        # OZ 문장에서는 CLOSE 조건만 이 경로를 사용해 봉마감 의미를 보존하고,
        # 일반 LIVE OZ 조건은 기존 StrategySpec 경로를 그대로 사용합니다.
        single_compound = False
        single_compound_mode = ""
        if len(triggers_raw) == 1:
            condition_type, single_compound_mode = trigger_watch_contract(triggers_raw[0][2])
            single_compound = condition_type == "COMPOUND_CONDITION"
        direct_compound_notify = bool(
            not durations and single_compound and oz_pos < 0 and syntax_literal('parse_timed_chain_local_term_4') in low
        )
        direct_compound_close_oz = bool(
            not durations and single_compound and oz_pos >= 0 and single_compound_mode == "CLOSE"
        )
        direct_compound = direct_compound_notify or direct_compound_close_oz

        if not durations and not is_unordered and not has_opposite_cross_cancel and not direct_compound:
            return None

        # FILTER는 각 이벤트 자체에 독립 유효시간을 부여합니다.
        # 기존 SEQUENTIAL/UNORDERED 문법을 바꾸지 않기 위해 봉마감/복합조건 이벤트가 있거나,
        # 논리 연결어(그리고/혹은/또는)와 모든 조건별 duration이 명시된 경우에만 진입합니다.
        post_duration_by_index: dict[int, float] = {}
        for idx, (_start, end, _trig) in enumerate(triggers_raw):
            boundary = triggers_raw[idx + 1][0] if idx < len(triggers_raw) - 1 else parse_end
            found = [sec for a, b, sec in durations if a >= end and b <= boundary]
            if found:
                post_duration_by_index[idx] = float(found[-1])
        # 기존 FILTER 의미는 유지하되 legacy suffix 대신 condition + evaluation_mode로 판정합니다.
        # 복합조건은 LIVE/CLOSE 모두 FILTER, BAR는 CLOSE 이벤트, WONBI는 CLOSE일 때만 FILTER입니다.
        has_filter_event = any(
            is_filter_watch_trigger(trig)
            for _a, _b, trig in triggers_raw
        )
        has_logic_connector = bool(re.search(syntax_literal('parse_timed_chain_local_pattern_2'), low[:parse_end], re.I))
        all_have_validity = bool(triggers_raw) and len(post_duration_by_index) == len(triggers_raw)
        is_filter = (not is_unordered) and (not direct_compound) and (
            has_filter_event or (has_logic_connector and all_have_validity)
        )

        # 시간연쇄/순서무관/유효시간 필터로 볼 만한 구조가 아니면 기존 개인전략 파서에게 넘깁니다.
        if is_unordered:
            if len(triggers_raw) < 2:
                return None
        elif is_filter:
            if not triggers_raw:
                return None
            missing_validity = [idx for idx in range(len(triggers_raw)) if idx not in post_duration_by_index]
            if missing_validity:
                raise ValueError("조건 유효시간 필터는 각 조건 뒤에 'N분/시간 동안'을 지정해 주세요")
        elif not triggers_raw and not (now_mode and oz_pos >= 0):
            return None

        symbol = self._parse_symbol(clean)
        if not symbol:
            return None

        # SEQUENTIAL: 다음 조건까지 deadline / UNORDERED: 공통 latch 창 / FILTER: 조건별 독립 유효시간.
        triggers: list[ChainTriggerSpec] = []
        unordered_windows: list[float] = []
        for idx, (start, end, trig) in enumerate(triggers_raw):
            if is_filter:
                trig.valid_sec = post_duration_by_index.get(idx)
            elif idx < len(triggers_raw) - 1:
                next_start = triggers_raw[idx + 1][0]
                between = [sec for a, b, sec in durations if a >= end and b <= next_start]
                if between:
                    value = float(between[-1])
                    if is_unordered:
                        unordered_windows.append(value)
                    else:
                        trig.next_window_sec = value
            trig.validate()
            triggers.append(trig)

        filter_groups: tuple[tuple[int, ...], ...] = ()
        if is_filter:
            groups: list[list[int]] = [[0]]
            for idx in range(1, len(triggers_raw)):
                prev_end = triggers_raw[idx - 1][1]
                current_start = triggers_raw[idx][0]
                gap = low[prev_end:current_start]
                if re.search(syntax_literal('parse_timed_chain_local_pattern_4'), gap, re.I):
                    groups[-1].append(idx)
                else:
                    groups.append([idx])
            filter_groups = tuple(tuple(group) for group in groups)

        unordered_window_sec: Optional[float] = None
        if is_unordered and unordered_windows:
            unique_windows = {round(x, 9) for x in unordered_windows}
            if len(unique_windows) > 1:
                raise ValueError("순서무관 조건 유효시간은 하나의 값으로 지정해 주세요")
            unordered_window_sec = float(unordered_windows[-1])

        required_count: Optional[int] = None
        if is_unordered:
            # 예: '3개 중 2개 순서무관'. 표기가 없으면 ALL=N-of-N입니다.
            kofn = re.search(syntax_literal('parse_timed_chain_local_pattern_3'), low)
            if kofn:
                declared_total = int(kofn.group(1))
                required_count = int(kofn.group(2))
                if declared_total != len(triggers):
                    raise ValueError(
                        f"순서무관 조건 개수 불일치: 문장={declared_total}개, 인식={len(triggers)}개"
                    )
            else:
                required_count = len(triggers)
            if required_count <= 0 or required_count > len(triggers):
                raise ValueError(f"순서무관 K-of-N 오류: {required_count}/{len(triggers)}")

        final_action = "OZ" if oz_pos >= 0 else "NOTIFY"
        final_window_sec = None
        oz_tfs: tuple[str, ...] = ()
        direction: Optional[str] = None
        validation_mode = "BLIND" if syntax_literal('parse_timed_chain_local_term_3') in low else "NORMAL"
        trigger_mode = _oz_trigger_mode(low)
        final_watch_persistent = True

        if final_action == "OZ":
            last_end = triggers_raw[-1][1] if triggers_raw else 0
            if not is_filter:
                final_durations = [sec for a, b, sec in durations if a >= last_end and b <= oz_pos]
                if not final_durations and (now_mode or not is_unordered):
                    # 기존 SEQUENTIAL 호환 및 NOW 문장만 전체 구간의 마지막 duration을 final window로 사용합니다.
                    final_durations = [sec for a, b, sec in durations if b <= oz_pos]
                if final_durations:
                    final_window_sec = float(final_durations[-1])
                elif not is_unordered and not has_opposite_cross_cancel and not direct_compound_close_oz:
                    return None

            explicit = self._explicit_oz_tfs_for_chain(clean, last_end, oz_pos, durations)
            if explicit:
                oz_tfs = explicit
            elif triggers:
                oz_tfs = (triggers[-1].tf,) if triggers[-1].tf in OZ_BASE_TFS else ()
            if not oz_tfs:
                default_oz = normalize_tf(self.config.get("CHAIN_DEFAULT_OZ_TF")) or "1m"
                oz_tfs = (default_oz,) if default_oz in OZ_BASE_TFS else ("1m",)

            final_zone = low[last_end:]
            direction = self._parse_oz_direction(final_zone)
            if direction is None:
                direction = "LONG" if syntax_literal('parse_timed_chain_local_term_12') in final_zone else "SHORT" if syntax_literal('parse_timed_chain_local_term_15') in final_zone else None
            if direction is None and triggers:
                direction = self._infer_trigger_default_direction(triggers)

            # 기간을 명시한 기존 시간연쇄는 기간 동안 반복 감시를 유지합니다.
            # 기간 없는 순서무관 '알려줘'는 직접 OZ와 동일하게 1회성이며 '계속'만 지속입니다.
            if is_filter:
                # FILTER의 최종 OZ는 각 조건의 남은 겹침시간 동안 유지합니다.
                final_watch_persistent = True
            elif (is_unordered or has_opposite_cross_cancel or direct_compound_close_oz) and final_window_sec is None:
                final_watch_persistent = syntax_literal('parse_timed_chain_local_term_13') in low
        else:
            if direct_compound_notify:
                final_watch_persistent = syntax_literal('parse_timed_chain_local_term_5') in low
            elif is_filter:
                if not triggers or syntax_literal('parse_timed_chain_local_term_16') not in low:
                    return None
            elif is_unordered:
                if len(triggers) < 2 or syntax_literal('parse_timed_chain_local_term_18') not in low:
                    return None
            else:
                # 마지막 트리거 자체를 알리는 형태는 최소 2단계 + 앞 단계 시간창이 있을 때만 시간연쇄로 처리합니다.
                if len(triggers) < 2 or not any(t.next_window_sec for t in triggers[:-1]):
                    return None
                if syntax_literal('parse_timed_chain_local_term_17') not in low:
                    return None

        invalidation_triggers: tuple[ChainTriggerSpec, ...] = ()
        if has_opposite_cross_cancel:
            source_cross = next((
                trig for trig in triggers
                if trig.watch_type in {"EMA_CROSS", "HMA_CROSS"} and trig.direction in {"LONG", "SHORT"}
            ), None)
            if source_cross is None:
                raise ValueError("역크로스 취소에는 방향이 명확한 EMA/HMA 골크·데크 조건이 필요합니다")
            opposite = "SHORT" if source_cross.direction == "LONG" else "LONG"
            cancel_trig = ChainTriggerSpec(
                watch_type=source_cross.watch_type, tf=source_cross.tf, direction=opposite,
                ma_family=source_cross.ma_family, fast_period=source_cross.fast_period,
                slow_period=source_cross.slow_period,
            )
            cancel_trig.validate()
            invalidation_triggers = (cancel_trig,)

        chain = TimedChainSpec(
            chain_id=f"CHAIN:{owner_chat_id}:{time.time_ns()}",
            owner_chat_id=owner_chat_id, symbol=symbol, triggers=tuple(triggers),
            final_action=final_action, final_window_sec=final_window_sec, oz_tfs=oz_tfs,
            direction=direction, validation_mode=validation_mode, trigger_mode=trigger_mode,
            order_mode="FILTER" if is_filter else "UNORDERED" if is_unordered else "SEQUENTIAL",
            required_count=required_count, unordered_window_sec=unordered_window_sec,
            filter_groups=filter_groups, invalidation_triggers=invalidation_triggers,
            final_watch_persistent=final_watch_persistent,
            repeat_filter=(is_filter and syntax_literal('parse_timed_chain_local_term_14') in low),
        )
        chain.validate()
        return chain


    def _command_language(self) -> dict:
        return self.command_interpreter.language()


    def _command_default(self, key: str, fallback):
        return self.command_interpreter.command_default(key, fallback)


    def _command_default_tf(self) -> str:
        return self.command_interpreter.default_tf()



    @staticmethod
    def _alias_present(text: str, alias: str) -> bool:
        return CommandInterpreter.alias_present(text, alias)


    def _explicit_symbol_from_text(self, text: str) -> Optional[str]:
        return self.command_interpreter.explicit_symbol_from_text(text)


    def _normalize_command_text(self, text: str) -> str:
        return self.command_interpreter.normalize_command_text(text)


    def _condition_macro_matches(self, text: str) -> list[dict]:
        return self.command_interpreter.condition_macro_matches(text)


    def _expand_condition_macro(self, name: str, tf: str, direction: object = None) -> dict:
        return self.command_interpreter.expand_condition_macro(name, tf, direction)


    @staticmethod
    def _condition_from_descriptor(raw: dict, default_tf: str) -> ConditionSpec:
        return condition_from_descriptor(raw, default_tf)


    @staticmethod
    def _macro_direction_near(text: str, start: int, end: int) -> Optional[str]:
        low = str(text or "").lower()
        local = low[max(0, start - 20):min(len(low), end + 12)]
        if any(x in local for x in (syntax_literal('macro_direction_near_term_1'), syntax_literal('macro_direction_near_term_2'), syntax_literal('macro_direction_near_term_3'))):
            return "LONG"
        if any(x in local for x in (syntax_literal('macro_direction_near_term_4'), syntax_literal('macro_direction_near_term_5'), syntax_literal('macro_direction_near_term_6'))):
            return "SHORT"
        return None



    def _parse_symbol(self, text: str) -> Optional[str]:
        return self.command_interpreter.parse_symbol(text)


    @staticmethod
    def _tf_occurrences(text: str) -> list[tuple[int, int, str]]:
        return CommandInterpreter.tf_occurrences(text)


    @staticmethod
    def _nearest_tf_before(occurrences: list[tuple[int, int, str]], pos: int) -> str:
        return CommandInterpreter.nearest_tf_before(occurrences, pos)


    @staticmethod
    def _keyword_pos(text: str, words: Iterable[str]) -> int:
        return CommandInterpreter.keyword_pos(text, words)


    def _parse_oz_direction(self, text: str) -> Optional[str]:
        return self.command_interpreter.parse_oz_direction(text)


    def _extract_oz_tfs(self, text: str, occurrences: list[tuple[int, int, str]], last_condition_pos: int) -> tuple[str, ...]:
        return self.command_interpreter.extract_oz_tfs(text, occurrences, last_condition_pos)


    def _parse_time_filters(self, text: str) -> tuple[str, ...]:
        return self.command_interpreter.parse_time_filters(text)


    @staticmethod
    def _condition_default_direction(cond: ConditionSpec) -> Optional[str]:
        """조건 자체가 암시하는 일반적인 OZ 기본 방향을 반환합니다."""
        if cond.kind in {"MA_STATE", "MA_PRICE_STATE"}:
            if cond.side == "ABOVE":
                return "LONG"
            if cond.side == "BELOW":
                return "SHORT"
        if cond.direction in {"LONG", "SHORT"}:
            return cond.direction
        if cond.kind in {"WONBI", "PERCENTILE"}:
            if cond.side == "LOWER":
                return "LONG"
            if cond.side == "UPPER":
                return "SHORT"
        if cond.kind == "FVG":
            if cond.side == "BULL":
                return "LONG"
            if cond.side == "BEAR":
                return "SHORT"
        return None


    @classmethod
    def _infer_condition_default_direction(cls, conditions: Iterable[ConditionSpec]) -> Optional[str]:
        directions = {
            direction
            for cond in conditions
            for direction in [cls._condition_default_direction(cond)]
            if direction in {"LONG", "SHORT"}
        }
        return next(iter(directions)) if len(directions) == 1 else None


    @staticmethod
    def _infer_trigger_default_direction(triggers: Iterable[ChainTriggerSpec]) -> Optional[str]:
        directions = {
            str(trig.direction)
            for trig in triggers
            if str(trig.direction) in {"LONG", "SHORT"}
        }
        return next(iter(directions)) if len(directions) == 1 else None


    @staticmethod
    def _condition_tfs_before(
        text: str, occurrences: list[tuple[int, int, str]], condition_pos: int
    ) -> tuple[str, ...]:
        """조건 바로 앞에 연속해서 나열된 TF들을 같은 조건의 대상 TF로 묶습니다.

        예: ``15분 30분 1시간 하락추세`` -> (15m, 30m, 1h)
            ``15분과 30분, 1시간 복합조건`` -> (15m, 30m, 1h)

        TF 사이에 다른 조건/단어가 끼면 거기서 묶음을 끊습니다. 따라서 기존
        ``15분 하락추세 30분 하단원비`` 같은 문장은 각 조건의 최근접 TF 하나만
        유지합니다.
        """
        before = [x for x in occurrences if x[1] <= condition_pos]
        if not before:
            return ()

        selected = [before[-1]]
        left = before[-1][0]

        def _is_tf_list_gap(gap: str) -> bool:
            # 일반적인 나열 구분자와 조사/접속사만 허용합니다. 다른 단어가 있으면
            # 이전 TF는 다른 조건에 속한 것으로 보고 확장을 중단합니다.
            compact = re.sub(syntax_literal('is_tf_list_gap_pattern_1'), "", str(gap or "").lower())
            for connector in (syntax_literal('is_tf_list_gap_term_1'), syntax_literal('is_tf_list_gap_term_2'), syntax_literal('is_tf_list_gap_term_3'), syntax_literal('is_tf_list_gap_term_4'), syntax_literal('is_tf_list_gap_term_5'), syntax_literal('is_tf_list_gap_term_6'), syntax_literal('is_tf_list_gap_term_7')):
                compact = compact.replace(connector, "")
            return not compact

        for item in reversed(before[:-1]):
            gap = str(text or "")[item[1]:left]
            if not _is_tf_list_gap(gap):
                break
            selected.append(item)
            left = item[0]

        selected.reverse()
        return tuple(dict.fromkeys(tf for _start, _end, tf in selected))


    @staticmethod
    def _ma_watch_condition_matches(text: str) -> list[dict]:
        """STAFF가 소유한 MA 값을 공용 primitive Condition descriptor로 변환합니다.

        HMA6/17/50/168 방향은 MT5→STAFF 원본 HMA의 현재값과 2봉 전 값을 비교합니다.
        기존 50 HMA 가격 위/아래 조건도 TREND 재계산값이 아니라 STAFF hma_50을 직접 사용합니다.
        """
        low = str(text or "").lower()
        hits: list[dict] = []
        price_prefix = syntax_literal('ma_watch_condition_matches_grammar_847')
        hma50 = syntax_literal('ma_watch_condition_matches_grammar_848')
        hma_any = syntax_literal('ma_watch_condition_matches_grammar_849')
        ema200 = syntax_literal('ma_watch_condition_matches_grammar_850')

        for m in re.finditer(
            f"{price_prefix}{hma50}{syntax_literal('ma_watch_condition_matches_regex_part_853_2')}", low, re.I
        ):
            above = str(m.group("side")).startswith(syntax_literal('ma_watch_condition_matches_term_1'))
            hits.append({
                "start": m.start(), "end": m.end(),
                "name": syntax_literal('ma_watch_condition_matches_term_4') if above else syntax_literal('ma_watch_condition_matches_term_5'),
                "direction": "LONG" if above else "SHORT",
                "descriptor": {
                    "kind": "MA_PRICE_STATE",
                    "direction": "LONG" if above else "SHORT",
                    "side": "ABOVE" if above else "BELOW",
                    "ma_family": "HMA",
                    "slow_period": 50,
                },
            })

        for m in re.finditer(f"{hma_any}{syntax_literal('ma_watch_condition_matches_regex_part_869_1')}", low, re.I):
            period = int(m.group("hma_period"))
            up = str(m.group("slope")) == syntax_literal('ma_watch_condition_matches_term_2')
            hits.append({
                "start": m.start(), "end": m.end(),
                "name": f"{period}헐 우상향" if up else f"{period}헐 우하향",
                "direction": "LONG" if up else "SHORT",
                "descriptor": {
                    "kind": "MA_SLOPE_STATE",
                    "direction": "LONG" if up else "SHORT",
                    "side": "UP" if up else "DOWN",
                    "ma_family": "HMA",
                    "slow_period": period,
                },
            })

        for m in re.finditer(
            f"{price_prefix}{ema200}{syntax_literal('ma_watch_condition_matches_regex_part_886_2')}", low, re.I
        ):
            above = str(m.group("side")).startswith(syntax_literal('ma_watch_condition_matches_term_3'))
            hits.append({
                "start": m.start(), "end": m.end(),
                "name": syntax_literal('ma_watch_condition_matches_term_6') if above else syntax_literal('ma_watch_condition_matches_term_7'),
                "direction": "LONG" if above else "SHORT",
                "descriptor": {
                    "kind": "MA_PRICE_STATE",
                    "direction": "LONG" if above else "SHORT",
                    "side": "ABOVE" if above else "BELOW",
                    "ma_family": "EMA",
                    "slow_period": 200,
                },
            })

        hits.sort(key=lambda x: (int(x["start"]), int(x["end"])))
        return hits


    def _parse_private_strategy_local(self, text: str, owner_chat_id: str) -> Optional[StrategySpec]:
        clean = str(text or "").strip()
        low = clean.lower()
        if syntax_literal('parse_private_strategy_local_term_1') not in low:
            return None
        symbol = self._parse_symbol(clean)
        if not symbol:
            return None
        occurrences = self._tf_occurrences(clean)
        conditions: list[ConditionSpec] = []
        last_condition_pos = -1
        final_direction = self._parse_oz_direction(clean)
        final_direction_explicit = final_direction in {"LONG", "SHORT"}

        # "하단올존/상단올존/매수올존/매도올존"의 방향어가 바로 앞 조건의
        # WONBI/SWEEP 방향 표식으로 오인되지 않도록 marker 검색에서만 가립니다.
        marker_low = low
        oz_dir_alias_re = re.compile(
            syntax_literal('parse_private_strategy_local_pattern_1'),
            re.I,
        )
        marker_low = oz_dir_alias_re.sub(lambda m: " " * len(m.group(0)), marker_low)

        # 사전 기반 조건 매크로를 primitive ConditionSpec으로 확장합니다.
        # 김매니저에는 매크로별 투자 개념이나 조합식이 하드코딩되지 않습니다.
        for macro_hit in self._condition_macro_matches(clean):
            macro_pos = int(macro_hit["start"])
            macro_end = int(macro_hit["end"])
            tfs = self._condition_tfs_before(clean, occurrences, macro_pos)
            if not tfs and occurrences:
                nearest = self._nearest_tf_before(occurrences, macro_pos)
                tfs = (nearest,) if nearest else (occurrences[0][2],)
            if not tfs:
                default_tf = self._command_default_tf()
                tfs = (default_tf,) if default_tf else ()
            macro_direction = self._macro_direction_near(clean, macro_pos, macro_end) or "AUTO"
            for tf in tfs:
                expanded = self._expand_condition_macro(
                    str(macro_hit["name"]), tf, macro_direction
                )
                for raw_condition in expanded.get("conditions") or ():
                    cond = self._condition_from_descriptor(raw_condition, tf)
                    if cond not in conditions:
                        conditions.append(cond)
            if tfs:
                last_condition_pos = max(last_condition_pos, macro_pos)

        def _append_condition(cond: ConditionSpec) -> None:
            # 같은 조건이 표현 중복으로 두 번 잡혀도 한 번만 유지합니다.
            if cond not in conditions:
                conditions.append(cond)

        def _nearest_marker(start: int, end: int, groups: tuple[tuple[str, tuple[str, ...]], ...]) -> str:
            """조건 표현 주변에서 가장 가까운 방향/side 표식을 찾습니다."""
            window_start = max(0, start - 18)
            window_end = min(len(low), end + 18)
            segment = marker_low[window_start:window_end]
            best_distance = 10**9
            best_label = ""
            for label, words in groups:
                for word in words:
                    for mm in re.finditer(re.escape(word), segment, re.I):
                        abs_start = window_start + mm.start()
                        abs_end = window_start + mm.end()
                        if abs_end <= start:
                            distance = start - abs_end
                        elif abs_start >= end:
                            distance = abs_start - end
                        else:
                            distance = 0
                        if distance < best_distance:
                            best_distance = distance
                            best_label = label
            return best_label

        # STAFF MA 현재상태 조건도 다른 Composer 조건과 동일한 ConditionSpec으로 보존합니다.
        for hit in self._ma_watch_condition_matches(low):
            pos = int(hit["start"])
            tfs = self._condition_tfs_before(clean, occurrences, pos)
            if not tfs and occurrences:
                nearest = self._nearest_tf_before(occurrences, pos)
                tfs = (nearest,) if nearest else (occurrences[0][2],)
            if not tfs:
                default_tf = self._command_default_tf()
                tfs = (default_tf,) if default_tf else ()
            for tf in tfs:
                raw = dict(hit["descriptor"])
                raw["tf"] = tf
                _append_condition(self._condition_from_descriptor(raw, tf))
            if tfs:
                last_condition_pos = max(last_condition_pos, pos)

        # 현재 MA 배열 상태 Gate. CROSS 발생을 기다리는 이벤트 조건과 분리합니다.
        # 예: "1분 EMA50/200 역배열일 때 1분 매도 올존 알려줘"
        #     "1분 50 200 지수이평 정배열일 때 1분 매수 올존 알려줘"
        ma_family_alias = syntax_literal('private_strategy_ma_family_pattern')
        ma_state_re = re.compile(
            f"{syntax_literal('parse_private_strategy_local_regex_part_1003_0')}{ma_family_alias}{syntax_literal('parse_private_strategy_local_regex_part_1003_2')}{ma_family_alias}{syntax_literal('parse_private_strategy_local_regex_part_1003_4')}",
            re.I,
        )

        def _ma_state_family(value: Optional[str]) -> Optional[str]:
            if value is None:
                return None
            token = re.sub(syntax_literal('ma_state_family_pattern_1'), "", str(value).lower())
            if token in {"hma", syntax_literal('ma_state_family_term_1'), syntax_literal('ma_state_family_term_2')}:
                return "HMA"
            if token in {"ema", syntax_literal('ma_state_family_term_3'), syntax_literal('ma_state_family_term_4'), syntax_literal('ma_state_family_term_5')}:
                return "EMA"
            return None

        for m in ma_state_re.finditer(low):
            state_pos = m.start()
            tfs = self._condition_tfs_before(clean, occurrences, state_pos)
            if not tfs and occurrences:
                nearest = self._nearest_tf_before(occurrences, state_pos)
                tfs = (nearest,) if nearest else (occurrences[0][2],)
            if not tfs:
                default_tf = self._command_default_tf()
                tfs = (default_tf,) if default_tf else ()

            prefix_family = _ma_state_family(m.group("prefix"))
            suffix_family = _ma_state_family(m.group("suffix"))
            if prefix_family and suffix_family and prefix_family != suffix_family:
                raise ValueError("MA 배열 family 표현이 서로 충돌합니다")
            family = prefix_family or suffix_family or str(self._command_default("ma_family", "EMA")).upper()
            fast = int(m.group("fast"))
            slow = int(m.group("slow"))
            side = "ABOVE" if m.group("state") == syntax_literal('parse_private_strategy_local_term_28') else "BELOW"

            for tf in tfs:
                _append_condition(ConditionSpec(
                    "MA_STATE", tf, direction="AUTO", side=side,
                    ma_family=family, fast_period=fast, slow_period=slow,
                ))
            if tfs:
                last_condition_pos = max(last_condition_pos, state_pos)

        # TREND 내부 지표값을 필요할 때만 query하는 범용 metric Gate.
        # 예: "15분 ADX 25 이상일때 1분 매수 올존 알려줘"
        #     "15분 MACD가 시그널보다 높을때 1분 올존 알려줘"
        #     "15분 슈퍼트랜드 상승일때 1분 올존 알려줘"
        metric_aliases = {
            syntax_literal('parse_private_strategy_local_term_2'): "long_score", syntax_literal('parse_private_strategy_local_term_3'): "long_score", "long score": "long_score",
            syntax_literal('parse_private_strategy_local_term_4'): "short_score", syntax_literal('parse_private_strategy_local_term_5'): "short_score", "short score": "short_score",
            syntax_literal('parse_private_strategy_local_term_6'): "trend_score", syntax_literal('parse_private_strategy_local_term_7'): "trend_score", "trend score": "trend_score",
            "adx": "adx", "+di": "plus_di", "plus di": "plus_di", "plusdi": "plus_di", syntax_literal('parse_private_strategy_local_term_8'): "plus_di",
            "-di": "minus_di", "minus di": "minus_di", "minusdi": "minus_di", syntax_literal('parse_private_strategy_local_term_9'): "minus_di",
            "rsi": "rsi14", "rsi14": "rsi14", "cci": "cci20", "cci20": "cci20",
            "mfi": "mfi14", "mfi14": "mfi14", "cmf": "cmf20", "cmf20": "cmf20",
            "chop": "chop14", "chop14": "chop14", "choppiness": "chop14",
            "bop": "bop", "hv": "hv20", "hv20": "hv20", "hvma": "hvma20", "hvma20": "hvma20",
            "macd signal": "macd_signal", syntax_literal('parse_private_strategy_local_term_10'): "macd_signal", "macd_signal": "macd_signal",
            syntax_literal('parse_private_strategy_local_term_11'): "macd_signal", "signal": "macd_signal", "macd": "macd",
            "aroon up": "aroon_up", "aroon_up": "aroon_up", syntax_literal('parse_private_strategy_local_term_12'): "aroon_up",
            "aroon down": "aroon_down", "aroon_down": "aroon_down", syntax_literal('parse_private_strategy_local_term_13'): "aroon_down",
            "vortex plus": "vortex_plus", "vortex_plus": "vortex_plus", syntax_literal('parse_private_strategy_local_term_14'): "vortex_plus",
            "vortex minus": "vortex_minus", "vortex_minus": "vortex_minus", syntax_literal('parse_private_strategy_local_term_15'): "vortex_minus",
            "vwap": "vwap", syntax_literal('parse_private_strategy_local_term_16'): "vwap", "price": "price", syntax_literal('parse_private_strategy_local_term_17'): "price",
            "psar": "psar", "sar": "psar", syntax_literal('parse_private_strategy_local_term_18'): "psar",
            "linreg20": "linreg20", "mss": "mss", "vol_state": "vol_state", "vol_surge": "vol_surge",
            "ema10_open": "ema10_open", "ema50_open": "ema50_open", "sma20_open": "sma20_open",
            "wma17_open": "wma17_open",
            "supertrend": "supertrend", syntax_literal('parse_private_strategy_local_term_19'): "supertrend", syntax_literal('parse_private_strategy_local_term_20'): "supertrend",
        }
        metric_alias_pattern = syntax_literal('parse_private_strategy_local_pattern_5').join(
            re.escape(x) for x in sorted(metric_aliases, key=len, reverse=True)
        )

        def _metric_tfs(pos: int) -> tuple[str, ...]:
            tfs = self._condition_tfs_before(clean, occurrences, pos)
            if not tfs and occurrences:
                nearest = self._nearest_tf_before(occurrences, pos)
                tfs = (nearest,) if nearest else (occurrences[0][2],)
            if not tfs:
                default_tf = self._command_default_tf()
                tfs = (default_tf,) if default_tf else ()
            return tfs

        def _metric_op_from_text(raw: str) -> str:
            token = re.sub(syntax_literal('metric_op_from_text_pattern_1'), "", str(raw or "").lower())
            if token in {syntax_literal('metric_op_from_text_term_11'), syntax_literal('metric_op_from_text_term_12')} or any(x in token for x in (syntax_literal('metric_op_from_text_term_15'), syntax_literal('metric_op_from_text_term_16'), syntax_literal('metric_op_from_text_term_17'))):
                return "GT"
            if token in {syntax_literal('metric_op_from_text_term_1'), syntax_literal('metric_op_from_text_term_2')}:
                return "GTE"
            if token in {syntax_literal('metric_op_from_text_term_13'), syntax_literal('metric_op_from_text_term_14')} or any(x in token for x in (syntax_literal('metric_op_from_text_term_18'), syntax_literal('metric_op_from_text_term_19'), syntax_literal('metric_op_from_text_term_20'))):
                return "LT"
            if token in {syntax_literal('metric_op_from_text_term_3'), syntax_literal('metric_op_from_text_term_4')}:
                return "LTE"
            if token in {syntax_literal('metric_op_from_text_term_5'), syntax_literal('metric_op_from_text_term_6'), syntax_literal('metric_op_from_text_term_7'), syntax_literal('metric_op_from_text_term_8')}:
                return "EQ"
            if token in {syntax_literal('metric_op_from_text_term_9'), syntax_literal('metric_op_from_text_term_10')}:
                return "NE"
            return ""

        # 1) 숫자 임계값 비교
        metric_numeric_re = re.compile(
            f"{syntax_literal('parse_private_strategy_local_regex_part_1106_0')}{metric_alias_pattern}{syntax_literal('parse_private_strategy_local_regex_part_1106_2')}",
            re.I,
        )
        for m in metric_numeric_re.finditer(low):
            metric = metric_aliases.get(m.group("metric").lower())
            operator = _metric_op_from_text(m.group("op"))
            if not metric or not operator:
                continue
            direction = "LONG" if metric == "long_score" else "SHORT" if metric == "short_score" else "AUTO"
            tfs = _metric_tfs(m.start())
            for tf in tfs:
                _append_condition(ConditionSpec(
                    "TREND_METRIC", tf, direction=direction,
                    metric=metric, metric_operator=operator, metric_value=float(m.group("value")),
                ))
            if tfs:
                last_condition_pos = max(last_condition_pos, m.start())

        # 2) 현재 지표 A와 지표 B 비교. 계산은 TREND에 둘 다 요청합니다.
        metric_relation_re = re.compile(
            f"{syntax_literal('parse_private_strategy_local_regex_part_1128_0')}{metric_alias_pattern}{syntax_literal('parse_private_strategy_local_regex_part_1128_2')}{metric_alias_pattern}{syntax_literal('parse_private_strategy_local_regex_part_1128_4')}",
            re.I,
        )
        directional_pairs = {
            ("macd", "macd_signal"), ("plus_di", "minus_di"),
            ("aroon_up", "aroon_down"), ("vortex_plus", "vortex_minus"),
            ("price", "vwap"),
        }
        reverse_pairs = {(b, a) for a, b in directional_pairs}
        for m in metric_relation_re.finditer(low):
            left = metric_aliases.get(m.group("left").lower())
            right = metric_aliases.get(m.group("right").lower())
            rel = str(m.group("rel") or "").lower()
            if not left or not right or left == right:
                continue
            operator = "GT" if any(x in rel for x in (syntax_literal('parse_private_strategy_local_term_49'), syntax_literal('parse_private_strategy_local_term_50'), syntax_literal('parse_private_strategy_local_term_51'))) or rel in {syntax_literal('parse_private_strategy_local_term_41'), syntax_literal('parse_private_strategy_local_term_42')} else "LT"
            if rel in {syntax_literal('parse_private_strategy_local_term_29'), syntax_literal('parse_private_strategy_local_term_30')}:
                operator = "GTE" if rel == syntax_literal('parse_private_strategy_local_term_39') else "LTE"
            direction = "AUTO"
            if (left, right) in directional_pairs:
                direction = "LONG" if operator in {"GT", "GTE"} else "SHORT"
            elif (left, right) in reverse_pairs:
                direction = "SHORT" if operator in {"GT", "GTE"} else "LONG"
            tfs = _metric_tfs(m.start())
            for tf in tfs:
                _append_condition(ConditionSpec(
                    "TREND_METRIC", tf, direction=direction,
                    metric=left, metric_operator=operator, metric_rhs=right,
                ))
            if tfs:
                last_condition_pos = max(last_condition_pos, m.start())

        # 3) Supertrend 방향 상태. TREND 내부 bool을 1=상승, 0=하락으로 query합니다.
        supertrend_re = re.compile(
            syntax_literal('parse_private_strategy_local_pattern_2'), re.I
        )
        for m in supertrend_re.finditer(low):
            bullish = m.group(1) in {syntax_literal('parse_private_strategy_local_term_31'), syntax_literal('parse_private_strategy_local_term_32'), syntax_literal('parse_private_strategy_local_term_33')}
            direction = "LONG" if bullish else "SHORT"
            tfs = _metric_tfs(m.start())
            for tf in tfs:
                _append_condition(ConditionSpec(
                    "TREND_METRIC", tf, direction=direction,
                    metric="supertrend", metric_operator="EQ", metric_value=1.0 if bullish else 0.0,
                ))
            if tfs:
                last_condition_pos = max(last_condition_pos, m.start())

        # 같은 종류 조건이 여러 번 있어도 전부 보존합니다.
        # 각 조건의 방향/side는 문장 전체가 아니라 해당 표현 주변에서 판정합니다.
        trend_re = re.compile(
            syntax_literal('parse_private_strategy_local_pattern_3'), re.I
        )
        for m in trend_re.finditer(low):
            trend_pos = m.start()
            tfs = self._condition_tfs_before(clean, occurrences, trend_pos)
            if not tfs and occurrences:
                nearest = self._nearest_tf_before(occurrences, trend_pos)
                tfs = (nearest,) if nearest else (occurrences[0][2],)
            if not tfs:
                default_tf = self._command_default_tf()
                tfs = (default_tf,) if default_tf else ()
            token = re.sub(syntax_literal('parse_private_strategy_local_pattern_6'), "", m.group(0).lower())
            direction = "LONG" if token.startswith(syntax_literal('parse_private_strategy_local_term_34')) else "SHORT" if token.startswith(syntax_literal('parse_private_strategy_local_term_40')) else "AUTO"
            for tf in tfs:
                _append_condition(ConditionSpec("TREND", tf, direction=direction))
            if tfs:
                last_condition_pos = max(last_condition_pos, trend_pos)

        for m in re.finditer(syntax_literal('parse_private_strategy_local_term_21'), low, re.I):
            wonbi_pos = m.start()
            tfs = self._condition_tfs_before(clean, occurrences, wonbi_pos)
            if not tfs and conditions:
                tfs = (conditions[-1].tf,)
            if not tfs:
                default_tf = self._command_default_tf()
                tfs = (default_tf,) if default_tf else ()
            side = _nearest_marker(
                wonbi_pos, m.end(),
                (("LOWER", (syntax_literal('parse_private_strategy_local_term_43'),)), ("UPPER", (syntax_literal('parse_private_strategy_local_term_44'),))),
            )
            direction = "LONG" if side == "LOWER" else "SHORT" if side == "UPPER" else "AUTO"
            for tf in tfs:
                _append_condition(ConditionSpec("WONBI", tf, direction=direction, side=side))
            if tfs:
                last_condition_pos = max(last_condition_pos, wonbi_pos)

        # Percentile OUT은 방향을 조건 자체에서 맵핑합니다.
        # 하단 OUT=LONG, 상단 OUT=SHORT, 단순 '아웃'=양방향(AUTO).
        out_re = re.compile(syntax_literal('parse_private_strategy_local_pattern_4'), re.I)
        for out_match in out_re.finditer(low):
            out_pos = out_match.start()
            tfs = self._condition_tfs_before(clean, occurrences, out_pos)
            if not tfs and occurrences:
                nearest = self._nearest_tf_before(occurrences, out_pos)
                tfs = (nearest,) if nearest else (occurrences[0][2],)
            if not tfs:
                default_tf = self._command_default_tf()
                tfs = (default_tf,) if default_tf else ()
            percentile_side = self._command_language().get("percentile_side") or {}
            lower_terms = (syntax_literal('parse_private_strategy_local_term_23'), *(str(x) for x in (percentile_side.get("LOWER") or [])))
            upper_terms = (syntax_literal('parse_private_strategy_local_term_24'), *(str(x) for x in (percentile_side.get("UPPER") or [])))
            side = _nearest_marker(
                out_pos, out_match.end(),
                (("LOWER", lower_terms), ("UPPER", upper_terms)),
            )
            direction = "LONG" if side == "LOWER" else "SHORT" if side == "UPPER" else "AUTO"
            for tf in tfs:
                _append_condition(ConditionSpec("PERCENTILE", tf, direction=direction, side=side))
            if tfs:
                last_condition_pos = max(last_condition_pos, out_pos)

        for m in re.finditer(r"fvg", low, re.I):
            fvg_pos = m.start()
            tfs = self._condition_tfs_before(clean, occurrences, fvg_pos)
            if not tfs and occurrences:
                nearest = self._nearest_tf_before(occurrences, fvg_pos)
                tfs = (nearest,) if nearest else (occurrences[0][2],)
            if not tfs:
                default_tf = self._command_default_tf()
                tfs = (default_tf,) if default_tf else ()
            side = _nearest_marker(
                fvg_pos, m.end(),
                (("BULL", (syntax_literal('parse_private_strategy_local_term_45'),)), ("BEAR", (syntax_literal('parse_private_strategy_local_term_46'),))),
            )
            direction = "LONG" if side == "BULL" else "SHORT" if side == "BEAR" else "AUTO"
            for tf in tfs:
                _append_condition(ConditionSpec("FVG", tf, direction=direction, side=side))
            if tfs:
                last_condition_pos = max(last_condition_pos, fvg_pos)

        # 외부유동성은 레벨 자체가 방향을 결정할 수 있습니다.
        # 전일저가/PDL=LONG, 전일고가/PDH=SHORT 등.
        liquidity_aliases = (
            ((syntax_literal('parse_private_strategy_local_term_35'),), "PDL", "LONG"),
            ((syntax_literal('parse_private_strategy_local_term_36'),), "PDH", "SHORT"),
            ((syntax_literal('parse_private_strategy_local_term_37'),), "SESSION_LOW", "LONG"),
            ((syntax_literal('parse_private_strategy_local_term_38'),), "SESSION_HIGH", "SHORT"),
        )
        matched_liquidity = False
        liquidity_hits: list[tuple[int, int, str, str]] = []
        for aliases, selector, mapped_direction in liquidity_aliases:
            pattern = syntax_literal('parse_private_strategy_local_pattern_7').join(re.escape(a) for a in sorted(aliases, key=len, reverse=True))
            for m in re.finditer(pattern, low, re.I):
                liquidity_hits.append((m.start(), m.end(), selector, mapped_direction))
        for pos, _end, selector, mapped_direction in sorted(liquidity_hits):
            tf = self._nearest_tf_before(occurrences, pos)
            if not tf:
                tf = self._command_default_tf()
            _append_condition(ConditionSpec("SWEEP", tf, direction=mapped_direction, side=selector))
            last_condition_pos = max(last_condition_pos, pos)
            matched_liquidity = True

        if not matched_liquidity:
            sweep_re = re.compile(syntax_literal('parse_private_strategy_local_term_25'), re.I)
            for m in sweep_re.finditer(low):
                sweep_pos = m.start()
                tfs = self._condition_tfs_before(clean, occurrences, sweep_pos)
                if not tfs and occurrences:
                    nearest = self._nearest_tf_before(occurrences, sweep_pos)
                    tfs = (nearest,) if nearest else (occurrences[0][2],)
                if not tfs:
                    default_tf = self._command_default_tf()
                    tfs = (default_tf,) if default_tf else ()
                marker = _nearest_marker(
                    sweep_pos, m.end(),
                    (("LONG", (syntax_literal('parse_private_strategy_local_term_47'),)), ("SHORT", (syntax_literal('parse_private_strategy_local_term_48'),))),
                )
                direction = marker or "AUTO"
                for tf in tfs:
                    _append_condition(ConditionSpec("SWEEP", tf, direction=direction))
                if tfs:
                    last_condition_pos = max(last_condition_pos, sweep_pos)

        if not conditions:
            return None
        if not final_direction_explicit:
            final_direction = self._infer_condition_default_direction(conditions)
        oz_tfs = self._extract_oz_tfs(clean, occurrences, last_condition_pos)
        if not oz_tfs:
            return None

        validation_mode = "BLIND" if syntax_literal('parse_private_strategy_local_term_26') in low else "NORMAL"
        trigger_mode = _oz_trigger_mode(low)
        combination = "ANY" if syntax_literal('parse_private_strategy_local_term_27') in low else "ALL"
        persistent = syntax_literal('parse_private_strategy_local_term_22') in low
        spec_id = f"PRIVATE:{owner_chat_id}:{time.time_ns()}"
        spec = StrategySpec(
            spec_id=spec_id,
            name="개인전략",
            symbol=symbol,
            conditions=tuple(conditions),
            oz_tfs=oz_tfs,
            combination=combination,
            validation_mode=validation_mode,
            trigger_mode=trigger_mode,
            final_direction=final_direction,
            final_direction_explicit=final_direction_explicit,
            destination="PRIVATE",
            owner_chat_id=owner_chat_id,
            persistent=persistent,
            enabled=True,
            time_filters=self._parse_time_filters(clean),
            source="PRIVATE",
        )
        spec.validate()
        return spec


    def _parse_condition_notify_local(self, text: str, owner_chat_id: str) -> Optional[StrategySpec]:
        """올존 없이 Composer 조건 자체가 성립하면 바로 알려주는 1회성/지속 알림."""
        clean = str(text or "").strip()
        low = clean.lower()
        if syntax_literal('parse_condition_notify_local_term_2') in low or not syntax_literal('parse_condition_notify_local_term_3') in low:
            return None

        # 기존 Composer 조건 파서를 재사용하되 최종 행동만 OZ가 아닌 NOTIFY로 바꿉니다.
        synthetic = clean + f" {self._command_default_tf()} 올존"
        spec = self._parse_private_strategy_local(synthetic, owner_chat_id)
        if spec is None:
            return None
        spec.spec_id = f"NOTIFY:{owner_chat_id}:{time.time_ns()}"
        macro_hits = self._condition_macro_matches(clean)
        spec.name = str(macro_hits[0]["name"]) if len(macro_hits) == 1 else "조건알림"
        spec.final_action = "NOTIFY"
        spec.oz_tfs = ()
        spec.validation_mode = "NORMAL"
        spec.trigger_mode = "OZ"
        spec.persistent = syntax_literal('parse_condition_notify_local_term_1') in low
        spec.validate()
        return spec


    def _detect_intent(self, text: str) -> str:
        intent = self.command_interpreter.detect_intent(text, self._parse_chain_triggers)
        low = str(text or "").lower()
        # 공용 복합조건이 단독 알림으로 들어오면 기존 intent 사전에 새 문구를 매번
        # 하드코딩하지 않고 canonical COMPOUND_CONDITION 계약으로 라우팅합니다.
        if syntax_literal('detect_intent_term_1') in low and syntax_literal('detect_intent_term_2') not in low and not self._duration_matches(text):
            triggers = self._parse_chain_triggers(text, len(text), [])
            if len(triggers) == 1:
                condition_type, evaluation_mode = trigger_watch_contract(triggers[0][2])
                if condition_type == "COMPOUND_CONDITION":
                    if evaluation_mode == "CLOSE" or intent in {"FALLBACK", "GENERIC_WATCH", "GENERIC_INVALID"}:
                        return "TIMED_CHAIN"
        return intent


    def _query_target_from_text(self, text: str, label: str) -> tuple[str, str]:
        """조회 명령의 종목/TF를 검증하여 잘못된 요청이 엔진으로 넘어가지 않게 합니다."""
        symbol = self._parse_symbol(text)
        if not symbol:
            raise ValueError(f"{label} 조회 종목을 찾지 못했습니다")
        occ = self._tf_occurrences(text)
        tf = occ[0][2] if occ else self._command_default_tf()
        if tf not in CONDITION_DATA_TFS:
            raise ValueError(
                f"{label} 조회가 지원하지 않는 시간봉: {tf} "
                f"(지원: {','.join(CONDITION_DATA_TFS)})"
            )
        return symbol, tf


    def _generic_invalid_message(self, text: str) -> str:
        return self.command_interpreter.generic_invalid_message(text)


    def _parse_canonical_ma_watch(self, text: str, owner_chat_id: str) -> Optional[dict]:
        from watch_ma import parse_canonical_command
        value = parse_canonical_command(text, self.command_interpreter)
        if value is None:
            return None
        return dict(value, action="GENERIC_WATCH",
                    watch_id=stable_id("GENMANUAL", owner_chat_id, time.time_ns(), length=20),
                    persistent=False, request_chat_id=owner_chat_id, silent=False)


    def _parse_generic_watch_local(self, text: str, owner_chat_id: str) -> Optional[dict]:
        """시간연쇄/OZ가 아닌 단일 조건 알림을 GenericWatch payload로 변환합니다."""
        canonical_ma = self._parse_canonical_ma_watch(text, owner_chat_id)
        if canonical_ma is not None:
            return canonical_ma
        clean = str(text or "").strip()
        low = clean.lower()
        if not syntax_literal('parse_generic_watch_local_term_1') in low:
            return None
        if syntax_literal('parse_generic_watch_local_term_2') in low or syntax_literal('parse_generic_watch_local_term_3') in low:
            return None
        # 'OUT→IN' 자체의 화살표는 단일 조건 표현이므로 화살표 유무로 배제하지 않습니다.
        if self._duration_matches(clean):
            return None

        triggers_raw = self._parse_chain_triggers(clean, len(clean), [])
        if len(triggers_raw) != 1:
            return None
        trig = triggers_raw[0][2]
        symbol = self._parse_symbol(clean)
        if not symbol:
            return None
        condition_type, evaluation_mode = trigger_watch_contract(trig)
        return {
            "action": "GENERIC_WATCH",
            "watch_id": stable_id("GENMANUAL", owner_chat_id, time.time_ns(), length=20),
            "watch_type": condition_type,
            "evaluation_mode": evaluation_mode,
            "timeframes": [trig.tf],
            "symbol": symbol,
            "direction": trig.direction,
            "level_side": trig.level_side,
            "ma_family": trig.ma_family,
            "fast_period": trig.fast_period,
            "slow_period": trig.slow_period,
            # Generic notifications share the existing 계속/지속/항상 contract
            # with condition notifications; aliases were normalized upstream.
            "persistent": syntax_literal('parse_condition_notify_local_term_1') in low,
            "request_chat_id": owner_chat_id,
            "silent": False,
        }


    def _parse_direct_oz_watch_local(self, text: str, owner_chat_id: str) -> Optional[dict]:
        """조건식 없이 직접 요청한 OZ 감시를 MANUAL_WATCH payload로 변환합니다.

        예: "골드 1분 올존 알려줘", "골드 1분 5분 무지성 브레이커 올존 알려줘".
        시간연쇄 문장은 앞 단계 파서가 담당하므로 여기서는 받지 않습니다.
        """
        clean = str(text or "").strip()
        low = clean.lower()
        if syntax_literal('parse_direct_oz_watch_local_term_1') not in low:
            return None
        if not syntax_literal('parse_direct_oz_watch_local_term_4') in low:
            return None
        if syntax_literal('parse_direct_oz_watch_local_term_5') in low or self._duration_matches(clean):
            return None

        symbol = self._parse_symbol(clean)
        if not symbol:
            return None

        occurrences = self._tf_occurrences(clean)
        invalid_explicit = [tf for _a, _b, tf in occurrences if tf not in OZ_BASE_TFS]
        if invalid_explicit:
            requested = ",".join(dict.fromkeys(invalid_explicit))
            raise ValueError(
                f"지원하지 않는 올존 시간봉입니다: {requested} "
                f"(지원: {','.join(OZ_BASE_TFS)})"
            )
        oz_tfs = self._extract_oz_tfs(clean, occurrences, -1)
        if not oz_tfs:
            oz_tfs = (self._command_default_tf(),)

        validation_mode = "BLIND" if syntax_literal('parse_direct_oz_watch_local_term_6') in low else "NORMAL"
        trigger_mode = _oz_trigger_mode(low)
        direction = self._parse_oz_direction(clean)
        persistent = syntax_literal('parse_direct_oz_watch_local_term_2') in low
        watch_id = stable_id("DIRECTOZ", owner_chat_id, time.time_ns(), length=20)

        # 사용자가 직접 지정한 가격(예: "골드 4310에서 올존 나오면 알려줘")은
        # SWEEP이 계산한 공식 레벨과 섞지 않고, 개인 Watch 전용 수동 외부유동성 레벨로 표시합니다.
        # 실제 터치 판독과 ATR14 x1.5 자격 심사는 monitor_OZ의 기존 외부유동성 Gate가 담당합니다.
        manual_level_price = None

        # 숫자가 포함된 종목명(예: US30/NAS100/US500)을 수동 가격으로 오인하지 않도록
        # 현재 문장에서 실제 종목으로 인식되는 토큰만 가격 검색 대상에서 마스킹합니다.
        # 종목 뒤에 별도 가격을 쓴 "US500 7000에서"의 7000은 그대로 남겨둡니다.
        price_scan_text = low
        protected_symbol_terms: set[str] = set()

        explicit_symbol = self._explicit_symbol_from_text(clean)
        if explicit_symbol and any(ch.isdigit() for ch in explicit_symbol):
            protected_symbol_terms.add(str(explicit_symbol).strip().lower())

        for canonical, aliases in (self._command_language().get("symbols") or {}).items():
            canonical_text = str(canonical or "").strip().lower()
            terms = [canonical_text, *(str(x or "").strip().lower() for x in (aliases or []))]
            terms = [x for x in terms if x]
            if not terms or not any(self._alias_present(low, term) for term in terms):
                continue

            suffix_match = re.search(syntax_literal('parse_direct_oz_watch_local_pattern_2'), canonical_text)
            numeric_suffix = suffix_match.group(1) if suffix_match is not None else ""
            for term in terms:
                if any(ch.isdigit() for ch in term):
                    protected_symbol_terms.add(term)
                elif numeric_suffix:
                    # "나스닥100", "다우30", "에스앤피500"처럼 alias와
                    # canonical 숫자 suffix를 붙여 쓰는 표현도 종목명으로 보호합니다.
                    protected_symbol_terms.add(term + numeric_suffix)

        for term in sorted(protected_symbol_terms, key=len, reverse=True):
            price_scan_text = re.sub(
                re.escape(term),
                lambda m: " " * (m.end() - m.start()),
                price_scan_text,
                flags=re.I,
            )

        # "지정가 4300"처럼 사용자가 수동 외부유동성 가격임을 명시하면
        # 뒤에 "에서"가 없어도 가격으로 확정합니다. 다른 동의어는 command_aliases에서
        # "지정가"로 정규화해 이 단일 문법으로 들어오게 합니다.
        level_match = re.search(
            syntax_literal('parse_direct_oz_watch_local_pattern_1'),
            price_scan_text,
        )
        if level_match is None:
            level_match = re.search(
                syntax_literal('parse_direct_oz_watch_local_pattern_3'),
                price_scan_text,
            )
        if level_match is not None:
            try:
                candidate = float(level_match.group("price").replace(",", ""))
            except (TypeError, ValueError):
                candidate = float("nan")
            if math.isfinite(candidate) and candidate > 0.0:
                manual_level_price = candidate

        payload = {
            "action": "MANUAL_WATCH",
            "watch_id": watch_id,
            "timeframes": list(oz_tfs),
            "symbol": symbol,
            "direction": direction,
            "persistent": persistent,
            "request_chat_id": owner_chat_id,
            "validation_mode": validation_mode,
            "trigger_mode": trigger_mode,
            "source_spec_id": watch_id,
            "source_name": syntax_literal('parse_direct_oz_watch_local_term_3'),
        }
        if manual_level_price is not None:
            # 별도 setup TF 문법을 새로 만들지 않습니다. 직접 OZ 요청의 첫 TF를
            # 수동 외부유동성의 source/setup TF로 사용합니다. TF 미지정 시 기존 기본 TF입니다.
            external_source_tf = oz_tfs[0]
            payload.update({
                "external_watch_id": watch_id,
                "external_source_tf": external_source_tf,
                "external_liquidity_required": True,
                "external_source_kind": "MANUAL_LEVEL",
                "external_level_price": manual_level_price,
                "external_level_name": f"수동 가격 {manual_level_price:g}",
                "external_registered_at": time.time(),
                "source_name": f"직접 OZ 감시 · 수동 외부유동성 {manual_level_price:g}",
            })
        return payload


    def _parse_fvg_created_oz_local(self, text: str, owner_chat_id: str) -> Optional[dict]:
        """'신규 FVG 생성 → OZ'를 FVG_CREATED 이벤트 기반으로 직접 연결합니다.

        기존 'FVG에서 올존'은 FVG_TOUCH 조건이므로 생성/신규 표현이 있을 때만 이 경로를 사용합니다.
        """
        clean = str(text or "").strip()
        low = clean.lower()
        if syntax_literal('parse_fvg_created_oz_local_term_1') not in low:
            return None

        fvg_match = None
        for m in re.finditer(r"fvg", low, re.I):
            local = low[max(0, m.start() - 18):min(len(low), m.end() + 22)]
            if any(word in local for word in (syntax_literal('parse_fvg_created_oz_local_term_6'), syntax_literal('parse_fvg_created_oz_local_term_7'))):
                fvg_match = m
                break
        if fvg_match is None:
            return None

        symbol = self._parse_symbol(clean)
        if not symbol:
            return None
        occurrences = self._tf_occurrences(clean)
        fvg_tf = self._nearest_tf_before(occurrences, fvg_match.start()) or self._command_default_tf()
        if fvg_tf not in CONDITION_DATA_TFS:
            raise ValueError(f"FVG가 지원하지 않는 시간봉: {fvg_tf}")

        local = low[max(0, fvg_match.start() - 18):min(len(low), fvg_match.end() + 22)]
        fvg_direction = "LONG" if syntax_literal('parse_fvg_created_oz_local_term_2') in local else "SHORT" if syntax_literal('parse_fvg_created_oz_local_term_4') in local else None
        oz_tfs = self._extract_oz_tfs(clean, occurrences, fvg_match.end())
        if not oz_tfs:
            default_oz = self._command_default_tf()
            oz_tfs = (default_oz,) if default_oz in OZ_BASE_TFS else ("1m",)

        explicit_oz_direction = self._parse_oz_direction(clean)
        oz_direction = explicit_oz_direction or fvg_direction

        return {
            "watch_id": stable_id("FVGCREATEDOZ", owner_chat_id, time.time_ns(), length=20),
            "symbol": symbol, "timeframes": [fvg_tf], "direction": fvg_direction,
            "persistent": syntax_literal('parse_fvg_created_oz_local_term_3') in low, "request_chat_id": owner_chat_id, "silent": False,
            "final_action": "OZ", "oz_tfs": list(oz_tfs),
            "oz_direction": oz_direction,
            "validation_mode": "BLIND" if syntax_literal('parse_fvg_created_oz_local_term_5') in low else "NORMAL",
            "trigger_mode": _oz_trigger_mode(low),
        }
