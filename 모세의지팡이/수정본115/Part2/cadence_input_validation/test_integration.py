"""Actual coordinator/isolated workers, plus nonempty state and cancellation oracles.

Short SPECIAL inputs deliberately lack adequate native history: their empty
alerts prove execution/input/result equality, NOT positive trading-signal parity.
Nonempty WATCH/generic/OZ component cases are asserted separately below.
"""
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import tempfile

import numpy as np
import pytest

from generic_backtest import runner
from generic_backtest.contracts import ROOT, GenericRunConfig
from generic_backtest.canonical import encode, file_hash, read_json
from generic_backtest.results import read_lines, verify_result
from generic_backtest.history.cache import GenericArchiveReader
from generic_backtest.watch.compiler import compile_watch
from generic_backtest.ipc import Cancelled
from cadence_input_validation.reference_reader import LegacyArchiveReader
from cadence_input_validation.test_input import archive_from_parts, native_rows, CAL, INSTRUMENT


@pytest.fixture(scope='module')
def replay_fixture():
    parent=ROOT/'generic_runs';parent.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='cadence-input-test-',dir=parent) as directory:
        root=Path(directory)
        ms=sorted([*range(0,660_000,10_000),60_000,60_000,300_000])
        rows=native_rows(ms)
        rows['bid']=100.+np.sin(np.arange(len(rows))*.8)*2
        rows['ask']=rows['bid']+.1;rows['last']=rows['bid']
        raw,_=archive_from_parts(root/'raw',[rows],[0,660*10**9])
        yield root,raw,len(rows)


def config_for(raw,plugin,mode,trade=False):
    parameters={}
    if plugin.startswith('WATCH_'):
        command={'WATCH_BAR':'5분봉 마감 알려줘',
                 'WATCH_OZ':'1시간 추세 상승이고 5분 올존 알려줘',
                 'WATCH_TREND_OZ':'15분 추세 상승이고 1분 올존 알려줘'}[plugin]
        plan=compile_watch(command,'XAUUSD+')
        assert plan['status']=='READY'
        parameters={'plan_json':json.dumps(plan,ensure_ascii=False)};plugin='WATCH_UI_V1'
    return GenericRunConfig(mode='TRADE' if trade else 'ALERT_ONLY',plugin_id=plugin,
        plugin_sha256=file_hash(ROOT/'BACKTEST_SPECIAL'/f'{plugin}.py'),parameters=parameters,
        instrument=INSTRUMENT,start_ns=0,end_ns=660*10**9,calendar=CAL,archive=str(raw),
        evaluation_mode=mode,session_filter={'enabled':False},
        stop_variants=({'id':'distance','kind':'PRICE_DISTANCE','value':.5},) if trade else None,
        target_variants=({'id':'rr','kind':'RR_MULTIPLE','value':1.5},) if trade else None,
        outcome_price_basis='CHART_PRICE' if trade else None,
        resources={'max_history_bars':100000,'max_occurrences':100000,
                   'worker_timeout_seconds':120,'max_ipc_bytes':32*1024*1024,'disk_cache':False})


def semantic(value):
    # Only nondeterministic diagnostic durations are excluded. All state, event,
    # consumption, cursor and timing-of-market fields are compared unchanged.
    if isinstance(value,dict):
        return {k:semantic(v) for k,v in value.items() if not k.endswith('_seconds')}
    if isinstance(value,list):return [semantic(v) for v in value]
    return value


def compare_runs(root,config,monkeypatch,tag,*,cancel_after=None):
    outputs=[];payloads=[]
    original_payload=runner.context_payload
    for name,reader in (('legacy',LegacyArchiveReader),('current',GenericArchiveReader)):
        if cancel_after is not None:
            class CancelReader(reader):
                def __iter__(self):
                    for n,tick in enumerate(super().__iter__(),1):
                        if n==cancel_after:raise Cancelled()
                        yield tick
            selected=CancelReader
        else:selected=reader
        monkeypatch.setattr(runner,'GenericArchiveReader',selected)
        captured=[]
        def payload(*args,**kwargs):
            result=original_payload(*args,**kwargs)
            captured.append(hashlib.sha256(encode(result)).hexdigest())
            return result
        monkeypatch.setattr(runner,'context_payload',payload)
        output=root/f'{tag}-{name}'
        result=runner.GenericRunCoordinator(config).run(output)
        manifest=verify_result(output)
        outputs.append((result,manifest,output));payloads.append(captured)
    assert payloads[0]==payloads[1] and payloads[0]
    old,new=outputs
    assert old[0]['status']==new[0]['status']
    files={p.name for p in old[2].glob('*.jsonl')}
    assert files=={p.name for p in new[2].glob('*.jsonl')}
    for filename in files:
        assert (old[2]/filename).read_bytes()==(new[2]/filename).read_bytes(),filename
    for key in ('execution','evaluation_schedule','last_token','requirements',
                'conditional_computation','worker_finish_diagnostics'):
        assert semantic(old[1]['metadata'].get(key))==semantic(new[1]['metadata'].get(key)),key
    assert read_json(old[2]/'dashboard.json')==read_json(new[2]/'dashboard.json')
    return new,len(payloads[1])


