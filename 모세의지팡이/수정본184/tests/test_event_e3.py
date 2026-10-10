"""Event-only host/output/restore contracts. No external transport is allowed."""
import ast,io,json,socket,sys
from pathlib import Path
from types import SimpleNamespace
import numpy as np
import pytest
R=Path(__file__).resolve().parents[1];P=R/'Part1/program'
sys.path[:0]=[str(P),str(R/'Part2')]
from event_application import create_event_engine
from event_host import EventHost
from event_engine import Kind
from event_engine.model import Event
from event_engine.domain_support import plain
from command_interpreter import CommandInterpreter
from test_event_e2_domains import snap


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def deny(*a,**k):raise AssertionError('E3 tests forbid actual network')
    for name in ('connect','connect_ex','sendto'):monkeypatch.setattr(socket.socket,name,deny)


def application():
    config={'STAFF_ALLOWED_SYMBOLS':'XAUUSD+,BTCUSD','TARGET_SYMBOLS':'XAUUSD+,BTCUSD',
            'TELEGRAM_CHAT_ID':'official','TELEGRAM_COMMAND_CHAT_IDS':'user','WONBI_SIGMA':'3'}
    e=create_event_engine(config,enabled_specials=())
    interpreter=CommandInterpreter(config)
    sent=[]
    def transport(data):
        sent.append(dict(data))
        return SimpleNamespace(status_code=200,json=lambda:{'ok':True,'result':{'message_id':900+len(sent)}})
    h=EventHost(e,config,interpreter,transport=transport)
    return e,h,sent


def update(text,mid=10,reply=None):
    m={'chat':{'id':'user','type':'private'},'text':text,'message_id':mid,'date':1790380679}
    if reply is not None:m['reply_to_message']={'message_id':reply}
    return {'update_id':mid,'message':m}


def market(e,n=0,*,touch=True):
    snapshot=snap(n,n+1)
    if not touch:
        from event_engine.model import FeedSnapshot
        values=snapshot.values.copy();values[-1,:4]=[100,101,99,100]
        snapshot=FeedSnapshot(snapshot.time,snapshot.volume,values,snapshot.seq,snapshot.source_epoch,snapshot.indicator_validity)
    e.ingress.post(Kind.MARKET_BUNDLE,source='staff',source_seq=n+1,source_time=1790380680000+n*60000,
                  payload={'symbol':'XAUUSD+','feeds':{'1m':snapshot}})
    e.run();assert not e.error_log,e.error_log


def test_command_watch_notification_dedup_and_actual_reply():
    e,h,sent=application()
    assert h.inputs.telegram_update(update('골드 1분 상단 원비 터치 알려줘'))
    e.run();h.drain_outputs()
    assert not e.error_log,e.error_log
    assert sent and '감시' in sent[0]['text']
    confirmation_id=901
    market(e,touch=False);h.drain_outputs();assert len(sent)==1
    market(e,1);h.drain_outputs()
    alerts=[s for s in e.signals if s.payload['content'].get('type')=='NOTIFICATION' and '상단 원비' in s.payload['content']['message']]
    assert alerts and len(sent)>=2
    # Existing Watch semantics: alerts reply to the user's original command;
    # cancellation replies to the bot's registration confirmation.
    assert json.loads(sent[1]['reply_parameters'])['message_id']==10
    count=len(sent)
    for s in e.signals:h.output.accept(s)
    assert len(sent)==count
    assert h.inputs.telegram_update(update('취소',11,confirmation_id))
    e.run();h.drain_outputs()
    assert not e.error_log,e.error_log
    assert any('취소' in data['text'] for data in sent[count:])
    assert h.output.logical_reply_id('user',confirmation_id)>=10**15


def test_gemini_is_an_ingress_reply_not_a_strategy_network_call():
    e,h,sent=application()
    h.inputs.telegram_update(update('골드 뭔가 모르는 새로운 문장 알려줘'))
    e.run();h.drain_outputs()
    request=h.external_requests.get_nowait()
    assert request.payload['content']['type']=='EXTERNAL_REQUEST'
    h.inputs.external_reply(request,'골드 1분 상단 원비 터치 알려줘',received_time=1790380680000)
    e.run();h.drain_outputs();assert not e.error_log,e.error_log
    assert any('감시' in row['text'] for row in sent)
    count=len(sent)
    h.inputs.external_reply(request,'골드 1분 상단 원비 터치 알려줘',received_time=1790380681000)
    e.run();h.drain_outputs();assert len(sent)==count


