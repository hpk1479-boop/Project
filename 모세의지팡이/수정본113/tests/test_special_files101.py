"""Developer-maintained specials remain distinct from the test library."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import sys
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'Part1/program'), str(ROOT / 'Part3'), str(ROOT / 'Part2')]
from strategy_recipe import registry, special_files, user_catalog
from lab import storage


@pytest.fixture
def project(tmp_path, monkeypatch):
    for part in ('Part1/program', 'Part2', 'Part3', 'settings'):
        (tmp_path / part).mkdir(parents=True)
    target = tmp_path / 'settings/strategy_registry.json'
    target.write_bytes(registry.REGISTRY.read_bytes())
    monkeypatch.setattr(registry, 'REGISTRY', target)
    monkeypatch.setattr(storage, 'ROOT', tmp_path / 'Part3')
    return tmp_path


def add_original_specials(project):
    folder = project / special_files.SPECIAL_DIRECTORY
    folder.mkdir(parents=True)
    original = json.loads(registry.REGISTRY.read_text('utf-8-sig'))['presets']
    for entry in original:
        user_catalog.write_json(folder / (entry['id'] + '.recipe.json'), entry)
    return folder, original


def test_existing_special_folder_is_authoritative_for_seven_four_and_zero(project):
    assert len(registry.builtin_entries()) == 7
    folder, original = add_original_specials(project)
    assert registry.builtin_entries() == {row['id']: row for row in original}
    for row in original[4:]:
        (folder / (row['id'] + '.recipe.json')).unlink()
    assert tuple(registry.builtin_entries()) == tuple(row['id'] for row in original[:4])
    for row in original[:4]:
        (folder / (row['id'] + '.recipe.json')).unlink()
    assert special_files.read_special_entries(project) == {}
    assert registry.builtin_entries() == {}
    assert registry.list_presets() == ()
    # The historical registry still has seven: empty means an intentional empty release.
    assert len(json.loads(registry.REGISTRY.read_text('utf-8-sig'))['presets']) == 7


def test_config_symbols_are_validated_without_mutating_saved_definitions(project):
    folder, original = add_original_specials(project)
    before = {p.name: p.read_bytes() for p in folder.iterdir()}
    rows = registry.builtin_entries()
    for row in original:
        assert rows[row['id']] == row
        if row['symbol_source'] == 'CONFIG':
            assert rows[row['id']]['recipe']['strategy_intent']['symbols'] == []
    assert before == {p.name: p.read_bytes() for p in folder.iterdir()}
    plugins = registry.load_plugins({'SYMBOLS': 'XAUUSD+'})
    assert set(plugins) == set(rows)
    assert all(module.recipe['strategy_intent']['symbols'] == ['XAUUSD+'] for module in plugins.values())


def test_manual_copy_becomes_builtin_and_source_callbacks_are_not_executed(project):
    folder, original = add_original_specials(project)
    recipe = copy.deepcopy(original[0]['recipe'])
    recipe['name'] = '직접 추가한 스페셜'
    recipe['strategy_intent']['symbols'] = ['XAUUSD+']
    recipe['symbols'] = ['XAUUSD+']
    created = storage.generate(recipe)
    identifier = created['strategy_id']
    assert identifier not in registry.list_presets()
    marker = project / 'unsafe_callback_was_run.txt'
    source = Path(created['path']).read_text('utf-8')
    source = 'raise RuntimeError("source must never execute")\n' + source
    source += '\nopen(' + repr(str(marker)) + ', "w").write("unsafe")\n'
    source += 'def dangerous_callback():\n    raise RuntimeError("callback must never execute")\n'
    target = folder / 'SPECIAL8.py'
    target.write_text(source, encoding='utf-8')
    rows = registry.builtin_entries()
    assert rows['SPECIAL8']['name'] == '직접 추가한 스페셜'
    assert rows['SPECIAL8']['recipe'] == recipe
    assert rows['SPECIAL8']['recipe']['strategy_intent'] == recipe['strategy_intent']
    assert identifier not in registry.list_presets()
    assert 'SPECIAL8' in registry.list_presets('Part1') and 'SPECIAL8' in registry.list_presets('Part2')
    assert not marker.exists()
    plugins = registry.load_plugins({'SYMBOLS': 'XAUUSD+'}, selected=['SPECIAL8'])
    assert list(plugins) == ['SPECIAL8'] and callable(plugins['SPECIAL8'].register)
    assert not marker.exists()


def test_special_file_cache_reflects_changed_literal_data(project):
    folder, original = add_original_specials(project)
    name = original[0]['id']
    path = folder / (name + '.recipe.json')
    assert registry.builtin_entries()[name]['name'] == original[0]['name']
    changed = copy.deepcopy(original[0])
    changed['name'] = '수정된 이름'
    user_catalog.write_json(path, changed)
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))
    assert registry.builtin_entries()[name]['name'] == '수정된 이름'
    first = registry.builtin_entries()
    first[name]['recipe']['strategy_intent']['symbols'].append('MUTATED')
    assert registry.builtin_entries()[name]['recipe'] == changed['recipe']


def test_duplicate_special_file_definitions_are_rejected(project):
    folder, original = add_original_specials(project)
    recipe = copy.deepcopy(original[0]['recipe'])
    source = 'PART3_RECIPE = ' + repr(recipe) + '\n'
    (folder / (original[0]['id'] + '.py')).write_text(source, encoding='utf-8')
    with pytest.raises(ValueError, match='중복'):
        registry.builtin_entries()


def test_nonliteral_recipe_is_never_evaluated(project):
    folder, original = add_original_specials(project)
    marker = project / 'executed.txt'
    (folder / 'SPECIAL8.py').write_text('PART3_RECIPE = open(' + repr(str(marker)) + ', "w")\n', encoding='utf-8')
    with pytest.raises(ValueError, match='literal'):
        registry.builtin_entries()
    assert not marker.exists()


def test_unpromoted_part3_file_uses_process_local_backtest_loader(project):
    import event_application
    from event_selection import SPECIAL_DEPENDENCIES
    original = registry.builtin_entries()
    recipe = copy.deepcopy(next(iter(original.values()))['recipe'])
    recipe['name'] = '미승급 백테스트'
    recipe['strategy_intent']['symbols'] = ['XAUUSD+']
    recipe['symbols'] = ['XAUUSD+']
    created = storage.generate(recipe)
    assert created['strategy_id'] not in registry.list_presets('Part2')
    spec = importlib.util.spec_from_file_location('_part3_backtest101_probe', ROOT / 'Part3/backtest.py')
    entry = importlib.util.module_from_spec(spec)
    with patch.dict(os.environ), patch.dict(event_application._strategy_loaders), patch.dict(SPECIAL_DEPENDENCIES):
        spec.loader.exec_module(entry)
        key = entry.initialize({'project_root': str(project), 'strategy': created['path'],
                                'job_dir': str(project / 'probe')})
        try:
            engine = event_application.create_event_engine(
                {'WONBI_SIGMA': '3', 'TELEGRAM_TOKEN': '', 'TELEGRAM_CHAT_ID': 'TEST',
                 'SYMBOLS': 'XAUUSD+', 'STAFF_ALLOWED_SYMBOLS': 'XAUUSD+', 'LONDON': '1600-0100'},
                symbols=('XAUUSD+',), selection=[key], backtest=True, oz_evaluation='all')
            assert engine.selection.specials == (key,)
            assert not engine.error_log
            assert created['strategy_id'] not in registry.list_presets('Part2')
        finally:
            event_application.unregister_strategy_loader(key)
