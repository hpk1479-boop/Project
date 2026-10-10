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
    # Runs replayed together at most (lanes, 수정본184): more are replayed in several groups one after another.
    data.setdefault('max_lanes',MAX_LANES)
    return data

# About 300MB per worker process for 36 lanes (수정본183 analysis); more lanes are split into groups.
MAX_LANES=36

def lane_limit(value=None,config=None):
    """The most runs one replay takes together: the given number, else this PC's setting."""
    value=value if value is not None else (config if config is not None else settings()).get('max_lanes',MAX_LANES)
    if type(value) is not int or not 1<=value<=200:raise ValueError('한 번에 함께 재생할 실행 수(max_lanes)는 1~200 정수입니다.')
    return value

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
          'triggers':{},'commands':[],'result_mode':'ALERT_ONLY','spread_points':{},'commission':{},'build_only':False,'available_only':False,
          'overlap_trading_days':3,'cores':None,'timer_ms':1000,
          'capture_start':'keyframe','oz_evaluation':'selected','virtual_bases':[],'trigger_variants':[],
          'lanes':[],'virtual_entries':{}}
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
    if data.get('lanes'):
        # Several strategies (each with its OZ trigger) replayed together: one complete run per lane (수정본184).
        _lane_scenario(data)
    elif data.get('virtual_entries'):raise ValueError('virtual_entries는 lanes와 함께만 씁니다.')
    else:data['lanes']=[];data['virtual_entries']={}
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
    if data.get('trigger_variants'):
        # Several OZ triggers compared in one replay: one run per trigger (variant_scenarios, 수정본171).
        if data['build_only']:raise ValueError('데이터 구축에는 트리거 비교를 쓸 수 없습니다.')
        if data['virtual_bases']:raise ValueError('기준 프레임 비교와 트리거 비교는 함께 할 수 없습니다.')
        data['trigger_variants']=target_triggers(data['strategies'],data['commands'],data['trigger_variants'])
        # The first is the run's own trigger.
        data['triggers']=trigger_settings(data['triggers'],data['strategies'][0],data['trigger_variants'][0])
    else:data['trigger_variants']=[]
    for c in data['commands']:
        if not str(c.get('chat_id','')) or not c.get('text'):raise ValueError('command requires chat_id/text')
        if data['strategies']!=['ALL'] and c.get('strategy') not in data['strategies']:raise ValueError('command.strategy must be selected')
    data['warnings']=[COMMAND_CONTINUOUS_REASON] if data['commands'] else []
    return data


def target_triggers(strategies,commands,triggers):
    """The OZ triggers of the one selected strategy to compare in one replay (수정본171).

    At least two distinct ones of the four, in the order asked: the first is the run's own.
    """
    import oz_profiles
    from strategy_recipe.registry import default_settings
    if commands or len(strategies)!=1 or strategies[0] in ('ALL','WATCH'):
        raise ValueError('트리거 비교는 전략 1개를 골랐을 때만 됩니다.')
    if not isinstance(triggers,(list,tuple)) or any(not isinstance(text,str) for text in triggers):
        raise ValueError('비교할 트리거 목록 형식이 올바르지 않습니다.')
    if default_settings(strategies[0])[0] is None:
        raise ValueError('이 전략은 OZ 트리거가 없어 트리거를 비교할 수 없습니다.')
    canonical=[]
    for text in triggers:
        try:canonical.append(oz_profiles.canonical_profile_text(text))
        except ValueError as exc:raise ValueError(f'비교할 트리거 {text!r}: {exc}') from None
    if len(canonical)<2 or len(set(canonical))!=len(canonical):
        raise ValueError('비교할 트리거는 서로 다른 두 개 이상이어야 합니다.')
    return canonical


def trigger_settings(triggers,name,text):
    """The scenario's triggers with `text` for `name`. The strategy's default is left unset, as the screen
    sends it, so the run is the same scenario as that trigger's run alone."""
    from strategy_recipe.registry import default_settings
    value={key:item for key,item in triggers.items() if key!=name}
    if text!=default_settings(name)[0]:value[name]=text
    return value


LANE_KEYS=('strategy','trigger')


def lane_triggers(lane):
    """A lane's triggers as its run alone has them: its trigger, the strategy's own left unset."""
    return {} if lane.get('trigger') is None else trigger_settings({},lane['strategy'],lane['trigger'])


