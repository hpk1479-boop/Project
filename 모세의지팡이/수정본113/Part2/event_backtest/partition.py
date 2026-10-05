"""Scheduling only: each task keeps the existing trading-day warm-up policy."""
from .settings import work_periods


def plan_periods(start, end, workers, size='AUTO', adaptive=True):
    if size=='AUTO':
        size='MONTH';adaptive=True
    periods=list(work_periods(start,end,size))
    if adaptive and size=='MONTH' and len(periods)<workers:
        size='WEEK';periods=list(work_periods(start,end,size))
        if len(periods)<workers:
            size='DAY';periods=list(work_periods(start,end,size))
    return periods,size
