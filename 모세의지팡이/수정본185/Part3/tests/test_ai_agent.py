"""Offline contract tests for read-only Qwen interpretation and trusted Part3 application."""
from __future__ import annotations

import copy
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

PART3 = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(PART3))

from lab import catalog, storage
from lab.ai.agent import Agent, system_prompt
from lab.ai.intent import recipe_from_intent, validate_intent
from lab.ai.provider import ScriptedProvider, OpenAICompatible, from_settings
from lab.ai import tools


def simple(*, steps=None, final=None, direction='LONG', order='SIMULTANEOUS', gap=None, window=None):
    return {'supported': True, 'intent': 'CREATE_STRATEGY',
            'interpretation': {'direction': direction, 'symbols': ['XAUUSD+'],
                'steps': steps or [{'kind': 'TREND', 'tf': '15m', 'direction': direction},
                                   {'kind': 'FVG_TOUCH', 'tf': '15m', 'direction': direction, 'side': 'BULL'}],
                'order_mode': order, 'within_sec': gap, 'final_window_sec': window,
                'final': final or {'kind': 'OZ', 'tf': '1m', 'validation_mode': 'NORMAL',
                                    'trigger_mode': 'BREAKER'}},
            'needs_clarification': False, 'clarification_question': None, 'message_ko': '전략 의도'}


def response(value):
    return {'content': json.dumps(value, ensure_ascii=False), 'tool_calls': []}


