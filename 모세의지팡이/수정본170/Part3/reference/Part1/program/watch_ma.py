"""Canonical WATCH MA syntax and comparisons (no market I/O or MA formula).

Each Part owns its own copy.  Calculations are supplied by that Part's existing
source functions; the expression layer only refers to canonical feature names.
"""
from __future__ import annotations
from dataclasses import dataclass
import math
import re

_MA = re.compile(r"(SMA|WMA|EMA|HMA)([1-9][0-9]*)", re.I)
_FUNCTIONS = {"기울기": "SLOPE", "정배열": "ABOVE", "역배열": "BELOW",
              "골든크로스": "GOLDEN", "데드크로스": "DEAD"}
_COMPARISONS = {">": lambda a, b: a > b, "<": lambda a, b: a < b,
                ">=": lambda a, b: a >= b, "<=": lambda a, b: a <= b,
                "==": lambda a, b: a == b, "!=": lambda a, b: a != b}
_NUMBER = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?")


class WatchMAError(ValueError):
    """A user-visible syntax error; never converted to a false condition."""


def _error(text, detail):
    raise WatchMAError(f"WATCH MA 조건 오류: {text!r} · {detail}")


def parse_ma_name(value):
    if not isinstance(value, str):
        _error(value, "MA 이름은 SMA/WMA/EMA/HMA + 양의 정수여야 합니다")
    match = _MA.fullmatch(value)
    if match is None:
        _error(value, "허용 문법: SMA20, WMA23, EMA37, HMA17 (공백/소수/0 불가)")
    try:
        period = int(match[2])
    except ValueError:
        _error(value, "period 정수 범위를 확인해 주세요")
    family=match[1].upper()
    if family=='EMA' and period==21:period=20  # Retired spelling maps to the canonical fixed EMA20.
    return family, period


def canonical_ma(value):
    family, period = parse_ma_name(value)
    return f"{family}{period}"


def ma_history(name):
    """Number of input rows through the first valid output, not an index.

    Existing rolling/ewm(min_periods=n) require n valid observations.  HMA's
    outer WMA requires floor(sqrt(n)) valid inner outputs after WMA(n).
    """
    family, period = parse_ma_name(name)
    return period + max(1, int(math.sqrt(period))) - 1 if family == "HMA" else period


def slope_lookback(name):
    return 2 if parse_ma_name(name)[0] == "HMA" else 1


def _split_boolean(text, keyword):
    depth = 0
    start = 0
    parts = []
    for match in re.finditer(r"\(|\)|\b(?:AND|OR)\b", text, re.I):
        token = match[0].upper()
        if token == "(":
            depth += 1
        elif token == ")":
            depth -= 1
            if depth < 0:
                _error(text, "괄호가 맞지 않습니다")
        elif depth == 0 and token == keyword:
            parts.append(text[start:match.start()].strip())
            start = match.end()
    if depth:
        _error(text, "괄호가 맞지 않습니다")
    parts.append(text[start:].strip())
    if any(not part for part in parts):
        _error(text, "AND/OR의 양쪽 조건이 필요합니다")
    return parts


def _function(text):
    match = re.fullmatch(r"([^()]+?)\s*\((.*)\)", text)
    if match is None or match[1].strip() not in _FUNCTIONS:
        _error(text, "지원하지 않는 WATCH MA 함수")
    kind = _FUNCTIONS[match[1].strip()]
    body = match[2]
    if "(" in body or ")" in body:
        _error(text, "함수 인수에는 canonical MA만 허용합니다 (중첩 불가)")
    args = tuple(x.strip() for x in body.split(",")) if body.strip() else ()
    required = 1 if kind == "SLOPE" else 2
    if (len(args) != required if kind in ("SLOPE", "GOLDEN", "DEAD") else len(args) < 2):
        _error(text, f"{match[1].strip()} 인수는 {'정확히 ' if kind in ('SLOPE','GOLDEN','DEAD') else '최소 '}{required}개 MA여야 합니다")
    return kind, tuple(canonical_ma(x) for x in args)