@pytest.mark.parametrize('mode',['TICK','ONE_MINUTE_CLOSE','LIVE_PARITY'])
# Part2 BACKTEST_SPECIAL1..7 copies were removed: SPECIALs now run as the Part1 LIVE code
# (validation_suite/test_part1_host_parity.py). The generic plugin paths remain covered here.
@pytest.mark.parametrize('plugin',['WATCH_OZ','WATCH_TREND_OZ','WATCH_BAR'])
def test_all_shipped_paths_modes_input_and_result_exact(replay_fixture,monkeypatch,mode,plugin):
    root,raw,count=replay_fixture
    config=config_for(raw,plugin,mode)
    (result,manifest,output),calls=compare_runs(root,config,monkeypatch,plugin+'-'+mode)
    assert result['status']=='SUCCEEDED'
    meta=manifest['metadata']
    assert meta['execution']['last_committed_raw_cursor']==count
    if mode=='ONE_MINUTE_CLOSE':
        schedule=meta['evaluation_schedule']
        expected='5m' if plugin in ('WATCH_OZ','WATCH_BAR') else '1m'
        assert schedule['evaluation_base_tf']==expected
        closes=2 if expected=='5m' else 10
        assert schedule['roles']['SIGNAL']['observed_base_closes']==closes
        assert schedule['roles']['SIGNAL']['strategy_callback_opportunities']['MEASURE']==closes
        assert calls==closes
    if mode=='TICK':assert calls==count
    if mode=='LIVE_PARITY':
        assert meta['evaluation_schedule']['counts']['raw_ticks']==count
        assert meta['evaluation_schedule']['counts']['evaluations']==calls
    if plugin=='WATCH_BAR':
        events=read_lines(output/'alerts.jsonl')
        # Epoch-zero is used to avoid fabricating a months-long warmup. The
        # unchanged primitive treats open_ns==0 as its bootstrap sentinel.
        assert len(events)==1
        assert [e['observed_at_ns'] for e in events]==[600*10**9]


GENERIC_SOURCE='''from generic_backtest.contracts import StrategyRequirements, AlertIntent, GenericEntryIntent
BACKTEST_PLUGIN = {
    'api_version': 'GENERIC_BACKTEST_PLUGIN_V1', 'plugin_id': 'INPUT_CONTRACT_TEST_ONLY',
    'display_name': 'Test fixture only', 'plugin_version': '1.0.0',
    'supported_modes': ['ALERT_ONLY', 'TRADE'], 'parameters_schema': {}, 'risk_anchors': {}}
def requirements(params, selection):
    req=StrategyRequirements(('1m',), {'1m':0})
    return {'signal':req, 'entry':req}
class Signal:
    def on_observation(self,ctx):
        n=ctx.token.source_ordinal
        if n%3:return ()
        alert=AlertIntent(str(n),'LONG' if n%2 else 'SHORT',ctx.symbol,signal_tf='1m')
        return (alert,alert)
class Entry:
    def on_observation(self,ctx,alerts):
        return tuple(GenericEntryIntent(str(a['event_id']),ctx.symbol,a['direction'],
            ctx.quote['bid'],ctx.token.now_ns,ctx.token,'OBSERVED_QUOTE',{'field':'bid'},
            (a['event_id'],),'1m',{}) for a in alerts)
def create_alert_strategy(params):return Signal()
def create_entry_strategy(params):return Entry()
'''


@pytest.fixture(scope='module')
def generic_plugin():
    path=ROOT/'BACKTEST_SPECIAL/INPUT_CONTRACT_TEST_ONLY.py'
    assert not path.exists()
    path.write_text(GENERIC_SOURCE,encoding='utf-8')
    try:yield path.stem
    finally:path.unlink(missing_ok=True)


