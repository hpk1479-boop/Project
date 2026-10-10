"""Only the stale transport-boundary test is rerun; keep the full report intact."""
import json
import sys
import unittest
from staff_s4_evidence import ROOT,OUT,write,sha

sys.path.insert(0,str(ROOT/'Part1/audit'))
from run_tests import Result
from test_baseline import Baseline
case='test_pipe_roundtrip_features_and_copy'
result=unittest.TextTestRunner(verbosity=2,resultclass=Result).run(unittest.TestSuite([Baseline(case)]))
write(OUT/'audit_diagnosed_rerun.json',{'reason':'S4a raw-only TREND request cannot be used as the legacy wonbi/ATR consumer. All original assertions retained at their correct boundaries; added raw-only and compat copy checks.',
    'production_changed':False,'expected_changed':False,'tests':result.records,
    'test_file':'Part1/audit/test_baseline.py','test_sha256':sha(ROOT/'Part1/audit/test_baseline.py')})
inventory={p.relative_to(ROOT).as_posix():sha(p) for p in (ROOT/'Part1').rglob('*')
    if p.is_file() and '__pycache__' not in p.parts and 'results' not in p.parts
    and p.name!='special_settings.json' and p.suffix!='.ex5'}
write(ROOT/'build/part1_immutable_sha256.json',dict(sorted(inventory.items())))
assert result.wasSuccessful()
