"""Finalize local evidence only; no terminal or network access."""
from pathlib import Path
import ast,csv,hashlib,json,sys,difflib
R=Path(__file__).resolve().parents[1];OLD=R.with_name('수정본29')
O=R/'검증결과/virtual_entry';A=R/'검증결과/log_restore'
W=R.parent.with_name(R.parent.name+'_warehouse')
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def read(p):return json.loads(p.read_text('utf-8-sig'))
def save(p,v):p.write_text(json.dumps(v,ensure_ascii=False,indent=2),encoding='utf8')
manifest=read(A/'source29_sha256.json')
old_changes=[n for n,h in manifest.items() if sha(OLD/n)!=h]
assert not old_changes,old_changes
live=read(A/'behavior/30_synthetic240_live_final/result.json')
replay=read(A/'behavior/30_synthetic240_replay_final/result.json')
assert all(live[k]==replay[k] for k in ('signal_sha256','alerts','deliveries','errors'))
week=read(O/'week_comparison.json')
def alerts(run):
 with (W/'runs'/run/'alerts.csv').open(encoding='utf-8-sig',newline='') as f:
  return [{k:v for k,v in row.items() if k not in ('run_id','b0_price','b0_time')} for row in csv.DictReader(f)]
assert alerts(week['runs']['ALERT_ONLY'])==alerts(week['runs']['VIRTUAL_ENTRY'])
canonical=lambda rows:sorted(rows,key=lambda row:(row['time_ms'],row['signal_id'],row['recipient']))
assert canonical(alerts('trace30_after'))==canonical(alerts(week['runs']['ALERT_ONLY']))
virtual=read(O/'VIRTUAL_ENTRY.json')['virtual_entry']
build=read(W/'runs/0b9b93403a354f158729dab5aff88510/result.json')
assert build['build_only'] and not build['backtest_executed']
journal=[json.loads(x) for x in (W/build['progress_log']).read_text('utf8').splitlines()]
assert journal[-1]['event']=='COMPLETE'
config_diff=''.join(difflib.unified_diff((OLD/'Part1/program/config.txt').read_text('utf-8-sig').splitlines(True),(R/'Part1/program/config.txt').read_text('utf-8-sig').splitlines(True)))
assert '+POINT_XAUUSD+=0.01' in config_diff
unchanged=[]
for p in (OLD/'Part1').rglob('*'):
 if p.is_file() and (p.suffix in ('.mq5','.mqh') or p.name in ('staff_schema.py','staff_wire.py')):
  name=p.relative_to(OLD);assert sha(p)==sha(R/name),str(name);unchanged.append(name.as_posix())
files=[]
for folder in ('Part1/program','Part2/event_backtest','tests','build'):
 for p in (R/folder).rglob('*'):
  if not p.is_file() or p.suffix not in ('.py','.pyw','.txt','.json') or '__pycache__' in p.parts:continue
  n=p.relative_to(R).as_posix()
  if not (OLD/n).is_file() or sha(p)!=sha(OLD/n):
   files.append(n)
   if p.suffix in ('.py','.pyw'):ast.parse(p.read_text('utf-8-sig'),filename=n)
files.append('Part1/OZ_SYSTEM CONTROL.pyw')
sys.path.insert(0,str(R/'Part1/audit'));from source_integrity import verify_sources
initial=verify_sources();expected=initial['final_sha256'];empty=hashlib.sha256(b'').hexdigest()
groups={
 '49-module-status-final':['program/module_diagnostics.py','program/event_engine/engine.py'],
 '50-virtual-entry-metadata':['program/oz_engine/profile.py','program/oz_engine/controllers.py','program/config.txt']}
for unit,names in groups.items():
 rows=[dict(file=n,before_sha256=expected.get(n,empty),after_sha256=sha(R/'Part1'/n)) for n in names if expected.get(n)!=sha(R/'Part1'/n)]
 path=R/'Part1/audit/remediation'/unit
 if path.exists():
  assert not rows,'already registered unit modified'
  continue
 path.mkdir();save(path/'changes.json',rows)
 save(path/'review.json',{'scope':unit,'strategy_judgment_changed':False,'evidence':'검증결과/virtual_entry','note':'A component status isolation; B existing B0 metadata forwarding and user-confirmed point only'})
 expected=verify_sources()['final_sha256']
final=verify_sources();prior=read(A/'integrity.json')['remaining_errors']
assert sorted(final['integrity_errors'])==sorted(prior),final['integrity_errors']
save(O/'integrity.json',{'remaining_errors':final['integrity_errors'],'new_errors':[],'units':list(groups)})
save(R/'build/part1_immutable_sha256.json',{p.relative_to(R).as_posix():sha(p) for p in (R/'Part1').rglob('*') if p.is_file() and not set(p.parts)&{'logs','__pycache__','results'} and p.suffix!='.ex5' and p.name!='special_settings.json'})
save(O/'final_checks.json',{'previous_sources_checked':len(manifest),'previous_changed':old_changes,'synthetic':{'bundles':live['bundles'],'signals':live['signals'],'alerts':len(live['alerts']),'live_replay_equal':True},'week_A_B_alerts_equal':True,'week_alerts':len(alerts('trace30_after')),'config_diff':config_diff,'ea_wire_schema_unchanged':unchanged,'cli_build_only':{'run_id':build['run_id'],'pieces':build['pieces'],'backtest_executed':False,'journal_events':len(journal)},'virtual_entry':virtual})
save(O/'changed_files.json',sorted(set(files)))
save(A/'status.json',{'A':'COMPLETE','B':'COMPLETE','alerts_equal':True,'new_logic_failure':0,'known_fact_test_failure':1})
save(O/'status.json',{'status':'COMPLETE','point':0.01,'point_source':'user confirmed config','actual_telegram_sent':False,'actual_mt5_recording':False,'week_alerts_equal':True,'virtual_summary_rows':len(virtual['summary']),'new_integrity_errors':0,'known_integrity_diagnostics':len(prior)})
print(json.dumps({'old_checked':len(manifest),'changed_count':len(files),'summary':virtual['summary'][0],'cli_run':build['run_id'],'integrity':final['integrity_errors']},ensure_ascii=False))
