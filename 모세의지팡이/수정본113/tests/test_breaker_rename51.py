"""BREAKER-only OZ contract; retired names appear solely as rejected test inputs."""
from __future__ import annotations

import ast
import copy
import hashlib
import json
import re
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from test_oz_profiles48 import CONFIG, prohibit_network
from test_oz_rewrite import view, edit, profile, seed
from test_ma_strategy_chain49 import CASES, chain, trigger
import oz_profiles
from oz_profile_loader import LegacyUnsupportedProfile, load_saved_oz
from oz_engine.runtime import OZRuntime
from event_application import load_strategy_inputs
from durable_protocol import identity
from lab import catalog, storage
from lab.compiler import compile_recipe, prepare_manual

ROOT = Path(__file__).resolve().parents[1]
RETIRED = ('DIVERGENCE', 'divergence', 'BLIND_DIVERGENCE', 'DIVERGENCE_BLIND')
RETIRED_TEXT = (*RETIRED, '다이버전스 올존', '무지성 다이버전스 올존')


@pytest.mark.parametrize('name', RETIRED)
@pytest.mark.parametrize('field', ('trigger_mode', 'oz_mode', 'trigger_type', 'special5_source_trigger_mode'))
def test_loader_never_accepts_retired_watch_names_even_with_current_profile(name, field):
    value = {'watch_id': 'watch51', 'validation_mode': 'BLIND', 'trigger_mode': 'OZ', field: name}
    before = copy.deepcopy(value)
    with pytest.raises(LegacyUnsupportedProfile) as caught:
        load_saved_oz(value, path='watch51')
    assert value == before
    assert name.upper() in str(caught.value).upper() and 'watch51' in str(caught.value)


@pytest.mark.parametrize('text', RETIRED_TEXT)
@pytest.mark.parametrize('kind', ('text', 'specials', 'scenario'))
def test_all_text_setting_boundaries_reject_without_alias_or_mutation(text, kind):
    value = text if kind == 'text' else (
        {'SPECIAL7': {'enabled': True, 'trigger': text}} if kind == 'specials'
        else {'triggers': {'SPECIAL7': text}})
    before = copy.deepcopy(value)
    if kind == 'specials':
        result = load_saved_oz(value, kind=kind, path='settings51')
        row = result['SPECIAL7']
        assert row['enabled'] is False and row['original'] == value['SPECIAL7']
        assert 'trigger' not in row and text in row['load_error']
        with pytest.raises(oz_profiles.ProfileError):
            oz_profiles.normalize_special_settings(result)
    else:
        with pytest.raises(LegacyUnsupportedProfile):
            load_saved_oz(value, kind=kind, path='settings51')
    assert value == before


@pytest.mark.parametrize('location', ('chain', 'trigger', 'cancel'))
@pytest.mark.parametrize('expression,step,direction,window', CASES)
def test_retired_nested_timed_chain_profile_is_not_replaced(location, expression, step, direction, window):
    opposite = expression.replace('골든크로스', '데드크로스') if '골든크로스' in expression else expression.replace('데드크로스', '골든크로스')
    raw = chain(trigger(expression), invalidation_triggers=(trigger(opposite),)).to_json()
    selected = raw if location == 'chain' else raw['triggers' if location == 'trigger' else 'invalidation_triggers'][0]
    selected['trigger_mode'] = 'DIVERGENCE'
    before = copy.deepcopy(raw)
    with pytest.raises(LegacyUnsupportedProfile):
        load_saved_oz(raw, path='chain51')
    assert raw == before


@pytest.mark.parametrize('vm', ('NORMAL', 'BLIND'))
@pytest.mark.parametrize('with_symbol', (True, False))
def test_retired_checkpoint_is_quarantined_not_renamed_or_restored(vm, with_symbol):
    import monitor_OZ
    v = view(); p = profile(vm, 'BREAKER'); seed(p, v)
    old_name = 'oz_observed_' + identity('XAUUSD+', vm, 'DIVERGENCE')[:24] + '.json'
    payload = {'version': 1, 'profile': [vm, 'DIVERGENCE'], 'observed': p.export_event_state()}
    if with_symbol:
        payload['symbol'] = 'XAUUSD+'
    original = json.dumps(payload)
    runtime = OZRuntime(monitor_OZ, CONFIG, {old_name: original})
    restored = runtime._profile('XAUUSD+', vm, 'BREAKER')
    assert all(candidate is None for candidate in restored.candidates.values())
    files = runtime.export_files()
    assert old_name not in files
    assert 'oz_profile_checkpoint_migrations.json' not in files
    rejection = json.loads(files['oz_rejected_profile_checkpoints.json'])[old_name]
    assert rejection['original'] == original and 'DIVERGENCE' in rejection['reason']
    assert json.loads(files[restored.checkpoint_name])['profile'] == [vm, 'BREAKER']
    assert all(candidate is None for candidate in copy.deepcopy(runtime)._profile('XAUUSD+', vm, 'BREAKER').candidates.values())


