"""167: a strategy of the installed 스페셜/내 전략 folder starts with its live alerts off.

It shows unchecked in the live strategy settings and runs live only once the saved settings turn it on.
The shipped strategies (스페셜/기본) keep starting on, a development project (no 스페셜 folder) is
unchanged, and backtests list every strategy as before.
"""
import copy
import importlib.util
import json
import socket
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'Part1/program'), str(ROOT / 'Part3'), str(ROOT / 'Part2')]
from strategy_recipe import registry, special_files, user_catalog

SHIPPED = special_files.read_special_entries(ROOT)
CONFIG = {'SYMBOLS': 'XAUUSD+', 'STAFF_ALLOWED_SYMBOLS': 'XAUUSD+', 'TARGET_SYMBOLS': 'XAUUSD+',
          'TELEGRAM_TOKEN': '', 'TELEGRAM_CHAT_ID': 'TEST', 'WONBI_SIGMA': '3', 'LONDON': '1600-0100'}


def entry(number, like='SPECIAL7'):
    value = copy.deepcopy(SHIPPED[like])
    value['id'] = 'SPECIAL' + str(number)
    value['name'] = value['recipe']['name'] = '전략 ' + str(number)
    return value


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')


def project(root, folders):
    for part in ('Part1/program', 'Part2', 'Part3', 'settings'):
        (root / part).mkdir(parents=True, exist_ok=True)
    (root / 'settings/strategy_registry.json').write_bytes(registry.REGISTRY.read_bytes())
    (root / 'Part1/program/config.txt').write_text('\n'.join(k + '=' + v for k, v in CONFIG.items()), encoding='utf-8')
    for folder, numbers in folders.items():
        for number in numbers:
            write(root / folder / f'SPECIAL{number}.recipe.json', entry(number))
    return root


@pytest.fixture
def installed(tmp_path, monkeypatch):
    def deny(*args, **kwargs):
        raise AssertionError('No live transport may be started by this test')
    for method in ('connect', 'connect_ex', 'sendto'):
        monkeypatch.setattr(socket.socket, method, deny)
    root = project(tmp_path / 'MOSES', {'스페셜/기본': (1, 2), '스페셜/내 전략': (100,)})
    monkeypatch.setattr(registry, 'REGISTRY', root / 'settings/strategy_registry.json')
    return root


def settings(root, rows):
    user_catalog.write_json(root / 'Part1/special_settings.json', {'version': 1, 'specials': {
        name: {'enabled': on, 'trigger': None, 'time_filters': None} for name, on in rows.items()}})


def test_only_the_installed_folder_holds_user_strategies(installed, tmp_path, monkeypatch):
    assert registry.user_strategies() == {'SPECIAL100'}
    development = project(tmp_path / '수정본', {special_files.SPECIAL_DIRECTORY: (1, 100)})
    monkeypatch.setattr(registry, 'REGISTRY', development / 'settings/strategy_registry.json')
    assert registry.user_strategies() == set()


def test_engine_start_leaves_a_new_user_strategy_off_until_it_is_turned_on(installed):
    from event_application import load_modules
    from event_startup import load_startup
    inputs = load_startup(installed / 'Part1/program', load_modules())
    assert set(inputs.enabled_specials) == {'SPECIAL1', 'SPECIAL2'}
    settings(installed, {'SPECIAL100': True, 'SPECIAL2': False})
    inputs = load_startup(installed / 'Part1/program', load_modules())
    assert set(inputs.enabled_specials) == {'SPECIAL1', 'SPECIAL100'}


def test_development_project_starts_every_strategy_as_before(tmp_path, monkeypatch):
    from event_application import load_modules
    from event_startup import load_startup
    development = project(tmp_path / '수정본', {special_files.SPECIAL_DIRECTORY: (1, 100)})
    monkeypatch.setattr(registry, 'REGISTRY', development / 'settings/strategy_registry.json')
    assert set(load_startup(development / 'Part1/program', load_modules()).enabled_specials) == {'SPECIAL1', 'SPECIAL100'}


def test_strategy_settings_screen_shows_it_unchecked(installed, monkeypatch):
    from lab import unified_live
    owner = {'load_special_settings': lambda: None, 'load_oz_profiles': lambda: None,
             'special_code_default_trigger': lambda name: None}
    monkeypatch.setattr(unified_live, 'control', lambda: owner)
    items = unified_live.specials()['items']
    assert {name: row['enabled'] for name, row in items.items()} == {
        'SPECIAL1': True, 'SPECIAL2': True, 'SPECIAL100': False}
    owner['load_special_settings'] = lambda: {'SPECIAL100': {'enabled': True}}
    assert unified_live.specials()['items']['SPECIAL100']['enabled'] is True


@pytest.fixture
def control(installed, monkeypatch):
    spec = importlib.util.spec_from_file_location('live_control167', ROOT / 'Part1/live_control.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, 'SPECIAL_SETTINGS_PATH', installed / 'Part1/special_settings.json')
    monkeypatch.setattr(module, 'start_program', Mock(return_value=module.LiveStartResult(True, 'started')))
    monkeypatch.setattr(module, 'find_program_process_ids', lambda name: [])
    monkeypatch.setattr(module.subprocess, 'Popen', Mock(side_effect=AssertionError('real engine start forbidden')))
    return module


def test_live_start_and_status_follow_the_same_rule(control, installed):
    settings(installed, {'SPECIAL1': True})
    control.start_live()
    assert control.start_program.call_args.kwargs['enabled_specials'] == {'SPECIAL1', 'SPECIAL2'}
    assert control.live_status()['enabled_specials'] == ['SPECIAL1', 'SPECIAL2']
    settings(installed, {'SPECIAL100': True})
    control.start_live()
    assert control.start_program.call_args.kwargs['enabled_specials'] == {'SPECIAL1', 'SPECIAL2', 'SPECIAL100'}
    assert control.live_status()['enabled_specials'] == ['SPECIAL1', 'SPECIAL2', 'SPECIAL100']


def test_live_status_without_a_settings_file(control):
    assert control.live_status()['enabled_specials'] == ['SPECIAL1', 'SPECIAL2']


def test_promotion_creating_the_settings_file_keeps_user_strategies_off(installed, monkeypatch):
    from lab import storage
    monkeypatch.setattr(storage, 'ROOT', installed / 'Part3')
    recipe = copy.deepcopy(SHIPPED['SPECIAL7']['recipe'])
    recipe['name'] = '승급 검사'
    recipe['strategy_intent']['symbols'] = recipe['symbols'] = ['XAUUSD+']
    identifier = storage.generate(recipe)['strategy_id']
    user_catalog.promote_selected([identifier], registry.builtin_entries(), installed)
    saved = json.loads((installed / 'Part1/special_settings.json').read_text('utf-8'))['specials']
    assert {name: row['enabled'] for name, row in saved.items()} == {
        'SPECIAL1': True, 'SPECIAL2': True, 'SPECIAL100': False, identifier: False}


def test_backtests_still_list_every_strategy(installed):
    assert registry.list_presets('Part2') == ('SPECIAL1', 'SPECIAL2', 'SPECIAL100')
