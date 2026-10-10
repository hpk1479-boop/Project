"""The single public backtest entry: propose, approve, build, verify, replay."""
from .warehouse import Warehouse
from .build_plan import make_plan,require_approval
from .history_check import require_complete
from pathlib import Path
import json,uuid
from .cancellation import Cancelled
from .warehouse_cleanup import cleanup_warehouse,warehouse_activity

def build_scenario(s):
    return {**s,'overlap_trading_days':0} if s.get('build_only') else s

def proposal(s,warehouse,*,rebuild=False,verify=True,checked=None,cleanup=True):
    """The recording plan (build_plan.make_plan: verify, checked).

    cleanup=False when the caller has just cleaned the warehouse, or runs for a Part3 job: that
    job's own live record keeps every cleanup busy until it ends (수정본170)."""
    if cleanup:cleanup_warehouse(warehouse)
    with warehouse_activity(warehouse):
        catalog=Warehouse(warehouse)
        try:return make_plan(build_scenario(s),catalog,rebuild=rebuild,verify=verify,checked=checked)
        finally:catalog.close()

def _backtest_priority():
    # While LIVE runs on this PC, this run and its worker processes take CPU after LIVE (and MT5);
    # otherwise they run at normal priority. The runner follows LIVE starting or stopping (수정본167).
    import sys
    from .settings import PROGRAM
    if str(PROGRAM) not in sys.path:sys.path.insert(0,str(PROGRAM))
    from process_priority import backtest_priority
    return backtest_priority()

def execute(s,warehouse,*,yes=False,approved_token=None,rebuild=False,profile=None,
            cores=None,sequential=False,emit=lambda *a:None,cancel=lambda:None,cleanup=True,max_lanes=None):
    _backtest_priority()
    if cleanup:cleanup_warehouse(warehouse,emit=emit)
    with warehouse_activity(warehouse):
        return _execute(s,warehouse,yes=yes,approved_token=approved_token,rebuild=rebuild,
                        profile=profile,cores=cores,sequential=sequential,emit=emit,cancel=cancel,max_lanes=max_lanes)

LANES_FILE='lanes.json'

