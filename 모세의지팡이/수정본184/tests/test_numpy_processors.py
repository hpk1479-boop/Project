"""Intended predicates and close/forming boundaries, independent of old alerts."""
import ast, copy, json, socket, sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
import numpy as np
import pandas as pd
import pytest
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'Part1/program'))
from event_engine.model import FeedSnapshot
from event_engine.market import MarketView,COLUMNS,select
from event_engine.sweep_runtime import LiquidityDetector,SweepRuntime
from event_engine.sweep_levels import external_levels,LevelStore
from event_engine.fvg_structure import StructureStore,structure,wilder_atr
from event_engine.fvg_runtime import FVGRuntime
from event_engine.indicator_runtime import IndicatorRuntime
from event_engine.watch_runtime import WatchMonitor
from indicator_facts_numpy import ArrayFactFrame,ma_array
from watch_array_facts import WatchMAStore
from strategy_SWEEP import SweepSpec
from monitor_OZ import GenericWatchSpec
import strategy_FVG as fvg

@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def deny(*a,**k):raise AssertionError('test network forbidden')
    for name in ('connect','connect_ex','sendto'):monkeypatch.setattr(socket.socket,name,deny)

def view(n=100,time=None,**cols):
    x=np.full((n,len(COLUMNS)),100.,dtype=float)
    defaults=dict(open=100,high=101,low=99,close=100,open_band_4_mid=100,wonbi_lower=90,wonbi_upper=110)
    for family in ('RSI','STO','DI'):defaults.update({family+'_val':100,family+'_db':99,family+'_ub':101})
    defaults.update(price_hma_6=100,price_band_lower=99,price_band_upper=101)
    for key,value in (defaults|cols).items():x[:,COLUMNS[key]]=value
    native(x)
    t=np.arange(n,dtype='int64')*60+1790380000 if time is None else np.asarray(time,dtype='int64')
    return MarketView(FeedSnapshot(t,np.ones(n,dtype='int64'),x,1,'test',{}))

def edit(v,advance=0,**cols):
    x=v.values.copy()
    for key,value in cols.items():x[:,COLUMNS[key]]=value
    native(x)
    return MarketView(FeedSnapshot(v.time+advance,v.volume,x,v.snapshot.seq+1,v.snapshot.source_epoch,{}))

def native(x):
    for p in ('price','RSI','STO','DI'):
        value,lo,hi,basis=('price_hma_6','price_band_lower','price_band_upper','price_regime_basis') if p=='price' else (p+'_val',p+'_db',p+'_ub',p+'_basis')
        v=x[:,COLUMNS[value]]
        x[:,COLUMNS[p+'_lower_out']]=np.where(v<x[:,COLUMNS[lo]],v,np.nan)
        x[:,COLUMNS[p+'_upper_out']]=np.where(v>x[:,COLUMNS[hi]],v,np.nan)
        x[:,COLUMNS[p+'_regime_slope']]=np.r_[np.nan,np.diff(x[:,COLUMNS[basis]])]

def board(views,symbol='XAUUSD+'):
    from indicator_facts import wonbi_bands
    return SimpleNamespace(feeds={(symbol,tf):v.snapshot for tf,v in views.items()},health={},source_time=0,
        observed={(symbol,tf):0 for tf in views},_frame_cache={},snapshot=lambda s,tf:views[tf].snapshot,
        fact=lambda name,s,tf:wonbi_bands(*(views[tf].column(c) for c in ('open_band_4_mid','wonbi_upper','wonbi_lower'))))

def epoch(text):return int(datetime.fromisoformat(text).replace(tzinfo=timezone.utc).timestamp())

