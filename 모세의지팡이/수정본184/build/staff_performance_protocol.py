"""S1-S8 CPU protocol. Calibrate on frozen S0 only, then lock before candidates."""
from __future__ import annotations
import argparse
import gc
import hashlib
import importlib.util
import json
import logging
import math
import os
from pathlib import Path
import platform
import statistics
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / '검증결과/staff_s1/performance_protocol'
BASE = ROOT / '검증결과/staff_s0/baseline_input/Part1'
sys.path.insert(0, str(ROOT / 'Part2'))

SPEC = {
    'version': 'STAFF_CPU_S1_S8_v2', 'stages': ['S1','S2','S3','S4','S5','S6','S7','S8'],
    'affinity': 'worker only: pin to lowest logical CPU in the inherited affinity mask; normal priority',
    'calibration': '5 independent frozen-S0 processes; no candidate sampled before lock',
    'comparison': '5 pairs, S0 then candidate, sequential fresh processes',
    'metric': 'candidate median process CPU / paired frozen-S0 median process CPU, per workload',
    'workloads': {'parser_650_rows': 1000, 'request_0': 250, 'request_1': 250,
                  'request_2': 250, 'request_3': 250, 'oz_fvg_live_seconds': 240},
    'warmup': '10 publications and 5 requests per group; gc.collect before each STAFF batch; GC enabled',
    'full_path': 'unchanged 240-second test_oz_fvg_optimization.run, including same 8 Watches',
    'limit_rule': 'ceil to 0.01 of 1 + max(0.05, 2 * largest absolute S0 CPU deviation from workload median)',
    'max_limit': 1.10,
    'unstable_calibration': 'If derived limit exceeds 1.10, fail; do not silently widen or resample',
    'environment_guard': 'same Python/numpy/pandas/platform; paired S0 medians within locked tolerance of calibration',
    'wall_latency': 'retain as diagnostics, not additional independent noisy pass/fail samples',
    'no_selection': 'retain all 5 samples; no outlier removal; no best-of retries',
}

def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def read(path):
    return json.loads(Path(path).read_text('utf-8'))

def write_new(path, value):
    with Path(path).open('x', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)

def program_hashes(part1):
    return {p.relative_to(part1 / 'program').as_posix(): sha(p)
            for p in (part1 / 'program').rglob('*') if p.is_file()}

def pin_worker_cpu():
    """Control only this benchmark worker; never change another application."""
    import ctypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.GetCurrentProcess.restype = ctypes.c_void_p
    kernel.GetProcessAffinityMask.argtypes = [ctypes.c_void_p,ctypes.POINTER(ctypes.c_size_t),ctypes.POINTER(ctypes.c_size_t)]
    kernel.SetProcessAffinityMask.argtypes = [ctypes.c_void_p,ctypes.c_size_t]
    handle = kernel.GetCurrentProcess()
    process_mask, system_mask = ctypes.c_size_t(), ctypes.c_size_t()
    if not kernel.GetProcessAffinityMask(handle,ctypes.byref(process_mask),ctypes.byref(system_mask)):
        raise ctypes.WinError(ctypes.get_last_error())
    mask = process_mask.value & -process_mask.value
    if not kernel.SetProcessAffinityMask(handle,mask):
        raise ctypes.WinError(ctypes.get_last_error())
    if not kernel.GetProcessAffinityMask(handle,ctypes.byref(process_mask),ctypes.byref(system_mask)):
        raise ctypes.WinError(ctypes.get_last_error())
    assert process_mask.value == mask
    return mask

def worker(part1, output):
    affinity = pin_worker_cpu()
    import numpy as np
    import pandas as pd
    from part1_host import runtime, capture
    from staff_golden.scenario import market, SYMBOL, START, INDICATORS, MA_CASES
    source = program_hashes(part1)
    data = market()
    payload = data.payload('1m', START + 239)
    requests = [{'symbol': SYMBOL, 'timeframes': ['1m'], 'indicators': list(names)}
                for names in ((), ('PRICE','RSI','STO','DI'), INDICATORS, INDICATORS + MA_CASES[-1])]
    metrics = {}
    with runtime.Part1Runtime(symbols=[SYMBOL], start_epoch=START+239, specials=['SPECIAL7'],
                              part1_root=part1, log_level=logging.CRITICAL) as rt:
        logging.disable(logging.CRITICAL)
        wires = [capture.pack_wire(SYMBOL, '1m', *payload, snapshot=i+1) for i in range(1010)]
        for wire in wires[:10]:
            rt.publish(wire)
        for request in requests:
            for _ in range(5):
                assert 'error' not in rt._staff_handle(request)
        batches = [('parser_650_rows', rt.publish, wires[10:])]
        batches += [(f'request_{i}', rt._staff_handle, [r]*250) for i,r in enumerate(requests)]
        for name, fn, inputs in batches:
            gc.collect()
            latencies = []
            wall, cpu = time.perf_counter(), time.process_time()
            for value in inputs:
                start = time.perf_counter()
                reply = fn(value)
                latencies.append((time.perf_counter()-start)*1000)
                if name.startswith('request'):
                    assert 'error' not in reply
            metrics[name] = {'cpu_s': time.process_time()-cpu, 'wall_s': time.perf_counter()-wall,
                             'p50_ms': float(np.percentile(latencies,50)),
                             'p95_ms': float(np.percentile(latencies,95)), 'calls': len(inputs)}
            print(name, metrics[name]['cpu_s'], flush=True)
    # Load exactly the existing scenario; retain all Fact reuse and outcome checks.
    spec = importlib.util.spec_from_file_location('oz_perf_scenario', ROOT/'Part2/validation_suite/test_oz_fvg_optimization.py')
    scenario = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(scenario)
    result = scenario.run(data, part1_root=part1, feed='live')
    metrics['oz_fvg_live_seconds'] = {'cpu_s': result['cpu'], 'wall_s': result['wall'], 'seconds': 240}
    assert result['oz_fact_memo']['reused'] > result['oz_fact_memo']['computed'] > 0
    assert result['fvg_structure']['reuses'] > result['fvg_structure']['builds'] > 0
    assert result['special'] and result['finals'] and len(result['oz_state']) == 16
    assert program_hashes(part1) == source
    write_new(output, {'metrics': metrics, 'source_sha256': source,
              'scenario': {k: result[k] for k in ('oz_fact_memo','fvg_structure','special')},
              'environment': {'python': platform.python_version(), 'numpy': np.__version__,
                              'pandas': pd.__version__, 'platform': platform.platform(),
                              'affinity_mask':affinity},
              'protocol_sha256': sha(__file__)})

