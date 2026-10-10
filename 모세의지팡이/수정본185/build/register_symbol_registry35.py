"""Register the EA-owned symbol registry change in the Part1 integrity chain."""
from pathlib import Path
import hashlib,json,sys
ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'검증결과/symbol_registry'
FILES=['program/event_pipe_host.py','program/event_host.py','program/event_application.py','program/config.txt']
def sha(p):
    with p.open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
def write(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
def main():
    sys.path.insert(0,str(ROOT/'Part1/audit'));from source_integrity import verify_sources
    before=verify_sources();expected=before['final_sha256']
    prior=sorted(set(before['integrity_errors'])-{f'Unrecorded source change: {n}' for n in FILES})
    unit=ROOT/'Part1/audit/remediation/65-ea-symbol-registry'
    assert not unit.exists(),'already registered'
    write(unit/'changes.json',[{'file':n,'before_sha256':expected[n],'after_sha256':sha(ROOT/'Part1'/n)} for n in FILES])
    write(unit/'review.json',{'scope':'LIVE input no longer limited by config symbol lists: pipe accepts well-formed EA symbols, registry from validated cache keys, engine subscription wildcard, host symbols = config hint + EA symbols; config.txt comment only',
         'tests':'tests/test_symbol_registry.py, tests/test_event_e3.py, synthetic240 LIVE(no lists) = replay',
         'limitations':'MT5 EA broker mapping and Part2 tester symbol are later stages; composer command symbols unchanged (config+SPECIAL+manual)'})
    result=verify_sources();new=sorted(set(result['integrity_errors'])-set(prior));assert not new,new
    write(OUT/'integrity.json',{'unit':unit.relative_to(ROOT).as_posix(),'new_errors':new,'remaining_errors':result['integrity_errors']})
    immutable={p.relative_to(ROOT).as_posix():sha(p) for p in (ROOT/'Part1').rglob('*') if p.is_file() and not any(x in p.parts for x in ('__pycache__','logs','event_state','results')) and p.name!='special_settings.json' and p.suffix!='.ex5'}
    write(ROOT/'build/part1_immutable_sha256.json',dict(sorted(immutable.items())))
    print('registered',unit.name,'remaining',len(result['integrity_errors']))
if __name__=='__main__':main()
