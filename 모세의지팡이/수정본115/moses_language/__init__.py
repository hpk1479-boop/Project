"""Shared MOSES language data, without interpretation or execution.

The command interpreter and AI reference retrieval read the same portable
dictionary. Runtime callers keep their existing grammar and validators.
"""
from __future__ import annotations

from functools import lru_cache
import json
from pathlib import Path
from types import MappingProxyType


_COMMAND_ALIAS_SECTIONS = (
    "symbols", "phrase_aliases", "oz_direction", "ma_family", "cross",
    "wonbi_side", "percentile_side", "condition_macros",
)
_validated_reloads = {}


def language_path() -> Path:
    """Locate the dictionary relative to this package after project relocation."""
    return Path(__file__).with_name("language.json")


def _freeze(value):
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


def _copy(value):
    if isinstance(value, (dict, MappingProxyType)):
        return {key: _copy(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_copy(item) for item in value]
    return value


def _aliases(value, label):
    if not isinstance(value, dict):
        raise ValueError(f"MOSES 언어사전 {label} 구조가 올바르지 않습니다.")
    for name, aliases in value.items():
        if not isinstance(name, str) or not name or not isinstance(aliases, list):
            raise ValueError(f"MOSES 언어사전 {label} 용어가 올바르지 않습니다.")
        for alias in aliases:
            words = alias if isinstance(alias, list) else [alias]
            if not words or not all(isinstance(word, str) and word for word in words):
                raise ValueError(f"MOSES 언어사전 {label} 별칭이 올바르지 않습니다.")


def load_dictionary(path: str | Path | None = None):
    """Read once and return an immutable dictionary until cache invalidation.

    Callers responsible for command hot reload clear the shared cache after
    detecting a changed file. No interpretation or API request occurs here.
    """
    location = Path(path) if path is not None else language_path()
    return _load_dictionary(location.resolve())


@lru_cache(maxsize=4)
def _load_dictionary(location: Path):
    if location in _validated_reloads:
        return _validated_reloads[location]
    return _read_dictionary(location)


def _read_dictionary(location: Path):
    with location.open(encoding="utf-8") as stream:
        raw = json.load(stream)
    required = {"version", "reference_version", "command_language", "concepts",
                "core_rules", "condition_terms", "examples"}
    if not isinstance(raw, dict) or not required <= set(raw):
        raise ValueError("MOSES 언어사전 구조가 올바르지 않습니다.")
    for name in ("version", "reference_version"):
        if not isinstance(raw[name], str) or not raw[name]:
            raise ValueError("MOSES 언어사전 버전이 없습니다.")
    command = raw["command_language"]
    if not isinstance(command, dict) or not isinstance(command.get("defaults"), dict):
        raise ValueError("MOSES 언어사전 명령 기본값이 올바르지 않습니다.")
    for name in _COMMAND_ALIAS_SECTIONS:
        value = command.get(name, {})
        if name == "condition_macros":
            if not isinstance(value, dict) or not all(isinstance(item, dict) for item in value.values()):
                raise ValueError("MOSES 언어사전 조건 매크로가 올바르지 않습니다.")
        else:
            _aliases(value, name)
    _aliases(raw["concepts"], "concepts")
    _aliases(raw["condition_terms"], "condition_terms")
    syntax = raw.get("syntax_literals", {})
    if not isinstance(syntax, dict) or not all(
        isinstance(name, str) and name and isinstance(value, str)
        for name, value in syntax.items()
    ):
        raise ValueError("MOSES 언어사전 해석 문법이 올바르지 않습니다.")
    grammar = raw.get("grammar_terms", {})
    if not isinstance(grammar, dict):
        raise ValueError("MOSES 언어사전 문법 용어가 올바르지 않습니다.")
    for name, value in grammar.items():
        if isinstance(value, dict):
            _aliases(value, name)
        else:
            _aliases({name: value}, "grammar_terms")
    if not isinstance(raw["core_rules"], list) or not all(
        isinstance(rule, str) and rule for rule in raw["core_rules"]
    ):
        raise ValueError("MOSES 언어사전 공통 규칙이 올바르지 않습니다.")
    if not isinstance(raw["examples"], list):
        raise ValueError("MOSES 언어사전 예제 목록이 올바르지 않습니다.")
    ids = set()
    for example in raw["examples"]:
        if not isinstance(example, dict) or set(example) != {"id", "input", "meaning"}:
            raise ValueError("MOSES 언어사전 예제 구조가 올바르지 않습니다.")
        if not all(isinstance(value, str) and value for value in example.values()):
            raise ValueError("MOSES 언어사전 예제 내용이 없습니다.")
        if example["id"] in ids:
            raise ValueError("MOSES 언어사전 예제 번호가 중복됩니다.")
        ids.add(example["id"])
    return _freeze(raw)


def reload_dictionary(path: str | Path | None = None):
    """Publish a validated replacement atomically; failed reload keeps all views.

    The validated value primes the cache without rereading the file between
    validation and publication. An exception is explicit and leaves the old
    immutable dictionary and public reference view untouched.
    """
    location = (Path(path) if path is not None else language_path()).resolve()
    replacement = _read_dictionary(location)
    _validated_reloads[location] = replacement
    _reference_view.cache_clear()
    _load_dictionary.cache_clear()
    return _load_dictionary(location)


def command_language(path: str | Path | None = None) -> dict:
    """Return a private mutable copy for the existing command interpreter."""
    return _copy(load_dictionary(path)["command_language"])


def reference_pack(path: str | Path | None = None):
    """Keep the established public reference view and its immutable identity."""
    location = Path(path) if path is not None else language_path()
    return _reference_view(location.resolve())


@lru_cache(maxsize=4)
def _reference_view(location: Path):
    dictionary = load_dictionary(location)
    return MappingProxyType({
        "version": dictionary["reference_version"],
        "core_rules": dictionary["core_rules"],
        "condition_terms": dictionary["condition_terms"],
        "examples": dictionary["examples"],
    })


def concepts(path: str | Path | None = None):
    return load_dictionary(path)["concepts"]


def syntax_literal(name: str, path: str | Path | None = None) -> str:
    """Read parser syntax by name; never expose regex implementation to AI."""
    return load_dictionary(path).get("syntax_literals", {})[name]


def public_vocabulary(path: str | Path | None = None) -> dict:
    """Expose only static public language terms, not settings or examples.

    This allowlist does not inspect runtime files, user settings, accounts,
    model settings or credentials. External-provider sanitization still runs
    independently before sending this public view.
    """
    dictionary = load_dictionary(path)
    command = dictionary["command_language"]
    return {
        "aliases": {name: _copy(command.get(name, {})) for name in _COMMAND_ALIAS_SECTIONS},
        "concepts": _copy(dictionary["concepts"]),
        "condition_terms": _copy(dictionary["condition_terms"]),
        "grammar_terms": _copy(dictionary.get("grammar_terms", {})),
    }


def cache_clear():
    _validated_reloads.clear()
    _reference_view.cache_clear()
    _load_dictionary.cache_clear()


def cache_info():
    return _load_dictionary.cache_info()


load_dictionary.cache_clear = cache_clear
load_dictionary.cache_info = cache_info
