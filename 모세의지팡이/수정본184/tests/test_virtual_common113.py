"""User-defined post-alert fills, closed-bar boundaries, and shared Fact semantics."""
from pathlib import Path
from types import SimpleNamespace
import copy,csv,json,sys
import numpy as np
import pytest
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'Part2'),str(ROOT/'Part1/program')]
from event_backtest.virtual_entry import VirtualEntry,load_alerts,RATIOS
from event_backtest.virtual_contract import normalize_virtual_entry,immediate_virtual_entry,signal_is_oz
from event_backtest.virtual_facts import VirtualFacts
from indicator_facts import parameterized_ma_fact,parameterized_atr_fact,standalone_frame,sma,ema,wma,hma,rma,true_range
from staff_schema import PIPE_VALUE_COLUMNS as C


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    import socket
    def fail(*a,**kw):raise AssertionError('network forbidden')
    monkeypatch.setattr(socket.socket,'connect',fail)
    monkeypatch.setattr(socket.socket,'sendto',fail)


def alert(*,direction='LONG',oz=False,time=0,stop=7,**extra):
    data=dict(signal_id='signal',strategy='CUSTOM',symbol='TEST',tf='1m',direction=direction,time_ms=time,
              signal_source='OZ' if oz else 'SIGNAL',signal_price=10,b0_price=stop)
    data.update(extra)
    return data


def view(rows):
    # rows: (seconds, open, high, low, close, optional hma6, optional hma17)
    data=np.ones((len(rows),len(C)))
    for i,row in enumerate(rows):
        values=dict(zip(('open','high','low','close','hma_6','hma_17'),row[1:]))
        values.setdefault('hma_6',9);values.setdefault('hma_17',8)
        for name,value in values.items():data[i,C.index(name)]=value
    return SimpleNamespace(time=np.array([r[0] for r in rows]),values=data,columns=C)


CANDLE={'kind':'CANDLE_CLOSE'}


def policy(mode='CONFIRM',conditions=(),filters=(),stop=None,*,candle=True,**extra):
    """Conditional entry checks the closed candle first unless a test leaves it out."""
    data=immediate_virtual_entry()
    listed=[CANDLE,*conditions] if mode=='CONFIRM' and candle else list(conditions)
    data.update(mode=mode,conditions=listed,filters=list(filters))
    data['stop']=stop or {'kind':'RECENT_EXTREME','tf':'SIGNAL','bars':1,'multiplier':1}
    data.update(extra)
    return normalize_virtual_entry(data)


def oz_policy(*filters):
    """An OZ recipe's entry: the candle closed bullish above HMA6 (bearish below for a sell), B0 stop."""
    return policy(conditions=[{'kind':'MA_POSITION','family':'HMA','period':6}],filters=filters,stop={'kind':'OZ_B0'})


def replay(rows,a=None,p=None,*,spread=0,end=1000000000):
    a=a or alert()
    calc=VirtualEntry([a],spread,p or (oz_policy() if a.get('signal_source')=='OZ' else immediate_virtual_entry()))
    for i,row in enumerate(rows):calc.observe(row[0]*1000,{'1m':view(rows[:i+1])},end_ms=end)
    return calc


@pytest.mark.parametrize('direction,closed,entered',[
    ('LONG',11,True),('LONG',10,False),('LONG',9,False),
    ('SHORT',9,True),('SHORT',10,False),('SHORT',11,False)])
def test_confirmation_requires_explicit_previous_candle_direction(direction,closed,entered):
    rows=[(0,10,12,8,closed),(60,10,11,9,10)]
    c=replay(rows,alert(direction=direction),policy())
    assert (c.trades[0]['entry_time']==60000)==entered


def test_no_ma_condition_allows_open_below_ma_without_adding_close_constraint():
    c=replay([(0,10,12,7,11,20),(60,8,9,7,8,20)],p=policy())
    assert c.trades[0]['entry_price']==8


@pytest.mark.parametrize('closed,opened,entered',[(10,8,True),(8,10,False)])
def test_ma_position_judges_the_confirmation_close_not_the_next_open(closed,opened,entered):
    # "HMA6 위 양봉 마감": the confirmation candle's close against HMA6 (9), as the recipe judges it.
    p=policy(conditions=[{'kind':'MA_POSITION','family':'HMA','period':6}])
    c=replay([(0,6,11,5,closed,9),(60,opened,11,7,opened,9)],p=p)
    assert (c.trades[0]['entry_time']==60000)==entered
    if entered:assert c.trades[0]['entry_price']==8


