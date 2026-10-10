"""184: Part3's AI (Gemini) runs several strategies × OZ triggers as one lanes request, as `cli.py lanes` does.

A START with a trigger list, several strategies with a virtual entry, or several generated files becomes one
lanes plan: every strategy × trigger is one run, each SPECIAL with its screen settings (trigger, trading time),
so a lane is the screen's run of that strategy alone. REPORT shows the result tables of a finished request.
The external model gets the same rules. No model, network or engine is used here.
"""
import copy
import json
from pathlib import Path
import socket
import sys

import pytest
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'Part3'), str(ROOT / 'Part2'), str(ROOT / 'Part1/program')]
from lab.ai import backtest_commands as commands  # noqa: E402
from lab.ai import research_backtest, research_interpreter  # noqa: E402

TRIGGERS = ['올존', '무지성 올존', '브레이커 올존', '무지성 브레이커 올존']
LONDON = {'MAIN_LONDON': {'enabled': True}}


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def deny(*args, **kwargs):
        raise AssertionError('No live transport is allowed in AI contract tests')
    monkeypatch.setattr(socket, 'create_connection', deny)
    for key in ('connect', 'connect_ex', 'sendto'):
        monkeypatch.setattr(socket.socket, key, deny)


def snapshot(jobs=()):
    return {'today': '2026-10-05', 'generated': [{'filename': 'Test_SPECIAL001.py'}], 'jobs': list(jobs),
            'options': {'symbols': ['XAUUSD+'], 'specials': ['SPECIAL1', 'SPECIAL2', 'SPECIAL7', 'SPECIAL8'], 'mode': 'BAR',
                        'result_mode': 'VIRTUAL_ENTRY', 'trigger_choices': TRIGGERS,
                        'special_settings': {'SPECIAL2': {'enabled': True, 'trigger': '무지성 올존', 'time_filters': LONDON}}},
            'execution_defaults': {}, 'execution_version': 'stable'}


def start(**changes):
    request = {'target_mode': 'SPECIAL', 'specials': ['SPECIAL2'], 'filename': None, 'filenames': None, 'triggers': None,
               'watch_text': None, 'symbol': 'XAUUSD+', 'start': '2026-01-01', 'end': '2026-02-01', 'mode': 'BAR',
               'result_mode': 'VIRTUAL_ENTRY', 'spread_points': 20, 'build_only': False, 'rebuild': False,
               'available_only': True, 'virtual_entry': None}
    request.update(changes)
    return {'supported': True, 'action': 'START', 'request': request, 'job_id': None,
            'needs_clarification': False, 'clarification_question': None, 'message_ko': '확인'}


def asked(action, job_id=None):
    return {'supported': True, 'action': action, 'request': None, 'job_id': job_id,
            'needs_clarification': False, 'clarification_question': None, 'message_ko': '확인'}


# ---- what becomes one lanes request ---------------------------------------------------------------

def test_the_command_contract_has_the_trigger_and_file_lists_and_the_report():
    contract = commands.command_schema()
    Draft202012Validator.check_schema(contract)
    Draft202012Validator(contract).validate(start(triggers=['올존', '무지성 올존']))
    Draft202012Validator(contract).validate(start(target_mode='GENERATED', specials=None, filenames=['Test_SPECIAL001.py']))
    Draft202012Validator(contract).validate(asked('REPORT', 'a' * 32))


