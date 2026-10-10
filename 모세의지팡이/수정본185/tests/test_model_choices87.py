"""Warehouse defaults and Gemini inventory: no real engine or external API."""
import io
import json
from pathlib import Path
import sys
import time
from types import SimpleNamespace
import urllib.error
from unittest.mock import Mock

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT/'Part3'), str(ROOT/'verification')]
from common_ai import client, gemini, model_runtime
from common_ai.provider import from_settings
from lab import server, unified_backtest, unified_settings
from test_backtest_routes67 import handler

KEY = 'AQ.synthetic.inventory-key'


class Reply:
    def __init__(self, data): self.payload = json.dumps(data).encode()
    def __enter__(self): return self
    def __exit__(self, *_): pass
    def read(self, limit): return self.payload[:limit]


@pytest.mark.parametrize('symbols,expected', [
    (['EURUSD','NAS100','XAUUSD+'], 'XAUUSD+'),
    (['EURUSD','GOLDm','USTEC'], 'GOLDm'),
    (['EURUSD','NAS100'], 'NAS100'),
    (['EURUSD','USTEC+'], 'USTEC+'),
    (['gold.micro','nas100'], 'gold.micro'),
    (['EURUSD','USDJPY'], 'EURUSD'), ([], '')])
def test_existing_symbol_preference(symbols, expected):
    assert unified_backtest._default_symbol(symbols) == expected
    assert expected in symbols or not symbols


def test_list_pages_only_callable_actual_ids_and_key_in_header(monkeypatch):
    pages = [
        {'models': [{'name':'models/gemini-future','displayName':'Display name',
                     'supportedGenerationMethods':['generateContent']},
                    {'name':'models/embed','supportedGenerationMethods':['embedContent']}],
         'nextPageToken':'next+/='},
        {'models': [{'name':'models/gemini-another','supportedGenerationMethods':['generateContent']},
                    {'name':'models/gemini-future','supportedGenerationMethods':['generateContent']},
                    {'name':'models/'+KEY,'supportedGenerationMethods':['generateContent']}]}]
    sent = []
    def send(request, *, timeout):
        sent.append(request)
        assert 0 < timeout <= 10
        assert request.get_header('X-goog-api-key') == KEY
        assert request.data is None and KEY not in request.full_url
        return Reply(pages.pop(0))
    monkeypatch.setattr(gemini, '_open_request', send)
    result = gemini.list_models({'gemini_api_key':KEY, 'gemini_model':'bad/display model'})
    assert result == {'available':True, 'models':['gemini-another','gemini-future'], 'error':None}
    assert len(sent) == 2 and 'pageToken=next%2B%2F%3D' in sent[1].full_url
    assert KEY not in json.dumps(result)


@pytest.mark.parametrize('data', [[], {'models':None}, {'models':[{'supportedGenerationMethods':None}]},
                                     {'models':[], 'nextPageToken':[]}, {'models':[], 'nextPageToken':'repeat'}])
def test_invalid_inventory_falls_back_without_echo(monkeypatch, data):
    monkeypatch.setattr(gemini, '_open_request', lambda *a,**k:Reply(data))
    result = gemini.list_models({'gemini_api_key':KEY})
    assert not result['available'] and result['models'] == [] and KEY not in json.dumps(result)


@pytest.mark.parametrize('code', [400,403,429])
def test_key_or_network_error_is_safe(monkeypatch, code):
    def send(*a, **k):
        raise urllib.error.HTTPError('https://example.invalid',code,KEY,{},
            io.BytesIO(json.dumps({'error':{'message':KEY}}).encode()))
    monkeypatch.setattr(gemini, '_open_request', send)
    result = gemini.list_models({'gemini_api_key':KEY})
    assert not result['available'] and KEY not in json.dumps(result)


def test_no_key_does_not_request(monkeypatch):
    send = Mock(side_effect=AssertionError('No credential'))
    monkeypatch.setattr(gemini, '_open_request', send)
    assert not gemini.list_models({})['available']
    send.assert_not_called()