def test_zero_copy_and_no_dataframe_or_deepcopy_on_bundle(monkeypatch):
    v=view(650);b=board({'1m':v})
    def denied(*a,**k):raise AssertionError('per-bundle pandas/copy forbidden')
    monkeypatch.setattr(pd,'DataFrame',denied);monkeypatch.setattr(copy,'deepcopy',denied)
    assert np.shares_memory(select(b,'XAUUSD+','1m').values,v.values)
    assert not v.values.flags.writeable
    s=StructureStore();s.evaluate('XAUUSD+','1m',v)
    d=LiquidityDetector(SweepSpec('w','XAUUSD+','1m'));d.process(v,[])
    f=ArrayFactFrame(v,'1m');assert len(f['s20'])==650
    ma=WatchMAStore();assert len(ma.get('XAUUSD+','1m',v,['SMA17','EMA37'])['SMA17'])==650

@pytest.mark.parametrize('tf,high_code,low_code',[('1d','PDH','PDL'),('4h','PREV_4H_HIGH','PREV_4H_LOW'),('8h','PREV_8H_HIGH','PREV_8H_LOW')])
def test_sweep_previous_levels_ignore_forming(tf,high_code,low_code):
    v=view(3,high=[110,120,999],low=[90,80,1]);levels=external_levels({tf:v})
    got={x['level_code']:x['price'] for x in levels}
    assert got[high_code]==120 and got[low_code]==80

def test_week_boundary_uses_previous_completed_monday_week():
    times=np.arange(epoch('2026-09-14'),epoch('2026-09-29'),86400)
    v=view(len(times),time=times,high=np.arange(len(times))+100,low=90-np.arange(len(times)))
    levels={x['level_code']:x for x in external_levels({'1d':v})}
    assert levels['PWH']['price']==113 and levels['PWL']['price']==77
    assert levels['PWH']['id']=='PWH:2026-09-21T00:00:00+00:00'

@pytest.mark.parametrize('local_hour,session',[(18,'LONDON'),(23,'NY'),(2,'NY')])
def test_previous_session_including_ny_across_midnight(local_hour,session):
    start=epoch('2026-09-24')-9*3600
    end=start+(local_hour if local_hour>=3 else local_hour+24)*3600
    times=np.arange(start,end+601,300)
    v=view(len(times),time=times,high=np.arange(len(times))+100,low=99)
    result={x['level_code']:x for x in external_levels({'5m':v},'1600-2100','2200-0300')}
    anchor=start+(22 if session=='NY' else 16)*3600
    assert result['PREV_SESSION_HIGH']['price']==100+(anchor-start)//300-1
    assert f':{session}:' in result['PREV_SESSION_LOW']['id']

def test_sweep_first_touch_representative_release_and_restore():
    v=view(3,high=[100,112,999],low=[100,88,1]);spec=SweepSpec('w','XAUUSD+','1m')
    levels=[dict(id=str(x),direction=d,level_code='PDH' if d=='SHORT' else 'PDL',level_name='test',price=x)
            for x,d in ((110,'SHORT'),(111,'SHORT'),(89,'LONG'),(90,'LONG'))]
    detector=LiquidityDetector(spec);events=detector.process(v,levels)
    assert {x['level_price'] for x in events}=={111.,89.}
    assert detector.process(edit(v,high=[100,112,9999]),levels)==[]
    restored=LiquidityDetector(spec,detector.export_state())
    assert len(restored.active_touch_events(restored=True))==2
    assert restored.process(v,levels)==[]
    released=restored.process(v,[])
    assert len(released)==2 and all(x['kind']=='SWEEP_INVALIDATED' for x in released)

def test_sweep_cache_closed_boundary_and_symbol_separation():
    store=LevelStore();v=view(10);views={tf:v for tf in ('1d','4h','8h','5m')}
    store.get(views,symbol='X');assert store.computations==1
    store.get({tf:edit(v,high=[101]*9+[999]) for tf in views},symbol='X');assert store.computations==1
    store.get(views,symbol='BTC');assert store.computations==2
    views['5m']=edit(v,advance=300);store.get(views,symbol='X');assert store.computations==3

def gap_view(gap):
    # ATR seed=2 at candle 1. Third candle gap exactly equals requested width.
    return view(5,high=[101,101,103+gap,102+gap,102+gap],low=[99,99,101+gap,100+gap,100+gap],close=[100,100,102+gap,101+gap,101+gap])

