from pathlib import Path
from types import SimpleNamespace
import sys
import struct
import numpy as np
import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from data_warehouse import native
from generic_backtest.native_features import NativeSnapshotSource,NativeFeatureRegistry
from generic_backtest.contracts import StrategyRequirements,GenericError
from generic_backtest.watch.percentile_runtime import ContextPercentileBundle
from pit.models import AsOfToken


def exported(tmp_path, policy='SECOND_SNAPSHOT_V1'):
    root=tmp_path/'native';root.mkdir()
    rows=np.zeros(3,dtype=native.RECORD_DTYPE)
    rows['observed_time']=[61,62,121];rows['bar_time']=[60,60,120]
    for j,name in enumerate(native.VALUE_COLUMNS):rows[name]=np.array([1.125,2.375,99.5])+j
    (root/'feed.bin').write_bytes(native.HEADER.pack(native.MAGIC,native.VERSION,
        len(native.VALUE_COLUMNS),native.RECORD_BYTES)+rows.tobytes())
    (root/'manifest.tsv').write_text('\n'.join([native.REQUEST_MAGIC,'session\tS','symbol\tTEST',
        'format_version\t1',f'value_columns\t{len(native.VALUE_COLUMNS)}',
        'sampling_policy\t'+policy,'feed\t0\t1m\tfeed.bin\t3','']),encoding='ascii')
    (root/'complete.txt').write_text(native.REQUEST_MAGIC+'\nS\n',encoding='ascii')
    descriptor={'path':str(root),'symbol':'TEST','payload_sha256':native.parse_export(root)['payload_sha256']}
    return descriptor,rows


def test_native_numbers_reach_strategy_without_python_kernels(tmp_path,monkeypatch):
    from pit.features.percentile.provider import PercentileFeatureProvider
    monkeypatch.setattr(PercentileFeatureProvider,'on_market',lambda *a,**k:pytest.fail('Python percentile invoked'))
    desc,rows=exported(tmp_path)
    source=NativeSnapshotSource(desc,'TEST')
    req=StrategyRequirements(('1m',),features=tuple({'name':f,'kind':'MOSES_PERCENTILE',
        'family':f,'timeframe':'1m'} for f in ('PRICE','RSI','STO','DI')))
    registry=NativeFeatureRegistry(req,'owner',source)
    token=AsOfToken('epoch',1,61_500_000_000,1,'prefix','v','profile','view')
    bar=SimpleNamespace(open_ns=60_000_000_000,bar_id='bar',state='FORMING')
    view=SimpleNamespace(token=token,bars=lambda tf:[bar])
    values=registry.advance(view,None)
    for family in ('PRICE','RSI','STO','DI'):
        bundle=ContextPercentileBundle(values[family])
        assert bundle.asof_token==token
        assert struct.pack('<d',bundle.field('strategy_value').value)==struct.pack('<d',rows[0][family.lower()+'_value'])
        assert bundle.strategy_state.columns[('price' if family=='PRICE' else family)+'_regime_slope']==rows[0][family.lower()+'_regime_slope']
    assert registry.other.specs==()
    assert source.row('1m',60_000_000_000,60_000_000_000) is None
    assert source.row('1m',121_000_000_000,60_000_000_000)['observed_time']==62
    assert source.row('1m',121_000_000_000,120_000_000_000)['observed_time']==121
    assert source.row('1m',122_000_000_000,180_000_000_000) is None


def test_native_reader_rejects_sparse_legacy_and_tampering(tmp_path):
    desc,_=exported(tmp_path,policy='MINUTE_OR_STATE_CHANGE_V1')
    with pytest.raises(GenericError,match='SECOND_SNAPSHOT'):NativeSnapshotSource(desc,'TEST')
    desc['payload_sha256']='wrong'
    with pytest.raises(GenericError,match='hash mismatch'):NativeSnapshotSource(desc,'TEST')


def test_native_boolean_state_reaches_strategy_dataframe(tmp_path):
    import pandas as pd
    from generic_backtest.watch.engines.frames import with_percentile_bundles
    desc,_=exported(tmp_path)
    req=StrategyRequirements(('1m',),features=tuple({'name':f,'kind':'MOSES_PERCENTILE',
        'family':f,'timeframe':'1m'} for f in ('PRICE','RSI','STO','DI')))
    registry=NativeFeatureRegistry(req,'owner',NativeSnapshotSource(desc,'TEST'))
    token=AsOfToken('epoch',1,61_500_000_000,1,'prefix','v','profile','view')
    bar=SimpleNamespace(open_ns=60_000_000_000,bar_id='bar',state='FORMING')
    values=registry.advance(SimpleNamespace(token=token,bars=lambda tf:[bar]),None)
    bundles={f:ContextPercentileBundle(v) for f,v in values.items()}
    frame=pd.DataFrame({'time':pd.to_datetime([0,60],unit='s')})
    frame.attrs.update(symbol='TEST',timeframe='1m')
    result=with_percentile_bundles(frame,bundles,token)
    booleans=0
    for bundle in bundles.values():
        for name,value in bundle.strategy_state.columns.items():
            if isinstance(value,(bool,np.bool_)):
                booleans+=1
                assert isinstance(result[name].iat[-1],(bool,np.bool_))
                assert result[name].iat[-1]==value
                assert pd.isna(result[name].iat[0])
    assert booleans>0
    assert list(frame.columns)==['time']
    assert with_percentile_bundles(result,bundles,token,owned=True) is result


