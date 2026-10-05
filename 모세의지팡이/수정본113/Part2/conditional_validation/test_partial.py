"""Synthetic integration tests: real coordinator, aggregation, journal, risk and result verifier."""
import io
import json
import tempfile
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import pytest

from generic_backtest import runner
from generic_backtest.canonical import plain, read_json
from generic_backtest.contracts import GenericRunConfig, InstrumentSpec, StrategyRequirements, ROOT
from generic_backtest.calendar import CalendarRegistry
from generic_backtest.ipc import Cancelled, JobProtocol
from generic_backtest.results import read_lines, verify_result
from validation_suite.integration_fixtures import archive, START, CAL, INSTRUMENT


@pytest.fixture
def harness(monkeypatch):
    parent=ROOT/'generic_runs';parent.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='conditional-test-',dir=parent) as directory:
        root=Path(directory);archive(root/'raw', days=1/24, step_seconds=20)
        req=StrategyRequirements(('1m',),{'1m':0})
        record=SimpleNamespace(sha256='0'*64,metadata={'plugin_version':'fixture','risk_anchors':[]})
        state={'cutoff':None,'cancelled':False,'mid':False}
        def setup(config):
            instrument=InstrumentSpec(**config.instrument)
            return record,{},instrument,CalendarRegistry(CAL),{},req,req,{}
        class Worker:
            def __init__(self,record,params,selection,role,resources):self.role=role
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def mark_observation_gap(self):pass
            def observe(self,ctx,alerts=(),op='observe'):
                if op=='finish':return {}
                if op=='warmup':return ()
                now=ctx['token']['now_ns'];key=str(now)
                if state['cutoff'] is not None and now>=state['cutoff']:state['cancelled']=True
                if self.role=='SIGNAL':
                    value={'event_key':key,'direction':'LONG','symbol':'TEST','signal_tf':'1m','label':'synthetic'}
                    if state['cancelled'] and state['mid']:
                        def interrupted():
                            yield value
                            raise Cancelled()
                        return interrupted()
                    return (value,)
                return ({'entry_key':key,'symbol':'TEST','direction':'LONG',
                    'entry_price':ctx['quote']['bid'],'entry_time_ns':now,'decision_token':ctx['token'],
                    'price_basis':'OBSERVED_QUOTE','price_evidence':{'field':'bid'},
                    'parent_alert_ids':tuple(x['event_id'] for x in alerts),'entry_tf':'1m','risk_anchors':{}},)
        monkeypatch.setattr(runner,'load_setup',setup)
        monkeypatch.setattr(runner,'StrategyWorker',Worker)
        config=GenericRunConfig(mode='TRADE',plugin_id='SYNTHETIC_TEST_ONLY',plugin_sha256='0'*64,
            parameters={},instrument=INSTRUMENT,start_ns=START,end_ns=START+3600*10**9,
            calendar=CAL,archive=str(root/'raw'),session_filter={'enabled':False},
            stop_variants=({'id':'price','kind':'PRICE_DISTANCE','value':.5},),
            target_variants=({'id':'rr','kind':'RR_MULTIPLE','value':1.5},),outcome_price_basis='CHART_PRICE',
            resources={'max_history_bars':100000,'max_occurrences':1000000,'disk_cache':False})
        def check():
            if state['cancelled']:raise Cancelled()
        yield root,config,state,check


@pytest.mark.parametrize('fraction',[.1,.3,.5,.8])
@pytest.mark.parametrize('mode',['ALERT_ONLY','TRADE'])
def test_cancelled_prefix(harness,fraction,mode):
    root,config,state,check=harness;config=replace(config,mode=mode)
    full=runner.GenericRunCoordinator(config).run(root/'full')
    full_alerts=read_lines(Path(full['result'])/'alerts.jsonl')
    state['cutoff']=START+int(3600*10**9*fraction)
    partial=runner.GenericRunCoordinator(config,cancel=check).run(root/'partial')
    assert partial['status']=='PARTIAL'
    manifest=verify_result(partial['result']);execution=manifest['metadata']['execution']
    end=execution['computed_interval'][1]
    assert end<=state['cutoff']
    assert read_lines(root/'partial/alerts.jsonl')==[a for a in full_alerts if a['observed_at_ns']<end]
    assert execution['progress_percent']<fraction*100
    assert not (root/'partial/incomplete.json').exists()
    if mode=='TRADE':
        outcomes=read_lines(root/'partial/outcomes.jsonl')
        assert all(o['exit_token'] is None or o['exit_token']['now_ns']<end for o in outcomes)
        confirmed=[o for o in read_lines(root/'full/outcomes.jsonl') if o['status'] in ('WIN','LOSS') and o['exit_token']['now_ns']<end]
        assert [o for o in outcomes if o['status'] in ('WIN','LOSS')]==confirmed
    # A fresh run has neither cancelled state nor truncated populations.
    state.update(cancelled=False,cutoff=None)
    again=runner.GenericRunCoordinator(config,cancel=check).run(root/'again')
    assert read_lines(Path(again['result'])/'alerts.jsonl')==full_alerts


def test_incomplete_observation_is_rolled_back(harness):
    root,config,state,check=harness
    state.update(cutoff=START+400*10**9,mid=True)
    result=runner.GenericRunCoordinator(config,cancel=check).run(root/'interrupted')
    manifest=verify_result(result['result'])
    alerts=read_lines(root/'interrupted/alerts.jsonl')
    assert alerts and max(a['observed_at_ns'] for a in alerts)<state['cutoff']
    journal=read_lines(root/'interrupted/events.jsonl')
    assert [e['payload'] for e in journal if e['type']=='ALERT']==alerts
    assert journal[-1]['type']=='CANCELLED'
    assert manifest['metadata']['execution']['last_committed_at_ns']<state['cutoff']


def test_empty_prefix_and_serialized_commit(harness):
    root,config,state,check=harness
    state['cancelled']=True
    protocol=JobProtocol('test',io.StringIO());protocol.cancelled=True
    result=runner.GenericRunCoordinator(config,cancel=protocol.check,
        commit=protocol.commit,commit_partial=protocol.commit_partial).run(root/'empty')
    assert result['status']=='PARTIAL' and protocol.committed
    dashboard=read_json(root/'empty/dashboard.json')
    assert dashboard['alerts']['count']==0
    assert dashboard['alerts']['buckets']=={'day':[],'iso_week':[],'month':[]}
    assert dashboard['execution']['progress_percent']==0
    protocol.accept_cancel({'command':'CANCEL','job_id':'test','request_id':'late'})
    assert 'TOO_LATE' in protocol.output.getvalue()


def test_cancel_wins_after_compute_before_result_commit(harness):
    root,config,state,check=harness
    def late_cancel(callback):raise Cancelled()
    result=runner.GenericRunCoordinator(config,commit=late_cancel).run(root/'late')
    assert result['status']=='PARTIAL'
    verify_result(result['result'])
    assert read_lines(root/'late/events.jsonl')[-1]['type']=='CANCELLED'
    outcomes=read_lines(root/'late/outcomes.jsonl')
    assert not any(o['status']=='OPEN_END_OF_DATA' for o in outcomes)
    assert any(o['status']=='OPEN_CANCELLED' for o in outcomes)
