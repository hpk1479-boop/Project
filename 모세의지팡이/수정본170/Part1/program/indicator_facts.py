# -*- coding: utf-8 -*-
"""Independent indicator Fact calculators (pure: no network, threads or files).

Role split (was one file, strategy_TREND.py):
  indicator_facts.py    each indicator as an independent Fact + reuse cache  <- this file
  indicator_score.py    full-indicator trend score (on-demand only)
  strategy_INDICATOR.py runtime: STAFF/manager clients, Watch registry, engine

Fact model
  * A Fact is one named calculation with declared dependencies (other Facts or
    raw STAFF columns). It is computed only when someone asks for it.
  * FactFrame memoizes every Fact for one input frame, so LONG/SHORT scoring,
    several metric fields and the trend state share one calculation.
  * FactStore keeps the last FactFrame per (symbol, TF). For the next frame it
    compares the raw columns; a Fact whose raw inputs did not change is carried
    over unchanged (same inputs -> same values). A new bar changes the time axis
    and invalidates everything, so OPEN-based Facts (SMA20/EMA10/HMA50/trend ...)
    are recalculated once per bar and close/high/low/volume Facts only when those
    inputs change.

The indicator formulas are the ones of the former strategy_TREND.py, unchanged.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Callable, Iterable, Optional

import numpy as np
import pandas as pd

MT5_TIMEFRAMES = (
    "1m", "2m", "3m", "4m", "5m", "6m", "10m", "12m", "15m", "20m", "30m",
    "1h", "2h", "3h", "4h", "6h", "8h", "12h", "1d",
)


def normalize_tf(value: object) -> str:
    raw = str(value or "").strip().lower().replace(" ", "")
    m = re.fullmatch(r"(\d+)(분|시간|일)", raw)
    if m:
        unit = {"분": "m", "시간": "h", "일": "d"}[m.group(2)]
        raw = f"{int(m.group(1))}{unit}"
    if raw == "24h":
        raw = "1d"
    return raw if raw in MT5_TIMEFRAMES else ""


def tf_seconds(tf: str) -> int:
    m = re.fullmatch(r"(\d+)([mhd])", normalize_tf(tf))
    if not m:
        return 10**12
    n = int(m.group(1))
    unit = m.group(2)
    return n * (60 if unit == "m" else 3600 if unit == "h" else 86400)


def sort_timeframes(values) -> tuple[str, ...]:
    normalized = []
    seen: set[str] = set()
    for value in values:
        tf = normalize_tf(value)
        if tf and tf not in seen:
            seen.add(tf)
            normalized.append(tf)
    normalized.sort(key=lambda x: (tf_seconds(x), x))
    return tuple(normalized)


def finite(v) -> bool:
    try:
        x = float(v)
        return math.isfinite(x)
    except (TypeError, ValueError):
        return False


# =============================================================================
# Indicator functions (public API, formulas unchanged from strategy_TREND.py)
# =============================================================================
def ema(s, n): return pd.to_numeric(s, errors="coerce").ewm(span=n, adjust=False, min_periods=n).mean()
def sma(s, n): return pd.to_numeric(s, errors="coerce").rolling(n).mean()
def wma(s, n):
    s = pd.to_numeric(s, errors="coerce")
    w = np.arange(1, n+1, dtype=float)
    return s.rolling(n).apply(lambda x: float(np.dot(x, w)/w.sum()), raw=True)
def hma(s, n):
    n2, ns = max(1,n//2), max(1,int(math.sqrt(n)))
    return wma(2*wma(s,n2)-wma(s,n), ns)

def true_range(df):
    h,l,c = [pd.to_numeric(df[x], errors="coerce") for x in ("high","low","close")]
    return _true_range(h, l, c)

def _true_range(h, l, c):
    pc = c.shift(1)
    return pd.concat([h-l,(h-pc).abs(),(l-pc).abs()],axis=1).max(axis=1)

def rma(s,n): return pd.to_numeric(s, errors="coerce").ewm(alpha=1/n, adjust=False, min_periods=n).mean()

def dmi(df,n=14):
    h,l = pd.to_numeric(df["high"],errors="coerce"), pd.to_numeric(df["low"],errors="coerce")
    return _dmi(h, l, true_range(df), n)

def _dmi(h, l, tr, n):
    up, down = h.diff(), -l.diff()
    pdm = up.where((up>down)&(up>0),0.0)
    mdm = down.where((down>up)&(down>0),0.0)
    atr = rma(tr,n)
    plus, minus = 100*rma(pdm,n)/atr, 100*rma(mdm,n)/atr
    dx = 100*(plus-minus).abs()/(plus+minus).replace(0,np.nan)
    return plus, minus, rma(dx,n)

def rsi(s,n=14):
    s = pd.to_numeric(s,errors="coerce")
    d=s.diff(); up=d.clip(lower=0); dn=(-d).clip(lower=0)
    rs=rma(up,n)/rma(dn,n)
    return 100-(100/(1+rs))

def cci(df,n=20):
    return _cci(pd.to_numeric(df.high,errors="coerce"), pd.to_numeric(df.low,errors="coerce"),
                pd.to_numeric(df.close,errors="coerce"), n)

def _cci(h, l, c, n):
    tp=(h+l+c)/3
    ma=tp.rolling(n).mean()
    md=tp.rolling(n).apply(lambda x: np.mean(np.abs(x-np.mean(x))),raw=True)
    return (tp-ma)/(0.015*md.replace(0,np.nan))

def linreg(s,n=20):
    s=pd.to_numeric(s,errors="coerce"); x=np.arange(n,dtype=float)
    return s.rolling(n).apply(lambda y: np.polyfit(x,y,1)[0]*(n-1)+np.polyfit(x,y,1)[1],raw=True)

def psar(df, af0=.02, step=.02, afmax=.2):
    return _psar(pd.to_numeric(df.high,errors="coerce"), pd.to_numeric(df.low,errors="coerce"),
                 af0, step, afmax)

def _psar(high, low, af0=.02, step=.02, afmax=.2):
    h=high.to_numpy(float)
    l=low.to_numpy(float)
    n=len(h); out=np.full(n,np.nan)
    if n<2: return pd.Series(out,index=high.index)
    bull=True; af=af0; ep=h[0]; sar=l[0]; out[0]=sar
    for i in range(1,n):
        sar=sar+af*(ep-sar)
        if bull:
            if i>=2: sar=min(sar,l[i-1],l[i-2])
            else: sar=min(sar,l[i-1])
            if l[i]<sar:
                bull=False; sar=ep; ep=l[i]; af=af0
            elif h[i]>ep:
                ep=h[i]; af=min(af+step,afmax)
        else:
            if i>=2: sar=max(sar,h[i-1],h[i-2])
            else: sar=max(sar,h[i-1])
            if h[i]>sar:
                bull=True; sar=ep; ep=h[i]; af=af0
            elif l[i]<ep:
                ep=l[i]; af=min(af+step,afmax)
        out[i]=sar
    return pd.Series(out,index=high.index)

def supertrend_dir(df, period=10, mult=3.0):
    h,l,c=[pd.to_numeric(df[x],errors="coerce") for x in ("high","low","close")]
    return _supertrend_dir(h, l, c, true_range(df), period, mult)

def _supertrend_dir(high, low, close, tr_series, period=10, mult=3.0):
    h,l,c=[x.to_numpy(float) for x in (high, low, close)]
    tr=tr_series.to_numpy(float)
    atr=pd.Series(tr).ewm(alpha=1/period,adjust=False,min_periods=period).mean().to_numpy()
    hl2=(h+l)/2; bu=hl2+mult*atr; bl=hl2-mult*atr
    fu=bu.copy(); fl=bl.copy(); bull=np.ones(len(h),dtype=bool)
    for i in range(1,len(h)):
        if not np.isfinite(atr[i-1]): continue
        fu[i]=bu[i] if (bu[i]<fu[i-1] or c[i-1]>fu[i-1]) else fu[i-1]
        fl[i]=bl[i] if (bl[i]>fl[i-1] or c[i-1]<fl[i-1]) else fl[i-1]
        if c[i]>fu[i-1]: bull[i]=True
        elif c[i]<fl[i-1]: bull[i]=False
        else: bull[i]=bull[i-1]
    return pd.Series(bull,index=high.index)

def atr_trend_state(df):
    h=pd.to_numeric(df.high,errors="coerce"); l=pd.to_numeric(df.low,errors="coerce")
    return _atr_trend_state(h, l, pd.to_numeric(df.close,errors="coerce"), true_range(df))

def _atr_trend_state(h, l, c, tr):
    # Same state machine as before; scalar loop over NumPy arrays instead of
    # pandas .iloc per row (regression-tested for identical output).
    e=ema((h+l)/2,10); a=rma(tr,14)
    bu=(e+2*a).to_numpy(float); bl=(e-2*a).to_numpy(float); cv=c.to_numpy(float)
    state=1; final_u=np.nan; final_l=np.nan; states=[]
    for i in range(len(cv)):
        if not math.isfinite(bu[i]) or not math.isfinite(bl[i]):
            states.append(state); continue
        if state==1:
            final_l=max(float(bl[i]), float(final_l) if math.isfinite(final_l) else float(bl[i]))
            final_u=float(bu[i])
        else:
            final_u=min(float(bu[i]), float(final_u) if math.isfinite(final_u) else float(bu[i]))
            final_l=float(bl[i])
        prev_u=final_u; prev_l=final_l
        if state==-1 and math.isfinite(cv[i]) and float(cv[i])>prev_u: state=1
        elif state==1 and math.isfinite(cv[i]) and float(cv[i])<prev_l: state=-1
        states.append(state)
    return pd.Series(states,index=h.index)

def mss_state(df,left=5,right=5):
    return _mss_state(pd.to_numeric(df.high,errors="coerce"), pd.to_numeric(df.low,errors="coerce"),
                      pd.to_numeric(df.close,errors="coerce"), left, right)

def _mss_state(high, low, close, left=5, right=5):
    # Same pivot/break rule as before over NumPy arrays (regression-tested).
    h=high.to_numpy(float); l=low.to_numpy(float); c=close.to_numpy(float)
    n=len(h); last_ph=last_pl=None; state=0; states=[]
    for i in range(n):
        p=i-right
        if p>=left and p+right<n:
            hw=h[p-left:p+right+1]; lw=l[p-left:p+right+1]
            if not np.isnan(hw).any() and float(h[p])==float(hw.max()): last_ph=float(h[p])
            if not np.isnan(lw).any() and float(l[p])==float(lw.min()): last_pl=float(l[p])
        cv=c[i]
        if math.isfinite(cv):
            if last_ph is not None and float(cv)>last_ph: state=1
            elif last_pl is not None and float(cv)<last_pl: state=-1
        states.append(state)
    return pd.Series(states,index=high.index)

def anchored_vwap(df, tf):
    return _anchored_vwap(df["time"], pd.to_numeric(df.high,errors="coerce"), pd.to_numeric(df.low,errors="coerce"),
                          pd.to_numeric(df.close,errors="coerce"),
                          pd.to_numeric(df.volume,errors="coerce").fillna(0), tf)

def _anchored_vwap(time_col, h, l, c, v, tf):
    t=pd.to_datetime(time_col,errors="coerce")
    tp=(h+l+c)/3
    secs = tf_seconds(tf)
    # Pine anchor: <15m=D, 15m~<1h=W, 1h~<4h=M, >=4h=Q.
    if secs < 15 * 60:
        key=t.dt.to_period("D")
    elif secs < 60 * 60:
        key=t.dt.to_period("W")
    elif secs < 4 * 60 * 60:
        key=t.dt.to_period("M")
    else:
        key=t.dt.to_period("Q")
    pv=tp*v
    return pv.groupby(key).cumsum()/v.groupby(key).cumsum().replace(0,np.nan)

def mfi(df,n=14):
    return _mfi(pd.to_numeric(df.high,errors="coerce"), pd.to_numeric(df.low,errors="coerce"),
                pd.to_numeric(df.close,errors="coerce"), pd.to_numeric(df.volume,errors="coerce").fillna(0), n)

def _mfi(h, l, c, vol, n):
    tp=(h+l+c)/3
    flow=tp*vol; d=tp.diff()
    pos=flow.where(d>0,0.0).rolling(n).sum(); neg=flow.where(d<0,0.0).rolling(n).sum()
    ratio=pos/neg.replace(0,np.nan)
    return 100-(100/(1+ratio))

def cmf(df,n=20):
    h,l,c,v=[pd.to_numeric(df[x],errors="coerce") for x in ("high","low","close","volume")]
    return _cmf(h, l, c, v, n)

def _cmf(h, l, c, v, n):
    ad=(((c-l)-(h-c))/(h-l).replace(0,np.nan)*v).fillna(0)
    return ad.rolling(n).sum()/v.rolling(n).sum().replace(0,np.nan)


# =============================================================================
# Fact registry
# =============================================================================
# Raw STAFF columns a Fact may read. FactStore compares exactly these columns.
RAW_COLUMNS = ("time", "open", "high", "low", "close", "volume", "hma_50", "open_band_4_mid", "wonbi_upper", "wonbi_lower")


@dataclass(frozen=True)
class FactSpec:
    name: str
    deps: tuple[str, ...]        # other Facts, or "@column" for a raw STAFF column
    compute: Callable            # compute(frame, *dep_values) -> value
    description: str = ""
    owner: str = "indicator_facts"
    invalidation: str = "forming_bar"
    shared: bool = True
    array_compute: Optional[Callable] = None
    parameters: tuple[str, ...] = ()
    recurrence: Optional[tuple] = None


FACTS: dict[str, FactSpec] = {}


def fact(name: str, *deps: str, description: str = "", owner: str = "indicator_facts",
         invalidation: str = "forming_bar", shared: bool = True, array_compute=None, parameters=(), recurrence=None):
    def register(fn):
        if name in FACTS:
            raise ValueError(f"duplicate fact: {name}")
        if invalidation not in ("forming_bar", "bar_close"):
            raise ValueError("unknown fact invalidation unit")
        FACTS[name] = FactSpec(name, tuple(deps), fn, description, owner, invalidation, shared, array_compute, tuple(parameters),recurrence)
        return fn
    return register


def wonbi_standard_deviation(mid, native_upper):
    """MT5's fixed OPEN4/3-sigma source; never recalculate its rolling window."""
    return (native_upper-mid)/3.0