def test_recipe_oz_policy_is_the_generic_conditional_entry():
    from event_backtest.virtual_defaults import strategy_profile
    rows=[(0,10,12,8,11,9,8),(60,10,11,9,10,9,50)]
    recipe=replay(rows,alert(oz=True),strategy_profile('SPECIAL2')['default'])
    explicit=replay(rows,alert(oz=True),oz_policy())
    assert recipe.trades[0]['entry_time']==explicit.trades[0]['entry_time']==60000
    assert recipe.trades[0]['entry_price']==explicit.trades[0]['entry_price']
    assert recipe.trades[0]['status']=='ENTERED'
    assert 'pass_cross' not in recipe.results()[0][0]


def test_oz_doji_does_not_enter_merely_above_hma6():
    c=replay([(0,10,11,9,10),(60,12,13,11,12)],alert(oz=True),oz_policy())
    assert c.trades[0]['status']=='WAITING'


def history():
    return [(i*60,10,11,9,10.5) for i in range(-30,1)]


def test_non_oz_default_immediate_at_alert_price_and_atr_one():
    c=VirtualEntry([alert(signal_price=10.25,time=30000)],config=immediate_virtual_entry())
    c.observe(30000,{'1m':view(history())},end_ms=1000000)
    assert c.trades[0]['entry_time']==30000
    assert c.trades[0]['entry_price']==10.25
    assert c.trades[0]['stop_price']==pytest.approx(8.25)


def test_immediate_uses_exact_signal_price_not_old_bar_open():
    a=alert(time=30000,signal_price=15)
    c=VirtualEntry([a],config=policy('IMMEDIATE'))
    c.observe(60000,{'1m':view([(-60,10,11,9,10),(0,10,100,0,11),(60,15,17,14,16)])},end_ms=1000000)
    assert c.trades[0]['entry_price']==15 and c.trades[0]['entry_time']==30000
    assert all(row['result']=='UNCERTAIN' for row in c.trades[0]['exits'].values())
    c.observe(120000,{'1m':view([(-60,10,11,9,10),(0,10,100,0,11),(60,15,17,14,16)])},end_ms=1000000)
    assert all(row['result']=='UNCERTAIN' for row in c.trades[0]['exits'].values())


def test_intrabar_missing_price_errors_instead_of_inventing_fill():
    c=VirtualEntry([alert(time=30000,signal_price=None)],config=policy('IMMEDIATE'))
    with pytest.raises(ValueError,match='시점 가격'):
        c.observe(30000,{'1m':view([(0,10,11,9,10)])},end_ms=1000000)


@pytest.mark.parametrize('measure,min_,max_,entered',[
    ('BODY',.25,.3,True),('BODY',.251,None,False),('BODY',None,.25,False),
    ('RANGE',1,1.1,True),('RANGE',None,1,False)])
def test_atr_filter_minimum_inclusive_maximum_exclusive(measure,min_,max_,entered):
    rows=history()+[(60,10,11,9,10)]
    p=policy(filters=[{'kind':'CANDLE_ATR','measure':measure,'min':min_,'max':max_}])
    c=VirtualEntry([alert()],config=p);c.observe(60000,{'1m':view(rows)},end_ms=1000000)
    assert (c.trades[0]['entry_time']==60000)==entered


@pytest.mark.parametrize('opened,entered',[(10.9,True),(11,False),(11.1,False)])
def test_ma_distance_atr_uses_selected_ma_and_strict_max(opened,entered):
    rows=history()+[(60,opened,12,9,opened)]
    p=policy(filters=[{'kind':'MA_DISTANCE_ATR','family':'HMA','period':6,'min':None,'max':1}])
    c=VirtualEntry([alert()],config=p);c.observe(60000,{'1m':view(rows)},end_ms=1000000)
    assert (c.trades[0]['entry_time']==60000)==entered


@pytest.mark.parametrize('direction,rows',[
    ('LONG',[(-60,11,12,8,9),(0,8,13,7,12),(60,12,13,11,12)]),
    ('SHORT',[(-60,9,12,8,11),(0,12,13,7,8),(60,8,9,7,8)])])
def test_engulfing_confirmed_body_pattern(direction,rows):
    c=replay(rows,alert(direction=direction),policy(conditions=[{'kind':'ENGULFING'}]))
    assert c.trades[0]['entry_time']==60000


