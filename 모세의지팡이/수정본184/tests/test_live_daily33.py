from pathlib import Path
from types import SimpleNamespace as NS
import csv,datetime as dt,json,logging,sys
import pytest
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'Part1/program'),str(ROOT/'Part2')]
from event_engine.model import Kind,Signal,Subscriptions
from live_alert_recording import LiveAlertRecorder,FIELDS,KST
from manager_KIM import SignalOutput

@pytest.fixture(autouse=True)
def offline(monkeypatch):
    import socket
    def fail(*a,**k):raise AssertionError('network forbidden')
    monkeypatch.setattr(socket.socket,'connect',fail)
    monkeypatch.setattr(socket.socket,'sendto',fail)

def event(stamp='2026-09-29T14:59:59+00:00',strategy='SPECIAL1',key='one',**extra):
    return NS(kind=Kind.SIGNAL,source_time=int(dt.datetime.fromisoformat(stamp).timestamp()*1000),payload={
        'signal_id':key,'strategy':strategy,'symbol':'XAUUSD+',
        'content':{'type':'NOTIFICATION','message':'테스트 알림','recipients':['TEST'],
            'event':{'source_tf':'1m','direction':'LONG','grade':'A','b0_price':3500.,'b0_time':1750000000},**extra}})

def recorder(root,config=None,logger=None):
    return LiveAlertRecorder(config or {},root_provider=lambda:{'path':str(root),'selection_id':'test'},logger=logger)

def rows(root,day):
    with (root/'alerts'/(day+'.csv')).open(encoding='utf-8',newline='') as f:return list(csv.DictReader(f))

def transport(calls):
    def send(data):
        calls.append(data);return NS(status_code=200,json=lambda:{'ok':True,'result':{'message_id':len(calls)}})
    return send

@pytest.mark.xfail(strict=True, reason='알려진 불일치: 백테스트 알림 CSV에 signal_source·signal_tf·signal_price·'
                   'env_tf·neckline_price·neckline_time_ms 6개 열이 추가됐지만 LIVE 기록 CSV에는 없다. '
                   'LIVE와 백테스트 기록을 같은 열로 맞출지 따로 결정한다. 맞추면 이 표시를 지운다.')
def test_live_record_columns_are_the_backtest_columns_plus_delivery_result():
    from event_backtest.warehouse import FIELDS as BACKTEST
    assert FIELDS==(*BACKTEST,'delivery_result')

def test_live_record_shares_the_backtest_columns_it_has_in_the_same_order():
    from event_backtest.warehouse import FIELDS as BACKTEST
    assert FIELDS[:-1]==BACKTEST[:len(FIELDS)-1] and FIELDS[-1]=='delivery_result'

def test_columns_midnight_flush_and_filtered_signals(tmp_path):
    calls=[];r=recorder(tmp_path)
    output=SignalOutput({'OZ_OUTPUT_POLICY':'MARKET_REPRESENTATIVE'},transport=transport(calls),result_observer=r.record)
    output.accept(event(market_event_id='shared'))
    output.accept(event(strategy='SPECIAL2',key='two',market_event_id='shared'))
    output.accept(event('2026-09-29T15:00:00+00:00',key='three'))
    before=rows(tmp_path,'2026-09-29');after=rows(tmp_path,'2026-09-30')
    assert len(calls)==2 and len(before)==2 and len(after)==1
    assert [x['delivery_result'] for x in before]==['전송됨','대표 정책으로 걸러짐']
    assert before[1]['strategy']=='SPECIAL2' and before[0]['b0_price']=='3500.0'
    assert tuple(before[0])==FIELDS and after[0]['signal_id']=='three'

def test_failure_and_missing_token(tmp_path):
    r=recorder(tmp_path)
    output=SignalOutput({},transport=lambda data:NS(status_code=400,json=lambda:{'ok':False}),result_observer=r.record)
    output.accept(event());output.record_skipped(event(key='empty'),'토큰 미설정')
    assert [x['delivery_result'] for x in rows(tmp_path,'2026-09-29')]==['전송 실패','토큰 미설정']

def test_notification_b0_is_kept_without_source_context(tmp_path):
    r=recorder(tmp_path);r.context['one']={'b0_price':None,'b0_time':None}
    r.record(event(),'TEST','전송됨')
    row=rows(tmp_path,'2026-09-29')[0]
    assert row['b0_price']=='3500.0' and row['b0_time']=='1750000000'

def test_unset_or_unavailable_root_never_blocks_delivery(tmp_path,caplog):
    with caplog.at_level(logging.WARNING):
        r=LiveAlertRecorder({},root_provider=lambda:None);calls=[]
        output=SignalOutput({},transport=transport(calls),result_observer=r.record)
        output.accept(event());output.accept(event(key='two'))
        assert len(calls)==2 and len([x for x in caplog.messages if '미지정' in x])==1
        missing=tmp_path/'gone';r=recorder(missing)
        output.result_observer=r.record
        output.accept(event(key='three'));output.accept(event(key='four'))
        assert len(calls)==4 and not missing.exists()
        assert len([x for x in caplog.messages if '없거나 접근' in x])==1

