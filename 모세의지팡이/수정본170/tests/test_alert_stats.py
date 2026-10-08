"""Alert-only statistics: counted while writing the CSV, one alert per signal_id, selected UTC period."""
from pathlib import Path
import csv,datetime as dt,json,sys
import pytest
R=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(R/'Part2'),str(R/'Part1/program')]
from event_backtest.warehouse import ResultWriter
from event_backtest.alert_stats import summarize,lines
from event_engine.model import Event,Kind


def ms(text):
    return int(dt.datetime.fromisoformat(text).replace(tzinfo=dt.timezone.utc).timestamp()*1000)


def feed(writer,sid,when,*,kind='NOTIFICATION',recipients=('1',)):
    p={'strategy':'OZ','symbol':'XAUUSD+','signal_id':sid,
       'content':{'type':kind,'message':'m','recipients':list(recipients),'status':'DEGRADED' if kind!='NOTIFICATION' else None}}
    writer.accept(Event(1,0,'strategy',None,ms(when),Kind.SIGNAL,p))


def test_writer_counts_rows_it_writes_once_per_signal(tmp_path):
    w=ResultWriter(tmp_path/'a.csv',{'run_id':'r'},ms('2025-09-01'),ms('2025-11-01'))
    feed(w,'a','2025-09-02T01:00',recipients=('1','2'))   # 2 CSV rows, 1 alert
    feed(w,'b','2025-09-30T23:59')
    feed(w,'c','2025-10-15T00:00')
    feed(w,'d','2025-08-31T23:59')                        # before period: not written, not counted
    feed(w,'e','2025-11-01T00:00')                        # end exclusive
    feed(w,'f','2025-10-20T00:00',kind='HEALTH')          # DEGRADED row written but not an alert
    w.close()
    rows=list(csv.DictReader(open(w.path,encoding='utf-8')))
    assert len(rows)==5 and {r['signal_id'] for r in rows}=={'a','b','c','f'}
    assert len(w.alert_ids)==3 and w.alert_months=={'2025-09':2,'2025-10':1}


def test_summary_months_zero_and_averages():
    chunks=[{'alert_months':{'2025-09':2}},{'alert_months':{'2025-11':4}}]
    s=summarize('2025-09-01','2025-12-01',chunks)
    assert s['total']==6 and s['days']==91
    assert s['months']==[{'month':'2025-09','alerts':2},{'month':'2025-10','alerts':0},{'month':'2025-11','alerts':4}]
    assert s['daily_average']==pytest.approx(6/91)
    assert s['weekly_average']==pytest.approx(6*7/91)
    assert s['monthly_average']==pytest.approx(6*(365.2425/12)/91)
    json.dumps(s)


@pytest.mark.parametrize('start,end,week,month',[
    ('2025-09-01','2025-09-02',False,False),
    ('2025-09-01','2025-09-08',True,False),
    ('2025-09-01','2025-09-29',True,True),
    ('2025-09-15','2025-10-15',True,True)])
def test_short_periods_leave_average_blank(start,end,week,month):
    s=summarize(start,end,[{'alert_months':{}}])
    assert s['daily_average']==0
    assert (s['weekly_average'] is not None)==week and (s['monthly_average'] is not None)==month
    head,months=lines(s)
    assert '총 알림 0건' in head and (month or head.endswith('월평균 '))
    assert all('0건' in part for part in months.removeprefix('월별: ').split(' · '))


def test_partial_months_listed_by_selected_period():
    s=summarize('2025-09-15','2025-11-10',[{'alert_months':{'2025-09':1,'2025-11':2}}])
    assert [m['month'] for m in s['months']]==['2025-09','2025-10','2025-11'] and s['total']==3



def test_runner_and_result_screen_wiring(tmp_path,monkeypatch):
    """The current web result API exposes stored alert counts and raw CSV rows."""
    sys.path.insert(0,str(R/'Part3'))
    from lab import unified_backtest
    identifier='a'*32
    folder=tmp_path/'runs'/identifier
    folder.mkdir(parents=True)
    statistics=summarize('2025-09-01','2025-11-01',[
        {'alert_months':{'2025-09':2,'2025-10':1}}])
    csv_path=folder/'alerts.csv'
    message='[2. 외부유동성 스윕 발생!] SHORT 신호 발생\n두번째 줄'
    with csv_path.open('w',encoding='utf-8',newline='') as handle:
        writer=csv.DictWriter(handle,fieldnames=('source_time','message'))
        writer.writeheader()
        writer.writerow({'source_time':'2025-09-30T23:59:00+00:00','message':message})
    result_path=folder/'result.json'
    stored={'status':'COMPLETE','result_mode':'ALERT_ONLY','alert_statistics':statistics,
            'alerts_csv':csv_path.relative_to(tmp_path).as_posix()}
    result_path.write_text(json.dumps(stored,ensure_ascii=False),encoding='utf-8')
    before={path:path.read_bytes() for path in (csv_path,result_path)}
    monkeypatch.setattr(unified_backtest,'JOBS',{
        identifier:{'warehouse':tmp_path,'folder':folder}})
    result=unified_backtest.result(identifier)
    assert result['alert_statistics']==statistics
    assert result['alert_statistics']['total']==3
    assert result['alert_statistics']['months']==[
        {'month':'2025-09','alerts':2},{'month':'2025-10','alerts':1}]
    assert result['alerts_preview']==[
        {'source_time':'2025-09-30T23:59:00+00:00','message':message}]
    assert result['analysis'] is None and result['analytics_status']=='missing'   # 165: no trade rows, no analysis
    assert result['alerts_csv']==stored['alerts_csv']
    assert unified_backtest.download(identifier,'alerts')==csv_path
    assert all(path.read_bytes()==raw for path,raw in before.items())
