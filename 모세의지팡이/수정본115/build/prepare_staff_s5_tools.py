from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
BUILD=ROOT/'build'
def generate(old,new,changes):
    text=(BUILD/old).read_text('utf-8')
    for a,b in changes:text=text.replace(a,b)
    (BUILD/new).write_text(text,encoding='utf-8')
common=[('staff_s4_evidence','staff_s5_evidence'),('staff_s4','staff_s5'),('s3_frozen_manifest','s4_frozen_manifest')]
generate('staff_s4_evidence.py','staff_s5_evidence.py',[
    ("SOURCE=ROOT.parent/'수정본10'","SOURCE=ROOT.parent/'수정본11'"),
    ("OUT=ROOT/'검증결과/staff_s4'","OUT=ROOT/'검증결과/staff_s5'"),
    ('s3_frozen_manifest','s4_frozen_manifest'),("S3=ROOT/'검증결과/staff_s3'","S3=ROOT/'검증결과/staff_s3'\nS4=ROOT/'검증결과/staff_s4'"),
    ("'검증결과/staff_s3/',","'검증결과/staff_s3/','검증결과/staff_s4/',"),
    ("'Part1/watch_ma_validation/test_live_integration.py'", "'Part1/watch_ma_validation/test_live_integration.py',\n                'Part1/audit/test_ack_pressure.py','Part1/audit/test_slow_feed.py',\n                'Part1/audit/test_oz_trigger_profiles.py',\n                'Part2/validation_suite/test_staff_s2.py','Part2/validation_suite/test_staff_s4.py'"),
    ("'stage':'S4'","'stage':'S5'")])
generate('verify_staff_s4_actual_oz.py','verify_staff_s5_actual_oz.py',common)
generate('inventory_part3_legacy_s4.py','inventory_part3_legacy_s5.py',common)
generate('run_staff_s4_gates.py','run_staff_s5_gates.py',common+[
    ("STAFF_STAGE='S4'","STAFF_STAGE='S5',STAFF_PERFORMANCE_RESULTS=str(OUT/'performance')"),
    ("str(ROOT/'build/record_staff_s4_dual.py')","'-m','staff_golden'"),
    ("str(ROOT/'build/record_staff_s5_dual.py')","'-m','staff_golden'"),
    ("choices=['G1','G2','S4a','S4b','S4c','S4d','reference']","choices=['G1','G2','S5']"),
    ("args.gate.startswith('S4')","args.gate=='S5'")])
# Record all pre-removal legacy/data dependencies from the complete S4 tree.
import ast,json
out=ROOT/'검증결과/staff_s5'
rows=[]
needles=('_staff_handle','server.handle','cache.get','send_pyobj','apply_requested_features','_legacy_frame')
for part in ('Part1','Part2'):
 for path in (ROOT.parent/'수정본11'/part).rglob('*.py'):
  if any(x in {'__pycache__','results','logs','.pytest_cache','generic_runs'} or x.startswith(('.venv','tmp_')) for x in path.parts):continue
  text=path.read_text('utf-8-sig')
  for n,line in enumerate(text.splitlines(),1):
   if any(word in line for word in needles):
    rel=path.relative_to(ROOT.parent/'수정본11').as_posix()
    rows.append({'file':rel,'line':n,'text':line.strip(),'category':'frozen fixture' if '/fixtures/' in rel else 'test' if path.name.startswith('test_') else 'source/helper'})
(out/'legacy_callers_before.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding='utf-8')
print('Prepared S5 evidence tools;',len(rows),'pre-removal search hits')
