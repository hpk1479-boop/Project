from pathlib import Path
import sys,json,shutil,subprocess,hashlib,csv
R=Path(__file__).resolve().parents[1];out=R/'검증결과/log_restore';before=out/'before29'
if sys.argv[-1]!='worker':
 for rel in ('Part1/program','Part2/event_backtest'):
  shutil.copytree(R.with_name('수정본29')/rel,before/rel,ignore=shutil.ignore_patterns('__pycache__'),dirs_exist_ok=True)
 shutil.copytree(R.with_name('수정본29')/'Part2/generic_backtest',before/'Part2/generic_backtest',ignore=shutil.ignore_patterns('__pycache__'),dirs_exist_ok=True)
 for tag,base in [('before',before),('after',R)]:
  subprocess.run([sys.executable,'-B','-X','utf8',__file__,str(base),tag,'worker'],check=True)
 warehouse=R.parent.with_name(R.parent.name+'_warehouse')
 results={tag:json.loads((warehouse/'runs'/('trace30_'+tag)/'comparison.json').read_text('utf8')) for tag in ('before','after')}
 (out/'week_comparison.json').write_text(json.dumps(results,indent=2,ensure_ascii=False),encoding='utf8')
 print(json.dumps(results,ensure_ascii=False));raise SystemExit
base=Path(sys.argv[1]);tag=sys.argv[2];sys.path.insert(0,str(base/'Part2'));sys.path.insert(0,str(base/'Part1/program'))
from event_backtest.settings import scenario
from event_backtest.runner import run_chunk,runtime_config
import duckdb
warehouse=R.parent.with_name(R.parent.name+'_warehouse')
db=duckdb.connect(str(warehouse/'captures.duckdb'),read_only=True)
rows=[json.loads(r[0]) for r in db.execute("SELECT metadata FROM captures WHERE symbol='XAUUSD+' AND mode='BAR' ORDER BY recorded_at DESC").fetchall()];db.close()
import staff_schema
pieces=[]
for a,b in [('2025-08-01','2025-09-01'),('2025-09-01','2025-10-01')]:
 c=next(c for c in rows if c['start']==a and c['end']==b and c['schema_id']==staff_schema.WIRE_SCHEMA_ID)
 pieces.append(c)
s=scenario(symbol='XAUUSD+',start='2025-09-01',end='2025-09-08',mode='BAR',strategies=['SPECIAL1'])
folder=warehouse/'runs'/('trace30_'+tag)
task=dict(scenario=s,config=runtime_config(s),start=s['start'],end=s['end'],warm_start='2025-08-27',captures=pieces,run_id='trace30_'+tag,out=str(folder),warehouse=str(warehouse))
result=run_chunk(task)
# run_chunk applies a filesystem write guard: keep evidence in the authorized run.
with (folder/'alerts.csv').open(encoding='utf8',newline='') as f:
 alerts=[{k:v for k,v in row.items() if k!='run_id'} for row in csv.DictReader(f)]
result['alerts_digest']=hashlib.sha256(json.dumps(alerts,ensure_ascii=False,sort_keys=True).encode()).hexdigest();result['alert_count']=len(alerts)
# Disable the worker-only write guard by using parent to copy the persisted metrics later.
(folder/'comparison.json').write_text(json.dumps(result,indent=2,ensure_ascii=False),encoding='utf8')
