"""169: the backtest list shows what a run tested, as kept when it started.

A run from 169 shows its kept plan, trigger, alert times, base frame, virtual entry and configuration
values, even after its recipe changed (and says so); a running run shows the copy in its folder; a run
from before 169 shows what it kept then (request, applied trigger and times), without conditions.
"""
import json
import shutil
import sys
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

PART3 = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PART3))
from lab import server, unified_backtest
unified_backtest._part2()
from event_backtest import runner
from event_backtest.portable import validate
from event_backtest.settings import scenario, variant_scenarios
from event_backtest.tested_settings import FILE, tested_settings as keep
from strategy_recipe import registry

PERIOD = dict(symbol='XAUUSD+', start='2026-09-01', end='2026-10-01')
TIMES = {'MAIN_ASIA': {'enabled': True}, 'MAIN_NEWYORK': {'enabled': True, 'start': '2200', 'end': '0100'}}


def write(folder, s, *, snapshot=True, result=True):
    """A run folder as Part2 leaves it: the snapshot from the start, result.json when it has ended."""
    config = runner.runtime_config(s)
    applied = runner.applied_strategy_settings(s, config)
    folder.mkdir(parents=True)
    data = {'scenario': s, 'applied_special_settings': applied, 'wonbi_sigma': float(config['WONBI_SIGMA'])}
    if snapshot:
        data['tested_settings'] = keep(s, config, applied)
        (folder / FILE).write_text(json.dumps(data['tested_settings'], ensure_ascii=False), encoding='utf-8')
    if result:
        (folder / 'result.json').write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
    return data


def shown(folder, job_scenario=None):
    identifier = folder.name
    with patch.dict(unified_backtest.JOBS, {identifier: {'folder': folder, 'scenario': job_scenario or {}}}):
        return unified_backtest.tested(identifier)


def test_a_run_shows_its_kept_plan_trigger_times_and_values(tmp_path):
    s = scenario(strategies=['SPECIAL2'], result_mode='VIRTUAL_ENTRY', triggers={'SPECIAL2': '무지성 브레이커 올존'},
                 special_time_filters={'SPECIAL2': TIMES}, spread_points={'XAUUSD+': 12}, **PERIOD)
    data = write(tmp_path / ('a' * 32), s)
    view = shown(tmp_path / ('a' * 32))
    (row,) = view['strategies']
    kept = data['tested_settings']['strategies']['SPECIAL2']
    assert view['kept'] is True and row['name'] == 'SPECIAL2' and row['now'] == 'same'
    assert row['conditions'] == kept['conditions'] and row['trigger'] == '무지성 브레이커 올존'
    assert row['times'] == '아시아 08:00~12:00, 뉴욕 22:00~01:00'          # MAIN_ASIA from the session setting
    assert {'time_filters', 'time_source', 'recipe_sha256'}.isdisjoint(row)
    assert view['virtual_entry'] == s['virtual_entry'] and view['virtual_entry_current'] is True
    assert (view['result_mode'], view['spread_points'], view['mode']) == ('VIRTUAL_ENTRY', 12.0, 'BAR')
    assert view['config'] == data['tested_settings']['config'] and view['session_labels']['MAIN_ASIA'] == '아시아'
    validate(view)


def test_a_recipe_changed_after_the_run_leaves_what_it_shows_and_says_so(tmp_path):
    s = scenario(strategies=['SPECIAL8'], **PERIOD)
    write(tmp_path / ('b' * 32), s)
    before = shown(tmp_path / ('b' * 32))
    # The recipe files as the registry has read them (수정본170: preset_entry copies one entry of them).
    entries = registry.builtin_entries()
    changed = deepcopy(entries)
    changed['SPECIAL8']['recipe']['strategy_intent']['steps'][1]['slow_period'] = 99
    with patch.object(registry, '_cached_builtin_entries', lambda: deepcopy(changed)):
        after = shown(tmp_path / ('b' * 32))
    assert before['strategies'][0]['now'] == 'same' and after['strategies'][0]['now'] == 'changed'
    assert after['strategies'][0]['conditions'] == before['strategies'][0]['conditions']
    assert after['strategies'][0]['conditions']['steps'][1]['slow_period'] == 50
    del changed['SPECIAL8']
    with patch.object(registry, '_cached_builtin_entries', lambda: deepcopy(changed)):
        assert shown(tmp_path / ('b' * 32))['strategies'][0]['now'] == 'missing'