def wonbi_bands(mid, native_upper, native_lower, sigma=3.0):
    """Single configurable Wonbi Fact; sigma3 passes original MT5 bits through."""
    sigma = float(sigma)
    if not math.isfinite(sigma) or sigma <= 0:
        raise ValueError('WONBI_SIGMA must be positive and finite')
    if sigma == 3.0:
        return {'wonbi_mid':mid, 'wonbi_upper':native_upper, 'wonbi_lower':native_lower}
    width = sigma * wonbi_standard_deviation(mid, native_upper)
    return {'wonbi_mid':mid, 'wonbi_upper':mid+width, 'wonbi_lower':mid-width}


def _wonbi_from_frame(frame, *unused):
    data=frame.df
    return wonbi_bands(data['open_band_4_mid'],data['wonbi_upper'],data['wonbi_lower'],
                       getattr(frame,'parameters',{}).get('WONBI_SIGMA',3.0))


fact('WONBI_BANDS','@open_band_4_mid','@wonbi_upper','@wonbi_lower',
     description='MT5 fixed 3-sigma bands, optionally rescaled by startup config',
     parameters=('WONBI_SIGMA',),array_compute=_wonbi_from_frame)(_wonbi_from_frame)


def _num(frame, column):
    return pd.to_numeric(frame.df[column], errors="coerce")


