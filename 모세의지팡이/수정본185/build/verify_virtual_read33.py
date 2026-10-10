"""Offline virtual-reader measurements; never runs a backtest or records MT5."""
from pathlib import Path
import argparse, csv, datetime as dt, hashlib, importlib.util, json, sys, time
from collections import Counter
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'검증결과/virtual_read_optimized'
sys.path[:0]=[str(ROOT/'Part2'),str(ROOT/'Part1/program')]

def main():
    p=argparse.ArgumentParser();p.add_argument('version',choices=['before','after']);p.add_argument('--period',choices=['week','month','both'],default='both');a=p.parse_args()
    from event_backtest.system import deny_network
    deny_network()
    from event_backtest import settings,keyframes,virtual_entry
    from event_backtest.runner import runtime_config
    if a.version=='before':
        for name in ('virtual_source','virtual_entry'):
            spec=importlib.util.spec_from_file_location('event_backtest.'+name,OUT/'before_sources'/f'{name}.py')
            mod=importlib.util.module_from_spec(spec);sys.modules[spec.name]=mod;spec.loader.exec_module(mod)
        virtual_entry=sys.modules['event_backtest.virtual_entry']
    warehouse=ROOT.parent.with_name(ROOT.parent.name+'_warehouse')
    files=list((warehouse/'captures/XAUUSD+/BAR/2025/09').glob('*/capture.delta2'));assert len(files)==1
    file=files[0];index=keyframes.read_index(file)
    offsets={d['offset']:str(dt.datetime.fromtimestamp(d['day']*86400,dt.timezone.utc).date()) for d in index['days']}
    dates=Counter();original_gzip=keyframes.gzip.GzipFile
    class TrackedGzip(original_gzip):
        def __init__(self,*args,**kw):
            obj=kw.get('fileobj')
            if isinstance(obj,keyframes.LimitedReader):dates[offsets[obj.file.tell()]]+=1
            super().__init__(*args,**kw)
    keyframes.gzip.GzipFile=TrackedGzip
    original_relative=settings.relative_path
    settings.relative_path=lambda root,path:Path(path).resolve().relative_to(OUT).as_posix() if Path(path).resolve().is_relative_to(OUT) else original_relative(root,path)
    for period,end,alerts in (
        ('week','2025-09-08',ROOT/'검증결과/live_daily_partition_virtual/week_single/chunk_000/alerts.csv'),
        ('month','2025-10-01',ROOT/'검증결과/stop_virtual_parallel/partition_MONTH/chunk_000/alerts.csv')):
        if a.period not in (period,'both'):continue
        dates.clear();dest=OUT/a.version/period/'same_run';dest.mkdir(parents=True,exist_ok=True)
        s=settings.scenario(symbol='XAUUSD+',start='2025-09-01',end=end,strategies=['SPECIAL1'],overlap_trading_days=0)
        captures=[{'path':file.parent.relative_to(warehouse).as_posix(),'start':'2025-09-01','end':'2025-10-01'}]
        began=time.perf_counter()
        result=virtual_entry.calculate(alerts,captures,warehouse,s,runtime_config(s),dest)
        wall=time.perf_counter()-began
        report={'period':period,'seconds':wall,'restores':dict(dates),'restore_count':sum(dates.values()),
            'unique_dates':len(dates),'alerts':result['eligible_signals'],'read_bundles':result['read_bundles'],
            'csv_sha256':hashlib.sha256((dest/'virtual_trades.csv').read_bytes()).hexdigest(),
            'summary_sha256':hashlib.sha256((dest/'virtual_summary.csv').read_bytes()).hexdigest()}
        (dest.parent/'measurement.json').write_text(json.dumps(report,indent=2),encoding='utf8')
        (dest/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
        print(json.dumps(report),flush=True)

if __name__=='__main__':main()
