"""Input windows shared by pipe ingress and replay, independent of wall time.

Finite lookbacks get 16 spare bars. Recursive indicators get a 96-bar seed
window (seed differences are an explicitly approved PERF1 diagnostic).
OZ now reads full NumPy Snapshot history independently of these windows.
"""
import pandas as pd
from watch_ma import ma_history

MARGIN = 16


def ma_source_rows(name):
    return max(80,ma_history(name)+3)+MARGIN


def required_rows(raw, tf, indicators, *, role='default'):
    n=len(raw)
    if not n:return 0
    times=pd.DatetimeIndex(raw['time'])
    required=80  # largest finite base window <= 50; extra recursive warmup.
    if role=='fvg':required=33  # 30 candidate closes + two predecessors + live
    elif role=='sweep':
        required=3
        if tf=='1d':
            day=times[-1].normalize()
            cutoff=day-pd.Timedelta(days=day.weekday()+7)
            required=max(required,n-int(times.searchsorted(cutoff)))
        elif tf=='5m':
            # Prior session may begin on the preceding KST calendar day.
            last=times[-2 if n>1 else -1].tz_localize('UTC').tz_convert('Asia/Seoul')
            cutoff=(last.normalize()-pd.Timedelta(days=1)).tz_convert('UTC').tz_localize(None)
            required=max(required,n-int(times.searchsorted(cutoff)))
    for name in indicators or ():
        try:required=max(required,ma_history(name)+3)
        except (ValueError,TypeError):pass
    if role=='indicator':
        amount=int(tf[:-1]);seconds=amount*{'m':60,'h':3600,'d':86400}[tf[-1]]
        period='D' if seconds<900 else 'W' if seconds<3600 else 'M' if seconds<14400 else 'Q'
        required=max(required,n-int(times.searchsorted(times[-1].to_period(period).start_time)))
    if role=='composer' and not indicators:
        # Timed-chain expiry counts closed observations since arbitrary stored
        # anchors. Empty-indicator requests are a bar calendar.
        required=n
    return min(n,required+MARGIN)