def candle_direction_values(open_prices, close_prices):
    """Pure candle polarity: up=1, down=-1, equal=0, invalid input=NaN.

    Compare prices directly instead of subtracting them, so finite extreme
    prices cannot overflow. Both the Series and incremental array paths use
    this one formula; the latest close may belong to a forming candle.
    """
    opened = np.asarray(open_prices, dtype=float)
    closed = np.asarray(close_prices, dtype=float)
    valid = np.isfinite(opened) & np.isfinite(closed)
    values = np.where(valid, np.where(closed > opened, 1.,
                                     np.where(closed < opened, -1., 0.)), np.nan)
    return float(values) if values.ndim == 0 else values


def _candle_direction_from_frame(frame, *unused):
    return pd.Series(candle_direction_values(_num(frame, "open"), _num(frame, "close")),
                     index=frame.df.index)


def _candle_direction_from_array(frame, *unused):
    return candle_direction_values(frame.df["open"], frame.df["close"])


def candle_shape_values(open_prices, high_prices, low_prices, close_prices):
    """Pure candle shape: hammer=1, inverted hammer=-1, neither=0, invalid input=NaN.

    Hammer: the lower wick is at least twice the body and the upper wick is shorter
    than half the body. Inverted hammer: the same upside down. A candle without a
    body is neither. Colour is not part of the shape. Every Fact path uses this one
    formula; the latest candle may be forming.
    """
    opened, high, low, closed = (np.asarray(v, dtype=float)
                                 for v in (open_prices, high_prices, low_prices, close_prices))
    valid = np.isfinite(opened) & np.isfinite(high) & np.isfinite(low) & np.isfinite(closed)
    with np.errstate(invalid='ignore', over='ignore'):
        top, bottom = np.maximum(opened, closed), np.minimum(opened, closed)
        body, upper, lower = top - bottom, high - top, bottom - low
        hammer = (lower >= 2 * body) & (upper < 0.5 * body)
        inverted = (upper >= 2 * body) & (lower < 0.5 * body)
    values = np.where(valid, np.where(hammer, 1., np.where(inverted, -1., 0.)), np.nan)
    return float(values) if values.ndim == 0 else values


