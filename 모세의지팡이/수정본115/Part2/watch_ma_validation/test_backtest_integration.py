"""Independent Part2 parser/PIT provider and real isolated-worker replay tests."""
from dataclasses import replace
from functools import lru_cache
from pathlib import Path
from types import SimpleNamespace
import json
import sys
import tempfile
import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from generic_backtest.watch.compiler import compile_watch
from generic_backtest.watch.ma import parse_ma_expression, feature_source_rows
from generic_backtest.watch.ma_features import WatchMAFeatures
from generic_backtest.watch.engines import trend_math
from generic_backtest.watch.engines.frames import hma_open_history
from generic_backtest.contracts import GenericError, GenericRunConfig
from generic_backtest.canonical import file_hash
from generic_backtest.runner import GenericRunCoordinator
from generic_backtest.results import read_lines, verify_result
from cadence_input_validation.test_input import archive_from_parts, native_rows, CAL, INSTRUMENT


@pytest.mark.parametrize('text',[
    '1분 SMA0 > 0', '1분 HMA 17 > 0', '1분 SMMA20 > 0',
    '1분 기울기(HMA17, 3) > 0', '1분 골든크로스(SMA20)',
    '1분 데드크로스(SMA20, ABC50)', '1분 정배열(SMA20)',
    '1분 역배열(기울기(HMA17), SMA20)',
])
def test_offline_parser_exposes_canonical_errors(text):
    with pytest.raises(GenericError) as error:
        compile_watch(text,'XAUUSD+')
    assert '오류' in str(error.value)


@pytest.mark.parametrize('expression,mode',[
    ('기울기(SMA17) > 0','LIVE'), ('기울기(HMA17) < 0','LIVE'),
    ('정배열(HMA17, EMA50, WMA100, SMA200)','LIVE'),
    ('골든크로스(SMA17, EMA37)','CLOSE'),
])
def test_compiler_feature_identity_and_warmup(expression,mode):
    from generic_backtest.watch.runtime import watch_requirements
    plan=compile_watch('1분 '+expression,'XAUUSD+')
    assert plan['status']=='READY' and plan['backend']=='MA_EXPRESSION_V1'
    assert plan['live_semantic']['evaluation_mode']==mode
    parsed=parse_ma_expression(expression)
    assert {f['name']:f['lookback'] for f in plan['ma_features']}==parsed.dependencies
    req=watch_requirements(json.dumps(plan))['signal']
    assert req.completed_lookback_by_tf['1m'] >= parsed.history_rows(mode)
    assert req.raw_tick_warmup_ns >= parsed.history_rows(mode)*60*10**9


def make_bars(count=720):
    return tuple(SimpleNamespace(open_ns=i*60*10**9, nominal_end_ns=(i+1)*60*10**9,
        open=100.+np.sin(i*.17)*3, close=101.+np.sin(i*.31)*4,
        quality='COMPLETE_PREFIX',state='COMPLETED',gap_before=False,last_ordinal=i+1)
        for i in range(count))


@pytest.mark.parametrize('name',['SMA17','WMA23','EMA37','HMA23','HMA17'])
def test_provider_matches_local_source_exactly(name):
    bars=make_bars()
    expr=parse_ma_expression(name+' > 0')
    provider=WatchMAFeatures({'test':'local-source'})
    token=SimpleNamespace(now_ns=720*60*10**9,source_ordinal=720)
    actual=provider.tails(bars,'1m',expr,token)[name]
    count=max(feature_source_rows(name),682 if name=='HMA17' else 0)
    family=name[:3];period=int(name[3:])
    values=np.array([b.close if family=='EMA' else b.open for b in bars[-count:]])
    expected=(np.asarray(hma_open_history(tuple(values),period),dtype=float) if name=='HMA17'
        else getattr(trend_math,family.lower())(pd.Series(values),period).to_numpy())[-4:]
    np.testing.assert_array_equal(actual.view('uint64'),expected.view('uint64'))
    assert provider.tails(bars,'1m',expr,token)[name] is actual


def test_provider_future_and_ordinal_guards():
    bars=make_bars(5);expr=parse_ma_expression('SMA2 > 0')
    with pytest.raises(GenericError,match='E_FUTURE_READ'):
        WatchMAFeatures({}).tails(bars,'1m',expr,SimpleNamespace(now_ns=4*60*10**9,source_ordinal=5))
    with pytest.raises(GenericError,match='E_FUTURE_READ'):
        WatchMAFeatures({}).tails(bars,'1m',expr,SimpleNamespace(now_ns=5*60*10**9,source_ordinal=4))