@pytest.mark.parametrize('ratio,yes',[(.099,False),(.1,True),(.5,True),(.501,False)])
def test_fvg_atr_ratio_bounds(monkeypatch,ratio,yes):
    monkeypatch.setattr(fvg,'FVG_ATR_PERIOD',1);monkeypatch.setattr(fvg,'FVG_MIN_ATR_RATIO',.1);monkeypatch.setattr(fvg,'FVG_MAX_ATR_RATIO',.5)
    out=structure('X','1m',gap_view(2*ratio))
    assert any(z['fvg_time']==float(gap_view(0).time[2]) for z in (*out['active_candidates'],*out['filled_zones'])) is yes

def test_fvg_live_touch_does_not_fill_until_close_and_max_age(monkeypatch):
    monkeypatch.setattr(fvg,'FVG_ATR_PERIOD',1);monkeypatch.setattr(fvg,'FVG_MIN_ATR_RATIO',.1);monkeypatch.setattr(fvg,'FVG_MAX_ATR_RATIO',2.)
    v=edit(gap_view(.5),low=[99,99,101.5,101.2,102]);store=StructureStore();a=store.evaluate('X','1m',v)
    assert a['zones']
    forming_high=v.column('high').copy();forming_high[-1]=104
    changed=edit(v,low=[99,99,101.5,101.2,90],high=forming_high)
    b=store.evaluate('X','1m',changed)
    assert store.computations==1 and b['zones'][0]['touched_now']
    assert not b['filled_zones']
    closed=edit(changed,advance=60,low=[99,99,101.5,90,90])
    c=store.evaluate('X','1m',closed)
    assert c['filled_zones'] and store.computations==2
    monkeypatch.setattr(fvg,'FVG_MAX_AGE_BARS',1)
    d=store.evaluate('X','1m',v)
    assert all(z['age_bars']==0 for z in d['zones'])

@pytest.mark.parametrize('sign,trend',[(1,'UP'),(-1,'DOWN'),(0,'NEUTRAL')])
def test_indicator_trend_and_current_price(sign,trend):
    n=100;values=100+np.arange(n)*sign
    v=view(n,open=values,hma_50=values,close=123)
    runtime=IndicatorRuntime({},{});f=runtime.frame('X','1m',v)
    assert runtime.trend('X','1m',v,f)['trend']==trend
    closes=v.column('close').copy();closes[-1]=456
    changed=edit(v,close=closes);f=runtime.frame('X','1m',changed)
    assert runtime.trend('X','1m',changed,f)['price']==456 and f.rebuilds==1
    corrected=edit(changed,close=456);f=runtime.frame('X','1m',corrected)
    assert runtime.trend('X','1m',corrected,f)['price']==456 and f.rebuilds==2

def test_indicator_disagreement_flat_or_unready_is_not_directional():
    runtime=IndicatorRuntime({},{})
    v=view(100,open=np.arange(100),hma_50=-np.arange(100))
    assert runtime.trend('X','1m',v,ArrayFactFrame(v,'1m'))['trend']=='NEUTRAL'
    assert runtime.trend('X','1m',view(10),ArrayFactFrame(view(10),'1m')) is None

def test_score_all_facts_current_row_and_closed_prefix_match_full_rebuild():
    from indicator_score import SCORE_FACTS
    from indicator_array_score import score_from_facts
    rng=np.random.default_rng(71);o=100+np.cumsum(rng.normal(0,.5,100))
    v=view(100,open=o,high=o+2,low=o-2,close=o+.25,hma_50=o)
    frame=ArrayFactFrame(v,'1m')
    for name in SCORE_FACTS:frame[name]
    for last in (102.,110.,90.):
        col=v.column('close').copy();col[-1]=last;changed=edit(v,close=col)
        frame.update(changed);fresh=ArrayFactFrame(changed,'1m')
        for name in SCORE_FACTS:np.testing.assert_allclose(frame[name],fresh[name],equal_nan=True,rtol=1e-12,atol=1e-12)
        for direction in ('LONG','SHORT'):assert score_from_facts(frame,direction)==score_from_facts(fresh,direction)
    assert frame.rebuilds==1

