"""No implicit model; unconfigured settings remain readable and recoverable."""
import http.client
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from lab import server, unified_settings
from lab.ai import model_runtime, provider
from lab.ai.schema import output_schema
from common_ai.client import Client


class PrivateLease:
    """Keeps this fixture off the machine-wide model lock of a running MOSES window."""
    def acquire(self): pass
    def release(self): pass


class NoDefaultTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        project=Path(self.temp.name)
        self.path = project/'settings/ai_settings.json'
        self.path.parent.mkdir()
        # Own runtime per test: a model owned after an earlier test's chat would
        # make the next settings save try to unload it from the real Ollama.
        runtime = model_runtime.ModelRuntime()
        runtime.lease = PrivateLease()
        self.connect = patch.object(Client, '_connect', side_effect=AssertionError('No AI connection in this fixture'))
        for context in (patch.object(server, 'ROOT', project/'Part3'),
                        patch.object(server, 'AI_SETTINGS', self.path),
                        patch.object(server, '_AI_CLIENT', None),
                        patch.object(server, 'AI_SESSIONS', {}),
                        patch.object(server, 'RESEARCH_SESSIONS', {}),
                        patch.object(server, 'BACKTEST_COMMAND_SESSIONS', {}),
                        patch.object(Client, '_lookup', return_value=None),
                        patch.object(Client, '_start', side_effect=AssertionError('No AI service startup')),
                        self.connect,
                        patch.object(model_runtime, 'RUNTIME', runtime)):
            context.start(); self.addCleanup(context.stop)
        context = patch.object(provider.urllib.request, 'urlopen', side_effect=AssertionError('No real Ollama in this fixture'))
        blocked = context.start()
        self.addCleanup(context.stop)
        self.addCleanup(blocked.assert_not_called)
        self.addCleanup(server.close_ai_client)

    def test_no_default_factory_or_constructor_never_open_model_connection(self):
        self.assertIsNone(provider.DEFAULT_MODEL)
        with patch.object(provider.urllib.request, 'urlopen') as request:
            for settings in (None, {}, {'model': None}, {'model': ''}, {'model': ' \t '}, {'model': False}):
                with self.subTest(settings=settings), self.assertRaisesRegex(RuntimeError, '^AI 모델이 설정되지 않았습니다\\.$'):
                    provider.from_settings(settings)
            for model in (None, '', ' \t ', False):
                with self.subTest(model=model), self.assertRaisesRegex(RuntimeError, '^AI 모델이 설정되지 않았습니다\\.$'):
                    provider.OpenAICompatible(model=model)
            request.assert_not_called()

    def test_settings_missing_empty_or_null_are_readable_but_agent_is_blocked(self):
        for data in (None, {}, {'model': None}, {'model': ''}, {'model': '   '}):
            with self.subTest(data=data):
                if data is None:
                    if self.path.exists(): self.path.unlink()
                else: self.path.write_text(json.dumps(data), encoding='utf-8')
                before = self.path.read_bytes() if self.path.exists() else None
                self.assertIsNone(server.ai_settings()['model'])
                with patch.object(provider.urllib.request, 'urlopen') as request, \
                        self.assertRaisesRegex(RuntimeError, 'AI 모델이 설정되지 않았습니다.'):
                    server.ai_post('/api/ai/chat', {'session': 'unconfigured', 'message': '전략 해석'})
                request.assert_not_called()
                # Incomplete sessions can serve the manual editor, but cannot
                # create/apply a strategy or contact the common AI service.
                agent=server.AI_SESSIONS['unconfigured']
                self.assertIsNone(agent.last_intent)
                self.assertIsNone(agent.candidate)
                Client._connect.assert_not_called()
                Client._start.assert_not_called()
                self.assertEqual(self.path.read_bytes() if self.path.exists() else None, before)

    def test_initial_save_requires_model_then_preserves_selected_name_on_partial_save(self):
        for changes in ({'timeout': 120}, {'model': ''}, {'model': None}):
            with self.subTest(changes=changes), self.assertRaises(ValueError): unified_settings.save_ai(changes)
            self.assertFalse(self.path.exists())
        unified_settings.save_ai({'model': 'qwen3.5:4b'})
        self.assertEqual(server.ai_agent('configured').provider.model, 'qwen3.5:4b')
        unified_settings.save_ai({'timeout': 120})
        self.assertEqual(server.ai_settings(), {'provider':'ollama','model':'qwen3.5:4b','timeout':120,'watch_enabled':True})

    def test_invalid_nonempty_names_still_fail_validation(self):
        for value in ('bad name', 7, 'bad\x00name'):
            with self.subTest(value=value), self.assertRaises(ValueError): provider.from_settings({'model':value})
        self.assertEqual(provider.from_settings({'model':' qwen3.5:4b '}).model, 'qwen3.5:4b')

    def test_real_http_unconfigured_read_and_block_then_save_and_selected_call(self):
        app = server.LabServer(0)
        worker = threading.Thread(target=app.serve_forever, daemon=True); worker.start()
        def request(method, path, data=None, expected=200):
            conn = http.client.HTTPConnection('127.0.0.1', app.server_port, timeout=5)
            try:
                conn.request(method, path, None if data is None else json.dumps(data),
                    {'X-Lab-Token':app.token,'Content-Type':'application/json'})
                response = conn.getresponse(); body = json.loads(response.read())
                self.assertEqual(response.status, expected, body)
                return body
            finally: conn.close()
        calls = []
        class Reply:
            def __enter__(self): return self
            def __exit__(self, *_): pass
            def read(self):
                return json.dumps({'message':{'content':json.dumps({'supported':False,
                    'reason':'MOSES_SCOPE_ONLY','message_ko':'시험 응답'})}}).encode('utf-8')
        def model_request(req, timeout):
            if req.full_url.endswith(('/api/ps', '/api/generate')):
                is_inventory = req.full_url.endswith('/api/ps')
                if not is_inventory:
                    body = json.loads(req.data)
                    self.assertEqual(body['keep_alive'], 0)
                    self.assertIs(body['stream'], False)
                class Inventory:
                    def __enter__(self): return self
                    def __exit__(self, *_): pass
                    def read(self, *_): return b'{"models":[]}' if is_inventory else b'{"done":true}'
                return Inventory()
            calls.append(json.loads(req.data)); return Reply()
        try:
            with patch.object(provider.urllib.request, 'urlopen', side_effect=model_request), \
                    patch('lab.ai.shared_provider.shared_from_settings',
                          side_effect=lambda settings, **_kwargs: provider.from_settings(settings)):
                self.assertIsNone(request('GET','/api/ai/settings')['settings']['model'])
                self.assertIsNone(request('GET','/api/mo/settings')['ai']['model'])
                error = request('POST','/api/ai/chat', {'session':'same','message':'전략 해석'},400)
                self.assertEqual(error['error'],'AI 모델이 설정되지 않았습니다.')
                self.assertEqual(calls,[])
                self.assertFalse(self.path.exists())
                request('POST','/api/mo/settings',{'group':'ai','changes':{'model':'qwen3.5:4b'}})
                self.assertEqual(request('GET','/api/ai/settings')['settings']['model'],'qwen3.5:4b')
                request('POST','/api/ai/chat',{'session':'same','message':'전략 해석'})
                self.assertEqual([body['model'] for body in calls],['qwen3.5:4b'])
                self.assertEqual(calls[0]['format'],output_schema())
                self.assertIs(calls[0]['think'],False)
                request('POST','/api/ai/settings',{'model':'organization/custom:q4_K_M'})
                request('POST','/api/ai/chat',{'session':'same','message':'수정 요청'})
                self.assertEqual([body['model'] for body in calls],['qwen3.5:4b','organization/custom:q4_K_M'])
        finally:
            app.shutdown(); app.server_close(); worker.join(3)


if __name__ == '__main__': unittest.main()
