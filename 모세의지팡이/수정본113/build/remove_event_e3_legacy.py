"""Exact, auditable deletions inside revision19 only; no previous revision I/O."""
from pathlib import Path
import ast,hashlib,json
R=Path(__file__).resolve().parents[1];assert R.name=='수정본19'
deleted=[]
plan=json.loads((R/'검증결과/event_e3/deletion_plan.json').read_text('utf-8'))
assert plan['original_backups_all_identical']
planned={row['path']:row['sha256'] for row in plan['files']}
def remove(path):
    absolute=path.resolve();absolute.relative_to(R.resolve())
    assert absolute.is_file() and not path.is_symlink(),path
    rel=path.relative_to(R).as_posix();checksum=hashlib.sha256(path.read_bytes()).hexdigest()
    if rel.startswith('Part2/'):assert planned[rel]==checksum
    original=R.parent/'수정본18'/path.relative_to(R)
    assert original.is_file() and hashlib.sha256(original.read_bytes()).hexdigest()==checksum
    deleted.append({'path':rel,'sha256':checksum})
    path.unlink()
P=R/'Part1/program'
for name,classname in [('staff_snapshot.py','SnapshotClient')]:
    path=P/name;s=path.read_text('utf-8');lines=s.splitlines(keepends=True)
    node=next(n for n in ast.parse(s).body if isinstance(n,ast.ClassDef) and n.name==classname)
    del lines[node.lineno-1:node.end_lineno];path.write_text(''.join(lines),encoding='utf-8')
path=P/'staff_compat.py';s=path.read_text('utf-8');lines=s.splitlines(keepends=True)
cls=next(n for n in ast.parse(s).body if isinstance(n,ast.ClassDef) and n.name=='StaffCompat')
node=next(n for n in cls.body if getattr(n,'name','')=='request');del lines[node.lineno-1:node.end_lineno]
path.write_text(''.join(lines),encoding='utf-8')
if (P/'strategy_TREND.py').exists():remove(P/'strategy_TREND.py')
keep_calc={'__init__.py','identity.py','timeframe.py','state_codec.py','support/pit_models.py','support/pit_contracts.py','support/generic_contracts.py'}
keep_generic={'__init__.py','native_mt5.py','data_build.py','canonical.py','contracts.py','paths.py','calendar.py','session_filter.py','provenance.py','history/__init__.py','history/provider.py','history/selection.py','history/live_status.py','history/prepare.py','history/cache.py','history/raw_verification.py'}
keep_pit={'__init__.py','models.py','contracts.py','archive/__init__.py','archive/reader.py'}
for folder,keep,extra in [('calculations',keep_calc,()),('generic_backtest',keep_generic,()),
                          ('pit',keep_pit,()),('live_replay',set(),()),
                          ('part1_host',{'__init__.py','capture.py','synthetic.py','wire_v2.py'},())]:
    root=R/'Part2'/folder
    for path in sorted(root.rglob('*.py')):
        relative=path.relative_to(root).as_posix()
        if relative in keep or any(relative.startswith(prefix) for prefix in extra):continue
        remove(path)
for path in (R/'Part2/BACKTEST_SPECIAL').glob('*.py'):remove(path)
# Old warehouse compute validators are removed; storage/read/import remains.
for name,removed_names in [('replay.py',{'validate_replay'}),('observations.py',{'validate_observation_job'})]:
    path=R/'Part2/data_warehouse'/name;s=path.read_text('utf-8');tree=ast.parse(s);lines=s.splitlines(keepends=True)
    nodes=[]
    for node in tree.body:
        if isinstance(node,ast.FunctionDef) and any(isinstance(child,ast.ImportFrom) and (child.module or '') in ('calculations.native_replay','calculations.allzone_checkpoint') for child in ast.walk(node)):
            nodes.append(node)
    for n in reversed(nodes):
        del lines[n.lineno-1:n.end_lineno]
        deleted.append({'path':path.relative_to(R).as_posix(),'removed_function':n.name})
    path.write_text(''.join(lines),encoding='utf-8')
for name in ('BACKTEST CONTROL.pyw','LIVE_REPLAY.pyw'):
    path=R/'Part2'/name
    path.write_text('''"""Old polling/ported executors were removed in E3; Part2 event integration is pending."""
def main():
    import tkinter as tk
    from tkinter import messagebox
    root=tk.Tk();root.withdraw()
    messagebox.showinfo("MOSES Part2", "이전 폴링·Python 재계산 엔진은 제거되었습니다.\\nMT5 네이티브 추출과 DuckDB 창고는 보존되어 있습니다.\\n이벤트 엔진 연결은 후속 Part2 작업입니다.")
    root.destroy()
if __name__=="__main__":main()
''',encoding='utf-8')
(R/'Part2/part1_host/__init__.py').write_text('"""Preserved capture codec and test-only synthetic fixtures. No polling executor."""\n',encoding='utf-8')
out=R/'검증결과/event_e3/deletions.json';out.write_text(json.dumps(deleted,ensure_ascii=False,indent=2),encoding='utf-8')
print('Removed entries',len(deleted))