@pytest.mark.parametrize('direction',['LONG','SHORT'])
def test_score_strict_dmi_threshold_and_weight(direction):
    from indicator_score import SCORE_FACTS
    from indicator_array_score import score_from_facts
    class Facts(dict):
        def __len__(self):return 100
    facts=Facts({name:np.full(100,np.nan) for name in SCORE_FACTS})
    # Isolate DMI: >=25 ADX, dominant DI strictly >15, weight exactly 2/30.
    dominant='plus' if direction=='LONG' else 'minus';other='minus' if direction=='LONG' else 'plus'
    facts[dominant][:]=16;facts[other][:]=10;facts['adx'][:]=24.999
    before=score_from_facts(facts,direction)
    facts['adx'][:]=25
    assert score_from_facts(facts,direction)-before==pytest.approx(200/30)
    facts[dominant][:]=15
    assert score_from_facts(facts,direction)==before

def test_score_forming_price_can_change_without_new_bar():
    from indicator_array_score import score_from_facts
    n=100;o=np.linspace(90,100,n)
    v=view(n,open=o,high=o+1,low=o-1,close=o,hma_50=o)
    f=ArrayFactFrame(v,'1m');first=score_from_facts(f,'LONG')
    closes=v.column('close').copy();closes[-1]=80
    f.update(edit(v,close=closes))
    assert score_from_facts(f,'LONG')!=first
    assert f.rebuilds==1

class Controller:
    def __init__(self,watch):self.watches=[watch];self.events=[]
    def snapshot_for_symbol(self,symbol):return 1,tuple(self.watches)
    def fire(self,wid,message,**meta):self.events.append((wid,message,meta));return True

def monitor(kind,**kw):
    watch=GenericWatchSpec('w',kind,('1m',),symbol='XAUUSD+',persistent=True,**kw)
    c=Controller(watch);return WatchMonitor('XAUUSD+',c),c,watch

@pytest.mark.parametrize('kind',['BAR','WONBI_TOUCH','EMA_CROSS','HMA_CROSS'])
def test_watch_closed_first_observation_and_exact_boundary(kind):
    m,c,w=monitor(kind,evaluation_mode='CLOSE',level_side='HIGH')
    v=view(5,high=[101,101,101,120,150],ema_50=[90,90,90,110,110],ema_200=100,hma_6=[90,90,90,110,110],hma_17=100)
    m.evaluate(board({'1m':v}),1000.);assert not c.events
    m.evaluate(board({'1m':v}),1001.);assert not c.events
    m.evaluate(board({'1m':edit(v,advance=60)}),1060.)
    assert len(c.events)==1
    m.evaluate(board({'1m':edit(v,advance=60)}),1061.);assert len(c.events)==1

@pytest.mark.parametrize('kind',['WONBI_TOUCH','PREV_DAY_TOUCH','PERCENTILE_OUT','PERCENTILE_OUT_IN'])
def test_watch_live_condition_edges(kind):
    m,c,w=monitor(kind,level_side='HIGH');base=view(5)
    high=view(5,high=120,RSI_val=102,STO_val=102,DI_val=102,price_hma_6=102)
    if kind=='PERCENTILE_OUT_IN':base,high=high,base
    m.evaluate(board({'1m':base,'1d':view(5,high=110)}),1000.);assert not c.events
    m.evaluate(board({'1m':high,'1d':view(5,high=110)}),1001.);assert len(c.events)==1
    m.evaluate(board({'1m':high,'1d':view(5,high=110)}),1002.);assert len(c.events)==1