class AIContract(unittest.TestCase):
    def test_prompt_and_local_model(self):
        prompt = system_prompt()
        for word in ('MA_STATE', 'MA_CROSS', 'FVG_NEW', 'FVG_TOUCH', 'BLIND', 'SPECIAL3'):
            self.assertIn(word, prompt)
        contract = json.loads(prompt)['moses_contract']
        self.assertIn('MA_CROSS', contract['vocabulary']['intent_kinds'])
        self.assertTrue(contract['presets'])
        self.assertNotIn('read_project_code', prompt)
        self.assertNotIn('업무 매뉴얼', prompt)
        model = from_settings({'provider': 'ollama', 'base_url': 'https://example.invalid', 'model': 'other'})
        self.assertEqual(model.url, 'http://127.0.0.1:11434/api/chat')
        self.assertEqual(model.model, 'other')
        with self.assertRaises(ValueError): from_settings({'provider': 'unsupported'})

    def test_outside_scope_weather_and_game(self):
        rejected = {'supported': False, 'reason': 'MOSES_SCOPE_ONLY',
                    'message_ko': '모세 전략 해석 범위의 요청이 아닙니다.'}
        provider = ScriptedProvider([response(rejected) for _ in range(3)])
        agent = Agent(provider)
        for sentence in ('오늘 날씨 알려줘', '파이썬으로 게임 만들어줘',
                         '파이썬으로 SPECIAL 코드를 작성해줘'):
            result = agent.send(sentence)
            self.assertFalse(result['result']['supported'])
            self.assertEqual(result['result']['reason'], 'MOSES_SCOPE_ONLY')
            self.assertFalse(result['can_apply'])
            with self.assertRaises(ValueError): agent.apply()
        self.assertEqual(len(provider.seen), 3)

    def test_simple_strategy_is_intent_then_trusted_recipe(self):
        agent = Agent(ScriptedProvider([response(simple())]))
        out = agent.send('15분 상승추세와 상승 FVG 접촉이 같이 성립하면 1분 브레이커 올존')
        self.assertTrue(out['can_apply'])
        self.assertIsNone(out['result'].get('source_text'))
        self.assertEqual(out['result']['interpretation']['symbols'], ['XAUUSD+'])
        self.assertNotIn('symbol_source', out['result']['interpretation'])
        recipe = agent.apply()
        self.assertEqual(recipe['base'], 'AI')
        self.assertEqual([c['kind'] for c in recipe['strategy_intent']['steps']], ['TREND', 'FVG_TOUCH'])
        self.assertEqual(recipe['strategy_intent']['final']['trigger_mode'], 'BREAKER')
        with tempfile.TemporaryDirectory() as folder, patch.object(storage, 'ROOT', Path(folder)):
            preview = storage.preview(recipe)
            self.assertIn('register(manager)', preview['code'])
            self.assertFalse((Path(folder) / 'generated').exists())

    def test_sequence_without_touch_clarifies_instead_of_inventing(self):
        value = simple(steps=[{'kind': 'MA_CROSS', 'tf': '1m', 'direction': 'LONG',
                               'ma_left': 'EMA50', 'ma_right': 'EMA200'},
                              {'kind': 'FVG_NEW', 'tf': '5m', 'direction': 'LONG', 'side': 'BULL'}],
                       order='SEQUENTIAL', gap=600, window=3600)
        value['interpretation']['final']['validation_mode'] = 'BLIND'
        agent = Agent(ScriptedProvider([response(value)]))
        out = agent.send('1분 EMA50/200 골크 후 10분 안에 5분 상승 FVG가 생기면 무지성 브레이커 올존 알림')
        self.assertTrue(out['result']['supported'])
        self.assertEqual(out['result']['interpretation']['within_sec'], 600)
        self.assertEqual([s['kind'] for s in out['result']['interpretation']['steps']], ['MA_CROSS', 'FVG_NEW'])
        self.assertEqual(out['result']['interpretation']['final']['validation_mode'], 'BLIND')
        self.assertFalse(out['result']['needs_clarification'])
        self.assertTrue(out['can_apply'])

    def test_explicit_touch_can_use_existing_special3(self):
        value = simple(steps=[{'kind': 'MA_CROSS', 'tf': '1m', 'ma_left': 'EMA50', 'ma_right': 'EMA200'},
                              {'kind': 'FVG_NEW', 'tf': '5m', 'side': 'BULL'},
                              {'kind': 'FVG_TOUCH', 'tf': '5m', 'side': 'BULL'}],
                       direction='BOTH', order='SEQUENTIAL', gap=600, window=3600)
        recipe = recipe_from_intent(value)
        self.assertEqual(recipe['base'], 'AI')
        self.assertEqual(recipe['strategy_intent']['within_sec'], 600)
        self.assertEqual(recipe['strategy_intent']['steps'][2]['tfs'], ['5m'])

    def test_explicit_no_touch_uses_part1_supported_immediate_chain(self):
        value = simple(steps=[{'kind': 'MA_CROSS', 'tf': '1m', 'ma_left': 'EMA50', 'ma_right': 'EMA200'},
                              {'kind': 'FVG_NEW', 'tf': '5m', 'side': 'BULL'}],
                       direction='BOTH', order='SEQUENTIAL', gap=600, window=3600)
        value['interpretation']['final_after'] = 'FVG_NEW'
        recipe = recipe_from_intent(value)
        self.assertNotIn('FVG_TOUCH', [s['kind'] for s in recipe['strategy_intent']['steps']])
        with tempfile.TemporaryDirectory() as folder, patch.object(storage, 'ROOT', Path(folder)):
            source = storage.preview(recipe)['code']
        self.assertIn("'final_after': 'FVG_NEW'", source)

    def test_short_clarification_answer_continues_same_strategy(self):
        first = simple(steps=[{'kind': 'MA_CROSS', 'tf': '1m', 'ma_left': 'EMA50', 'ma_right': 'EMA200'},
                              {'kind': 'FVG_NEW', 'tf': '5m'}],
                       direction='BOTH', order='SEQUENTIAL', gap=600, window=3600)
        first['needs_clarification'] = True
        first['clarification_question'] = '최종 감시 시간은 얼마인가요?'
        followup = copy.deepcopy(first)
        followup['needs_clarification'] = False
        followup['clarification_question'] = None
        followup['interpretation']['final_after'] = 'FVG_NEW'
        agent = Agent(ScriptedProvider([response(first), response(followup)]))
        self.assertTrue(agent.send('1분 EMA 골크 후 10분 안에 새 FVG 생기면 OZ')['result']['needs_clarification'])
        second = agent.send('신규 FVG 직후')
        self.assertTrue(second['can_apply'])
        self.assertIn('[확인 답변]', agent.original_text)

    def test_state_and_event_are_not_interchangeable(self):
        for kind in ('MA_STATE', 'MA_CROSS', 'FVG_STATE', 'FVG_NEW'):
            step = {'kind': kind, 'tf': '1m'}
            if kind == 'MA_STATE': step.update(ma_family='EMA', fast_period=50, slow_period=200)
            if kind == 'MA_CROSS': step.update(ma_left='EMA50', ma_right='EMA200')
            value = recipe_from_intent(simple(steps=[step]))
            self.assertEqual(value['strategy_intent']['steps'][0]['kind'], kind)

    def test_agent_preserves_model_ma_state_without_text_rejudgment(self):
        value = simple(steps=[{'kind': 'MA_STATE', 'tf': '1m', 'direction': 'LONG',
                               'ma_family': 'EMA', 'fast_period': 50, 'slow_period': 200}])
        provider = ScriptedProvider([response(value)])
        agent = Agent(provider)
        out = agent.send('1분 EMA50이 EMA200을 상향 교차하면 1분 올존 전략')
        self.assertTrue(out['can_apply'])
        self.assertEqual(out['result']['interpretation']['steps'][0]['kind'], 'MA_STATE')
        self.assertEqual(len(provider.seen), 1)
        self.assertEqual(agent.apply()['strategy_intent'], validate_intent(value)['interpretation'])

    def test_agent_preserves_model_ma_event_without_text_rejudgment(self):
        value = simple(steps=[{'kind': 'MA_CROSS', 'tf': '1m', 'direction': 'LONG',
                               'ma_left': 'EMA50', 'ma_right': 'EMA200'}])
        provider = ScriptedProvider([response(value)])
        out = Agent(provider).send('1분 EMA50이 EMA200 위에 있으면 1분 올존 전략')
        self.assertTrue(out['can_apply'])
        self.assertEqual(out['result']['interpretation']['steps'][0]['kind'], 'MA_CROSS')
        self.assertEqual(len(provider.seen), 1)

    def test_agent_preserves_model_fvg_state_without_text_rejudgment(self):
        value = simple(steps=[{'kind': 'FVG_STATE', 'tf': '5m', 'side': 'BULL'}])
        provider = ScriptedProvider([response(value)])
        out = Agent(provider).send('5분 새 상승 FVG가 생기면 1분 올존 전략')
        self.assertTrue(out['can_apply'])
        self.assertEqual(out['result']['interpretation']['steps'][0]['kind'], 'FVG_STATE')
        self.assertEqual(len(provider.seen), 1)

    def test_agent_does_not_override_model_profile_or_contract_spelling(self):
        value = simple()
        provider = ScriptedProvider([response(value)])
        out = Agent(provider).send('15분 추세면 1분 올존 전략')
        self.assertTrue(out['can_apply'])
        self.assertEqual(out['result']['interpretation']['final']['trigger_mode'], 'BREAKER')
        self.assertEqual(len(provider.seen), 1)
        value['intent'] = 'create_strategy'
        provider = ScriptedProvider([response(value), response(value)])
        out = Agent(provider).send('15분 추세면 1분 올존 전략')
        self.assertFalse(out['can_apply'])
        self.assertEqual(out['result']['reason'], 'MODEL_OUTPUT_INVALID')

    def test_agent_does_not_override_model_symbols_with_alias_regex(self):
        value = simple()
        value['interpretation']['symbols'] = ['NAS100']
        agent = Agent(ScriptedProvider([response(value)]))
        out = agent.send('골드 15분 상승추세와 상승 FVG면 1분 올존 전략')
        self.assertEqual(out['result']['interpretation']['symbols'], ['NAS100'])
        self.assertNotIn('symbol_source', out['result']['interpretation'])
        self.assertEqual(agent.apply()['strategy_intent'], validate_intent(value)['interpretation'])

    def test_invalid_contract_and_model_code_are_rejected(self):
        value = simple()
        for change in ('tf', 'period', 'kind', 'code'):
            bad = copy.deepcopy(value)
            if change == 'tf': bad['interpretation']['steps'][0]['tf'] = '9h'
            if change == 'period': bad['interpretation']['steps'] = [{'kind': 'MA_STATE', 'tf': '1m',
                'ma_family': 'EMA', 'fast_period': 0, 'slow_period': 200}]
            if change == 'kind': bad['interpretation']['steps'][0]['kind'] = 'NEW_MAGIC_CALC'
            if change == 'code': bad['source_text'] = 'open("x", "w")'
            with self.subTest(change=change), self.assertRaises(ValueError): validate_intent(bad)
        agent = Agent(ScriptedProvider([response({**value, 'source_text': 'print(1)'}),
                                       response({**value, 'source_text': 'print(1)'})]))
        result = agent.send('새 전략 만들어줘')
        self.assertFalse(result['can_apply'])
        self.assertEqual(result['result']['reason'], 'MODEL_OUTPUT_INVALID')

    def test_lookup_is_read_only_and_continues_original_goal(self):
        from common_ai.security import filter_tools
        self.assertEqual({s['function']['name'] for s in filter_tools('ollama', tools.TOOL_SPECS)},
                         {'vocabulary', 'list_specials', 'describe_special'})
        script = [{'content': '', 'tool_calls': [{'id': 'v', 'name': 'vocabulary', 'arguments': {}}]},
                  {'content': 'Here is the vocabulary. What would you like to do?', 'tool_calls': []},
                  response(simple())]
        agent = Agent(ScriptedProvider(script))
        out = agent.send('15분 상승추세 + 상승 FVG면 1분 브레이커 올존 전략')
        self.assertTrue(out['can_apply'])
        self.assertEqual(out['actions'][0]['tool'], 'vocabulary')
        self.assertIn('원래 전략 요청', agent.messages[-2]['content'])

    def test_read_only_tool_paths_and_no_write_or_shell(self):
        workspace = tools.ReadOnlyWorkspace()
        self.assertIn('MA_STATE', workspace.vocabulary()['intent_kinds'])
        self.assertIn('EMA_CROSS', workspace.read_project_code('Part1/program/watch_orchestrator.py')['text']
                      + workspace.search_project_code('EMA_CROSS')['matches'][0]['excerpt'])
        for path in ('../Part1/program/config.txt', 'Part3/reference/Part1/program/SPECIAL/SPECIAL1.py',
                     'Part1/program/config.txt', 'Part1/program/STAFF.exe', 'C:/Windows/system.ini',
                     'Part3/TEST_SPECIAL/Test_SPECIAL008.py', 'Part3/test_special/Test_SPECIAL008.recipe.json'):
            with self.subTest(path=path), self.assertRaises(ValueError): workspace.read_project_code(path)
        agent = Agent(ScriptedProvider([{'content': '', 'tool_calls': [
            {'id': 'x', 'name': 'write_file', 'arguments': {'path': 'Part1/x.py'}},
            {'id': 'y', 'name': 'shell', 'arguments': {'command': 'dir'}}]}, response(simple())]))
        out = agent.send('15분 추세 + 상승 FVG 전략')
        self.assertEqual([a['ok'] for a in out['actions']], [False, False])
        self.assertTrue(out['can_apply'])

    def test_lookup_does_not_modify_any_project_part(self):
        paths = [PART3.parent/'moses_language/language.json',
                 PART3.parent/'Part2/event_backtest/runner.py', PART3/'lab/catalog.py']
        before = [hashlib.sha256(p.read_bytes()).hexdigest() for p in paths]
        workspace = tools.ReadOnlyWorkspace()
        workspace.vocabulary(); workspace.list_specials(); workspace.describe_special(1)
        workspace.search_project_code('FVG_NEW', limit=2)
        workspace.read_project_code('Part1/program/command_interpreter.py', 30, 8)
        self.assertEqual(before, [hashlib.sha256(p.read_bytes()).hexdigest() for p in paths])

    def test_provider_parses_tool_calls_without_real_network(self):
        class Response:
            def __enter__(self): return self
            def __exit__(self, *_): return False
            def read(self):
                return json.dumps({'message': {'content': None, 'tool_calls': [
                    {'id': 'v', 'function': {'name': 'vocabulary', 'arguments': '{}'}}]}}).encode()
        captured = {}
        def fake(request, timeout):
            captured['url'] = request.full_url
            captured['body'] = json.loads(request.data)
            return Response()
        with patch('lab.ai.provider.urllib.request.urlopen', fake):
            result = OpenAICompatible(model='qwen3:8b').chat([{'role': 'user', 'content': 'x'}], tools.TOOL_SPECS)
        self.assertEqual(captured['url'], 'http://127.0.0.1:11434/api/chat')
        self.assertEqual(captured['body']['model'], 'qwen3:8b')
        self.assertEqual(result['tool_calls'][0]['name'], 'vocabulary')

    def test_confirmed_dataset_only_after_apply(self):
        from lab.ai import training
        with tempfile.TemporaryDirectory() as folder, patch.object(catalog, 'ROOT', Path(folder)):
            target = Path(folder)/'projects/ai_confirmed.jsonl'
            self.assertFalse(target.exists())
            training.record_confirmed('15분 추세', simple()['interpretation'],
                                      simple(), {'base': 'CUSTOM'})
            row = json.loads(target.read_text('utf-8').splitlines()[0])
            self.assertEqual(row['status'], 'USER_APPLIED_TO_EDITOR')
            self.assertEqual(row['user_text'], '15분 추세')
            self.assertEqual(row['ai_first_interpretation']['steps'][0]['kind'], 'TREND')
            self.assertEqual(row['user_applied_intent']['intent'], 'CREATE_STRATEGY')

    def test_server_apply_uses_trusted_recipe_without_generation(self):
        from lab import server
        from lab.ai import training
        agent = Agent(ScriptedProvider([response(simple())]))
        saved = []
        with tempfile.TemporaryDirectory() as folder, \
             patch.object(server, 'ai_agent', return_value=agent), \
             patch.object(storage, 'ROOT', Path(folder)), \
             patch.object(training, 'record_confirmed', side_effect=lambda *args: saved.append(args)):
            interpreted = server.ai_post('/api/ai/chat', {'session': 's', 'message': '15분 추세와 상승 FVG 전략'})
            self.assertTrue(interpreted['can_apply'])
            self.assertEqual(saved, [])
            applied = server.ai_post('/api/ai/apply', {'session': 's'})
            self.assertEqual(applied['recipe']['base'], 'AI')
            self.assertEqual(saved, [])  # No training corpus creation in this phase.
            self.assertFalse((Path(folder) / 'generated').exists())
            with self.assertRaises(ValueError): server.ai_post('/api/ai/apply', {'session': 's'})


if __name__ == '__main__':
    unittest.main(verbosity=2)
