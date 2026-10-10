"""Invocation cadence only; all raw ticks still reach market/risk state.

The serialized ONE_MINUTE_CLOSE name is retained for old configuration files.
Its schedule is now the strategy's lowest required timeframe. The first valid
price event in the next real base bar confirms a close; no synthetic EOF close
or backdated event is produced. The evaluation row for the base TF is the just
completed bar; every other TF is the actual post-tick point-in-time prefix.
"""
from dataclasses import dataclass, replace
from .contracts import GenericError, TIMEFRAMES
from .canonical import finite
from .market import quote_values

TICK = 'TICK'
ONE_MINUTE_CLOSE = 'ONE_MINUTE_CLOSE'  # compatibility configuration value
LIVE_PARITY = 'LIVE_PARITY'
EVALUATION_MODES = (TICK, ONE_MINUTE_CLOSE, LIVE_PARITY)
EVALUATION_LABELS = {'틱': TICK, '봉마감': ONE_MINUTE_CLOSE, 'LIVE_PARITY':LIVE_PARITY}
SCHEDULE_VERSION = 'LOWEST_REQUIRED_TIMEFRAME_CLOSE_V2'


def validate_evaluation_mode(value):
    if type(value) is not str or value not in EVALUATION_MODES:
        raise GenericError('E_EVALUATION_MODE', 'expected TICK, ONE_MINUTE_CLOSE or LIVE_PARITY')
    return value


def evaluation_label(value):
    validate_evaluation_mode(value)
    return '틱' if value == TICK else '봉마감' if value == ONE_MINUTE_CLOSE else 'LIVE_PARITY'


def evaluation_base_tf(required_timeframes):
    """Derive scheduling only; never add a TF to the WATCH semantic plan.

    A non-WATCH quote-only plugin may have no TF dependency; retain its legacy
    M1 sampling in that case. WATCH plans always declare their dependencies.
    """
    tfs = tuple(required_timeframes)
    if any(tf not in TIMEFRAMES for tf in tfs):
        raise GenericError('E_EVALUATION_MODE', 'unknown required timeframe')
    return min(tfs, key=TIMEFRAMES.__getitem__) if tfs else '1m'


@dataclass(frozen=True)
class TimeframeClose:
    bar_open_ns: int
    nominal_end_ns: int
    observed_at_ns: int
    source_ordinal: int


# Public compatibility aliases are kept for callers importing old names.
MinuteClose = TimeframeClose


class TimeframeCloseGate:
    def __init__(self, instrument, calendar, base_tf):
        if instrument.chart_mode not in ('BID', 'LAST'):
            raise GenericError('E_PLUGIN_SCHEMA', 'chart price policy')
        if base_tf not in TIMEFRAMES:
            raise GenericError('E_EVALUATION_MODE', 'unknown base timeframe')
        self.instrument = instrument
        self.calendar = calendar
        self.base_tf = base_tf
        self._interval = None
        self._last_order = None
        self.count = 0
        self.first = None
        self.last = None

    def on_tick(self, tick):
        order = (tick.time_msc * 1_000_000, tick.source_ordinal)
        if self._last_order is not None and (
                order[0] < self._last_order[0] or order[1] <= self._last_order[1]):
            raise GenericError('E_FUTURE_READ', 'timeframe gate order regression')
        self._last_order = order
        field, mask = ('bid', 2) if self.instrument.chart_mode == 'BID' else ('last', 8)
        if not tick.flags & mask:
            return None
        price = quote_values(tick)[field]
        if not finite(price) or price <= 0:
            return None
        # The calendar is immutable for a run. Reuse its verified current
        # interval for intra-bar price events instead of querying on every tick.
        previous = self._interval
        if previous is not None and previous[0] <= order[0] < previous[1]:
            return None
        interval = self.calendar.interval_for(self.instrument.broker_symbol, self.base_tf, order[0])
        if previous is not None and interval[0] < previous[0]:
            raise GenericError('E_CALENDAR_UNVERIFIED', 'timeframe interval regressed')
        self._interval = interval
        if previous is None or previous[0] == interval[0]:
            return None
        event = TimeframeClose(previous[0], previous[1], order[0], order[1])
        self.count += 1
        if self.first is None:
            self.first = event
        self.last = event
        return event


class OneMinuteCloseGate(TimeframeCloseGate):
    """Old direct API only. The coordinator uses TimeframeCloseGate instead."""
    def __init__(self, instrument, calendar):
        super().__init__(instrument, calendar, '1m')


def close_evaluation_view(view, base_tf, close):
    """Remove only the newly opened base candle from a real post-tick view.

    No OHLC aggregation is changed and no bar is copied from a future prefix.
    Quote/token retain the actual first-next-bar tick, so fills and barriers
    cannot be retroactively executed at the nominal close. An ENTRY role that
    does not request the signal's base TF simply keeps its own ordinary view.
    """
    feeds = []
    for tf, bars in view.feeds:
        if tf == base_tf:
            if bars and bars[-1].state == 'FORMING':
                bars = bars[:-1]
            if not bars or bars[-1].state != 'COMPLETED':
                raise GenericError('E_EVALUATION_MODE', 'base close not retained by market core')
            if close is not None and bars[-1].open_ns != close.bar_open_ns:
                raise GenericError('E_EVALUATION_MODE', 'base close identity mismatch')
            if bars[-1].nominal_end_ns > view.token.now_ns:
                raise GenericError('E_FUTURE_READ', 'base candle has not closed')
        feeds.append((tf, bars))
    return replace(view, feeds=tuple(feeds))


def close_gate_lookbacks(lookbacks, base_tf):
    """Retain the evaluation row even for a quote-only declared lookback of 0.

    No TF is added to requirements. The declared warmup/readiness remains 0.
    This internal extra slot is used only by CLOSE mode, never by TICK.
    """
    result=dict(lookbacks)
    if base_tf in result:result[base_tf]=max(1,result[base_tf])
    return result
