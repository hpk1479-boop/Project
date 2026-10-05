"""Original pattern context survives notification, CSV and moved warehouse."""
import csv
import json
import sys
from pathlib import Path
from types import SimpleNamespace as NS

import duckdb
import numpy as np
import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'Part1/program'))
sys.path.insert(0,str(ROOT/'Part2'))
from event_engine.model import Event,Kind,Signal,signal_id
from event_backtest.warehouse import Warehouse,ResultWriter,FIELDS,ENTRY_FIELDS
from event_composition import NotificationPort
from test_oz_rewrite import view,edit,profile,seed


def metadata():
    return {'signal_source':'OZ','current_price':101.25,
        'oz_validity':{'tf':'1m','bar_time_ms':120000,'remaining_bars':2},
        'neckline_price':104.,'neckline_time_ms':60000,
        'b0_price':99.,'b0_time':0,'source_tf':'1m','direction':'LONG'}


@pytest.mark.parametrize('direction,side,extreme',[('LONG','high',109.),('SHORT','low',91.)])
def test_neckline_is_exact_pattern_interior_without_future_right_bar(direction,side,extreme):
    v=view(8)
    prices=np.full(8,101. if direction=='LONG' else 99.)
    prices[0]=10000. if direction=='LONG' else -10000.  # Different earlier pattern.
    prices[3]=extreme
    prices[-1]=20000. if direction=='LONG' else -20000.  # Forming alert bar.
    v=edit(v,**{side:prices})
    assert v.neckline(direction,int(v.time[1]))==(extreme,int(v.time[3])*1000)
    assert v.neckline(direction,int(v.time[-2])) is None
    assert v.neckline(direction,int(v.time[0])-60) is None


def test_actual_oz_decision_publishes_remaining_original_bars(monkeypatch):
    p=profile();v=view();c=seed(p,v)
    values=np.full(len(v),100.);values[-3]=98.
    v=edit(v,price_hma_6=values,**{f+'_val':values for f in ('RSI','STO','DI')})
    p.max_bars=4;p.max_bars_after_hma_cross=3
    captured=[]
    monkeypatch.setattr(p.watch,'validate_external_true_b0',lambda *a:True)
    monkeypatch.setattr(p.watch,'try_fire',lambda *a,**kw:captured.append(kw) or True)
    p._evaluate_candidate({'1m':v},'1m','LONG')
    assert len(captured)==1
    assert captured[0]['oz_validity']=={'tf':'1m','bar_time_ms':int(v.time[-1])*1000,'remaining_bars':1}
    assert captured[0]['b0_time']==int(v.time[-3])
    assert captured[0]['neckline_price']==101.
    assert captured[0]['neckline_time_ms']==int(v.time[-2])*1000
    assert c.alerted


@pytest.mark.parametrize('source',['OZ','SIGNAL'])
def test_notification_keeps_signal_price_and_origin_without_message_changes(source):
    raw=metadata() if source=='OZ' else {'signal_source':'SIGNAL','current_price':103.5,'source_tf':'1h','direction':'LONG'}
    kernel=NS(config={'TELEGRAM_CHAT_ID':'offline'},manager=None,current_event={**raw,'signal_strategy':'SPECIAL1'},
        timestamp=120000,messages=[])
    assert NotificationPort(kernel).send('unchanged text',event_id='one')
    message=kernel.messages[0]
    assert message['message']=='unchanged text' and message['signal_price']==raw['current_price']
    assert message['signal_source']==source
    assert ('oz_validity' in message)==(source=='OZ')


def signal_event(content,identity='one'):
    return Event(1,0,'test',None,120123,Kind.SIGNAL,
        {'strategy':'SPECIAL1','symbol':'XAUUSD+','signal_id':identity,'content':{'type':'NOTIFICATION',
        'message':'unchanged text','recipients':['offline'],'direction':'LONG','source_tf':'1m',**content}})