def test_ram_memo_period_identity_in_run_hit_and_new_run_cold(monkeypatch):
    from generic_backtest.fast.feature_memo import CommonFeatureMemo
    from generic_backtest.fast import memo_client
    parent=ROOT/'generic_runs';parent.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='watch-ma-memo-',dir=parent) as directory:
        root=Path(directory)
        memo=CommonFeatureMemo({'kind':'WATCH_MA_TEST_ONLY'},root=root)
        keys=[]
        def get(kind,tf,params,fingerprint):
            key={'kind':kind,'timeframe':tf,'indicator_parameters':params,'input_hash':fingerprint}
            keys.append(key)
            return memo.handle({'op':'get','key':key})
        def put(kind,tf,params,fingerprint,value):
            key={'kind':kind,'timeframe':tf,'indicator_parameters':params,'input_hash':fingerprint}
            return memo.handle({'op':'put','key':key,'value':value})
        monkeypatch.setattr(memo_client,'get',get);monkeypatch.setattr(memo_client,'put',put)
        bars=make_bars();token=SimpleNamespace(now_ns=720*60*10**9,source_ordinal=720)
        expr=parse_ma_expression('정배열(SMA20, SMA21, HMA17, HMA18, EMA50, EMA200)')
        cold=WatchMAFeatures({'source':'same'}).tails(bars,'1m',expr,token)
        assert memo.stats['misses']==memo.stats['writes']==6
        # Same run (same RAM memo): the shared values are reused exactly.
        warm=WatchMAFeatures({'source':'same'}).tails(bars,'1m',expr,token)
        assert memo.stats['hits']==6 and memo.stats['writes']==6
        for name in cold: np.testing.assert_array_equal(cold[name].view('uint64'),warm[name].view('uint64'))
        memo.close()
        # A new run never sees the previous run's memo: nothing was persisted.
        memo=CommonFeatureMemo({'kind':'WATCH_MA_TEST_ONLY'},root=root)
        again=WatchMAFeatures({'source':'same'}).tails(bars,'1m',expr,token)
        assert memo.stats['hits']==0 and memo.stats['misses']==memo.stats['writes']==6
        for name in cold: np.testing.assert_array_equal(cold[name].view('uint64'),again[name].view('uint64'))
        assert not any(root.iterdir())
        assert {k['indicator_parameters']['name'] for k in keys}==set(expr.dependencies)
        assert len({(k['indicator_parameters']['family'],k['indicator_parameters']['period']) for k in keys})==6
        memo.close()


@pytest.fixture(scope='module')
def replay_fixture():
    parent=ROOT/'generic_runs';parent.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='watch-ma-replay-',dir=parent) as directory:
        root=Path(directory)
        rows=native_rows(range(0,12*60*1000,20000));minutes=rows['time_msc']//60000
        rows['bid']=100+4*np.sin(minutes*.7)+.001*(rows['time_msc']%60000)/1000
        rows['ask']=rows['bid']+.1;rows['last']=rows['bid']
        raw,_=archive_from_parts(root/'raw',[rows],[0,12*60*10**9])
        yield root,raw


@lru_cache(maxsize=8)
def plan_for(expression):
    return compile_watch('1분 '+expression,'XAUUSD+')


def config(raw,expression,mode,cache=False):
    return GenericRunConfig(mode='ALERT_ONLY',plugin_id='WATCH_UI_V1',
        plugin_sha256=file_hash(ROOT/'BACKTEST_SPECIAL/WATCH_UI_V1.py'),
        parameters={'plan_json':json.dumps(plan_for(expression),ensure_ascii=False)},
        instrument=INSTRUMENT,start_ns=0,end_ns=12*60*10**9,
        calendar=CAL,archive=str(raw),evaluation_mode=mode,session_filter={'enabled':False},
        resources={'max_history_bars':100000,'max_occurrences':100000,'worker_timeout_seconds':120,
                   'max_ipc_bytes':32*1024*1024,'disk_cache':cache})


