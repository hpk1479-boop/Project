"""171: OZ triggers compared from the backtest screen reach the scenario; the run list and a result name each
run's trigger; a request with several runs opens when it has ended."""
from copy import deepcopy
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

PART3 = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PART3))
from lab import backtest_jobs, unified_backtest
settings, ui_model, _ = unified_backtest._part2()
from event_backtest import runner
from event_backtest.settings import variant_scenarios
from event_backtest.virtual_defaults import strategy_profile


def request(result_mode='ALERT_ONLY', trigger=None, **changes):
    value = {'symbol': 'XAUUSD+', 'start': '2025-09-01', 'end': '2025-09-08', 'mode': 'BAR', 'target_mode': 'SPECIAL',
             'specials': ['SPECIAL2'], 'special_settings': {'SPECIAL2': {'enabled': True, 'trigger': trigger, 'time_filters': None}},
             'result_mode': result_mode}
    if result_mode == 'VIRTUAL_ENTRY':
        value['virtual_entry'] = deepcopy(strategy_profile('SPECIAL2')['default'])
    value.update(changes)
    return value


def scenario_of(value):
    with patch.object(ui_model, 'load', return_value=deepcopy(ui_model.load())), \
         patch.object(ui_model, 'save_virtual_entry'):
        return unified_backtest._scenario(value)


@pytest.mark.parametrize('result_mode', ['ALERT_ONLY', 'VIRTUAL_ENTRY'])
def test_compared_triggers_reach_the_scenario(result_mode):
    s = scenario_of(request(result_mode, '무지성 올존', trigger_variants=['무지성 올존', '올존', '무지성 브레이커 올존']))
    assert s['trigger_variants'] == ['무지성 올존', '올존', '무지성 브레이커 올존']
    assert [run['triggers'] for run in variant_scenarios(s)] == [
        {'SPECIAL2': '무지성 올존'}, {'SPECIAL2': '올존'}, {'SPECIAL2': '무지성 브레이커 올존'}]


def test_watch_and_a_data_build_ignore_the_list():
    s = scenario_of(request(target_mode='WATCH', watch_text='1분 올존 알려줘', trigger_variants=['올존', '무지성 올존']))
    assert s['trigger_variants'] == [] and variant_scenarios(s) == [s]
    s = scenario_of(request(build_only=True, trigger_variants=['올존', '무지성 올존']))
    assert s['trigger_variants'] == [] and s['build_only'] is True


@pytest.mark.parametrize('changes,message', [
    ({'trigger_variants': ['올존', '올존']}, '두 개 이상'),
    ({'specials': ['SPECIAL8'], 'special_settings': {'SPECIAL8': {'enabled': True, 'trigger': None, 'time_filters': None}},
      'trigger_variants': ['올존', '무지성 올존']}, 'OZ 트리거가 없어'),
])
def test_an_invalid_list_is_refused(changes, message):
    with pytest.raises(ValueError, match=message):
        scenario_of(request(**changes))


def test_the_run_list_names_a_trigger_other_than_the_strategys_own():
    lead = scenario_of(request(trigger='무지성 올존', trigger_variants=['무지성 올존', '브레이커 올존', '올존']))
    runs = variant_scenarios(lead)
    assert backtest_jobs._trigger(lead) == '무지성 올존'                              # the job: its first trigger
    assert [backtest_jobs._trigger(run) for run in runs] == ['무지성 올존', None, '올존']   # SPECIAL2's own: unnamed
    assert backtest_jobs._trigger({'strategies': ['SPECIAL1', 'SPECIAL2'], 'triggers': {'SPECIAL1': '올존'}}) is None


def test_a_listed_run_carries_its_trigger(tmp_path):
    lead = scenario_of(request(trigger_variants=['무지성 올존', '올존']))
    folder = tmp_path / 'warehouse' / 'runs' / ('d' * 32)
    folder.mkdir(parents=True)
    (folder / 'result.json').write_text(json.dumps({'status': 'COMPLETE', 'scenario': variant_scenarios(lead)[1]}),
                                        encoding='utf-8')
    context = {'warehouse': tmp_path / 'warehouse', 'project_root': PART3.parent, 'python_executable': sys.executable}
    [item] = backtest_jobs.recent(20, **context)['items']
    assert item['trigger'] == '올존' and item['compared_triggers'] == [] and item['phase'] == 'complete'


def test_a_result_names_its_own_and_the_other_triggers(tmp_path):
    lead = scenario_of(request(trigger_variants=['올존', '무지성 올존', '브레이커 올존']))
    runs = variant_scenarios(lead)
    ids = ['a' * 32, 'b' * 32, 'c' * 32]
    for run_id, run in zip(ids, runs):
        (tmp_path / run_id).mkdir()
        data = {'scenario': run, 'tested_with': [other for other in ids if other != run_id],
                'applied_special_settings': runner.applied_strategy_settings(run, runner.runtime_config(run))}
        (tmp_path / run_id / 'result.json').write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
    data = json.loads((tmp_path / ids[1] / 'result.json').read_text('utf-8'))
    assert unified_backtest._tested_trigger(data) == '무지성 올존'
    assert unified_backtest._compared_runs({'folder': tmp_path / ids[1]}, data) == [
        {'run_id': ids[0], 'base_frame': None, 'trigger': '올존', 'strategy': 'SPECIAL2'},
        {'run_id': ids[2], 'base_frame': None, 'trigger': '브레이커 올존', 'strategy': 'SPECIAL2'}]   # SPECIAL2's own, named here
    assert unified_backtest._tested_trigger({'scenario': {'strategies': ['SPECIAL8']}}) is None


def write(folder, **fields):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / 'result.json').write_text(json.dumps({'status': 'COMPLETE', 'result_mode': 'ALERT_ONLY', **fields}),
                                        encoding='utf-8')
    return folder


def test_a_request_with_several_runs_opens_when_it_has_ended(tmp_path):
    together = write(tmp_path / 'together', tested_with=['b' * 32])
    alone = write(tmp_path / 'alone')
    # An alert-only run alone opens at once, as before; one of several runs once the request has ended.
    assert backtest_jobs._result_ready({'folder': alone, 'active': True}) is True
    assert backtest_jobs._result_ready({'folder': together, 'active': True}) is False
    assert backtest_jobs._result_ready({'folder': together, 'active': False}) is True
    # A run held for its running request reads as running (수정본163), whatever its result.
    assert backtest_jobs._result_ready({'folder': alone, 'active': True, 'lane_of': 'e' * 32}) is False
