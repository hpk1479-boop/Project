"""Conversation revisions over the existing schema. No real model or trading engines."""
from __future__ import annotations
import copy
import http.client
import json
import shutil
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from lab import server, storage
from lab.ai import agent as agent_module
from lab.ai.agent import Agent
from lab.ai.intent import validate_intent
from lab.ai.provider import ScriptedProvider


def original():
    return {'supported': True, 'intent': 'CREATE_STRATEGY', 'interpretation': {
        'direction': 'LONG', 'symbols': ['XAUUSD+'], 'steps': [
            {'kind': 'TREND', 'tfs': ['15m'], 'direction': 'LONG', 'bar_state': 'CLOSED'},
            {'kind': 'WONBI_TOUCH', 'tfs': ['3m'], 'side': 'LOWER', 'direction': 'LONG'}],
        'order_mode': 'SIMULTANEOUS', 'global_combine': 'ALL', 'within_sec': None,
        'final_window_sec': 600, 'persistent': True,
        'final': {'kind': 'OZ', 'tfs': ['1m'], 'validation_mode': 'NORMAL', 'trigger_mode': 'BREAKER'}},
        'needs_clarification': False, 'clarification_question': None, 'message_ko': '조건 충족 시 브레이커 감시'}


def changed(tf='15m'):
    value = original()
    value['interpretation']['steps'][1]['tfs'] = [tf]
    return value


def reply(value):
    return {'content': json.dumps(value, ensure_ascii=False), 'tool_calls': []}


class RecordingProvider(ScriptedProvider):
    def chat(self, messages, tools):
        # Snapshot every actual user context passed to the provider.
        return super().chat(copy.deepcopy(messages), tools)


def context(provider):
    return json.loads(provider.seen[-1]['content'])['context']['current_strategy']


