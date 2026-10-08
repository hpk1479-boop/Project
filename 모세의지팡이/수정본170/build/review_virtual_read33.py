"""Final source-scope, syntax and artifact checks (no replay)."""
from pathlib import Path
import ast,difflib,hashlib,json
ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'검증결과/virtual_read_optimized'
before=OUT/'before_sources/virtual_entry.py';after=ROOT/'Part2/event_backtest/virtual_entry.py'
old=ast.parse(before.read_text(encoding='utf8'));new=ast.parse(after.read_text(encoding='utf8'))
unchanged=[]
for name in ('VirtualEntry','finite','pricing','load_alerts'):
    a=next(n for n in old.body if getattr(n,'name',None)==name)
    b=next(n for n in new.body if getattr(n,'name',None)==name)
    assert ast.dump(a)==ast.dump(b),name
    unchanged.append(name)
files=['Part2/event_backtest/virtual_entry.py','Part2/event_backtest/virtual_source.py','Part2/event_backtest/virtual_msd.py',
       'tests/test_virtual_read33.py','tests/test_stop_virtual33.py']
hashes={}
for name in files:
    p=ROOT/name;compile(p.read_text(encoding='utf8'),name,'exec');hashes[name]=hashlib.sha256(p.read_bytes()).hexdigest()
diff=''
for name in ('virtual_entry.py','virtual_source.py'):
    diff+=''.join(difflib.unified_diff((OUT/'before_sources'/name).read_text(encoding='utf8').splitlines(True),
        (ROOT/'Part2/event_backtest'/name).read_text(encoding='utf8').splitlines(True),fromfile='before/'+name,tofile='after/'+name))
(OUT/'source_changes.diff').write_text(diff,encoding='utf8')
checks={}
for period in ('week','month'):
    checks[period]={}
    for filename in ('virtual_trades.csv','virtual_summary.csv'):
        a=(OUT/'before'/period/'same_run'/filename).read_bytes()
        b=(OUT/'after'/period/'same_run'/filename).read_bytes()
        assert a==b
        checks[period][filename]={'bytes_identical':True,'sha256':hashlib.sha256(b).hexdigest(),'bytes':len(b)}
record={'unchanged_decision_ast':unchanged,'syntax_checked':files,'source_sha256':hashes,'csv_comparison':checks}
(OUT/'final_review.json').write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf8')
print(json.dumps(record,ensure_ascii=False))
