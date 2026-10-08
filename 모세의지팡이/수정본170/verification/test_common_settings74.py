"""Portable common settings, optional Gemini contract and serialized remote turns."""
from __future__ import annotations

import copy
import io
import json
from pathlib import Path
import sys
import time
from types import SimpleNamespace
import urllib.error

import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from common_ai import gemini,model_runtime
from common_ai.gemini import Gemini,validate_settings
from common_ai.provider import configured_settings,from_settings
from common_ai.settings import masked_settings,read_settings


VALUE={'provider':'local_gguf','gguf_model_path':'models/gguf/user-file.gguf',
       'llama_server_path':'runtime/llama.cpp/llama-server.exe','timeout':90}
REMOTE={'provider':'gemini','gemini_model':'user-selected-remote-model',
        'gemini_api_key':'synthetic_KEY_123','timeout':90}
SCHEMA={'type':'object','properties':{'canonical_text':{'type':'string'}},
        'required':['canonical_text'],'additionalProperties':False}
TOOLS=[{'type':'function','function':{'name':'vocabulary','description':'Read strategy vocabulary',
    'parameters':{'type':'object','properties':{},'additionalProperties':False}}}]


def store(root,relative,value):
    path=root/relative;path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value),encoding='utf-8')
    return path


def test_missing_settings_default_to_gemini_without_choosing_a_model(tmp_path):
    value=read_settings(tmp_path)
    assert value['provider']=='gemini' and value['watch_enabled'] is True
    assert not value.get('gguf_model_path') and not value.get('model')
    assert not list(tmp_path.iterdir())


def test_common_settings_win_and_legacy_is_read_only(tmp_path):
    old=store(tmp_path,'Part3/projects/ai_settings.json',{'provider':'ollama','model':'prior:tag','timeout':90})
    before=old.read_bytes()
    assert read_settings(tmp_path)['model']=='prior:tag'
    store(tmp_path,'settings/ai_settings.json',VALUE)
    assert read_settings(tmp_path)['gguf_model_path']==VALUE['gguf_model_path']
    assert old.read_bytes()==before


@pytest.mark.parametrize('raw',['not JSON','[]','null'])
def test_corrupt_common_file_is_not_replaced_by_legacy(tmp_path,raw):
    old=store(tmp_path,'Part3/projects/ai_settings.json',{'provider':'ollama','model':'prior:tag'})
    current=tmp_path/'settings/ai_settings.json';current.parent.mkdir()
    current.write_text(raw,encoding='utf-8')
    with pytest.raises(ValueError,match='AI 설정'):
        read_settings(tmp_path)
    assert current.read_text('utf-8')==raw and json.loads(old.read_text('utf-8'))['model']=='prior:tag'


def test_settings_remain_portable_when_project_moves(tmp_path):
    original=tmp_path/'original';original.mkdir()
    store(original,'settings/ai_settings.json',VALUE)
    first=read_settings(original)
    moved=tmp_path/'새 위치';original.rename(moved)
    assert read_settings(moved)==first
    assert first['gguf_model_path']==VALUE['gguf_model_path']
    assert first['llama_server_path']==VALUE['llama_server_path']


@pytest.mark.parametrize('updates',[
    {'gguf_model_path':'../outside.gguf'},
    {'gguf_model_path':'Z:/private/model.gguf'},
    {'llama_server_path':'/outside/llama-server.exe'},
    {'watch_enabled':'true'},
    {'timeout':True},
    {'timeout':301},
])
def test_invalid_paths_flags_and_timeout_fail_honestly(tmp_path,updates):
    store(tmp_path,'settings/ai_settings.json',{**VALUE,**updates})
    with pytest.raises(ValueError):read_settings(tmp_path)


def test_masked_settings_never_return_or_modify_stored_key():
    original={**VALUE,**REMOTE,'watch_enabled':False}
    before=copy.deepcopy(original)
    masked=masked_settings(original)
    assert original==before
    assert 'gemini_api_key' not in masked
    assert masked['gemini_api_key_configured'] is True
    assert masked['gguf_model_path']==VALUE['gguf_model_path']
    assert masked_settings(VALUE)['gemini_api_key_configured'] is False


def test_incomplete_gemini_settings_can_be_read_but_cannot_execute(tmp_path):
    store(tmp_path,'settings/ai_settings.json',{'provider':'gemini'})
    value=read_settings(tmp_path)
    assert value['provider']=='gemini'
    with pytest.raises(ValueError,match='모델명'):Gemini(value)
    with pytest.raises(ValueError,match='연결 키'):
        Gemini({'provider':'gemini','gemini_model':'chosen-model'})
    assert configured_settings(VALUE)['provider']=='local_gguf'