@pytest.mark.parametrize('family',['SMA','WMA','HMA','EMA'])
def test_watch_variable_period_and_live_close(family):
    v=view(100,open=np.arange(100,dtype=float),close=np.arange(100,dtype=float))
    store=WatchMAStore();one=store.get('X','1m',v,[family+'7'])[family+'7'].copy()
    two=store.get('X','1m',v,[family+'23'])[family+'23']
    assert not np.allclose(one[-3:],two[-3:])
    changed=v.column('close').copy();changed[-1]+=20
    value=store.get('X','1m',edit(v,close=changed),[family+'7'])[family+'7']
    assert bool(value[-1]!=one[-1]) is (family=='EMA')

def test_watch_expression_positive_negative_and_new_period():
    m,c,w=monitor('MA_EXPRESSION',ma_expression='SMA7 > 120',evaluation_mode='LIVE')
    v=view(100,open=np.arange(100,dtype=float));m.evaluate(board({'1m':v}),1000.);assert not c.events
    v=edit(v,advance=60,open=np.arange(100,dtype=float)+100);m.evaluate(board({'1m':v}),1060.);assert len(c.events)==1
    m.evaluate(board({'1m':v}),1061.);assert len(c.events)==1

def test_watch_checkpoint_and_legacy_json_restore():
    from event_engine.watch_lifetime import WatchRuntime
    import monitor_OZ
    state={'version':2,'watches':[dict(watch_id='w',watch_type='BAR',timeframes=['1m'],symbol='XAUUSD+',persistent=True,evaluation_mode='CLOSE')]}
    runtime=WatchRuntime(monitor_OZ,{}, {'oz_generic_watch_state.json':json.dumps(state)})
    assert 'w' in runtime.controller._watches
    restored=copy.deepcopy(runtime)
    assert restored.controller._watches==runtime.controller._watches
    assert json.loads(restored.export_files()['oz_generic_watch_state.json'])['watches'][0]['watch_id']=='w'

def test_four_processors_market_dispatch_has_no_dataframe_or_deepcopy(monkeypatch):
    import durable_protocol,monitor_OZ,strategy_INDICATOR,strategy_SWEEP,staff_compat
    import event_engine.domain_support as support
    from event_engine import EventEngine,IngressSequencer,Kind
    from event_engine.fvg_state import FVGProcessor,FVGConsumer
    from event_engine.sweep_state import SweepProcessor,SweepConsumer
    from event_engine.indicator_consumer import IndicatorConsumer
    from event_engine.watch_consumer import WatchConditionConsumer
    from event_engine.watch_lifetime import WatchRuntime
    sweep={'watch_id':'s','symbol':'XAUUSD+','source_tf':'1m','levels':['PDH','PDL']}
    engine=EventEngine(IngressSequencer(),[FVGConsumer(),SweepConsumer(),
        IndicatorConsumer(strategy_INDICATOR,durable_protocol,staff_compat,monitor_OZ,initial=(('XAUUSD+','1m'),)),
        WatchConditionConsumer(monitor_OZ,staff_compat,{})],[FVGProcessor(fvg,durable_protocol,initial=(('XAUUSD+','1m'),)),
        SweepProcessor(strategy_SWEEP,durable_protocol,staff_compat,monitor_OZ,initial=(sweep,))])
    watch=WatchRuntime(monitor_OZ,{},{});watch.controller._watches['w']=GenericWatchSpec('w','BAR',('1m',),symbol='XAUUSD+',persistent=True,evaluation_mode='CLOSE')
    engine.strategy_state['WATCH_CONDITIONS']['runtime']=watch
    def denied(*a,**k):raise AssertionError('per-bundle frame/copy forbidden')
    monkeypatch.setattr(pd,'DataFrame',denied);monkeypatch.setattr(copy,'deepcopy',denied);monkeypatch.setattr(support,'deepcopy',denied)
    for i in range(3):
        v=view(650,time=np.arange(650)*60+1790000000+(60 if i==2 else 0))
        engine.ingress.post(Kind.MARKET_BUNDLE,source='test',source_seq=i,source_time=(1790038940+i)*1000,
            payload={'symbol':'XAUUSD+','feeds':{tf:v.snapshot for tf in ('1m','1d','4h','8h','5m')}})
        engine.run()
    assert not engine.error_log
    assert engine.processor_state['FVG_STATE']['runtime'].structures.computations==2
    assert engine.processor_state['SWEEP_STATE']['runtime'].levels.computations==2
    frame=engine.strategy_state['INDICATOR']['runtime'].frames['XAUUSD+','1m']
    assert frame.provided_atr is not None
    assert frame['ATR14_GENERAL'] is frame.provided_atr