def _candle_shape_from_frame(frame, *unused):
    return pd.Series(candle_shape_values(_num(frame, "open"), _num(frame, "high"),
                                         _num(frame, "low"), _num(frame, "close")), index=frame.df.index)


def _candle_shape_from_array(frame, *unused):
    return candle_shape_values(frame.df["open"], frame.df["high"], frame.df["low"], frame.df["close"])


def true_range_array(high, low, close):
    previous = np.empty_like(close); previous[0:1] = np.nan; previous[1:] = close[:-1]
    return np.fmax(np.fmax(high-low, np.abs(high-previous)), np.abs(low-previous))


# Public array backend shared by event INDICATOR and variable-period Watch Facts.
# The old pandas API remains for COMPOSER and numerical reference diagnostics.
from indicator_facts_numpy import ArrayFactFrame, ma_array


def rma_array(values, period):
    """Same adjust=False/ignore_na=False EWM recurrence and TR[0] seed."""
    from indicator_facts_numpy import _ewm_array
    alpha = 1. / (1. + ((1. - 1./period) / (1./period)))
    return _ewm_array(values, alpha, period)


# --- raw inputs --------------------------------------------------------------
fact("o", "@open", description="open")(lambda f, _col: _num(f, "open"))
fact("h", "@high", description="high", array_compute=lambda f, _: f.df['high'])(lambda f, _col: _num(f, "high"))
fact("l", "@low", description="low", array_compute=lambda f, _: f.df['low'])(lambda f, _col: _num(f, "low"))
fact("c", "@close", description="close", array_compute=lambda f, _: f.df['close'])(lambda f, _col: _num(f, "close"))
fact("v", "@volume", description="volume, NaN->0")(lambda f, _col: _num(f, "volume").fillna(0))
fact("vraw", "@volume", description="volume as delivered (CMF)")(lambda f, _col: _num(f, "volume"))
fact("h50", "@hma_50", description="STAFF OPEN HMA50")(lambda f, _col: _num(f, "hma_50"))
fact("candle_direction", "@open", "@close",
     description="candle polarity: close vs open; up=1, down=-1, equal=0, invalid=NaN",
     array_compute=_candle_direction_from_array)(_candle_direction_from_frame)
