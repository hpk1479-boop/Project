"""WATCH keeps its parser/meaning checks while using the common AI connection."""
from __future__ import annotations

import json
import logging
from pathlib import Path
import subprocess
import sys
import threading
import time
from types import ModuleType, SimpleNamespace

import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'Part1/program'))
from command_interpreter import CommandInterpreter, DEFAULT_COMMAND_LANGUAGE
from event_application import create_event_engine
from event_engine import Kind
from event_host import EventHost, HTTPServices


CONFIG={'STAFF_ALLOWED_SYMBOLS':'XAUUSD,BTCUSD','TARGET_SYMBOLS':'XAUUSD,BTCUSD',
        'TELEGRAM_TOKEN':'OFFLINE','TELEGRAM_CHAT_ID':'official',
        'TELEGRAM_COMMAND_CHAT_IDS':'user','WONBI_SIGMA':'3',
        # Legacy Gemini settings do not override the selected common provider.
        'GEMINI_FALLBACK_ENABLED':'false','GEMINI_API_KEY':'synthetic secret',
        'GEMINI_MODEL':'legacy-model-must-not-be-used'}


@pytest.fixture
def connection(monkeypatch):
    settings={'provider':'local_gguf','gguf_model_path':'models/gguf/one.gguf',
              'llama_server_path':'runtime/llama.cpp/llama-server.exe','watch_enabled':True}
    clients=[]
    class Client:
        def __init__(self,root):
            self.root=root;self.calls=[];self.closed=0
            self.reply={'content':json.dumps({'canonical_text':'골드 1분 상단 원비 터치 알려줘'},ensure_ascii=False),
                        'tool_calls':[]}
            clients.append(self)
        def chat(self,messages,tools,response_schema,*,role):
            self.calls.append({'messages':messages,'tools':tools,'schema':response_schema,
                               'role':role,'selected_model':settings['gguf_model_path']})
            if isinstance(self.reply,Exception):raise self.reply
            return self.reply
        def close(self):self.closed+=1
    package=ModuleType('common_ai');package.__path__=[]
    storage=ModuleType('common_ai.settings');storage.read_settings=lambda _root:dict(settings)
    client=ModuleType('common_ai.client');client.Client=Client
    for name,module in [('common_ai',package),('common_ai.settings',storage),('common_ai.client',client)]:
        monkeypatch.setitem(sys.modules,name,module)
    import requests
    def deny(*_args,**_kwargs):raise AssertionError('WATCH attempted direct external HTTP')
    monkeypatch.setattr(requests,'post',deny)
    monkeypatch.setattr(requests,'get',deny)
    return SimpleNamespace(settings=settings,clients=clients)


def services(tmp_path):
    aliases=tmp_path/'aliases.json'
    aliases.write_text(json.dumps(DEFAULT_COMMAND_LANGUAGE),encoding='utf-8')
    interpreter=CommandInterpreter(CONFIG,aliases,
                                   allowed_symbols_provider=lambda:['XAUUSD','BTCUSD'])
    return HTTPServices(CONFIG,interpreter)


def test_common_schema_prompt_and_existing_validator(connection,tmp_path):
    http=services(tmp_path)
    text='골드 1분 뭔가 모르는 새로운 문장 알려줘'
    assert http.canonicalize(text)=='골드 1분 상단 원비 터치 알려줘'
    assert len(connection.clients)==1
    client=connection.clients[0]
    assert client.root==ROOT
    call=client.calls[0]
    assert call['role']=='watch' and call['tools']==[]
    assert call['messages']==[{'role':'user','content':http.interpreter.gemini_prompt(text)}]
    assert call['schema']=={'type':'object','properties':{'canonical_text':{'type':'string'}},
                            'required':['canonical_text'],'additionalProperties':False}
    assert 'legacy-model-must-not-be-used' not in json.dumps(call)
    assert 'synthetic secret' not in json.dumps(call)


def test_watch_uses_current_selection_and_reuses_client(connection,tmp_path):
    http=services(tmp_path)
    text='골드 1분 뭔가 모르는 새로운 문장 알려줘'
    assert http.canonicalize(text)
    connection.settings['gguf_model_path']='models/gguf/another-model.gguf'
    assert http.canonicalize(text)
    assert len(connection.clients)==1
    assert [c['selected_model'] for c in connection.clients[0].calls]==[
        'models/gguf/one.gguf','models/gguf/another-model.gguf']