@pytest.mark.parametrize('updates',[
    {'gemini_model':'https://untrusted/model'},
    {'gemini_model':'chosen?key=leak'},
    {'gemini_model':'chosen/model'},
    {'gemini_model':'chosen\nmodel'},
    {'gemini_api_key':'key\nHeader:injection'},
    {'gemini_api_key':'key with spaces'},
    {'timeout':0},
    {'max_new_tokens':False},
])
def test_gemini_identifiers_do_not_redirect_or_inject_headers(updates):
    with pytest.raises(ValueError):validate_settings({**REMOTE,**updates},required=True)
    assert validate_settings({**REMOTE,'gemini_model':'models/my-model.v2'},required=True)['gemini_model']=='my-model.v2'


class Reply:
    def __init__(self,value):self.data=value if isinstance(value,bytes) else json.dumps(value).encode('utf-8')
    def __enter__(self):return self
    def __exit__(self,*_args):return False
    def read(self,limit):return self.data[:limit]


def packet(value,**candidate):
    return {'candidates':[{'finishReason':'STOP','content':{'parts':[{'text':json.dumps(value,ensure_ascii=False)}]},**candidate}]}


@pytest.fixture
def remote(monkeypatch):
    seen=[];replies=[];guard=[]
    def guarded(provider,operation):
        guard.append(provider)
        return operation(time.monotonic()+provider.timeout)
    monkeypatch.setattr(model_runtime.RUNTIME,'remote_chat',guarded)
    def send(request,*,timeout):
        seen.append({'url':request.full_url,'headers':dict(request.header_items()),
                     'body':json.loads(request.data),'timeout':timeout})
        value=replies.pop(0)
        if isinstance(value,Exception):raise value
        return Reply(value)
    monkeypatch.setattr(gemini,'_open_request',send)
    return SimpleNamespace(seen=seen,replies=replies,guard=guard)


def test_gemini_uses_selected_model_key_header_and_watch_schema(remote):
    remote.replies.append(packet({'canonical_text':'1분 상승추세'}))
    provider=Gemini({**REMOTE,'gemini_model':'my-changing-model','max_new_tokens':128})
    value=provider.chat([{'role':'user','content':'1분 상승추세'}],[],response_schema=SCHEMA)
    assert json.loads(value['content'])=={'canonical_text':'1분 상승추세'} and not value['tool_calls']
    sent=remote.seen[0]
    assert sent['url']==gemini.API_ROOT+'my-changing-model:generateContent'
    assert sent['headers']['X-goog-api-key']==REMOTE['gemini_api_key']
    assert REMOTE['gemini_api_key'] not in sent['url']+json.dumps(sent['body'])
    assert sent['body']['generationConfig']['responseJsonSchema']==SCHEMA
    assert sent['body']['generationConfig']['maxOutputTokens']==128
    assert 0<sent['timeout']<=90 and remote.guard==[provider]


def test_gemini_json_tool_loop_preserves_agent_messages_and_schema(remote):
    contract={'purpose':'strategy','moses_contract':{'vocabulary':{'intent_kinds':['TREND']}}}
    messages=[{'role':'system','content':json.dumps(contract)},{'role':'user','content':'describe strategy'}]
    schema={'type':'object','properties':{'answer':{'const':'complete'}},'required':['answer'],'additionalProperties':False}
    before=copy.deepcopy(messages);original_schema=copy.deepcopy(schema)
    remote.replies.append(packet({'tool_calls':[{'name':'vocabulary','arguments':{}}]}))
    provider=Gemini(REMOTE)
    first=provider.chat(messages,TOOLS,response_schema=schema)
    assert first['content']=='' and first['tool_calls']==[{'id':'local_0','name':'vocabulary','arguments':{}}]
    call=first['tool_calls'][0]
    messages.extend([
        {'role':'assistant','content':'','tool_calls':[{'id':call['id'],'function':{'name':call['name'],'arguments':'{}'}}]},
        {'role':'tool','name':call['name'],'tool_call_id':call['id'],'content':'{"ok":true,"result":{"intent_kinds":["TREND"]}}'}])
    history=copy.deepcopy(messages)
    remote.replies.append(packet({'answer':'complete'}))
    final=provider.chat(messages,TOOLS,response_schema=schema)
    assert json.loads(final['content'])=={'answer':'complete'} and not final['tool_calls']
    assert messages==history and messages[:2]==before and schema==original_schema
    sent=remote.seen[-1]['body']
    assert sent['contents'][-2]['role']=='model' and 'tool_calls' in sent['contents'][-2]['parts'][0]['text']
    assert sent['contents'][-1]['role']=='user' and 'tool_result' in sent['contents'][-1]['parts'][0]['text']
    # Vocabulary is available through its permitted tool result; duplicated
    # static vocabulary text is no longer required in the compact prefix.
    tool_result=json.loads(sent['contents'][-1]['parts'][0]['text'])['tool_result']
    assert json.loads(tool_result['content'])=={'ok':True,'result':{'intent_kinds':['TREND']}}
    assert sent['generationConfig']['responseJsonSchema']['anyOf'][0]['properties']['answer']=={'enum':['complete']}


