"""Stored-data policy stays explicit through AI, editing and job transport."""
import copy
import json
from pathlib import Path
import sys
from unittest.mock import Mock
from types import SimpleNamespace

import pytest
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'Part3')]

from lab.ai import backtest_commands as commands, research_interpreter
from lab import integration, unified_backtest, backtest_adapters, backtest_jobs, storage
from common_ai import security, external_prompt
from test_backtest_commands67 import request, value, snapshot, session
from test_research79 import research_reply, plan, step, sequences, OTHER_JOB
from test_research_modes80 import conversation


def test_recent_policy_keeps_requested_dates_and_does_not_parse_user_words():
    payload = value(data=request(start='2026-07-04', end='2026-10-04', available_only=True))
    agent, execute = session(payload)
    response = agent.send('언어 표기는 실행 정책을 다시 판정하는 코드가 아닙니다.')
    selected = response['command']['request']
    assert selected['start'] == '2026-07-04' and selected['end'] == '2026-10-04'
    assert selected['available_only'] is True
    assert '요청 기간 안의 보유 데이터만' in response['preview']
    assert '자동 구축하지 않습니다' in response['preview']
    execute.assert_not_called()
    agent.confirm(response['revision'])
    assert execute.call_args.args[0]['request']['available_only'] is True


@pytest.mark.parametrize('policy', [None, False])
def test_explicit_dates_and_today_build_policy_remain_unchanged(policy):
    clean, question = commands._normalize(value(data=request(
        end='2026-10-09', available_only=policy)), snapshot())
    assert question is None
    assert clean['request']['end'] == '2026-10-09'
    assert clean['request']['available_only'] is False
    build, question = commands._normalize(value(data=request(
        specials=[], build_only=True, available_only=policy)), snapshot())
    assert question is None and build['request']['build_only'] is True
    assert build['request']['available_only'] is False


@pytest.mark.parametrize('changes', [
    {'available_only': 'true'}, {'available_only': 1}, {'available_only': []},
    {'available_only': True, 'build_only': True},
    {'available_only': True, 'rebuild': True},
])
def test_invalid_or_contradictory_policies_rejected_without_execution(changes):
    agent, execute = session(value(data=request(**changes)))
    response = agent.send('백테스트')
    assert not response['can_confirm']
    execute.assert_not_called()
    with pytest.raises(ValueError):
        unified_backtest._available_only(changes)


def test_shared_schema_and_transmitted_contract_include_same_policy():
    original = research_interpreter.response_schema('strategy')
    payload = json.loads(research_reply('BACKTEST', research_plan=plan(step(available_only=True)))['content'])
    assert Draft202012Validator(original).is_valid(payload)
    messages = [{'role': 'system', 'content': json.dumps({'moses_contract': {}})},
        {'role': 'user', 'content': json.dumps({'mode': 'strategy', 'message': '최근 3개월 백테스트',
            'context': {'selection': {'today': '2026-10-04'}, 'previous_plan': payload['plan']}})}]
    sanitized, _, sanitized_schema = security.external_payload(messages, [], original)
    policy = json.loads(sanitized[0]['content'])['instructions']
    assert 'today 기준의 요청 기간을 유지' in policy
    assert 'command.request.available_only=true' in policy
    transmitted, _, returned_schema = external_prompt.prepare(messages, [], original, {})
    assert 'command.request.available_only=true' in transmitted[0]['content']
    assert 'available_only' in transmitted[0]['content']
    assert Draft202012Validator(sanitized_schema).is_valid(payload)
    assert Draft202012Validator(returned_schema).is_valid(payload)
    context = json.loads(sanitized[-1]['content'])['context']
    assert context['previous_plan']['steps'][0]['command']['request']['available_only'] is True


def test_table_date_edit_preserves_policy_and_confirm_passes_it_once(monkeypatch):
    research, agent, provider, apply = conversation([research_reply('BACKTEST',
        research_plan=plan(step(available_only=True, start='2026-07-04', end='2026-10-04')))])
    first = research.send('최근 3개월 백테스트')
    assert first['can_confirm']
    changed = copy.deepcopy(first['plan'])
    changed['steps'][0]['command']['request']['start'] = '2026-08-01'
    edited = research.edit(first['revision'], None, 'BACKTEST', changed)
    assert edited['ok'] and edited['can_confirm']
    assert edited['plan']['steps'][0]['command']['request']['available_only'] is True
    start = Mock(return_value={'sequence_id': OTHER_JOB, 'jobs': []})
    monkeypatch.setattr(sequences, 'start', start)
    research.confirm(edited['revision'])
    clean = start.call_args.args[0][0]['request']
    assert clean['available_only'] is True and clean['start'] == '2026-08-01'
    assert clean['end'] == '2026-10-04'
    start.assert_called_once()
    apply.assert_not_called()
    assert len(provider.exchanges) == 1


