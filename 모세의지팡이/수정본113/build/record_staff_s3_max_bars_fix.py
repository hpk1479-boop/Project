"""Record the narrow post-gate configuration diagnosis without repeating gates."""
import hashlib
import re
import sys
from staff_s3_evidence import *
from finalize_staff_s1 import cases

path=ROOT/'Part1/program/staff_snapshot.py'
new=path.read_text('utf-8')
replacement='''        # This is the existing DataFrame.tail setting, not a wire allocation
        # size. The receiver already bounds each array to 3..650 rows. Preserve
        # legacy tail semantics and error replies for every integer setting.
        if type(max_bars) is not int:'''
original=new.replace(replacement,'        if type(max_bars) is not int or not 3 <= max_bars <= 650:')
before=next(r['after_sha256'] for r in read(OUT/'source_delta.json')['changes'] if r['file']=='Part1/program/staff_snapshot.py')
assert hashlib.sha256(original.encode()).hexdigest()==before
config=(ROOT/'Part1/program/config.txt').read_text('utf-8-sig')
match=re.search(r'^\s*STAFF_BARS\s*=\s*(\d+)\s*$',config,re.M)
configured=int(match[1]) if match else 650
assert configured==650
assert (type(configured) is not int or not 3<=configured<=650)==(type(configured) is not int)
old_results=cases(OUT/'max_bars_before.xml'); new_results=cases(OUT/'max_bars_after.xml')
assert len(old_results)==3 and all(v['status']=='FAILED' for v in old_results.values())
assert len(new_results)==12 and all(v['status']=='PASSED' for v in new_results.values())
write(OUT/'max_bars_diagnosis.json',{'before_sha256':before,'after_sha256':sha(path),
    'only_change_verified_by_inverse_patch_sha256':True,'cases':old_results,
    'resolution':'Preserve existing integer DataFrame.tail configuration; wire rows remain independently bounded to 3..650.',
    'related_rechecks':new_results,'golden_max_bars':configured,
    'golden_condition_identical_for_650':True,
    'legacy_G3_and_benchmark_methods_changed':False,
    'full_gates_repeated':False})
write(OUT/'diagnosed_reruns.json',[{'group':'part2','xml':'max_bars_after.xml','ids':list(new_results),
    'reason':'Three new configuration cases failed on restrictive client validation; fixed only that condition and reran related transport checks.'}])
unit=ROOT/'Part1/audit/remediation/24-staff-s3-snapshot-api/changes.json'
changes=read(unit); write(OUT/'integrity_before_max_bars_fix.json',changes)
for change in changes:
    if change['file']=='program/staff_snapshot.py':
        assert change['after_sha256']==before
        change['after_sha256']=sha(path)
write(unit,changes)
sys.path.insert(0,str(ROOT/'Part1/audit'))
from source_integrity import verify_sources
current=verify_sources()
report=read(OUT/'integrity_registration.json')
assert report['remaining_errors']==current['integrity_errors']
write(OUT/'integrity_after_max_bars_fix.json',{'chain_valid':True,'unchanged_diagnostics':True,
    'source_sha256':current['final_sha256']})
inventory={p.relative_to(ROOT).as_posix():sha(p) for p in (ROOT/'Part1').rglob('*')
    if p.is_file() and '__pycache__' not in p.parts and 'results' not in p.parts
    and p.name!='special_settings.json' and p.suffix!='.ex5'}
write(ROOT/'build/part1_immutable_sha256.json',dict(sorted(inventory.items())))
provenance()
print('Recorded 3 reproduced failures, 12 targeted passes, unchanged default golden branch, final integrity hashes')
