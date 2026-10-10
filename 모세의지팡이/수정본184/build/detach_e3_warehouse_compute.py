"""Remove only the two retired Python recalculation validators, not storage."""
from pathlib import Path
import ast,json
R=Path(__file__).resolve().parents[1]
changes=[]
for filename,fn in [('replay.py','validate_percentile_replay'),('observations.py','build_job')]:
    path=R/'Part2/data_warehouse'/filename
    text=path.read_text('utf-8');tree=ast.parse(text);lines=text.splitlines(keepends=True)
    node=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name==fn)
    del lines[node.lineno-1:node.end_lineno]
    final=''.join(lines);ast.parse(final);path.write_text(final,encoding='utf-8')
    changes.append({'path':path.relative_to(R).as_posix(),'removed_function':fn,
                    'reason':'retired Python indicator recomputation; storage/import/read API retained'})
(R/'검증결과/event_e3/warehouse_compute_detach.json').write_text(json.dumps(changes,ensure_ascii=False,indent=2),encoding='utf-8')
print(changes)