@pytest.mark.parametrize('mode',['TICK','ONE_MINUTE_CLOSE','LIVE_PARITY'])
@pytest.mark.parametrize('trade',[False,True])
def test_generic_nonempty_alerts_trades_and_duplicates(replay_fixture,generic_plugin,monkeypatch,mode,trade):
    root,raw,count=replay_fixture
    config=config_for(raw,generic_plugin,mode,trade)
    (result,manifest,output),_=compare_runs(root,config,monkeypatch,'generic-'+mode+'-'+str(trade))
    assert result['status']=='SUCCEEDED'
    alerts=read_lines(output/'alerts.jsonl')
    assert alerts
    assert len({a['event_id'] for a in alerts})==len(alerts)
    if mode=='TICK':assert {a['direction'] for a in alerts}=={'LONG','SHORT'}
    if trade:
        entries=read_lines(output/'entries.jsonl');outcomes=read_lines(output/'outcomes.jsonl')
        assert entries and outcomes
        assert len(entries)==len(alerts)


@pytest.mark.parametrize('mode',['TICK','ONE_MINUTE_CLOSE','LIVE_PARITY'])
@pytest.mark.parametrize('trade',[False,True])
def test_actual_workers_cancelled_committed_prefix_exact(replay_fixture,generic_plugin,monkeypatch,mode,trade):
    root,raw,_=replay_fixture
    config=config_for(raw,generic_plugin,mode,trade)
    (result,manifest,output),_=compare_runs(root,config,monkeypatch,
        'partial-'+mode+'-'+str(trade),cancel_after=40)
    assert result['status']=='PARTIAL'
    meta=manifest['metadata']['execution']
    assert meta['last_committed_raw_cursor']==39
    assert read_lines(output/'events.jsonl')[-1]['type']=='CANCELLED'


@pytest.mark.parametrize('direction',['LONG','SHORT'])
@pytest.mark.parametrize('reason',['COMPLETE','OPPOSITE','EXPIRE','GAP','DELIVERY_RETRY'])
def test_nonempty_oz_transitions_full_state_consumption_and_event_order(tmp_path,direction,reason):
    # Indicator frames are explicitly controlled component fixtures, not a claim
    # of SPECIAL1-7 signals generated from adequately warmed broker history.
    from generic_backtest.watch.engines.oz import HistoricalOZEngine,OZEnvironment
    from generic_backtest.watch.engines.inputs import exact_tree
    from validation_suite.test_oz import targeted_frames
    from validation_suite.oz_fixtures import full_state
    start=1736726400*1000
    rows=native_rows([start,start+1,start+60_000,start+120_000,start+180_000])
    raw,_=archive_from_parts(tmp_path/'raw',[rows],[start*1_000_000,(start+180_001)*1_000_000])
    options={'max_cross_bars':0} if reason=='EXPIRE' else {}
    engines=[HistoricalOZEngine('TEST','NORMAL','BREAKER',**options) for _ in range(2)]
    recorded=[];raw_alerts=[]
    for engine,reader in zip(engines,(LegacyArchiveReader,GenericArchiveReader)):
        records=[];alerts=[]
        for tick in reader(raw):
            i=tick.source_ordinal-1
            if reason=='GAP' and i==1:engine.mark_observation_gap()
            frames,token=targeted_frames(direction,i,opposite=reason=='OPPOSITE')
            now=tick.time_msc*1_000_000
            token=replace(token,now_ns=now,source_ordinal=tick.source_ordinal)
            for frame in frames.values():frame.attrs.update(available_at_ns=now,source_ordinal=tick.source_ordinal)
            env={('1m',direction):OZEnvironment(frozenset({'input-equivalence'}))}
            events=engine.observe(frames,token,environments=env,
                delivery_succeeded=not(reason=='DELIVERY_RETRY' and i==2))
            alerts.extend(e for e in events if e['kind']=='OZ_LOCAL_ALERT')
            records.append((exact_tree(events),exact_tree(full_state(engine))))
        recorded.append(records);raw_alerts.append(alerts)
    assert recorded[0]==recorded[1]
    alerts=raw_alerts[1]
    if reason in ('COMPLETE','DELIVERY_RETRY'):
        assert len(alerts)==1 and alerts[0]['direction']==direction
