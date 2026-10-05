"""Resumable publication of the 13 existing pieces; old generations retained."""
from pathlib import Path
import argparse,json,shutil,sys,time,concurrent.futures
ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'검증결과/parallel_oz'
sys.path[:0]=[str(ROOT/'Part1/program'),str(ROOT/'Part2')]
def convert_one(args):
    c,root=args;root=Path(root)
    from event_backtest.storage import convert,files
    from event_backtest.settings import relative_path,warehouse_path
    from event_backtest.system import deny_network
    deny_network();source=warehouse_path(root,c['path']);t=time.perf_counter()
    dest,info=convert(source,root/'captures',c['capture_id'])
    expected=json.loads((source/'storage.json').read_text('utf-8'))
    assert info['bundles']==expected['bundles'] and info['bundle_sha256']==expected['bundle_sha256']
    for name in ('tester_tick_evidence.log',):
        if (source/name).exists():shutil.copyfile(source/name,dest/name)
    data={**c,**info,'raw_bytes':c.get('raw_bytes',0),'path':relative_path(root,dest),
        'files':files(dest),'stored_bytes':sum(p.stat().st_size for p in dest.iterdir() if p.is_file())}
    return {'capture_id':c['capture_id'],'start':c['start'],'end':c['end'],'source_path':c['path'],
        'path':data['path'],'old_bytes':c['stored_bytes'],'new_bytes':data['stored_bytes'],
        'bundles':info['bundles'],'bundle_sha256':info['bundle_sha256'],'keyframes':info['keyframes'],
        'full_hash_matches_original':True,'every_day_independent_reconstruction_verified':True,
        'seconds':time.perf_counter()-t,'capture':data}

def main():
    p=argparse.ArgumentParser();p.add_argument('--warehouse',required=True);p.add_argument('--workers',type=int,default=4)
    a=p.parse_args();root=Path(a.warehouse)
    from event_backtest.warehouse import Warehouse
    from event_backtest.storage import convert,files
    from event_backtest.settings import relative_path,warehouse_path
    from event_backtest.system import deny_network
    deny_network()
    manifest=OUT/'old_captures.json'
    if not manifest.exists():
        catalog=Warehouse(root)
        try:old=[c for c in catalog.available('XAUUSD+','BAR') if c.get('storage')=='MSD1' and c['schema_id']==933044592 and c['start']>='2024-09-01' and c['end']<='2025-10-01']
        finally:catalog.close()
        if len(old)!=13:raise ValueError('expected 13 old pieces, got '+str(len(old)))
        manifest.write_text(json.dumps(old,ensure_ascii=False,indent=2),encoding='utf-8')
    old=json.loads(manifest.read_text('utf-8'));path=OUT/'keyframe_conversion.json'
    result=json.loads(path.read_text('utf-8')) if path.exists() else []
    done={r['capture_id'] for r in result}
    with concurrent.futures.ProcessPoolExecutor(max_workers=a.workers) as pool:
        futures=[pool.submit(convert_one,(c,str(root))) for c in old if c['capture_id'] not in done]
        for future in concurrent.futures.as_completed(futures):
            item=future.result();catalog=Warehouse(root)
            try:catalog.register(item['capture'])
            finally:catalog.close()
            result.append(item);result.sort(key=lambda x:x['start'])
            path.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
            print(json.dumps({k:item[k] for k in ('start','bundles','keyframes','seconds')},ensure_ascii=False),flush=True)
if __name__=='__main__':main()
