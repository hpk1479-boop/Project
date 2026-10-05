from pathlib import Path
import sys,struct
import numpy as np
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from generic_backtest.market import GenericMarketCore
from generic_backtest.close_market import CloseMarketCore
from generic_backtest.contracts import InstrumentSpec
from generic_backtest.calendar import CalendarRegistry
from generic_backtest.evaluation import TimeframeCloseGate,close_evaluation_view
from generic_backtest.canonical import encode,bits
from pit.models import TickRecord


def number(x):return struct.unpack('<Q',struct.pack('<d',float(x)))[0]


def test_sender_cache_holds_all_nineteen_histories(monkeypatch):
    from generic_backtest import encoding_cache
    from pit.models import BarState
    bars=[BarState(str(i),'TEST','1m',i*60*10**9,(i+1)*60*10**9,
                   100.,101.,99.,100.,1,0,0,'prefix',1,'COMPLETED') for i in range(19*682)]
    cache=encoding_cache.BarPlainCache()
    first=cache.rows(bars)
    monkeypatch.setattr(encoding_cache,'plain',lambda b:pytest.fail('unchanged history reserialized'))
    second=cache.rows(bars)
    assert [r.canonical_bytes() for r in first]==[r.canonical_bytes() for r in second]
    assert first[0] is not second[0]


@pytest.mark.parametrize('base',['1m','5m'])
@pytest.mark.parametrize('basis',['BID','LAST'])
@pytest.mark.parametrize('seeded',[False,True])
def test_close_batch_matches_every_field_of_tick_engine(base,basis,seeded):
    instrument=InstrumentSpec('TEST','S',chart_mode=basis)
    calendar=CalendarRegistry({'kind':'UTC_GRID_RESEARCH_V1','explicit_research_choice':True})
    lookbacks={tf:20 for tf in ('1m','5m','6m','1h')}
    ref=GenericMarketCore(instrument,calendar,7*10**9,lookbacks,'same')
    batch=CloseMarketCore.from_core(GenericMarketCore(instrument,calendar,7*10**9,lookbacks,'same'))
    if seeded:
        seed=TickRecord('S',0,0,number(90),number(91),number(90),1,0,10,number(1))
        ref.book.apply_tick(seed,'seed');batch.book.apply_tick(seed,'seed')
    gate=TimeframeCloseGate(instrument,calendar,base)
    rng=np.random.default_rng(41);evaluations=0
    for i in range(1,2201):
        stamp=7000+i*500+(200000 if i>=1200 else 0) # genuine missing-time jump
        price=100+rng.normal()
        if i%277==0:price=float('nan')
        if i%331==0:price=0
        flags=4 if i%17==0 else 10
        tick=TickRecord('S',i,stamp//1000,number(price),number(102),number(price),1,stamp,flags,number(1))
        gap=i in (350,1200,1224)
        old,change=ref.step(tick,gap)
        batch.accumulate(tick,gap)
        close=gate.on_tick(tick)
        if close is not None:
            new,new_change=batch.snapshot();evaluations+=1
            assert encode(bits(old))==encode(bits(new))
            assert encode(bits(change))==encode(bits(new_change))
            assert encode(bits(close_evaluation_view(old,base,close)))==encode(bits(close_evaluation_view(new,base,close)))
    new,_=batch.snapshot()
    assert encode(bits(ref.current))==encode(bits(new))
    assert batch.materializations==evaluations+1
    assert batch.materializations<ref.ordinal/10


@pytest.mark.parametrize('mode,base,expected',[('ONE_MINUTE_CLOSE','1m',10),('ONE_MINUTE_CLOSE','5m',2),('TICK','1m',31),('LIVE_PARITY','1m',1202)])
def test_runner_separates_close_and_realtime_paths(tmp_path,monkeypatch,mode,base,expected):
    import hashlib
    from types import SimpleNamespace
    from generic_backtest import runner
    from generic_backtest.contracts import GenericRunConfig,StrategyRequirements
    from generic_backtest.canonical import plain,identity,write_json,read_json
    from generic_backtest.history.cache import RawChunkWriter
    from pit.archive.reader import TICK_DTYPE
    instrument=InstrumentSpec('TEST','SYNTHETIC')
    calendar=CalendarRegistry({'kind':'UTC_GRID_RESEARCH_V1','explicit_research_choice':True})
    req=StrategyRequirements((base,'1h'),{base:1,'1h':1})
    record=SimpleNamespace(sha256='a'*64,metadata={'plugin_version':'TEST','risk_anchors':{}})
    monkeypatch.setattr(runner,'load_setup',lambda c:(record,{},instrument,calendar,{},req,None,{}))
    seen=[]
    class Worker:
        def __init__(self,*a,**k):pass
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def mark_observation_gap(self):pass
        def observe(self,payload,op=None):
            if op!='finish':seen.append(payload['token']['now_ns'])
            return []
    monkeypatch.setattr(runner,'StrategyWorker',Worker)
    monkeypatch.setattr(runner,'output_path',lambda p:Path(p))
    rows=np.zeros(31,dtype=TICK_DTYPE);rows['time']=np.arange(31)*20
    rows['time_msc']=rows['time']*1000;rows['bid']=100;rows['ask']=101;rows['flags']=2
    archive=tmp_path/'archive';end=601*10**9
    chunk=RawChunkWriter(archive,'SYNTHETIC').write(0,rows,0,end)
    manifest={'kind':'GENERIC_RAW_ARCHIVE_V1','status':'READY','instrument':plain(instrument),
        'stream_namespace':'SYNTHETIC','coverage_start_ns':0,'coverage_end_ns':end,
        'count':len(rows),'raw_sha256':hashlib.sha256(rows.tobytes()).hexdigest(),'chunks':[chunk],
        'gaps':[],'coverage':{'completeness':'UNVERIFIED'},'input_kind':'SYNTHETIC_VALIDATION_ONLY','source_build':['TEST']}
    manifest['archive_identity']=identity(manifest);write_json(archive/'manifest.json',manifest)
    from dataclasses import replace
    config=GenericRunConfig('ALERT_ONLY','TEST','a'*64,{},plain(instrument),0,end,calendar.definition,str(archive))
    config=replace(config,evaluation_mode=mode,resources=dict(config.resources,disk_cache=False))
    result=runner.GenericRunCoordinator(config).run(tmp_path/'result')
    assert result['status']=='SUCCEEDED'
    assert len(seen)==expected
    meta=read_json(tmp_path/'result/generic_manifest.json')['metadata']['market_processing']['SIGNAL']
    assert meta['snapshots']==(31 if mode=='LIVE_PARITY' else expected)
    assert meta['mode']==('CLOSE_BATCH' if mode=='ONE_MINUTE_CLOSE' else 'PER_TICK')
    if mode=='ONE_MINUTE_CLOSE':
        seconds=60 if base=='1m' else 300
        assert seen==[t*10**9 for t in range(seconds,601,seconds)]
