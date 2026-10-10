"""Exercise the complete process runner after relocating project and warehouse."""
from pathlib import Path
import ast,csv,json,shutil,subprocess,sys,uuid
R=Path(__file__).resolve().parents[1]

def child(root,warehouse):
    sys.path[:0]=[str(root/'Part2'),str(root/'Part1/program')]
    from event_backtest.runner import run
    from event_backtest.warehouse import Warehouse
    from event_backtest.settings import scenario
    from event_backtest.portable import validate
    w=Warehouse(warehouse);capture=w.find_capture('preserved');w.close()
    s=scenario(start=capture['start'],end=capture['end'],mode='TICK',overlap_trading_days=0)
    s['commands']=json.loads((root/'Part2/fixture_commands.json').read_text('utf-8'))
    s['triggers']={'SPECIAL7':'무지성 올존'}
    data=run(s,warehouse,captures=[capture],sequential=True)
    validate(data)
    w=Warehouse(warehouse)
    assert w.db.execute('select status from runs where run_id=?',[data['run_id']]).fetchone()==('COMPLETE',)
    alerts=w.db.execute('select count(*) from alerts where run_id=?',[data['run_id']]).fetchone()[0]
    timings=w.db.execute('select count(*) from timings where run_id=?',[data['run_id']]).fetchone()[0]
    w.close();assert alerts==2 and timings>=9
    return {'project_relocated':True,'warehouse_relocated':True,'alerts':alerts,'timing_rows':timings,'result':data}

def main():
    if len(sys.argv)>1:
        data=child(Path(sys.argv[1]),Path(sys.argv[2]))
        Path(sys.argv[3]).write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
        return
    case=R.parent/('_project_portability_'+uuid.uuid4().hex)
    before=case/'original';after=case/'moved';before.mkdir(parents=True)
    for folder in ('Part1/program','Part2/event_backtest'):
        for path in (R/folder).rglob('*'):
            if not path.is_file() or any(p in ('__pycache__','logs','MT5','results','python setup') for p in path.parts):continue
            dest=before/path.relative_to(R);dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(path,dest)
    (before/'Part2/generic_backtest').mkdir(parents=True)
    shutil.copy2(R/'Part2/generic_backtest/native_mt5.py',before/'Part2/generic_backtest/native_mt5.py')
    shutil.copy2(R/'Part2/event_backtest.json',before/'Part2/event_backtest.json')
    tree=ast.parse((R/'Part2/staff_golden/scenario.py').read_text('utf-8'))
    watches=next(ast.literal_eval(n.value) for n in tree.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='WATCHES' for t in n.targets))
    (before/'Part2/fixture_commands.json').write_text(json.dumps([{'chat_id':c,'text':t} for c,t in watches],ensure_ascii=False),encoding='utf-8')
    before.rename(after)
    source=next(p/'moved' for p in R.parent.glob('_warehouse_portability_*') if (p/'moved/event_backtest.duckdb').exists())
    warehouse=case/'warehouse_original';shutil.copytree(source,warehouse)
    moved=case/'warehouse_moved';warehouse.rename(moved)
    out=R/'검증결과/part2_connection/project_and_runner_move.json'
    subprocess.run([sys.executable,'-X','utf8','-B',__file__,str(after),str(moved),str(out)],check=True)
    print(out.name,'PASS',flush=True)

if __name__=='__main__':main()