@pytest.mark.parametrize('vm', ('NORMAL', 'BLIND'))
def test_no_retired_hash_fallback_for_checkpoint_without_profile(vm):
    import monitor_OZ
    v = view(); p = profile(vm, 'BREAKER'); seed(p, v)
    old_name = 'oz_observed_' + identity('XAUUSD+', vm, 'DIVERGENCE')[:24] + '.json'
    payload = {'version': 1, 'observed': p.export_event_state()}
    runtime = OZRuntime(monitor_OZ, CONFIG, {old_name: json.dumps(payload)})
    assert all(candidate is None for candidate in runtime._profile('XAUUSD+', vm, 'BREAKER').candidates.values())


@pytest.mark.parametrize('direction,field,sign', [('LONG', 'low', -1), ('SHORT', 'high', 1)])
@pytest.mark.parametrize('previous,current,cross_offset,expected', [
    (0., 0., -60, False),          # Equality is not a strict break.
    (0., .000001, -60, True),     # Equality -> strictly beyond TRUE B0.
    (-1., .01, -60, True),
    (-1., -.000001, -60, False),  # Near without crossing is not accepted.
    (.01, .02, -60, False),       # Already beyond is not a new cross.
    (.01, 0., -60, False),
    (0., .01, 0, False),          # The HMA-cross bar itself is ineligible.
    (0., .01, 60, False),
    (np.nan, .01, -60, False),
    (0., np.nan, -60, False),
])
def test_strict_true_b0_boundary_is_identical_in_dataframe_and_array_paths(direction, field, sign, previous, current, cross_offset, expected):
    import monitor_OZ
    v = view(**{field: 100.})
    values = np.full(len(v), 100.)
    values[-2:] = 100. + sign * np.array([previous, current])
    v = edit(v, **{field: values})
    cross = int(v.time[-1]) + cross_offset
    frame = pd.DataFrame({'time': pd.to_datetime(v.time, unit='s'), field: values})
    array_result = v.bo_break(direction, 100., cross)
    dataframe_result = monitor_OZ.OZMonitor._breaker_bo_break_trigger(
        frame, direction, 100., pd.to_datetime(cross, unit='s'))
    assert bool(array_result) is expected and bool(dataframe_result) is expected


@pytest.mark.parametrize('number,expected', [(1, 'BREAKER'), (2, 'BREAKER'), (3, 'OZ'),
    (4, 'OZ'), (5, 'OZ'), (6, 'BREAKER'), (7, 'BREAKER')])
def test_special1_to_7_current_source_catalog_and_generated_names_match(number, expected):
    plugin = load_strategy_inputs({})[2][f'SPECIAL{number}']
    assert plugin.FINAL_TRIGGER_MODE == expected
    recipe = catalog.load(number)
    assert recipe['config']['FINAL_TRIGGER_MODE'] == expected
    code = compile_recipe(recipe, f'Test_SPECIAL{900+number:03}.py')
    assert not re.search('divergence|다이버전스', code, re.I)
    assert load_saved_oz(code, kind='source') == code
    label = oz_profiles.profile_label(plugin.FINAL_VALIDATION_MODE, expected)
    assert ('브레이커' in label) == (expected == 'BREAKER')
    if number == 5:
        assert (plugin.SOURCE_VALIDATION_MODE, plugin.SOURCE_TRIGGER_MODE) == ('NORMAL', 'BREAKER')
        assert not hasattr(plugin, '_migrate_source_children')
        assert recipe['slots'][0]['params'] == {'ema_filter': True}
    if number == 7:
        assert plugin.FINAL_VALIDATION_MODE == 'NORMAL'


@pytest.mark.parametrize('number', (8, 9, 10, 11, 13, 15))
def test_existing_generated_strategies_reopen_without_alias_or_hash_drift(number):
    name = f'Test_SPECIAL{number:03}.py'
    result = storage.reopen(name)
    assert not result['external_edit']
    assert result['code'] == (ROOT/'Part3/generated'/name).read_text('utf-8')
    assert not re.search('divergence|다이버전스', json.dumps(result, ensure_ascii=False), re.I)
    assert result['recipe']['config']['FINAL_TRIGGER_MODE'] in {'OZ', 'BREAKER'}


@pytest.mark.parametrize('number', (12, 14))
def test_unsupported_regime_generated_strategies_are_not_revived(number):
    with pytest.raises(LegacyUnsupportedProfile):
        storage.reopen(f'Test_SPECIAL{number:03}.py')


def test_special5_embedded_example_has_no_alias_migration_or_regime_substitute():
    raw = json.loads((ROOT/'Part3/examples/SPECIAL5.json').read_text('utf-8'))
    loaded = storage.load_saved_recipe(raw)
    assert loaded['config']['SOURCE_TRIGGER_MODE'] == 'BREAKER'
    assert '_migrate_source_children' not in loaded['source_text']
    assert 'SOURCE_BAND_ENABLED' not in loaded['config']
    assert loaded['slots'][0]['params'] == {'ema_filter': True}
    assert raw['source_sha256'] == hashlib.sha256(raw['source_text'].encode()).hexdigest()


