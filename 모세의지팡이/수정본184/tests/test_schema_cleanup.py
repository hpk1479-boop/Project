"""Final schema and config-only Wonbi logic; no network or old-alert oracle."""
import io,struct,sys,socket,zlib
from pathlib import Path
from types import SimpleNamespace
import numpy as np
import pytest
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'Part1/program'),str(ROOT/'Part2')]
import staff_schema as wire
from indicator_facts import wonbi_bands,add_ema_derived
from event_host import load_staff
from event_engine import EventEngine,IngressSequencer,Kind
from event_engine.model import FeedSnapshot,Subscriptions
from event_engine.market import select,MarketView
from event_engine.watch_runtime import WatchMonitor
from monitor_OZ import GenericWatchSpec
from oz_engine.market import OZMarketView

@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def deny(*a,**k):raise AssertionError('network forbidden')
    for name in ('connect','connect_ex','sendto'):monkeypatch.setattr(socket.socket,name,deny)

def snapshot(n=30,**columns):
    c={k:i for i,k in enumerate(wire.PIPE_VALUE_COLUMNS)}
    values=np.ones((n,len(c)))*100
    for name,value in dict(open=100,high=102,low=99,close=100,open_band_4_mid=100,wonbi_upper=103,wonbi_lower=97,**columns).items():values[:,c[name]]=value
    return FeedSnapshot(np.arange(n,dtype='<i8')*60+1700000000,np.ones(n,dtype='<i8'),values,1,'x',{})

def test_registry_roundtrip_and_receiving_normalization(tmp_path):
    assert len(wire.PIPE_VALUE_COLUMNS)==50 and wire.WIRE_SCHEMA_ID==0x379d2170
    assert 'wonbi_sigma' not in wire.PIPE_VALUE_COLUMNS and 'ema_21' not in wire.PIPE_VALUE_COLUMNS
    assert not any('open_band_'+n in c for c in wire.PIPE_VALUE_COLUMNS for n in ('179','279','300','400'))
    s=snapshot();x=s.values.copy();x[-1,wire.PIPE_VALUE_COLUMNS.index('price_lower_out')]=np.finfo(float).max
    frame=wire.pack_v2('XAUUSD+','1m',s.time,s.volume,x,seq=1)
    decoded=wire.decode_v2(frame);np.testing.assert_array_equal(decoded.values,x)
    cache=load_staff().StaffPipeCache('',gap_journal=tmp_path/'gaps.jsonl')
    cache.publish_frame(frame);actual=cache.snapshots_with_age('XAUUSD+',['1m'])['1m'][0]
    assert np.isnan(actual.values[-1,wire.PIPE_VALUE_COLUMNS.index('price_lower_out')])
    assert actual.values[-1,wire.PIPE_VALUE_COLUMNS.index('wonbi_upper')]==103
    assert actual.values[-1,wire.PIPE_VALUE_COLUMNS.index('wonbi_lower')]==97

@pytest.mark.parametrize('old_columns,old_id',[(48,918720360),(45,0x12345678)])
def test_unknown_schema_is_rejected_and_health_unavailable(tmp_path,old_columns,old_id):
    s=snapshot();symbol=b'XAUUSD+';tf=b'1m'
    payload=symbol+tf+s.time.tobytes()+s.volume.tobytes()+np.zeros((len(s.time),old_columns),dtype='<f8').tobytes()
    raw=wire.WIRE_HEADER.pack(wire.WIRE_MAGIC,2,1,len(symbol),len(tf),len(s.time),old_columns,old_id,wire.WIRE_FULL)+payload+struct.pack('<I',zlib.crc32(payload))
    cache=load_staff().StaffPipeCache('',gap_journal=tmp_path/'gaps.jsonl')
    with pytest.raises(wire.UnknownWireSchema):cache.publish_frame(bytes(raw))
    assert cache.health('XAUUSD+',['1m'])['1m']['status']=='UNAVAILABLE'
    with pytest.raises(ValueError):wire.mt5_values(np.zeros((3,old_columns)))

