"""Broker server clock -> real time: the one conversion of MT5 times (수정본172).

MT5 bar times, and the recordings made from them, carry the broker's server clock. It differs between
brokers and moves with that broker's daylight saving. Moses judges and shows Korean time (UTC+9, fixed)
from real time, so a server time is converted where it enters: the STAFF adapter (LIVE and replay), the
replay's observation times and the virtual-entry reader. LIVE and backtest use this same function.

A trading day stays the broker's server day: daily and weekly boundaries (previous-week levels, the
anchored VWAP periods) are taken on the server clock, as MT5's own daily bars are.

Setting (config.txt): SERVER_UTC_OFFSET is the server's hours from UTC outside daylight saving and
SERVER_DST its daylight saving (US, EU or NONE). The default, UTC+2 with US daylight saving, is
New York + 7 h: the daily break of US indices and gold at server 00:00 all year.
"""
from __future__ import annotations

import bisect
import datetime as _dt

import numpy as np

HOUR = 3600
DST_RULES = ('US', 'EU', 'NONE')
DEFAULT_OFFSET = 2
DEFAULT_DST = 'US'
DST_LABELS = {'US': '미국 서머타임', 'EU': '유럽 서머타임', 'NONE': '서머타임 없음'}
_YEARS = range(1970, 2101)
# A LIVE bar open and the EA's real send time agree on a whole-hour offset; a wrong setting is reported
# only after this many consecutive LIVE publications disagree (a stale bar is never one of them).
CLOCK_MISSES = 30


def _epoch(day):
    return (day - _dt.date(1970, 1, 1)).days * 86400


def _sunday(year, month, nth):
    """The nth Sunday of the month (nth=-1: the last one)."""
    if nth > 0:
        first = _dt.date(year, month, 1)
        return first + _dt.timedelta(days=(6 - first.weekday()) % 7 + 7 * (nth - 1))
    last = _dt.date(year + (month == 12), month % 12 + 1, 1) - _dt.timedelta(days=1)
    return last - _dt.timedelta(days=(last.weekday() + 1) % 7)


def _us_period(year):
    """US daylight saving in UTC seconds: 02:00 local standard time to 02:00 local daylight time."""
    if year >= 2007:
        start, end = _sunday(year, 3, 2), _sunday(year, 11, 1)
    else:
        start, end = _sunday(year, 4, 1), _sunday(year, 10, -1)
    return _epoch(start) + 7 * HOUR, _epoch(end) + 6 * HOUR


def _eu_period(year):
    """EU summer time in UTC seconds: 01:00 UTC on the last Sundays of March and October."""
    return _epoch(_sunday(year, 3, -1)) + HOUR, _epoch(_sunday(year, 10, -1)) + HOUR


