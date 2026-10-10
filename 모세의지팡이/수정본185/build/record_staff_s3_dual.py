"""One G2 pass checks legacy and new client for every case, records new client.

No baseline/expected files are written. Existing S0 recorder and scenario are
unchanged; the injected host is only used in this verification subprocess.
"""
import json
from pathlib import Path
import sys
import pandas as pd

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'Part2'))
from part1_host import runtime

comparisons=0
class DualRuntime(runtime.Part1Runtime):
    def __init__(self,**kwargs):
        super().__init__(**kwargs)
        client=self.modules['staff_snapshot'].SnapshotClient(transport=self.staff_server.dispatch_multipart)
        self.compat=self.modules['staff_compat'].StaffCompat(client)

    def _staff_handle(self,request):
        global comparisons
        before=super()._staff_handle(request)
        if str(request.get('kind','')).upper() in ('PING','SOURCE_HEALTH','SET_WONBI_SIGMA'):
            return before
        after=self.compat.request(request)
        assert before.keys()==after.keys(), (request,before.keys(),after.keys())
        for key,value in before.items():
            if isinstance(value,pd.DataFrame):
                pd.testing.assert_frame_equal(value,after[key],check_exact=True)
                assert value.attrs==after[key].attrs
            else:
                assert value==after[key],(request,before,after)
        comparisons+=1
        return after

runtime.Part1Runtime=DualRuntime
from staff_golden.__main__ import main
code=main()
output=Path(sys.argv[sys.argv.index('--out')+1])
(output/'dual_path_comparison.json').write_text(json.dumps({'equal':True,'comparisons':comparisons,
    'recorded_path':'SNAPSHOT JSON multipart + staff_compat','reference_path':'unchanged legacy handler'}),encoding='utf-8')
raise SystemExit(code)
