"""Register the shared Part1/Part2 strategy settings window in the Part1 integrity chain."""
from pathlib import Path
import hashlib,json,sys
ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'검증결과/part2_settings_ui'
FILES=['program/special_ui.py','program/strategy_settings_view.py']
def sha(p):
    with p.open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
def write(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
def main():
    sys.path.insert(0,str(ROOT/'Part1/audit'));from source_integrity import verify_sources
    before=verify_sources();expected=before['final_sha256']
    prior=sorted(set(before['integrity_errors'])-{f'Unrecorded source change: {n}' for n in FILES})
    unit=ROOT/'Part1/audit/remediation/67-part2-settings-ui'
    assert not unit.exists(),'already registered'
    write(unit/'changes.json',[{'file':n,'before_sha256':expected[n],'after_sha256':sha(ROOT/'Part1'/n)} for n in FILES])
    write(unit/'review.json',{'scope':'Part2 strategy settings uses the same modern window as Part1 (live=False: backtest title/subtitle, no LIVE record-folder button, Part2 footer, new strategies unchecked); old ttk dialog removed',
         'tests':'tests/test_strategy_settings_ui31.py (new Part2 case), tests/test_module_status_navigation.py',
         'limitations':'UI only; settings data and Part2 run inputs unchanged'})
    result=verify_sources();new=sorted(set(result['integrity_errors'])-set(prior));assert not new,new
    write(OUT/'integrity.json',{'unit':unit.relative_to(ROOT).as_posix(),'new_errors':new,'remaining_errors':result['integrity_errors']})
    immutable={p.relative_to(ROOT).as_posix():sha(p) for p in (ROOT/'Part1').rglob('*') if p.is_file() and not any(x in p.parts for x in ('__pycache__','logs','event_state','results')) and p.name!='special_settings.json' and p.suffix!='.ex5'}
    write(ROOT/'build/part1_immutable_sha256.json',dict(sorted(immutable.items())))
    print('registered',unit.name,'remaining',len(result['integrity_errors']))
if __name__=='__main__':main()
