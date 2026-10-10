"""Deterministic synthetic STAFF market for LIVE <-> backtest parity tests.

The numbers are NOT MT5 indicator values. They only have the same 45-column
STAFF layout so that the same Part1 code can be driven through two input paths:

  LIVE path     : every second, every feed publishes its full payload (like the EA timer)
  BACKTEST path : the same seconds written as STAFF_PIPE_V1 capture (FULL/ROW records)
                  by ``CaptureWriter`` and read back by ``capture.iter_publications``

Completed bars are fixed once closed (open-based columns never change inside a
bar); the forming bar's close-based columns move every second.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd

from .capture import MAX_BARS
from staff_schema import PIPE_VALUE_COLUMNS
C={name:i for i,name in enumerate(PIPE_VALUE_COLUMNS)}

TIMEFRAMES = ('1m', '2m', '3m', '4m', '5m', '6m', '10m', '12m', '15m', '20m', '30m',
              '1h', '2h', '3h', '4h', '6h', '8h', '12h', '1d')
TF_SECONDS = {tf: (int(tf[:-1]) * (60 if tf[-1] == 'm' else 3600 if tf[-1] == 'h' else 86400)) for tf in TIMEFRAMES}
OPEN_COLS = [C[name] for name in ('hma_6','hma_17','hma_50','hma_168','open_band_4_mid','wonbi_upper','wonbi_lower')]


def _wma(x: np.ndarray, n: int) -> np.ndarray:
    w = np.arange(1, n + 1, dtype=float)
    out = np.full(len(x), np.nan)
    if len(x) >= n:
        view = np.lib.stride_tricks.sliding_window_view(x, n)
        out[n - 1:] = view @ w / w.sum()
    return out


def hma(x: np.ndarray, n: int) -> np.ndarray:
    """Port of the EA CalcHMA (WMA half / full, sqrt root)."""
    half, root = max(1, n // 2), max(1, int(math.sqrt(n)))
    raw = 2 * _wma(x, half) - _wma(x, n)
    out = np.full(len(x), np.nan)
    valid = ~np.isnan(raw)
    if valid.sum() >= root:
        first = int(np.argmax(valid))
        out[first:] = _wma(raw[first:], root)
    return out


def _rsi(close: pd.Series, n: int = 14) -> pd.Series:
    d = close.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))


def _bands(value: pd.Series, n: int = 60):
    lo = value.rolling(n, min_periods=10).quantile(0.12)
    hi = value.rolling(n, min_periods=10).quantile(0.88)
    basis = value.ewm(span=20, adjust=False).mean()
    sd = value.rolling(20, min_periods=5).std(ddof=0)
    return lo, hi, basis, basis + 0.4 * sd, basis - 0.4 * sd


def ea_open4_synthetic(opens):
    """Synthetic-only EA CalcOpenBands4 port; two passes in the original order."""
    mid=np.full(len(opens),np.nan);std=np.full(len(opens),np.nan)
    for i in range(3,len(opens)):
        total=0.0
        for k in range(i-3,i+1):total+=float(opens[k])
        mean=total/4.0;variance=0.0
        for k in range(i-3,i+1):
            delta=float(opens[k])-mean;variance+=delta*delta
        mid[i]=mean;std[i]=math.sqrt(variance/4.0)
    return mid,std


def indicator_frame(bars: pd.DataFrame) -> np.ndarray:
    """Synthetic-only values in the current registry; MT5 source sigma is fixed3."""
    o,h,l,c=(bars[k].to_numpy(float) for k in ('open','high','low','close'))
    close=pd.Series(c);out=np.full((len(bars),len(PIPE_VALUE_COLUMNS)),np.nan)
    def put(name,value):out[:,C[name]]=value
    for name,value in zip(('open','high','low','close'),(o,h,l,c)):put(name,value)
    for n in (20,50,200):put(f'ema_{n}',close.ewm(span=n,adjust=False).mean())
    for n in (6,17,50,168):put(f'hma_{n}',hma(o,n))
    mid,sd=ea_open4_synthetic(o)
    put('open_band_4_mid',mid);put('wonbi_upper',mid+3*sd);put('wonbi_lower',mid-3*sd)
    k=100*(close-pd.Series(l).rolling(14).min())/(pd.Series(h).rolling(14).max()-pd.Series(l).rolling(14).min())
    values={'price':pd.Series(hma(c,6)), 'RSI':_rsi(close),'STO':k.rolling(3).mean(),
            'DI':50+50*close.diff(5)/close.diff().abs().rolling(14).sum()}
    for prefix,value in values.items():
        lo,hi,basis,rup,rdn=_bands(value)
        names=('price_hma_6','price_band_lower','price_band_upper','price_regime_basis') if prefix=='price' else tuple(prefix+'_'+v for v in ('val','db','ub','basis'))
        for name,v in zip(names,(value,lo,hi,basis)):put(name,v)
        for suffix,v in [('regime_upper',rup),('regime_lower',rdn),('lower_out',value.where(value<lo)),
                         ('upper_out',value.where(value>hi)),('regime_slope',basis.diff())]:put(prefix+'_'+suffix,v)
    return out


@dataclass
class SyntheticMarket:
    symbol: str
    start: int            # first evaluated second (epoch, s)
    end: int              # exclusive
    history_days: int = 3
    seed: int = 7
    # Optional deterministic shape for the evaluated window: ((offset_s, price_delta), ...)
    # linearly interpolated and added to the random walk from ``start`` onward.
    pattern: tuple = ()

    def __post_init__(self):
        rng = np.random.default_rng(self.seed)
        hist_start = (self.start - self.history_days * 86400) // 86400 * 86400
        # The random path covers a fixed horizon (history + one day after start), so the
        # same second has the same price whatever window length is requested.
        horizon = max(self.end, self.start + 86400)
        seconds = np.arange(hist_start, horizon, dtype=np.int64)
        # regime-switching drift random walk
        steps = rng.normal(0, 0.035, len(seconds))
        regime = np.repeat(rng.choice([-1.0, 1.0], len(seconds) // 1800 + 1), 1800)[:len(seconds)]
        wave = np.sin(np.arange(len(seconds)) / 700.0) * 0.004
        price = 2400 + np.cumsum(steps + regime * 0.0025 + wave)
        keep = seconds < self.end
        seconds, price = seconds[keep], price[keep]
        if self.pattern:
            knots = np.array(self.pattern, dtype=float)
            offset = (seconds - self.start).astype(float)
            shape = np.interp(offset, knots[:, 0], knots[:, 1], left=0.0, right=float(knots[-1, 1]))
            shape[offset < 0] = 0.0
            price = price + shape
        self.seconds = seconds
        self.price = price
        self.frames = {}
        for tf in TIMEFRAMES:
            self.frames[tf] = self._build_tf(tf)

    def _build_tf(self, tf):
        width = TF_SECONDS[tf]
        bar_id = self.seconds // width * width
        df = pd.DataFrame({'t': bar_id, 'p': self.price})
        g = df.groupby('t')['p']
        bars = pd.DataFrame({'open': g.first(), 'high': g.max(), 'low': g.min(), 'close': g.last(),
                             'volume': g.count()}).reset_index()
        final = indicator_frame(bars)
        return {'times': bars['t'].to_numpy(np.int64), 'volume': bars['volume'].to_numpy(np.int64),
                'final': final, 'width': width}

    def payload(self, tf: str, second: int):
        """Full LIVE payload (<=650 rows) of ``tf`` observed at ``second``."""
        f = self.frames[tf]
        width = f['width']
        bar = second // width * width
        j = int(np.searchsorted(f['times'], bar))
        first = max(0, j - (MAX_BARS - 1))
        times = f['times'][first:j + 1].copy()
        volumes = f['volume'][first:j + 1].copy()
        values = f['final'][first:j + 1].copy()
        # forming bar at `second`
        idx0 = int(np.searchsorted(self.seconds, bar))
        idx1 = int(np.searchsorted(self.seconds, second, side='right'))
        part = self.price[idx0:idx1]
        row = values[-1]
        prev = f['final'][j - 1] if j > 0 else row
        frac = (second - bar + 1) / width
        blended = prev + (f['final'][j] - prev) * frac
        row[:] = blended
        row[OPEN_COLS] = f['final'][j][OPEN_COLS]
        row[0], row[1], row[2], row[3] = part[0], part.max(), part.min(), part[-1]
        for p in ('price','RSI','STO','DI'):
            value=row[C['price_hma_6' if p=='price' else p+'_val']]
            lo=row[C['price_band_lower' if p=='price' else p+'_db']]
            hi=row[C['price_band_upper' if p=='price' else p+'_ub']]
            row[C[p+'_lower_out']]=value if value<lo else np.nan
            row[C[p+'_upper_out']]=value if value>hi else np.nan
        volumes[-1] = len(part)
        values = np.where(np.isnan(values), 1.7976931348623157e308, values)  # EMPTY_VALUE on the wire
        return times, volumes, values


def capture_market(data, root, *, wire_version=2, timeframes=TIMEFRAMES):
    """Same synthetic inputs in MSP2 or MSP3; formulas above remain untouched."""
    from .capture import CaptureWriter
    if wire_version!=2:raise ValueError('current schema requires MSP3')
    writer=CaptureWriter(root,data.symbol,timeframes,wire_version=2)
    try:
        for second in range(data.start,data.end):
            for index,tf in enumerate(timeframes):
                t,v,x=data.payload(tf,second)
                writer.write(index,second,t,v,x)
    finally:
        writer.close()
    return root