def _operand(text):
    text = text.strip()
    if "(" in text or ")" in text:
        kind, args = _function(text)
        if kind != "SLOPE":
            _error(text, "숫자 비교에는 MA 값 또는 기울기(MA)만 사용할 수 있습니다")
        return ("SLOPE", args[0])
    if _NUMBER.fullmatch(text):
        value = float(text)
        if not math.isfinite(value):
            _error(text, "비교값은 유한한 숫자여야 합니다")
        return ("NUMBER", value)
    return ("MA", canonical_ma(text))


def _operand_text(value):
    kind, name = value
    if kind == "SLOPE":
        return f"기울기({name})"
    if kind == "NUMBER":
        return repr(name)
    return name


@dataclass(frozen=True)
class MACondition:
    kind: str
    args: tuple = ()
    operator: str = ""
    left: tuple = ()
    right: tuple = ()

    def text(self):
        if self.kind == "COMPARE":
            return f"{_operand_text(self.left)} {self.operator} {_operand_text(self.right)}"
        label = next(name for name, kind in _FUNCTIONS.items() if kind == self.kind)
        return f"{label}({', '.join(self.args)})"

    def dependencies(self):
        if self.kind == "COMPARE":
            operands = (self.left, self.right)
            return tuple((name, slope_lookback(name) if kind == "SLOPE" else 0)
                         for kind, name in operands if kind != "NUMBER")
        return tuple((name, 1 if self.kind in ("GOLDEN", "DEAD") else 0) for name in self.args)

    def evaluate(self, features, current):
        def read(name, lag=0):
            values = features.get(name)
            if values is None:
                return None
            index = current if current >= 0 else len(values) + current
            index -= lag
            if not 0 <= index < len(values):
                return None
            try:
                value = values.iloc[index] if hasattr(values, "iloc") else values[index]
                value = float(value)
                return value if math.isfinite(value) else None
            except (TypeError, ValueError, OverflowError):
                return None

        def numeric(operand):
            kind, name = operand
            if kind == "NUMBER":
                return name
            value = read(name)
            if kind == "MA":
                return value
            previous = read(name, slope_lookback(name))
            return value - previous if value is not None and previous is not None else None

        if self.kind == "COMPARE":
            left, right = numeric(self.left), numeric(self.right)
            return None if left is None or right is None else bool(_COMPARISONS[self.operator](left, right))
        values = tuple(read(name) for name in self.args)
        if any(value is None for value in values):
            return None
        if self.kind in ("ABOVE", "BELOW"):
            compare = _COMPARISONS[">" if self.kind == "ABOVE" else "<"]
            return all(compare(a, b) for a, b in zip(values, values[1:]))
        before = tuple(read(name, 1) for name in self.args)
        if any(value is None for value in before):
            return None
        if self.kind == "GOLDEN":
            return before[0] <= before[1] and values[0] > values[1]
        return before[0] >= before[1] and values[0] < values[1]


def _condition(text):
    depth = 0
    comparisons = []
    for match in re.finditer(r"\(|\)|>=|<=|==|!=|>|<", text):
        if match[0] == "(":
            depth += 1
        elif match[0] == ")":
            depth -= 1
        elif depth == 0:
            comparisons.append(match)
    if comparisons:
        if len(comparisons) != 1:
            _error(text, "한 숫자 조건에는 비교 연산자 하나만 허용합니다")
        match = comparisons[0]
        left, right = _operand(text[:match.start()]), _operand(text[match.end():])
        if left[0] == right[0] == "NUMBER":
            _error(text, "MA feature가 없는 조건입니다")
        return MACondition("COMPARE", operator=match[0], left=left, right=right)
    if "(" in text:
        kind, args = _function(text)
        if kind == "SLOPE":
            _error(text, "기울기에는 숫자 비교가 필요합니다 (예: 기울기(HMA17) > 0)")
        return MACondition(kind, args)
    canonical_ma(text)  # Name errors retain the exact offending spelling.
    _error(text, "MA 값에는 숫자 비교가 필요합니다 (예: SMA20 > EMA50)")