def test_metadata_roundtrip_and_original_warehouse_migration(tmp_path):
    folder=tmp_path/'moved-warehouse';folder.mkdir()
    old_fields=FIELDS[:-len(ENTRY_FIELDS)]
    db=duckdb.connect(str(folder/'results.duckdb'))
    db.execute('CREATE TABLE alerts ('+','.join('"'+f+'" '+('BIGINT' if f=='time_ms' else 'VARCHAR') for f in old_fields)+')')
    db.close()
    path=tmp_path/'new.csv';out=ResultWriter(path,{'run_id':'new'},0,200000)
    out.accept(signal_event(metadata()));out.close()
    warehouse=Warehouse(folder,results=True)
    warehouse.import_results(path)
    exported=tmp_path/'export.csv';warehouse.export_results('new',exported)
    row=next(csv.DictReader(exported.open(encoding='utf-8')))
    assert row['signal_source']=='OZ' and float(row['signal_price'])==101.25
    assert json.loads(row['oz_validity'])==metadata()['oz_validity']
    assert float(row['neckline_price'])==104.
    # Import old CSV without guessing a new expiry/neckline/price.
    old=tmp_path/'old.csv'
    with old.open('w',encoding='utf-8',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=old_fields);writer.writeheader()
        writer.writerow({'run_id':'old','time_ms':1,'signal_id':'old','message':'prior alert'})
    warehouse.import_results(old)
    assert warehouse.db.execute('SELECT signal_price,oz_validity FROM alerts WHERE run_id=\'old\'').fetchone()==(None,None)
    assert warehouse.db.execute('SELECT count(*) FROM alerts').fetchone()[0]==2
    warehouse.close()


def test_direct_nonoz_notification_never_inherits_parent_oz_expiry(tmp_path):
    writer=ResultWriter(tmp_path/'a.csv',{'run_id':'r'},0,200000)
    parent=Event(1,0,'OZ',None,120123,Kind.SIGNAL,{'content':{'event':metadata()}})
    output=Signal('XAUUSD+','key',{'type':'NOTIFICATION'})
    writer.note_emission('SPECIAL1',parent,output)
    identity=signal_id('SPECIAL1','XAUUSD+',120123,'key')
    writer.accept(signal_event({'signal_source':'SIGNAL','signal_price':106.,'source_tf':'1h','direction':'SHORT'},identity));writer.close()
    row=next(csv.DictReader(writer.path.open(encoding='utf-8')))
    assert row['signal_source']=='SIGNAL' and float(row['signal_price'])==106.
    assert row['oz_validity']==row['neckline_price']==''
    assert row['b0_price']==row['b0_time']==''
    assert row['tf']=='1h' and row['direction']=='SHORT'


def test_nonoz_lowest_signal_frame_does_not_depend_on_condition_list_order():
    from test_recipe_alerts101 import manager,rule
    from strategy_recipe.port import IntentPort
    m,kernel=manager()
    meaning=rule('NOTIFY',steps=[{'kind':'MA_PRICE_TOUCH','tfs':['5m','1h'],'ma_family':'EMA','slow_period':50}])
    port=IntentPort(m,meaning,'lowest-frame','USER_LOWEST')
    port.observe=lambda *args:{'ready':True,'matched':True,'event':True,
        'token':'one','at':1500,'source_tf':'5m'}
    port.poll()
    assert len(kernel.messages)==1
    assert kernel.messages[0]['signal_source']=='SIGNAL'
    assert kernel.messages[0]['signal_tf']=='5m'


