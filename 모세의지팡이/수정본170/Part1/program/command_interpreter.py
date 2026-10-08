# -*- coding: utf-8 -*-
"""Command interpretation layer for manager_KIM.

This module owns Telegram command language concerns only:
- external alias dictionary loading / hot reload
- canonical text normalization
- symbol / timeframe lexical parsing
- OZ direction / target timeframe parsing
- relative-duration token parsing
- top-level intent classification
- Gemini fallback canonicalization (translation only, never execution)

It deliberately does not create StrategySpec/OZ payloads and does not execute watches.
"""
from __future__ import annotations

from domain_clock import datetime as dt
import json
import logging
import re
import sys
from pathlib import Path
from typing import Callable, Iterable, Optional
from zoneinfo import ZoneInfo
from symbol_settings import configured_symbols

# Part1 remains independently importable when started from its program folder.
# The shared package is a sibling of Part1, and never imports Part3.
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))
from moses_language import command_language, language_path, load_dictionary, syntax_literal, reload_dictionary


DEFAULT_COMMAND_TF = command_language()['defaults']['timeframe']
DEFAULT_COMMAND_GOLD_SYMBOL = command_language()['defaults']['symbol']
DEFAULT_COMMAND_ALIASES_RELOAD_SEC = 5.0

MT5_TIMEFRAMES = (
    "1m", "2m", "3m", "4m", "5m", "6m", "10m", "12m", "15m", "20m", "30m",
    "1h", "2h", "3h", "4h", "6h", "8h", "12h", "1d",
)

OZ_BASE_TFS = (
    "1m", "2m", "3m", "4m", "5m", "6m", "10m", "12m", "15m", "20m", "30m",
    "1h", "2h", "3h", "4h",
)

OZ_TF_MAP = {
    "1m": "1분", "2m": "2분", "3m": "3분", "4m": "4분", "5m": "5분", "6m": "6분",
    "10m": "10분", "12m": "12분", "15m": "15분", "20m": "20분", "30m": "30분",
    "1h": "1시간", "2h": "2시간", "3h": "3시간", "4h": "4시간",
}

WATCH_TF_MAP = {
    "1m": "1분", "2m": "2분", "3m": "3분", "4m": "4분", "5m": "5분", "6m": "6분",
    "10m": "10분", "12m": "12분", "15m": "15분", "20m": "20분", "30m": "30분",
    "1h": "1시간", "2h": "2시간", "3h": "3시간", "4h": "4시간", "6h": "6시간",
    "8h": "8시간", "12h": "12시간", "1d": "1일",
}

DEFAULT_COMMAND_LANGUAGE = command_language()


def resolve_command_language_path(config: dict | None = None, program: Path | None = None) -> Path:
    """Use the shared dictionary unless a user explicitly selects an override."""
    selected = str((config or {}).get("COMMAND_ALIASES_FILE") or "").strip()
    if not selected:
        return language_path()
    path = Path(selected)
    return path if path.is_absolute() else Path(program or Path(__file__).parent) / path


def grammar_terms(name: str):
    """Read canonical grammar words from the same cached public dictionary."""
    return load_dictionary()['grammar_terms'][name]


def _merge_nested_dict(base: dict, override: dict) -> dict:
    out = {k: (dict(v) if isinstance(v, dict) else list(v) if isinstance(v, list) else v) for k, v in base.items()}
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge_nested_dict(out[key], value)
        else:
            out[key] = value
    return out