@dataclass(frozen=True)
class MAExpression:
    groups: tuple  # OR of AND groups; no nested indicator language.

    @property
    def canonical(self):
        return " OR ".join(" AND ".join(c.text() for c in group) for group in self.groups)

    @property
    def dependencies(self):
        result = {}
        for group in self.groups:
            for condition in group:
                for name, lag in condition.dependencies():
                    result[name] = max(result.get(name, 0), lag)
        return dict(sorted(result.items()))

    @property
    def has_cross(self):
        return any(c.kind in ("GOLDEN", "DEAD") for group in self.groups for c in group)

    @property
    def default_evaluation_mode(self):
        # Existing generic crosses use closed bars; MA states use the live row.
        return "CLOSE" if self.has_cross else "LIVE"

    def history_rows(self, evaluation_mode):
        if evaluation_mode not in ("LIVE", "CLOSE"):
            _error(evaluation_mode, "평가 모드는 기존 LIVE/CLOSE만 허용합니다")
        return max(ma_history(name) + lag for name, lag in self.dependencies.items()) + int(evaluation_mode == "CLOSE")

    def evaluate(self, features, current):
        states = []
        for group in self.groups:
            values = [condition.evaluate(features, current) for condition in group]
            states.append(False if False in values else None if None in values else True)
        return True if True in states else None if None in states else False


def parse_ma_expression(text):
    if not isinstance(text, str) or not text.strip():
        _error(text, "빈 표현식")
    try:
        groups = tuple(tuple(_condition(part) for part in _split_boolean(group, "AND"))
                       for group in _split_boolean(text.strip(), "OR"))
        return MAExpression(groups)
    except WatchMAError as exc:
        if repr(text) in str(exc):
            raise
        _error(text, str(exc))


def parse_canonical_command(text, interpreter, *, expected_symbol=None):
    """Recognize only explicit canonical expressions, before alias rewriting.

    Existing symbol/timeframe dictionaries parse the *wrapper only*. Natural
    commands without canonical functions/comparisons return None unchanged.
    """
    raw = str(text or "").strip()
    body = re.sub(r"\s*알려\s*(?:줘|주세요)\s*[.!?]?\s*$", "", raw).strip()
    body = re.sub(r"(?i)^WATCH\s+", "", body)
    start = re.search(r"(?<![A-Za-z0-9_])(?:(?:기울기|정배열|역배열|골든크로스|데드크로스)\s*\(|(?:SMA|WMA|EMA|HMA|SMMA)[A-Za-z0-9_.+\-]*)", body, re.I)
    if start is None:
        return None
    expression = body[start.start():].strip()
    at_tf = ""
    if "@" in expression:
        expression, at_tf = expression.rsplit("@", 1)
        at_tf = at_tf.strip()
    if not ("(" in expression or re.search(r"[<>=!]", expression) or
            re.fullmatch(r"(?:SMA|WMA|EMA|HMA|SMMA)[A-Za-z0-9_.+\- ]*", expression, re.I)):
        return None  # e.g. the original 'EMA50/200 골크' / '200EMA 우하향'.
    if any(c in raw for c in "\n\r\0;`"):
        _error(raw, "한 줄 WATCH 표현식만 허용합니다")
    parsed = parse_ma_expression(expression)
    prefix = body[:start.start()].strip()
    prefix = interpreter.normalize_command_text(prefix)
    occurrences = interpreter.tf_occurrences(prefix)
    tfs = {tf for _, _, tf in occurrences}
    if at_tf:
        hits = interpreter.tf_occurrences(at_tf)
        if len(hits) != 1 or hits[0][0] != 0 or hits[0][1] != len(at_tf):
            _error(raw, f"지원하지 않는 timeframe: {at_tf}")
        tfs.add(hits[0][2])
    if len(tfs) > 1:
        _error(raw, "하나의 canonical WATCH에는 timeframe 하나만 지정해 주세요")
    symbol = interpreter.parse_symbol(prefix)
    if not symbol or expected_symbol is not None and symbol != expected_symbol:
        _error(raw, "WATCH 종목을 확인해 주세요")
    residue = list(prefix)
    for left, right, _ in occurrences:
        residue[left:right] = " " * (right-left)
    residue = "".join(residue)
    terms = {symbol}
    for key, aliases in interpreter.command_aliases.get("symbols", {}).items():
        if interpreter.parse_symbol(key) == symbol:
            terms.update((key, *aliases))
    for term in sorted(terms, key=len, reverse=True):
        residue = re.sub(r"(?<![\w])" + re.escape(term) + r"(?![\w])", " ", residue, flags=re.I)
    if residue.strip():
        _error(raw, f"해석되지 않은 문구: {residue.strip()}")
    tf = next(iter(tfs), interpreter.default_tf())
    return {"watch_type": "MA_EXPRESSION", "ma_expression": parsed.canonical,
            "timeframes": [tf], "symbol": symbol,
            "evaluation_mode": parsed.default_evaluation_mode, "direction": None}


