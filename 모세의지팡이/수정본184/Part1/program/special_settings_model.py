"""Read-only host settings vocabulary; no GUI or engine side effects."""
import calendar,datetime as dt,json,re
from pathlib import Path
import oz_profiles

SESSIONS={'MAIN_ASIA':'아시아','MAIN_LONDON':'런던','MAIN_NEWYORK':'뉴욕'}

def discover(folder,research=False):
    if not research:
        from strategy_recipe.registry import list_presets
        return list(list_presets('Part1'))
    pattern=r'(?:Test_)?SPECIAL(\d+)' if research else r'SPECIAL(\d+)'
    result=[]
    for path in Path(folder).glob('*.py'):
        match=re.fullmatch(pattern,path.stem,re.I)
        if match:result.append((int(match[1]),path.stem))
    return [name for _,name in sorted(result)]

def source_defaults(folder,name):
    from strategy_recipe.registry import default_settings
    return default_settings(name)

def gui_dates(today=None):
    end=today or dt.datetime.now(dt.timezone.utc).date()
    year,month=(end.year-1,12) if end.month==1 else (end.year,end.month-1)
    start=dt.date(year,month,min(end.day,calendar.monthrange(year,month)[1]))
    return start.isoformat(),end.isoformat()

def _clock(text):
    raw=str(text or '').replace(':','')
    return raw[:2]+':'+raw[2:] if len(raw)==4 else raw

def time_summary(value,default=0,session_times=None):
    """Checked sessions with their times; '24시간' when none is checked. value None = the strategy's own times (default)."""
    ranges=default if isinstance(default,dict) else {}
    names=default if isinstance(default,list) else [default.strip()] if isinstance(default,str) and default.strip() else []
    times=session_times or {}
    parts=[]
    for key,label in SESSIONS.items():
        own=None if value is None else value.get(key)
        if value is None:
            on,own=key in names or key in ranges,{}
        else:
            on=own is True or (isinstance(own,dict) and bool(own.get('enabled',True)))
            own=own if isinstance(own,dict) else {}
        if not on:continue
        fallback=str(ranges[key] if key in ranges else times.get(key,'')).replace(':','').split('-')
        start=own.get('start') or fallback[0]
        end=own.get('end') or (fallback[1] if len(fallback)>1 else '')
        parts.append(label+(' '+_clock(start)+'~'+_clock(end) if start and end else ''))
    return ', '.join(parts) if parts else '24시간'

def time_env(settings):
    return json.dumps({n:i['time_filters'] for n,i in (settings or {}).items() if i.get('time_filters') is not None},ensure_ascii=False)

