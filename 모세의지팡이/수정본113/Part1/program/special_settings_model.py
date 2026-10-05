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

def time_summary(value):
    if value is None:return '코드 기본값'
    names=[label for key,label in SESSIONS.items() if value.get(key,{}).get('enabled',False)]
    return '·'.join(names) if names else '알림 꺼짐 — 선택한 세션 없음'

def time_env(settings):
    return json.dumps({n:i['time_filters'] for n,i in (settings or {}).items() if i.get('time_filters') is not None},ensure_ascii=False)