def feature_source_rows(name):
    """Retain the existing 650-row STAFF window, enlarged for the supported lags.

    This is a source-window/seed contract, not a different min_periods formula.
    The extra rows are the fixed slope dependency and one forming row; using
    one per-feature window keeps EMA seeds independent of other WATCH requests.
    """
    return max(650, ma_history(name) + slope_lookback(name) + 1)


class MAFeatureCache:
    """Bounded prefix-scoped cache around existing, injected calculation functions.

    A provided source MA column is authoritative, including its NaN positions;
    it is never filled with a differently seeded calculation.
    """
    def __init__(self, calculators, max_entries=128):
        from collections import OrderedDict
        self.calculators = calculators
        self.max_entries = max_entries
        self.values = OrderedDict()
        self.hits = 0
        self.misses = 0

    @staticmethod
    def source(frame, name):
        family, period = parse_ma_name(name)
        native = f"{family.lower()}_{period}"
        if native in frame.columns:
            return native, "EXISTING_SOURCE_COLUMN"
        return ("close" if family == "EMA" else "open"), "EXISTING_GENERAL_FUNCTION"

    def calculate(self, frame, names):
        import hashlib
        import numpy as np
        import pandas as pd
        result = {}
        for name in dict.fromkeys(canonical_ma(name) for name in names):
            family, period = parse_ma_name(name)
            source_frame = frame.iloc[-feature_source_rows(name):]
            column, origin = self.source(source_frame, name)
            if column not in source_frame:
                _error(name, f"원본 입력 컬럼 누락: {column}")
            raw = pd.to_numeric(source_frame[column], errors="coerce")
            fingerprint = hashlib.sha256(raw.to_numpy(dtype="float64").tobytes()).digest()
            key = (name, column, origin, len(raw), fingerprint)
            cached = self.values.get(key)
            if cached is None:
                if origin == "EXISTING_SOURCE_COLUMN":
                    values = raw.to_numpy(copy=True)
                elif len(raw) < ma_history(name):
                    values = np.full(len(raw), np.nan, dtype=float)
                else:
                    values = self.calculators[family](raw, period).to_numpy(copy=True)
                values.setflags(write=False)
                self.values[key] = values
                self.misses += 1
                if len(self.values) > self.max_entries:
                    self.values.popitem(last=False)
            else:
                values = cached
                self.values.move_to_end(key)
                self.hits += 1
            # Only this feature's source window is meaningful. Missing history
            # remains NaN; no back-fill or later source values can leak backward.
            full = np.full(len(frame), np.nan, dtype=values.dtype)
            if len(values):
                full[-len(values):] = values
            result[name] = pd.Series(full, index=frame.index, name=name)
        return result