def test_authenticated_inventory_get_and_preview_key_post(monkeypatch):
    saved = {'provider':'gemini','gemini_api_key':'saved.fake-key','gemini_model':'saved-model'}
    monkeypatch.setattr(server, 'ai_settings', lambda:dict(saved))
    listing = Mock(return_value={'available':True,'models':['gemini-future'],'error':None})
    monkeypatch.setattr(gemini, 'list_models', listing)
    for method, body in [('GET',None), ('POST',{'gemini_api_key':KEY})]:
        value = handler('/api/ai/gemini-models',body)
        getattr(value,'do_'+method)()
        assert value.replies[-1][0] == 200
        assert 'api_key' not in json.dumps(value.replies)
    assert listing.call_args.args[0]['gemini_api_key'] == KEY
    assert saved['gemini_api_key'] == 'saved.fake-key'
    with pytest.raises(ValueError): server.ai_post('/api/ai/gemini-models',{'other':'forbidden'})


@pytest.mark.parametrize('token,origin', [('',None),('wrong',None),('test-token','https://example.test')])
def test_inventory_respects_existing_ui_authorization(monkeypatch, token, origin):
    listing = Mock(side_effect=AssertionError('Unauthorized'))
    monkeypatch.setattr(gemini, 'list_models', listing)
    for method, body in [('GET',None),('POST',{'gemini_api_key':KEY})]:
        value = handler('/api/ai/gemini-models',body,token,origin)
        getattr(value,'do_'+method)()
        assert value.replies[-1][0] == 403
    listing.assert_not_called()


def test_selected_model_saves_and_is_used_in_generation(tmp_path, monkeypatch):
    saved = tmp_path/'settings/ai_settings.json'
    saved.parent.mkdir()
    saved.write_text(json.dumps({'provider':'gemini','gemini_model':'old-model',
                                 'gemini_api_key':KEY,'timeout':90}), encoding='utf-8')
    monkeypatch.setattr(server,'AI_SETTINGS',saved)
    monkeypatch.setattr(server,'ROOT',tmp_path/'Part3')
    for name in ('AI_SESSIONS','RESEARCH_SESSIONS','BACKTEST_COMMAND_SESSIONS'):
        monkeypatch.setattr(server,name,{})
    monkeypatch.setattr(client.Client,'_lookup',Mock(return_value=None))
    monkeypatch.setattr(client.Client,'_connect',Mock(side_effect=AssertionError('No AI service connection')))
    monkeypatch.setattr(client.Client,'_start',Mock(side_effect=AssertionError('No AI service startup')))
    monkeypatch.setattr(model_runtime.RUNTIME,'invalidate',lambda:None)
    sent=[]
    def send(request,*,timeout):
        sent.append(request)
        return Reply({'candidates':[{'finishReason':'STOP','content':{'parts':[{'text':'{"answer":"valid"}'}]}}]})
    monkeypatch.setattr(gemini,'_open_request',send)
    monkeypatch.setattr(model_runtime.RUNTIME,'remote_chat',lambda provider,operation:operation(time.monotonic()+90))
    assert unified_settings.save_ai({'gemini_model':'gemini-arbitrary-future'})['ok']
    assert sent == []  # A model-only edit does not send the strategy or recheck credentials.
    updated=server.ai_settings()
    assert updated['gemini_model']=='gemini-arbitrary-future'
    assert json.loads(saved.read_text('utf-8'))['gemini_model']=='gemini-arbitrary-future'
    schema={'type':'object','properties':{'answer':{'type':'string'}},'required':['answer'],'additionalProperties':False}
    result=from_settings(updated).chat([{'role':'user','content':'해석'}],[],response_schema=schema)
    assert json.loads(result['content'])=={'answer':'valid'}
    assert sent[0].full_url.endswith('/gemini-arbitrary-future:generateContent')
    assert sent[0].get_header('X-goog-api-key')==KEY
    client.Client._connect.assert_not_called()
    client.Client._start.assert_not_called()
