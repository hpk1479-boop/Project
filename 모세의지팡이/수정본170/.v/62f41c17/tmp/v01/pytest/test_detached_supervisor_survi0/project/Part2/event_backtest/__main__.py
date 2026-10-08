import argparse,json,time
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('action');p.add_argument('--scenario');p.add_argument('--warehouse');p.add_argument('--session-id');p.add_argument('--skip-cleanup',action='store_true');p.add_argument('--approved-token');p.add_argument('--rebuild',action='store_true');a=p.parse_args()
s=json.loads(Path(a.scenario).read_text());f=Path(a.warehouse)/'runs'/a.session_id
def emit(kind,**kw):print(json.dumps({'event':kind,**kw}),flush=True)
if a.action=='plan':
 emit('COMPLETE',result={'record':[{'start':s['start'],'end':s['end']}] if s.get('record') else [],'convert':[],'estimate':{},'approval_token':'approved'})
else:
 if s.get('record') and a.approved_token!='approved':raise SystemExit(2)
 emit('RUN_START',percent=0);emit('RUN_PROGRESS',percent=25);(f/'worker.started').write_text('started')
 if s.get('hold'):
  end=time.monotonic()+10
  while not (f/'release').exists() and not (f/'stop.request').exists() and time.monotonic()<end:time.sleep(.01)
 cancelled=(f/'stop.request').exists();result={'run_id':a.session_id,'status':'CANCELLED' if cancelled else 'COMPLETE','scenario':s,'result_path':'runs/'+a.session_id+'/result.json'}
 (f/'result.json').write_text(json.dumps(result));emit('COMPLETE',result=result)
