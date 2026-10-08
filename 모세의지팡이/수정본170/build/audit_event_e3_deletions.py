from pathlib import Path
import ast,json,hashlib
R=Path(__file__).resolve().parents[1];old=R.parent/'수정본18'
tree=ast.parse((R/'build/remove_event_e3_legacy.py').read_text('utf-8-sig'))
sets={n.targets[0].id:ast.literal_eval(n.value) for n in tree.body if isinstance(n,ast.Assign) and isinstance(n.targets[0],ast.Name) and n.targets[0].id.startswith('keep_')}
groups=[('calculations',sets['keep_calc'],()),('generic_backtest',sets['keep_generic'],()),('pit',sets['keep_pit'],()),
        ('live_replay',set(),()),('part1_host',{'__init__.py','capture.py','synthetic.py','wire_v2.py'},()),('BACKTEST_SPECIAL',set(),())]
deletions=[];keep=[]
for folder,allowed,prefixes in groups:
    for p in sorted((R/'Part2'/folder).rglob('*.py')):
        rel=p.relative_to(R/'Part2'/folder).as_posix()
        if rel in allowed or any(rel.startswith(prefix) for prefix in prefixes):keep.append(p);continue
        prior=old/p.relative_to(R)
        h=hashlib.sha256(p.read_bytes()).hexdigest()
        deletions.append({'path':p.relative_to(R).as_posix(),'sha256':h,'original_exists':prior.is_file(),
            'identical_in_revision18':prior.is_file() and hashlib.sha256(prior.read_bytes()).hexdigest()==h})
keep+=list((R/'Part2/data_warehouse').glob('*.py'))
deleting={row['path'] for row in deletions};broken=[]
for p in keep:
    package=p.relative_to(R/'Part2').with_suffix('').parts[:-1]
    source=ast.parse(p.read_text('utf-8-sig'))
    for n in ast.walk(source):
        if isinstance(n,ast.Import):modules=[a.name for a in n.names]
        elif isinstance(n,ast.ImportFrom):
            prefix=list(package[:len(package)-n.level+1]) if n.level else []
            modules=['.'.join(prefix+((n.module or '').split('.') if n.module else []))]
        else:continue
        for module in modules:
            target='Part2/'+module.replace('.','/')+'.py'
            if target in deleting:broken.append({'file':p.relative_to(R).as_posix(),'line':n.lineno,'dependency':target})
report={'target_revision':str(R),'previous_revision_writable':False,'files':deletions,
        'original_backups_all_identical':all(r['identical_in_revision18'] for r in deletions),
        'kept_files':sorted(p.relative_to(R).as_posix() for p in keep),'dependencies_to_resolve_before_delete':broken,
        'launchers':'Only revision19 launchers; redirect retired engine to explicit not-yet-connected message. Revision18 and live revision16 untouched.',
        'user_authorization':'E3 items 1 and 7: remove polling/virtual-clock executors and Part2 old ported Python indicator recalculation engine; preserve native MT5 and DuckDB.'}
(R/'검증결과/event_e3/deletion_plan.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print('Deletion plan',len(deletions),'identical backups',report['original_backups_all_identical'],'dependency edges',broken)

