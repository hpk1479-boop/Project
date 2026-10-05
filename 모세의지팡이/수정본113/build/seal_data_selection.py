"""Register only reviewed Part1 changes; never traverse Part3 or old logs."""
from pathlib import Path
import argparse,hashlib,json,os,sys
ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'검증결과/data_selection'
def sha(p):
    with p.open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
def write(p,data):p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
def inventory(root,folder):
    result={}
    for base,dirs,names in os.walk(root/folder):
        dirs[:]=[x for x in dirs if x not in ('logs','__pycache__','.pytest_cache','.venv-generic','generic_runs')]
        for name in names:
            p=Path(base)/name
            if p.suffix in ('.py','.pyw','.mq5','.mqh','.ex5','.json','.txt','.md','.cmd'):
                result[p.relative_to(root).as_posix()]=sha(p)
    return result
def main():
    parser=argparse.ArgumentParser();parser.add_argument('--previous',type=Path,required=True);a=parser.parse_args();previous=a.previous.resolve()
    old={};new={}
    for folder in ('Part1/program','Part2/event_backtest','Part2/scenarios','tests','build'):
        old.update(inventory(previous,folder));new.update(inventory(ROOT,folder))
    changes=[{'path':n,'before_sha256':old.get(n),'after_sha256':new.get(n)} for n in sorted(old.keys()|new.keys()) if old.get(n)!=new.get(n)]
    protected=[n for n in old if n.startswith(('Part1/program/MT5/','Part1/program/SPECIAL/')) or Path(n).name in ('config.txt','command_aliases.json','staff_schema.py','event_composer_domain.py')]
    assert all(old[n]==new.get(n) for n in protected),'protected source changed'
    # Mixed newline preservation: only the logger destination line may differ.
    before=(previous/'Part1/program/monitor_OZ.py').read_bytes();after=(ROOT/'Part1/program/monitor_OZ.py').read_bytes()
    target=b'LOG_DIR = script_dir() / "logs"'
    replacement=b'LOG_DIR = Path(os.environ["MOSES_LOG_DIRECTORY"]) if os.environ.get("MOSES_LOG_DIRECTORY") else script_dir() / "logs"'
    assert before.replace(target,replacement,1)==after,'unexpected monitor/newline change'
    sys.path.insert(0,str(ROOT/'Part1/audit'));from source_integrity import verify_sources
    expected=verify_sources()['final_sha256'];empty=hashlib.sha256(b'').hexdigest()
    unit=ROOT/'Part1/audit/remediation/43-data-selection'
    if unit.exists():raise FileExistsError('register after final source edit only')
    rows=[{'file':x['path'].removeprefix('Part1/'),'before_sha256':expected.get(x['path'].removeprefix('Part1/'),empty),'after_sha256':x['after_sha256']} for x in changes if x['path'].startswith('Part1/program/')]
    write(unit/'changes.json',rows)
    write(unit/'review.json',{'scope':'Declared opt-in strategy dependency closure; same LIVE all-selection; checkpoint-only save suppression in replay; explicit host log directory',
        'tests':'Selection/approval/replacement/history/portable delta; exact actual-day alerts; offline LIVE=replay; network/write isolation',
        'protected':'EA, Wire schema, SPECIAL and Composer decision bodies, user config unchanged; Part3 excluded'})
    result=verify_sources();prior=json.loads((ROOT/'검증결과/schema_cleanup/integrity_registration.json').read_text('utf-8'))['remaining_errors']
    added=sorted(set(result['integrity_errors'])-set(prior));assert not added,added
    write(OUT/'integrity_registration.json',{'unit':unit.relative_to(ROOT).as_posix(),'chain_valid':True,'new_errors':added,'remaining_errors':result['integrity_errors']})
    immutable={p.relative_to(ROOT).as_posix():sha(p) for p in (ROOT/'Part1').rglob('*') if p.is_file() and '__pycache__' not in p.parts and 'results' not in p.parts and p.name!='special_settings.json' and p.suffix!='.ex5'}
    write(ROOT/'build/part1_immutable_sha256.json',dict(sorted(immutable.items())))
    write(OUT/'source_inventory.json',{'changes':changes,'protected_equal':True,'monitor_line_endings_preserved':True,'part3_excluded':True})
    print('registered',len(rows),'Part1 files; new integrity errors',len(added))
if __name__=='__main__':main()