def test_empty_values_remain_unavailable_and_custom_parameters_fail(tmp_path):
    desc,_=exported(tmp_path)
    source=NativeSnapshotSource(desc,'TEST')
    req=StrategyRequirements(('1m',),features=({'name':'r','kind':'MOSES_PERCENTILE',
        'family':'RSI','timeframe':'1m','params':{'length':99}},))
    with pytest.raises(GenericError,match='custom parameters'):NativeFeatureRegistry(req,'owner',source)


@pytest.mark.parametrize('selected_start,seeded',[(61,False),(121,False),(120,True)])
def test_coordinator_delivers_native_features_to_worker_and_finishes(tmp_path,monkeypatch,selected_start,seeded):
    import hashlib
    from generic_backtest import runner
    from generic_backtest.canonical import plain,identity,write_json,read_json
    from generic_backtest.contracts import GenericRunConfig,InstrumentSpec
    from generic_backtest.calendar import CalendarRegistry
    from generic_backtest.history.cache import RawChunkWriter
    from pit.archive.reader import TICK_DTYPE
    from pit.features.percentile.provider import PercentileFeatureProvider
    monkeypatch.setattr(PercentileFeatureProvider,'on_market',lambda *a,**k:pytest.fail('Python percentile invoked'))
    desc,native_rows=exported(tmp_path)
    desc.update(start_ns=selected_start*10**9,end_ns=122*10**9)
    instrument=InstrumentSpec('TEST','SYNTHETIC')
    calendar={'kind':'UTC_GRID_RESEARCH_V1','explicit_research_choice':True}
    req=StrategyRequirements(('1m',),{'1m':1},features=({'name':'RSI','kind':'MOSES_PERCENTILE','family':'RSI','timeframe':'1m'},))
    from dataclasses import replace
    req=replace(req,raw_tick_warmup_ns=(selected_start-61)*10**9)
    record=SimpleNamespace(sha256='a'*64,metadata={'plugin_version':'TEST','risk_anchors':{}})
    monkeypatch.setattr(runner,'load_setup',lambda c:(record,{},instrument,CalendarRegistry(calendar),{},req,None,{}))
    seen=[]
    class Worker:
        def __init__(self,*a,**k): pass
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def mark_observation_gap(self):pass
        def observe(self,payload,op=None):
            if op!='finish':
                bundle=ContextPercentileBundle(payload['features']['RSI'])
                seen.append(bundle.field('strategy_value').value)
            return []
    monkeypatch.setattr(runner,'StrategyWorker',Worker)
    monkeypatch.setattr(runner,'output_path',lambda p:Path(p))
    rows=np.zeros(3,dtype=TICK_DTYPE)
    rows['time']=[61,62,121];rows['time_msc']=rows['time']*1000
    rows['bid']=100;rows['ask']=101;rows['last']=100;rows['flags']=2
    archive_start=selected_start if seeded else 61
    if seeded:rows=rows[rows['time']>=selected_start]
    archive=tmp_path/'archive'
    chunk=RawChunkWriter(archive,'SYNTHETIC').write(0,rows,archive_start*10**9,122*10**9)
    m={'kind':'GENERIC_RAW_ARCHIVE_V1','status':'READY','instrument':plain(instrument),
       'stream_namespace':'SYNTHETIC','coverage_start_ns':archive_start*10**9,'coverage_end_ns':122*10**9,
       'count':len(rows),'raw_sha256':hashlib.sha256(rows.tobytes()).hexdigest(),'chunks':[chunk],
       'gaps':[],'coverage':{'completeness':'UNVERIFIED'},'input_kind':'SYNTHETIC_VALIDATION_ONLY',
       'source_build':['TEST']}
    m['archive_identity']=identity(m);write_json(archive/'manifest.json',m)
    config=GenericRunConfig('ALERT_ONLY','TEST','a'*64,{},plain(instrument),selected_start*10**9,122*10**9,
                            calendar,str(archive))
    from dataclasses import replace
    config=replace(config,resources=dict(config.resources,native_export=desc))
    if seeded:
        seed={'kind':'MT5_PRESTART_BARS_V1','instrument':plain(instrument),'calendar':calendar,
              'cutoff_ns':selected_start*10**9,'feeds':{'1m':{'closed':[[60*10**9,120*10**9,100,100,100,100,2]],'forming':[]}}}
        seed['identity']=identity(seed);write_json(tmp_path/'seed.json',seed)
        config=replace(config,resources=dict(config.resources,native_bar_seed={'path':str(tmp_path/'seed.json'),'identity':seed['identity']}))
    result=runner.GenericRunCoordinator(config,lambda *a:None,lambda:None).run(tmp_path/'result')
    assert result['status']=='SUCCEEDED'
    expected=native_rows[native_rows['observed_time']>=selected_start]['rsi_value']
    assert seen==list(expected)
