"""Annual equality, overlap differences and measured worker/CPU distributions."""
from pathlib import Path
import argparse,collections,csv,json
from check_parallel_evidence import alerts,compare
ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'검증결과/parallel_oz'
def main():
    p=argparse.ArgumentParser();p.add_argument('--warehouse',required=True);a=p.parse_args();root=Path(a.warehouse)
    runs={}
    for path in OUT.glob('year_*.json'):
        if path.name.endswith('_progress.json'):continue
        value=json.loads(path.read_text('utf-8'))
        if 'run_id' in value and 'chunks' in value:runs[path.stem]=value
    rows=[]
    for label,r in runs.items():
        chunks=r['chunks'];n=sum(c['bundles'] for c in chunks);warm=sum(c['warmup_bundles'] for c in chunks)
        rows.append({'label':label,'workers':r['cores'],'actual_workers':len(r['worker_distribution']),
            'work_size':r['scenario']['work_size'],'capture_start':r['scenario']['capture_start'],
            'elapsed_seconds':r['elapsed_seconds'],'first_progress_seconds':r['first_progress_seconds'],
            'tasks':len(chunks),'bundles':n,'warmup_bundles':warm,'warmup_share':warm/max(1,n),
            'notifications':sum(c['notifications'] for c in chunks),'run_id':r['run_id'],
            'worker_cpu_seconds':sum(c['cpu_seconds'] for c in chunks),
            'worker_elapsed_seconds':sum(c['elapsed_seconds'] for c in chunks),
            'aggregate_rss_mib':r['sampled_peak_total_memory_bytes']/2**20})
        with (OUT/(label+'_cpu_distribution.csv')).open('w',encoding='utf-8-sig',newline='') as f:
            writer=csv.writer(f);writer.writerow(('logical_cpu','pid','task_start','task_end','bundles_sampled'))
            for c in sorted(chunks,key=lambda x:x['task_start']):
                for cpu,count in sorted(c.get('logical_cpu_bundle_samples',{}).items(),key=lambda x:int(x[0])):
                    writer.writerow((cpu,c['pid'],c['task_start'],c['task_end'],count))
    comparisons={}
    def pair(left,right):
        if left in runs and right in runs:
            def load(label):return alerts(root/Path(runs[label]['alerts_csv']).parent)
            comparisons[left+'__'+right]=compare(load(left),load(right))
    for n in (12,14):pair('year_month_6_keyframe',f'year_month_{n}_keyframe')
    for label in runs:
        if label.endswith('_beginning'):pair(label.replace('_beginning','_keyframe'),label)
        if label.startswith('year_fortnight_') and label.endswith('_keyframe'):
            pair(label.replace('fortnight','month'),label)
    result={'measurements':rows,'comparisons':comparisons,
        'classification':'Same-size different worker/seek comparisons must be identical. MONTH vs FORTNIGHT differences are C: independent warmup/state boundaries; preserve full differing rows.'}
    (OUT/'year_analysis.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'runs':len(rows),'comparisons':{k:{f:v for f,v in r.items() if f not in ('added','removed')} for k,r in comparisons.items()}},ensure_ascii=False))
if __name__=='__main__':main()