def test_unauthorized_input_and_gemini_semantic_validation():
    e,h,_=application();bad=update('골드 1분 알려줘');bad['message']['chat']['id']='stranger'
    assert not h.inputs.telegram_update(bad) and len(e.ingress)==0
    original='골드 1분 상단 원비 알려줘'
    assert h.inputs.interpreter.validate_gemini_reply(original,json.dumps({'canonical_text':'BTCUSD 1분 상단 원비 알려줘'})) is None


def test_event_restore_path_belongs_to_executing_revision(tmp_path):
    from event_startup import create_live_event_engine,write_event_state_files,export_engine_state
    source=tmp_path/'read_only'/'program';source.mkdir(parents=True)
    (source/'config.txt').write_text('STAFF_ALLOWED_SYMBOLS=XAUUSD+,BTCUSD\nTARGET_SYMBOLS=XAUUSD+,BTCUSD\nTELEGRAM_CHAT_ID=user',encoding='utf-8')
    state=tmp_path/'event_owned';state.mkdir()
    (state/'trend_watch_state.json').write_text(json.dumps({'version':3,'watches':{'restored':['BTCUSD','1m',[]]}}))
    before={p:p.read_bytes() for p in source.rglob('*') if p.is_file()}
    e=create_live_event_engine(source,state_directory=state)
    assert Path(e.event_state_directory)==state.resolve()
    assert e.processor_state['OZ_STATE']['startup_files']['trend_watch_state.json']
    market(e)
    files=export_engine_state(e)
    assert 'event_composer_memory.json' in files
    write_event_state_files(files,output_directory=state,polling_program=source)
    restored=create_live_event_engine(source,state_directory=state)
    market(restored,1)
    assert not restored.error_log
    assert before=={p:p.read_bytes() for p in source.rglob('*') if p.is_file()}
    default=create_live_event_engine(source)
    assert Path(default.event_state_directory)==P.parent/'event_state'


def test_output_failed_confirmation_cannot_break_reply_link():
    from manager_KIM import SignalOutput
    calls=[]
    def fail(data):calls.append(data);raise OSError('unknown delivery outcome')
    output=SignalOutput({'TELEGRAM_CHAT_ID':'user'},transport=fail)
    event=lambda key,content:Event(1,0,'test',1,10,Kind.SIGNAL,{'symbol':'XAUUSD+','signal_id':key,'content':content})
    output.accept(event('a',{'type':'NOTIFICATION','message':'confirmation','message_token':10**15+1}))
    output.accept(event('b',{'type':'NOTIFICATION','message':'reply','reply_to_message_id':10**15+1}))
    assert len(calls)==1 and not output.receipts


def test_no_polling_input_or_strategy_worker_routes():
    import manager_KIM
    assert not hasattr(manager_KIM,'ComposerManager')
    for name in ('manager_KIM.py','monitor_OZ.py','strategy_FVG.py','strategy_SWEEP.py','strategy_INDICATOR.py','event_composer_domain.py'):
        tree=ast.parse((P/name).read_text('utf-8'))
        for n in ast.walk(tree):
            if isinstance(n,ast.ClassDef):assert all('Thread' not in ast.unparse(b) for b in n.bases)
            if isinstance(n,ast.Import):assert not any(a.name in ('requests','zmq') for a in n.names)
            if isinstance(n,ast.Name):assert n.id!='SnapshotClient'
    assert not (P/'staff_snapshot.py').exists()
    assert not (R/'Part2/part1_host/runtime.py').exists()
    assert not (R/'Part2/live_replay/runtime.py').exists()
    assert not (R/'Part2/generic_backtest/engine.py').exists()
    from data_warehouse import native
    assert callable(native.parse_export)
    from generic_backtest.native_mt5 import run_native_tester
    assert callable(run_native_tester)