def test_a_trigger_list_is_one_request_with_a_run_per_trigger_and_the_screen_s_trading_time():
    from lab import lanes
    command, question = commands._normalize(start(triggers=['OZ', '무지성 올존', '브레이커 올존', '무지성 브레이커 올존']), snapshot())
    plan = command['request']['lane_plan']
    assert question is None and plan['strategies'] == ['SPECIAL2'] and plan['triggers'] == TRIGGERS
    assert plan['special_settings'] == {'SPECIAL2': {'trigger': '무지성 올존', 'time_filters': LONDON}}
    checked = lanes.normalize(plan)
    assert lanes.every_lane(checked) == [{'strategy': 'SPECIAL2', 'trigger': name} for name in TRIGGERS]
    s, kind, _ = lanes.build(checked, lanes.every_lane(checked), ROOT)
    assert kind == 'normal' and s['special_time_filters'] == {'SPECIAL2': LONDON} and s['spread_points'] == {'XAUUSD+': 20.0}
    text = commands._preview(command, snapshot())
    assert '함께 돌릴 실행: 4개' in text and '  4. SPECIAL2 · 무지성 브레이커 올존' in text
    assert 'SPECIAL2: 거래시간' in text and 'SPECIAL2: 최종 OZ' not in text


def test_several_strategies_with_a_virtual_entry_run_together_each_on_its_screen_trigger():
    from lab import lanes
    command, question = commands._normalize(start(specials=['SPECIAL1', 'SPECIAL2']), snapshot())
    plan = lanes.normalize(command['request']['lane_plan'])
    assert question is None
    # No trigger list: each strategy's own (SPECIAL2's saved one, SPECIAL1's recipe one).
    assert lanes.every_lane(plan) == [{'strategy': 'SPECIAL1', 'trigger': None}, {'strategy': 'SPECIAL2', 'trigger': '무지성 올존'}]
    text = commands._preview(command, snapshot())
    assert '가상진입: 전략마다 레시피 값' in text and 'SPECIAL2: 최종 OZ 무지성 올존' in text


def test_one_strategy_keeps_its_entry_policy_and_base_frame():
    from lab import lanes
    from event_backtest.settings import variant_scenarios
    from event_backtest.virtual_defaults import recipe_on_base, strategy_profile
    profile = strategy_profile('SPECIAL7')
    policy = copy.deepcopy(profile['default'])
    policy['stop'] = {'kind': 'ATR', 'tf': 'SIGNAL', 'period': 14, 'multiplier': 2.0}
    command, _ = commands._normalize(start(specials=['SPECIAL7'], triggers=['올존', '무지성 올존'], virtual_entry=policy), snapshot())
    assert command['request']['lane_plan']['virtual_entries'] == {'SPECIAL7': command['request']['virtual_entry']}
    # A base frame refills the recipe on it, for every trigger's run.
    command, _ = commands._normalize(start(specials=['SPECIAL7'], triggers=['올존', '무지성 올존'], base_frame='2m'), snapshot())
    moved = recipe_on_base(profile, '2m')
    assert command['request']['lane_plan']['virtual_entries'] == {'SPECIAL7': moved}
    checked = lanes.normalize(command['request']['lane_plan'])
    s, _, _ = lanes.build(checked, lanes.every_lane(checked), ROOT)
    assert [run['virtual_entry']['tf'] for run in variant_scenarios(s)] == ['2m', '2m']


def test_alerts_only_of_several_specials_without_triggers_stay_one_engine():
    command, question = commands._normalize(start(specials=['SPECIAL1', 'SPECIAL2'], result_mode='ALERT_ONLY'), snapshot())
    assert question is None and 'lane_plan' not in command['request']


def test_generated_files_replay_together_and_one_file_is_its_ordinary_run(monkeypatch):
    from lab import lanes, storage
    seen = {'generated': [{'filename': 'Test_SPECIAL001.py'}, {'filename': 'Test_SPECIAL002.py'}]}
    # The files are not written in this test: the Part2 check runs when they are (at confirmation).
    monkeypatch.setattr(storage, 'generated_path', lambda name: (_ for _ in ()).throw(FileNotFoundError(name)))
    many = start(target_mode='GENERATED', specials=None, filenames=['Test_SPECIAL001.py', 'Test_SPECIAL002.py'],
                 result_mode='ALERT_ONLY')
    command, question = commands._normalize(many, {**snapshot(), **seen})
    plan = lanes.normalize(command['request']['lane_plan'])
    assert question is None and plan['generated'] and plan['strategies'] == ['Test_SPECIAL001', 'Test_SPECIAL002']
    assert 'special_settings' not in command['request']['lane_plan']
    one, _ = commands._normalize(start(target_mode='GENERATED', specials=None, filenames=['Test_SPECIAL001.py']), {**snapshot(), **seen})
    assert one['request']['filename'] == 'Test_SPECIAL001.py' and 'lane_plan' not in one['request']


