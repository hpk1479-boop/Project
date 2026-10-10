"""Actual host startup/factory checks for independent Part1/Part2 registration."""
import copy
import json
from pathlib import Path
import socket
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'Part1/program'), str(ROOT / 'Part3'), str(ROOT / 'Part2')]
from strategy_recipe import registry, user_catalog
from lab import storage
from event_application import create_event_engine, load_modules
from event_startup import load_startup


CONFIG = {'SYMBOLS': 'XAUUSD+', 'STAFF_ALLOWED_SYMBOLS': 'XAUUSD+',
          'TARGET_SYMBOLS': 'XAUUSD+', 'TELEGRAM_TOKEN': '', 'TELEGRAM_CHAT_ID': 'TEST',
          'WONBI_SIGMA': '3', 'LONDON': '1600-0100'}


@pytest.fixture
def promoted(tmp_path, monkeypatch):
    def deny(*args, **kwargs):
        raise AssertionError('No live transport may be started by a factory test')
    for method in ('connect', 'connect_ex', 'sendto'):
        monkeypatch.setattr(socket.socket, method, deny)
    for part in ('Part1/program', 'Part2', 'Part3', 'settings'):
        (tmp_path / part).mkdir(parents=True)
    target = tmp_path / 'settings/strategy_registry.json'
    target.write_bytes(registry.REGISTRY.read_bytes())
    monkeypatch.setattr(registry, 'REGISTRY', target)
    monkeypatch.setattr(storage, 'ROOT', tmp_path / 'Part3')
    recipe = copy.deepcopy(next(iter(registry.builtin_entries().values()))['recipe'])
    recipe['name'] = '런타임 등록 검사'
    recipe['strategy_intent']['symbols'] = ['XAUUSD+']
    recipe['symbols'] = ['XAUUSD+']
    result = storage.generate(recipe)
    identifier = result['strategy_id']
    user_catalog.promote_selected([identifier], registry.builtin_entries(), tmp_path)
    program = tmp_path / 'Part1/program'
    (program / 'config.txt').write_text('\n'.join(key + '=' + value for key, value in CONFIG.items()), encoding='utf-8')
    return tmp_path, identifier, result


@pytest.mark.parametrize('excluded,blocked_backtest,allowed_backtest', [
    ('Part1', False, True), ('Part2', True, False)])
def test_actual_factory_refuses_excluded_host_and_keeps_other_host(promoted, excluded, blocked_backtest, allowed_backtest):
    root, identifier, result = promoted
    user_catalog.delete_selected([identifier], registry.builtin_entries(), root, part=excluded)
    with pytest.raises(ValueError, match='unknown selected strategies'):
        create_event_engine(dict(CONFIG), selection=[identifier], symbols=('XAUUSD+',),
                            backtest=blocked_backtest, oz_evaluation='all')
    engine = create_event_engine(dict(CONFIG), selection=[identifier], symbols=('XAUUSD+',),
                                 backtest=allowed_backtest, oz_evaluation='all')
    assert engine.selection.specials == (identifier,)
    assert not engine.error_log
    assert Path(result['path']).exists()


def test_actual_live_startup_keeps_freshly_promoted_strategy_disabled(promoted):
    root, identifier, _ = promoted
    inputs = load_startup(root / 'Part1/program', load_modules())
    assert identifier in registry.list_presets('Part1')
    assert identifier not in inputs.enabled_specials
    assert set(inputs.enabled_specials) == set(registry.builtin_entries())
    assert json.loads((root / 'Part1/special_settings.json').read_text('utf-8'))['specials'][identifier]['enabled'] is False


def test_missing_live_settings_does_not_enable_promoted_strategy(promoted):
    root, identifier, _ = promoted
    (root / 'Part1/special_settings.json').unlink()
    inputs = load_startup(root / 'Part1/program', load_modules())
    assert identifier not in inputs.enabled_specials
    assert set(inputs.enabled_specials) == set(registry.builtin_entries())


def test_startup_ignores_stale_saved_enabled_and_trigger_for_excluded_strategy(promoted):
    root, identifier, _ = promoted
    user_catalog.delete_selected([identifier], registry.builtin_entries(), root, part='Part1')
    path = root / 'Part1/special_settings.json'
    saved = json.loads(path.read_text('utf-8'))
    saved['specials'][identifier] = {'enabled': True, 'trigger': '무지성 올존', 'time_filters': None}
    user_catalog.write_json(path, saved)
    inputs = load_startup(root / 'Part1/program', load_modules())
    assert identifier not in inputs.enabled_specials
    assert identifier not in inputs.triggers
    assert identifier in registry.list_presets('Part2')


def test_part1_diagnostics_modules_follow_only_part1_registration(promoted):
    import module_diagnostics
    import module_status_state
    root, identifier, _ = promoted
    assert identifier in module_diagnostics.module_names()
    assert identifier in module_status_state.preset_modules()
    user_catalog.delete_selected([identifier], registry.builtin_entries(), root, part='Part1')
    assert identifier not in module_diagnostics.module_names()
    assert identifier not in module_status_state.preset_modules()
    assert identifier in registry.list_presets('Part2')


def test_repromotion_restores_actual_factory_acceptance_in_both_hosts(promoted):
    root, identifier, _ = promoted
    for part in ('Part1', 'Part2'):
        user_catalog.delete_selected([identifier], registry.builtin_entries(), root, part=part)
    for mode in (False, True):
        with pytest.raises(ValueError, match='unknown selected strategies'):
            create_event_engine(dict(CONFIG), selection=[identifier], backtest=mode)
    user_catalog.promote_selected([identifier], registry.builtin_entries(), root)
    for mode in (False, True):
        engine = create_event_engine(dict(CONFIG), selection=[identifier], symbols=('XAUUSD+',),
                                     backtest=mode, oz_evaluation='all')
        assert engine.selection.specials == (identifier,)
        assert not engine.error_log
    assert identifier not in load_startup(root / 'Part1/program', load_modules()).enabled_specials