class RevisionTests(unittest.TestCase):
    def test_only_requested_timeframe_changes_and_whole_intent_is_revalidated(self):
        provider = RecordingProvider([reply(original()), reply(changed())])
        agent = Agent(provider)
        first = agent.send('15분 상승추세일 때 3분 하단 원비 터치하면 1분 일반 브레이커')
        with patch.object(agent_module, 'validate_intent', wraps=validate_intent) as validate:
            result = agent.send('아니 원비는 15분이야')
        self.assertTrue(result['is_revision'])
        self.assertTrue(result['can_apply'])
        self.assertEqual(context(provider), first['result'])
        self.assertEqual(json.loads(provider.seen[-1]['content'])['message'], '아니 원비는 15분이야')
        self.assertEqual(validate.call_args.args[0], changed())
        expected = copy.deepcopy(first['result'])
        expected['interpretation']['steps'][1]['tfs'] = ['15m']
        self.assertEqual(result['result'], expected)
        self.assertEqual(agent.last_intent, expected)
        self.assertEqual(agent.apply(result['revision'])['strategy_intent'], expected['interpretation'])

    def test_each_revision_uses_latest_draft_and_keeps_other_fields(self):
        third = changed()
        third['interpretation']['final_window_sec'] = 120
        provider = RecordingProvider([reply(original()), reply(changed()), reply(third)])
        agent = Agent(provider)
        agent.send('최초 전략')
        second = agent.send('아니 원비는 15분이야')
        third_result = agent.send('감시 시간만 2분으로')
        self.assertEqual(context(provider), second['result'])
        expected = copy.deepcopy(second['result'])
        expected['interpretation']['final_window_sec'] = 120
        self.assertEqual(third_result['result'], expected)
        self.assertEqual(third_result['revision'], 3)

    def test_invalid_partial_or_unknown_values_cannot_replace_or_apply_old_draft(self):
        for bad in ({'supported': True, 'intent': 'CREATE_STRATEGY', 'interpretation': {'steps': []}},
                    changed('9h')):
            with self.subTest(bad=bad):
                provider = RecordingProvider([reply(original()), reply(bad), reply(bad), reply(changed())])
                agent = Agent(provider)
                first = agent.send('최초 전략')['result']
                failed = agent.send('잘못된 수정')
                self.assertFalse(failed['can_apply'])
                self.assertEqual(agent.last_intent, first)
                with self.assertRaises(ValueError): agent.apply()
                self.assertTrue(agent.send('원비는 15분')['can_apply'])
                self.assertEqual(context(provider), first)

    def test_connection_failure_drops_apply_candidate_but_preserves_context(self):
        provider = RecordingProvider([reply(original()), reply(changed())])
        agent = Agent(provider)
        first = agent.send('최초 전략')['result']
        with patch.object(provider, 'chat', side_effect=ValueError('연결 실패')):
            with self.assertRaises(ValueError): agent.send('원비는 15분')
        self.assertEqual(agent.last_intent, first)
        with self.assertRaises(ValueError): agent.apply()
        self.assertTrue(agent.send('원비는 15분')['can_apply'])
        self.assertEqual(context(provider), first)

    def test_unsupported_revision_keeps_draft_without_apply_permission(self):
        bad = {'supported': False, 'reason': 'MOSES_SCOPE_ONLY', 'message_ko': '범위 밖'}
        agent = Agent(RecordingProvider([reply(original()), reply(bad), reply(changed())]))
        first = agent.send('최초 전략')['result']
        self.assertFalse(agent.send('날씨')['can_apply'])
        self.assertEqual(agent.last_intent, first)
        with self.assertRaises(ValueError): agent.apply()
        agent.send('원비는 15분')
        self.assertEqual(context(agent.provider), first)

    def test_clarification_requires_answer_and_then_reconfirmation(self):
        question = original()
        question.update(needs_clarification=True, clarification_question='원비 시간봉은 몇 분인가요?')
        agent = Agent(RecordingProvider([reply(original()), reply(question), reply(changed())]))
        agent.send('최초 전략')
        pending = agent.send('원비 시간봉을 바꿔')
        self.assertFalse(pending['can_apply'])
        with self.assertRaises(ValueError): agent.apply()
        answered = agent.send('15분')
        self.assertTrue(answered['can_apply'])
        self.assertEqual(context(agent.provider), pending['result'])
        self.assertIn('[확인 답변]', agent.original_text)

    def test_cancel_and_application_preserve_revision_context(self):
        agent = Agent(RecordingProvider([reply(original()), reply(changed()), reply(original())]))
        first = agent.send('최초 전략')
        agent.cancel()
        with self.assertRaises(ValueError): agent.apply()
        second = agent.send('원비는 15분')
        self.assertEqual(context(agent.provider), first['result'])
        with tempfile.TemporaryDirectory() as folder, patch.object(storage, 'ROOT', Path(folder)), patch.dict(server.AI_SESSIONS, {'test': agent}, clear=True):
            server.ai_post('/api/ai/apply', {'session': 'test', 'revision': second['revision']})
            self.assertEqual(list(Path(folder).iterdir()), [])
            self.assertIsNone(agent.candidate)
            agent.send('원비 다시 3분')
            self.assertEqual(context(agent.provider), second['result'])

    def test_stale_revision_and_returned_object_mutation_do_not_change_candidate(self):
        agent = Agent(RecordingProvider([reply(original()), reply(changed())]))
        first = agent.send('최초 전략')
        second = agent.send('원비는 15분')
        for stale in (first['revision'], True, '2'):
            with self.subTest(stale=stale), self.assertRaisesRegex(ValueError, '최신 해석'):
                agent.apply(stale)
        second['result']['interpretation']['steps'].clear()
        self.assertEqual(len(agent.apply(2)['strategy_intent']['steps']), 2)
        self.assertEqual(len(agent.last_intent['interpretation']['steps']), 2)

    def test_sessions_are_independent_and_reset_starts_new_strategy(self):
        with patch.dict(server.AI_SESSIONS, {}, clear=True), patch('lab.ai.shared_provider.shared_from_settings',
                side_effect=lambda *_, **_kwargs: RecordingProvider([reply(original()), reply(changed())])):
            a = server.ai_post('/api/ai/chat', {'session': 'a', 'message': '전략 A'})
            server.ai_post('/api/ai/chat', {'session': 'b', 'message': '전략 B'})
            server.ai_post('/api/ai/chat', {'session': 'a', 'message': '원비 15분'})
            self.assertEqual(server.ai_agent('b').last_intent, a['result'])
            old = server.ai_agent('a')
            server.ai_post('/api/ai/reset', {'session': 'a'})
            new = server.ai_post('/api/ai/chat', {'session': 'a', 'message': '다른 전략'})
            self.assertIsNot(old, server.ai_agent('a'))
            self.assertFalse(new['is_revision'])
            request = json.loads(server.ai_agent('a').provider.seen[0]['content'])
            self.assertEqual(request['message'], '다른 전략')
            self.assertIsNone(request['context']['current_strategy'])

    def test_concurrent_confirmation_of_old_card_is_rejected_after_revision(self):
        agent = Agent(RecordingProvider([reply(original()), reply(changed())]))
        agent.send('최초 전략')
        entered, release = threading.Event(), threading.Event()
        chat = agent.provider.chat
        def blocked(*args):
            entered.set()
            if not release.wait(3): raise AssertionError('test timeout')
            return chat(*args)
        errors = []
        with patch.object(agent.provider, 'chat', side_effect=blocked):
            writer = threading.Thread(target=lambda: agent.send('원비 15분'))
            writer.start()
            self.assertTrue(entered.wait(2))
            def confirm():
                try: agent.apply(1)
                except ValueError as exc: errors.append(str(exc))
            reader = threading.Thread(target=confirm)
            reader.start()
            release.set()
            writer.join(3); reader.join(3)
        self.assertFalse(writer.is_alive() or reader.is_alive())
        self.assertEqual(len(errors), 1)
        self.assertIn('최신 해석', errors[0])

    def test_real_http_interpret_modify_confirm_apply_then_explicit_generate(self):
        agent = Agent(RecordingProvider([reply(original()), reply(changed())]))
        app = server.LabServer(0)
        thread = threading.Thread(target=app.serve_forever, daemon=True)
        thread.start()
        def post(route, data, expected=200):
            conn = http.client.HTTPConnection('127.0.0.1', app.server_port, timeout=5)
            try:
                conn.request('POST', route, json.dumps(data), {'X-Lab-Token': app.token, 'Content-Type': 'application/json'})
                response = conn.getresponse(); body = json.loads(response.read())
                self.assertEqual(response.status, expected, body)
                return body
            finally: conn.close()
        try:
            with tempfile.TemporaryDirectory() as folder, patch.object(storage, 'ROOT', Path(folder)), patch.dict(server.AI_SESSIONS, {'flow': agent}, clear=True):
                first = post('/api/ai/chat', {'session': 'flow', 'message': '15분 추세와 3분 원비'})
                second = post('/api/ai/chat', {'session': 'flow', 'message': '아니 원비는 15분이야'})
                self.assertEqual(list(Path(folder).iterdir()), [])
                post('/api/ai/apply', {'session': 'flow', 'revision': first['revision']}, 400)
                applied = post('/api/ai/apply', {'session': 'flow', 'revision': second['revision']})
                self.assertEqual(applied['recipe']['strategy_intent'], second['result']['interpretation'])
                self.assertEqual(list(Path(folder).iterdir()), [])
                post('/api/ai/apply', {'session': 'flow', 'revision': second['revision']}, 400)
                generated = post('/api/generate', {'recipe': applied['recipe']})
                self.assertTrue((Path(folder)/'TEST_SPECIAL'/generated['filename']).is_file())
                self.assertEqual(len(list((Path(folder)/'TEST_SPECIAL').glob('*.py'))), 1)
        finally:
            app.shutdown(); app.server_close(); thread.join(3)


if __name__ == '__main__':
    unittest.main()