def test_sigma3_returns_original_mt5_arrays_bit_exact():
    mid=np.array([100.,2.**-50,np.nan]);upper=np.array([103.,.1,np.nan]);lower=np.array([97.,-.1,np.nan])
    out=wonbi_bands(mid,upper,lower,3)
    assert out['wonbi_upper'] is upper and out['wonbi_lower'] is lower and out['wonbi_mid'] is mid
    assert out['wonbi_upper'].tobytes()==upper.tobytes()

@pytest.mark.parametrize('sigma,expected',[(3,False),(2,True),(4,False)])
def test_config_sigma_changes_bands_and_touch(sigma,expected):
    s=snapshot();bands=wonbi_bands(*(s.values[:,wire.PIPE_VALUE_COLUMNS.index(k)] for k in ('open_band_4_mid','wonbi_upper','wonbi_lower')),sigma)
    view=MarketView(s,features=bands);events=[]
    ctl=SimpleNamespace(fire=lambda *a,**kw:events.append((a,kw)) or True)
    monitor=WatchMonitor.__new__(WatchMonitor);monitor.symbol='XAUUSD+';monitor.controller=ctl;monitor.touch_state={('test','1m'):False}
    watch=GenericWatchSpec('test','WONBI_TOUCH',('1m',),symbol='XAUUSD+',persistent=True,level_side='HIGH')
    monitor._wonbi_touch(watch,'1m',view)
    assert bool(events)==expected
    count=len(events);monitor._wonbi_touch(watch,'1m',view);assert len(events)==count
    oz=OZMarketView(s,wonbi_provider=lambda:bands)
    assert oz.live.get('wonbi_upper')==100+sigma

def test_wonbi_fact_is_shared_per_publication_and_readonly():
    class Consumer:
        name='C'
        def subscriptions(self):return Subscriptions(facts=('WONBI_BANDS',))
        def on_event(self,event,board,state,emit):
            a=board.fact('WONBI_BANDS','XAUUSD+','1m');b=board.fact('WONBI_BANDS','XAUUSD+','1m')
            assert a is b
            assert a['wonbi_upper'][-1]==102
            with pytest.raises(ValueError):a['wonbi_upper'][0]=9
    engine=EventEngine(IngressSequencer(),[Consumer()],fact_parameters={'WONBI_SIGMA':2})
    engine.ingress.post(Kind.MARKET_BUNDLE,source='test',source_seq=1,source_time=1,payload={'symbol':'XAUUSD+','feeds':{'1m':snapshot()}})
    engine.run();assert not engine.error_log
    assert engine.facts.computed['XAUUSD+','1m','WONBI_BANDS']==1

def test_startup_config_is_single_sigma_source(tmp_path):
    from event_application import create_event_engine
    from event_composer_domain import load_config
    from event_engine.frames import BoardFrames
    import staff_compat,monitor_OZ
    for sigma in (3.,2.5):
        path=tmp_path/'config.txt';path.write_text(f'WONBI_SIGMA={sigma}\nSTAFF_ALLOWED_SYMBOLS=XAUUSD+\nTARGET_SYMBOLS=XAUUSD+\n',encoding='utf-8')
        config=load_config(str(path));engine=create_event_engine(config,symbols=('XAUUSD+',),enabled_specials=())
        assert engine.facts.parameters['WONBI_SIGMA']==sigma
        s=snapshot();expected=100+sigma
        class Reader:
            name='CONFIG_WONBI_TEST'
            def subscriptions(self):return Subscriptions(facts=('WONBI_BANDS',))
            def on_event(self,event,board,state,emit):
                bands=board.fact('WONBI_BANDS','XAUUSD+','1m')
                assert bands['wonbi_upper'][-1]==expected
                assert select(board,'XAUUSD+','1m',('WONBI',)).row(-1).get('wonbi_upper')==expected
                assert OZMarketView(s,wonbi_provider=lambda:bands).live.get('wonbi_upper')==expected
                frames=BoardFrames(board,staff_compat,monitor_OZ,sigma=sigma,role='composer')
                assert frames.select('XAUUSD+',['1m'],['WONBI'])['1m']['wonbi_upper'].iloc[-1]==expected
        engine=EventEngine(IngressSequencer(),[Reader()],fact_parameters=engine.facts.parameters)
        engine.ingress.post(Kind.MARKET_BUNDLE,source='test',source_seq=1,source_time=1,payload={'symbol':'XAUUSD+','feeds':{'1m':s}})
        engine.run();assert not engine.error_log
        assert engine.facts.computed['XAUUSD+','1m','WONBI_BANDS']==1

