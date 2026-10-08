"""Available-only replay, future dates and real MT5 history tail regressions."""
import contextlib
import datetime as dt
import sys
from pathlib import Path
import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'Part2'))
from event_backtest import build_plan,periods,recording,runner,workflow
from generic_backtest.native_mt5 import NativeHistoryUnavailable


def cap(start,end,days,key='a'):
    return {'capture_id':key,'start':start,'end':end,'observed_days':days,
            'ea_build_hash':'ea','schema_id':7,'timer_ms':1000,'storage':'MSD2',
            'reconstruction_verified':True,'history_missing':[],'empty':False,
            'recorded_at':'2026-10-04','path':key,'symbol':'XAUUSD+','mode':'BAR'}


class Catalog:
    def __init__(self,root,rows=()):self.root=root;self.rows=list(rows);self.checked=[];self.bad=set()
    def available(self,*args):return self.rows
    def find_capture(self,key):
        self.checked.append(key)
        return next((c for c in self.rows if c['capture_id']==key and key not in self.bad),None)
    def close(self):pass


def scenario(**changes):
    return dict(symbol='XAUUSD+',mode='BAR',start='2026-07-04',end='2026-10-04',
                overlap_trading_days=0,timer_ms=1000,strategies=[],commands=[],
                build_only=False,result_mode='ALERT_ONLY',warnings=[],**changes)


@pytest.fixture
def planning(monkeypatch):
    monkeypatch.setattr(build_plan,'current_build',lambda *_:'ea')
    monkeypatch.setattr(build_plan,'schema_id',lambda:7)
    monkeypatch.setattr(build_plan,'compatible_hashes',lambda *_:{'ea'})


def test_future_request_keeps_original_and_caps_completed_dates(tmp_path,planning):
    s=scenario();s.update(start='2026-10-01',end='2026-10-08')
    plan=build_plan.make_plan(s,Catalog(tmp_path),today=dt.date(2026,10,4))
    assert plan['record']==[{'start':'2026-10-01','end':'2026-10-02','unit':'DAY'},
                            {'start':'2026-10-02','end':'2026-10-03','unit':'DAY'},
                            {'start':'2026-10-03','end':'2026-10-04','unit':'DAY'}]
    assert s['end']=='2026-10-08'
    assert plan['period_adjustment']['requested_end']=='2026-10-08'
    assert periods.apply_plan(s,plan)['end']=='2026-10-04'


def test_entire_future_period_has_no_fake_success():
    s=scenario();s.update(start='2026-10-06',end='2026-10-08')
    with pytest.raises(ValueError,match='완료된 날짜'):periods.limit_end(s,dt.date(2026,10,4))


def test_utc_month_boundary_cannot_record_uncompleted_calendar_day(tmp_path,planning,monkeypatch):
    class Clock(dt.datetime):
        @classmethod
        def now(cls,tz=None):return cls(2026,9,30,16,0,tzinfo=dt.timezone.utc)
    monkeypatch.setattr(dt,'datetime',Clock)
    s=scenario();s.update(start='2026-09-01',end='2026-10-08')
    plan=build_plan.make_plan(s,Catalog(tmp_path))
    assert plan['record'][-1]['end']=='2026-09-30'
    assert all(p['unit']=='DAY' and p['end']<='2026-09-30' for p in plan['record'])


def test_recent_period_intersects_stored_range_never_builds(tmp_path,planning):
    c=cap('2026-07-01','2026-10-03',['2026-07-03','2026-07-06','2026-10-01','2026-10-02'])
    s=scenario();s['available_only']=True
    plan=build_plan.make_plan(s,Catalog(tmp_path,[c]),today=dt.date(2026,10,4))
    assert plan['record']==plan['convert']==[]
    assert plan['available_periods']==[{'start':'2026-07-06','end':'2026-10-03'}]
    assert plan['period_adjustment']['requested_start']=='2026-07-04'
    assert plan['period_adjustment']['end']=='2026-10-03'
    assert any(p['start']=='2026-10-03' for p in plan['excluded_periods'])