@pytest.mark.parametrize('value',[
    packet({'canonical_text':7}),
    packet({'canonical_text':'valid','extra':'invented'}),
    packet({'tool_calls':[{'name':'apply_strategy','arguments':{}}]}),
    {'candidates':[]},
    {'candidates':[{'finishReason':'SAFETY','content':{'parts':[]}}]},
    packet({'canonical_text':'truncated'},finishReason='MAX_TOKENS'),
    b'not JSON',
])
def test_gemini_invalid_or_incomplete_results_are_not_accepted(remote,value):
    remote.replies.append(value)
    with pytest.raises(ValueError,match='Gemini'):
        Gemini(REMOTE).chat([{'role':'user','content':'normalize'}],[],response_schema=SCHEMA)


@pytest.mark.parametrize('status',[400,401,403,404,429,500])
def test_gemini_http_errors_hide_secret_and_raw_api_response(remote,status):
    secret='synthetic_KEY_123 private-user-text'
    remote.replies.append(urllib.error.HTTPError(gemini.API_ROOT,status,secret,{},io.BytesIO(secret.encode())))
    with pytest.raises(ValueError,match='HTTP '+str(status)) as error:
        Gemini(REMOTE).chat([{'role':'user','content':'private-user-text'}],[],response_schema=SCHEMA)
    assert REMOTE['gemini_api_key'] not in str(error.value)
    assert 'private-user-text' not in str(error.value)


def test_gemini_ignores_thought_text_and_parses_only_final_output(remote):
    remote.replies.append({'candidates':[{'finishReason':'STOP','content':{'parts':[
        {'thought':True,'text':'internal reasoning'},
        {'text':'{"canonical_text":"final"}'}]}}]})
    assert json.loads(Gemini(REMOTE).chat([{'role':'user','content':'text'}],[],response_schema=SCHEMA)['content'])=={'canonical_text':'final'}


def guarded_runtime(monkeypatch):
    runtime=model_runtime.ModelRuntime()
    events=[];loaded=['previous:tag']
    class Lease:
        held=False
        def acquire(self):self.held=True;events.append('lease_acquired')
        def release(self):self.held=False;events.append('lease_released')
    runtime.lease=Lease()
    def http(route,deadline,data=None):
        events.append(route)
        if route=='/api/ps':return {'models':[{'name':name} for name in loaded]}
        assert route=='/api/generate' and data=={'model':'previous:tag','keep_alive':0,'stream':False}
        loaded.clear();return {}
    monkeypatch.setattr(runtime,'_http',http)
    return runtime,events,loaded


def test_remote_turn_closes_local_model_without_checking_unselected_ollama(monkeypatch):
    runtime,events,loaded=guarded_runtime(monkeypatch)
    runtime.worker=SimpleNamespace(close=lambda:events.append('local_closed'))
    provider=SimpleNamespace(generation=runtime.generation,timeout=1)
    def operation(_deadline):
        assert runtime.worker is None and loaded==['previous:tag'] and runtime.lease.held
        events.append('external_called');return {'content':'{}','tool_calls':[]}
    assert runtime.remote_chat(provider,operation)=={'content':'{}','tool_calls':[]}
    assert events.index('local_closed')<events.index('external_called')
    assert '/api/ps' not in events and '/api/generate' not in events
    assert loaded==['previous:tag'] and not runtime.lease.held


def test_remote_turn_rejects_response_after_settings_change(monkeypatch):
    runtime,events,_loaded=guarded_runtime(monkeypatch)
    provider=SimpleNamespace(generation=runtime.generation,timeout=1)
    def operation(_deadline):runtime.invalidate();return {'content':'stale','tool_calls':[]}
    with pytest.raises(ValueError,match='설정이 변경'):runtime.remote_chat(provider,operation)
    assert not runtime.lease.held and not runtime.lock.locked()


def test_failed_local_unload_does_not_call_remote_and_keeps_model_lease(monkeypatch):
    runtime,events,_loaded=guarded_runtime(monkeypatch)
    def close():events.append('close_failed');raise RuntimeError('worker is still resident')
    runtime.worker=SimpleNamespace(close=close)
    provider=SimpleNamespace(generation=runtime.generation,timeout=1)
    def operation(_deadline):raise AssertionError('remote must not execute before unload confirmation')
    with pytest.raises(RuntimeError,match='resident'):runtime.remote_chat(provider,operation)
    assert runtime.worker is not None and runtime.lease.held
    assert not runtime.lock.locked() and '/api/ps' not in events
