"""Current worker/Part2 regressions; missing SQL is explicitly reported."""
from pathlib import Path
import argparse, json, os, sys


def main():
    p=argparse.ArgumentParser();p.add_argument('project',type=Path);p.add_argument('output',type=Path)
    a=p.parse_args();root=a.project.resolve();out=a.output.resolve();out.mkdir(parents=True,exist_ok=True)
    sys.dont_write_bytecode=True;os.environ['PYTHONDONTWRITEBYTECODE']='1'
    os.environ['MOSES_LOG_DIRECTORY']=str(out/'logs')
    if hasattr(os,'sched_setaffinity'):
        available=os.sched_getaffinity(0);selected=available & {2,3}
        if selected:os.sched_setaffinity(0,selected)
    sys.path.insert(0,str(root/'build/optimization37'))
    from support import install_warehouse
    module=install_warehouse(root)
    targets=['test_parallel_oz.py','test_part2_event_runner.py','test_stop_virtual33.py',
             'test_alert_stats.py','test_data_selection.py','test_ea_build_compatibility.py',
             'test_part2_optimization37.py','test_part2_optimization38.py','test_progress_build30.py']
    (out/'scope.json').write_text(json.dumps({'targets':targets,'without_duckdb':module._validation_without_duckdb,
        'parent_sql_tested':False},ensure_ascii=False,indent=2))
    import pytest
    return pytest.main(['-q','-p','no:cacheprovider','--tb=short','--junitxml='+str(out/'results.xml'),
                       '--basetemp='+str(out/'tmp'),*[str(root/'tests'/name) for name in targets]])


if __name__=='__main__':raise SystemExit(main())
