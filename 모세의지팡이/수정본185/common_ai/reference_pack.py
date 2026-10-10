"""Approved public strategy examples, loaded once and selected locally.

This module supplies language reference material only. It does not classify
requests, interpret strategies, execute recipes, or substitute an answer.
"""

from __future__ import annotations

from functools import lru_cache
import json
import math
from pathlib import Path
import re
from types import MappingProxyType
from typing import Mapping
import unicodedata


from moses_language import (cache_clear as _clear_language_cache, cache_info as _language_cache_info,
                            concepts, reference_pack as _shared_reference_pack)

_TF = re.compile(r"(\d+)\s*(분|시간|일(?:봉)?|[mhd])", re.I)
_WORDS = re.compile(r"[a-z][a-z0-9_]*|[가-힣]+|\d+", re.I)


def _freeze(value):
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


def load_pack(path: str | Path | None = None) -> Mapping:
    """Return the same immutable in-memory pack until explicitly invalidated.

    The default view shares the portable MOSES language dictionary. An explicit
    path continues to support independent legacy public reference packs.
    """
    if path is None:
        return _shared_reference_pack()
    return _load_pack(Path(path).resolve())


@lru_cache(maxsize=4)
def _load_pack(location: Path) -> Mapping:
    with location.open(encoding="utf-8") as stream:
        raw = json.load(stream)
    required = {"version", "core_rules", "examples"}
    if not isinstance(raw, dict) or not required <= set(raw) or set(raw) - required - {"condition_terms"}:
        raise ValueError("AI 참고자료 구조가 올바르지 않습니다.")
    if not isinstance(raw["version"], str) or not raw["version"]:
        raise ValueError("AI 참고자료 버전이 없습니다.")
    if not isinstance(raw["core_rules"], list) or not all(
        isinstance(rule, str) and rule for rule in raw["core_rules"]
    ):
        raise ValueError("AI 참고자료 공통 규칙이 올바르지 않습니다.")
    if not isinstance(raw["examples"], list):
        raise ValueError("AI 참고자료 예제 목록이 올바르지 않습니다.")
    condition_terms = raw.setdefault("condition_terms", {})
    if not isinstance(condition_terms, dict):
        raise ValueError("AI 참고자료 조건 용어가 올바르지 않습니다.")
    for kind, aliases in condition_terms.items():
        if not isinstance(kind, str) or not kind or not isinstance(aliases, list):
            raise ValueError("AI 참고자료 조건 용어가 올바르지 않습니다.")
        for alias in aliases:
            words = alias if isinstance(alias, list) else [alias]
            if not words or not all(isinstance(word, str) and word for word in words):
                raise ValueError("AI 참고자료 조건 용어가 올바르지 않습니다.")
    ids = set()
    for example in raw["examples"]:
        if not isinstance(example, dict) or set(example) != {"id", "input", "meaning"}:
            raise ValueError("AI 참고자료 예제 구조가 올바르지 않습니다.")
        if not all(isinstance(value, str) and value for value in example.values()):
            raise ValueError("AI 참고자료 예제 내용이 없습니다.")
        if example["id"] in ids:
            raise ValueError("AI 참고자료 예제 번호가 중복됩니다.")
        ids.add(example["id"])
    return _freeze(raw)


# Expose normal cache controls while canonicalizing optional path arguments
# before caching (load_pack() and load_pack(None) must not read twice).
def _clear_pack_cache():
    _load_pack.cache_clear()
    _clear_language_cache()
    _index.cache_clear()


load_pack.cache_clear = _clear_pack_cache
load_pack.cache_info = _language_cache_info


def _lookup_text(text: str) -> str:
    # Numbers select periods/TFs in the authoritative intent, not vocabulary
    # families. Removing them here lets EMA200 touch and EMA50 touch share the
    # same public reference entry without individual period rules.
    return re.sub(r"[\s\d]+", "", unicodedata.normalize("NFKC", text).casefold())


def relevant_kinds(text: str, *, pack_path: str | Path | None = None) -> list[str]:
    """Find vocabulary topics whose detailed public contract may be useful.

    The result is reference selection, not an intent or condition decision.
    Alternative aliases OR together; a list alias is an AND of phrase parts.
    Every kind uses this same matching rule, with no strategy-ID branches.
    """
    if not isinstance(text, str) or not text.strip():
        return []
    query = _lookup_text(text)
    spaced_query = re.sub(r"\d+", "", unicodedata.normalize("NFKC", text).casefold())

    def matches(word):
        if word.isascii():
            normalized = re.sub(r"\d+", "", unicodedata.normalize("NFKC", word).casefold())
            phrase = r"\s*".join(re.escape(part) for part in normalized.split())
            return re.search(r"(?<![a-z_])" + phrase + r"(?![a-z_])", spaced_query) is not None
        return _lookup_text(word) in query

    result = []
    for kind, aliases in load_pack(pack_path)["condition_terms"].items():
        for alias in aliases:
            words = alias if isinstance(alias, tuple) else (alias,)
            if all(matches(word) for word in words):
                result.append(kind)
                break
    return result


