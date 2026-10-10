"""Register the SPECIAL3 final-alert-hours change in the Part1 integrity chain."""
from pathlib import Path
import hashlib,json,sys
ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'검증결과/special3_time'
FILES=['program/SPECIAL/SPECIAL3.py','program/special_time_slot.py','program/special_ui.py']
def sha(p):
    with p.open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
def write(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
def main():
    sys.path.insert(0,str(ROOT/'Part1/audit'));from source_integrity import verify_sources
    before=verify_sources();expected=before['final_sha256']
    prior=sorted(set(before['integrity_errors'])-{f'Unrecorded source change: {n}' for n in FILES})
    unit=ROOT/'Part1/audit/remediation/47-special3-time'
    assert not unit.exists(),'already registered'
    write(unit/'changes.json',[{'file':n,'before_sha256':expected[n],'after_sha256':sha(ROOT/'Part1'/n)} for n in FILES])
    write(unit/'review.json',{'scope':'SPECIAL3 final alert hours: OPENING final_time_filters removed; code default 09:00-11:00/16:00-18:00/21:00-24:00 KST; slot accepts 24:00; slot dialog shows/saves strategy-own hours',
         'tests':'tests/test_special3_time.py, tests/test_ui_slots.py',
         'limitations':'setup time_filters, config OPENING_* keys and chain deadline logic unchanged'})
    result=verify_sources();new=sorted(set(result['integrity_errors'])-set(prior));assert not new,new
    write(OUT/'integrity.json',{'unit':unit.relative_to(ROOT).as_posix(),'new_errors':new,'remaining_errors':result['integrity_errors']})
    immutable={p.relative_to(ROOT).as_posix():sha(p) for p in (ROOT/'Part1').rglob('*') if p.is_file() and '__pycache__' not in p.parts and 'results' not in p.parts and p.name!='special_settings.json' and p.suffix!='.ex5'}
    write(ROOT/'build/part1_immutable_sha256.json',dict(sorted(immutable.items())))
    print('registered',unit.name,'remaining',len(result['integrity_errors']))
if __name__=='__main__':main()
