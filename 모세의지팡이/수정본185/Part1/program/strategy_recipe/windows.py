"""Windows after an event (수정본173): one rule for a chain's `within` and a condition's `recent`.

A window is {"seconds": S} or {"bars": N, "tf": TF}.

Seconds: from the event's time to that time + S, both ends included (the meaning of within_sec).
Market closures pass as time.

Bars: the bar an event was judged on is bar 0 and the next N actual bars of TF are the window.
A closed-bar judgment belongs to the bar it judged; anything judged on the bar in progress (an OZ
completion, a forming-bar cross or touch) belongs to that bar. The window ends when bar N+1 opens:
a closed judgment of bar N, made at that very moment, is still inside; anything on bar N+1 is not.
Only bars present in the data count, so closures are skipped and nothing is converted to minutes.

Pure functions: bar times and event times come from the caller, in real seconds (server_time).
"""
from __future__ import annotations

import numpy as np

# A snapshot holds at most 650 bars per timeframe (staff_schema.WIRE_MAX_BARS). A bar window stays
# well inside it, so a window's start never leaves the data before the window has ended.
MAX_BARS = 500


_FRAME_SECONDS = {}


def frame_seconds(tf):
    """A timeframe's length in seconds, as the command language reads it; one table, built on first use."""
    if not _FRAME_SECONDS:
        from command_interpreter import MT5_TIMEFRAMES, tf_seconds
        _FRAME_SECONDS.update((name, tf_seconds(name)) for name in MT5_TIMEFRAMES)
    return _FRAME_SECONDS[tf]


def check(window, tf_labels, label):
    """Validate a window object; the bar frame must be a real timeframe."""
    if not isinstance(window, dict) or set(window) - {'seconds', 'bars', 'tf'}:
        raise ValueError(f'{label}에 허용되지 않거나 사용하지 않는 필드가 있습니다.')
    if ('seconds' in window) == ('bars' in window):
        raise ValueError(f'{label}은 seconds 또는 bars 중 하나로 지정하세요.')
    if 'seconds' in window:
        if type(window['seconds']) is not int or window['seconds'] <= 0:
            raise ValueError(f'{label} seconds는 양의 정수입니다.')
        if 'tf' in window: raise ValueError(f'{label}: 초 기간에는 tf가 필요 없습니다.')
        return
    bars = window['bars']
    if type(bars) is not int or not 1 <= bars <= MAX_BARS:
        raise ValueError(f'{label} bars는 1~{MAX_BARS}의 정수입니다.')
    if window.get('tf') not in tuple(tf_labels):
        raise ValueError(f'{label} 시간봉을 확인하세요.')


def bar_end(open_time, tf):
    """End of a bar of `tf` that opened at `open_time` (real seconds): its open plus its length.

    Exact for every bar the market has: a broker's daylight-saving switch falls on a weekend,
    when no bar is open, so a day bar is 24 hours of the server's day on either clock.
    """
    return float(open_time) + frame_seconds(tf)


def close_time(times, at, end, bars):
    """When a bar window ends: the open time of bar N+1 after the event's own bar.

    times: the window frame's bar open times, ascending, the bar in progress last.
    at: the event's time; end: for a closed-bar judgment, the end of the bar it judged, else None.
    None while bar N+1 has not opened. An event older than every bar in the data is far behind
    a bar window (MAX_BARS), so its window is reported as already over.
    """
    if times is None or not len(times) or at is None:
        return None
    start = float(at if end is None else end)
    if start < float(times[0]):
        return float(times[0])
    # A closed judgment's bar ends at `end`: bar 1 is the first bar opening at or after it.
    # An event on a bar in progress: bar 1 is the first bar opening after the event.
    first = int(np.searchsorted(times, start, side='left' if end is not None else 'right'))
    index = first + int(bars)
    return float(times[index]) if index < len(times) else None


def within_chain(window, start, close, at, end):
    """Whether an observation at `at` (closed judgments: of a bar ending at `end`) is inside a chain
    window that began at `start` and, for bars, ends at `close` (None while open)."""
    if 'seconds' in window:
        return at - start <= window['seconds']
    if close is None:
        return True
    return end <= close if end is not None else at < close


def chain_over(window, start, close, now):
    """Whether a chain window that began at `start` is over at `now`."""
    if 'seconds' in window:
        return start is not None and now - start > window['seconds']
    return close is not None and now >= close


def recent_holds(window, mark, close, reference, closed_reference):
    """Whether an event remembered as `mark` (its time, and for a closed judgment the end of its bar)
    is still recent when judged at `reference`.

    reference: now, or for a bar window judged on closed bars, the end of the latest bar judged.
    The event's own moment is inside the window.
    """
    if mark is None:
        return False
    at, end = mark
    if 'seconds' in window:
        return at <= reference <= at + window['seconds']
    if reference < (at if end is None else end):
        return False
    if close is None:
        return True
    return reference <= close if closed_reference else reference < close
