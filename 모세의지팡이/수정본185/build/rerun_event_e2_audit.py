"""Only the failed audit items affected by the missing dependency copy."""
import sys,unittest
from event_e2_common import *
sys.path.insert(0,str(ROOT/'Part1/audit'))
import run_tests
attempt=sys.argv[1] if len(sys.argv)>1 else '1'
original=(read(OUT/'regressions/summary.json')[0]['audit']['tests'] if attempt=='1'
          else read(OUT/('audit_focused'+str(int(attempt)-1)+'.json'))['tests'])
names=sorted({r['test'].split(' (')[0] for r in original if r['status']=='ERROR' or (attempt=='1' and r['status']=='FAIL')})
if len(sys.argv)>2:names=sys.argv[2:]
suite=unittest.defaultTestLoader.loadTestsFromNames(names)
result=unittest.TextTestRunner(verbosity=2,resultclass=run_tests.Result).run(suite)
write(OUT/('audit_focused'+attempt+'.json'),{'reason':'E2 clock/memory dependency files missing from isolated audit loader; no assertions removed',
    'selected_failed_tests':names,'tests_run':result.testsRun,'failures':len(result.failures),
    'errors':len(result.errors),'tests':result.records})