def load_command_language(path: Path | None = None, previous: Optional[dict] = None) -> dict:
    """외부 자연어 사전을 atomic하게 로드합니다. 실패 시 마지막 정상값을 유지합니다."""
    fallback = previous if isinstance(previous, dict) else DEFAULT_COMMAND_LANGUAGE
    try:
        location = Path(path) if path is not None else language_path()
        shared = location.resolve() == language_path().resolve()
        if shared:
            raw = command_language(location)
        else:
            raw = json.loads(location.read_text(encoding="utf-8"))
            # A relocated full dictionary and an explicit legacy override use
            # the same validated command section, without executing any data.
            if isinstance(raw, dict) and "command_language" in raw:
                raw = raw["command_language"]
                shared = True
        if not isinstance(raw, dict):
            raise ValueError("root must be an object")
        merged = raw if shared else _merge_nested_dict(DEFAULT_COMMAND_LANGUAGE, raw)
        defaults = merged.get("defaults") or {}
        if not str(defaults.get("symbol") or "").strip():
            raise ValueError("defaults.symbol is empty")
        tf_raw = str(defaults.get("timeframe") or DEFAULT_COMMAND_TF).strip()
        if tf_raw not in MT5_TIMEFRAMES:
            raise ValueError("defaults.timeframe is invalid")
        for section in ("symbols", "phrase_aliases", "oz_direction", "ma_family", "cross", "wonbi_side", "condition_macros"):
            if not isinstance(merged.get(section), dict):
                raise ValueError(f"{section} must be an object")
        for macro_name, macro in (merged.get("condition_macros") or {}).items():
            if not str(macro_name or "").strip() or not isinstance(macro, dict):
                raise ValueError("condition_macros entry must be an object")
            aliases = macro.get("aliases") or []
            conditions = macro.get("conditions") or []
            combination = str(macro.get("combination") or "ALL").upper()
            if not isinstance(aliases, list):
                raise ValueError(f"condition_macros.{macro_name}.aliases must be an array")
            if combination not in {"ALL", "ANY"}:
                raise ValueError(f"condition_macros.{macro_name}.combination must be ALL/ANY")
            if not isinstance(conditions, list) or not conditions:
                raise ValueError(f"condition_macros.{macro_name}.conditions must be a non-empty array")
            for condition in conditions:
                if not isinstance(condition, dict) or not str(condition.get("kind") or "").strip():
                    raise ValueError(f"condition_macros.{macro_name}.conditions item is invalid")
        return merged
    except Exception as exc:
        logging.error("❌ [Command Aliases] 로드 실패 - 마지막 정상 설정 유지 | %s | %s", path, exc)
        return fallback


def normalize_tf(value: object) -> str:
    raw = str(value or "").strip().lower().replace(" ", "")
    m = re.fullmatch(syntax_literal('lexical_normalize_tf_pattern_1'), raw)
    if m:
        unit = {syntax_literal('lexical_normalize_tf_term_1'): "m", syntax_literal('lexical_normalize_tf_term_2'): "h", syntax_literal('lexical_normalize_tf_term_3'): "d"}[m.group(2)]
        raw = f"{int(m.group(1))}{unit}"
    if raw == "24h":
        raw = "1d"
    return raw if raw in MT5_TIMEFRAMES else ""


def tf_seconds(tf: str) -> int:
    m = re.fullmatch(syntax_literal('lexical_tf_seconds_pattern_1'), normalize_tf(tf))
    if not m:
        return 10**12
    n = int(m.group(1))
    return n * (60 if m.group(2) == "m" else 3600 if m.group(2) == "h" else 86400)


