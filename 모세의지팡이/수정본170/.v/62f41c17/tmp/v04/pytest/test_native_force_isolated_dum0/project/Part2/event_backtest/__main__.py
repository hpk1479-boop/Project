import argparse,json,subprocess,sys,time
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('action');p.add_argument('--scenario');p.add_argument('--warehouse');p.add_argument('--session-id');p.add_argument('--skip-cleanup',action='store_true');a=p.parse_args();f=Path(a.warehouse)/'runs'/a.session_id
if a.action=='plan':
 print(json.dumps({'event':'COMPLETE','result':{'record':[],'convert':[],'estimate':{},'approval_token':'approved'}}),flush=True)
else:
 child=subprocess.Popen([sys.executable,'-B','-c','import time;time.sleep(15)'])
 (f/'dummy.child').write_text(str(child.pid));(f/'worker.started').write_text('started')
 print(json.dumps({'event':'RUN_START'}),flush=True)
 time.sleep(15)