def _terms(text: str, *, _concepts=None) -> frozenset[str]:
    normalized = unicodedata.normalize("NFKC", text).lower()
    compact = re.sub(r"\s+", "", normalized)
    terms = set()
    for name, aliases in (concepts() if _concepts is None else _concepts).items():
        for alias in aliases:
            # Latin terms must not match unrelated words such as 'studio'.
            if alias.isascii() and re.fullmatch(r"[a-z]+", alias):
                matched = re.search(r"(?<![a-z])" + re.escape(alias) + r"(?![a-z])", compact)
            else:
                matched = alias in compact
            if matched:
                terms.add("domain:" + name)
                break
    for number, unit in _TF.findall(normalized):
        minutes = int(number) * ({"시간": 60, "h": 60, "일": 1440, "일봉": 1440, "d": 1440}.get(unit.lower(), 1))
        terms.add(f"tf:{minutes}")
    for word in _WORDS.findall(normalized):
        if word.isdecimal():
            continue
        if re.fullmatch(r"[가-힣]+", word):
            # Korean particles need not be enumerated. Shared short character
            # spans give related inflected domain phrases a common local index.
            for offset in range(max(0, len(word) - 2)):
                terms.add("lex:" + word[offset:offset + 3])
        elif len(word) >= 3:
            terms.add("lex:" + word)
    return frozenset(terms)


def _index(path: str | Path | None):
    pack = load_pack(path)
    examples = tuple((example['input'], example['meaning']) for example in pack['examples'])
    topics = tuple((name, tuple(aliases)) for name, aliases in concepts().items())
    return _index_content(examples, topics)


@lru_cache(maxsize=4)
def _index_content(examples, topics):
    # The same path may now contain new examples or language topics after a
    # command dictionary hot reload. Cache the immutable content, not its path.
    topic_snapshot = dict(topics)
    documents = tuple(
        (_terms(text, _concepts=topic_snapshot), _terms(text + " " + meaning, _concepts=topic_snapshot))
        for text, meaning in examples
    )
    frequency = {}
    for _, terms in documents:
        for term in terms:
            frequency[term] = frequency.get(term, 0) + 1
    weights = {term: math.log(1 + len(documents) / count) for term, count in frequency.items()}
    return documents, weights


_index.cache_clear = _index_content.cache_clear
_index.cache_info = _index_content.cache_info


def select_examples(
    text: str,
    limit: int = 2,
    *,
    max_chars: int = 1200,
    pack_path: str | Path | None = None,
) -> list[dict[str, str]]:
    """Select relevant complete examples; never interpret or trim their meaning.

    Relevance is generic local vocabulary / TF / character-span overlap. Number
    labels and entire source sentences are not matching rules. No relevant
    domain overlap means no example is attached. The budget includes each whole
    serialized example; over-budget examples are skipped without truncation.
    """
    if not isinstance(text, str) or not text.strip() or limit <= 0 or max_chars <= 0:
        return []
    query = _terms(text)
    query_domains = {term for term in query if term.startswith("domain:")}
    if not query_domains:
        return []
    pack = load_pack(pack_path)
    documents, weights = _index(pack_path)
    candidates = []
    for position, (input_terms, terms) in enumerate(documents):
        matched_domains = query_domains.intersection(terms)
        if not matched_domains:
            continue
        # Prefer full domain coverage to a common incidental word. Extra
        # condition families lower rank so a simple request stays simple.
        score = sum(
            weights.get(term, 0) * (
                4 if term.startswith("domain:") else 2 if term.startswith("tf:") else 0.1
            )
            for term in query.intersection(terms)
        )
        score -= 4 * len({term for term in input_terms if term.startswith("domain:")} - query_domains)
        # Input coverage comes first: explanations such as 'not a NEW event'
        # must not outrank an example that actually requests a NEW event.
        candidates.append((-len(query_domains.intersection(input_terms)), -len(matched_domains), -score, position))
    candidates.sort()
    selected = []
    used = 2  # JSON list delimiters
    for _, _, _, position in candidates:
        example = dict(pack["examples"][position])
        size = len(json.dumps(example, ensure_ascii=False, separators=(",", ":")))
        addition = size + (1 if selected else 0)
        if used + addition > max_chars:
            continue
        selected.append(example)
        used += addition
        if len(selected) >= limit:
            break
    return selected
