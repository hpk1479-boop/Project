"""Core regressions on a selected project; real dependencies unless unavailable."""
from pathlib import Path
import argparse,json,os,sys


def main():
    p=argparse.ArgumentParser();p.add_argument('project',type=Path);p.add_argument('output',type=Path)
    p.add_argument('--new',action='store_true');p.add_argument('--cpu',type=int);args=p.parse_args()
    root=args.project.resolve();out=args.output.resolve();out.mkdir(parents=True,exist_ok=True)
    sys.dont_write_bytecode=True;os.environ['PYTHONDONTWRITEBYTECODE']='1'
    os.environ['MOSES_LOG_DIRECTORY']=str(out/'logs')
    if args.cpu is not None and hasattr(os,'sched_setaffinity'):os.sched_setaffinity(0,{args.cpu})
    sys.path.insert(0,str(root/'build/optimization37'))
    from support import install_warehouse
    module=install_warehouse(root)
    import pytest
    targets=['test_composer_input32.py','test_engine_optimization.py','test_schema_cleanup.py',
             'test_event_perf1.py','test_shared_oz32.py','test_event_e2_boundaries.py',
             'test_event_e2_domains.py','test_event_e2_composition.py','test_event_e2_startup.py',
             'test_alert_stats.py','test_stop_virtual33.py','test_control_reconnect.py',
             'test_event_e1.py','test_event_e2_oz_pilot.py','test_event_e3.py',
             'test_data_selection.py','test_numpy_processors.py','test_part2_optimization37.py',
             'test_oz_rewrite.py','test_parallel_oz.py','test_composer_input32.py']
    targets=list(dict.fromkeys(targets))
    if args.new:targets=['test_part2_optimization38.py']
    (out/'scope.json').write_text(json.dumps({'targets':targets,'without_duckdb':module._validation_without_duckdb,
                                             'parent_sql_tested':False},ensure_ascii=False,indent=2))
    return pytest.main(['-q','-p','no:cacheprovider','--tb=short','--junitxml='+str(out/'results.xml'),
                        '--basetemp='+str(out/'tmp'),*[str(root/'tests'/t) for t in targets]])


if __name__=='__main__':raise SystemExit(main())
