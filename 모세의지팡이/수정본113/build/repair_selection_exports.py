"""Repair only this task's final exports; retain computation evidence and timing."""
from pathlib import Path
import csv,hashlib,json,shutil,sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'Part2'))
from event_backtest.settings import settings
from event_backtest.warehouse import Warehouse,FIELDS
from event_backtest.runner import code_hash

def main():
    out=ROOT/'검증결과/data_selection/approved';backup=out/'exports_before_repair'
    backup.mkdir(exist_ok=True)
    evidence=[out/'month_special1.json']+[out/f'week_special{i}.json' for i in range(1,8)]
    evidence.append(out.parent/'public_day_run.json')
    warehouse=Path(settings()['warehouse']);catalog=Warehouse(warehouse,results=True)
    repaired=[]
    try:
        for path in evidence:
            data=json.loads(path.read_text('utf-8'));key=data['run_id']
            before=backup/(key+'.json')
            if before.exists():raise FileExistsError('export repair already recorded')
            shutil.copy2(path,before)
            row=catalog.db.execute('SELECT status,metadata FROM runs WHERE run_id=?',[key]).fetchone()
            assert row[0]=='COMPLETE'
            stored=json.loads(row[1]);target=warehouse/data['alerts_csv']
            assert target.resolve().parent==(warehouse/'runs'/key).resolve()
            assert not target.exists(),'do not replace an existing verified export'
            count=catalog.export_results(key,target)
            with target.open(encoding='utf-8',newline='') as f:rows=list(csv.DictReader(f))
            expected=sum(c['notifications'] for c in data['chunks'])
            assert count==len(rows)==expected
            misplaced=ROOT/'Part2'/key;removed=None
            if misplaced.exists():
                assert misplaced.resolve().parent==(ROOT/'Part2').resolve()
                with misplaced.open(encoding='utf-8',newline='') as f:assert list(csv.reader(f))==[list(FIELDS)]
                shutil.copy2(misplaced,backup/(key+'.misplaced.csv'))
                removed=misplaced.relative_to(ROOT).as_posix();misplaced.unlink()
            repair={'result_export_repaired':True,'result_export_code_hash':code_hash()}
            data.update(repair);stored.update(repair)
            catalog.db.execute('UPDATE runs SET metadata=? WHERE run_id=?',[json.dumps(stored,ensure_ascii=False),key])
            for p in (path,warehouse/'runs'/key/'result.json'):
                p.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
            repaired.append({'run_id':key,'alerts':count,'path':data['alerts_csv'],'sha256':hashlib.sha256(target.read_bytes()).hexdigest(),'removed_empty_file':removed})
    finally:catalog.close()
    (out/'export_repair.json').write_text(json.dumps({'cause':'DuckDB COPY binds destination before nested query parameter',
        'calculation_rerun':False,'computation_code_hash_preserved':True,'runs':repaired},ensure_ascii=False,indent=2),encoding='utf-8')
    print('repaired',len(repaired),'exports; computation and timings preserved')

if __name__=='__main__':main()
