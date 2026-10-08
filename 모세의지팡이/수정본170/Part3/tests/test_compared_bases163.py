"""163: a result names every other base frame of its request, one that reused a finished replay included."""
import json
import sys
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

PART3 = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PART3))
from lab import unified_backtest
settings, ui_model, _ = unified_backtest._part2()
from event_backtest.settings import variant_scenarios
from event_backtest.virtual_defaults import strategy_profile


def runs():
    value = {'symbol': 'XAUUSD+', 'start': '2025-09-01', 'end': '2025-09-08', 'mode': 'BAR', 'target_mode': 'SPECIAL',
             'specials': ['SPECIAL9'], 'special_settings': {'SPECIAL9': {'enabled': True, 'trigger': None, 'time_filters': None}},
             'result_mode': 'VIRTUAL_ENTRY', 'virtual_entry': deepcopy(strategy_profile('SPECIAL9')['default']),
             'virtual_bases': ['1m', '2m', '5m']}
    with patch.object(ui_model, 'load', return_value=deepcopy(ui_model.load())), patch.object(ui_model, 'save_virtual_entry'):
        return variant_scenarios(unified_backtest._scenario(value))


def write(folder, ids, together, replayed):
    for run_id, run in zip(ids, runs()):
        data = {'scenario': run, 'replayed_with': replayed[run_id]}
        if together:
            data['tested_with'] = [other for other in ids if other != run_id]
        (folder / run_id).mkdir()
        (folder / run_id / 'result.json').write_text(json.dumps(data), encoding='utf-8')


def compared(folder, run_id):
    data = json.loads((folder / run_id / 'result.json').read_text('utf-8'))
    return unified_backtest._compared_runs({'folder': folder / run_id}, data), data


def test_the_reused_base_frame_is_named_by_and_with_the_others(tmp_path):
    ids = ['a' * 32, 'b' * 32, 'c' * 32]                                  # 1m, 2m (reused), 5m
    write(tmp_path, ids, True, {ids[0]: [ids[2]], ids[1]: [], ids[2]: [ids[0]]})
    rows, _ = compared(tmp_path, ids[0])
    assert rows == [{'run_id': ids[1], 'base_frame': '2m'}, {'run_id': ids[2], 'base_frame': '5m'}]
    rows, data = compared(tmp_path, ids[1])
    assert rows == [{'run_id': ids[0], 'base_frame': '1m'}, {'run_id': ids[2], 'base_frame': '5m'}]
    assert unified_backtest._together(data) and unified_backtest._tested_frame(data['scenario']) == '2m'


def test_a_result_saved_before_163_still_names_the_runs_replayed_with_it(tmp_path):
    ids = ['a' * 32, 'b' * 32, 'c' * 32]
    write(tmp_path, ids, False, {ids[0]: [ids[1], ids[2]], ids[1]: [ids[0], ids[2]], ids[2]: [ids[0], ids[1]]})
    rows, _ = compared(tmp_path, ids[2])
    assert rows == [{'run_id': ids[0], 'base_frame': '1m'}, {'run_id': ids[1], 'base_frame': '2m'}]