def test_common_watch_disable_does_not_start_client(connection,tmp_path):
    connection.settings['watch_enabled']=False
    assert services(tmp_path).canonicalize('골드 1분 모르는 문장 알려줘') is None
    assert not connection.clients


@pytest.mark.parametrize('original,candidate',[
    ('XAUUSD 1분 원비 알려줘','BTCUSD 1분 상단 원비 알려줘'),
    ('원비 알려줘','BTCUSD 상단 원비 알려줘'),
    ('골드 원비 알려줘','골드 15분 상단 원비 알려줘'),
    ('골드 1분 원비 알려줘','골드 1분 무지성 원비 알려줘'),
    ('골드 1분 무지성 원비 알려줘','골드 1분 원비 알려줘'),
    ('골드 1분 올존 알려줘','골드 1분 원비 알려줘'),
    ('골드 1분 원비 알려줘','골드 1분 브레이커 원비 알려줘'),
])
def test_existing_semantic_rejections_preserved(connection,tmp_path,original,candidate):
    http=services(tmp_path)
    # First obtain the common client, then vary only its untrusted AI output.
    http.canonicalize('골드 1분 모르는 문장 알려줘')
    connection.clients[0].reply={'content':json.dumps({'canonical_text':candidate},ensure_ascii=False),
                               'tool_calls':[]}
    assert http.canonicalize(original) is None


@pytest.mark.parametrize('reply',[
    {'content':'not JSON','tool_calls':[]},
    {'content':None,'tool_calls':[]},
    {'content':'{"canonical_text":""}','tool_calls':[]},
    {'content':'{"canonical_text":"골드 1분 상단 원비 알려줘"}',
     'tool_calls':[{'name':'apply_strategy','arguments':{}}]},
    RuntimeError('private-user-command synthetic secret'),
])
def test_failures_return_none_without_private_text_or_keys(connection,tmp_path,caplog,reply):
    http=services(tmp_path)
    http.canonicalize('골드 1분 모르는 문장 알려줘')
    connection.clients[0].reply=reply
    with caplog.at_level(logging.WARNING):
        assert http.canonicalize('private-user-command') is None
    assert 'private-user-command' not in caplog.text
    assert 'synthetic secret' not in caplog.text


def host(tmp_path):
    engine=create_event_engine(CONFIG,symbols=('XAUUSD',),enabled_specials=(),
                               aliases=DEFAULT_COMMAND_LANGUAGE)
    http=services(tmp_path)
    sent=[]
    def transport(data):
        sent.append(dict(data))
        return SimpleNamespace(status_code=200,json=lambda:{'ok':True,'result':{'message_id':900+len(sent)}})
    application=EventHost(engine,CONFIG,http.interpreter,transport=transport,external=http)
    return engine,application,http,sent


def post(engine,text):
    engine.ingress.post(Kind.COMMAND,source='telegram',source_seq=1,source_time=1790380680000,
                        payload={'symbol':'XAUUSD','chat_id':'user','text':text,'message_id':10})
    engine.run()
    assert not engine.error_log,engine.error_log


def test_valid_local_watch_never_calls_ai(connection,tmp_path):
    engine,application,_http,sent=host(tmp_path)
    post(engine,'골드 1분 상단 원비 터치 알려줘')
    application.drain_outputs()
    assert application.external_requests.empty()
    assert not connection.clients
    assert any('감시' in data['text'] for data in sent)


def test_unknown_command_normalizes_then_registers_through_existing_parser(connection,tmp_path):
    engine,application,http,sent=host(tmp_path)
    post(engine,'골드 1분 뭔가 모르는 새로운 문장 알려줘')
    request=application.external_requests.get_nowait()
    assert request.payload['content']['type']=='EXTERNAL_REQUEST'
    # No watch is accepted before normalization and the existing parser retry.
    assert not any('감시' in data['text'] for data in sent)
    canonical=http.canonicalize(request.payload['content']['text'])
    assert canonical=='골드 1분 상단 원비 터치 알려줘'
    assert application.inputs.external_reply(request,canonical,received_time=1790380680001)
    engine.run();application.drain_outputs()
    assert not engine.error_log,engine.error_log
    assert len(connection.clients[0].calls)==1
    assert any('상단 원비' in data['text'] and '감시' in data['text'] for data in sent)
    # A duplicate AI delivery must not register another watch.
    before=len(sent)
    assert not application.inputs.external_reply(request,canonical,received_time=1790380680002)
    engine.run();application.drain_outputs()
    assert len(sent)==before


