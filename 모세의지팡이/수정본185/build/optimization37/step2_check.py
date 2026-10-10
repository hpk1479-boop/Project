"""Exercise the real find_capture with an isolated SELECT-result test double.

No database implementation is simulated. Only the already-read catalog row is
supplied; filesystem checks, hashing, caching and metadata comparison are real.
"""
from pathlib import Path
from types import SimpleNamespace
import argparse,hashlib,json,os,shutil,statistics,sys,time
from support import load_warehouse


def main():
    ap=argparse.ArgumentParser();ap.add_argument('before',type=Path);ap.add_argument('after',type=Path);ap.add_argument('output',type=Path);a=ap.parse_args()
    sys.dont_write_bytecode=True;a.output.mkdir(parents=True,exist_ok=True)
    old=load_warehouse(a.before,'event_backtest.warehouse_old37');new=load_warehouse(a.after,'event_backtest.warehouse_new37')
    root=a.output/'warehouse';capture=root/'captures/a';capture.mkdir(parents=True,exist_ok=True)
    path=capture/'capture.delta2';path.write_bytes(b'0123456789abcdef'*65536*16) # 16 MiB
    (capture/'complete.txt').write_text('complete')
    digest=hashlib.sha256(path.read_bytes()).hexdigest()
    data={'capture_id':'a','path':'captures/a','files':{'capture.delta2':digest}}
    def catalog(module,base=root):
        class DB:
            def execute(self,sql,params):
                assert sql=='SELECT path,metadata FROM captures WHERE capture_id=?'
                return SimpleNamespace(fetchone=lambda:None if params!=['a'] else ('captures/a',json.dumps(data)))
        w=module.Warehouse.__new__(module.Warehouse);w.root=base;w.db=DB();return w
    original_hash=new.file_hash;calls=[]
    def counted(p):calls.append(str(p));return original_hash(p)
    new.file_hash=counted;new._capture_hash_for_stat.cache_clear()
    assert catalog(new).find_capture('a')==data
    assert catalog(new).find_capture('a')==data
    assert len(calls)==1 # distinct plan/run catalog instances reuse the digest
    checks={'separate_catalog_reuse':True,'hash_calls_for_two_catalogs':len(calls)}
    # The cached hash is compared to CURRENT metadata, not cached approval.
    data['files']['capture.delta2']='0'*64
    assert catalog(new).find_capture('a') is None and len(calls)==1
    data['files']['capture.delta2']=digest;checks['changed_expected_digest_rejected']=True
    # Missing completion marker cannot be bypassed by a cached digest.
    (capture/'complete.txt').unlink();assert catalog(new).find_capture('a') is None
    (capture/'complete.txt').write_text('complete');checks['completion_marker_checked_on_hit']=True
    before=path.stat();path.write_bytes(b'x'*before.st_size)
    os.utime(path,ns=(before.st_atime_ns,before.st_mtime_ns+1_000_000))
    assert catalog(new).find_capture('a') is None and len(calls)==2
    checks['same_size_new_mtime_rehashed_and_corruption_rejected']=True
    data['files']['capture.delta2']=original_hash(path)
    assert catalog(new).find_capture('a')==data
    before=path.stat();path.write_bytes(b'x'*(before.st_size+1));os.utime(path,ns=(before.st_atime_ns,before.st_mtime_ns))
    assert catalog(new).find_capture('a') is None and len(calls)==3
    checks['new_size_same_mtime_rehashed']=True
    # A relocated warehouse gets a distinct canonical path key.
    data['files']['capture.delta2']=original_hash(path);moved=a.output/'relocated';shutil.copytree(root,moved)
    assert catalog(new,moved).find_capture('a')==data and len(calls)==4
    checks['relocated_warehouse_verified']=True
    path.unlink();assert catalog(new).find_capture('a') is None
    assert catalog(new).find_capture('a',verify=False)==data
    assert catalog(new).find_capture('unknown') is None
    checks['deleted_file_and_verify_false']=True
    # Changing the file inside the hash operation must not fill the cache.
    path.write_bytes(b'first');new._capture_hash_for_stat.cache_clear()
    def change_during_hash(p):
        h=original_hash(p);Path(p).write_bytes(b'second larger');return h
    new.file_hash=change_during_hash
    assert catalog(new).find_capture('a') is None
    assert new._capture_hash_for_stat.cache_info().currsize==0
    new.file_hash=original_hash
    data['files']['capture.delta2']=original_hash(path)
    assert catalog(new).find_capture('a')==data
    checks['concurrent_change_rejected_without_cache_entry']=True
    assert new._capture_hash_for_stat.cache_info().maxsize==1024
    checks['bounded_cache_1024']=True
    # 16 MiB file; time initial plan verification separately from repeated run.
    path.write_bytes(b'0123456789abcdef'*65536*16);data['files']['capture.delta2']=original_hash(path)
    samples={k:{'plan':[],'run':[]} for k in ('before','after')}
    for rep in range(7):
        for label,module in ([('before',old),('after',new)] if rep%2==0 else [('after',new),('before',old)]):
            if label=='after':new._capture_hash_for_stat.cache_clear()
            for phase in ('plan','run'):
                w=catalog(module);began=time.perf_counter();assert w.find_capture('a')==data
                samples[label][phase].append(time.perf_counter()-began)
    medians={k:{p:statistics.median(v) for p,v in phases.items()} for k,phases in samples.items()}
    result={'step':2,'checks':checks,'benchmark_file_bytes':path.stat().st_size,'samples_seconds':samples,
            'median_seconds':medians,'database_integration_tested':False,
            'cache_scope':'same Python process; distinct CLI processes each verify initially',
            'cache_key':'resolved path, st_size, st_mtime_ns'}
    (a.output/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False),flush=True)
    shutil.rmtree(root);shutil.rmtree(moved)

if __name__=='__main__':main()
