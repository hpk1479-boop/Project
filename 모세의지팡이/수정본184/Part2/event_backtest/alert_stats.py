"""Alert-only statistics from counts taken while the result CSV was written.

One alert = one signal_id (the CSV has one row per recipient). Basis is the
selected UTC period [start, end); months without alerts are listed as 0.
"""
import datetime as dt

MONTH_DAYS = 365.2425 / 12


def month_key(time_ms):
    return dt.datetime.fromtimestamp(time_ms / 1000, dt.timezone.utc).strftime('%Y-%m')


def summarize(start, end, chunks, *, periods=None):
    counts = {}
    for chunk in chunks:
        for key, value in (chunk.get('alert_months') or {}).items():
            counts[key] = counts.get(key, 0) + int(value)
    lo, hi = dt.date.fromisoformat(start), dt.date.fromisoformat(end)
    selected=[(dt.date.fromisoformat(p['start']),dt.date.fromisoformat(p['end'])) for p in periods] if periods else [(lo,hi)]
    keys=set()
    for first,last in selected:
        cursor=dt.date(first.year,first.month,1)
        while cursor<last:
            keys.add(cursor.strftime('%Y-%m'))
            cursor=dt.date(cursor.year+cursor.month//12,cursor.month%12+1,1)
    months=[{'month':key,'alerts':counts.get(key,0)} for key in sorted(keys)]
    total = sum(counts.values())
    days = sum((last-first).days for first,last in selected)
    # Averages need at least one full unit of the selected period; otherwise None (blank).
    return {'basis': 'UTC stored periods, end exclusive; excluded gaps omitted; one alert per signal_id' if periods else 'UTC selected period, end exclusive; one alert per signal_id',
            'days': days, 'total': total, 'months': months,
            'daily_average': total / days if days >= 1 else None,
            'weekly_average': total * 7 / days if days >= 7 else None,
            'monthly_average': total * MONTH_DAYS / days if days >= 28 else None}


def lines(stats):
    def value(v):
        return '' if v is None else f'{v:.2f}'
    head = (f"총 알림 {stats['total']}건 · 일평균 {value(stats['daily_average'])} · "
            f"주평균 {value(stats['weekly_average'])} · 월평균 {value(stats['monthly_average'])}")
    months = '월별: ' + ' · '.join(f"{m['month']} {m['alerts']}건" for m in stats['months'])
    return head, months