def test_missing_middle_has_separate_replay_state(tmp_path,planning):
    rows=[cap('2026-07-01','2026-08-01',['2026-07-06','2026-07-31']),
          cap('2026-09-01','2026-10-01',['2026-09-01','2026-09-30'],'b')]
    s=scenario();s['available_only']=True
    plan=build_plan.make_plan(s,Catalog(tmp_path,rows),today=dt.date(2026,10,4))
    actual=periods.apply_plan(s,plan)
    actual['commands']=[{'text':'watch'}]
    chunks,policy=runner.execution_plan(actual,cores=4)
    assert chunks==[('2026-07-06','2026-08-01'),('2026-09-01','2026-10-01')]
    assert policy['cores']==1 and policy['execution_policy']=='AVAILABLE_INDEPENDENT'
    assert any(p['start']=='2026-08-01' and p['end']=='2026-09-01' for p in plan['excluded_periods'])


def test_verified_empty_market_day_keeps_state_continuous(tmp_path):
    rows=[cap('2026-10-02','2026-10-03',['2026-10-02']),
          cap('2026-10-03','2026-10-05',[],'closed'),
          cap('2026-10-05','2026-10-06',['2026-10-05'],'b')]
    rows[1]['empty']=True
    s=scenario();s.update(start='2026-10-02',end='2026-10-06')
    _,chunks,_=periods.available_plan(s,Catalog(tmp_path,rows),rows,s['end'])
    assert chunks==[{'start':'2026-10-02','end':'2026-10-06'}]


def test_average_does_not_count_unrecorded_month_as_zero_execution():
    from event_backtest.alert_stats import summarize
    s=summarize('2026-07-01','2026-10-01',[{'alert_months':{'2026-07':31,'2026-09':30}}],
                periods=[{'start':'2026-07-01','end':'2026-08-01'},{'start':'2026-09-01','end':'2026-10-01'}])
    assert s['days']==61 and s['daily_average']==1
    assert [p['month'] for p in s['months']]==['2026-07','2026-09']


def test_available_special_still_uses_existing_parallel_partition():
    s=scenario();s['_available_periods']=[{'start':s['start'],'end':s['end']}]
    chunks,policy=runner.execution_plan(s,cores=4)
    normal={key:value for key,value in s.items() if key!='_available_periods'}
    expected,original=runner.execution_plan(normal,cores=4)
    assert chunks==expected and policy['cores']==original['cores']


