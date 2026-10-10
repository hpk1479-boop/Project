"""Structural source evidence; clock/state ports are separately reviewed."""
import ast
from event_e2_common import *
names=('monitor_OZ.py','strategy_FVG.py','strategy_SWEEP.py','strategy_INDICATOR.py',
       'manager_KIM.py','watch_orchestrator.py','command_interpreter.py',
       *(f'SPECIAL/SPECIAL{i}.py' for i in range(1,8)))
def definitions(path):
    root=ast.parse(path.read_text('utf-8-sig'));result={}
    def visit(nodes,prefix=''):
        for node in nodes:
            if isinstance(node,ast.ClassDef):visit(node.body,prefix+node.name+'.')
            elif isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef)):
                result[prefix+node.name]=hashlib.sha256(ast.dump(node,include_attributes=False).encode()).hexdigest()
    visit(root.body);return result
result={}
for name in names:
    a=definitions(ROOT.parent/'수정본16/Part1/program'/name)
    b=definitions(ROOT/'Part1/program'/name)
    result[name]={'unchanged_functions':[n for n in a if a[n]==b.get(n)],
                  'changed_functions':[n for n in a if a[n]!=b.get(n)],
                  'added_functions':sorted(b.keys()-a.keys()),'removed_functions':sorted(a.keys()-b.keys())}
write(OUT/'decision_source_review.json',result)
for name,row in result.items():print(name,len(row['unchanged_functions']),'unchanged; changed',row['changed_functions'],flush=True)