@pytest.mark.parametrize('target', ['SPECIAL', 'WATCH', 'GENERATED'])
def test_execution_transport_keeps_policy_for_every_target(target, monkeypatch):
    payload = request(target_mode=target, available_only=True, build_only=False, rebuild=False)
    if target == 'GENERATED':
        payload.update(specials=[], filename='Test_SPECIAL001.py', watch_text=None)
    elif target == 'WATCH':
        # 수정본139: WATCH runs alert-only; the inherited VIRTUAL_ENTRY would be refused.
        payload.update(specials=[], filename=None, watch_text='1분 RSI 70 돌파', result_mode='ALERT_ONLY')
    clean, question = commands._normalize(value(data=payload), snapshot())
    assert question is None
    scenario = {'available_only': True, 'start': clean['request']['start'], 'end': clean['request']['end']}
    normal_start = Mock(return_value={'job_id': OTHER_JOB})
    monkeypatch.setattr(unified_backtest, 'start', normal_start)
    if target != 'GENERATED':
        commands.execute(clean)
        assert normal_start.call_args.args[0]['available_only'] is True
        return
    generated = Mock(return_value=(dict(scenario), {'filename': payload['filename']}))
    start = Mock(return_value={'job_id': OTHER_JOB})
    monkeypatch.setattr(integration, '_root', lambda: ROOT)
    monkeypatch.setattr(integration, '_python', lambda: sys.executable)
    monkeypatch.setattr(storage, 'connections', lambda: {'warehouse': 'warehouse'})
    monkeypatch.setattr(storage, 'resolve_warehouse', lambda *_: ROOT / 'warehouse')
    monkeypatch.setattr(backtest_adapters, 'prepare_generated', generated)
    monkeypatch.setattr(backtest_jobs, 'start', start)
    result = commands.execute(clean)
    assert result['message'] == '보유 데이터 사용 구간 확인을 시작했습니다.'
    assert generated.call_args.args[0]['available_only'] is True
    assert start.call_args.args[0]['scenario']['available_only'] is True
    assert start.call_args.args[0]['scenario']['start'] == scenario['start']
    assert start.call_args.args[0]['scenario']['end'] == scenario['end']
    normal_start.assert_not_called()


@pytest.mark.parametrize('policy', [True, False, None])
def test_generated_adapter_validates_and_passes_policy_to_scenario(policy, monkeypatch, tmp_path):
    strategy = tmp_path / 'test' / 'Test_SPECIAL001.py'
    strategy.parent.mkdir()
    strategy.write_text('TEST_RECIPE = {}', encoding='utf-8')
    monkeypatch.setattr(storage, 'generated_path', lambda _: strategy)
    monkeypatch.setattr(storage, 'reopen', Mock())
    from strategy_recipe import user_catalog
    monkeypatch.setattr(user_catalog, 'test_directory', lambda _: strategy.parent)
    current = {'cores': None, 'work_size': 'AUTO', 'capture_start': 'keyframe', 'overlap_trading_days': 3}
    create = Mock(side_effect=lambda **data: data)
    settings = SimpleNamespace(settings=lambda: current, scenario=create)
    monkeypatch.setattr(backtest_adapters, '_part2', lambda _: settings)
    data = request(target_mode='GENERATED', specials=[], filename=strategy.name, available_only=policy)
    scenario, adapter = backtest_adapters.prepare_generated(data, tmp_path)
    assert scenario['available_only'] is (policy is True)
    assert scenario['start'] == data['start'] and scenario['end'] == data['end']
    assert adapter['filename'] == strategy.name
    create.assert_called_once()


