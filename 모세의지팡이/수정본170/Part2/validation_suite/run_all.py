"""Sequential, failure-preserving replay. Does not fetch real broker data.

Run from the extracted package using its Python. Reference ZIPs are extracted
only below a new output directory; native/source test flags never touch the UI.
"""
from pathlib import Path
import argparse,json,os,subprocess,sys,time,zipfile
ROOT=Path(__file__).resolve().parents[1];SUITE=ROOT/'validation_suite'
p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--out',default=str(ROOT/'validation_replay'))
p.add_argument('--full',action='store_true',help='also replay native/OZ/component and cumulative 1d/7d benchmarks')
a=p.parse_args();out=Path(a.out).resolve();out.mkdir(parents=True,exist_ok=False)
status=[]
def run(name,args,timeout=1200):
    t=time.perf_counter();env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1',PYTHONUTF8='1',OPENBLAS_NUM_THREADS='1',OMP_NUM_THREADS='1');env.pop('PYTHONPATH',None)
    with (out/(name+'.log')).open('w',encoding='utf-8') as f:
        try:r=subprocess.run([sys.executable,*map(str,args)],cwd=ROOT,env=env,stdout=f,stderr=subprocess.STDOUT,timeout=timeout);code=r.returncode
        except subprocess.TimeoutExpired:code='TIMEOUT'
        except OSError as exc:code=str(exc)
    status.append({'name':name,'returncode':code,'wall_s':time.perf_counter()-t})
    (out/'process_status.json').write_text(json.dumps(status,indent=2),encoding='utf-8');print(status[-1],flush=True)
run('unit_tests',['-m','pytest',str(SUITE),'-q'],600)
if a.full:
    refs={}
    for name in ('baseline','step1','step2','step3','ipc_before'):
        target=out/'references'/name;target.mkdir(parents=True)
        with zipfile.ZipFile(SUITE/'references'/(name+'.zip')) as z:
            for info in z.infolist():
                path=(target/info.filename).resolve()
                if not path.is_relative_to(target.resolve()):raise ValueError('Unsafe reference archive path')
            z.extractall(target)
        refs[name]=target
    for name,root in [('before',refs['baseline']),('after',ROOT)]:
        run('gate_'+name,[SUITE/'bench_gate.py','--root',root,'--out',out/f'gate_{name}.json'])
    for family in ('PRICE','RSI','STO','DI'):
        run('native_'+family,[SUITE/'validate_percentile_long.py','--family',family,'--out',out/f'native_{family}.json'],1800)
    run('band_only',[SUITE/'bench_band_only.py','--out',out/'band_only.json'],600)
    for name,root in [('before',refs['step2']),('after',ROOT)]:
        cmd=[SUITE/'bench_oz.py','--root',root,'--out',out/f'oz_{name}.json','--count','2000','--rows','650']
        if name=='before':cmd+=['--reference']
        run('oz_'+name,cmd,1800)
    for n in range(1,4):
        for name,root in [('before',refs['ipc_before']),('after',ROOT)]:
            cmd=[SUITE/'bench_ipc.py','--root',root,'--out',out/f'ipc_{name}_{n}.json','--count','120']
            if name=='after':cmd+=['--batch']
            run(f'ipc_{name}_{n}',cmd)
    for name,root in [*((n,refs[n]) for n in ('baseline','step1','step2','step3')),('step4',ROOT)]:
        run('cumulative_'+name,[SUITE/'bench_cumulative.py','--root',root,'--stage','replay_'+name,'--out',out/f'cumulative_{name}.json','--archive-parent',out/'raw'],1800)
    for name,root in [('baseline',refs['baseline']),('step1',refs['step1']),('step4',ROOT)]:
        run('trade_'+name,[SUITE/'bench_cumulative.py','--root',root,'--stage','replay_'+name,'--out',out/f'trade_{name}.json','--archive-parent',out/'raw','--trade-only'],1800)
    for name,root in [('before',refs['baseline']),('after',ROOT)]:
        run('contracts_'+name,[SUITE/'validate_contracts.py','--root',root,'--out',out/f'contracts_{name}.json'],1800)
    for name,root in [('before',refs['baseline']),('after',ROOT)]:
        run('hma_tick_'+name,[SUITE/'validate_hma_tick.py','--root',root,'--out',out/f'hma_tick_{name}.json','--archive-parent',out/'raw','--stage','replay_'+name],1800)
    run('exact_comparison',[SUITE/'compare_results.py','--evidence',out,'--out',out/'exact_equality_summary.json'])
print('Finished. Inspect every JSON row status and exact digests, not only process exit codes.')
sys.exit(0 if all(s['returncode']==0 for s in status) else 1)