def _lane_scenario(data):
    """Check a lanes request and make its first lane the scenario's own run (수정본184).

    lanes: [{"strategy": name, "trigger": OZ trigger (optional)}], each lane a complete run of its own, as if run
    alone. A lane without a trigger uses the strategy's own. virtual_entries: {strategy: policy} for a virtual
    entry; a strategy left out uses its recipe's values. Messages say what to do next, for the AI writing them.
    """
    import oz_profiles
    from strategy_recipe.registry import default_settings
    lanes=data['lanes']
    if not isinstance(lanes,(list,tuple)):raise ValueError('lanes는 [{"strategy": 전략, "trigger": 트리거}] 목록이어야 합니다.')
    if data['build_only']:raise ValueError('데이터 구축에는 lanes를 쓸 수 없습니다.')
    if data.get('commands'):raise ValueError('감시 명령(WATCH)은 lanes로 묶을 수 없습니다. 명령마다 따로 실행하세요.')
    if data.get('virtual_bases'):raise ValueError('lanes와 기준 프레임 비교(virtual_bases)는 함께 쓸 수 없습니다.')
    if data.get('trigger_variants'):
        raise ValueError('lanes와 trigger_variants는 함께 쓸 수 없습니다. 트리거는 lanes 항목마다 적으세요.')
    if data.get('virtual_strategy') is not None:
        raise ValueError('lanes에서는 전략 조건을 바꿀 수 없습니다. 바꾼 조건은 Part3 시험 전략으로 만들어 lanes에 넣으세요.')
    checked=[];seen=set()
    for number,lane in enumerate(lanes,1):
        if not isinstance(lane,dict) or set(lane)-set(LANE_KEYS) or not isinstance(lane.get('strategy'),str):
            raise ValueError(f'lanes {number}번째 항목은 {{"strategy": 전략 이름, "trigger": 트리거(선택)}} 형식이어야 합니다.')
        name=lane['strategy'].strip()
        if name in ('','ALL','WATCH'):raise ValueError(f'lanes {number}번째 전략 이름을 확인하세요(ALL·WATCH는 쓸 수 없습니다).')
        try:own=default_settings(name)[0]
        except ValueError:
            raise ValueError(f'lanes {number}번째 전략 {name!r}을 찾지 못했습니다. 전략 이름(예: SPECIAL8, Test_SPECIAL005)을 확인하세요.') from None
        trigger=lane.get('trigger')
        if trigger is not None:
            if not isinstance(trigger,str):raise ValueError(f'lanes {number}번째 trigger는 트리거 이름 글자여야 합니다.')
            if own is None:raise ValueError(f'lanes {number}번째 전략 {name}은 OZ 트리거가 없어 trigger를 넣을 수 없습니다. trigger를 빼세요.')
            try:trigger=oz_profiles.canonical_profile_text(trigger)
            except ValueError as exc:raise ValueError(f'lanes {number}번째 트리거 {lane["trigger"]!r}: {exc}') from None
        if (name,trigger or own) in seen:
            raise ValueError(f'lanes {number}번째 항목({name}, {trigger or own or "트리거 없음"})이 앞의 항목과 같습니다. 하나만 남기세요.')
        seen.add((name,trigger or own))
        checked.append({'strategy':name,'trigger':trigger})
    lead=checked[0]['strategy']
    given=data.get('strategies') or []
    if isinstance(given,str):given=[x.strip() for x in given.split(',') if x.strip()]
    if list(given) not in ([],[lead]):
        raise ValueError('lanes를 쓸 때 strategies는 비워 두세요. 전략은 lanes에만 적고, 첫 번째 lane이 이 실행의 전략입니다.')
    data['lanes']=checked;data['strategies']=[lead];data['triggers']=lane_triggers(checked[0])
    entries=data.get('virtual_entries') or {}
    if not isinstance(entries,dict):raise ValueError('virtual_entries는 {전략 이름: 가상진입 정책} 형식이어야 합니다.')
    names=list(dict.fromkeys(lane['strategy'] for lane in checked))
    unknown=sorted(set(entries)-set(names))
    if unknown:raise ValueError('virtual_entries에 lanes에 없는 전략이 있습니다: '+', '.join(unknown))
    if data['result_mode']!='VIRTUAL_ENTRY':
        if entries:raise ValueError('virtual_entries는 가상 진입(VIRTUAL_ENTRY) 백테스트에서만 씁니다.')
        data['virtual_entries']={}
        return
    from .virtual_defaults import target_policy
    policies={name:target_policy([name],(),entries.get(name)) for name in names}
    if data.get('virtual_entry') is not None and target_policy([lead],(),data['virtual_entry'])!=policies[lead]:
        raise ValueError('lanes에서는 가상진입을 virtual_entry 하나가 아니라 virtual_entries에 전략별로 적으세요.')
    data['virtual_entries']=policies;data['virtual_entry']=policies[lead]


def lane_runs(s):
    """Each lane's complete run: the scenario of that strategy (and trigger, and its virtual entry) alone."""
    entries=s.get('virtual_entries') or {}
    runs=[]
    for index,lane in enumerate(s['lanes']):
        name=lane['strategy']
        values={key:value for key,value in s.items() if key!='_run_id'}
        values.update(strategies=[name],triggers=lane_triggers(lane),lanes=[],virtual_entries={},
                      virtual_entry=entries.get(name))
        if 'special_time_filters' in s:
            # Only its own trading time, as its run alone has it.
            values['special_time_filters']={key:item for key,item in s['special_time_filters'].items() if key==name}
        run=scenario(**values)
        run['_run_id']=s.get('_run_id') if index==0 else None
        runs.append(run)
    return runs


def variant_scenarios(s):
    """The runs a scenario asks for: itself, or one complete run per base frame in virtual_bases, per
    OZ trigger in trigger_variants (수정본171) or per lane in lanes (수정본184).

    Each base frame's run is the scenario of a virtual entry on that frame alone (the recipe's own
    values there), each trigger's run the scenario with that trigger alone, each lane's run the scenario
    of that strategy alone, so it equals and can stand in for that run; the first keeps the scenario's run ID.
    """
    if s.get('lanes'):return lane_runs(s)
    if s.get('trigger_variants'):
        name=s['strategies'][0]
        return [{**s,'triggers':trigger_settings(s['triggers'],name,text),'trigger_variants':[],
                 '_run_id':s.get('_run_id') if index==0 else None} for index,text in enumerate(s['trigger_variants'])]
    if not s.get('virtual_bases'):return [s]
    from .virtual_defaults import base_variants
    return [{**s,**variant,'virtual_bases':[],'_run_id':s.get('_run_id') if index==0 else None}
            for index,variant in enumerate(base_variants(s['strategies'],s['commands'],s['virtual_bases']))]
