from pathlib import Path
import datetime as dt
import hashlib
import json
from pathlib import PureWindowsPath

ROOT=Path(__file__).resolve().parents[2]
PROGRAM=ROOT/'Part1/program'
UTC=dt.timezone.utc
COMMAND_CONTINUOUS_REASON='명령으로 등록한 감시 상태를 보존하기 위해 한 엔진에서 연속 재생합니다.'

def default_dates(today=None):
    today=today or dt.date.today()
    end=today.replace(day=1)
    return end.replace(year=end.year-1).isoformat(),end.isoformat()

# Logical symbols come from the EA/captures; only the form is checked here
# (same rule as the LIVE pipe: 1-64 chars, no whitespace, comma or control chars).
import re as _re
SYMBOL_FORM=_re.compile(r'[^\s,\x00-\x1f\x7f]{1,64}')

def settings(path=None):
    path=Path(path or ROOT/'Part2/event_backtest.json')
    data=json.loads(path.read_text('utf-8-sig')) if path.exists() else {}
    root=data.get('warehouse')
    if root:
        selected=Path(root).expanduser()
        if not selected.is_absolute():selected=path.parent/selected
    else:
        parent=ROOT.parent
        selected=parent.with_name(parent.name+'_warehouse') if parent.name else parent/'개피곤_warehouse'
    data['warehouse']=str(selected.resolve())
    data.setdefault('overlap_trading_days',3)
    data.setdefault('cores',None)
    data.setdefault('capture_start','keyframe')
    data.setdefault('oz_evaluation','selected')
    # Per computer: logical symbol -> this broker's Strategy Tester symbol. Missing = same name.
    data.setdefault('broker_symbols',{})
    return data

def tester_symbol(logical,config=None):
    mapping=(config if config is not None else settings()).get('broker_symbols') or {}
    broker=str(mapping.get(logical,logical)).strip()
    if not SYMBOL_FORM.fullmatch(broker):raise ValueError('broker_symbols 값 형식 오류: '+repr(broker))
    return broker

def stored_symbols(warehouse):
    """Logical symbols that already have captures; empty when the catalog is absent or busy."""
    path=Path(warehouse)/'captures.duckdb'
    if not path.is_file():return []
    try:
        import duckdb
        db=duckdb.connect(str(path),read_only=True)
        try:return [r[0] for r in db.execute('SELECT DISTINCT symbol FROM captures WHERE symbol IS NOT NULL ORDER BY symbol').fetchall()]
        finally:db.close()
    except Exception:return []

def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()

def file_hash(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda:f.read(4*1024*1024),b''):h.update(block)
    return h.hexdigest()

def warehouse_path(root,relative):
    value=str(relative).replace('\\','/')
    if PureWindowsPath(value).drive or value.startswith('/') or '..' in Path(value).parts:
        raise ValueError('창고 목록은 루트 기준 상대 경로만 허용합니다: '+value)
    path=(Path(root)/value).resolve()
    if not path.is_relative_to(Path(root).resolve()):raise ValueError('warehouse path escape')
    return path

def relative_path(root,path):
    return Path(path).resolve().relative_to(Path(root).resolve()).as_posix()

def milliseconds(value):
    v=dt.datetime.fromisoformat(str(value))
    if v.tzinfo is None:v=v.replace(tzinfo=UTC)
    return int(v.timestamp()*1000)

def months(start,end):
    start=dt.date.fromisoformat(start);end=dt.date.fromisoformat(end)
    if start>=end:raise ValueError('start must precede exclusive end')
    while start<end:
        after=(start.replace(day=28)+dt.timedelta(days=4)).replace(day=1)
        stop=min(after,end)
        yield start.isoformat(),stop.isoformat()
        start=stop

def work_periods(start,end,size='MONTH'):
    if size=='MONTH':yield from months(start,end);return
    if size not in ('FORTNIGHT','WEEK','DAY'):raise ValueError('work_size: MONTH / FORTNIGHT / WEEK / DAY')
    cursor=dt.date.fromisoformat(start);limit=dt.date.fromisoformat(end)
    if cursor>=limit:raise ValueError('start must precede exclusive end')
    while cursor<limit:
        stop=min(cursor+dt.timedelta(days={'FORTNIGHT':14,'WEEK':7,'DAY':1}[size]),limit)
        yield cursor.isoformat(),stop.isoformat();cursor=stop

def fragments(start,end,today=None):
    """Canonical, reusable calendar pieces; no request-dependent partial months."""
    today=today or dt.date.today();current=today.replace(day=1)
    first=dt.date.fromisoformat(start);limit=dt.date.fromisoformat(end)
    if limit>today:raise ValueError('녹화 종료일은 오늘 이전의 배타적 경계여야 합니다.')
    cursor=first.replace(day=1) if first<current else first
    while cursor<limit:
        monthly=cursor<current
        stop=(cursor.replace(day=28)+dt.timedelta(days=4)).replace(day=1) if monthly else cursor+dt.timedelta(days=1)
        yield {'start':cursor.isoformat(),'end':stop.isoformat(),'unit':'MONTH' if monthly else 'DAY'}
        cursor=stop

def overlap_start(start,days):
    value=dt.date.fromisoformat(start)
    for _ in range(int(days)):
        value-=dt.timedelta(days=1)
        while value.weekday()>=5:value-=dt.timedelta(days=1)
    return value.isoformat()

