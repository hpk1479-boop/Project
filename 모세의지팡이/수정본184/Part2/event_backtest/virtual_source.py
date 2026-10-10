"""Native columns for post-run fills only. No strategy or STAFF replay here.

Published MSD2 uses a column projection without rebuilding Wire bytes. Other
inputs retain their checked reader. The immutable recording is never changed.
"""
from types import SimpleNamespace
import numpy as np
import staff_schema as wire
from .storage import bundles
from .settings import milliseconds, warehouse_path

NAMES = ('open', 'high', 'low', 'close', 'ema_20', 'ema_50', 'ema_200',
         'hma_6', 'hma_17', 'hma_50', 'hma_168')
INDICES = tuple(wire.PIPE_VALUE_COLUMNS.index(k) for k in NAMES)


class _EndWindow:
    """Bound the optional finalization tail to its first recorded server day (수정본178)."""
    def __init__(self, end, past_end):
        self.end=end;self.past_end=past_end;self.day=None;self.finished=False

    def allows(self, stamp):
        if self.finished:return False
        if stamp < self.end:return True
        if not self.past_end:
            self.finished=stamp>self.end
            return not self.finished
        day=stamp//86_400_000
        if self.day is None:self.day=day
        self.finished=day!=self.day
        return not self.finished


def shared_observations(captures, warehouse, selected, start, end, check=lambda: None,
                        *, needed_from=None, metrics=None, server_time=None, past_end=False):
    """(observation time, {(symbol, tf): view}) of the recordings from start to end.

    server_time (수정본172): the recordings' broker clock. start, end and needed_from are then real
    time, as the alerts are, and observations and their bar times come out in real time.
    past_end (수정본178): read at most the first recorded server day at/after end. The consumer stops
    earlier once M1 final data arrives; an absent feed must not cause an unbounded later-date scan."""
    if server_time is not None:
        yield from _real_observations(captures, warehouse, selected, start, end, check, needed_from, metrics,
                                      server_time, past_end)
        return
    from .virtual_msd import verified_index, read_selected
    needed_from=needed_from or (lambda:start)
    metrics={} if metrics is None else metrics
    limit=_EndWindow(end,past_end)
    views = {}
    def update(raw):
        packet = wire.decode_v2(raw)
        for f in packet.children:
            key=(f.symbol,f.timeframe)
            if key not in selected:
                continue
            if f.kind == wire.WIRE_HEARTBEAT:
                if key not in views:
                    raise ValueError('가상 진입: FULL 없는 HEARTBEAT')
                continue
            values = f.values[:, INDICES]
            invalid = ~np.isfinite(values) | (np.abs(values) > 1.e300)
            if invalid.any():
                values = values.copy(); values[invalid] = np.nan
            if f.kind == wire.WIRE_FULL:
                views[key] = SimpleNamespace(time=f.times, values=values, columns=NAMES)
            elif f.kind == wire.WIRE_ROW:
                old = views.get(key)
                if old is None or old.time[-1] != f.times[0]:
                    raise ValueError('가상 진입: FULL과 이어지지 않는 ROW')
                x = old.values.copy(); x[-1] = values[0]
                views[key] = SimpleNamespace(time=old.time, values=x, columns=NAMES)
            elif f.kind == wire.WIRE_APPEND:
                # The closed bar's final row, then the new bar: the window STAFF builds from it.
                old = views.get(key)
                if old is None or old.time[-1] != f.times[0] or f.times[1] <= f.times[0]:
                    raise ValueError('가상 진입: FULL과 이어지지 않는 APPEND')
                drop = int(len(old.time) >= wire.WIRE_MAX_BARS)
                views[key] = SimpleNamespace(time=np.concatenate((old.time[drop:-1], f.times)),
                                             values=np.concatenate((old.values[drop:-1], values)), columns=NAMES)
            else:
                raise ValueError('가상 진입: 지원하지 않는 프레임 종류')
    for c in sorted(captures, key=lambda c: c['start']):
        if needed_from() is None or limit.finished:return
        if limit.day is not None and milliseconds(c['start'])//86_400_000>limit.day:return
        if milliseconds(c['end']) <= start or (milliseconds(c['start']) >= end and not past_end):
            continue
        root=warehouse_path(warehouse,c['path']);index=verified_index(root)
        if index is not None:
            for stamp, views_now in read_selected(root,index,selected,start,end,check=check,
                                                  needed_from=needed_from,metrics=metrics,past_end=past_end,
                                                  read_limit=limit):
                yield stamp, views_now
            continue
        for stamp, raw in bundles(root, start_ms=start,
                                 bootstrap=lambda raw, metadata: update(raw)):
            check()
            if not limit.allows(stamp):
                return
            update(raw)
            if stamp >= start:
                yield stamp, views


def _real_observations(captures, warehouse, selected, start, end, check, needed_from, metrics, broker, past_end=False):
    """The recorded observations in real time; the recordings are read on their own server clock."""
    needed = needed_from or (lambda: start)

    def server_needed():
        value = needed()
        return None if value is None else broker.to_server_ms(value)
    converted = {}
    stream = shared_observations(captures, warehouse, selected, broker.to_server_ms(start), broker.to_server_ms(end),
                                 check, needed_from=server_needed, metrics=metrics, past_end=past_end)
    try:
        for stamp, views in stream:
            real = {}
            for key, view in views.items():
                cached = converted.get(key)
                if cached is None or cached[0] is not view:
                    # One conversion per recorded view; an unchanged view keeps its converted one.
                    cached = converted[key] = (view, SimpleNamespace(time=broker.to_utc_array(view.time),
                                                                     values=view.values, columns=view.columns))
                real[key] = cached[1]
            yield broker.to_utc_ms(stamp), real
    finally:
        stream.close()


def observations(captures, warehouse, symbol, timeframe, start, end, check=lambda: None):
    """Single-signal compatibility adapter; calculation uses one shared stream."""
    for stamp,feeds in shared_observations(captures,warehouse,{(symbol,timeframe),(symbol,'1m')},start,end,check):
        yield stamp,{tf:view for (sym,tf),view in feeds.items() if sym==symbol}