def test_virtual_entry_cannot_close_using_price_after_unrecorded_gap(tmp_path,monkeypatch):
    import csv
    import numpy as np
    from types import SimpleNamespace
    sys.path.insert(0,str(ROOT/'Part1/program'))
    from staff_schema import PIPE_VALUE_COLUMNS as columns
    from event_backtest import virtual_source,virtual_entry
    from event_backtest.settings import milliseconds
    from event_backtest.virtual_contract import immediate_virtual_entry
    first=milliseconds('2026-07-01')//1000;later=milliseconds('2026-07-03')//1000
    rows=[(first,10,11,9),(first+60,10,11,9),(first+120,10,11,9),(later,10,100,0),(later+60,10,100,0)]
    def stream(*args,**kwargs):
        for index,row in enumerate(rows):
            values=np.ones((index+1,len(columns)))
            for ordinal,(_,op,hi,lo) in enumerate(rows[:index+1]):
                for key,value in dict(open=op,high=hi,low=lo,close=op,hma_6=9,hma_17=8).items():values[ordinal,columns.index(key)]=value
            yield row[0]*1000,{('XAUUSD+','1m'):SimpleNamespace(time=np.array([r[0] for r in rows[:index+1]]),values=values)}
    monkeypatch.setattr(virtual_source,'shared_observations',stream)
    path=tmp_path/'alerts.csv'
    with path.open('w',newline='',encoding='utf8') as handle:
        # The alert CSV names its signal source (OZ / SIGNAL / NOTICE); SPECIAL1 is an OZ alert.
        writer=csv.DictWriter(handle,fieldnames=['signal_id','strategy','symbol','tf','direction','time_ms','b0_price','b0_time','signal_source'])
        writer.writeheader();writer.writerow(dict(signal_id='signal',strategy='SPECIAL1',symbol='XAUUSD+',tf='1m',direction='LONG',time_ms=first*1000,b0_price=8,b0_time=first-60,signal_source='OZ'))
    # 수정본137: a virtual entry needs an explicit policy; immediate entry with the alert's OZ B0 stop.
    s=scenario();s.update(start='2026-07-01',end='2026-07-04',strategies=['SPECIAL1'],spread_points={'XAUUSD+':0},
        virtual_entry={**immediate_virtual_entry(),'stop':{'kind':'OZ_B0'}},
        _available_periods=[{'start':'2026-07-01','end':'2026-07-02'},{'start':'2026-07-03','end':'2026-07-04'}])
    out=tmp_path/'result';out.mkdir()
    result=virtual_entry.calculate(path,[{'point':0.01}],tmp_path,s,{},out)
    assert all(row['unclosed']==1 and row['wins']==row['losses']==0 for row in result['summary'])
    assert result['processed_signals']==1 and not result['cancelled']


def test_gap_warmup_is_rebuilt_inside_each_stored_segment(tmp_path):
    rows=[cap('2026-06-01','2026-07-01',['2026-06-26','2026-06-29','2026-06-30']),
          cap('2026-09-01','2026-10-01',['2026-09-01','2026-09-02','2026-09-03','2026-09-04','2026-09-30'],'b')]
    s=scenario();s.update(start='2026-09-01',overlap_trading_days=3)
    _,chunks,excluded=periods.available_plan(s,Catalog(tmp_path,rows),rows,s['end'])
    assert chunks==[{'start':'2026-09-04','end':'2026-10-01'}]
    assert any(p['reason']=='WARMUP' and p['end']=='2026-09-04' for p in excluded)


def test_corrupt_stored_capture_is_error_not_missing_success(tmp_path):
    row=cap('2026-07-01','2026-10-01',['2026-07-06','2026-09-30'])
    catalog=Catalog(tmp_path,[row]);catalog.bad.add('a')
    with pytest.raises(ValueError,match='무결성'):periods.available_plan(scenario(),catalog,[row],'2026-10-04')


def test_verified_replacement_can_supersede_deleted_old_generation(tmp_path):
    old=cap('2026-07-01','2026-10-01',['2026-07-06','2026-09-30'],'old')
    new={**old,'capture_id':'new','path':'new'}
    catalog=Catalog(tmp_path,[old,new]);catalog.bad.add('old')
    selected,chunks,_=periods.available_plan(scenario(),catalog,[new,old],'2026-10-04')
    assert selected==[new] and chunks==[{'start':'2026-07-06','end':'2026-10-01'}]


def test_no_available_data_cannot_report_complete(tmp_path,planning):
    s=scenario();s['available_only']=True
    with pytest.raises(ValueError,match='보유 데이터'):build_plan.make_plan(s,Catalog(tmp_path),today=dt.date(2026,10,4))


@pytest.mark.parametrize('flag',['build_only','rebuild'])
def test_available_only_never_silently_changes_build_intent(tmp_path,planning,flag):
    s=scenario();s['available_only']=True;s[flag]=True
    with pytest.raises(ValueError,match='함께 선택'):build_plan.make_plan(s,Catalog(tmp_path),rebuild=flag=='rebuild',today=dt.date(2026,10,4))


