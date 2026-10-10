"""Fixed stage policy: performance gates only at S5 and S8 (user decision)."""
import ast
import hashlib
import json
import statistics
from pathlib import Path
import staff_performance_protocol as measurement

POLICY = measurement.ROOT / 'build/staff_performance_policy.json'

def scenario_digest():
    tree=ast.parse((measurement.ROOT/'Part2/validation_suite/test_oz_fvg_optimization.py').read_bytes())
    constants={'SYMBOL','START','PATTERN','SEED','WINDOW','SPECIALS','TRIGGERS','WATCHES'}
    selected=[ast.dump(n) for n in tree.body if
              (isinstance(n,ast.FunctionDef) and n.name in ('run','_plain')) or
              (isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id in constants for t in n.targets))]
    return hashlib.sha256(json.dumps(selected).encode()).hexdigest()

def evaluate(policy,pairs,current_source):
    assert len(pairs)==5
    metrics={}
    for key,limit in policy['limits'].items():
        before=[a['metrics'][key]['cpu_s'] for a,b in pairs]
        after=[b['metrics'][key]['cpu_s'] for a,b in pairs]
        median=statistics.median(before)
        ratio=statistics.median(after)/median
        drift=median/policy['calibration'][key]['median']
        metrics[key]={'s0_cpu_s':before,'candidate_cpu_s':after,'s0_median':median,
                      'candidate_median':statistics.median(after),'ratio':ratio,'limit':limit,
                      'baseline_drift_ratio':drift,'environment_stable':1/limit<=drift<=limit,
                      'pass':ratio<=limit}
    environment=all(a['environment']==b['environment']==policy['environment'] and
                    a['source_sha256']==policy['baseline_source'] and b['source_sha256']==current_source
                    for a,b in pairs)
    return {'metrics':metrics,'environment_match':environment,
            'pass':environment and all(v['pass'] and v['environment_stable'] for v in metrics.values())}

def compare(out,stage):
    policy=measurement.read(POLICY)
    assert stage in policy['gating_stages']
    assert measurement.sha(measurement.__file__)==policy['measurement_runner_sha256']
    assert scenario_digest()==policy['scenario_contract_sha256']
    measurement.OUT=Path(out)
    measurement.OUT.mkdir(parents=True,exist_ok=False)
    measurement.write_new(measurement.OUT/'policy.json',policy)
    pairs=[]
    for i in range(1,6):
        pairs.append((measurement.invoke(f'pair_{i}_s0',measurement.BASE),
                      measurement.invoke(f'pair_{i}_candidate',measurement.ROOT/'Part1')))
    report=evaluate(policy,pairs,measurement.program_hashes(measurement.ROOT/'Part1'))
    report.update(stage=stage,policy_sha256=measurement.sha(POLICY))
    measurement.write_new(measurement.OUT/'comparison.json',report)
    return int(not report['pass'])
