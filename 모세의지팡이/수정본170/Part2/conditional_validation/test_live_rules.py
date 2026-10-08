"""Read-only Part1 oracle: production definitions are parsed, never app-imported.

Set PART1_REFERENCE to the user-owned Part1 folder. No Part1 source is bundled.
Transport, persistence, external SWEEP and LIVE polling phase are NOT certified
by this numerical/state-rule test. Its external eligibility is an explicit input.
"""
import ast
import os
from pathlib import Path
from types import SimpleNamespace
import pytest
from generic_backtest.contracts import ROOT
from generic_backtest.watch.engines import oz as oz_module,oz_rules
from generic_backtest.watch.engines.inputs import exact_tree
from validation_suite.oz_fixtures import PROFILES,full_state

@pytest.fixture(scope='module')
def oracle():
    path=Path(os.environ.get('PART1_REFERENCE',str(ROOT.parent/'모세의지팡이 Part1')))/'program/monitor_OZ.py'
    if not path.is_file():pytest.skip('Part1 read-only oracle is not supplied with the Part2 artifact')
    source=ast.parse(path.read_text(encoding='utf-8-sig'))
    monitor=next(n for n in source.body if isinstance(n,ast.ClassDef) and n.name=='OZMonitor')
    methods=set(oz_rules._OZRules.__dict__)
    nodes=[n for n in monitor.body if isinstance(n,ast.FunctionDef) and n.name in methods]
    assert len(nodes)==30
    # Exclude durable I/O decorator only; all LIVE method bodies stay unchanged.
    namespace=dict(vars(oz_rules));namespace['_checkpoint_observation']=lambda f:f
    cls=ast.ClassDef(name='OZMonitor',bases=[],keywords=[],body=nodes,decorator_list=[])
    module=ast.fix_missing_locations(ast.Module(body=[cls],type_ignores=[]))
    exec(compile(module,str(path),'exec'),namespace)
    rules=namespace['OZMonitor']
    engine_ast=ast.parse(Path(oz_module.__file__).read_text())
    engine=next(n for n in engine_ast.body if isinstance(n,ast.ClassDef) and n.name=='HistoricalOZEngine')
    engine.name='OracleEngine';engine.bases=[ast.Name(id='OracleRules',ctx=ast.Load())]
    env=dict(vars(oz_module),OracleRules=rules)
    exec(compile(ast.fix_missing_locations(ast.Module(body=[engine],type_ignores=[])),'oracle_transport_only','exec'),env)
    cls=env['OracleEngine']
    class Wrapped(cls):
        def __init__(self,*args,**kwargs):
            super().__init__(*args,**kwargs)
            self.watch=SimpleNamespace(validate_external_true_b0=lambda sym,tf,d,vm,tm,p:self._external_eligible(tf,d,p))
    return Wrapped

@pytest.mark.parametrize('profile',PROFILES)
@pytest.mark.parametrize('direction',['LONG','SHORT'])
def test_part1_actual_rules_nonempty_events_and_full_state(oracle,profile,direction):
    from validation_suite.test_oz import targeted_frames
    a=oracle('TEST',*profile,use_numpy=False)
    b=oz_module.HistoricalOZEngine('TEST',*profile,use_numpy=True)
    env={('1m',direction):oz_module.OZEnvironment(frozenset({'gate'}))}
    alerts=[]
    for i in range(5):
        data,token=targeted_frames(direction,i)
        ea=a.observe(data,token,environments=env);eb=b.observe(data,token,environments=env)
        assert exact_tree(ea)==exact_tree(eb)
        assert exact_tree(full_state(a))==exact_tree(full_state(b))
        alerts.extend(e for e in eb if e['kind']=='OZ_LOCAL_ALERT')
    assert len(alerts)==1 and alerts[0]['direction']==direction

@pytest.mark.parametrize('reason',['OPPOSITE','EXPIRE','GAP','DELIVERY_RETRY'])
def test_part1_cancellation_expiry_and_consumption(oracle,reason):
    from validation_suite.test_oz import targeted_frames
    options={'max_cross_bars':0} if reason=='EXPIRE' else {}
    a=oracle('TEST','NORMAL','BREAKER',use_numpy=False,**options)
    b=oz_module.HistoricalOZEngine('TEST','NORMAL','BREAKER',use_numpy=True,**options)
    for i in range(5):
        if reason=='GAP' and i==1:a.mark_observation_gap();b.mark_observation_gap()
        data,token=targeted_frames('LONG',i,opposite=reason=='OPPOSITE')
        env={} if i==0 else {('1m','LONG'):oz_module.OZEnvironment(frozenset({'gate'}))}
        delivery=not(reason=='DELIVERY_RETRY' and i==2)
        assert exact_tree(a.observe(data,token,environments=env,delivery_succeeded=delivery))==exact_tree(
            b.observe(data,token,environments=env,delivery_succeeded=delivery))
        assert exact_tree(full_state(a))==exact_tree(full_state(b))