def tail_fixture(tmp_path,monkeypatch,available_end):
    s=scenario();s.update(start='2026-10-01',end='2026-10-08')
    rows=[cap('2026-10-01','2026-10-02',['2026-10-01']),cap('2026-10-02','2026-10-03',['2026-10-02'],'b')]
    plan={'symbol':'XAUUSD+','mode':'BAR','ea_build_hash':'ea','schema_id':7,'record':[{'start':'2026-10-03','end':'2026-10-04','unit':'DAY'}],
          'reuse':rows,'convert':[],'approval_token':'approved'}
    monkeypatch.setattr(recording,'Warehouse',lambda root:Catalog(root,rows))
    monkeypatch.setattr(build_plan,'make_plan',lambda *a,**k:dict(plan))
    monkeypatch.setattr(recording,'check_destination',lambda *a:None)
    monkeypatch.setattr(recording,'source_hash',lambda:'compiled')
    monkeypatch.setattr(recording,'journal_positions',lambda *_:{})
    @contextlib.contextmanager
    def installed(*a,**k):yield 'ea'
    monkeypatch.setattr(recording,'installed_build',installed)
    import event_backtest.terminal_lifecycle as lifecycle
    monkeypatch.setattr(lifecycle,'launch_once_retry',lambda launch,*a,**k:launch())
    def unavailable(*a,**k):
        exc=NativeHistoryUnavailable('XAUUSD+',1790985600000000000,1791072000000000000,evidence=[])
        exc.available_end=available_end
        raise exc
    monkeypatch.setattr(recording.native,'run_native_tester',unavailable)
    ready=tmp_path/'builds'/'compiled'/'ready.json';ready.parent.mkdir(parents=True);ready.write_text('{}')
    return s,rows,plan


def test_verified_broker_tail_preserves_days_and_announces_actual_dates(tmp_path,monkeypatch):
    s,rows,plan=tail_fixture(tmp_path,monkeypatch,'2026-10-03');events=[]
    actual=recording.prepare(s,tmp_path,{},plan=plan,approved_token='approved',emit=lambda k,v:events.append((k,v)))
    assert actual==rows
    assert plan['period_adjustment']['end']=='2026-10-03'
    assert plan['period_adjustment']['requested_end']=='2026-10-08'
    assert any(k=='PERIOD_ADJUSTED' for k,_ in events)
    assert periods.apply_plan(s,plan)['end']=='2026-10-03'


@pytest.mark.parametrize('end',[None,'2026-10-04','2026-10-01'])
def test_unknown_internal_or_before_requested_history_is_not_success(tmp_path,monkeypatch,end):
    s,_,plan=tail_fixture(tmp_path,monkeypatch,end)
    with pytest.raises(NativeHistoryUnavailable):recording.prepare(s,tmp_path,{},plan=plan,approved_token='approved')


def test_available_only_never_touches_mt5(tmp_path,monkeypatch):
    s,rows,plan=tail_fixture(tmp_path,monkeypatch,None);s['available_only']=True;plan['record']=[]
    monkeypatch.setattr(recording,'recover_without_recording',lambda *a,**k:pytest.fail('MT5 access'))
    assert recording.prepare(s,tmp_path,None,plan=plan,approved_token='approved')==rows


def test_progress_displays_actual_and_excluded_periods():
    from event_backtest.progress_view import ProgressView
    view=ProgressView();s=scenario();change=periods.adjustment(s,'2026-07-06','2026-10-03','AVAILABLE_ONLY')
    view.accept({'event':'BUILD_PLAN','record':[],'available_periods':[{'start':'2026-07-06','end':'2026-10-03'}],
                 'period_adjustment':change,'excluded_periods':[{'start':'2026-10-03','end':'2026-10-04','message':'보유 데이터 없음'}]})
    assert '보유 데이터만 사용합니다.' in view.lines
    assert change['message'] in view.warnings
    assert any('2026-10-03 ~ 2026-10-04' in w for w in view.warnings)