@pytest.mark.parametrize('mode',['TICK','ONE_MINUTE_CLOSE','LIVE_PARITY'])
@pytest.mark.parametrize('expression',['기울기(SMA3) > 0','골든크로스(SMA3, WMA5)'])
def test_real_worker_nonempty_watch_all_cadences(replay_fixture,mode,expression):
    root,raw=replay_fixture
    tag=('slope' if expression.startswith('기울기') else 'cross')+'-'+mode
    output=root/tag
    result=GenericRunCoordinator(config(raw,expression,mode)).run(output)
    assert result['status']=='SUCCEEDED',result
    manifest=verify_result(output)
    events=read_lines(output/'alerts.jsonl')
    assert events, 'fixture must produce positive alerts, not empty-parity evidence'
    timestamps=[e['observed_at_ns'] for e in events]
    assert timestamps==sorted(set(timestamps))
    assert all(0 <= t < 12*60*10**9 for t in timestamps)
    for event in events:
        details=event['diagnostics']
        assert details['semantic_event_time_ns'] <= event['observed_at_ns']
        assert details['source_ordinal']==event['decision_token']['source_ordinal']
        if expression.startswith('골든'):
            assert event['direction']=='LONG' and details['effective_detection']=='CLOSED_BAR'
            assert details['semantic_event_time_ns'] == details['bar_open_ns']+60*10**9
    assert manifest['metadata']['execution']['last_committed_raw_cursor']>0


def test_real_worker_cache_off_cold_warm_results_identical(replay_fixture):
    root,raw=replay_fixture;record=[]
    expression='역배열(SMA3, WMA5)'
    for tag,enabled in [('off',False),('cold',True),('warm',True)]:
        output=root/('cache-'+tag)
        result=GenericRunCoordinator(config(raw,expression,'TICK',cache=enabled)).run(output)
        assert result['status']=='SUCCEEDED'
        verify_result(output)
        record.append((output/'alerts.jsonl').read_bytes())
    assert record[0] and record[0]==record[1]==record[2]


def test_real_worker_cache_on_leaves_nothing_on_disk(replay_fixture):
    root,raw=replay_fixture
    cache_root=ROOT/'generic_cache'
    before=sorted(p for p in cache_root.rglob('*')) if cache_root.exists() else []
    spools=sorted(p.name for p in Path(tempfile.gettempdir()).glob('moses-*'))
    output=root/'ram-cache-on'
    result=GenericRunCoordinator(config(raw,'역배열(SMA3, WMA5)','TICK',cache=True)).run(output)
    assert result['status']=='SUCCEEDED'
    manifest=verify_result(output)
    after=sorted(p for p in cache_root.rglob('*')) if cache_root.exists() else []
    assert after==before
    assert sorted(p.name for p in Path(tempfile.gettempdir()).glob('moses-*'))==spools
    stats=manifest['metadata']['calculation_cache']['SIGNAL']
    assert stats['storage']=='RAM_RUN_SCOPED' and stats['status']=='RAM_RELEASED' and stats['hits']==0
    assert manifest['metadata']['common_feature_memo']['storage']=='RAM_RUN_SCOPED'


def test_no_runtime_import_of_part1():
    import generic_backtest.watch.ma as local
    assert Path(local.__file__).is_relative_to(ROOT)
    assert all('모세의지팡이 Part1' not in str(getattr(m,'__file__','')) for m in list(sys.modules.values()))


@pytest.mark.parametrize('cache',[False,True])
def test_real_worker_mixed_native_hma_and_dynamic_ema(replay_fixture,cache):
    """Exercise both adapters under the worker's actual post-start I/O denial."""
    root,_=replay_fixture
    rows=native_rows(range(0,45*60*1000,20000))
    minutes=rows['time_msc']//60000
    rows['bid']=100.+minutes*.25+(rows['time_msc']%60000)*.000001
    rows['ask']=rows['bid']+.1;rows['last']=rows['bid']
    raw,_=archive_from_parts(root/('mixed-raw-'+str(cache)),[rows],[0,45*60*10**9])
    expression='정배열(HMA17, EMA37, SMA37) AND 기울기(HMA17) > 0'
    cfg=replace(config(raw,expression,'TICK',cache=cache),end_ns=45*60*10**9)
    output=root/('mixed-worker-'+str(cache))
    result=GenericRunCoordinator(cfg).run(output)
    assert result['status']=='SUCCEEDED',result
    verify_result(output)
    events=read_lines(output/'alerts.jsonl')
    assert events and events[0]['observed_at_ns']>=36*60*10**9
    assert all(set(e['diagnostics']['ma_features'])=={'HMA17','EMA37','SMA37'} for e in events)