def run_groups(s,scenarios,warehouse,*,cores=None,sequential=False,captures=None,emit=lambda *a:None,
               cancel=lambda:None,max_lanes=None):
    """The runs of one request replayed in groups of at most max_lanes, one group after another (수정본184).

    Every run has its run ID before the first group starts; the first is the request's own. A lanes request
    records them all in the first run's lanes.json (its run IDs, strategies, triggers and groups), with each
    group's state as it ends. A stop asked for the request ends the running group and starts no other.
    """
    from .runner import run_many
    from .settings import lane_limit
    from .cancellation import Cancelled,requested
    limit=lane_limit(max_lanes)
    for index,item in enumerate(scenarios):
        if not item.get('_run_id'):item['_run_id']=uuid.uuid4().hex
    ids=[item['_run_id'] for item in scenarios]
    if len(set(ids))!=len(ids):raise ValueError('함께 실행하는 실행의 ID가 겹칩니다.')
    groups=[scenarios[i:i+limit] for i in range(0,len(scenarios),limit)]
    lead=ids[0]
    record=None
    if s.get('lanes'):
        record={'lead_run_id':lead,'max_lanes':limit,'groups':len(groups),'group_times':[],
                'runs':[{'run_id':item['_run_id'],'strategy':item['strategies'][0],
                         'trigger':lane.get('trigger'),'group':index//limit+1,'status':'WAITING'}
                        for index,(item,lane) in enumerate(zip(scenarios,s['lanes']))]}
    def save():
        if record is None:return
        folder=Path(warehouse)/'runs'/lead;folder.mkdir(parents=True,exist_ok=True)
        (folder/LANES_FILE).write_text(json.dumps(record,ensure_ascii=False,indent=1),encoding='utf-8')
    save()
    results=[]
    import time
    for number,group in enumerate(groups,1):
        if number>1 and requested(cancel):break
        if len(groups)>1:emit('LANE_GROUP',{'group':number,'groups':len(groups),'runs':len(group),'total_runs':len(ids)})
        if record is not None:
            # Each group's start and end (seconds since the epoch), so a status can tell the time left.
            record['group_times'].append({'group':number,'started':time.time(),'ended':None});save()
        try:
            done=run_many(group,warehouse,cores=cores,sequential=sequential,captures=captures,verified=True,
                          emit=emit,cancel=cancel,lead=lead if len(groups)>1 else None,group=ids if len(groups)>1 else None)
        except Cancelled:
            if not results:raise
            break
        results.extend(done)
        if record is not None:
            states={item['run_id']:item.get('status') for item in done}
            for row in record['runs']:
                if row['run_id'] in states:row['status']=states[row['run_id']] or 'COMPLETE'
            record['group_times'][-1]['ended']=time.time()
            save()
        if any(item.get('status')=='CANCELLED' for item in done):break
    return results

def _execute(s,warehouse,*,yes=False,approved_token=None,rebuild=False,profile=None,
             cores=None,sequential=False,emit=lambda *a:None,cancel=lambda:None,max_lanes=None):
    from .runner import runtime_config
    from .recording import prepare
    s=build_scenario(s);config=runtime_config(s)
    from event_selection import resolve
    if not s.get('build_only'):resolve(s['strategies'],config)
    # Each stored recording is hashed once, here, before it is replayed; prepare's plan and the
    # runner use this result (수정본170). execute has cleaned the warehouse already.
    checked={}
    plan=proposal(s,warehouse,rebuild=rebuild,checked=checked,cleanup=False);emit('BUILD_PLAN',plan)
    if s.get('result_mode')=='VIRTUAL_ENTRY' and not s.get('build_only'):
        from .virtual_entry import pricing
        pricing(s,plan.get('reuse',[]),config)
    token=plan['approval_token'] if yes else approved_token
    require_approval(plan,token)
    completed=[];current=[None]
    def progress(kind,data):
        if kind=='CAPTURE_START':current[0]=data
        if kind in ('CAPTURE_COMPLETE','CAPTURE_CONVERTED'):completed.append(data)
        emit(kind,data)
    def save_build(captures,error=None):
        from .settings import relative_path
        from .history_check import tick_warning
        run_id=s.get('_run_id') or uuid.uuid4().hex
        if len(run_id)!=32 or any(c not in '0123456789abcdef' for c in run_id):raise ValueError('invalid run ID')
        out=Path(warehouse)/'runs'/run_id;out.mkdir(parents=True,exist_ok=True)
        reused={c['capture_id'] for c in plan.get('reuse',[])}
        rows=[{'symbol':c['symbol'],'start':c['start'],'end':c['end'],'path':c['path'],
               'status':'실패' if c.get('history_missing') else '기존' if c['capture_id'] in reused else '새로 구축'} for c in captures]
        if error and current[0] and not any(r['start']==current[0]['start'] and r['end']==current[0]['end'] for r in rows):rows.append({k:current[0].get(k,'') for k in ('symbol','start','end')}|{'status':'중단' if isinstance(error,Cancelled) else '실패'})
        data={'run_id':run_id,'build_only':True,'result_mode':s.get('result_mode','ALERT_ONLY'),'scenario':s,
              'pieces':rows,'backtest_executed':False,'status':'CANCELLED' if isinstance(error,Cancelled) else 'FAILED' if error else 'COMPLETE',
              'status_label':'중단됨(부분 결과)' if isinstance(error,Cancelled) else '실패' if error else '완료',
              'processed_periods':[{'start':r['start'],'end':r['end']} for r in rows if r['status'] not in ('실패','중단')],
              'warnings':[w for c in captures if (w:=tick_warning(c,s['mode']))]+([plan['period_adjustment']['message']] if plan.get('period_adjustment') else []),
              'period_adjustment':plan.get('period_adjustment'),'excluded_periods':plan.get('excluded_periods',[]),
              'result_path':relative_path(warehouse,out/'result.json'),'progress_log':relative_path(warehouse,out/'progress.jsonl')}
        if error:data['error']=type(error).__name__
        (out/'result.json').write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
        return data
    try:
        cancel()
        captures=prepare(s,warehouse,profile,plan=plan,approved_token=token,rebuild=rebuild,emit=progress,cancel=cancel,
                         checked=checked)
        from .periods import apply_plan
        s=apply_plan(s,plan)
        cancel()
        require_complete(captures)
    except Cancelled as exc:
        return save_build([*plan.get('reuse',[]),*completed],exc)
    except Exception as exc:
        if s.get('build_only'):save_build([*plan.get('reuse',[]),*completed],exc)
        raise
    emit('BUILD_VERIFIED',{'pieces':len(captures),'strategies':s['strategies']})
    if s.get('build_only'):return save_build(captures)
    from .runner import run
    from .settings import variant_scenarios
    scenarios=variant_scenarios(s)
    # prepare returns recordings hashed above or recorded and verified in this run: not hashed again.
    if len(scenarios)==1 and not s.get('lanes'):
        result=run(s,warehouse,cores=cores,sequential=sequential,captures=captures,verified=True,emit=emit,cancel=cancel)
        results=[result]
    else:
        # Several base frames, OZ triggers or lanes: one run each, replayed together (one recording reading
        # for all), in groups of at most max_lanes one after another (수정본184).
        results=run_groups(s,scenarios,warehouse,cores=cores,sequential=sequential,captures=captures,
                           emit=emit,cancel=cancel,max_lanes=max_lanes)
    # The result screen computes its analysis from the run's trades when it opens (result_analysis,
    # 수정본165), so no analytics.json is written after the run (수정본170).
    return results[0]
