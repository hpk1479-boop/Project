"""Run revision48's explicitly scoped offline checks; never start LIVE services.

Usage: python build/validate_oz48.py [--no-ui]
Linux UI checks use Xvfb when available; Windows uses its desktop session.
DuckDB warehouse replay, native Windows pipe/MT5 and historical version26 tests
are not part of this runner. Existing pytest.ini exclusions are retained.
"""
from __future__ import annotations
import argparse,json,os,platform,shutil,subprocess,sys,tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'검증결과/OZ레짐슈퍼제거48'
CORE=['tests/test_oz_profiles48.py','tests/test_special5_profiles48.py','tests/test_oz_regime_super_removed48.py',
      'tests/test_oz_rewrite.py','tests/test_parallel_oz.py','Part3/tests']
REGRESSION=['tests/test_event_e1.py','tests/test_event_e2_boundaries.py','tests/test_event_e2_composition.py',
 'tests/test_event_e2_domains.py','tests/test_event_e2_startup.py','tests/test_numpy_processors.py',
 'tests/test_engine_optimization.py','tests/test_fact_optimization42.py','tests/test_array_validity43.py',
 'tests/test_composer_input32.py','tests/test_event_perf1.py']

def main()->int:
    args=argparse.ArgumentParser(description=__doc__);args.add_argument('--no-ui',action='store_true');options=args.parse_args()
    OUT.mkdir(parents=True,exist_ok=True)
    env=dict(os.environ);env['PYTHONPATH']=os.pathsep.join(str(ROOT/p) for p in ('tests/offline48','Part1/program','Part2','tests','Part3'))
    env['PYTHONDONTWRITEBYTECODE']='1'
    jobs=[('requirements_run',CORE+['-k','not measured_worker_default_and_explicit_override and not reused_worker_moves_write_boundary_before_creating_next_job']),
          ('regression_run',REGRESSION+['-k','not native_windows_named_pipe_live_vs_replay']),
          ('part2_shared_run',['tests/test_part2_oz_cleanup48.py'])]
    result={'python':sys.version,'platform':platform.platform(),'network':'socket connections blocked by tests/offline48/sitecustomize.py',
            'scope':'offline engine input parity; no MT5 or Telegram transport','runs':[]}
    with tempfile.TemporaryDirectory(prefix='oz48-ui-') as directory:
        env.setdefault('LOCALAPPDATA',directory)
        if not options.no_ui:jobs.append(('ui_run',['tests/test_strategy_settings_ui31.py','tests/test_ui_slots.py','-k','not special5_exact_eight_inserted_lines']))
        for name,tests in jobs:
            cmd=[sys.executable,'-m','pytest',*tests,'-q','-p','no:cacheprovider']
            if name=='ui_run' and os.name!='nt':
                xvfb=shutil.which('xvfb-run')
                if not xvfb:
                    result['runs'].append({'name':name,'status':'NOT_RUN','reason':'no display/Xvfb'});continue
                cmd=[xvfb,'-a',*cmd]
            done=subprocess.run(cmd,cwd=ROOT,env=env,text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
            (OUT/(name+'.txt')).write_text(done.stdout.replace(str(ROOT),'.').replace(directory,'<LOCALAPPDATA>'),encoding='utf-8')
            result['runs'].append({'name':name,'command':[('python' if x==sys.executable else Path(x).name if str(x).endswith('xvfb-run') else str(x).replace(str(ROOT)+os.sep,'')) for x in cmd],'exit_code':done.returncode,'status':'PASS' if done.returncode==0 else 'FAIL',
                'summary':next((line for line in reversed(done.stdout.splitlines()) if 'passed' in line or 'failed' in line),'')})
            print(name+': '+result['runs'][-1]['summary'],flush=True)
    node=shutil.which('node')
    if node:
        js=subprocess.run([node,'--check',str(ROOT/'Part3/web/app.js')],capture_output=True,text=True)
        result['javascript_syntax']={'exit_code':js.returncode,'output':js.stdout+js.stderr}
    (OUT/'run_summary.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    return int(any(item.get('exit_code',0)!=0 for item in result['runs']) or result.get('javascript_syntax',{}).get('exit_code',0)!=0)

if __name__=='__main__':raise SystemExit(main())