def test_native_watch_notification_is_an_entry_signal_not_a_status_notice(tmp_path):
    from test_recipe_alerts101 import manager
    from event_backtest.virtual_entry import load_alerts
    m,kernel=manager();m._last_signatures={};m._source_bindings={}
    m._remember_signature=lambda *args:None
    cond=NS(tf='1h',kind='WONBI',label=lambda:'1시간 원비 터치')
    spec=NS(spec_id='user-watch',name='원비 알림',conditions=[cond],symbol='TEST',
        destination='PRIVATE',owner_chat_id='PRIVATE',source='PRIVATE',persistent=True,time_filters=None)
    m._notify_spec_locked(spec,'LONG','one')
    m._notify_spec_locked(spec,'LONG','one')
    assert len(kernel.messages)==1
    message=kernel.messages[0]
    assert message['message']=='🔔 원비 알림 조건 성립\nTEST · 매수\n1시간 원비 터치'
    assert message['recipients']==['PRIVATE']
    assert message['signal_source']=='SIGNAL' and message['signal_tf']=='1h'
    assert message['source_tf']=='1h' and message['direction']=='LONG'
    assert message['signal_strategy']=='WATCH' and message['signal_price']==2400.
    assert not getattr(m._delivery_context,'oz_event',None)
    writer=ResultWriter(tmp_path/'watch.csv',{'run_id':'watch'},0,2000000)
    writer.accept(Event(2,0,'COMPOSER',None,1500000,Kind.SIGNAL,
        {'strategy':'COMPOSER','symbol':'TEST','signal_id':'watch-one',
         'content':{'type':'NOTIFICATION',**message}}));writer.close()
    alerts,ignored=load_alerts(writer.path)
    assert ignored==0 and len(alerts)==1
    assert alerts[0]['strategy']=='WATCH' and alerts[0]['signal_source']=='SIGNAL'
    assert not alerts[0]['oz_validity']


def test_generic_trigger_does_not_implicitly_turn_progress_into_an_entry(tmp_path):
    from test_recipe_alerts101 import manager
    from event_backtest.virtual_entry import load_alerts
    _m,kernel=manager()
    original={'kind':'GENERIC_TRIGGER','symbol':'TEST','source_tf':'5m','direction':'SHORT','event_time':1500}
    kernel.current_event=original
    assert NotificationPort(kernel).send('generic unchanged',event_id='generic-one')
    message=kernel.messages[0]
    assert message['signal_source']=='NOTICE' and 'signal_tf' not in message
    assert original=={'kind':'GENERIC_TRIGGER','symbol':'TEST','source_tf':'5m','direction':'SHORT','event_time':1500}
    writer=ResultWriter(tmp_path/'progress.csv',{'run_id':'progress'},0,2000000)
    writer.accept(Event(2,0,'COMPOSER',None,1500000,Kind.SIGNAL,
        {'strategy':'COMPOSER','symbol':'TEST','signal_id':'progress-one',
         'content':{'type':'NOTIFICATION',**message}}));writer.close()
    assert load_alerts(writer.path)==([],1)


def test_explicit_notice_does_not_inherit_active_oz_parent(tmp_path):
    from test_recipe_alerts101 import manager
    from event_backtest.virtual_entry import load_alerts
    m,kernel=manager();kernel.current_event=metadata()
    m._delivery_context.oz_event=metadata()
    assert m.send_telegram('watch started',signal_context={})
    message=kernel.messages[0]
    assert message['signal_source']=='NOTICE'
    assert not message['source_tf'] and not message['direction']
    writer=ResultWriter(tmp_path/'notice.csv',{'run_id':'notice'},0,200000)
    parent=Event(1,0,'OZ',None,120123,Kind.SIGNAL,{'content':{'event':metadata()}})
    output=Signal('TEST','notice',{'type':'NOTIFICATION'})
    writer.note_emission('WATCH',parent,output)
    identity=signal_id('WATCH','TEST',120123,'notice')
    writer.accept(signal_event(message,identity));writer.close()
    row=next(csv.DictReader(writer.path.open(encoding='utf-8')))
    assert row['tf']==row['direction']==row['b0_price']==row['oz_validity']==''
    assert load_alerts(writer.path)==([],1)