def test_write_failure_once_and_selection_recovery(tmp_path,caplog):
    blocked=tmp_path/'blocked';blocked.mkdir();(blocked/'alerts').write_text('not a directory')
    choice={'path':str(blocked),'selection_id':'one'}
    r=LiveAlertRecorder({},root_provider=lambda:dict(choice));calls=[]
    output=SignalOutput({},transport=transport(calls),result_observer=r.record)
    with caplog.at_level(logging.WARNING):
        output.accept(event());output.accept(event(key='two'))
    assert len(calls)==2 and len([x for x in caplog.messages if '기록 실패' in x])==1
    good=tmp_path/'good';good.mkdir();choice.update(path=str(good),selection_id='two')
    output.accept(event(key='three'));assert len(rows(good,'2026-09-29'))==1

def test_invalid_root_setting_does_not_stop_host(caplog):
    with caplog.at_level(logging.WARNING):
        r=LiveAlertRecorder({},root_provider=lambda:{'bad':'data'});calls=[]
        output=SignalOutput({},transport=transport(calls),result_observer=r.record)
        output.accept(event());output.accept(event(key='two'))
    assert len(calls)==2 and len([x for x in caplog.messages if '설정을 읽지' in x])==1

def test_missing_folder_does_not_get_recreated(tmp_path,caplog):
    root=tmp_path/'records';root.mkdir();r=recorder(root)
    root.rmdir();calls=[];output=SignalOutput({},transport=transport(calls),result_observer=r.record)
    with caplog.at_level(logging.WARNING):
        output.accept(event());output.accept(event(key='two'))
    assert not root.exists() and len(calls)==2
    assert len([x for x in caplog.messages if '없거나 접근' in x])==1

def test_daily_0700_summary_zero_and_restart(tmp_path):
    config={'LIVE_DAILY_SUMMARY_ENABLED':'true','TELEGRAM_CHAT_ID':'TEST'}
    r=recorder(tmp_path,config)
    assert r.summary_due(dt.datetime(2026,9,30,6,59,tzinfo=KST)) is None
    notice=r.summary_due(dt.datetime(2026,9,30,7,0,tzinfo=KST))
    assert '2026-09-29' in notice.payload['content']['message'] and '0건' in notice.payload['content']['message']
    r.mark_summary_sent(notice)
    assert recorder(tmp_path,config).summary_due(dt.datetime(2026,9,30,8,0,tzinfo=KST)) is None
    assert recorder(tmp_path).summary_due(dt.datetime(2026,10,1,7,0,tzinfo=KST)) is None

def test_summary_counts_pre_policy_signals_and_unique_recipients(tmp_path):
    r=recorder(tmp_path,{'LIVE_DAILY_SUMMARY_ENABLED':'true'});e=event()
    r.record(e,'a','전송 실패');r.record(e,'a','전송됨');r.record(e,'b','전송됨')
    r.record(event(strategy='SPECIAL2',key='two'),'a','대표 정책으로 걸러짐')
    result=r.summary_due(dt.datetime(2026,9,30,7,0,tzinfo=KST)).payload['content']['message']
    assert '알림 신호 2건' in result and '전송됨 2건' in result and '걸러짐 1건' in result

def test_synthetic_host_metadata_and_token_result(tmp_path):
    from event_engine.engine import EventEngine
    from event_engine.ingress import IngressSequencer
    from event_host import EventHost,HTTPServices
    class Trial:
        name='SPECIAL1'
        def subscriptions(self):return Subscriptions(kinds=(Kind.COMMAND,))
        def on_event(self,e,b,s,emit):emit(Signal('XAUUSD+','condition',{'type':'NOTIFICATION','message':'알림','recipients':['TEST']}))
    engine=EventEngine(IngressSequencer(),strategies=(Trial(),));config={'TELEGRAM_TOKEN':''}
    r=recorder(tmp_path);host=EventHost(engine,config,NS(),transport=lambda d:pytest.fail('send'),external=HTTPServices(config,NS()),recorder=r)
    original=event();engine.ingress.post(Kind.COMMAND,source='synthetic',source_seq=1,source_time=original.source_time,
        payload={'symbol':'XAUUSD+','content':{'event':original.payload['content']['event']}})
    engine.run();host.drain_outputs()
    row=rows(tmp_path,'2026-09-29')[0]
    assert row['b0_price']=='3500.0' and row['tf']=='1m' and row['delivery_result']=='토큰 미설정'
    assert not r.context

