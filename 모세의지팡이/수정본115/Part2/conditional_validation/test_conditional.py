"""Demand-native equivalence at exact raw/publication prefixes (synthetic ticks)."""
import json
from dataclasses import replace
import pytest
from generic_backtest.conditional import ConditionalFeatureRegistry
from generic_backtest.features import OptionalFeatureRegistry
from generic_backtest.market import GenericMarketCore
from generic_backtest.calendar import CalendarRegistry
from generic_backtest.contracts import StrategyRequirements, GenericError
from generic_backtest.runner import context_payload
from generic_backtest.canonical import plain
from generic_backtest.context import AsOfContext, frozen
from generic_backtest import conditional_client as client
from generic_backtest.live_parity import observer_view
from generic_backtest.evaluation import TimeframeCloseGate,close_evaluation_view
from validation_suite.test_gate import tick, INST, CAL


def setup(mode='TICK'):
    req=StrategyRequirements(('1m','3m'),{'1m':40,'3m':40},features=(
        {'name':'H','kind':'HMA_OPEN','timeframe':'1m','period':6},)+tuple(
        {'name':family,'kind':'MOSES_PERCENTILE','timeframe':'3m','family':family} for family in ('PRICE','RSI','STO','DI')))
    core=GenericMarketCore(INST,CalendarRegistry(CAL),0,req.completed_lookback_by_tf,'TEST')
    eager=OptionalFeatureRegistry(req,'SIGNAL')
    lazy=ConditionalFeatureRegistry(req,'SIGNAL',core,evaluation_mode=mode,base_tf='1m' if mode=='ONE_MINUTE_CLOSE' else None)
    return req,core,eager,lazy


def bind(lazy,req,view,markers,op='observe'):
    payload=lazy.bind(context_payload(view,markers,req,'MEASURE'),op)
    ctx=AsOfContext.decode(payload);client.begin(ctx)
    return ctx


@pytest.mark.parametrize('mode',['TICK','ONE_MINUTE_CLOSE','LIVE_PARITY'])
def test_native_exact_prefix_replay_and_resume(monkeypatch,mode):
    req,core,eager,lazy=setup(mode)
    monkeypatch.setattr(client,'_rpc',lambda context,body:lazy.handle(dict(body,binding=dict(context._conditional))))
    client._receivers.clear()
    close_gate=TimeframeCloseGate(INST,core.calendar,'1m')
    observations=[];expected=[];demanded=0
    for i in range(1,151):
        # repeated timestamps and an intentionally observed quote spike only at 91
        t=tick((i//2)*30,i,90000. if i==91 else 100+(i%13))
        raw,change=core.step(t,gap=(i==87));lazy.on_raw(t,i==87,raw)
        if mode=='ONE_MINUTE_CLOSE':
            close=close_gate.on_tick(t)
            if close is None:continue
            view=close_evaluation_view(raw,'1m',close)
        else:view=raw
        eager.advance(view,change,materialize=False);markers=lazy.advance(view,change,materialize=False)
        if mode=='LIVE_PARITY':view=observer_view(view,view.token.now_ns+500000,1000+i)
        values=eager.export_snapshot(eager.snapshot(),view.token)
        ctx=bind(lazy,req,view,markers,op='resume_after_session_gap' if i==87 else 'observe')
        observations.append(ctx.token);expected.append(values)
        n=len(observations)
        # No indicator engine exists prior to the first real consumer demand.
        if n<12:
            assert not lazy.streams
        if n in (12,13,28,29,35):
            for name in values:
                assert ctx.feature(name)==frozen(values[name])
            demanded+=1
        if n in (16,17,32,33):
            restored=list(client.stage_observations(ctx,'OZ_TEST',tuple(values)))
            assert restored[-1][1] is True
            for old,current,gap in restored:
                j=observations.index(old.token)
                assert all(old.feature(name)==frozen(value) for name,value in expected[j].items())
                assert old.token.source_ordinal<=ctx.token.source_ordinal
                assert old.token.now_ns<=ctx.token.now_ns
    assert demanded>=3
    assert lazy.diagnostics()['native_opportunities']>=35
    assert lazy.streams['HISTORY:OZ_TEST'].history_cursor==33


def test_expired_or_future_request_is_rejected():
    req,core,eager,lazy=setup()
    for i in range(1,4):
        t=tick(i*60,i);view,change=core.step(t);lazy.on_raw(t,False,view)
        markers=lazy.advance(view,change)
        ctx=bind(lazy,req,view,markers)
        if i==2:expired=dict(ctx._conditional)
    with pytest.raises(GenericError,match='E_FUTURE_READ'):
        lazy.handle({'op':'feature','name':'PRICE','binding':expired})
    with pytest.raises(GenericError,match='E_FUTURE_READ'):
        lazy._get_stream('test',('PRICE',)).advance_to(999)
    assert lazy.diagnostics()['streams']['test']['native_invocations']==0


@pytest.mark.parametrize('plugin,backend',[
    ('WATCH_UI_V1','OZ_PERCENTILE_SOURCE_V1'),
    ('WATCH_UI_V1','BAR_CLOSE_V1'),('GENERIC_EXAMPLE_V1',None)])
def test_all_existing_cadences_are_allowed_without_conversion(plugin,backend):
    from types import SimpleNamespace
    from generic_backtest.conditional import validate_cadence
    for mode in ('ONE_MINUTE_CLOSE','TICK','LIVE_PARITY'):
        config=SimpleNamespace(plugin_id=plugin,parameters={'plan_json':json.dumps({'backend':backend})},
            evaluation_mode=mode)
        validate_cadence(config)
        assert config.evaluation_mode==mode


def test_close_reaches_normal_setup_instead_of_intrabar_rejection(monkeypatch,tmp_path):
    from types import SimpleNamespace
    from generic_backtest import runner
    config=SimpleNamespace(plugin_id='BACKTEST_SPECIAL1',parameters={},
        evaluation_mode='ONE_MINUTE_CLOSE',session_filter={'enabled':False})
    class ReachedNormalSetup(Exception):pass
    def setup(actual):
        assert actual is config
        raise ReachedNormalSetup()
    monkeypatch.setattr(runner,'load_setup',setup)
    with pytest.raises(ReachedNormalSetup):
        runner.GenericRunCoordinator(config).run(tmp_path/'must-not-exist')
    assert not (tmp_path/'must-not-exist').exists()