fact("candle_shape", "@open", "@high", "@low", "@close",
     description="candle shape: hammer=1, inverted hammer=-1, neither=0, invalid=NaN",
     array_compute=_candle_shape_from_array)(_candle_shape_from_frame)
fact("tr", "h", "l", "c", description="true range", array_compute=lambda f,h,l,c: true_range_array(h,l,c))(lambda f, h, l, c: _true_range(h, l, c))

@fact("ATR14_GENERAL", "tr", description="STAFF ATR14: TR[0] ewm seed", array_compute=lambda f,tr: rma_array(tr,14),
      recurrence=('rma',14))
def ATR14_GENERAL(frame, tr):
    return rma(tr, 14)


# --- OPEN based (change only on a new bar) -----------------------------------
fact("e10", "o")(lambda f, o: ema(o, 10))
fact("e50", "o")(lambda f, o: ema(o, 50))
fact("s20", "o", description="SMA20(open)")(lambda f, o: sma(o, 20))
fact("w17", "o")(lambda f, o: wma(o, 17))
fact("std", "@open_band_4_mid", "@wonbi_upper", description="MT5 OPEN4 population std recovered from fixed 3-sigma source")(
    lambda f, *_: wonbi_standard_deviation(_num(f,'open_band_4_mid'),_num(f,'wonbi_upper')))
fact("width", "std")(lambda f, std: 6 * std)
fact("safe", "width")(lambda f, width: width > width.rolling(8).mean())

# --- trend / momentum --------------------------------------------------------
fact("st", "h", "l", "c", "tr", description="Supertrend(10,3) bullish")(
    lambda f, h, l, c, tr: _supertrend_dir(h, l, c, tr, 10, 3))
fact("sar", "h", "l", description="PSAR")(lambda f, h, l: _psar(h, l))
fact("dmi", "h", "l", "tr", description="DMI14 (+DI, -DI, ADX)")(lambda f, h, l, tr: _dmi(h, l, tr, 14))
fact("plus", "dmi")(lambda f, d: d[0])
fact("minus", "dmi")(lambda f, d: d[1])
fact("adx", "dmi")(lambda f, d: d[2])
fact("lr", "c", description="linreg20")(lambda f, c: linreg(c, 20))
fact("rv", "c", description="RSI14")(lambda f, c: rsi(c, 14))
fact("cv", "h", "l", "c", description="CCI20")(lambda f, h, l, c: _cci(h, l, c, 20))
fact("e12", "c")(lambda f, c: ema(c, 12))
fact("e26", "c")(lambda f, c: ema(c, 26))
fact("macd", "e12", "e26")(lambda f, e12, e26: e12 - e26)
fact("sig", "macd")(lambda f, macd: ema(macd, 9))
fact("aup", "h")(lambda f, h: h.rolling(14).apply(lambda x: 100 * (14 - 1 - np.argmax(x[::-1])) / 14, raw=True))
fact("adn", "l")(lambda f, l: l.rolling(14).apply(lambda x: 100 * (14 - 1 - np.argmin(x[::-1])) / 14, raw=True))
fact("vmp", "h", "l")(lambda f, h, l: (h - l.shift(1)).abs().rolling(14).sum())
fact("vmm", "h", "l")(lambda f, h, l: (l - h.shift(1)).abs().rolling(14).sum())
fact("trs", "tr", description="TR 14 sum")(lambda f, tr: tr.rolling(14).sum())
fact("vip", "vmp", "trs")(lambda f, vmp, trs: vmp / trs.replace(0, np.nan))
fact("vim", "vmm", "trs")(lambda f, vmm, trs: vmm / trs.replace(0, np.nan))
fact("vw", "@time", "h", "l", "c", "v", description="anchored VWAP")(
    lambda f, _t, h, l, c, v: _anchored_vwap(f.df["time"], h, l, c, v, f.tf))