def test_engulfing_requires_opposite_older_candle():
    c=replay([(-60,9,12,8,11),(0,8,13,7,12),(60,12,13,11,12)],p=policy(conditions=[{'kind':'ENGULFING'}]))
    assert c.trades[0]['status']=='WAITING'


def test_ma_cross_uses_completed_close_cross_not_current_open_position():
    rows=[(-60,10,11,7,8,9),(0,8,11,7,10,9),(60,8,9,7,8,9)]
    p=policy(conditions=[{'kind':'MA_CROSS','family':'HMA','period':6}])
    c=replay(rows,p=p)
    assert c.trades[0]['entry_time']==60000 and c.trades[0]['entry_price']==8


def test_ma_touch_tracks_bearish_touch_then_separate_bullish_confirmation():
    rows=[(0,12,13,11,12,9),(60,10,11,8,8.5,9),(120,8.5,12,8,11,9),(180,11,12,10,11,9)]
    p=policy(conditions=[{'kind':'MA_TOUCH','family':'HMA','period':6}])
    c=replay(rows,p=p)
    assert c.trades[0]['entry_time']==180000


def test_neckline_uses_signal_pattern_value_not_another_later_pattern():
    a=alert(oz=True,neckline_price=11)
    rows=[(-60,10,11,8,9),(0,9,13,8,12),(60,12,13,11,12)]
    c=replay(rows,a,policy(conditions=[{'kind':'NECKLINE_BREAK'}],stop={'kind':'OZ_B0'}))
    assert c.trades[0]['entry_time']==60000
    c=replay(rows,dict(a,neckline_price=13),policy(conditions=[{'kind':'NECKLINE_BREAK'}],stop={'kind':'OZ_B0'}))
    assert c.trades[0]['status']=='WAITING'


@pytest.mark.parametrize('oz,neckline,error',[(False,11,'올존'),(True,None,'넥라인')])
def test_missing_or_inapplicable_neckline_errors(oz,neckline,error):
    with pytest.raises(ValueError,match=error):
        replay([(0,10,11,9,11)],alert(oz=oz,neckline_price=neckline),policy(conditions=[{'kind':'NECKLINE_BREAK'}]))


@pytest.mark.parametrize('direction,stop', [('LONG',7),('SHORT',13)])
def test_recent_n_stops_use_only_completed_bars(direction,stop):
    rows=[(-60,10,13,7,10),(0,10,12,8,11 if direction=='LONG' else 9),(60,10,100,0,10)]
    p=policy(stop={'kind':'RECENT_EXTREME','bars':2})
    c=replay(rows,alert(direction=direction),p)
    assert c.trades[0]['stop_price']==stop


def test_wrong_side_b0_is_rejected_not_absolute_risk():
    c=replay([(0,10,12,8,11),(60,10,11,9,10)],alert(oz=True,stop=12))
    assert c.trades[0]['status']=='PASS_RISK'


def test_forming_minute_never_exits_and_risk_ratios_unchanged():
    c=replay([(0,10,12,8,11),(60,10,30,0,11)],alert(oz=True,stop=7))
    assert not c.trades[0]['exits'] and tuple(c.trades[0]['targets'])==RATIOS


@pytest.mark.parametrize('family',['SMA','EMA','WMA','HMA'])
@pytest.mark.parametrize('length',[2,7,19])
def test_parameterized_ma_matches_shared_formula(family,length):
    import pandas as pd
    values=np.linspace(5,30,100)+np.sin(np.arange(100))
    df=pd.DataFrame({'open':values,'close':values+2,'high':values+3,'low':values-1,'time':np.arange(100)})
    actual=standalone_frame(df,'1m').get(parameterized_ma_fact(family,length))
    expected={'SMA':sma,'EMA':ema,'WMA':wma,'HMA':hma}[family](df['close' if family=='EMA' else 'open'],length)
    np.testing.assert_allclose(actual,expected,equal_nan=True,rtol=1e-13,atol=1e-13)


@pytest.mark.parametrize('length',[2,7,14,21])
def test_parameterized_atr_matches_existing_wilder_fact(length):
    import pandas as pd
    close=np.linspace(5,30,100)+np.sin(np.arange(100))
    df=pd.DataFrame({'open':close-.2,'close':close,'high':close+2,'low':close-1,'time':np.arange(100)})
    actual=standalone_frame(df,'1m').get(parameterized_atr_fact(length))
    np.testing.assert_allclose(actual,rma(true_range(df),length),equal_nan=True)


