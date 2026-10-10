"""State conditions judged the same way by the recipe engine and the virtual entry.

Each caller reads the moving-average Facts and the judged bar's close from its own
market view; only the comparison rules live here. A state keeps no memory: the
same values always give the same answer.
"""
from __future__ import annotations
import math

# Market states held while a strategy or an entry waits, judged by the rules below.
STATE_KINDS = frozenset(('TREND', 'MA_STATE', 'MA_SLOPE_STATE', 'MA_PRICE_STATE'))


def ma_names(step):
    """The moving-average series a state condition reads, in the order its rule uses them."""
    kind = step['kind']
    if kind == 'TREND':
        return ('SMA20', 'HMA50')
    slow = f"{step['ma_family']}{step['slow_period']}"
    if kind == 'MA_STATE':
        return (f"{step['ma_family']}{step['fast_period']}", slow)
    return (slow,)


def slope_lookback(step):
    return step.get('lookback', 2 if step['ma_family'] == 'HMA' else 1)


def state_ready(step, series, offset, names=None):
    """Whether the series hold enough values to judge the bar `offset` places before their end."""
    names = names or ma_names(step)
    if step['kind'] == 'TREND':
        return min(len(series[names[0]]), len(series[names[1]])) >= offset + 3
    need = slope_lookback(step) + 1 if step['kind'] == 'MA_SLOPE_STATE' else 1
    return len(series[names[-1]]) >= offset + need


def _finite(*values):
    return all(isinstance(v, (int, float)) and math.isfinite(v) for v in values)


def state_inputs(step, series, close, offset, names=None):
    """Every value the rule below reads, for a caller that rejects unusable data first."""
    kind = step['kind']; names = names or ma_names(step); o = offset
    if kind == 'TREND':
        a, b = series[names[0]], series[names[1]]
        return [a[-1-o], a[-2-o], b[-1-o], b[-3-o]]
    data = series[names[-1]]
    if kind == 'MA_STATE':
        return [series[names[0]][-1-o], data[-1-o]]
    if kind == 'MA_SLOPE_STATE':
        return [data[-1-o], data[-1-o-slope_lookback(step)]]
    return [close, data[-1-o]]


def state_matches(step, direction, series, close, offset, names=None):
    """One state condition at the bar `offset` places before the end of each series.

    `close` is that bar's close, read by MA_PRICE_STATE. Without its own side a step
    follows the direction: rising and above for LONG, falling and below for SHORT.
    `names` are ma_names(step), passed by a caller that already has them.
    """
    kind = step['kind']; names = names or ma_names(step); o = offset
    if kind == 'TREND':
        a, b = series[names[0]], series[names[1]]
        left, right = float(a[-1-o] - a[-2-o]), float(b[-1-o] - b[-3-o])
        return _finite(left, right) and (left > 0 and right > 0 if direction == 'LONG' else left < 0 and right < 0)
    data = series[names[-1]]
    right = float(data[-1-o])
    if kind == 'MA_STATE':
        left = float(series[names[0]][-1-o])
    elif kind == 'MA_SLOPE_STATE':
        left, right = right, float(data[-1-o-slope_lookback(step)])
    else:
        left = float(close)
    side = step.get('side') or (('UP' if direction == 'LONG' else 'DOWN') if kind == 'MA_SLOPE_STATE'
                                else ('ABOVE' if direction == 'LONG' else 'BELOW'))
    return _finite(left, right) and (left > right if side in ('UP', 'ABOVE') else left < right)


def candle_matches(side, polarity):
    """A closed candle's polarity (up 1, down -1, flat 0) against BULL or BEAR."""
    return polarity == (1 if side == 'BULL' else -1)


def shape_matches(shape, value):
    """A candle's shape Fact (hammer 1, inverted hammer -1, neither 0) against HAMMER or INVERTED_HAMMER."""
    return value == (1 if shape == 'HAMMER' else -1)


def touch_matches(low, high, average):
    """A candle touched the average: its range from low to high includes it."""
    return low <= average <= high
