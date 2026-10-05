"""Rerun only audit tests blocked by the newly required schema dependency."""
import sys, unittest
from staff_s6_evidence import *
sys.path.insert(0,str(ROOT/'Part1/audit'))
from run_tests import Result
raw=read(OUT/'regressions/summary.json')[0]['audit']
ids=[r['test'] for r in raw['tests'] if r['status']=='ERROR' and "No module named 'staff_schema'" in r.get('detail','')]
assert ids
suite=unittest.defaultTestLoader.loadTestsFromNames(ids)
result=unittest.TextTestRunner(verbosity=2,resultclass=Result).run(suite)
write(OUT/'audit_import_diagnosis.json',{'reason':'STAFF now imports staff_schema at module load; audit isolated harness loaded it afterwards. Reorder the same modules without removing assertions.',
    'original_report_preserved':'regressions/summary.json','selected_ids':ids,
    'tests_run':result.testsRun,'failures':len(result.failures),'errors':len(result.errors),
    'tests':result.records})
print('Audit dependency diagnosis:',result.testsRun,'tests',len(result.failures),'failures',len(result.errors),'errors')
