"""One-week diagnostic instrumentation; production reader/calculator unchanged."""
from pathlib import Path
import cProfile,csv,datetime as dt,json,pstats,sys,time
from collections import Counter
ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'검증결과/live_daily_partition_virtual'
sys.path[:0]=[str(ROOT/'Part2'),str(ROOT/'Part1/program')]

def main():
    import numpy as np
    from event_backtest.system import deny_network
    deny_network()
    from event_backtest import keyframes,virtual_entry,settings
    from event_backtest.runner import runtime_config
    warehouse=ROOT.parent.with_name(ROOT.parent.name+'_warehouse')
    paths=list((warehouse/'captures/XAUUSD+/BAR/2025/09').glob('*/capture.delta2'));assert len(paths)==1
    file=paths[0];index=keyframes.read_index(file)
    offsets={x['offset']:dt.datetime.fromtimestamp(x['day']*86400,dt.timezone.utc).date().isoformat() for x in index['days']}
    dates=Counter();original_gzip=keyframes.gzip.GzipFile
    class TrackedGzip(original_gzip):
        def __init__(self,*a,**kw):
            obj=kw.get('fileobj')
            if isinstance(obj,keyframes.LimitedReader):dates[offsets[obj.file.tell()]]+=1
            super().__init__(*a,**kw)
    keyframes.gzip.GzipFile=TrackedGzip
    original_relative=settings.relative_path
    settings.relative_path=lambda root,p:Path(p).resolve().relative_to(OUT).as_posix() if Path(p).resolve().is_relative_to(OUT) else original_relative(root,p)
    source=OUT/'week_single/chunk_000/alerts.csv';out=OUT/'virtual_week';out.mkdir(exist_ok=True)
    s=settings.scenario(symbol='XAUUSD+',start='2025-09-01',end='2025-09-08',strategies=['SPECIAL1'],overlap_trading_days=0)
    captures=[{'path':file.parent.relative_to(warehouse).as_posix(),'start':'2025-09-01','end':'2025-10-01'}]
    profile=cProfile.Profile();start=time.perf_counter();profile.enable()
    result=virtual_entry.calculate(source,captures,warehouse,s,runtime_config(s),out)
    profile.disable();wall=time.perf_counter()-start;stats=pstats.Stats(profile)
    def find(suffix,name):
        return sum(v[3] for (f,line,n),v in stats.stats.items() if f.replace('\\','/').endswith(suffix) and n==name)
    def own(suffix,name):
        return sum(v[2] for (f,line,n),v in stats.stats.items() if f.replace('\\','/').endswith(suffix) and n==name)
    buckets={
        'keyframe_restore':find('keyframes.py','read_index')+find('keyframes.py','decode_metadata')+find('delta.py','encode'),
        'delta_restore':find('delta.py','decode'),
        'm1_hma_extract':find('virtual_source.py','update'),
        'entry_and_exit_decisions':find('virtual_entry.py','observe')}
    buckets['other_read_gzip_hash_setup']=max(0,wall-sum(buckets.values()))
    rows=list(csv.DictReader((out/'virtual_trades.csv').open(encoding='utf-8-sig')))
    distributions={}
    for rr in virtual_entry.RATIOS:
        subset=[r for r in rows if float(r['rr'])==rr];closed=[r for r in subset if r['exit_time'] and r['entry_time']]
        def distribution(values):
            if not values:return {'count':0}
            return {'count':len(values),'min_minutes':min(values),'median_minutes':float(np.median(values)),
                'p90_minutes':float(np.percentile(values,90)),'max_minutes':max(values),
                'under_1h':sum(x<60 for x in values),'1h_to_1d':sum(60<=x<1440 for x in values),'at_least_1d':sum(x>=1440 for x in values)}
        distributions[str(rr)]={'from_entry':distribution([(int(r['exit_time'])-int(r['entry_time']))/60000 for r in closed]),
            'from_alert':distribution([(int(r['exit_time'])-int(r['alert_time']))/60000 for r in closed]),
            'unclosed':sum(r['result']=='UNCLOSED' for r in subset),'outcomes':dict(Counter(r['result'] for r in subset))}
    def safe(key):
        f,line,n=key;p=Path(f)
        if p.is_absolute():f=p.relative_to(ROOT).as_posix() if p.is_relative_to(ROOT) else 'runtime/'+p.name
        return (f,line,n)
    top=[{'file':safe(k)[0],'line':k[1],'function':k[2],'calls':v[1],'own_seconds':v[2],'cumulative_seconds':v[3]} for k,v in sorted(stats.stats.items(),key=lambda kv:kv[1][3],reverse=True)[:35]]
    stats.stats={safe(k):(v[0],v[1],v[2],v[3],{safe(c):value for c,value in v[4].items()}) for k,v in stats.stats.items()}
    stats.dump_stats(str(OUT/'virtual_week_profile.prof'))
    report={'period':['2025-09-01','2025-09-08'],'wall_seconds_profiled':wall,'eligible_signals':result['eligible_signals'],
        'observations_after_alert':result['read_bundles'],'buckets_seconds':buckets,
        'bucket_note':'keyframe=read_index+decode_metadata+bootstrap DeltaCodec.encode; delta=DeltaCodec.decode; extraction=virtual_source.update; decisions=VirtualEntry.observe including entry/wait; remainder includes gzip, hashes, stream overhead and setup',
        'unique_restored_dates':len(dates),'date_restore_sessions':sum(dates.values()),'duplicate_date_restores':sum(max(0,n-1) for n in dates.values()),
        'restores_per_date':dict(sorted(dates.items())),'duration_by_rr':distributions,'top_functions':top}
    (OUT/'virtual_week_profile.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k not in ('duration_by_rr','top_functions')},ensure_ascii=False),flush=True)

if __name__=='__main__':main()
