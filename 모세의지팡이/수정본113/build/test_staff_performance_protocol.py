"""Negative controls for timing adjudication; no production workload execution."""
import json
import pytest
import staff_performance_protocol as protocol


def sample(cpu):
    return {'metrics': {k: {'cpu_s':cpu} for k in protocol.SPEC['workloads']},
            'environment': {'same':True}, 'source_sha256': {'one':'hash'}}


@pytest.mark.parametrize('values,expected,stable', [([10,10.1,9.9,10.2,9.8],1.05,True),
                                                  ([10,10,10,10.3,10],1.06,True),
                                                  ([10,10,10,11,10],1.2,False)])
def test_calibration_uses_all_s0_samples_and_caps_instability(tmp_path,monkeypatch,values,expected,stable):
    monkeypatch.setattr(protocol,'OUT',tmp_path/'out')
    rows=iter([sample(v) for v in values])
    monkeypatch.setattr(protocol,'invoke',lambda label,path: next(rows))
    result=protocol.calibrate()
    lock=protocol.read(protocol.OUT/'locked_rule.json')
    assert lock['limit_ratio']==expected and lock['stable']==stable
    assert result==int(not stable)
    assert lock['candidate_seen_before_lock'] is False
    assert all(x['samples_cpu_s']==values for x in lock['calibration'].values())


@pytest.mark.parametrize('baseline,candidate,passed', [(10,10.4,True),(10,10.6,False),(12,12,False)])
def test_paired_cpu_gate_rejects_regression_and_environment_drift(tmp_path,monkeypatch,baseline,candidate,passed):
    monkeypatch.setattr(protocol,'OUT',tmp_path)
    monkeypatch.setattr(protocol,'program_hashes',lambda path: {'one':'hash'})
    lock={'spec':protocol.SPEC,'stable':True,'limit_ratio':1.05,
          'runner_sha256':protocol.sha(protocol.__file__),
          'scenario_sha256':protocol.sha(protocol.ROOT/'Part2/validation_suite/test_oz_fvg_optimization.py'),
          'baseline_source':{'one':'hash'},'environment':{'same':True},
          'calibration':{k:{'median':10} for k in protocol.SPEC['workloads']}}
    protocol.write_new(tmp_path/'locked_rule.json',lock)
    labels=[]
    def invoke(label,path):
        labels.append(label)
        return sample(baseline if label.endswith('_s0') else candidate)
    monkeypatch.setattr(protocol,'invoke',invoke)
    assert protocol.compare()==int(not passed)
    result=protocol.read(tmp_path/'comparison.json')
    assert result['pass']==passed
    assert labels==[f'pair_{i}_{kind}' for i in range(1,6) for kind in ('s0','candidate')]
    assert all(len(v['candidate_cpu_s'])==5 for v in result['metrics'].values())


@pytest.mark.parametrize('baseline,candidate,passed', [(10,10.4,True),(10,10.6,False),(12,12,False)])
def test_final_s5_s8_policy_preserves_each_workload_gate(baseline,candidate,passed):
    from staff_performance_policy import evaluate
    policy={'limits':{k:1.05 for k in protocol.SPEC['workloads']},
            'calibration':{k:{'median':10} for k in protocol.SPEC['workloads']},
            'environment':{'same':True},'baseline_source':{'one':'hash'}}
    result=evaluate(policy,[(sample(baseline),sample(candidate)) for _ in range(5)],{'one':'hash'})
    assert result['pass']==passed
    assert len(result['metrics'])==6


def test_final_policy_only_gates_s5_s8():
    from staff_performance_policy import POLICY
    policy=protocol.read(POLICY)
    assert policy['gating_stages']==['S5','S8']
    assert policy['reference_only_stages']==['S1','S2','S3','S4','S6','S7']
    assert policy['candidate_samples_before_freeze']==0
    assert policy['limits']=={'parser_650_rows':1.05,'request_0':1.15,'request_1':1.09,
                              'request_2':1.06,'request_3':1.09,'oz_fvg_live_seconds':1.06}
