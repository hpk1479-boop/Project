"""Run selected existing regressions with the real worker module available.

When DuckDB is absent, only tests not opening a database are selected. This is
not a replacement for installing the application's normal dependencies.
"""
from pathlib import Path
import argparse,json,os,sys
from support import install_warehouse


def main():
    p=argparse.ArgumentParser();p.add_argument('project',type=Path);p.add_argument('output',type=Path);p.add_argument('--new-tests',action='store_true');a=p.parse_args()
    a.project=a.project.resolve();a.output=a.output.resolve();a.output.mkdir(parents=True,exist_ok=True)
    sys.dont_write_bytecode=True;os.environ['PYTHONDONTWRITEBYTECODE']='1';os.environ['MOSES_LOG_DIRECTORY']=str(a.output/'logs')
    if hasattr(os,'sched_getaffinity'):os.sched_setaffinity(0,{max(os.sched_getaffinity(0))})
    module=install_warehouse(a.project)
    import pytest
    targets=['tests/test_composer_input32.py','tests/test_engine_optimization.py','tests/test_schema_cleanup.py',
             'tests/test_event_perf1.py','tests/test_shared_oz32.py','tests/test_event_e2_boundaries.py',
             'tests/test_event_e2_domains.py','tests/test_event_e2_composition.py','tests/test_event_e2_startup.py',
             'tests/test_alert_stats.py','tests/test_stop_virtual33.py','tests/test_control_reconnect.py']
    if a.new_tests:targets=['tests/test_part2_optimization37.py']
    (a.output/'scope.json').write_text(json.dumps({'targets':targets,'without_duckdb':module._validation_without_duckdb,
           'database_integration_tested':False},ensure_ascii=False,indent=2),encoding='utf-8')
    rc=pytest.main(['-q','-p','no:cacheprovider','--tb=short','--junitxml='+str(a.output/'results.xml'),
                   '--basetemp='+str(a.output/'tmp'),*[str(a.project/t) for t in targets]])
    return rc

if __name__=='__main__':raise SystemExit(main())