def test_close_detaches_this_client_and_is_idempotent(connection,tmp_path):
    http=services(tmp_path)
    assert http.canonicalize('골드 1분 모르는 문장 알려줘')
    client=connection.clients[0]
    # There is intentionally no shutdown call: another UI may own the service.
    http.close();http.close()
    assert client.closed==1 and http.ai is None


def test_event_host_close_detaches_common_connection(connection,tmp_path):
    _engine,application,http,_sent=host(tmp_path)
    assert http.canonicalize('골드 1분 모르는 문장 알려줘')
    assert application.close(timeout=0)
    assert connection.clients[0].closed==1


def test_close_interrupts_active_watch_before_join_and_does_not_restart_queued_ai(connection,tmp_path,monkeypatch):
    engine,application,http,_sent=host(tmp_path)
    assert http.canonicalize('골드 1분 모르는 문장 알려줘')
    client=connection.clients[0]
    entered=threading.Event();released=threading.Event();calls=[]
    original_close=client.close
    def slow_chat(*_args,**_kwargs):
        calls.append('active')
        entered.set()
        assert released.wait(3),'WATCH client was not interrupted during shutdown'
        return client.reply
    def detach():
        original_close();released.set()
    monkeypatch.setattr(client,'chat',slow_chat)
    monkeypatch.setattr(client,'close',detach)
    post(engine,'골드 1분 뭔가 모르는 새로운 문장 알려줘')
    queued=SimpleNamespace(payload={'symbol':'XAUUSD','signal_id':'queued-watch',
        'content':{'type':'EXTERNAL_REQUEST','text':'골드 1분 다른 모르는 문장 알려줘',
                   'command':{'text':'골드 1분 다른 모르는 문장 알려줘','chat_id':'user'}}})
    application._signal(queued)
    worker=threading.Thread(target=application._external_loop,daemon=True)
    application.workers.append(worker)
    worker.start()
    try:
        assert entered.wait(1)
        started=time.monotonic()
        assert application.close(timeout=1)
        assert time.monotonic()-started<1
        assert not worker.is_alive()
        assert client.closed==1 and len(connection.clients)==1 and calls==['active']
        assert http.canonicalize('골드 1분 종료 후 문장 알려줘') is None
        assert len(connection.clients)==1
    finally:
        released.set();worker.join(timeout=3)


def test_close_before_first_request_prevents_lazy_client_creation(connection,tmp_path):
    http=services(tmp_path)
    http.close()
    assert http.canonicalize('골드 1분 모르는 문장 알려줘') is None
    assert not connection.clients


def test_part1_standalone_import_and_watch_do_not_import_part3(tmp_path):
    script=r'''
import importlib.abc,json,sys,types
from pathlib import Path
root=Path(sys.argv[1])
sys.path[:0]=[str(root/'Part1/program'),str(root)]
class NoPart3(importlib.abc.MetaPathFinder):
    def find_spec(self,fullname,path=None,target=None):
        if fullname.split('.')[0] in ('Part3','lab'):
            raise AssertionError('Part1 attempted to import Part3: '+fullname)
sys.meta_path.insert(0,NoPart3())
import common_ai.client,common_ai.settings
import event_host
class Client:
    def __init__(self,project):assert project==root
    def chat(self,messages,tools,response_schema,*,role):
        assert role=='watch' and tools==[]
        return {'content':'{"canonical_text":"valid unchanged text"}','tool_calls':[]}
    def close(self):pass
common_ai.client.Client=Client
common_ai.settings.read_settings=lambda _root:{'provider':'local_gguf','watch_enabled':True}
interpreter=types.SimpleNamespace(gemini_prompt=lambda text:text,
    validate_gemini_reply=lambda text,raw:json.loads(raw)['canonical_text'])
http=event_host.HTTPServices({},interpreter)
assert http.canonicalize('valid unchanged text')=='valid unchanged text'
http.close()
assert not any(name=='Part3' or name.startswith('lab.') for name in sys.modules)
print('Part1 standalone common AI PASS')
'''
    result=subprocess.run([sys.executable,'-B','-X','utf8','-c',script,str(ROOT)],cwd=tmp_path,
                          capture_output=True,text=True,encoding='utf-8',timeout=30)
    assert result.returncode==0,result.stdout+result.stderr
    assert 'Part1 standalone common AI PASS' in result.stdout