@pytest.mark.parametrize('family,period,column',[('EMA',50,'ema_50'),('HMA',17,'hma_17')])
def test_native_ma_is_authoritative_and_zero_copy(family,period,column):
    v=view(100,**{column:np.arange(100,dtype=float)})
    values=WatchMAStore().get('X','1m',v,[family+str(period)])[family+str(period)]
    assert np.shares_memory(values,v.values)
    assert values[-1]==99

def test_fvg_lifecycle_created_filled_expired_and_no_initial_rebroadcast():
    import durable_protocol
    from event_engine.domain_support import FactPort
    core=FVGRuntime({});port=FactPort(durable_protocol,'FVG',{});core.manager=port
    def zone(when,ident):return dict(zone_id=ident,symbol='X',source_tf='1m',fvg_time=float(when),fvg_side='BULL',direction='LONG',zone_bot=100.,zone_top=101.,gap=1.,age_bars=0,touched_now=False)
    def result(when,zones,filled=(),oldest=0):return dict(symbol='X',source_tf='1m',latest_closed_time=float(when),live_time=float(when+60),live_price=110.,zones=zones,filled_zones=filled,eligible_zone_ids=[z['zone_id'] for z in zones],oldest_allowed_time=oldest)
    first=zone(100,'first');core.process_watch_result(result(100,[first]));assert not any(e['kind']=='FVG_CREATED' for e in port.events)
    second=zone(160,'second');core.process_watch_result(result(160,[first,second]));assert any(e['kind']=='FVG_CREATED' for e in port.events)
    core.process_watch_result(result(220,[second],[dict(first,fill_time=220.)]));assert any(e['kind']=='FVG_FILLED' for e in port.events)
    core.process_watch_result(result(280,[],oldest=200));assert any(e['kind']=='FVG_EXPIRED' for e in port.events)

def test_fvg_atr_seed_stays_separate_from_general():
    from indicator_facts import rma_array,true_range_array
    h=np.array([101.,102.,105.,103.,110.,109.]);l=h-2;c=h-1
    assert not np.array_equal(wilder_atr(h,l,c,3)[2:],rma_array(true_range_array(h,l,c),3)[2:])

@pytest.mark.parametrize('family',['EMA_CROSS','HMA_CROSS'])
def test_watch_cross_wrong_direction_and_forming_only_do_not_fire(family):
    m,c,w=monitor(family,direction='SHORT')
    base=view(5,ema_50=[90,90,90,90,110],ema_200=100,hma_6=[90,90,90,90,110],hma_17=100)
    m.evaluate(board({'1m':base}),1000.);m.evaluate(board({'1m':base}),1001.);assert not c.events
    crossed=edit(base,advance=60,ema_50=[90,90,90,110,110],hma_6=[90,90,90,110,110])
    m.evaluate(board({'1m':crossed}),1060.);assert not c.events

def test_static_four_adapters_have_no_frame_preparation_and_core_clock_rules():
    from event_engine.static_rules import violations
    paths=['sweep_state','fvg_state','indicator_consumer','watch_consumer','sweep_runtime','fvg_runtime','watch_runtime','indicator_runtime','market','sweep_levels','fvg_structure']
    for name in paths:
        source=(ROOT/'Part1/program/event_engine'/f'{name}.py').read_text('utf-8')
        tree=ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node,ast.Name):assert node.id not in ('BoardFrames','SelectionPort','RawSelectionPort','legacy_frame')
        assert not violations(source,module=name,framework=True),(name,violations(source,module=name,framework=True))