def scenario(path=None,*,defaults=None,**overrides):
    start,end=default_dates()
    data={'symbol':'XAUUSD+','start':start,'end':end,'mode':'BAR',
          'strategies':[],'enabled_specials':[],
          'triggers':{},'commands':[],'result_mode':'ALERT_ONLY','spread_points':{},'build_only':False,'available_only':False,
          'overlap_trading_days':3,'cores':None,'timer_ms':1000,
          'capture_start':'keyframe','oz_evaluation':'selected','virtual_bases':[]}
    if defaults:data.update(defaults)
    if path:
        import sys
        if str(PROGRAM) not in sys.path:sys.path.insert(0,str(PROGRAM))
        from oz_profile_loader import load_saved_oz
        data.update(load_saved_oz(json.loads(Path(path).read_text('utf-8-sig')), kind='scenario', path='scenario'))
    data.update({k:v for k,v in overrides.items() if v is not None})
    # Same four-profile vocabulary as LIVE. Invalid explicit values abort, never become OZ.
    import sys
    if str(PROGRAM) not in sys.path:sys.path.insert(0,str(PROGRAM))
    import oz_profiles
    canonical={}
    for name,text in data.get('triggers',{}).items():
        try:canonical[name]=oz_profiles.canonical_profile_text(text)
        except ValueError as exc:raise ValueError(f'triggers.{name}={text!r}: {exc}') from exc
    data['triggers']=canonical
    if data['result_mode'] not in ('ALERT_ONLY','VIRTUAL_ENTRY'):raise ValueError('결과 모드: ALERT_ONLY / VIRTUAL_ENTRY')
    if data['result_mode']=='VIRTUAL_ENTRY' and not data['build_only']:
        # One target only; its explicit policy, or its defaults when none is given.
        from .virtual_defaults import target_policy,target_base_frames,target_strategy
        data['virtual_entry']=target_policy(data['strategies'],data['commands'],data.get('virtual_entry'))
        # The policy's base frame decides the frame the whole test runs on; never taken from the input as given.
        data['base_frames']=target_base_frames(data['strategies'],data['virtual_entry'])
        # The strategy conditions as edited for this test (on that base frame); None runs the recipe's own.
        data['virtual_strategy']=target_strategy(data['strategies'],data['virtual_entry'],data.get('virtual_strategy'))
    else:data['virtual_entry']=None;data['base_frames']={};data['virtual_strategy']=None
    if data.get('virtual_bases'):
        # Several base frames compared in one replay: one run per frame (variant_scenarios).
        if data['result_mode']!='VIRTUAL_ENTRY' or data['build_only']:
            raise ValueError('기준 프레임 여러 개 비교는 가상 진입 백테스트에서만 됩니다.')
        if data['commands']:raise ValueError('명령이 있는 실행은 기준 프레임 여러 개를 비교할 수 없습니다.')
        from .virtual_defaults import target_bases
        data['virtual_bases']=target_bases(data['strategies'],data['virtual_bases'])
    else:data['virtual_bases']=[]
    if type(data['available_only']) is not bool:raise ValueError('보유 데이터만 사용 여부는 참/거짓이어야 합니다.')
    if data['mode']=='TIMER':data['mode']='TICK'
    if data['mode'] not in ('BAR','EVENT','TICK'):raise ValueError('mode: BAR / EVENT / TICK')
    if data['capture_start'] not in ('keyframe','beginning'):raise ValueError('capture_start: keyframe / beginning')
    if data['oz_evaluation'] not in ('selected','all'):raise ValueError('oz_evaluation: selected / all')
    if not isinstance(data['symbol'],str) or not SYMBOL_FORM.fullmatch(data['symbol']):raise ValueError('symbol form')
    if milliseconds(data['start'])>=milliseconds(data['end']):raise ValueError('invalid dates (end exclusive)')
    if int(data['timer_ms'])<1 or int(data['overlap_trading_days'])<0:raise ValueError('invalid interval/overlap')
    if isinstance(data['strategies'],str):data['strategies']=[x.strip() for x in data['strategies'].split(',') if x.strip()]
    from event_selection import strategy_dependencies
    registered=strategy_dependencies()
    data['enabled_specials']=[x for x in data['strategies'] if x in registered]
    if data['strategies']==['ALL']:data['enabled_specials']=list(registered)
    for c in data['commands']:
        if not str(c.get('chat_id','')) or not c.get('text'):raise ValueError('command requires chat_id/text')
        if data['strategies']!=['ALL'] and c.get('strategy') not in data['strategies']:raise ValueError('command.strategy must be selected')
    data['warnings']=[COMMAND_CONTINUOUS_REASON] if data['commands'] else []
    return data


def variant_scenarios(s):
    """The runs a scenario asks for: itself, or one complete run per base frame in virtual_bases.

    Each base frame's run is the scenario of a virtual entry on that frame alone (the recipe's own
    values there), so it equals and can stand in for that run; the first keeps the scenario's run ID.
    """
    if not s.get('virtual_bases'):return [s]
    from .virtual_defaults import base_variants
    return [{**s,**variant,'virtual_bases':[],'_run_id':s.get('_run_id') if index==0 else None}
            for index,variant in enumerate(base_variants(s['strategies'],s['commands'],s['virtual_bases']))]
