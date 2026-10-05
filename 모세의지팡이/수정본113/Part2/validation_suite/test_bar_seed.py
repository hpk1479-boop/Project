from pathlib import Path
from types import SimpleNamespace
import sys
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from generic_backtest.bar_seed import collect,BarSeed
from generic_backtest.canonical import plain,read_json,write_json,identity
from generic_backtest.contracts import InstrumentSpec,StrategyRequirements,GenericError
from generic_backtest.calendar import CalendarRegistry
from generic_backtest.market import GenericMarketCore


def fixture(monkeypatch,tmp_path):
    from generic_backtest import runner
    instrument=InstrumentSpec('TEST','S')
    calendar=CalendarRegistry({'kind':'UTC_GRID_RESEARCH_V1','explicit_research_choice':True})
    config=SimpleNamespace(start_ns=180*10**9,instrument=plain(instrument),calendar=calendar.definition,
        mode='ALERT_ONLY',evaluation_mode='TICK',resources={'native_export':True})
    req=StrategyRequirements(('1m','5m'),{'1m':2,'5m':2},raw_tick_warmup_ns=10**18)
    monkeypatch.setattr(runner,'load_setup',lambda c:(None,None,instrument,calendar,None,req,None,None))
    def row(t,o,h,l,c,v):return dict(time=t,open=o,high=h,low=l,close=c,tick_volume=v)
    class Provider:
        def ticks(self,*a):pytest.fail('historical ticks must never be requested')
        def bars_before(self,symbol,tf,cutoff,count):
            if tf=='1m':return [row(0,10,12,9,11,2),row(60,11,13,10,12,3),row(120,12,14,11,13,4),row(180,999,999,999,999,99)]
            return [row(0,10,999,9,999,999)] # final higher-TF values include future!
    desc=collect(Provider(),config,tmp_path/'seed.json',{'signal':plain(req),'entry':None})
    return config,instrument,calendar,req,desc


def test_seed_excludes_future_and_initializes_partial_higher_timeframe(monkeypatch,tmp_path):
    config,instrument,calendar,req,desc=fixture(monkeypatch,tmp_path)
    seed=BarSeed(desc,config)
    core=GenericMarketCore(instrument,calendar,config.start_ns,req.completed_lookback_by_tf,'test')
    seed.apply(core)
    assert [b.open_ns for b in core.book.closed['1m']]==[60*10**9,120*10**9]
    assert not core.book.closed['5m']
    b=core.book.forming['5m']
    assert (b.open,b.high,b.low,b.close,b.tick_volume)==(10,14,9,13,9)
    import struct
    from pit.models import TickRecord
    # Updating the seed must preserve its prefix OHLC and add only the new tick.
    tick=SimpleNamespace(time_msc=180000,source_ordinal=1,flags=2,volume=1,time_sec=180,
        bid_bits=struct.unpack('<Q',struct.pack('<d',15))[0],ask_bits=0,last_bits=0,volume_real_bits=0,stream_id='S')
    core.book.apply_tick(tick,'new')
    b=core.book.forming['5m']
    assert (b.open,b.high,b.low,b.close,b.tick_volume)==(10,15,9,15,10)
    from generic_backtest.hybrid import required_start
    config.resources['native_bar_seed']=desc
    assert required_start(config,req,None)==config.start_ns


def test_seed_rejects_tampering_and_future_completed_bar(monkeypatch,tmp_path):
    config,instrument,calendar,req,desc=fixture(monkeypatch,tmp_path)
    payload=read_json(desc['path']);payload['feeds']['1m']['closed'][-1][1]=240*10**9
    from generic_backtest.canonical import replace_json
    replace_json(desc['path'],payload)
    with pytest.raises(GenericError,match='identity'):BarSeed(desc,config)
    payload['identity']=identity({k:v for k,v in payload.items() if k!='identity'})
    replace_json(desc['path'],payload);desc['identity']=payload['identity']
    core=GenericMarketCore(instrument,calendar,config.start_ns,req.completed_lookback_by_tf,'test')
    with pytest.raises(GenericError,match='invalid/future'):BarSeed(desc,config).apply(core)
