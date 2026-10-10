"""Move a real MSP3 warehouse; use the same runner before/after relocation."""
from pathlib import Path
import csv,datetime as dt,json,shutil,sys,uuid
R=Path(__file__).resolve().parents[1];sys.path.insert(0,str(R/'Part2'))
from event_backtest.settings import scenario,file_hash,relative_path,warehouse_path
from event_backtest.warehouse import Warehouse
from event_backtest.runner import run_chunk,runtime_config
from event_backtest.portable import validate

def main():
    evidence=R/'검증결과/part2_connection'
    parent=R.parent/('_warehouse_portability_'+uuid.uuid4().hex);parent.mkdir()
    a=parent/'original';b=parent/'moved';a.mkdir()
    origin=R/'검증결과/staff_s7/final_mt5_sigma3_v2'
    info=json.loads((origin/'result.json').read_text('utf-8'));source=origin/Path(info['retained_export']).name
    target=a/'captures'/'preserved';shutil.copytree(source,target)
    dates=[dt.datetime.fromtimestamp(info[k],dt.timezone.utc).isoformat() for k in ('start_s','end_s')]
    c={'capture_id':'preserved','symbol':info['symbol'],'start':dates[0],'end':dates[1],'unit':'DAY','mode':'TIMER',
       'ea_build_hash':'preserved','schema_id':918720360,'tick_evidence':{'actual':'UNCONFIRMED'},'path':'captures/preserved',
       'stored_bytes':sum(p.stat().st_size for p in target.iterdir() if p.is_file()),
       'files':{p.name:file_hash(p) for p in target.iterdir() if p.is_file()},'recorded_at':dt.datetime.now(dt.timezone.utc).isoformat()}
    w=Warehouse(a);w.register(c);w.close()
    s=scenario(start=dates[0],end=dates[1],mode='TICK',overlap_trading_days=0)
    from staff_golden.scenario import WATCHES
    s['commands']=[{'chat_id':chat,'text':text} for chat,text in WATCHES];s['triggers']={'SPECIAL7':'무지성 올존'}
    config=runtime_config(s)
    def process(root,label):
        w=Warehouse(root);capture=w.find_capture('preserved');w.close();assert capture
        task={'scenario':s,'config':config,'run_id':'portability','start':s['start'],'end':s['end'],'warm_start':s['start'],
              'captures':[capture],'out':str(root/'runs'/label),'warehouse':str(root)}
        result=run_chunk(task);validate(result)
        with warehouse_path(root,result['alerts_csv']).open(encoding='utf-8',newline='') as f:rows=list(csv.DictReader(f))
        return result,rows
    before,arows=process(a,'before')
    a.rename(b)
    after,brows=process(b,'after')
    result={'catalog_found_after_move':True,'replay_alerts_identical':arows==brows,'alerts':len(arows),
            'metadata_relative_only':True,'before':before,'after':after}
    (evidence/'portability_replay.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    assert arows==brows
    print(result,flush=True)
if __name__=='__main__':main()