def test_native_ema_open_boundary_ignores_eventual_current_close():
    rows=history()+[(60,10,10000,-10000,9000)]
    v=view(rows);v.values[:,C.index('ema_20')]=8
    v.values[-1,C.index('ema_20')]=9999
    facts=VirtualFacts();facts.update({'1m':v},'TEST',60000)
    assert facts.ma('1m',60000,'EMA',20)==pytest.approx(8+2/21*2)
    assert facts.atr('1m',60000,14)==pytest.approx(2)


def test_fact_frame_and_ma_are_shared_for_multiple_consumers():
    facts=VirtualFacts();facts.update({'1m':view(history())},'TEST',30000)
    frame=facts.frame('1m',30000,closed=True)
    a=facts.atr('1m',30000,14);b=facts.atr('1m',30000,14)
    assert a==b and len(frame.values)==len(set(frame.values))
    assert facts.frame('1m',30000,closed=True) is frame


@pytest.mark.parametrize('bad',[
    {'mode':'IMMEDIATE','conditions':[{'kind':'ENGULFING'}]},
    {'mode':'AUTO','conditions':[{'kind':'ENGULFING'}]},
    {'mode':'CONFIRM','conditions':[]}, {'stop':{'kind':'AUTO'}}, {'schema':1},
    {'mode':'CONFIRM','conditions':[{'kind':'CANDLE_CLOSE','tf':'1m'}]},
    {'mode':'CONFIRM','conditions':[{'kind':'ENGULFING','period':6}]},
    {'filters':[{'kind':'CANDLE_ATR','min':1,'max':1}]},
    {'filters':[{'kind':'CANDLE_ATR','min':None,'max':None}]},
    {'stop':{'kind':'ATR','multiplier':0}}, {'atr':{'period':True}},
    {'conditions':[{'kind':'FIXED_HMA6'}]}, {'legacy_hma17':True},
])
def test_invalid_and_ignored_policy_paths_are_rejected(bad):
    with pytest.raises(ValueError):normalize_virtual_entry(bad)


def test_load_non_oz_csv_does_not_require_b0(tmp_path):
    path=tmp_path/'alerts.csv'
    path.write_text('signal_id,strategy,symbol,tf,direction,time_ms,signal_source,signal_price\ns,CUSTOM,TEST,1m,LONG,30000,SIGNAL,10\n',encoding='utf8')
    rows,ignored=load_alerts(path)
    assert rows[0]['signal_source']=='SIGNAL' and ignored==0


def test_saved_policy_only_does_not_overwrite_strategy_draft(tmp_path):
    from event_backtest.ui_model import save_virtual_entry
    path=tmp_path/'ui.json';draft={'specials':{'CUSTOM':{'enabled':True}},'watch':{'text':'keep'}}
    path.write_text(json.dumps(draft),encoding='utf8')
    expected=policy('IMMEDIATE');save_virtual_entry(expected,'CUSTOM',path)
    actual=json.loads(path.read_text('utf8'))
    assert actual['specials']==draft['specials'] and actual['watch']==draft['watch']
    assert actual['virtual_entry']==expected and actual['virtual_entry_target']=='CUSTOM'