class CommandInterpreter:
    """Natural-language boundary. It interprets text but never executes a strategy."""

    def __init__(
        self,
        config: dict[str, str],
        aliases_path: Path | None = None,
        allowed_symbols_provider: Optional[Callable[[], list[str]]] = None,
    ) -> None:
        self.config = config
        self.aliases_path = Path(aliases_path) if aliases_path is not None else resolve_command_language_path(config)
        self.allowed_symbols_provider = allowed_symbols_provider or (lambda: list(configured_symbols(self.config)))
        self.command_aliases = load_command_language(self.aliases_path)
        self._last_alias_check = 0.0
        try:
            self._last_alias_mtime: Optional[float] = self.aliases_path.stat().st_mtime
        except FileNotFoundError:
            self._last_alias_mtime = None

    def language(self) -> dict:
        return self.command_aliases or DEFAULT_COMMAND_LANGUAGE

    def command_default(self, key: str, fallback):
        defaults = self.language().get("defaults") or {}
        value = defaults.get(key, fallback)
        return fallback if value is None or value == "" else value

    def default_tf(self) -> str:
        return normalize_tf(self.command_default("timeframe", DEFAULT_COMMAND_TF)) or DEFAULT_COMMAND_TF

    def default_symbol(self) -> str:
        return str(self.command_default("symbol", DEFAULT_COMMAND_GOLD_SYMBOL)).strip() or DEFAULT_COMMAND_GOLD_SYMBOL

    def condition_macros(self) -> dict:
        macros = self.language().get("condition_macros") or {}
        return macros if isinstance(macros, dict) else {}

    def condition_macro(self, name: object) -> Optional[dict]:
        key = str(name or "").strip()
        macro = self.condition_macros().get(key)
        return dict(macro) if isinstance(macro, dict) else None

    def condition_macro_matches(self, text: str) -> list[dict]:
        """조건 매크로 이름/별칭의 위치를 반환합니다. 실행 의미는 사전 정의만 사용합니다."""
        source = str(text or "")
        low = source.lower()
        hits: list[dict] = []
        occupied: list[tuple[int, int]] = []
        terms: list[tuple[str, str, dict]] = []
        for canonical, raw_macro in self.condition_macros().items():
            if not isinstance(raw_macro, dict):
                continue
            macro = dict(raw_macro)
            for term in (str(canonical), *(str(x) for x in (macro.get("aliases") or []))):
                term = term.strip()
                if term:
                    terms.append((term, str(canonical), macro))
        terms.sort(key=lambda item: len(item[0]), reverse=True)
        for term, canonical, macro in terms:
            for match in re.finditer(re.escape(term), low, re.I):
                start, end = match.span()
                if any(start < b and end > a for a, b in occupied):
                    continue
                occupied.append((start, end))
                hits.append({
                    "name": canonical, "start": start, "end": end,
                    "definition": dict(macro),
                })
        hits.sort(key=lambda item: (item["start"], item["end"]))
        return hits

    def expand_condition_macro(self, name: object, tf: str, direction: object = None) -> dict:
        """사전 매크로를 primitive 조건 descriptor로 펼칩니다."""
        macro = self.condition_macro(name)
        if macro is None:
            raise ValueError(f"지원하지 않는 condition macro: {name}")
        resolved_tf = normalize_tf(tf) or self.default_tf()
        resolved_direction = str(direction or "AUTO").strip().upper()
        if resolved_direction not in {"AUTO", "LONG", "SHORT"}:
            resolved_direction = "AUTO"
        conditions: list[dict] = []
        for raw in macro.get("conditions") or []:
            item = dict(raw)
            if str(item.get("tf") or "") == "$tf" or not str(item.get("tf") or "").strip():
                item["tf"] = resolved_tf
            if str(item.get("direction") or "").upper() == "$DIRECTION" or not str(item.get("direction") or "").strip():
                item["direction"] = resolved_direction
            conditions.append(item)
        return {
            "name": str(name),
            "combination": str(macro.get("combination") or "ALL").upper(),
            "conditions": tuple(conditions),
        }

    @staticmethod
    def alias_present(text: str, alias: str) -> bool:
        low = str(text or "").lower()
        token = str(alias or "").strip().lower()
        if not token:
            return False
        if len(token) == 1:
            return re.search(syntax_literal('lexical_alias_single_format').format(token=re.escape(token)), low, re.I) is not None
        if re.fullmatch(syntax_literal('lexical_alias_present_pattern_1'), token, re.I):
            return re.search(syntax_literal('lexical_alias_word_format').format(token=re.escape(token)), low, re.I) is not None
        return token in low

    def allowed_symbols(self) -> list[str]:
        values = self.allowed_symbols_provider() or []
        return list(dict.fromkeys(str(x).strip() for x in values if str(x).strip()))

    def default_gold_symbol(self, allowed: Optional[list[str]] = None) -> str:
        symbols = list(allowed) if allowed is not None else self.allowed_symbols()
        for symbol in symbols:
            low = symbol.lower()
            if any(marker in low for marker in grammar_terms('default_gold_markers')):
                return symbol
        return self.default_symbol()

    def explicit_symbol_from_text(self, text: str) -> Optional[str]:
        low = str(text or "").lower()
        for symbol in sorted(self.allowed_symbols(), key=len, reverse=True):
            if self.alias_present(low, symbol):
                return symbol
        for canonical, aliases in (self.language().get("symbols") or {}).items():
            if self.alias_present(low, str(canonical)) or any(self.alias_present(low, str(a)) for a in (aliases or [])):
                return str(canonical)
        return None

    def normalize_command_text(self, text: str) -> str:
        """외부 자연어 사전을 canonical 표현으로 치환합니다. 내부 action 이름은 만들지 않습니다."""
        clean = str(text or "").strip()
        if not clean:
            return clean
        lang = self.language()
        replacements: list[tuple[str, str]] = []

        for canonical, macro in (lang.get("condition_macros") or {}).items():
            if not isinstance(macro, dict):
                continue
            for alias in macro.get("aliases") or []:
                if str(alias).strip():
                    replacements.append((str(alias), str(canonical)))

        for canonical, aliases in (lang.get("phrase_aliases") or {}).items():
            for alias in aliases or []:
                if str(alias).strip():
                    replacements.append((str(alias), str(canonical)))
        for direction, aliases in (lang.get("oz_direction") or {}).items():
            canonical = syntax_literal('lexical_normalize_command_text_term_1') if str(direction).upper() == "LONG" else syntax_literal('lexical_normalize_command_text_term_4') if str(direction).upper() == "SHORT" else ""
            if canonical:
                for alias in aliases or []:
                    if str(alias).strip():
                        replacements.append((str(alias), canonical))
        for family, aliases in (lang.get("ma_family") or {}).items():
            canonical = str(family).lower()
            for alias in aliases or []:
                if str(alias).strip():
                    replacements.append((str(alias), canonical))
        for direction, aliases in (lang.get("cross") or {}).items():
            canonical = syntax_literal('lexical_normalize_command_text_term_2') if str(direction).upper() == "LONG" else syntax_literal('lexical_normalize_command_text_term_5') if str(direction).upper() == "SHORT" else ""
            if canonical:
                for alias in aliases or []:
                    if str(alias).strip():
                        replacements.append((str(alias), canonical))
        for side, aliases in (lang.get("wonbi_side") or {}).items():
            canonical = syntax_literal('lexical_normalize_command_text_term_3') if str(side).upper() == "LOWER" else syntax_literal('lexical_normalize_command_text_term_6') if str(side).upper() == "UPPER" else ""
            if canonical:
                for alias in aliases or []:
                    if str(alias).strip():
                        replacements.append((str(alias), canonical))

        replacements.sort(key=lambda item: len(item[0]), reverse=True)
        out = clean
        for alias, canonical in replacements:
            out = re.sub(re.escape(alias), canonical, out, flags=re.I)
        return re.sub(syntax_literal('lexical_normalize_command_text_pattern_3'), " ", out).strip()

    def reload_if_needed(self, now_mono: float) -> bool:
        interval = float(self.config.get("COMMAND_ALIASES_RELOAD_SEC", DEFAULT_COMMAND_ALIASES_RELOAD_SEC))
        if now_mono - self._last_alias_check < max(1.0, interval):
            return False
        self._last_alias_check = now_mono
        try:
            mtime = self.aliases_path.stat().st_mtime
        except FileNotFoundError:
            return False
        if self._last_alias_mtime is not None and mtime == self._last_alias_mtime:
            return False
        previous = self.command_aliases
        if self.aliases_path.resolve() == language_path().resolve():
            try:
                reload_dictionary(self.aliases_path)
            except Exception as exc:
                logging.error("❌ [Command Aliases] 로드 실패 - 마지막 정상 설정 유지 | %s | %s", self.aliases_path, exc)
                loaded = previous
            else:
                loaded = load_command_language(self.aliases_path, previous=previous)
        else:
            loaded = load_command_language(self.aliases_path, previous=previous)
        changed = loaded is not previous
        if changed:
            self.command_aliases = loaded
            if logging.getLogger().isEnabledFor(logging.INFO):
                logging.info("♻️ [Command Aliases] hot reload 완료 | %s", self.aliases_path)
        self._last_alias_mtime = mtime
        return changed

    def gemini_prompt(self, text):
        macro_names = ", ".join(self.condition_macros()) or "(없음)"
        return f"""당신은 트레이딩 명령의 자연어 표현만 정규화하는 통역기입니다.
새 조건, 종목, 시간봉, 방향, 올존, 무지성, 브레이커를 창작하면 안 됩니다.
입력에 없는 값은 생략한 채 유지하세요. 실행하지 말고 JSON 객체 하나만 반환하세요.
반환 스키마: {{"canonical_text":"..."}}
조건 매크로: {macro_names}
권장 표준 표현: 골드/XAUUSD, 비트/BTCUSD, 1분/15분/1시간, 상승추세/하락추세, 추세점수 몇 점, 하단 원비/상단 원비, FVG, 하단 OUT/상단 OUT, EMA/HMA, 골크/데크, 매수올존/매도올존, 알려줘.
사용자 문장: {text}
"""

    def validate_gemini_reply(self, text, raw):
        raw = re.sub(syntax_literal('lexical_validate_gemini_reply_pattern_1'), "", raw, flags=re.I | re.S).strip()
        candidate = str(json.loads(raw).get("canonical_text") or "").strip()
        if not candidate:
            return None
        original = str(text or "").lower()
        cand_low = candidate.lower()
        for token in (syntax_literal('lexical_validate_gemini_reply_term_1'), syntax_literal('lexical_validate_gemini_reply_term_2'), syntax_literal('lexical_validate_gemini_reply_term_3')):
            original_has = token in original
            candidate_has = token in cand_low
            if original_has != candidate_has:
                reason = "새 의미 추가" if candidate_has else "기존 의미 누락"
                logging.warning("[Composer/Gemini] canonicalize 거부 - %s: %s", reason, token)
                return None
        original_symbol = self.explicit_symbol_from_text(text)
        candidate_symbol = self.explicit_symbol_from_text(candidate)
        if candidate_symbol and not original_symbol:
            logging.warning("[Composer/Gemini] canonicalize 거부 - 새 종목 추가: %s", candidate_symbol)
            return None
        if original_symbol and candidate_symbol and original_symbol != candidate_symbol:
            logging.warning("[Composer/Gemini] canonicalize 거부 - 종목 변경: %s -> %s", original_symbol, candidate_symbol)
            return None
        if self.tf_occurrences(candidate) and not self.tf_occurrences(text):
            logging.warning("[Composer/Gemini] canonicalize 거부 - 새 시간봉 추가")
            return None
        return candidate


    def parse_symbol(self, text: str) -> Optional[str]:
        low = str(text or "").lower()
        allowed = self.allowed_symbols()
        for symbol in sorted(allowed, key=len, reverse=True):
            if symbol.lower() in low:
                return symbol

        symbol_aliases = self.language().get("symbols") or {}
        for canonical, aliases in symbol_aliases.items():
            canonical = str(canonical).strip()
            terms = [canonical, *(str(x) for x in (aliases or []))]
            if not any(self.alias_present(low, term) for term in terms if term):
                continue
            if canonical in allowed or not allowed:
                return canonical
            canonical_low = canonical.lower()
            for symbol in allowed:
                sl = symbol.lower()
                family_match = any(canonical.startswith(prefix) and any(marker in sl for marker in markers)
                                   for prefix, markers in grammar_terms('broker_symbol_families').items())
                if canonical_low in sl or family_match:
                    return symbol
            if canonical == self.default_symbol():
                return self.default_gold_symbol(allowed)
            return None
        default_symbol = self.default_symbol()
        if default_symbol in allowed or not allowed:
            return default_symbol
        if default_symbol == DEFAULT_COMMAND_GOLD_SYMBOL:
            return self.default_gold_symbol(allowed)
        return default_symbol

    @staticmethod
    def tf_occurrences(text: str) -> list[tuple[int, int, str]]:
        raw = str(text or "").lower()
        found: list[tuple[int, int, str]] = []
        pattern = re.compile(syntax_literal('lexical_tf_occurrences_pattern_1'))
        for m in pattern.finditer(raw):
            n = int(m.group(1))
            unit = m.group(2)
            if unit in {syntax_literal('lexical_tf_occurrences_term_1'), "m"}:
                tf = f"{n}m"
            elif unit in {syntax_literal('lexical_tf_occurrences_term_2'), "h"}:
                tf = f"{n}h"
            else:
                tf = f"{n}d"
            found.append((m.start(), m.end(), tf))
        return found

    @staticmethod
    def nearest_tf_before(occurrences: list[tuple[int, int, str]], pos: int) -> str:
        before = [x for x in occurrences if x[1] <= pos]
        return before[-1][2] if before else ""

    @staticmethod
    def keyword_pos(text: str, words: Iterable[str]) -> int:
        low = text.lower()
        hits = [low.find(w.lower()) for w in words if low.find(w.lower()) >= 0]
        return min(hits) if hits else -1

    def parse_oz_direction(self, text: str) -> Optional[str]:
        low = self.normalize_command_text(text).lower()
        profiles = '|'.join(re.escape(word) for word in grammar_terms('oz_profile_words'))
        directions = grammar_terms('oz_direction_words')
        profile = syntax_literal('lexical_oz_profile_format').format(profiles=profiles)
        long_words = '|'.join(re.escape(word) for word in directions['LONG'])
        short_words = '|'.join(re.escape(word) for word in directions['SHORT'])
        direction_pattern = syntax_literal('lexical_oz_direction_format')
        long_hit = re.search(direction_pattern.format(words=long_words, profile=profile), low, re.I) is not None
        short_hit = re.search(direction_pattern.format(words=short_words, profile=profile), low, re.I) is not None
        if long_hit and short_hit:
            raise ValueError("매수/하단 올존과 매도/상단 올존을 동시에 지정할 수 없습니다.")
        if long_hit:
            return "LONG"
        if short_hit:
            return "SHORT"
        return None

    def extract_oz_tfs(self, text: str, occurrences: list[tuple[int, int, str]], last_condition_pos: int) -> tuple[str, ...]:
        raw = str(text or "")
        low = raw.lower()
        oz_pos = low.rfind(syntax_literal('lexical_extract_oz_name'))
        if oz_pos < 0:
            return ()
        zone_start = max(0, last_condition_pos)
        zone = low[zone_start:oz_pos]
        all_requested = syntax_literal('lexical_extract_oz_tfs_term_1') in zone

        range_matches = list(re.finditer(syntax_literal('lexical_extract_oz_tfs_pattern_2'), zone))
        if range_matches:
            m = range_matches[-1]
            n = int(m.group(1))
            unit = m.group(2)
            op = m.group(3)
            anchor = f"{n}{'m' if unit in {syntax_literal('lexical_extract_oz_tfs_term_6'),'m'} else 'h' if unit in {syntax_literal('lexical_extract_oz_tfs_term_7'),'h'} else 'd'}"
            threshold = tf_seconds(anchor)
            if op == syntax_literal('lexical_extract_oz_tfs_term_2'):
                return tuple(tf for tf in OZ_BASE_TFS if tf_seconds(tf) <= threshold)
            if op == syntax_literal('lexical_extract_oz_tfs_term_3'):
                return tuple(tf for tf in OZ_BASE_TFS if tf_seconds(tf) >= threshold)
            if op == syntax_literal('lexical_extract_oz_tfs_term_4'):
                return tuple(tf for tf in OZ_BASE_TFS if tf_seconds(tf) < threshold)
            if op == syntax_literal('lexical_extract_oz_tfs_term_5'):
                return tuple(tf for tf in OZ_BASE_TFS if tf_seconds(tf) > threshold)

        between = re.search(
            syntax_literal('lexical_extract_oz_tfs_pattern_1'),
            zone,
        )
        if between:
            def _mk(n_s, u):
                return f"{int(n_s)}{'m' if u in {syntax_literal('lexical__mk_term_1'),'m'} else 'h' if u in {syntax_literal('lexical__mk_term_2'),'h'} else 'd'}"
            a = tf_seconds(_mk(between.group(1), between.group(2)))
            b = tf_seconds(_mk(between.group(3), between.group(4)))
            lo_s, hi_s = sorted((a, b))
            return tuple(tf for tf in OZ_BASE_TFS if lo_s <= tf_seconds(tf) <= hi_s)

        if all_requested:
            return OZ_BASE_TFS

        candidates = [
            tf for start, end, tf in occurrences
            if start > last_condition_pos and end <= oz_pos and tf in OZ_BASE_TFS
        ]
        if candidates:
            return tuple(dict.fromkeys(candidates))
        return (self.default_tf(),)

    @staticmethod
    def parse_time_filters(text: str) -> tuple[str, ...]:
        compact = re.sub(syntax_literal('lexical_parse_time_filters_pattern_1'), "", str(text or "").lower())
        out = []
        for code, names in grammar_terms('session_names').items():
            for ko in names:
                if ko not in compact:
                    continue
                prefix = "OPENING_" if (syntax_literal('lexical_parse_time_filters_term_1') + ko in compact or ko + syntax_literal('lexical_parse_time_filters_term_1') in compact) else "MAIN_"
                out.append(prefix + code)
                break
        return tuple(out)

    @staticmethod
    def duration_matches(text: str) -> list[tuple[int, int, float]]:
        raw = str(text or "").lower()
        out: list[tuple[int, int, float]] = []
        pattern = re.compile(syntax_literal('lexical_duration_matches_pattern_1'))
        for m in pattern.finditer(raw):
            n = float(m.group(1))
            unit = m.group(2)
            mult = 1.0 if unit in {syntax_literal('lexical_duration_matches_term_1'), "s"} else 60.0 if unit in {syntax_literal('lexical_duration_matches_term_2'), "m"} else 3600.0 if unit in {syntax_literal('lexical_duration_matches_term_3'), "h"} else 86400.0
            sec = n * mult
            if sec > 0:
                out.append((m.start(), m.end(), sec))
        return out

    def parse_start_at(self, text: str, now: Optional[dt.datetime] = None) -> Optional[dict]:
        """`2시부터`, `오후 2시부터`, `14:30부터`를 다음 실제 시작시각으로 해석합니다.

        시각만 쓴 1~12시는 현재 시점 이후 가장 가까운 동일 시각(AM/PM)을 선택합니다.
        예: 13:10의 `2시부터` -> 오늘 14:00, 15:10의 `2시부터` -> 다음날 02:00.
        """
        raw = str(text or "")
        m = re.search(
            syntax_literal('lexical_parse_start_at_pattern_1'),
            raw, re.I,
        )
        if not m:
            return None

        day_word, meridiem = m.group(1), m.group(2)
        if m.group(3) is not None:
            hour = int(m.group(3))
            minute = int(m.group(4) or 0)
        else:
            hour = int(m.group(5))
            minute = int(m.group(6) or 0)
        if minute < 0 or minute > 59:
            raise ValueError(f"시작시각 분 값 오류: {minute}")

        tz_name = str(
            self.config.get("COMMAND_TIMEZONE")
            or self.config.get("TIMEZONE")
            or "Asia/Seoul"
        ).strip() or "Asia/Seoul"
        try:
            tz = ZoneInfo(tz_name)
        except Exception:
            tz = ZoneInfo("Asia/Seoul")
        current = now or dt.datetime.now(tz)
        if current.tzinfo is None:
            current = current.replace(tzinfo=tz)
        else:
            current = current.astimezone(tz)

        base_date = current.date() + dt.timedelta(days=1 if day_word == syntax_literal('lexical_parse_start_at_term_3') else 0)

        def candidate(h: int, day_offset: int = 0) -> dt.datetime:
            return dt.datetime.combine(
                base_date + dt.timedelta(days=day_offset), dt.time(h, minute), tzinfo=tz
            )

        if meridiem:
            if not (1 <= hour <= 12):
                raise ValueError(f"오전/오후 시작시각 오류: {hour}시")
            h24 = hour % 12 + (12 if meridiem == syntax_literal('lexical_parse_start_at_term_2') else 0)
            target = candidate(h24)
            if day_word == syntax_literal('lexical_parse_start_at_term_1') and target <= current:
                raise ValueError(f"오늘 {target:%H:%M}은 이미 지났습니다")
            if day_word is None and target <= current:
                target += dt.timedelta(days=1)
        elif 0 <= hour <= 23 and hour > 12:
            target = candidate(hour)
            if day_word == syntax_literal('lexical_parse_start_at_term_1') and target <= current:
                raise ValueError(f"오늘 {target:%H:%M}은 이미 지났습니다")
            if day_word is None and target <= current:
                target += dt.timedelta(days=1)
        elif hour == 0:
            target = candidate(0)
            if day_word == syntax_literal('lexical_parse_start_at_term_1') and target <= current:
                raise ValueError(f"오늘 {target:%H:%M}은 이미 지났습니다")
            if day_word is None and target <= current:
                target += dt.timedelta(days=1)
        elif 1 <= hour <= 12:
            # 오전/오후 생략 시 현재 이후 가장 가까운 같은 시각을 선택합니다.
            hours = [hour % 12, (hour % 12) + 12]
            candidates = sorted(candidate(h) for h in hours)
            future = [x for x in candidates if x > current] if day_word != syntax_literal('lexical_parse_start_at_term_3') else candidates
            if day_word == syntax_literal('lexical_parse_start_at_term_1'):
                future = [x for x in candidates if x > current]
                if not future:
                    raise ValueError(f"오늘 {hour}시는 이미 지났습니다")
                target = future[0]
            elif day_word == syntax_literal('lexical_parse_start_at_term_3'):
                target = candidates[0]
            elif future:
                target = future[0]
            else:
                target = candidate(hours[0], day_offset=1)
        else:
            raise ValueError(f"시작시각 오류: {hour}시")

        return {
            "timestamp": float(target.timestamp()),
            "start": m.start(),
            "end": m.end(),
            "label": target.strftime("%Y-%m-%d %H:%M %Z"),
            "timezone": tz.key,
        }

    @staticmethod
    def overlaps_span(start: int, end: int, spans: Iterable[tuple[int, int, object]]) -> bool:
        return any(start < b and end > a for a, b, *_ in spans)

    @classmethod
    def is_trend_score_query(cls, text: str) -> bool:
        """'골드 15분 추세점수 몇 점?' 같은 전체 지표 추세점수 단발 조회인지 판정합니다.

        '추세점수 40 이상이면' 같은 임계값 조건은 조회가 아니라 Watch 조건이므로 제외합니다.
        """
        compact = re.sub(syntax_literal('lexical_is_trend_score_query_pattern_1'), "", str(text or "").lower())
        if syntax_literal('lexical_is_trend_score_query_term_1') in compact or not any(word in compact for word in grammar_terms('trend_score_words')):
            return False
        if re.search(syntax_literal('lexical_is_trend_score_query_pattern_2'), compact):
            return False
        return any(word in compact for word in grammar_terms('trend_score_questions'))

    def detect_intent(self, text: str, trigger_parser: Callable[[str, int, list], list]) -> str:
        """최상위 라우팅만 결정합니다. 실제 Watch/Payload 생성은 호출자가 담당합니다."""
        clean = str(text or "").strip()
        low = clean.lower()
        compact = re.sub(syntax_literal('lexical_detect_intent_pattern_1'), "", low)
        if not clean:
            return "EMPTY"
        if compact == syntax_literal('lexical_detect_intent_term_1'):
            return "RESET"
        if compact == syntax_literal('lexical_detect_intent_term_2'):
            return "LIST"
        if syntax_literal('lexical_detect_intent_term_3') in low:
            return "OZ"
        # 전체 지표 추세점수는 사용자가 명시적으로 물을 때만 계산합니다.
        if self.is_trend_score_query(clean):
            return "TREND_SCORE_QUERY"

        has_notify = syntax_literal('lexical_detect_intent_term_4') in low
        has_duration = bool(self.duration_matches(clean)) or syntax_literal('lexical_detect_intent_term_5') in low
        generic_candidate = False
        if has_notify and not has_duration:
            if self.condition_macro_matches(clean):
                return "CONDITION_NOTIFY"
            triggers = trigger_parser(clean, len(clean), [])
            if len(triggers) == 1:
                if syntax_literal('lexical_detect_intent_term_6') in low:
                    return "CONDITION_NOTIFY"
                return "GENERIC_WATCH"
            generic_candidate = any(x in low for x in grammar_terms('generic_candidates'))

        query_words = grammar_terms('query_words')
        if syntax_literal('lexical_detect_intent_term_6') in low and any(x in low for x in query_words):
            return "TREND_QUERY"
        if "fvg" in low and any(x in low for x in query_words):
            return "FVG_QUERY"
        if syntax_literal('lexical_detect_intent_term_7') in low and any(x in low for x in query_words):
            return "SWEEP_QUERY"
        if generic_candidate:
            return "GENERIC_INVALID"
        if has_duration:
            return "TIMED_CHAIN"
        return "FALLBACK"

    @staticmethod
    def generic_invalid_message(text: str) -> str:
        low = str(text or "").lower()
        if syntax_literal('lexical_generic_invalid_message_term_2') in low and any(x in low for x in (syntax_literal('lexical_generic_invalid_message_term_3'), "out", syntax_literal('lexical_generic_invalid_message_term_1'), "fvg", "ema", "hma")):
            return "추세와 다른 조건을 묶은 복합 알림은 아직 단일 Generic 감시로 등록하지 않습니다"
        if "ema" in low or "hma" in low:
            return "MA Cross 형식이 불완전합니다. 예: 비트 1분 50 200지수 골크나면 알려줘"
        if syntax_literal('lexical_generic_invalid_message_term_1') in low:
            return "원비 감시 형식이 불완전합니다. 예: 골드 5분 하단 원비 알려줘"
        if any(x in low for x in (syntax_literal('lexical_generic_invalid_message_term_4'), syntax_literal('lexical_generic_invalid_message_term_5'))):
            return "전일 고가/저가 조건에는 터치 표현이 필요합니다. 예: 골드 5분 전일고가 터치 알려줘"
        if syntax_literal('lexical_generic_invalid_message_term_3') in low or "out" in low:
            return "Percentile 조건 형식이 불완전합니다. 예: 골드 1분 하단 OUT→IN 알려줘"
        if "fvg" in low:
            return "FVG 알림 형식이 불완전합니다. 생성 알림은 'FVG 생성 알려줘', 현재 상태는 'FVG 상태 알려줘'를 사용하세요"
        return "조건 감시 형식이 불완전합니다"
