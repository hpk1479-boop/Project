"""Saved model -> both settings APIs -> cached agent -> native Ollama request."""
import http.client
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
import urllib.error
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


class ControlReply:
    """Ollama's read-only model inventory; chat assertions remain unchanged."""
    def __init__(self, value=None): self.value = {'models': []} if value is None else value
    def __enter__(self): return self
    def __exit__(self, *_): pass
    def read(self, *_): return json.dumps(self.value).encode()


def model_control(request):
    if request.full_url.endswith('/api/generate'):
        body = json.loads(request.data)
        assert body['keep_alive'] == 0 and body['stream'] is False
        return ControlReply({'done': True})
    return ControlReply() if request.full_url.endswith('/api/ps') else None


class ModelSettingsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name) / 'project'
        self.path = self.project / 'settings/ai_settings.json'
        self.path.parent.mkdir(parents=True)
        # Own runtime per test: a model owned after an earlier test's chat would
        # make the next settings save try to unload it from the real Ollama.
        runtime = model_runtime.ModelRuntime()
        runtime.lease = PrivateLease()
        for context in (patch.object(server, 'ROOT', self.project / 'Part3'),
                        patch.object(server, 'AI_SETTINGS', self.path),
                        patch.object(server, '_AI_CLIENT', None),
                        patch.object(server, 'AI_SESSIONS', {}),
                        patch.object(server, 'RESEARCH_SESSIONS', {}),
                        patch.object(server, 'BACKTEST_COMMAND_SESSIONS', {}),
                        patch.object(model_runtime, 'RUNTIME', runtime)):
            context.start(); self.addCleanup(context.stop)
        for method in ('_start', '_request'):
            context = patch.object(Client, method, side_effect=AssertionError('No common AI service in this fixture'))
            blocked = context.start()
            self.addCleanup(context.stop)
            self.addCleanup(blocked.assert_not_called)
        context = patch.object(provider.urllib.request, 'urlopen', side_effect=AssertionError('No real Ollama in this fixture'))
        blocked = context.start()
        self.addCleanup(context.stop)
        self.addCleanup(blocked.assert_not_called)
        self.addCleanup(server.close_ai_client)

    def test_missing_settings_have_no_default_and_saved_model_is_read(self):
        self.assertIsNone(server.ai_settings()['model'])
        with self.assertRaisesRegex(RuntimeError, 'AI 모델이 설정되지 않았습니다.'):
            provider.from_settings()
        self.path.write_text(json.dumps({'provider': 'ollama', 'model': 'qwen3.5:4b', 'timeout': 120}))
        self.assertEqual(server.ai_settings()['model'], 'qwen3.5:4b')
        self.assertEqual(unified_settings.read()['ai']['model'], 'qwen3.5:4b')
        model = provider.from_settings(server.ai_settings())
        self.assertEqual((model.model, model.timeout), ('qwen3.5:4b', 120))

    def test_provider_respects_model_but_keeps_local_endpoint(self):
        model = provider.from_settings({'provider': 'ollama', 'model': 'qwen3.5:4b',
                                        'base_url': 'https://example.invalid', 'timeout': 45})
        self.assertEqual(model.model, 'qwen3.5:4b')
        self.assertEqual(model.url, provider.BASE_URL + '/api/chat')
        self.assertEqual(model.timeout, 45)
        with self.assertRaises(ValueError):
            provider.from_settings({'provider': 'unsupported', 'model': 'other'})

    def test_native_request_uses_model_and_unchanged_structured_output(self):
        captured = {}
        class Response:
            def __enter__(self): return self
            def __exit__(self, *_): pass
            def read(self): return b'{"message":{"content":"ok"}}'
        def send(request, timeout):
            control = model_control(request)
            if control is not None: return control
            captured.update(body=json.loads(request.data), url=request.full_url, timeout=timeout)
            return Response()
        with patch.object(provider.urllib.request, 'urlopen', send):
            provider.from_settings({'model': 'qwen3.5:4b'}).chat([{'role': 'user', 'content': 'test'}], [])
        self.assertEqual(captured['body']['model'], 'qwen3.5:4b')
        self.assertEqual(captured['body']['format'], output_schema())
        self.assertEqual(captured['body']['options'], {'temperature': 0.1, 'num_ctx': 16384})
        self.assertIs(captured['body']['think'], False)
        self.assertIs(captured['body']['stream'], False)

    def test_partial_saves_preserve_model_or_timeout_and_clear_sessions(self):
        unified_settings.save_ai({'model': 'qwen3.5:4b', 'timeout': 120})
        old = server.ai_agent('existing')
        self.assertEqual(old.provider.model, 'qwen3.5:4b')
        unified_settings.save_ai({'timeout': 180})
        self.assertEqual(server.ai_settings(), {'provider': 'ollama', 'model': 'qwen3.5:4b', 'timeout': 180, 'watch_enabled': True})
        replacement = server.ai_agent('existing')
        self.assertIsNot(replacement, old)
        unified_settings.save_ai({'model': ' qwen3:8b '})
        self.assertEqual(server.ai_settings(), {'provider': 'ollama', 'model': 'qwen3:8b', 'timeout': 180, 'watch_enabled': True})
        self.assertEqual(server.ai_agent('existing').provider.model, 'qwen3:8b')

    def test_ai_settings_api_uses_same_save_and_preserves_timeout(self):
        unified_settings.save_ai({'model': 'qwen3:8b', 'timeout': 150})
        response = server.ai_post('/api/ai/settings', {'provider': 'ollama', 'model': 'qwen3.5:4b'})
        self.assertEqual(response['settings'], {'provider': 'ollama', 'model': 'qwen3.5:4b', 'timeout': 150,
                                                'watch_enabled': True, 'gemini_api_key_configured': False})
        self.assertEqual(unified_settings.read()['ai']['model'], 'qwen3.5:4b')

    def test_invalid_changes_do_not_write_or_reset_current_session(self):
        unified_settings.save_ai({'model': 'qwen3.5:4b'})
        old = server.ai_agent('keep')
        before = self.path.read_bytes()
        for changes in ({'model': ''}, {'model': None}, {'model': 4}, {'model': 'qwen bad'},
                        {'model': 'bad\x00name'}, {'timeout': 9}, {'timeout': True}, {'base_url': 'remote'}, {}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                unified_settings.save_ai(changes)
            self.assertEqual(self.path.read_bytes(), before)
            self.assertIs(server.ai_agent('keep'), old)
        with self.assertRaises(ValueError):
            server.ai_post('/api/ai/settings', {'provider': 'unsupported', 'model': 'other'})
        self.assertEqual(self.path.read_bytes(), before)

    def test_model_missing_error_names_selected_model(self):
        model = provider.from_settings({'model': 'qwen3.5:4b'})
        error = urllib.error.HTTPError(model.url, 404, 'not found', {}, None)
        def missing(request, timeout):
            control = model_control(request)
            if control is not None: return control
            raise error
        with patch.object(provider.urllib.request, 'urlopen', side_effect=missing), \
             self.assertRaisesRegex(ValueError, 'ollama pull qwen3[.]5:4b'):
            model.chat([], [])

    def test_unlisted_ollama_name_is_accepted(self):
        name = 'organization/custom-model:q4_K_M'
        unified_settings.save_ai({'model': name})
        self.assertEqual(server.ai_agent('custom').provider.model, name)

    def test_moved_settings_file_preserves_saved_model(self):
        unified_settings.save_ai({'model': 'qwen3.5:4b'})
        previous = server.ai_agent('before-move').provider.client
        moved = self.project.parent / 'relocated'
        self.project.rename(moved)
        with patch.object(server, 'ROOT', moved / 'Part3'), \
                patch.object(server, 'AI_SETTINGS', moved / 'settings/ai_settings.json'):
            self.assertEqual(provider.from_settings(server.ai_settings()).model, 'qwen3.5:4b')
            unified_settings.save_ai({'timeout': 100})
            self.assertEqual(server.ai_settings()['model'], 'qwen3.5:4b')
            selected = server.ai_agent('after-move').provider
            self.assertEqual(selected.client.root, moved.resolve())
            self.assertTrue(previous._closed)
            self.assertEqual((selected.model, selected.timeout), ('qwen3.5:4b', 100))

    def test_authenticated_http_save_read_chat_and_model_switch(self):
        host = server.LabServer(0)
        worker = threading.Thread(target=host.serve_forever, daemon=True)
        worker.start()
        def request(method, route, data=None):
            client = http.client.HTTPConnection('127.0.0.1', host.server_port, timeout=5)
            headers = {'X-Lab-Token': host.token, 'Content-Type': 'application/json'}
            client.request(method, route, body=None if data is None else json.dumps(data), headers=headers)
            response = client.getresponse()
            code, body = response.status, json.loads(response.read())
            client.close()
            self.assertEqual(code, 200, body)
            return body
        calls = []
        class Reply:
            def __enter__(self): return self
            def __exit__(self, *_): pass
            def read(self):
                return json.dumps({'message': {'content': json.dumps({'supported': False,
                    'reason': 'MOSES_SCOPE_ONLY', 'message_ko': '범위 밖'})}}).encode()
        def local_model(request, timeout):
            control = model_control(request)
            if control is not None: return control
            calls.append(json.loads(request.data)['model'])
            return Reply()
        try:
            with patch.object(provider.urllib.request, 'urlopen', local_model), \
                    patch('lab.ai.shared_provider.shared_from_settings',
                          side_effect=lambda settings, **_kwargs: provider.from_settings(settings)):
                request('POST', '/api/mo/settings', {'group': 'ai', 'changes': {'model': 'qwen3.5:4b'}})
                self.assertEqual(request('GET', '/api/ai/settings')['settings']['model'], 'qwen3.5:4b')
                self.assertEqual(request('GET', '/api/mo/settings')['ai']['model'], 'qwen3.5:4b')
                request('POST', '/api/ai/chat', {'session': 'same', 'message': 'test'})
                request('POST', '/api/ai/settings', {'model': 'qwen3:8b'})
                request('POST', '/api/ai/chat', {'session': 'same', 'message': 'test again'})
                self.assertEqual(calls, ['qwen3.5:4b', 'qwen3:8b'])
        finally:
            host.shutdown(); host.server_close(); worker.join(2)


if __name__ == '__main__':
    unittest.main(verbosity=2)