fact("bop", "o", "h", "l", "c")(lambda f, o, h, l, c: (c - o) / (h - l).replace(0, np.nan))
fact("e13", "c")(lambda f, c: ema(c, 13))
fact("bullp", "h", "e13")(lambda f, h, e13: h - e13)
fact("bearp", "l", "e13")(lambda f, l, e13: l - e13)
fact("cmfv", "h", "l", "c", "vraw", description="CMF20")(lambda f, h, l, c, v: _cmf(h, l, c, v, 20))
fact("mfiv", "h", "l", "c", "v", description="MFI14")(lambda f, h, l, c, v: _mfi(h, l, c, v, 14))
fact("vol_ema", "v")(lambda f, v: ema(v, 20))
fact("vol_surge", "v", "vol_ema")(lambda f, v, vol_ema: v > vol_ema * 1.2)
fact("vol_state", "h", "l", "c", "tr", description="ATR trend state")(
    lambda f, h, l, c, tr: _atr_trend_state(h, l, c, tr))
fact("trsum", "trs")(lambda f, trs: trs)
fact("hh", "h")(lambda f, h: h.rolling(14).max())
fact("ll", "l")(lambda f, l: l.rolling(14).min())
fact("chop", "trsum", "hh", "ll")(
    lambda f, trsum, hh, ll: 100 * np.log10(trsum / (hh - ll).clip(lower=1e-12)) / math.log10(14))
fact("logret", "c")(lambda f, c: np.log(c / c.shift(1)))
fact("hv", "logret")(lambda f, logret: logret.rolling(20).std(ddof=0) * math.sqrt(252) * 100)
fact("hvma", "hv")(lambda f, hv: hv.rolling(20).mean())
fact("mss", "h", "l", "c", description="market structure shift")(lambda f, h, l, c: _mss_state(h, l, c, 5, 5))
fact("ten", "h", "l")(lambda f, h, l: (l.rolling(5).min() + h.rolling(5).max()) / 2)
fact("kij", "h", "l")(lambda f, h, l: (l.rolling(13).min() + h.rolling(13).max()) / 2)
fact("sa", "ten", "kij")(lambda f, ten, kij: (ten + kij) / 2)
fact("sb", "h", "l")(lambda f, h, l: (l.rolling(26).min() + h.rolling(26).max()) / 2)
fact("cloud_top", "sa", "sb")(lambda f, sa, sb: pd.concat([sa.shift(13), sb.shift(13)], axis=1).max(axis=1))
fact("cloud_bot", "sa", "sb")(lambda f, sa, sb: pd.concat([sa.shift(13), sb.shift(13)], axis=1).min(axis=1))


# --- lightweight strategy trend ---------------------------------------------
TREND_BASIS = "SMA20(open) slope vs 1 bar ago + HMA50 slope vs 2 bars ago"


@fact("trend", "s20", "h50", description="strategy trend: SMA20(open) and HMA50 slopes agree")
def _trend_fact(f, s20, h50):
    """Strategy 추세 (used by LIVE/SPECIAL/Watch conditions).

    UP   : SMA20(open) slope > 0 and HMA50 slope > 0
    DOWN : SMA20(open) slope < 0 and HMA50 slope < 0
    NEUTRAL otherwise (different directions or a flat slope).
    SMA20 slope = SMA20[-1] - SMA20[-2] (진행봉 vs 1봉 전, 사용자 지정).
    HMA50 slope = HMA50[-1] - HMA50[-3] (기존 hma50_slope / Hull color 기준).
    """
    if len(s20) < 3:
        return None
    sma_slope = s20.iloc[-1] - s20.iloc[-2]
    hma_slope = h50.iloc[-1] - h50.iloc[-3]
    if not (finite(sma_slope) and finite(hma_slope)):
        return None
    sma_slope, hma_slope = float(sma_slope), float(hma_slope)
    if sma_slope > 0 and hma_slope > 0:
        trend, direction = "UP", "LONG"
    elif sma_slope < 0 and hma_slope < 0:
        trend, direction = "DOWN", "SHORT"
    else:
        trend, direction = "NEUTRAL", "NEUTRAL"
    return {"trend": trend, "direction": direction,
            "sma20_slope": sma_slope, "hma50_slope": hma_slope}


def _resolve_inputs(name: str, seen=None) -> frozenset:
    spec = FACTS[name]
    out: set[str] = set()
    for dep in spec.deps:
        if dep.startswith("@"):
            out.add(dep[1:])
        else:
            out |= _resolve_inputs(dep)
    return frozenset(out)


FACT_INPUTS: dict[str, frozenset] = {name: _resolve_inputs(name) for name in FACTS}


# =============================================================================
# Per-frame evaluation and cross-frame reuse
# =============================================================================
class FactStats:
    """Counters used by performance tests and logs."""

    def __init__(self):
        self.computed: dict[str, int] = {}
        self.carried = 0
        self.frames = 0
        self.identical_frames = 0

    def reset(self) -> None:
        self.__init__()


