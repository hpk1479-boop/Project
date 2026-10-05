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


def shared_observations(captures, warehouse, selected, start, end, check=lambda: None,
                        *, needed_from=None, metrics=None):
    from .virtual_msd import verified_index, read_selected
    needed_from=needed_from or (lambda:start)
    metrics={} if metrics is None else metrics
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
            else:
                raise ValueError('가상 진입: 지원하지 않는 프레임 종류')
    for c in sorted(captures, key=lambda c: c['start']):
        if needed_from() is None:return
        if milliseconds(c['end']) <= start or milliseconds(c['start']) >= end:
            continue
        root=warehouse_path(warehouse,c['path']);index=verified_index(root)
        if index is not None:
            yield from read_selected(root,index,selected,start,end,check=check,
                                     needed_from=needed_from,metrics=metrics)
            continue
        for stamp, raw in bundles(root, start_ms=start,
                                 bootstrap=lambda raw, metadata: update(raw)):
            check()
            if stamp > end:
                return
            update(raw)
            if stamp >= start:
                yield stamp, views


def observations(captures, warehouse, symbol, timeframe, start, end, check=lambda: None):
    """Single-signal compatibility adapter; calculation uses one shared stream."""
    for stamp,feeds in shared_observations(captures,warehouse,{(symbol,timeframe),(symbol,'1m')},start,end,check):
        yield stamp,{tf:view for (sym,tf),view in feeds.items() if sym==symbol}