@pytest.mark.parametrize('format,published',[('MSD1',False),('MSD2',False),('MSD2',True)])
def test_actual_recording_projection_and_csv_for_oz_and_non_oz(tmp_path,format,published):
    import staff_schema as wire
    from event_backtest.delta import write_delta
    from event_backtest.keyframes import write_indexed,verify_indexed
    from event_host import load_staff
    from event_backtest.virtual_entry import calculate
    start=1756684800
    history_rows=[(start+i*60,10,11,9,10.5) for i in range(-29,1)]
    packets=[]
    for ordinal,(offset,kind) in enumerate([(0,wire.WIRE_FULL),(60,wire.WIRE_FULL),
        (90,wire.WIRE_ROW),(100,wire.WIRE_HEARTBEAT),(120,wire.WIRE_FULL),
        (180,wire.WIRE_FULL),(240,wire.WIRE_FULL)]):
        if offset in (60,120,180,240):
            history_rows.append((start+offset,10,13 if offset==60 else 11,-1 if offset==180 else 9,10.5))
        if kind==wire.WIRE_ROW:history_rows[-1]=(start+60,10,30,7,10.5)
        data=view(history_rows)
        if kind==wire.WIRE_HEARTBEAT:child=wire.pack_v2('TEST','1m',seq=ordinal+1,kind=kind)
        else:
            n=1 if kind==wire.WIRE_ROW else len(data.time)
            child=wire.pack_v2('TEST','1m',data.time[-n:],np.ones(n,dtype='<i8'),data.values[-n:],seq=ordinal+1,kind=kind)
        stamp=(start+offset)*1000
        packets.append((stamp,wire.pack_bundle('TEST',[child],seq=ordinal+1,sent_at_ms=stamp)))
    root=tmp_path/'capture';root.mkdir()
    if format=='MSD1':write_delta(packets,root/'capture.delta.gz')
    else:
        clock=[0.]
        staff=load_staff().StaffPipeCache('',health_session='VIRTUAL113',monotonic=lambda:clock[0],gap_journal=tmp_path/'gaps.jsonl')
        index=write_indexed(packets,root/'capture.delta2',staff_cache=staff,receive_clock=clock)
        verify_indexed(root/'capture.delta2',index)
        (root/'storage.json').write_text(json.dumps(index),encoding='utf8')
        if published:(root/'complete.txt').write_text('VERIFIED\n',encoding='ascii')
    def run(item,oz):
        # One strategy per virtual entry: an OZ strategy and a non-OZ strategy run separately.
        path=tmp_path/(item['signal_id']+'.csv')
        with path.open('w',newline='',encoding='utf8') as file:
            writer=csv.DictWriter(file,fieldnames=list(item));writer.writeheader();writer.writerow(item)
        out=tmp_path/'runs'/item['signal_id'];out.mkdir(parents=True)
        # The alerts are on the recording's own clock: a recording already in real time (수정본172).
        result=calculate(path,[{'start':'2025-09-01','end':'2025-09-02','path':'capture','point':.01,
                                'server_time':{'utc_offset':0,'dst':'NONE'}}],tmp_path,
            {'start':'2025-09-01','end':'2025-09-02','symbol':'TEST','strategies':['CUSTOM'],
             'virtual_entry':oz_policy() if oz else immediate_virtual_entry()},{},out)
        with (out/'virtual_trades.csv').open(encoding='utf-8-sig') as file:rows=list(csv.DictReader(file))
        assert len(rows)==9 and result['processed_signals']==1
        assert result['trades_csv']=='runs/'+item['signal_id']+'/virtual_trades.csv'
        if oz:assert result['read_bundles']==7
        if published and oz:assert result['reader_metrics']['delta_records']==7
        return rows
    oz=run(alert(oz=True,time=start*1000,stop=0,signal_id='oz'),True)
    other=run(alert(time=start*1000,signal_id='other'),False)
    assert all(r['entry_time']==str((start+60)*1000) for r in oz)
    assert all(r['entry_time']==str(start*1000) for r in other)
    assert [r['result'] for r in oz]==['WIN']*3+['LOSS']*6
    assert all(r['result']=='LOSS' for r in other)


def test_signal_tf_controls_lowest_frame_atr_and_confirm():
    from event_backtest.virtual_contract import required_timeframes
    a=alert(signal_tf='1m',tf='1h',time=30000)
    c=VirtualEntry([a],config=immediate_virtual_entry());c.observe(30000,{'1m':view(history())},end_ms=1000000)
    assert c.trades[0]['stop_price']==pytest.approx(8)
    assert required_timeframes(immediate_virtual_entry(),a)=={'1m'}
    assert required_timeframes(oz_policy(),a)=={'1m'}


# 수정본162: the scan for traces of the removed fixed HMA17 cancellation was deleted.