class FactFrame:
    """Lazily computed Facts for one input frame; each Fact at most once."""

    def __init__(self, df: pd.DataFrame, tf: str, *, carried: Optional[dict] = None,
                 stats: Optional[FactStats] = None):
        self.df = df
        self.tf = tf
        self.values: dict[str, object] = dict(carried or {})
        self.stats = stats

    def __len__(self):
        return len(self.df)

    def get(self, name: str):
        values = self.values
        if name in values:
            return values[name]
        spec = FACTS[name]
        args = [None if dep.startswith("@") else self.get(dep) for dep in spec.deps]
        value = spec.compute(self, *args)
        values[name] = value
        if self.stats is not None:
            self.stats.computed[name] = self.stats.computed.get(name, 0) + 1
        return value

    __getitem__ = get

    def last(self, name: str, offset: int = -1):
        return self.get(name).iloc[offset]


def _column_arrays(df: pd.DataFrame) -> dict[str, np.ndarray]:
    out = {}
    for column in RAW_COLUMNS:
        if column not in df.columns:
            continue
        values = df[column]
        if column == "time":
            out[column] = pd.to_datetime(values, errors="coerce").to_numpy(dtype="datetime64[ns]")
        else:
            out[column] = pd.to_numeric(values, errors="coerce").to_numpy(dtype=float)
    return out


class _Entry:
    __slots__ = ("frame", "arrays", "index")

    def __init__(self, frame, arrays, index):
        self.frame = frame
        self.arrays = arrays
        self.index = index


class FactStore:
    """Reuses Facts across frames of the same (symbol, TF) when their inputs match."""

    def __init__(self, max_keys: int = 512):
        self.max_keys = int(max_keys)
        self._entries: dict[tuple[str, str], _Entry] = {}
        self.stats = FactStats()

    def frame(self, symbol: str, tf: str, df: pd.DataFrame) -> FactFrame:
        key = (str(symbol), str(tf))
        arrays = _column_arrays(df)
        previous = self._entries.get(key)
        carried: dict[str, object] = {}
        self.stats.frames += 1
        if previous is not None and previous.index.equals(df.index) and previous.arrays.keys() == arrays.keys():
            changed = {name for name, a in arrays.items()
                       if not np.array_equal(a, previous.arrays[name], equal_nan=True)}
            if "time" not in changed:
                if not changed:
                    self.stats.identical_frames += 1
                for name, value in previous.frame.values.items():
                    if not (FACT_INPUTS[name] & changed):
                        carried[name] = value
                self.stats.carried += len(carried)
        frame = FactFrame(df, tf, carried=carried, stats=self.stats)
        if key not in self._entries and len(self._entries) >= self.max_keys:
            self._entries.pop(next(iter(self._entries)))
        self._entries[key] = _Entry(frame, arrays, df.index)
        return frame

    def clear(self) -> None:
        self._entries.clear()


def standalone_frame(df: pd.DataFrame, tf: str, stats: Optional[FactStats] = None) -> FactFrame:
    """A FactFrame with no cross-frame reuse (one-off calculations)."""
    return FactFrame(df, tf, stats=stats)


def parameterized_ma_fact(family: str, period: int, *, asof: bool = False) -> str:
    """Shared MA semantics: EMA=CLOSE, others=OPEN; fixed native values win.

    An as-of frame supplies the already-known quote as its forming close. EMA
    uses that quote and the preceding confirmed EMA, never an eventual close.
    """
    global RAW_COLUMNS
    family = str(family).upper()
    if family not in ('EMA', 'SMA', 'WMA', 'HMA') or not 1 <= int(period) <= 500:
        raise ValueError('invalid parameterized moving average')
    period = int(period)
    source = 'CLOSE' if family == 'EMA' else 'OPEN'
    name = f'MA_{source}_{family}_{period}' + ('_ASOF' if asof and family == 'EMA' else '')
    if name not in FACTS:
        native = f'{family.lower()}_{period}'
        fixed = native in ('ema_20', 'ema_50', 'ema_200', 'hma_6', 'hma_17', 'hma_50', 'hma_168')
        def compute(frame, values, *unused):
            if fixed and native in frame.df:
                result = _num(frame, native)
                if asof and family == 'EMA':
                    result = result.copy()
                    result.iloc[-1] = (float(result.iloc[-2]) + 2. / (period + 1) *
                                       (float(frame.df['close'].iloc[-1]) - float(result.iloc[-2]))) if len(result) > 1 else np.nan
                return result
            values = np.asarray(values, dtype=float)
            return pd.Series(ma_array(values, family, period),
                             index=frame.df.index)
        dependencies = ('c' if family == 'EMA' else 'o',) + (('@' + native,) if fixed else ())
        if fixed and native not in RAW_COLUMNS:
            RAW_COLUMNS += (native,)
        fact(name, *dependencies, description=f'{family}{period}({source})', invalidation='bar_close')(compute)
        FACT_INPUTS[name] = _resolve_inputs(name)
    return name