def test_ema20_slope_and_ema21_is_its_own_period():
    import pandas as pd
    from watch_ma import canonical_ma
    out=add_ema_derived(pd.DataFrame({'ema_20':[1.,3.,2.]}))
    assert out['ema_20_slope'].iloc[-1]==-1
    assert canonical_ma('EMA21')=='EMA21'
    from command_interpreter import CommandInterpreter
    interpreter=CommandInterpreter({})
    assert interpreter.normalize_command_text('EMA21')=='ema21'
    assert interpreter.normalize_command_text('21EMA')=='21ema'

@pytest.mark.parametrize('prefix',['price','RSI','STO','DI'])
@pytest.mark.parametrize('value,expected',[(98,'LOWER_OUT'),(99,'IN'),(100,'IN'),(101,'IN'),(102,'UPPER_OUT'),(np.nan,'NA')])
def test_native_out_boundaries_and_compat_names(prefix,value,expected):
    import pandas as pd
    from oz_engine.common import percentile_states
    from indicator_facts import add_native_band_state_features
    v,lo,hi=('price_hma_6','price_band_lower','price_band_upper') if prefix=='price' else (prefix+'_val',prefix+'_db',prefix+'_ub')
    row={v:value,lo:99.,hi:101.,prefix+'_lower_out':value if value<99 else np.nan,
         prefix+'_upper_out':value if value>101 else np.nan,prefix+'_regime_slope':.125,
         prefix+'_regime_lower':99.,prefix+'_regime_upper':101.}
    family='PRICE' if prefix=='price' else prefix
    assert percentile_states(row)[family]==expected
    result=add_native_band_state_features(pd.DataFrame([row]),prefix)
    zone=result[prefix+'_percentile_zone'].iloc[-1]
    assert np.isnan(zone) if expected=='NA' else zone=={'LOWER_OUT':-1,'IN':0,'UPPER_OUT':1}[expected]
    assert result[prefix+'_regime_slope'].iloc[-1]==.125

def test_oz_native_slope_is_not_recomputed_from_window():
    s=snapshot(price_regime_slope=np.full(30,.125),price_regime_basis=np.arange(30)*200.)
    assert OZMarketView(s).live.get('price_regime_slope')==.125

@pytest.mark.parametrize('sigma',[0,-1,np.nan,np.inf])
def test_invalid_config_sigma_rejected(sigma):
    with pytest.raises(ValueError):wonbi_bands(np.ones(3),np.ones(3),np.ones(3),sigma)

def test_synthetic_capture_current_schema(tmp_path):
    import pandas as pd
    from part1_host.synthetic import indicator_frame
    from part1_host.capture import CaptureWriter
    from event_engine.capture_io import capture_bundles
    bars=pd.DataFrame({'open':np.arange(40.)+100,'high':np.arange(40.)+102,'low':np.arange(40.)+99,'close':np.arange(40.)+101})
    x=indicator_frame(bars);assert x.shape==(40,50)
    writer=CaptureWriter(tmp_path,'XAUUSD+',['1m'])
    writer.write(0,1700002400,np.arange(40,dtype='<i8')*60+1700000000,np.ones(40,dtype='<i8'),x)
    writer.close();items=list(capture_bundles(tmp_path));assert len(items)==1
    assert wire.decode_v2(items[0][1]).children[0].schema_id==wire.WIRE_SCHEMA_ID