def test_a_setting_changed_after_the_run_leaves_the_values_it_read(tmp_path):
    # Asia without its own times takes the session setting of the time.
    s = scenario(strategies=['SPECIAL1'], special_time_filters={'SPECIAL1': {'MAIN_ASIA': {'enabled': True}}}, **PERIOD)
    now = runner.runtime_config(s)
    then = {**now, 'WONBI_SIGMA': '2.5', 'MAIN_ASIA': '0700-1100'}
    assert (now['WONBI_SIGMA'], now['MAIN_ASIA']) != ('2.5', '0700-1100')
    with patch.object(runner, 'runtime_config', lambda s: then):
        write(tmp_path / ('c' * 32), s)
    view = shown(tmp_path / ('c' * 32))
    assert (view['config']['WONBI_SIGMA'], view['config']['MAIN_ASIA']) == ('2.5', '0700-1100')
    assert view['strategies'][0]['times'] == '아시아 07:00~11:00'


def test_a_running_run_shows_the_copy_in_its_folder(tmp_path):
    s = variant_scenarios(scenario(strategies=['SPECIAL8'], result_mode='VIRTUAL_ENTRY', virtual_bases=['1m', '3m'],
                                   **PERIOD))[1]
    data = write(tmp_path / ('d' * 32), s, result=False)
    view = shown(tmp_path / ('d' * 32), job_scenario=s)
    assert view['kept'] is True and view['strategies'][0]['base_frame'] == '3m'
    assert view['virtual_entry'] == data['tested_settings']['virtual_entry'] and view['virtual_entry']['tf'] == '3m'


def test_a_run_from_before_169_shows_what_it_kept(tmp_path):
    from event_backtest.virtual_defaults import strategy_on_base, strategy_profile
    intent = strategy_on_base(strategy_profile('SPECIAL8'), 'SIGNAL')
    intent['steps'][1]['slow_period'] = 60
    s = scenario(strategies=['SPECIAL8'], result_mode='VIRTUAL_ENTRY', virtual_strategy=intent, **PERIOD)
    write(tmp_path / ('e' * 32), s, snapshot=False)
    view = shown(tmp_path / ('e' * 32))
    (row,) = view['strategies']
    assert view['kept'] is False and row['edited'] is True and row['conditions'] == s['virtual_strategy']
    assert row['now'] is None and row['title'] is None and row['times'] == '24시간'
    assert list(view['config'])[0] == 'WONBI_SIGMA' and set(view['config']) > {'MAIN_ASIA', 'MAIN_LONDON', 'MAIN_NEWYORK'}
    s = scenario(strategies=['SPECIAL1'], **PERIOD)
    write(tmp_path / ('f' * 32), s, snapshot=False)
    (row,) = shown(tmp_path / ('f' * 32))['strategies']
    assert row['conditions'] is None and row['edited'] is False and row['trigger'] == registry.default_settings('SPECIAL1')[0]


def test_an_older_virtual_entry_form_is_marked(tmp_path):
    s = scenario(strategies=['SPECIAL8'], result_mode='VIRTUAL_ENTRY', **PERIOD)
    data = write(tmp_path / ('1' * 32), s, snapshot=False)
    data['scenario']['virtual_entry'] = {'schema': 1, 'mode': 'CONFIRM', 'tf': '1m', 'stop': {'kind': 'AUTO'}}
    (tmp_path / ('1' * 32) / 'result.json').write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
    assert shown(tmp_path / ('1' * 32))['virtual_entry_current'] is False


def test_watch_and_commands(tmp_path):
    s = scenario(strategies=['WATCH'], commands=[{'strategy': 'WATCH', 'text': '1분 HMA17 골든크로스', 'chat_id': 'BACKTEST'}],
                 **PERIOD)
    write(tmp_path / ('2' * 32), s)
    view = shown(tmp_path / ('2' * 32))
    assert view['strategies'] == [] and view['commands'] == ['1분 HMA17 골든크로스']


def test_a_moved_run_folder_shows_the_same(tmp_path):
    s = scenario(strategies=['SPECIAL2'], **PERIOD)
    write(tmp_path / 'one' / ('3' * 32), s)
    shutil.copytree(tmp_path / 'one', tmp_path / 'moved' / 'elsewhere')
    assert shown(tmp_path / 'moved' / 'elsewhere' / ('3' * 32)) == shown(tmp_path / 'one' / ('3' * 32))


def test_the_http_route_reads_the_same(tmp_path):
    identifier = '4' * 32
    write(tmp_path / identifier, scenario(strategies=['SPECIAL8'], **PERIOD))
    handler = object.__new__(server.Handler)
    handler.server = SimpleNamespace(token='test-token', server_port=8763, last_seen=None)
    handler.path = '/api/mo/backtest/tested?id=' + identifier
    replies = []
    handler.send = lambda status, body: replies.append((status, body))
    with patch.dict(unified_backtest.JOBS, {identifier: {'folder': tmp_path / identifier, 'scenario': {}}}):
        handler.headers = {'X-Lab-Token': 'test-token'}
        handler.do_GET()
        assert replies[-1] == (200, unified_backtest.tested(identifier))
    handler.path = '/api/mo/backtest/tested?id=../x'
    handler.do_GET()
    assert replies[-1][0] == 400
