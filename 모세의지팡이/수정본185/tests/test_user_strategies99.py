"""Real generation, explicit host promotion, and permanent library deletion."""
import copy
import json
from pathlib import Path
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'Part1/program'), str(ROOT / 'Part3'), str(ROOT / 'Part2')]
from strategy_recipe import registry, user_catalog
from lab import storage


def fixture(tmp_path, monkeypatch):
    (tmp_path / 'settings').mkdir()
    (tmp_path / 'Part3').mkdir()
    (tmp_path / 'Part1').mkdir()
    (tmp_path / 'Part2').mkdir()
    target = tmp_path / 'settings/strategy_registry.json'
    target.write_bytes(registry.REGISTRY.read_bytes())
    monkeypatch.setattr(registry, 'REGISTRY', target)
    monkeypatch.setattr(storage, 'ROOT', tmp_path / 'Part3')
    recipe = copy.deepcopy(next(iter(registry.builtin_entries().values()))['recipe'])
    recipe['name'] = '사용자 전략 99'
    recipe['strategy_intent']['symbols'] = ['XAUUSD+']
    recipe['symbols'] = ['XAUUSD+']
    return recipe


def test_generation_and_promotion_share_the_recipe_in_live_and_part2(tmp_path, monkeypatch):
    recipe = fixture(tmp_path, monkeypatch)
    result = storage.generate(recipe)
    name = result['strategy_id']
    assert name not in registry.list_presets()
    assert not (tmp_path / 'Part1/special_settings.json').exists()
    user_catalog.promote_selected([name], registry.builtin_entries(), tmp_path)
    assert name in registry.list_presets('Part1') and name in registry.list_presets('Part2')
    assert registry.preset_entry(name)['recipe'] == recipe
    from event_selection import resolve, strategy_dependencies
    assert name in strategy_dependencies()
    # These exact same startup functions are used by the live and replay hosts.
    plugins = registry.load_plugins({'SYMBOLS': 'XAUUSD+'}, selected=[name])
    assert name in plugins and callable(plugins[name].register)
    assert resolve([name], {}).specials == (name,)
    from event_backtest import ui_model
    assert name in ui_model.load(tmp_path / 'Part2/backtest_ui.json')['specials']
    saved = json.loads((tmp_path / 'Part1/special_settings.json').read_text('utf-8'))
    assert saved['specials'][name]['enabled'] is False


def test_delete_reset_does_not_restore_added_strategy(tmp_path, monkeypatch):
    result = storage.generate(fixture(tmp_path, monkeypatch))
    added = result['strategy_id']
    before = registry.builtin_entries()
    user_catalog.promote_selected([added], before, tmp_path)
    builtin = next(iter(before))
    user_catalog.delete_selected([builtin, added], before, tmp_path)
    assert builtin not in registry.list_presets() and added not in registry.list_presets()
    assert Path(result['path']).exists()
    assert Path(result['path']).with_suffix('.recipe.json').exists()
    user_catalog.reset_builtins(tmp_path)
    assert builtin in registry.list_presets() and added not in registry.list_presets()
    assert registry.builtin_entries() == before
    assert set(json.loads((tmp_path / 'Part1/special_settings.json').read_text())['specials']) <= set(before)
    user_catalog.delete_library([added], tmp_path)
    assert not Path(result['path']).exists()
    assert not Path(result['path']).with_suffix('.recipe.json').exists()


def test_new_release_replaces_builtin_count(tmp_path, monkeypatch):
    fixture(tmp_path, monkeypatch)
    original = registry.builtin_entries()
    assert len(original) == 7
    target = registry.REGISTRY
    data = json.loads(target.read_text('utf-8-sig'))
    data['presets'] = data['presets'][:4]
    user_catalog.write_json(target, data)
    user_catalog.reset_builtins(tmp_path)
    assert len(registry.list_presets()) == 4


def test_altered_generated_code_excluded_and_reported(tmp_path, monkeypatch):
    result = storage.generate(fixture(tmp_path, monkeypatch))
    Path(result['path']).write_text('# altered', encoding='utf-8')
    assert result['strategy_id'] not in registry.list_presets()
    assert user_catalog.generated_errors(tmp_path)


def test_unsafe_delete_never_touches_other_files(tmp_path, monkeypatch):
    fixture(tmp_path, monkeypatch)
    sentinel = tmp_path / 'sentinel.txt'
    sentinel.write_text('keep')
    import pytest
    with pytest.raises(ValueError):
        user_catalog.delete_selected(['../sentinel'], registry.builtin_entries(), tmp_path)
    assert sentinel.read_text() == 'keep'
