"""Read-only Snapshot views for event consumers (no frame conversion)."""
from domain_clock import datetime
import numpy as np
from staff_schema import PIPE_VALUE_COLUMNS

COLUMNS = {name: i for i, name in enumerate(PIPE_VALUE_COLUMNS)}


def timestamp(seconds):
    return datetime.datetime.fromtimestamp(float(seconds), datetime.timezone.utc)


def available(board, symbol, tf):
    return ((symbol, tf) in board.feeds
            and board.health.get(symbol, {}).get('status') not in ('STALE', 'UNAVAILABLE', 'RECONNECT')
            and (board.source_time is None or board.source_time-board.observed[symbol, tf] <= 30000))


class Row:
    __slots__ = ('view', 'index')
    def __init__(self, view, index): self.view, self.index = view, index
    def get(self, name, default=None):
        try: return self.view.column(name)[self.index].item()
        except (KeyError, IndexError): return default
    def __getitem__(self, name): return self.view.column(name)[self.index].item()


class MarketView:
    """time/values/volume are the original immutable arrays, never copied."""
    def __init__(self, snapshot, end=None, features=None):
        self.snapshot = snapshot
        self.time = snapshot.time if end is None else snapshot.time[:end]
        self.values = snapshot.values if end is None else snapshot.values[:end]
        self.volume = snapshot.volume if end is None else snapshot.volume[:end]
        self.features = {} if features is None else features
    def __len__(self): return len(self.time)
    @property
    def empty(self): return not len(self)
    def row(self, index): return Row(self, index)
    def column(self, name):
        if name in self.features: return self.features[name]
        if name == 'time': return self.time
        if name == 'volume': return self.volume
        if name == 'wonbi_mid': name = 'open_band_4_mid'
        return self.values[:, COLUMNS[name]]
    def close_key(self):
        return (self.snapshot.source_epoch, len(self), int(self.time[0]) if len(self) else None,
                int(self.time[-2]) if len(self)>1 else None)


def select(board, symbol, tf, required=()):
    if not available(board, symbol, tf): return None
    snapshot = board.snapshot(symbol, tf)
    if not len(snapshot.time): return None
    # The old legacy request rejected invalid OHLC and requested indicator groups.
    # Optional unrequested groups must not make an OHLC-only watch unavailable.
    from staff_compat import MT5_REQUIRED_BY_INDICATOR
    columns=[COLUMNS[c] for name in required for c in MT5_REQUIRED_BY_INDICATOR.get(name,())]
    if columns and not np.isfinite(snapshot.values[-1,columns]).all():return None
    cache = board._frame_cache
    wonbi='WONBI' in required
    key = ('numpy_market', symbol, tf, wonbi)
    if key not in cache:
        if not np.isfinite(snapshot.values[:, :4]).all(): return None
        cache[key] = MarketView(snapshot,features=board.fact('WONBI_BANDS',symbol,tf) if wonbi else None)
    return cache[key]


def health(views, indicators=()):
    return {'sources': {tf: view.snapshot.source_epoch for tf, view in views.items() if view is not None},
            'indicators': list(indicators)}