@pytest.mark.parametrize('text', ('DIVERGENCE', 'BLIND_DIVERGENCE', '다이버전스 올존'))
def test_part1_and_part2_file_loads_exclude_old_setting_and_never_resave_default(tmp_path, monkeypatch, text):
    from lab.unified_live import control
    from event_backtest import ui_model
    functions = control(); load = functions['load_special_settings']
    part1 = tmp_path/'special_settings.json'
    value = {'SPECIAL7': {'enabled': True, 'trigger': text},
             'SPECIAL2': {'enabled': True, 'trigger': '브레이커 올존'}}
    part1.write_text(json.dumps({'version': 1, 'specials': value}), 'utf-8')
    before1 = part1.read_bytes()
    monkeypatch.setitem(load.__globals__, 'SPECIAL_SETTINGS_PATH', part1)
    loaded1 = load()
    assert not loaded1['SPECIAL7']['enabled'] and 'trigger' not in loaded1['SPECIAL7']
    assert loaded1['SPECIAL2']['trigger'] == '브레이커 올존'
    with pytest.raises(ValueError): functions['save_special_settings'](loaded1)
    assert part1.read_bytes() == before1
    part2 = tmp_path/'ui.json'
    part2.write_text(json.dumps({'target_mode': 'SPECIAL', 'specials': value, 'watch': {'text': ''}}), 'utf-8')
    before2 = part2.read_bytes(); loaded2 = ui_model.load(part2)
    assert not loaded2['specials']['SPECIAL7']['enabled'] and 'trigger' not in loaded2['specials']['SPECIAL7']
    assert loaded2['specials']['SPECIAL2']['trigger'] == '브레이커 올존'
    with pytest.raises(ValueError): ui_model.save(loaded2, part2)
    assert part2.read_bytes() == before2


@pytest.mark.parametrize('value', RETIRED)
def test_part3_recipe_source_and_draft_never_rewrite_old_names(tmp_path, monkeypatch, value):
    monkeypatch.setattr(storage, 'ROOT', tmp_path)
    recipe = catalog.new(); recipe['config']['FINAL_TRIGGER_MODE'] = value
    source = f"FINAL_TRIGGER_MODE={value!r}\ndef register(manager): pass\n"
    before = copy.deepcopy(recipe)
    with pytest.raises(LegacyUnsupportedProfile): storage.load_saved_recipe(recipe)
    with pytest.raises(LegacyUnsupportedProfile): load_saved_oz(source, kind='source')
    with pytest.raises(ValueError): prepare_manual(source, 'Test_SPECIAL999.py')
    with pytest.raises(ValueError): storage.save_project(recipe)
    with pytest.raises(ValueError): storage.save_draft({'recipe': recipe, 'manual': True, 'manual_code': source})
    assert recipe == before and not (tmp_path/'projects').exists()


@pytest.mark.parametrize('part', ('Part1', 'Part2', 'Part3'))
def test_all_non_test_python_and_executable_recipe_sources_have_no_old_oz_name(part):
    paths = []
    for path in (ROOT/part).rglob('*'):
        if not path.is_file() or path.suffix not in {'.py', '.pyw', '.js', '.html', '.mq5', '.mqh'}:
            continue
        if path.name.startswith('test_') or 'tests' in path.parts or '__pycache__' in path.parts or any(part.startswith('.venv') for part in path.parts):
            continue
        paths.append(path)
    if part == 'Part3':
        for folder in ('generated', 'projects', 'examples'):
            paths.extend((ROOT/part/folder).rglob('*.json'))
    assert paths
    for path in paths:
        assert not re.search('divergence|다이버전스|다이버젼스', path.read_text('utf-8-sig'), re.I), str(path.relative_to(ROOT))


@pytest.mark.parametrize('watch_id', ('USER_REGIME_TOOL', 'MY_REGIME_METRIC:USER_SUPERMAN'))
def test_unrelated_regime_metric_ids_are_not_reclassified(watch_id):
    row = {'watch_id': watch_id, 'trigger_mode': 'BREAKER', 'metrics': {'regime_band': 1., 'regime_slope': .2}}
    loaded = load_saved_oz(row)
    assert loaded['watch_id'] == watch_id and loaded['metrics'] == row['metrics']


@pytest.mark.parametrize('watch_id', ('OZARM:SP5:HIGH_DIVERGENCE_REGIME:5m', 'OZARM:SP5:FINAL:5m:DIVERGENCE_REGIME:old'))
def test_retired_special5_source_ids_remain_excluded_without_replacement(watch_id):
    row = {'watch_id': watch_id, 'trigger_mode': 'OZ'}
    before = copy.deepcopy(row)
    with pytest.raises(LegacyUnsupportedProfile): load_saved_oz(row)
    assert row == before
