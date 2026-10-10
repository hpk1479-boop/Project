"""One-time cleanup of pre-portability development captures; run after recorder exits."""
from pathlib import Path
import sys,json,shutil,uuid
R=Path(__file__).resolve().parents[1];sys.path.insert(0,str(R/'Part2'))
from event_backtest.settings import settings,relative_path
from event_backtest.portable import validate
import duckdb

def main():
    root=Path(settings()['warehouse']);db=root/'event_backtest.duckdb'
    old=duckdb.connect(str(db));data=[]
    for raw, in old.execute('select metadata from captures').fetchall():
        c=json.loads(raw)
        if Path(c['path']).is_absolute():c['path']=relative_path(root,c['path'])
        c.pop('export',None)
        validate(c);data.append(c)
    assert old.execute('select count(*) from runs').fetchone()[0]==0,'preserve completed runs explicitly before migrating'
    assert old.execute('select count(*) from alerts').fetchone()[0]==0
    old.close()
    out=R/'검증결과/part2_connection';backup=out/('pre_portability_'+uuid.uuid4().hex+'.duckdb')
    shutil.move(db,backup)
    from event_backtest.warehouse import Warehouse
    w=Warehouse(root)
    for c in data:w.register(c)
    w.close()
    # The new catalog contains the complete capture metadata. Do not retain a
    # second, machine-bound catalog with obsolete absolute path strings.
    assert backup.resolve().parent==out.resolve() and backup.name.startswith('pre_portability_')
    backup.unlink()
    work=(root/'mt5_work').resolve()
    if work.exists():
        assert work.parent==root.resolve()
        shutil.move(work,out/('legacy_mt5_work_'+uuid.uuid4().hex))
    for name in ('day_BAR.json','day_TICK.json','recorded_captures.json'):
        path=out/name
        if path.exists():
            rows=json.loads(path.read_text('utf-8'))
            for c in rows:
                if Path(c['path']).is_absolute():c['path']=relative_path(root,c['path'])
                c.pop('export',None);validate(c)
            path.write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding='utf-8')
    (out/'portability_migration.json').write_text(json.dumps({'captures':len(data),'absolute_paths_in_catalog':0,'runtime_moved_outside_warehouse':True},indent=2),encoding='utf-8')
    print('migrated',len(data),'captures')
if __name__=='__main__':main()