class ServerTime:
    """A broker's server clock: UTC + utc_offset hours, one more hour in its daylight saving."""

    __slots__ = ('utc_offset', 'dst', '_base', '_utc', '_server', '_utc_list', '_server_list')

    def __init__(self, utc_offset=DEFAULT_OFFSET, dst=DEFAULT_DST):
        offset = int(utc_offset)
        if offset != utc_offset or not -12 <= offset <= 14:
            raise ValueError('브로커 서버 UTC 시차는 -12~14 사이의 정수여야 합니다.')
        dst = str(dst).strip().upper()
        if dst not in DST_RULES:
            raise ValueError('브로커 서버 서머타임은 US, EU, NONE 중 하나여야 합니다.')
        self.utc_offset, self.dst, self._base = offset, dst, offset * HOUR
        rule = {'US': _us_period, 'EU': _eu_period}.get(dst)
        # Daylight saving is [start, end) in UTC; on the server clock it is [start, end) + base + 1 h.
        self._utc_list = [] if rule is None else [edge for year in _YEARS for edge in rule(year)]
        self._server_list = [edge + self._base + HOUR for edge in self._utc_list]
        self._utc = np.array(self._utc_list, dtype=np.int64)
        self._server = np.array(self._server_list, dtype=np.int64)

    @classmethod
    def from_config(cls, config):
        """The setting of config.txt; a config written before 수정본172 has the default."""
        get = getattr(config, 'get', None)
        offset = str((get('SERVER_UTC_OFFSET', '') if get else '') or DEFAULT_OFFSET).strip()
        dst = str((get('SERVER_DST', '') if get else '') or DEFAULT_DST).strip()
        try:
            number = float(offset)
        except ValueError:
            raise ValueError('브로커 서버 UTC 시차는 -12~14 사이의 정수여야 합니다.') from None
        return cls(number if number != int(number) else int(number), dst)

    @classmethod
    def from_record(cls, record):
        """A recording's own setting; recordings made before 수정본172 came from the default broker."""
        if not record:
            return DEFAULT
        return cls(record.get('utc_offset', DEFAULT_OFFSET), record.get('dst', DEFAULT_DST))

    def record(self):
        return {'utc_offset': self.utc_offset, 'dst': self.dst}

    def label(self):
        return f'UTC{self.utc_offset:+d} · {DST_LABELS[self.dst]}'

    @property
    def identity(self):
        return self._base == 0 and not self._utc_list

    def __eq__(self, other):
        return isinstance(other, ServerTime) and (self.utc_offset, self.dst) == (other.utc_offset, other.dst)

    def __hash__(self):
        return hash((self.utc_offset, self.dst))

    def __repr__(self):
        return f'ServerTime({self.utc_offset}, {self.dst!r})'

    # Seconds -----------------------------------------------------------------------------------------
    def offset_at_utc(self, seconds):
        """Server minus UTC at a real (UTC) time, in seconds."""
        return self._base + (bisect.bisect_right(self._utc_list, seconds) & 1) * HOUR

    def offset_at_server(self, seconds):
        """Server minus UTC at a server-clock time. A server time skipped by the spring change is read as
        standard time; one repeated by the autumn change as its first occurrence (no market is open then)."""
        return self._base + (bisect.bisect_right(self._server_list, seconds) & 1) * HOUR

    def to_utc(self, seconds):
        seconds = int(seconds)
        return seconds - self.offset_at_server(seconds)

    def to_server(self, seconds):
        seconds = int(seconds)
        return seconds + self.offset_at_utc(seconds)

    def to_utc_ms(self, milliseconds):
        milliseconds = int(milliseconds)
        return milliseconds - self.offset_at_server(milliseconds // 1000) * 1000

    def to_server_ms(self, milliseconds):
        milliseconds = int(milliseconds)
        return milliseconds + self.offset_at_utc(milliseconds // 1000) * 1000

    # Arrays of seconds -------------------------------------------------------------------------------
    def _shift(self, values, edges, edge_list, sign):
        values = np.asarray(values)
        if self.identity or not len(values):
            return values
        first = bisect.bisect_right(edge_list, int(values[0]))
        if first == bisect.bisect_right(edge_list, int(values[-1])):
            # One window rarely crosses a daylight-saving change: a single offset.
            return values + sign * (self._base + (first & 1) * HOUR)
        index = np.searchsorted(edges, values, side='right')
        return values + sign * (self._base + (index & 1) * HOUR).astype(values.dtype)

    def to_utc_array(self, values):
        """Server-clock times (increasing seconds) as real time."""
        return self._shift(values, self._server, self._server_list, -1)

    def to_server_array(self, values):
        """Real times (increasing seconds) on the server clock: the broker's trading days."""
        return self._shift(values, self._utc, self._utc_list, 1)


DEFAULT = ServerTime()
IDENTITY = ServerTime(0, 'NONE')

# The clock of the bar times this process judges, for trading-day boundaries (previous-week levels,
# anchored VWAP periods). The LIVE host and every backtest worker set it once; unset, times are taken
# as they are (tests that build their own bars).
_active = IDENTITY


def activate(server_time):
    global _active
    _active = server_time if server_time is not None else IDENTITY
    return _active


def active():
    return _active


def observed_offset(bar_open_seconds, sent_ms):
    """The whole-hour server offset a LIVE publication shows, or None.

    The EA stamps a LIVE publication with real time (TimeGMT); its forming 1m bar opened at most about a
    minute before, on the server clock. A bar that is not the current one (a closed market, a gap) gives
    no whole-hour difference and is not read.
    """
    difference = int(bar_open_seconds) * 1000 - int(sent_ms)
    hours = round(difference / 3_600_000)
    residual = difference - hours * 3_600_000
    if -12 <= hours <= 14 and -120_000 <= residual <= 30_000:
        return hours
    return None
