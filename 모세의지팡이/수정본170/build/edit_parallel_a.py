from pathlib import Path
import ast,json
ROOT=Path(__file__).resolve().parents[1];P=ROOT/'Part1/program';OUT=ROOT/'검증결과/parallel_oz'
def edit(name,changes):
    p=P/name;raw=p.read_bytes()
    for old,new in changes:
        a,b=old.encode(),new.encode()
        if a not in raw:raise ValueError((name,old))
        raw=raw.replace(a,b)
    p.write_bytes(raw)
edit('event_selection.py',[("'SPECIAL4':('OZ','WONBI')","'SPECIAL4':('OZ','WONBI','CHAINS')"),
                           ("'SPECIAL5':('OZ',)","'SPECIAL5':('OZ','CHAINS')")])
edit('event_composer_domain.py',[
    ('for symbol, tf_set in required.items():','for symbol, tf_set in sorted(required.items()):'),
    ('for tf in tf_set:','for tf in sorted(tf_set, key=tf_seconds):'),
    ('for symbol in symbols:','for symbol in sorted(symbols):'),
    ('for family, fast, slow in pair_reqs:','for family, fast, slow in sorted(pair_reqs):'),
    ('for family, period in price_reqs:','for family, period in sorted(price_reqs):'),
    ('for family, period in slope_reqs:','for family, period in sorted(slope_reqs):')])
edit('SPECIAL/SPECIAL5.py',[('for wid in {str(x) for x in watch_ids if str(x)}:',
                            'for wid in sorted({str(x) for x in watch_ids if str(x)}):')])
# Find set-derived loops, including collection construction upstream of dict
# iteration. Evidence is reviewed, not used to blanket-sort ordered priority lists.
findings=[]
for p in P.rglob('*.py'):
    if any(x in p.parts for x in ('__pycache__','logs')):continue
    try:tree=ast.parse(p.read_text('utf-8-sig'))
    except (SyntaxError,UnicodeError):continue
    for scope in ast.walk(tree):
        if not isinstance(scope,(ast.FunctionDef,ast.AsyncFunctionDef)):continue
        nodes=list(ast.walk(scope));names=set()
        for n in nodes:
            if isinstance(n,(ast.Assign,ast.AnnAssign)):
                value=n.value;targets=n.targets if isinstance(n,ast.Assign) else [n.target]
                if isinstance(value,(ast.Set,ast.SetComp)) or (isinstance(value,ast.Call) and isinstance(value.func,ast.Name) and value.func.id in ('set','frozenset')) or (isinstance(value,ast.BinOp) and isinstance(value.op,(ast.BitOr,ast.BitAnd,ast.Sub))):
                    names.update(t.id for t in targets if isinstance(t,ast.Name))
        for n in nodes:
            if not isinstance(n,(ast.For,ast.comprehension)):continue
            expr=n.iter
            if isinstance(expr,(ast.Set,ast.SetComp)) or isinstance(expr,ast.Name) and expr.id in names:
                findings.append({'file':p.relative_to(P).as_posix(),'function':scope.name,'line':getattr(n,'lineno',getattr(expr,'lineno',0)),'iteration':ast.unparse(expr)})
(OUT/'unordered_iteration_candidates.json').write_text(json.dumps(findings,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(findings,ensure_ascii=False,indent=2))
