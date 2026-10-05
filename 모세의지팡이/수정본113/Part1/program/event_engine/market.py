"""Read-only Snapshot views for event consumers (no frame conversion)."""
from domain_clock import datetime
import numpy as np
from staff_schema import PIPE_VALUE_COLUMNS
from .model import same_immutable_array

COLUMNS = {name: i for i, name in enumerate(PIPE_VALUE_COLUMNS)}


def timestamp(seconds):
    return datetime.datetime.fromtimestamp(float(seconds), datetime.timezone.utc)


def available(board, symbol, tf):
    return ((symbol, tf) in board.feeds
            and board.health.get(symbol, {}).get('status') not in ('STALE', 'UNAVAILABLE', 'RECONNECT')
            and (board.source_time is None or board.source_time-board.observed[symbol, tf] <= 30000))


def finite_values(board, symbol, tf, snapshot, columns=None):
    """Share only immutable-array checks inside the current board publication.

    ``None`` checks all OHLC rows; a column tuple checks the last row only.
    Feed health, observation age and minimum row count remain caller decisions.
    A lightweight board without a publication cache uses the full check.
    """
    cache = getattr(board, '_frame_cache', None)
    key = ('numpy_finite', symbol, tf, columns)
    if cache is not None:
        previous = cache.get(key)
        if previous is not None and previous[0] is snapshot:
            return previous[1]
    values = snapshot.values[:, :4] if columns is None else snapshot.values[-1, list(columns)]
    valid = np.isfinite(values).all()
    if cache is not None:
        cache[key] = (snapshot, valid)
    return valid


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
    def same_closed_inputs(self, other, columns, *, include_forming_time=False):
        """Compare the actual closed history used by a cached calculation.

        FULL may correct history without changing its epoch or bar timestamps.
        Forming prices never invalidate a closed-only calculation.
        """
        if self.close_key() != other.close_key(): return False
        time_cut = slice(None) if include_forming_time else slice(None, -1)
        def equal(left, right):
            return same_immutable_array(left, right) or np.array_equal(left, right, equal_nan=True)
        return equal(self.time[time_cut], other.time[time_cut]) and all(
            equal(self.column(name)[:-1], other.column(name)[:-1]) for name in columns)


def select(board, symbol, tf, required=()):
    if not available(board, symbol, tf): return None
    snapshot = board.snapshot(symbol, tf)
    if not len(snapshot.time): return None
    # The old legacy request rejected invalid OHLC and requested indicator groups.
    # Optional unrequested groups must not make an OHLC-only watch unavailable.
    from staff_compat import MT5_REQUIRED_BY_INDICATOR
    columns=[COLUMNS[c] for name in required for c in MT5_REQUIRED_BY_INDICATOR.get(name,())]
    if columns and not finite_values(board, symbol, tf, snapshot, tuple(columns)):return None
    cache = board._frame_cache
    wonbi='WONBI' in required
    key = ('numpy_market', symbol, tf, wonbi)
    if key not in cache:
        if not finite_values(board, symbol, tf, snapshot): return None
        cache[key] = MarketView(snapshot,features=board.fact('WONBI_BANDS',symbol,tf) if wonbi else None)
    return cache[key]


def health(views, indicators=()):
    return {'sources': {tf: view.snapshot.source_epoch for tf, view in views.items() if view is not None},
            'indicators': list(indicators)}