@pytest.mark.parametrize('changes,message', [
    ({'triggers': ['없는 트리거']}, '중에서 고르세요'),
    ({'triggers': '올존'}, '트리거 목록'),
    ({'specials': ['SPECIAL8'], 'triggers': ['올존', '무지성 올존']}, 'OZ 트리거가 없어'),
    ({'specials': ['SPECIAL1', 'SPECIAL2'], 'virtual_entry': 'policy'}, '전략 하나로'),
    ({'specials': ['SPECIAL1', 'SPECIAL2'], 'base_frame': '2m'}, '전략 하나로'),
    ({'triggers': ['올존'], 'rebuild': True, 'available_only': False}, '재구축'),
    ({'target_mode': 'WATCH', 'specials': None, 'watch_text': '5분 매수 올존', 'triggers': ['올존']}, 'SPECIAL 또는 생성 전략'),
    ({'filenames': ['Test_SPECIAL001.py']}, '생성 전략 이외'),
    ({'target_mode': 'GENERATED', 'specials': None, 'filename': 'Test_SPECIAL001.py', 'filenames': ['Test_SPECIAL001.py']}, '하나로만'),
])
def test_a_wrong_combination_request_says_what_to_do(changes, message):
    from event_backtest.virtual_defaults import strategy_profile
    if changes.get('virtual_entry') == 'policy':
        changes = {**changes, 'virtual_entry': strategy_profile('SPECIAL2')['default']}
    with pytest.raises(ValueError, match=message):
        commands._normalize(start(**changes), snapshot())


# ---- starting it, and its result tables ------------------------------------------------------------

def test_starting_runs_the_plan_and_an_all_finished_plan_points_at_the_report(monkeypatch):
    from lab import lanes, unified_backtest
    started = []
    monkeypatch.setattr(unified_backtest, 'warehouse', lambda: Path('W'))
    monkeypatch.setattr(lanes, 'start_saved', lambda plan, warehouse=None: started.append((plan, warehouse)) or
                        {'job_id': 'b' * 32, 'runs': 4, 'skipped': 0, 'message': '실행 4개를 시작했습니다.'})
    command, _ = commands._normalize(start(triggers=TRIGGERS), snapshot())
    result = commands.execute(copy.deepcopy(command))
    assert result['job_id'] == 'b' * 32 and result['phase'] == 'planning'
    assert started == [(command['request']['lane_plan'], 'W')]
    monkeypatch.setattr(lanes, 'start_saved', lambda plan, warehouse=None: {'job_id': None, 'runs': 0, 'message': '끝'})
    with pytest.raises(ValueError, match='결과 표를 요청하세요'):
        commands.execute(copy.deepcopy(command))


def test_a_report_picks_the_latest_finished_request_and_shows_its_tables(monkeypatch):
    from lab import lanes, unified_backtest
    jobs = [{'job_id': 'c' * 32, 'phase': 'run', 'active': True},
            {'job_id': 'd' * 32, 'phase': 'complete', 'active': False, 'source': '레인 4개: SPECIAL2'},
            {'job_id': 'e' * 32, 'phase': 'complete', 'active': False}]
    command, question = commands._normalize(asked('REPORT'), snapshot(jobs))
    assert question is None and command == {'action': 'REPORT', 'job_id': 'd' * 32}
    assert commands._normalize(asked('REPORT', 'e' * 32), snapshot(jobs))[0]['job_id'] == 'e' * 32
    assert commands._normalize(asked('REPORT'), snapshot(jobs[:1]))[1].startswith('결과가 있는 작업이 없습니다')
    monkeypatch.setattr(unified_backtest, 'warehouse', lambda: Path('W'))
    monkeypatch.setattr(unified_backtest, 'status', lambda job_id: {'symbol': 'XAUUSD+', 'phase': 'complete'})
    monkeypatch.setattr(lanes, 'job_report', lambda job_id, warehouse=None: f'# 레인 결과 {job_id[:4]} {warehouse}')
    assert commands.execute(command) == {'job_id': 'd' * 32, 'symbol': 'XAUUSD+', 'phase': 'complete',
                                         'message': '# 레인 결과 dddd W'}
    assert commands._preview(command, snapshot(jobs)).startswith('결과 표')