def parameterized_atr_fact(period: int) -> str:
    """One Wilder TR/RMA definition, shared with the existing ATR14 Fact."""
    period = int(period)
    if not 1 <= period <= 500:
        raise ValueError('invalid parameterized ATR')
    if period == 14:
        return 'ATR14_GENERAL'
    name = f'ATR_GENERAL_{period}'
    if name not in FACTS:
        fact(name, 'tr', description=f'Wilder ATR{period}: TR[0] ewm seed', invalidation='bar_close',
             array_compute=lambda frame, tr: rma_array(tr, period))(
             lambda frame, tr: rma(tr, period))
        FACT_INPUTS[name] = _resolve_inputs(name)
    return name


__all__ = [
    "MT5_TIMEFRAMES", "normalize_tf", "tf_seconds", "sort_timeframes", "finite",
    "candle_direction_values", "candle_shape_values",
    "ema", "sma", "wma", "hma", "true_range", "rma", "dmi", "rsi", "cci", "linreg", "psar",
    "supertrend_dir", "atr_trend_state", "mss_state", "anchored_vwap", "mfi", "cmf",
    "RAW_COLUMNS", "FactSpec", "FACTS", "FACT_INPUTS", "TREND_BASIS",
    "FactStats", "FactFrame", "FactStore", "standalone_frame",
]


# S1: pure legacy STAFF facts, function bodies preserved verbatim.
ATR14_LENGTH = 14

def add_ema_derived(df: pd.DataFrame) -> pd.DataFrame:
    # EMA 값 자체는 MT5에서 받습니다. slope만 전달값의 차분으로 계산합니다.
    if "ema_20" in df.columns:
        df["ema_20_slope"] = df["ema_20"].diff()
    return df


def add_atr14_feature(df: pd.DataFrame) -> pd.DataFrame:
    """순수 Wilder ATR14. 전략 multiplier/생존 판정은 여기서 수행하지 않습니다."""
    high = pd.to_numeric(df["high"], errors="coerce")
    low = pd.to_numeric(df["low"], errors="coerce")
    close = pd.to_numeric(df["close"], errors="coerce")
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    df["atr_14"] = tr.ewm(
        alpha=1.0 / ATR14_LENGTH,
        adjust=False,
        min_periods=ATR14_LENGTH,
    ).mean()
    return df


def add_supertrend(df: pd.DataFrame, period: int = 10, multiplier: float = 3.0) -> pd.DataFrame:
    if df.empty:
        return df

    high = df["high"].to_numpy(dtype=float)
    low = df["low"].to_numpy(dtype=float)
    close = df["close"].to_numpy(dtype=float)

    tr1 = high - low
    tr2 = np.abs(high - np.roll(close, 1))
    tr3 = np.abs(low - np.roll(close, 1))
    tr2[0] = tr3[0] = 0.0
    tr = np.maximum(tr1, np.maximum(tr2, tr3))

    atr = np.zeros(len(df), dtype=float)
    atr[0] = tr[0]
    for i in range(1, len(df)):
        atr[i] = (atr[i - 1] * (period - 1) + tr[i]) / period

    hl2 = (high + low) / 2.0
    fu = hl2 + multiplier * atr
    fl = hl2 - multiplier * atr
    st = np.ones(len(df), dtype=bool)

    for i in range(1, len(df)):
        if close[i] > fu[i - 1]:
            st[i] = True
        elif close[i] < fl[i - 1]:
            st[i] = False
        else:
            st[i] = st[i - 1]
            if st[i] and fl[i] < fl[i - 1]:
                fl[i] = fl[i - 1]
            if (not st[i]) and fu[i] > fu[i - 1]:
                fu[i] = fu[i - 1]

    df["supertrend_is_positive"] = st
    return df


def add_mt5_basis_slopes(df: pd.DataFrame) -> pd.DataFrame:
    # The canonical difference is delivered by the same MT5 indicator.
    for ind in ("RSI", "STO", "DI"):
        df[f"{ind}_slope"] = df[f"{ind}_regime_slope"]
    return df


def add_native_band_state_features(df, prefix):
    """Expose native OUT/slope with the legacy column vocabulary, no recalculation."""
    value, lower, upper = (('price_hma_6','price_band_lower','price_band_upper') if prefix=='price'
                           else (prefix+'_val',prefix+'_db',prefix+'_ub'))
    valid=df[[value,lower,upper]].notna().all(axis=1)
    zone=np.select([df[prefix+'_lower_out'].notna(),df[prefix+'_upper_out'].notna()],[-1.,1.],default=0.)
    df[prefix+'_percentile_zone']=pd.Series(zone,index=df.index).where(valid,np.nan)
    df[prefix+'_percentile_in']=pd.Series(zone==0,index=df.index).where(valid)
    # No native regime-zone column exists: classify against native boundaries.
    low,high=df[prefix+'_regime_lower'],df[prefix+'_regime_upper']
    valid=df[value].notna()&low.notna()&high.notna()
    zone=np.select([df[value]<low,df[value]>high],[-1.,1.],default=0.)
    df[prefix+'_regime_zone']=pd.Series(zone,index=df.index).where(valid,np.nan)
    df[prefix+'_regime_in']=pd.Series(zone==0,index=df.index).where(valid)
    return df


