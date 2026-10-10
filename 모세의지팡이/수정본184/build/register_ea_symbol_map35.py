"""Register the EA broker-symbol map change in the Part1 integrity chain."""
from pathlib import Path
import hashlib,json,sys
ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'검증결과/symbol_registry'
FILES=['program/MT5/THE_STAFF_OF_MOSES.mq5','program/MT5/STAFF_Wire_Schema.mqh',
       'program/MT5/STAFF_Symbol_Map.mqh','program/MT5/STAFF_Symbol_Map_Test.mq5']
EMPTY=hashlib.sha256(b'').hexdigest()
def sha(p):
    with p.open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
def write(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
def main():
    sys.path.insert(0,str(ROOT/'Part1/audit'));from source_integrity import verify_sources
    before=verify_sources();expected=before['final_sha256']
    mine={f'Unrecorded source change: {n}' for n in FILES}|{f'Unrecorded new source: {n}' for n in FILES}
    prior=sorted(set(before['integrity_errors'])-mine)
    unit=ROOT/'Part1/audit/remediation/66-ea-broker-symbol-map'
    assert not unit.exists(),'already registered'
    write(unit/'changes.json',[{'file':n,'before_sha256':expected.get(n,EMPTY),'after_sha256':sha(ROOT/'Part1'/n)} for n in FILES])
    write(unit/'review.json',{'scope':'EA logical symbol (Wire identity) vs broker symbol (MT5 data): ResolveBrokerSymbol exact/alias/affix; ambiguous or missing rejects only that symbol; two logical symbols on one broker symbol rejects the later one; tester logical input (empty=_Symbol); OnInit..EOF byte-identical; EA build hash regenerated',
         'tests':'MetaEditor compile 0 errors/0 warnings (EA and STAFF_Symbol_Map_Test.mq5); tests/test_symbol_registry.py',
         'limitations':'STAFF_Symbol_Map_Test.mq5 must be run in the MT5 terminal by the user; no terminal run here'})
    result=verify_sources();new=sorted(set(result['integrity_errors'])-set(prior));assert not new,new
    write(OUT/'integrity_ea.json',{'unit':unit.relative_to(ROOT).as_posix(),'new_errors':new,'remaining_errors':result['integrity_errors']})
    immutable={p.relative_to(ROOT).as_posix():sha(p) for p in (ROOT/'Part1').rglob('*') if p.is_file() and not any(x in p.parts for x in ('__pycache__','logs','event_state','results')) and p.name!='special_settings.json' and p.suffix!='.ex5'}
    write(ROOT/'build/part1_immutable_sha256.json',dict(sorted(immutable.items())))
    print('registered',unit.name,'remaining',len(result['integrity_errors']))
if __name__=='__main__':main()