def test_the_research_conversation_shows_a_report_at_once():
    executed = []

    class Agent:
        last_intent = None
    session = research_backtest.Session(provider=None, strategy_loader=Agent, context_loader=lambda: snapshot(
        [{'job_id': 'd' * 32, 'phase': 'complete', 'active': False}]), executor=lambda command: executed.append(command) or {'ok': 1})
    shown = session.accept_plan({'strategy_text': None, 'steps': [{'draft': False, 'command': asked('REPORT')}],
                                 'needs_clarification': False, 'clarification_question': None})
    assert executed == [{'action': 'REPORT', 'job_id': 'd' * 32}] and shown['result'] == {'ok': 1} and not shown['can_confirm']


def test_a_sequence_checks_every_file_of_a_lanes_request(tmp_path, monkeypatch):
    from lab import backtest_sequences, storage
    monkeypatch.setattr(backtest_sequences, '_running', {})      # no sequence is left behind
    files = {}
    for name in ('Test_SPECIAL001.py', 'Test_SPECIAL002.py'):
        files[name] = tmp_path / name
        files[name].write_text(name, encoding='utf-8')
    monkeypatch.setattr(storage, 'generated_path', lambda name: files[name])
    command = {'action': 'START', 'request': {'target_mode': 'GENERATED', 'filename': None, 'symbol': 'XAUUSD+',
                                              'filenames': list(files)}}
    result = backtest_sequences.start([command], executor=lambda value: {'job_id': 'f' * 32}, folder=tmp_path / 'seq',
                                      background=False)
    assert set(result['strategy_digests']) == set(files)


# ---- the external model's rules ----------------------------------------------------------------------

def test_the_external_model_gets_the_combination_rules_and_keeps_the_lists():
    from common_ai.security import _POLICY_PROMPT, external_payload
    for text in (_POLICY_PROMPT, commands._prompt({}), research_backtest.PROMPT):
        assert 'START 하나로 함께 돌린다(레인)' in text and 'REPORT' in text and 'filenames' in text
        assert '가상 진입은 전략 1개만' not in text
    assert 'REPORT' in research_interpreter.PROMPT and 'triggers 목록' in research_interpreter.PROMPT
    plan = {'strategy_text': None, 'steps': [{'draft': False, 'command': start(triggers=['올존', '무지성 올존'])}],
            'needs_clarification': False, 'clarification_question': None}
    jobs = [{'job_id': 'd' * 32, 'phase': 'complete', 'source': '레인 8개: Test_SPECIAL001, Test_SPECIAL002'}]
    messages = [{'role': 'user', 'content': json.dumps({'message': '결과 표 보여줘',
        'context': {'selection': research_interpreter.selection(snapshot(jobs)), 'previous_plan': plan}}, ensure_ascii=False)}]
    safe, _, _ = external_payload(messages, [], research_interpreter.response_schema('strategy'))
    context = json.loads(safe[1]['content'])['context']
    assert context['previous_plan']['steps'][0]['command']['request']['triggers'] == ['올존', '무지성 올존']
    assert context['selection']['jobs'][0]['source'] == '레인 8개: Test_SPECIAL001, Test_SPECIAL002'