@pytest.mark.parametrize('version',[1,2])
def test_native_export_columns_and_subsecond_archive(tmp_path,version):
    from data_warehouse import native as n
    dtype=n.LEGACY_RECORD_DTYPE if version==1 else n.RECORD_DTYPE
    rows=np.ones(2,dtype=dtype)
    rows['observed_time']=[100,101] if version==1 else [100000,100100]
    rows['bar_time']=60
    if version==2:rows['wonbi_upper']=103;rows['wonbi_lower']=97;rows['wonbi_sigma']=3
    (tmp_path/'feed.bin').write_bytes(n.HEADER.pack(n.MAGIC,version,len(dtype.names)-2,dtype.itemsize)+rows.tobytes())
    (tmp_path/'complete.txt').write_text(n.REQUEST_MAGIC+'\nsession\n',encoding='ascii')
    (tmp_path/'manifest.tsv').write_text(n.REQUEST_MAGIC+'\n'+f'session\tsession\nsymbol\tXAUUSD+\nformat_version\t{version}\nvalue_columns\t{len(dtype.names)-2}\n'+
        ('sampling_policy\tSECOND_SNAPSHOT_V1\n' if version==1 else 'sampling_policy\tTIMER_SNAPSHOT_V2\nobservation_unit\tmilliseconds\nobservation_interval_ms\t100\n')+
        'feed\t0\t1m\tfeed.bin\t2\n',encoding='ascii')
    parsed=n.parse_export(tmp_path);actual=list(n.iter_feed_blocks(parsed['feeds'][0],start_s=100,end_s=102))[0]
    assert np.array_equal(actual,rows)
    if version==2:assert actual['wonbi_upper'][0]>actual['wonbi_lower'][0] and actual['observed_time'][1]-actual['observed_time'][0]==100


def test_pipe_host_handshake_validation_and_publication(tmp_path):
    import threading,staff_schema as wire
    from event_host import load_staff
    from event_pipe_host import PipeReceiver
    from event_engine.staff_adapter import StaffIngressAdapter
    from event_engine import EventEngine,IngressSequencer
    staff=load_staff();cache=staff.StaffPipeCache('',gap_journal=tmp_path/'gaps.jsonl')
    engine=EventEngine(IngressSequencer(),retain_events=True)
    receiver=PipeReceiver(staff,cache,StaffIngressAdapter(cache,engine.ingress),('BTCUSD',),threading.Event())
    replies=[]
    receiver.receive(io.BytesIO(wire.pack_hello('a'*64)).read,replies.append)
    assert wire.decode_v2(replies.pop()).kind==wire.WIRE_ACK
    s=snap();raw=wire.pack_v2('BTCUSD','1m',s.time,s.volume,s.values,seq=1)
    receiver.receive(io.BytesIO(raw).read,replies.append,source_time=1790380680000)
    engine.run();assert sum(e.kind==Kind.MARKET_BUNDLE for e in engine.events)==1
    with pytest.raises((EOFError,wire.WireError)):receiver.receive(io.BytesIO(raw[:-1]).read,replies.append,source_time=1790380681000)
    bad=wire.pack_v2('XAU USD','1m',s.time,s.volume,s.values,seq=2)
    with pytest.raises(ValueError,match='disallowed'):receiver.receive(io.BytesIO(bad).read,replies.append,source_time=1790380682000)
    assert len(engine.ingress)==0


def test_btc_reply_routes_to_original_symbol():
    e,h,_=application()
    h.output.receipts['test']={'recipient':'user','message_id':321,'message_token':10**15+7,'symbol':'BTCUSD'}
    assert h.inputs.telegram_update(update('취소',reply=321))
    queued=e.ingress.take_input(e)
    assert queued.payload['symbol']=='BTCUSD' and queued.payload['reply_to_message_id']==10**15+7


def test_economy_host_state_lives_in_event_directory(tmp_path,monkeypatch):
    import threading
    def deny(*args,**kwargs):raise AssertionError('No HTTP in economy restore test')
    monkeypatch.setitem(sys.modules,'requests',SimpleNamespace(get=deny,post=deny))
    from event_economy_host import EconomyWorker
    worker=EconomyWorker({},threading.Event(),SimpleNamespace(send=lambda text:True),state_directory=tmp_path/'event')
    assert worker.alerted_file.parent==tmp_path/'event' and worker.briefing_file.parent==tmp_path/'event'
    worker.save_last_briefing(__import__('datetime').date(2026,9,26))
    assert EconomyWorker({},threading.Event(),SimpleNamespace(send=lambda text:True),
                         state_directory=tmp_path/'event').load_last_briefing()==__import__('datetime').date(2026,9,26)
    # Constructor/formatting does not fetch HTTP or deliver a Telegram message.
    import datetime
    assert '브리핑' in worker.build_briefing(datetime.date(2026,9,27),[])