@pytest.mark.parametrize('target', ['SPECIAL', 'WATCH'])
def test_normal_adapter_passes_policy_before_scenario_creation(target, monkeypatch):
    from strategy_recipe import registry
    import event_selection
    monkeypatch.setattr(registry, 'list_presets', lambda _: {'SPECIAL1': {}})
    monkeypatch.setattr(event_selection, 'strategy_dependencies', lambda: {'SPECIAL1': {}})
    make = Mock(side_effect=lambda _ui, common: dict(common))
    ui = SimpleNamespace(load=lambda: {'specials': {'SPECIAL1': {'enabled': True}}}, make_scenario=make)
    monkeypatch.setattr(unified_backtest, '_part2', lambda: (object(), ui, object()))
    data = request(target_mode=target, available_only=True)
    if target == 'WATCH':
        data.update(specials=[], watch_text='1분 RSI 70 돌파')
    scenario = unified_backtest._scenario(data)
    assert scenario['available_only'] is True
    assert scenario['start'] == data['start'] and scenario['end'] == data['end']
    make.assert_called_once()


def test_result_transport_keeps_requested_effective_and_excluded_periods(monkeypatch):
    adjustment = {'requested': {'start': '2026-07-04', 'end': '2026-10-04'},
                  'effective': {'start': '2026-07-06', 'end': '2026-10-03'}}
    available = [{'start': '2026-07-06', 'end': '2026-08-01'},
                 {'start': '2026-09-01', 'end': '2026-10-03'}]
    excluded = [{'start': '2026-08-01', 'end': '2026-09-01', 'reason': 'MISSING'}]
    data = {'period_adjustment': adjustment, 'available_periods': available, 'excluded_periods': excluded}
    files = SimpleNamespace(data=data, paths={'summary': None, 'trades': None},
        analysis=lambda *args: (None, 'missing', ''), describe=lambda: [])
    monkeypatch.setattr(unified_backtest, '_job', lambda _: {})
    monkeypatch.setattr(unified_backtest, '_result_files', lambda _: files)
    result = unified_backtest.result(OTHER_JOB)
    assert result['period_adjustment'] == adjustment
    assert result['available_periods'] == available
    assert result['excluded_periods'] == excluded
    files.data = {}
    old = unified_backtest.result(OTHER_JOB)
    assert old['period_adjustment'] is None
    assert old['available_periods'] == [] and old['excluded_periods'] == []


@pytest.mark.parametrize('phase', ['confirm', 'planned'])
def test_public_plan_displays_periods_without_private_execution_material(phase):
    change = {'requested_start': '2026-07-04', 'requested_end': '2026-10-04',
              'start': '2026-07-06', 'end': '2026-10-03', 'reason': 'AVAILABLE_ONLY',
              'message': '실제 사용 기간입니다.'}
    selected = [{'start': '2026-07-06', 'end': '2026-08-01'},
                {'start': '2026-09-01', 'end': '2026-10-03'}]
    excluded = [{'start': '2026-08-01', 'end': '2026-09-01', 'reason': 'NOT_STORED',
                 'message': '보유 데이터가 없습니다.'}]
    private = {'approval_token': 'private-token', 'reuse': [{'path': 'private/capture'}],
               'ea_build_hash': 'private-hash', 'symbol': 'XAUUSD+'}
    stored = {**private, 'record': [], 'convert': [], 'estimate': {'recording_seconds': 0},
              'period_adjustment': {**change, 'path': 'private/path'},
              'available_periods': [{**row, 'path': 'private/path'} for row in selected],
              'excluded_periods': [{**row, 'approval_token': 'private-token'} for row in excluded]}
    public = backtest_jobs._plan_public({'phase': phase, 'plan': stored})
    assert public['period_adjustment'] == change
    assert public['available_periods'] == selected and public['excluded_periods'] == excluded
    assert not set(private).intersection(public)
    assert 'private' not in json.dumps(public)
    assert backtest_jobs._plan_public({'phase': 'run', 'plan': stored}) is None


@pytest.mark.parametrize('policy', [True, False, None])
def test_job_status_keeps_policy_for_reconnection_and_old_jobs(policy, monkeypatch, tmp_path):
    scenario = {'symbol': 'XAUUSD+', 'start': '2026-07-04', 'end': '2026-10-04', 'mode': 'BAR'}
    if policy is not None:
        scenario['available_only'] = policy
    stored = {'scenario': scenario, 'kind': 'normal', 'phase': 'planned', 'folder': tmp_path,
        'plan': None, 'active': False, 'adapter': {}, 'identity_warning': False, 'cancel_requested': False}
    monkeypatch.setattr(backtest_jobs, 'get', lambda *_args, **_kwargs: stored)
    result = backtest_jobs.status(OTHER_JOB)
    assert result['scenario']['available_only'] is (policy is True)
    assert 'warehouse' not in result['scenario'] and 'project_root' not in result['scenario']
