"""Edited AI drafts name server-checked frames, including autosave and out-of-order replies."""
import copy
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import threading

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'Part3'), str(ROOT / 'Part2'), str(ROOT / 'Part1/program')]
from lab.ai import research, research_editor, schema
from event_backtest.virtual_contract import immediate_virtual_entry
from event_backtest.virtual_defaults import strategy_frames, strategy_profile
from test_virtual_ai113 import plan, value


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def deny(*args, **kwargs):
        raise AssertionError('No live transport is allowed in summary tests')
    monkeypatch.setattr(socket, 'create_connection', deny)
    for name in ('connect', 'connect_ex', 'sendto'):
        monkeypatch.setattr(socket.socket, name, deny)


def intent(tf='1m'):
    return schema.validate_intent({'supported': True, 'intent': 'CREATE_STRATEGY',
        'interpretation': {'symbols': ['XAUUSD+'], 'direction': 'LONG',
            'steps': [{'kind': 'BAR_CLOSE', 'tfs': [tf]}], 'order_mode': 'SIMULTANEOUS',
            'final': {'kind': 'NOTIFY'}}, 'needs_clarification': False,
        'clarification_question': None, 'message_ko': '지정 시간봉 봉 마감 알림'})


def draft_plan():
    command = value(immediate_virtual_entry(), 'GENERATED')
    command['request']['filename'] = None
    return plan(None) | {'steps': [{'draft': True, 'command': command}]}


class MemoryAgent:
    """Only execution/approval side effects are stubbed; schema and plan checks remain real."""
    def __init__(self):
        self.last_intent = intent()
        self.lock = threading.RLock()

    def cancel(self):
        pass

    def checkpoint(self):
        return copy.deepcopy(self.last_intent)

    def restore_checkpoint(self, state):
        self.last_intent = state

    def accept(self, strategy, message):
        self.last_intent = copy.deepcopy(strategy)
        return {'result': copy.deepcopy(strategy), 'can_apply': True, 'revision': 'memory-approval'}


def session():
    agent = MemoryAgent()
    snapshot = {'today': '2026-10-07', 'generated': [], 'jobs': [],
        'options': {'symbols': ['XAUUSD+'], 'specials': [], 'mode': 'BAR',
                    'result_mode': 'VIRTUAL_ENTRY'}, 'execution_defaults': {}, 'execution_version': 'stable'}
    result = research.Session(None, lambda: agent, lambda recipe: None,
                              context_loader=lambda: copy.deepcopy(snapshot))
    result._backtest().previewer = lambda recipe: {'filename': 'Test_SPECIAL148.py'}
    return result


def test_autosave_returns_frames_of_the_latest_checked_draft():
    current = session()
    for tf in ('1m', '2m', '3m'):
        response = current.edit(current.revision, intent(tf), 'BACKTEST', draft_plan())
        assert response['ok'], response
        expected = {'timeframes': [tf], 'env_timeframes': [tf]}
        assert response['virtual_draft_frames'] == expected
        assert current.editor()['options']['virtual_draft_frames'] == expected
        assert strategy_frames(current.backtest.pending['recipe']['strategy_intent']) == expected
        assert response['plan']['steps'][0]['command']['request']['virtual_entry']['stop']['tf'] == 'SIGNAL'


def test_failed_autosave_has_no_previous_numeric_frame_labels():
    current = session()
    good = current.edit(current.revision, intent('2m'), 'BACKTEST', draft_plan())
    assert good['ok'], good
    invalid = intent('2m')
    invalid['interpretation']['steps'][0]['tfs'] = ['']
    response = current.edit(current.revision, invalid, 'BACKTEST', draft_plan())
    assert response['ok'] is False and response['errors']
    assert response['virtual_draft_frames'] is None
    assert current.pending is None


def test_ai_summary_refresh_in_the_real_chat_page():
    runtime = Path.home() / '.cache/codex-runtimes/codex-primary-runtime/dependencies/node'
    node = os.environ.get('MOSES_TEST_NODE') or shutil.which('node') or str(runtime / 'bin/node.exe')
    playwright = os.environ.get('MOSES_TEST_PLAYWRIGHT') or str(runtime / 'node_modules/playwright')
    if not Path(node).is_file() or not Path(playwright).is_dir():
        pytest.skip('Headless browser test needs Node.js and Playwright')
    current = session()
    replies = {}
    for tf in ('1m', '2m', '3m'):
        replies[tf] = current.edit(current.revision, intent(tf), 'BACKTEST', draft_plan())
        assert replies[tf]['ok'], replies[tf]
    initial = replies['1m'] | {'kind': 'BACKTEST'}
    profiles = {name: strategy_profile(name) for name in ('SPECIAL1', 'SPECIAL9')}
    contract = research_editor.contract({'options': {'symbols': ['XAUUSD+'], 'virtual_profiles': profiles}}, intent())
    contract.update(operation='BACKTEST', strategy=intent(), plan=draft_plan())
    multi = {'interpretation': profiles['SPECIAL1']['strategy']}
    fixtures = {'initial': initial, 'contract': contract, 'replies': replies,
        'multi_frames': research_editor.draft_frames(multi), 'multi_policy': profiles['SPECIAL1']['default']}
    evidence = Path(os.environ.get('MOSES_EVIDENCE_ROOT') or ROOT / '검증결과') / 'revision148/ai_refresh'
    scratch = evidence / 'scratch'
    scratch.mkdir(parents=True, exist_ok=True)
    child_env = dict(os.environ, TEMP=str(scratch.resolve()), TMP=str(scratch.resolve()))
    completed = subprocess.run([node, str(Path(__file__).with_suffix('.cjs')), playwright, str(evidence)],
        input=json.dumps(fixtures, ensure_ascii=False), capture_output=True, text=True, encoding='utf-8',
        timeout=60, env=child_env)
    assert completed.returncode == 0, completed.stdout + completed.stderr
