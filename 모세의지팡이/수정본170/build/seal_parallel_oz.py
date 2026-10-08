"""Final source inventory and integrity-chain entry; previous copies are read-only."""
from pathlib import Path
import argparse,ast,hashlib,json,os,sys
ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'검증결과/parallel_oz'
def sha(path):
    with path.open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
def write(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
def inventory(root):
    return {p.relative_to(root).as_posix():sha(p) for folder in ('Part1/program','Part2/event_backtest','tests','build')
            for p in (root/folder).rglob('*') if p.is_file() and not set(p.parts)&{'__pycache__','.pytest_cache','logs'}
            and p.suffix in ('.py','.pyw','.mq5','.mqh','.ex5','.json','.txt','.md','.cmd')}
def main():
    parser=argparse.ArgumentParser();parser.add_argument('--previous',required=True,type=Path)
    parser.add_argument('--register',action='store_true');args=parser.parse_args();previous=args.previous.resolve()
    original=json.loads((OUT/'source_before.json').read_text('utf-8'))
    bad=[name for name,expected in original.items() if not (previous/name).is_file() or sha(previous/name)!=expected]
    write(OUT/'previous_preservation.json',{'checked':len(original),'changed':bad})
    if bad:raise ValueError('previous revision changed: '+str(bad))
    old,new=inventory(previous),inventory(ROOT)
    for name in ('AGENTS.md','AGENTS.md.txt'):
        old[name]=sha(previous/name);new[name]=sha(ROOT/name)
    changes=[{'path':name,'before_sha256':old.get(name),'after_sha256':new.get(name)} for name in sorted(old.keys()|new.keys()) if old.get(name)!=new.get(name)]
    protected=[name for name in old if name.startswith('Part1/program/MT5/') or Path(name).suffix in ('.mq5','.mqh','.ex5')
               or Path(name).name in ('config.txt','command_aliases.json','staff_schema.py')]
    assert all(old[name]==new.get(name) for name in protected)
    for name in ('native_mt5.py','native_features.py','bar_seed.py'):
        assert sha(previous/'Part2/generic_backtest'/name)==sha(ROOT/'Part2/generic_backtest'/name)
    # SPECIAL changes must be solely a stable ordering of an existing set loop.
    class IgnoreSortedLoop(ast.NodeTransformer):
        def visit_For(self,node):
            self.generic_visit(node)
            if isinstance(node.iter,ast.Call) and isinstance(node.iter.func,ast.Name) and node.iter.func.id=='sorted' and len(node.iter.args)==1:
                node.iter=node.iter.args[0]
            return node
    for i in range(1,8):
        rel=f'Part1/program/SPECIAL/SPECIAL{i}.py'
        trees=[IgnoreSortedLoop().visit(ast.parse((base/rel).read_text('utf-8-sig'))) for base in (previous,ROOT)]
        assert ast.dump(trees[0])==ast.dump(trees[1]),'SPECIAL decision changed: '+rel
    # Mixed monitor line endings must survive the exact-byte loop substitutions.
    rel='Part1/program/monitor_OZ.py';a=(previous/rel).read_bytes();b=(ROOT/rel).read_bytes()
    endings=lambda raw:(raw.count(b'\r\n'),raw.count(b'\n')-raw.count(b'\r\n'))
    assert endings(a)==endings(b)
    write(OUT/'source_inventory.json',{'changes':changes,'ea_schema_config_equal':True,
        'special_decision_ast_equal_except_sorted_loop':True,'monitor_line_endings_equal':True})
    if not args.register:return
    sys.path.insert(0,str(ROOT/'Part1/audit'));from source_integrity import verify_sources
    prior=json.loads((ROOT/'검증결과/engine_optimization/integrity_registration.json').read_text('utf-8'))['remaining_errors']
    expected=verify_sources()['final_sha256'];empty=hashlib.sha256(b'').hexdigest()
    unit=ROOT/'Part1/audit/remediation/45-parallel-oz'
    if unit.exists():raise FileExistsError('integrity entry already registered')
    rows=[{'file':c['path'].removeprefix('Part1/'),'before_sha256':expected.get(c['path'].removeprefix('Part1/'),empty),
           'after_sha256':c['after_sha256']} for c in changes if c['path'].startswith('Part1/program/')]
    write(unit/'changes.json',rows)
    write(unit/'review.json',{'scope':'Selected chain dependencies, deterministic unordered traversals, static OZ upper bound and exact EWM prefix cache',
        'tests':'Related offline logic, real capture comparisons, synthetic240/TIMER239 LIVE=replay, deterministic hash seeds',
        'protected':'EA/schema/config/formulas/text/thresholds unchanged; SPECIAL AST differs only by sorted loop; no network delivery'})
    result=verify_sources();added=sorted(set(result['integrity_errors'])-set(prior));assert not added,added
    write(OUT/'integrity_registration.json',{'unit':unit.relative_to(ROOT).as_posix(),'new_errors':added,'remaining_errors':result['integrity_errors']})
    immutable={p.relative_to(ROOT).as_posix():sha(p) for p in (ROOT/'Part1').rglob('*') if p.is_file() and '__pycache__' not in p.parts
               and 'results' not in p.parts and p.name!='special_settings.json' and p.suffix!='.ex5'}
    write(ROOT/'build/part1_immutable_sha256.json',dict(sorted(immutable.items())))
    print('registered',len(rows),'Part1 sources; no new integrity errors')
if __name__=='__main__':main()