def test_partial_minute_ambiguity_is_per_ratio_and_excluded_from_win_rate():
    a=alert(time=30000,signal_price=10)
    c=VirtualEntry([a],config=immediate_virtual_entry())
    rows=history();rows[-1]=(0,10,13,9,10.5)
    c.observe(30000,{'1m':view(rows)},end_ms=1000000)
    rows.append((60,10,15,9,10.5));c.observe(60000,{'1m':view(rows)},end_ms=1000000)
    rows.append((120,10,11,9,10.5));c.observe(120000,{'1m':view(rows)},end_ms=1000000)
    summary,details=c.results()
    assert [r['uncertain'] for r in summary]==[1,1]+[0]*7
    assert [r['wins'] for r in summary]==[0,0,1,1]+[0]*5
    assert all(r['unclosed']==0 and r['win_rate'] is None and r['average_r'] is None for r in summary[:2])
    assert all(r['unclosed']==1 for r in summary[4:])
    from event_backtest.analytics import analyze_rows
    analytics=analyze_rows([{**d,'alert_time':str(d['alert_time'])} for d in details])
    one=analytics['rr_results']['1.0']['summary']
    assert one['uncertain']==1 and one['total_trades']==1 and one['unclosed']==0
    assert one['win_rate'] is None and one['total_r']==0


def test_old_csv_without_signal_source_is_not_misclassified():
    # Only a B0 stop or a neckline needs the alert's source; it is never guessed.
    a=alert();a.pop('signal_source')
    with pytest.raises(ValueError,match='출처 정보'):
        replay(history()+[(60,10,11,9,10)],a,oz_policy())


@pytest.mark.parametrize('stamp,quote',[(60000,12),(30000,15)])
def test_upper_frame_ema_asof_uses_known_quote_not_upper_candle_open(stamp,quote):
    upper=view([(-7200,10,12,8,10),(-3600,10,12,8,10),(0,7,10000,-10000,9000)])
    upper.values[:,C.index('ema_20')]=8
    upper.values[-1,C.index('ema_20')]=9999
    facts=VirtualFacts();facts.update({'1h':upper},'TEST',stamp)
    expected=8+2/21*(quote-8)
    assert facts.ma('1h',stamp,'EMA',20,price=quote)==pytest.approx(expected)
    # Each observable quote has its own memoized as-of frame; a different
    # consumer cannot receive a prior quote's cached EMA.
    assert facts.ma('1h',stamp,'EMA',20,price=quote+1)==pytest.approx(expected+2/21)


def test_immediate_atr_distance_filter_uses_actual_intrabar_quote_for_ema():
    p=policy('IMMEDIATE',filters=[{'kind':'MA_DISTANCE_ATR','family':'EMA','period':20,'tf':'1h','max':.8}],
             stop={'kind':'ATR','tf':'1m','multiplier':1})
    upper=view([(-3600,10,11,9,10),(0,7,10000,-10000,9000)])
    upper.values[:,C.index('ema_20')]=8
    a=alert(time=30000,signal_price=10)
    c=VirtualEntry([a],config=p)
    c.observe(30000,{'1m':view(history()),'1h':upper},end_ms=1000000)
    # Quote-correct EMA=8.190476, distance/ATR=.90476 exceeds .8.
    assert c.trades[0]['status']=='BLOCKED'


def test_native_fact_store_invalidation_tracks_new_native_column():
    import pandas as pd
    from indicator_facts import FactStore
    name=parameterized_ma_fact('EMA',200)
    store=FactStore()
    df=pd.DataFrame({'time':[0,60],'open':[10,10],'high':[11,11],'low':[9,9],'close':[10,10],
                     'ema_200':[7,8]})
    assert store.frame('TEST','1m',df).get(name).iloc[-1]==8
    df['ema_200']=[7,9]
    assert store.frame('TEST','1m',df).get(name).iloc[-1]==9


def test_notice_with_inherited_tf_direction_is_ignored_while_market_sources_remain(tmp_path):
    path=tmp_path/'alerts.csv'
    rows=[dict(alert(oz=True),signal_source='NOTICE',signal_id='notice'),
          dict(alert(oz=True),signal_id='oz'),dict(alert(),signal_id='generic')]
    with path.open('w',newline='',encoding='utf8') as file:
        writer=csv.DictWriter(file,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
    loaded,ignored=load_alerts(path)
    assert ignored==1 and [row['signal_id'] for row in loaded]==['oz','generic']
    assert signal_is_oz(loaded[0]) and not signal_is_oz(loaded[1])


def test_notice_only_csv_does_not_activate_virtual_entry(tmp_path):
    path=tmp_path/'alerts.csv'
    row=dict(alert(),signal_source='NOTICE')
    with path.open('w',newline='',encoding='utf8') as file:
        writer=csv.DictWriter(file,fieldnames=list(row));writer.writeheader();writer.writerow(row)
    loaded,ignored=load_alerts(path)
    assert loaded==[] and ignored==1