def test_explicit_final_context_survives_watch_reply_routing():
    from test_recipe_alerts101 import manager
    from event_signal_context import market_signal_context
    m,kernel=manager();kernel.current_event=metadata()
    m._watch_links_for_ids=lambda *args:[{'command_message_id':7,'status':'active'}]
    context=market_signal_context(kernel.board,'TEST','SHORT',1500,('1h',))
    context['signal_strategy']='WATCH'
    assert m.send_telegram('final unchanged',watch_id='watch',event_id='one',signal_context=context)
    message=kernel.messages[0]
    assert message['reply_to_message_id']==7 and message['signal_source']=='SIGNAL'
    assert message['source_tf']=='1h' and message['signal_price']==2400.
    assert message['direction']=='SHORT' and 'oz_validity' not in message


def test_chain_context_uses_lowest_actual_compound_condition_frame():
    from test_recipe_alerts101 import manager
    from watch_orchestrator import ChainTriggerSpec,TimedChainSpec
    m,kernel=manager()
    chain=TimedChainSpec('chain','private','TEST',(
        ChainTriggerSpec('COMPOUND_CONDITION','1h',condition_specs=({'tf':'15m','price_tf':'1m'},)),
        ChainTriggerSpec('WONBI_TOUCH','5m')),final_action='NOTIFY')
    context=m._chain_signal_context(chain,{'direction':'LONG','event_time':1500})
    assert context['signal_source']=='SIGNAL' and context['signal_tf']=='1m'
    assert context['current_price']==2400. and context['signal_strategy']=='WATCH'


def test_unresolved_signal_frame_is_not_guessed():
    from event_signal_context import market_signal_context
    with pytest.raises(ValueError,match='프레임'):
        market_signal_context(None,'TEST','LONG',1500,('5m',None))


def test_cancel_receipt_is_a_notice_even_during_oz_delivery():
    from test_recipe_alerts101 import manager
    m,kernel=manager();kernel.current_event=metadata()
    m._delivery_context.oz_event=metadata()
    m._save_private_state_locked=lambda:None
    link={'owner_chat_id':'PRIVATE','watch_id':'one'}
    assert m._send_watch_cancel_result(link,'original cancellation')
    message=kernel.messages[0]
    assert message['message']=='original cancellation' and message['signal_source']=='NOTICE'
    assert not message['source_tf'] and not message['direction']


@pytest.mark.parametrize('has_validity',[True,False])
def test_explicit_oz_never_inherits_another_parent_pattern(tmp_path,has_validity):
    from test_recipe_alerts101 import manager
    m,kernel=manager()
    own={**metadata(),'b0_price':97.,'b0_time':-60,'source_tf':'1m',
        'validation_mode':'BLIND','trigger_mode':'BREAKER','final_trigger':'OWN_PATTERN'}
    if not has_validity:own.pop('oz_validity')
    kernel.current_event=own
    assert m.send_telegram('actual OZ without strategy decoration')
    message=kernel.messages[0]
    assert message['b0_price']==97. and message['b0_time']==-60
    writer=ResultWriter(tmp_path/'oz.csv',{'run_id':'oz'},0,200000)
    parent=Event(1,0,'OZ',None,120123,Kind.SIGNAL,{'content':{'event':{
        **metadata(),'b0_price':1.,'b0_time':-999,'source_tf':'5m'}}})
    output=Signal('TEST','child',{'type':'NOTIFICATION'})
    writer.note_emission('WATCH',parent,output)
    identity=signal_id('WATCH','TEST',120123,'child')
    writer.accept(signal_event(message,identity));writer.close()
    row=next(csv.DictReader(writer.path.open(encoding='utf-8')))
    assert float(row['b0_price'])==97. and row['b0_time']=='-60'
    assert row['tf']=='1m' and row['profile']=='BLIND/BREAKER'
    assert row['trigger']=='OWN_PATTERN'
    assert (bool(row['oz_validity']))==has_validity
