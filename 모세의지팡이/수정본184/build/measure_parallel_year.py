"""One isolated annual run. Run configurations sequentially for fair timing."""
from pathlib import Path
import argparse,json,os,sys,time
ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'검증결과/parallel_oz'
sys.path[:0]=[str(ROOT/'Part1/program'),str(ROOT/'Part2')]

def main():
    p=argparse.ArgumentParser();p.add_argument('--warehouse',required=True);p.add_argument('--workers',type=int,required=True)
    p.add_argument('--work-size',choices=('MONTH','FORTNIGHT'),default='MONTH')
    p.add_argument('--capture-start',choices=('keyframe','beginning'),default='keyframe');a=p.parse_args()
    os.environ['PYTHONHASHSEED']='0'
    from event_backtest.settings import scenario
    from event_backtest.runner import run
    from event_backtest.system import deny_network
    deny_network()
    captures=[x['capture'] for x in json.loads((OUT/'keyframe_conversion.json').read_text('utf-8'))]
    if len(captures)!=13:raise ValueError('all 13 pieces must finish conversion first')
    captures.sort(key=lambda c:c['start'])
    s=scenario(symbol='XAUUSD+',start='2024-10-01',end='2025-10-01',strategies=['SPECIAL1'],
               cores=a.workers,work_size=a.work_size,capture_start=a.capture_start,overlap_trading_days=3)
    label=f'year_{a.work_size.lower()}_{a.workers}_{a.capture_start}'
    target=OUT/(label+'.json')
    if target.exists():raise FileExistsError('completed evidence exists: '+label)
    progress=[];last=[0.0]
    def emit(kind,data):
        now=time.perf_counter()
        if kind=='CHUNK_COMPLETE' or not progress or now-last[0]>60:
            progress.append({'kind':kind,**data});last[0]=now
            (OUT/(label+'_progress.json')).write_text(json.dumps(progress,indent=2),encoding='utf-8')
            print(json.dumps({'kind':kind,**data}),flush=True)
    began=time.perf_counter();result=run(s,a.warehouse,cores=a.workers,captures=captures,emit=emit)
    result['command_seconds']=time.perf_counter()-began
    target.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'label':label,'seconds':result['command_seconds'],'first_progress_seconds':result['first_progress_seconds'],'run_id':result['run_id']}),flush=True)
if __name__=='__main__':main()
