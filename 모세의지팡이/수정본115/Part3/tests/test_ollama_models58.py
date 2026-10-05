"""Installed models -> authenticated settings -> native provider HTTP request."""
from __future__ import annotations

import http.client
from contextlib import ExitStack
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
import urllib.error
from unittest.mock import patch

PART3 = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PART3))
from lab import ollama_models, server
from lab.ai import provider
from lab.ai.schema import output_schema
from common_ai.client import Client


class Reply:
    def __init__(self, value): self.value = value
    def __enter__(self): return self
    def __exit__(self, *_): pass
    def read(self, size): return self.value


class ModelListTests(unittest.TestCase):
    def test_reads_local_tags_deduplicates_and_sorts_names(self):
        body = {'models':[{'name':'qwen3:8b'},{'model':'qwen3.5:4b'},{'name':'qwen3:8b'}]}
        with patch.object(ollama_models.urllib.request, 'urlopen', return_value=Reply(json.dumps(body).encode())) as send:
            result = ollama_models.list_models()
        self.assertEqual(result, {'available':True,'models':['qwen3.5:4b','qwen3:8b'],'error':None})
        request = send.call_args.args[0]
        self.assertEqual(request.full_url, provider.BASE_URL + '/api/tags')
        self.assertEqual(request.get_method(), 'GET')
        self.assertIsNone(request.data)
        self.assertEqual(send.call_args.kwargs['timeout'], 3)

    def test_offline_timeout_and_http_error_return_manual_fallback(self):
        for error in (urllib.error.URLError('offline'), TimeoutError('slow'),
                      urllib.error.HTTPError(provider.BASE_URL,500,'error',{},None)):
            with self.subTest(error=type(error).__name__), \
                 patch.object(ollama_models.urllib.request,'urlopen',side_effect=error):
                response=ollama_models.list_models()
                self.assertFalse(response['available'])
                self.assertEqual(response['models'],[])
                self.assertIn('직접 입력',response['error'])

    def test_malformed_response_and_invalid_name_return_fallback(self):
        for value in (b'not json', b'[]', b'{}', b'{"models":null}', b'{"models":[1]}',
                      b'{"models":[{"name":"bad model"}]}'):
            with self.subTest(value=value), \
                 patch.object(ollama_models.urllib.request,'urlopen',return_value=Reply(value)):
                self.assertFalse(ollama_models.list_models()['available'])

    def test_empty_installation_is_a_successful_empty_list(self):
        with patch.object(ollama_models.urllib.request,'urlopen',return_value=Reply(b'{"models":[]}')):
            self.assertEqual(ollama_models.list_models(),{'available':True,'models':[],'error':None})

    def test_model_lookup_does_not_create_provider_or_change_settings(self):
        with patch.object(ollama_models.urllib.request,'urlopen',return_value=Reply(b'{"models":[]}')), \
             patch.object(server,'ai_agent',side_effect=AssertionError('model was loaded')), \
             patch.object(server,'ai_settings',side_effect=AssertionError('settings were read')):
            self.assertTrue(ollama_models.list_models()['available'])

    def test_authenticated_api_and_new_static_display_script(self):
        host=server.LabServer(0); worker=threading.Thread(target=host.serve_forever,daemon=True);worker.start()
        try:
            for token, expected in (('',403),(host.token,200)):
                client=http.client.HTTPConnection('127.0.0.1',host.server_port,timeout=5)
                with patch.object(ollama_models,'list_models',return_value={'available':True,'models':['qwen3.5:4b'],'error':None}) as lookup:
                    client.request('GET','/api/ai/models',headers={'X-Lab-Token':token})
                    response=client.getresponse();body=json.loads(response.read());client.close()
                    self.assertEqual(response.status,expected)
                    self.assertEqual(lookup.call_count,1 if token else 0)
                    if token:self.assertEqual(body['models'],['qwen3.5:4b'])
            client=http.client.HTTPConnection('127.0.0.1',host.server_port,timeout=5)
            client.request('GET','/ai_display.js');response=client.getresponse()
            self.assertEqual(response.status,200)
            self.assertIn(b'part3IntentDisplay',response.read());client.close()
        finally:
            host.shutdown();host.server_close();worker.join(2)

    def test_selected_model_saved_and_used_over_real_http_then_changed_in_same_session(self):
        captured=[]
        class OllamaStub(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def reply(self,value):
                body=json.dumps(value).encode();self.send_response(200)
                self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(body)))
                self.end_headers();self.wfile.write(body)
            def do_GET(self):
                if self.path=='/api/ps':
                    return self.reply({'models':[]})
                self.assert_route('/api/tags')
                self.reply({'models':[{'name':'qwen3.5:4b'},{'name':'qwen3:8b'}]})
            def assert_route(self,route):
                if self.path!=route:raise AssertionError(self.path)
            def do_POST(self):
                if self.path=='/api/generate':
                    body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                    assert body['keep_alive']==0 and body['stream'] is False
                    return self.reply({'done':True})
                self.assert_route('/api/chat')
                captured.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
                self.reply({'message':{'content':json.dumps({'supported':False,'reason':'MOSES_SCOPE_ONLY',
                    'message_ko':'호출 확인용 시험 응답'})}})
        upstream=ThreadingHTTPServer(('127.0.0.1',0),OllamaStub)
        upstream_thread=threading.Thread(target=upstream.serve_forever,daemon=True);upstream_thread.start()
        host=server.LabServer(0);worker=threading.Thread(target=host.serve_forever,daemon=True);worker.start()
        def request(method,route,data=None):
            client=http.client.HTTPConnection('127.0.0.1',host.server_port,timeout=5)
            client.request(method,route,body=json.dumps(data) if data is not None else None,
                headers={'X-Lab-Token':host.token,'Content-Type':'application/json'})
            response=client.getresponse();body=json.loads(response.read());client.close()
            self.assertEqual(response.status,200,body);return body
        try:
            with tempfile.TemporaryDirectory() as folder, \
                 patch.object(server,'ROOT',Path(folder)/'moved'/'Part3'), \
                 patch.object(server,'AI_SETTINGS',Path(folder)/'moved'/'settings'/'ai_settings.json'), \
                 patch.object(server,'_AI_CLIENT',None), \
                 patch.object(server,'AI_SESSIONS',{}), \
                 patch.object(server,'RESEARCH_SESSIONS',{}), \
                 patch.object(server,'BACKTEST_COMMAND_SESSIONS',{}), \
                 patch.object(Client,'_start',side_effect=AssertionError('No common AI service in this fixture')) as start, \
                 patch.object(Client,'_request',side_effect=AssertionError('No common AI service in this fixture')) as send, \
                 patch.object(provider,'BASE_URL',f'http://127.0.0.1:{upstream.server_port}'), \
                 patch('lab.ai.shared_provider.shared_from_settings',
                       side_effect=lambda settings, **_kwargs: provider.from_settings(settings)), \
                 patch.object(ollama_models,'BASE_URL',f'http://127.0.0.1:{upstream.server_port}'), \
                 ExitStack() as cleanup:
                cleanup.callback(start.assert_not_called)
                cleanup.callback(send.assert_not_called)
                cleanup.callback(server.close_ai_client)
                self.assertEqual(request('GET','/api/ai/models')['models'],['qwen3.5:4b','qwen3:8b'])
                request('POST','/api/mo/settings',{'group':'ai','changes':{'model':'qwen3.5:4b'}})
                self.assertEqual(json.loads(server.AI_SETTINGS.read_text())['model'],'qwen3.5:4b')
                self.assertEqual(request('GET','/api/ai/settings')['settings']['model'],'qwen3.5:4b')
                request('POST','/api/ai/chat',{'session':'unchanged-id','message':'call test'})
                request('POST','/api/mo/settings',{'group':'ai','changes':{'model':'qwen3:8b'}})
                request('POST','/api/ai/chat',{'session':'unchanged-id','message':'call test again'})
                self.assertEqual([body['model'] for body in captured],['qwen3.5:4b','qwen3:8b'])
                for body in captured:
                    self.assertEqual(body['format'],output_schema())
                    self.assertIs(body['think'],False)
                    self.assertIs(body['stream'],False)
        finally:
            host.shutdown();host.server_close();worker.join(2)
            upstream.shutdown();upstream.server_close();upstream_thread.join(2)


if __name__=='__main__':unittest.main(verbosity=2)
