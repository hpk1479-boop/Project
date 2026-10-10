from pathlib import Path
import datetime as dt
import hashlib
import json
from pathlib import PureWindowsPath

ROOT=Path(__file__).resolve().parents[2]
PROGRAM=ROOT/'Part1/program'
UTC=dt.timezone.utc

def default_dates(today=None):
    today=today or dt.date.today()
    end=today.replace(day=1)
    return end.replace(year=end.year-1).isoformat(),end.isoformat()

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
    if Path(data['warehouse']).is_relative_to(ROOT):raise ValueError('warehouse must be outside revision directory')
    data.setdefault('overlap_trading_days',3)
    data.setdefault('cores',None)
    data.setdefault('work_size','MONTH')
    data.setdefault('capture_start','keyframe')
    data.setdefault('oz_evaluation','selected')
    return data

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
    if size!='FORTNIGHT':raise ValueError('work_size: MONTH / FORTNIGHT')
    cursor=dt.date.fromisoformat(start);limit=dt.date.fromisoformat(end)
    if cursor>=limit:raise ValueError('start must precede exclusive end')
    while cursor<limit:
        stop=min(cursor+dt.timedelta(days=14),limit)
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
          'triggers':{},'commands':[],
          'overlap_trading_days':3,'cores':None,'timer_ms':1000,
          'work_size':'MONTH','capture_start':'keyframe','oz_evaluation':'selected'}
    if defaults:data.update(defaults)
    if path:data.update(json.loads(Path(path).read_text('utf-8-sig')))
    data.update({k:v for k,v in overrides.items() if v is not None})
    if data['mode']=='TIMER':data['mode']='TICK'
    if data['mode'] not in ('BAR','EVENT','TICK'):raise ValueError('mode: BAR / EVENT / TICK')
    if data['work_size'] not in ('MONTH','FORTNIGHT'):raise ValueError('work_size: MONTH / FORTNIGHT')
    if data['capture_start'] not in ('keyframe','beginning'):raise ValueError('capture_start: keyframe / beginning')
    if data['oz_evaluation'] not in ('selected','all'):raise ValueError('oz_evaluation: selected / all')
    if data['symbol'] not in ('XAUUSD+','NAS100','BTCUSD'):raise ValueError('symbol not allowed')
    if milliseconds(data['start'])>=milliseconds(data['end']):raise ValueError('invalid dates (end exclusive)')
    if int(data['timer_ms'])<1 or int(data['overlap_trading_days'])<0:raise ValueError('invalid interval/overlap')
    if isinstance(data['strategies'],str):data['strategies']=[x.strip() for x in data['strategies'].split(',') if x.strip()]
    data['enabled_specials']=[x for x in data['strategies'] if x.startswith('SPECIAL')]
    if data['strategies']==['ALL']:data['enabled_specials']=[f'SPECIAL{i}' for i in range(1,8)]
    for c in data['commands']:
        if not str(c.get('chat_id','')) or not c.get('text'):raise ValueError('command requires chat_id/text')
        if data['strategies']!=['ALL'] and c.get('strategy') not in data['strategies']:raise ValueError('command.strategy must be selected')
    data['warnings']=['월별 독립 상태: 1회성 감시가 청크마다 재등록됩니다.'] if any('계속' not in c['text'] for c in data['commands']) else []
    return data