def invoke(label, part1):
    print('START '+label, flush=True)
    with (OUT/(label+'.log')).open('x', encoding='utf-8') as log:
        subprocess.run([sys.executable,'-X','utf8','-B',str(Path(__file__)), 'worker',
                        '--part1',str(part1),'--output',str(OUT/(label+'.json'))],
                       stdout=log, stderr=subprocess.STDOUT, check=True, cwd=ROOT,
                       env={**os.environ, 'PYTHONDONTWRITEBYTECODE':'1','PYTHONHASHSEED':'0'})
    print('DONE '+label, flush=True)
    return read(OUT/(label+'.json'))

def calibrate():
    OUT.mkdir(exist_ok=False)
    write_new(OUT/'preregistered.json', {'spec':SPEC,'runner_sha256':sha(__file__),
              'scenario_sha256':sha(ROOT/'Part2/validation_suite/test_oz_fvg_optimization.py')})
    samples = [invoke(f'calibration_s0_{i+1}', BASE) for i in range(5)]
    spread = {}
    for key in SPEC['workloads']:
        values = [s['metrics'][key]['cpu_s'] for s in samples]
        median = statistics.median(values)
        spread[key] = {'samples_cpu_s':values,'min':min(values),'max':max(values),'median':median,
                       'max_abs_relative_deviation':max(abs(x/median-1) for x in values)}
    largest = max(s['max_abs_relative_deviation'] for s in spread.values())
    limit = math.ceil((1 + max(.05, 2*largest))*100 - 1e-9)/100
    lock = {'spec':SPEC,'calibration':spread,'max_deviation':largest,'limit_ratio':limit,
            'stable':limit <= SPEC['max_limit'],'runner_sha256':sha(__file__),
            'scenario_sha256':sha(ROOT/'Part2/validation_suite/test_oz_fvg_optimization.py'),
            'baseline_source':samples[0]['source_sha256'],'environment':samples[0]['environment'],
            'candidate_seen_before_lock':False}
    assert all(s['source_sha256']==lock['baseline_source'] and s['environment']==lock['environment'] for s in samples)
    write_new(OUT/'locked_rule.json',lock)
    print(json.dumps({'stable':lock['stable'],'limit_ratio':limit,'calibration':spread},indent=2),flush=True)
    return int(not lock['stable'])

def compare():
    lock = read(OUT/'locked_rule.json')
    assert lock['stable'] and lock['runner_sha256']==sha(__file__) and lock['spec']==SPEC
    assert lock['scenario_sha256']==sha(ROOT/'Part2/validation_suite/test_oz_fvg_optimization.py')
    samples = []
    for i in range(5):
        samples.append((invoke(f'pair_{i+1}_s0',BASE),invoke(f'pair_{i+1}_candidate',ROOT/'Part1')))
    values = {}
    for key in SPEC['workloads']:
        before = [a['metrics'][key]['cpu_s'] for a,b in samples]
        after = [b['metrics'][key]['cpu_s'] for a,b in samples]
        ratio = statistics.median(after)/statistics.median(before)
        drift = statistics.median(before)/lock['calibration'][key]['median']
        values[key] = {'s0_cpu_s':before,'candidate_cpu_s':after,'ratio':ratio,
                       's0_median':statistics.median(before),'candidate_median':statistics.median(after),
                       'baseline_drift_ratio':drift,'environment_stable':1/lock['limit_ratio']<=drift<=lock['limit_ratio'],
                       'pass':ratio<=lock['limit_ratio']}
    environment = all(a['environment']==b['environment']==lock['environment'] and
                      a['source_sha256']==lock['baseline_source'] and
                      b['source_sha256']==program_hashes(ROOT/'Part1') for a,b in samples)
    report = {'version':SPEC['version'],'rule_sha256':sha(OUT/'locked_rule.json'),
              'limit_ratio':lock['limit_ratio'],'metrics':values,'environment_match':environment,
              'pass':environment and all(v['pass'] and v['environment_stable'] for v in values.values())}
    write_new(OUT/'comparison.json',report)
    print(json.dumps(report,indent=2),flush=True)
    return int(not report['pass'])

if __name__ == '__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('command',choices=['worker','calibrate','compare'])
    parser.add_argument('--part1',type=Path)
    parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    if args.command=='worker': worker(args.part1,args.output)
    else: raise SystemExit(calibrate() if args.command=='calibrate' else compare())
