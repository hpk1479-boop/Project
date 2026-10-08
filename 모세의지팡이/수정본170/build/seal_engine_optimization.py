"""Register reviewed changes and immutable inventory; no Part3 traversal."""
from pathlib import Path
import argparse,hashlib,json,os,sys
ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'검증결과/engine_optimization'

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
    p=argparse.ArgumentParser();p.add_argument('--previous',type=Path,required=True);a=p.parse_args();previous=a.previous.resolve()
    old={};new={}
    for folder in ('Part1/program','Part2/event_backtest','tests','build'):
        old.update(inventory(previous,folder));new.update(inventory(ROOT,folder))
    for name in ('AGENTS.md','AGENTS.md.txt','Part3_레거시_참조목록.md','Part2/validation_suite/test_staff_s1.py'):
        old[name]=sha(previous/name);new[name]=sha(ROOT/name)
    changes=[{'path':n,'before_sha256':old.get(n),'after_sha256':new.get(n)} for n in sorted(old.keys()|new.keys()) if old.get(n)!=new.get(n)]
    protected=[n for n in old if n.startswith(('Part1/program/MT5/','Part1/program/SPECIAL/')) or Path(n).name in ('config.txt','command_aliases.json','monitor_OZ.py')]
    assert all(old[n]==new.get(n) for n in protected),'protected EA/SPECIAL/config/monitor changed'
    assert all(old[n]==new.get(n) for n in old if n.startswith('Part2/event_backtest/')
               and n!='Part2/event_backtest/bridge.py'),'unrelated Part2 execution/storage source changed'
    for name in ('native_mt5.py','native_features.py','bar_seed.py'):
        relative='Part2/generic_backtest/'+name
        assert sha(previous/relative)==sha(ROOT/relative),relative
    # Codec implementation may change; the Wire registry and generated constants may not.
    import ast
    def registry(path):
        tree=ast.parse(path.read_text('utf-8-sig'))
        return {n.targets[0].id:ast.dump(n.value) for n in tree.body if isinstance(n,ast.Assign) and isinstance(n.targets[0],ast.Name)
                and n.targets[0].id in ('PIPE_VALUE_COLUMNS','COLUMN_ALIASES','WIRE_HEADER','WIRE_MAGIC','WIRE_VERSION')}
    assert registry(previous/'Part1/program/staff_schema.py')==registry(ROOT/'Part1/program/staff_schema.py')
    sys.path.insert(0,str(ROOT/'Part1/audit'));from source_integrity import verify_sources
    expected=verify_sources()['final_sha256'];empty=hashlib.sha256(b'').hexdigest()
    unit=ROOT/'Part1/audit/remediation/44-engine-optimization'
    rows=[{'file':x['path'].removeprefix('Part1/'),'before_sha256':expected.get(x['path'].removeprefix('Part1/'),empty),'after_sha256':x['after_sha256']}
          for x in changes if x['path'].startswith('Part1/program/')]
    if unit.exists():raise FileExistsError('register after final source edit only')
    write(unit/'changes.json',rows)
    write(unit/'review.json',{'scope':'Lazy actual Fact reads; resident Composer records and receipt aging; demanded NumPy inputs; canonical atomic STAFF decode/publication',
        'tests':'Offline conditions, complete synthetic240/TIMER239 LIVE=replay, source-clock memory retention, checkpoint/host save, CRC/atomic rollback, week alerts',
        'protected':'EA/schema/SPECIAL/config/monitor calculations unchanged; no network delivery; Part3 folder removed only in this revision'})
    result=verify_sources();prior=json.loads((ROOT/'검증결과/data_selection/integrity_registration.json').read_text('utf-8'))['remaining_errors']
    added=sorted(set(result['integrity_errors'])-set(prior));assert not added,added
    write(OUT/'integrity_registration.json',{'unit':unit.relative_to(ROOT).as_posix(),'chain_valid':True,'new_errors':added,'remaining_errors':result['integrity_errors']})
    immutable={p.relative_to(ROOT).as_posix():sha(p) for p in (ROOT/'Part1').rglob('*') if p.is_file() and '__pycache__' not in p.parts
               and 'results' not in p.parts and p.name!='special_settings.json' and p.suffix!='.ex5'}
    write(ROOT/'build/part1_immutable_sha256.json',dict(sorted(immutable.items())))
    write(OUT/'source_inventory.json',{'changes':changes,'protected_equal':True,'wire_schema_equal':True,'monitor_line_endings_preserved':True,'part3_folder_absent':not(ROOT/'Part3').exists()})
    print('registered',len(rows),'Part1 files; new integrity errors',len(added))
if __name__=='__main__':main()
