"""161: base frames compared in one replay reach the scenario; the run list and a result name each run's frame."""
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
from event_backtest.settings import variant_scenarios
from event_backtest.virtual_defaults import strategy_profile


def request(result_mode='VIRTUAL_ENTRY', **changes):
    value = {'symbol': 'XAUUSD+', 'start': '2025-09-01', 'end': '2025-09-08', 'mode': 'BAR', 'target_mode': 'SPECIAL',
             'specials': ['SPECIAL9'], 'special_settings': {'SPECIAL9': {'enabled': True, 'trigger': None, 'time_filters': None}},
             'result_mode': result_mode, 'virtual_entry': deepcopy(strategy_profile('SPECIAL9')['default'])}
    value.update(changes)
    return value


def scenario_of(value):
    with patch.object(ui_model, 'load', return_value=deepcopy(ui_model.load())), \
         patch.object(ui_model, 'save_virtual_entry') as save:
        return unified_backtest._scenario(value), save


def test_compared_base_frames_reach_the_scenario_in_frame_order():
    result, save = scenario_of(request(virtual_bases=['5m', '1m', '2m']))
    assert result['virtual_bases'] == ['1m', '2m', '5m']
    assert [run['virtual_entry']['tf'] for run in variant_scenarios(result)] == ['SIGNAL', '2m', '5m']
    save.assert_called_once()


@pytest.mark.parametrize('bases,message', [(['1m', '1m'], '두 개 이상'), (['1m', '7m'], '중에서 고르세요')])
def test_an_invalid_list_is_refused_and_nothing_is_saved(bases, message):
    with patch.object(ui_model, 'save_virtual_entry') as save:
        with pytest.raises(ValueError, match=message):
            unified_backtest._scenario(request(virtual_bases=bases))
    save.assert_not_called()


def test_alert_only_ignores_the_list():
    result, _ = scenario_of(request('ALERT_ONLY', virtual_bases=['1m', '2m']))
    assert result['virtual_bases'] == [] and variant_scenarios(result) == [result]


def test_the_run_list_names_each_runs_base_frame():
    lead, _ = scenario_of(request(virtual_bases=['1m', '2m']))
    runs = variant_scenarios(lead)
    assert backtest_jobs._base_frame(lead) == '1m'                 # the job: its first base frame
    assert [backtest_jobs._base_frame(run) for run in runs] == [None, '2m']   # SIGNAL: the recipe as written
    assert backtest_jobs._base_frame({'virtual_entry': None}) is None


def test_a_running_base_frame_waits_out_of_the_list_until_its_result(tmp_path):
    lead, _ = scenario_of(request(virtual_bases=['1m', '2m']))
    other = variant_scenarios(lead)[1]
    folder = tmp_path / 'warehouse' / 'runs' / ('d' * 32)
    folder.mkdir(parents=True)
    (folder / 'lane.json').write_text(json.dumps({'lead_run_id': 'e' * 32}), encoding='utf-8')
    context = {'warehouse': tmp_path / 'warehouse', 'project_root': PART3.parent, 'python_executable': sys.executable}
    from event_backtest.warehouse_cleanup import warehouse_activity
    with warehouse_activity(tmp_path / 'warehouse'):  # the replay still runs (163: no live lead and no lease = left over)
        assert backtest_jobs.recent(20, **context) == {'items': [], 'warnings': [], 'leftovers': []}
    (folder / 'result.json').write_text(json.dumps({'status': 'COMPLETE', 'scenario': other}), encoding='utf-8')
    [item] = backtest_jobs.recent(20, **context)['items']
    assert item['job_id'] == 'd' * 32 and item['base_frame'] == '2m' and item['phase'] == 'complete'


def test_a_result_names_its_own_and_the_other_base_frames(tmp_path):
    lead, _ = scenario_of(request(virtual_bases=['1m', '2m', '5m']))
    runs = variant_scenarios(lead)
    ids = ['a' * 32, 'b' * 32, 'c' * 32]
    for run_id, run in zip(ids, runs):
        (tmp_path / run_id).mkdir()
        (tmp_path / run_id / 'result.json').write_text(json.dumps({'scenario': run, 'replayed_with': [i for i in ids if i != run_id]}),
                                                       encoding='utf-8')
    job = {'folder': tmp_path / ids[1]}
    data = json.loads((tmp_path / ids[1] / 'result.json').read_text('utf-8'))
    assert unified_backtest._tested_frame(data['scenario']) == '2m'
    assert unified_backtest._compared_runs(job, data) == [{'run_id': ids[0], 'base_frame': '1m'}, {'run_id': ids[2], 'base_frame': '5m'}]
