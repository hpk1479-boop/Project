"""166: an installed MOSES reads SPECIAL files from 스페셜/기본 (1~99) and 스페셜/내 전략 (100~999).

Without a 스페셜 folder (the development project) Part1/program/SPECIAL is read as before.
A file whose number does not belong to its folder is left out with a one-line notice.
Live (Part1) and the backtest (Part2) use the same definitions.
"""
import copy
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys
from unittest.mock import Mock

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'Part1/program'), str(ROOT / 'Part3'), str(ROOT / 'Part2')]
from strategy_recipe import registry, special_files

SHIPPED = special_files.read_special_entries(ROOT)


def entry(number, like='SPECIAL7'):
    value = copy.deepcopy(SHIPPED[like])
    value['id'] = 'SPECIAL' + str(number)
    value['name'] = value['recipe']['name'] = '전략 ' + str(number)
    return value


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2)
    path.write_text(text, encoding='utf-8')
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))


def project(root, basic=(), mine=(), development=()):
    for part in ('Part1/program', 'Part2', 'Part3', 'settings'):
        (root / part).mkdir(parents=True, exist_ok=True)
    (root / 'settings/strategy_registry.json').write_bytes(registry.REGISTRY.read_bytes())
    for folder, numbers in (('스페셜/기본', basic), ('스페셜/내 전략', mine),
                            (special_files.SPECIAL_DIRECTORY, development)):
        for number in numbers:
            write(root / folder / f'SPECIAL{number}.recipe.json', entry(number))
    return root


@pytest.fixture
def installed(tmp_path, monkeypatch):
    root = project(tmp_path / 'MOSES', basic=(1, 99), mine=(100, 999))
    (root / '스페셜/기본').mkdir(parents=True, exist_ok=True)
    (root / '스페셜/내 전략').mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(registry, 'REGISTRY', root / 'settings/strategy_registry.json')
    return root


def test_both_folders_load_with_their_own_numbers(installed):
    entries = registry.builtin_entries()
    assert entries == {name: entry(int(name[7:])) for name in ('SPECIAL1', 'SPECIAL99', 'SPECIAL100', 'SPECIAL999')}
    assert registry.builtin_sources() == {'SPECIAL1': '스페셜/기본', 'SPECIAL99': '스페셜/기본',
                                          'SPECIAL100': '스페셜/내 전략', 'SPECIAL999': '스페셜/내 전략'}
    assert registry.skipped_builtins() == []
    # The shipped strategies come first, then the user's own; every part lists the same ones.
    assert registry.list_presets('Part1') == registry.list_presets('Part2') == (
        'SPECIAL1', 'SPECIAL99', 'SPECIAL100', 'SPECIAL999')
    assert set(registry.load_plugins({'SYMBOLS': 'XAUUSD+'})) == set(entries)


def test_live_and_backtest_use_the_same_definition_of_a_users_strategy(installed):
    from event_backtest.base_frames import recipe_entry
    plugin = registry.load_plugins({'SYMBOLS': 'XAUUSD+'}, selected={'SPECIAL100'})['SPECIAL100']
    assert recipe_entry('SPECIAL100') == registry.preset_entry('SPECIAL100') == entry(100)
    assert plugin.recipe['name'] == '전략 100'


@pytest.mark.parametrize('folder,number,notice', [
    ('기본', 100, '기본/SPECIAL100.recipe.json 제외 · 기본은 SPECIAL1~SPECIAL99만 씁니다'),
    ('내 전략', 99, '내 전략/SPECIAL99.recipe.json 제외 · 내 전략은 SPECIAL100~SPECIAL999만 씁니다'),
    ('내 전략', 1, '내 전략/SPECIAL1.recipe.json 제외 · 내 전략은 SPECIAL100~SPECIAL999만 씁니다'),
    ('내 전략', 50, '내 전략/SPECIAL50.recipe.json 제외 · 내 전략은 SPECIAL100~SPECIAL999만 씁니다'),
])
def test_a_number_outside_its_folder_is_left_out_with_one_line(installed, folder, number, notice):
    before = registry.builtin_entries()
    other = copy.deepcopy(entry(number, like='SPECIAL2'))
    write(installed / '스페셜' / folder / f'SPECIAL{number}.recipe.json', other)
    # The file does not join, and a strategy of the same number in the other folder keeps its own definition.
    assert registry.builtin_entries() == before
    assert registry.skipped_builtins() == [{'file': folder + '/' + f'SPECIAL{number}.recipe.json',
                                            'id': f'SPECIAL{number}', 'notice': notice}]


def test_two_files_for_one_number_in_my_strategies_both_stay_out(installed):
    write(installed / '스페셜/내 전략/SPECIAL100.py', 'PART3_RECIPE = ' + repr(entry(100)['recipe']) + '\n')
    assert 'SPECIAL100' not in registry.builtin_entries()
    rows = registry.skipped_builtins()
    assert sorted(row['file'] for row in rows) == ['내 전략/SPECIAL100.py', '내 전략/SPECIAL100.recipe.json']
    assert all('같은 전략 번호 파일 중복' in row['notice'] for row in rows)


def test_a_python_strategy_in_my_strategies_is_read_without_running_it(installed):
    write(installed / '스페셜/내 전략/SPECIAL101.py', 'PART3_RECIPE = ' + repr(entry(101)['recipe']) +
          '\nraise RuntimeError("never executed")\n')
    loaded = registry.builtin_entries()['SPECIAL101']
    assert loaded['recipe'] == entry(101)['recipe'] and registry.builtin_sources()['SPECIAL101'] == '스페셜/내 전략'


def test_a_new_file_joins_the_list_without_restarting(installed):
    assert 'SPECIAL150' not in registry.list_presets('Part1')
    write(installed / '스페셜/내 전략/SPECIAL150.recipe.json', entry(150))
    assert 'SPECIAL150' in registry.list_presets('Part1') and 'SPECIAL150' in registry.list_presets('Part2')
    (installed / '스페셜/내 전략/SPECIAL150.recipe.json').unlink()
    assert 'SPECIAL150' not in registry.list_presets('Part1')


def test_a_removed_installed_folder_reads_as_empty(installed):
    shutil.rmtree(installed / '스페셜/내 전략')
    assert set(registry.builtin_entries()) == {'SPECIAL1', 'SPECIAL99'}
    shutil.rmtree(installed / '스페셜/기본')
    # The 스페셜 folder still decides: zero strategies, not the old registry.
    assert registry.builtin_entries() == {} and registry.skipped_builtins() == []


def test_development_project_keeps_its_folder_until_a_special_folder_exists(tmp_path, monkeypatch):
    root = project(tmp_path / '수정본', development=(5,))
    monkeypatch.setattr(registry, 'REGISTRY', root / 'settings/strategy_registry.json')
    assert registry.builtin_entries() == {'SPECIAL5': entry(5)}
    assert registry.builtin_sources() == {'SPECIAL5': 'Part1/program/SPECIAL'}
    # The development folder allows every number, as before.
    write(root / special_files.SPECIAL_DIRECTORY / 'SPECIAL100.recipe.json', entry(100))
    assert set(registry.builtin_entries()) == {'SPECIAL5', 'SPECIAL100'}
    write(root / '스페셜/기본/SPECIAL1.recipe.json', entry(1))
    assert registry.builtin_entries() == {'SPECIAL1': entry(1)}


def test_the_installed_folders_read_the_same_after_the_installation_moves(installed, monkeypatch, tmp_path):
    before, sources = registry.builtin_entries(), registry.builtin_sources()
    moved = Path(shutil.copytree(installed, tmp_path / '다른 PC' / 'MOSES 이동'))
    monkeypatch.setattr(registry, 'REGISTRY', moved / 'settings/strategy_registry.json')
    assert registry.builtin_entries() == before and registry.builtin_sources() == sources


def test_a_special_folder_outside_the_installation_is_refused(tmp_path, monkeypatch):
    winapi = pytest.importorskip('_winapi')
    root = project(tmp_path / 'MOSES')
    outside = tmp_path / '밖'
    write(outside / '기본/SPECIAL1.recipe.json', entry(1))
    winapi.CreateJunction(str(outside), str(root / '스페셜'))
    monkeypatch.setattr(registry, 'REGISTRY', root / 'settings/strategy_registry.json')
    try:
        with pytest.raises(ValueError, match='스페셜 폴더는 MOSES 폴더 안에 두세요'):
            registry.builtin_entries()
    finally:
        os.rmdir(root / '스페셜')


def test_strategy_description_names_the_folder_it_came_from(installed):
    from lab import catalog
    assert catalog.describe_special(100)['source'] == '스페셜/내 전략'
    assert catalog.describe_special(1)['source'] == '스페셜/기본'


# ---- live start: a strategy in 내 전략 waits until it is turned on (수정본167; the folder guide says so) --

@pytest.fixture
def control(installed, tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location('live_control166', ROOT / 'Part1/live_control.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, 'SPECIAL_SETTINGS_PATH', tmp_path / 'special_settings.json')
    monkeypatch.setattr(module, 'start_program', Mock(return_value=module.LiveStartResult(True, 'started')))
    monkeypatch.setattr(module.subprocess, 'Popen', Mock(side_effect=AssertionError('real engine start forbidden')))
    return module


def saved(control, rows):
    control.SPECIAL_SETTINGS_PATH.write_text(json.dumps({'specials': rows}), encoding='utf-8')


def test_live_start_leaves_a_new_strategy_of_my_strategies_off(control):
    saved(control, {'SPECIAL1': {'enabled': True}, 'SPECIAL99': {'enabled': False}})
    control.start_live()
    assert control.start_program.call_args.kwargs['enabled_specials'] == {'SPECIAL1'}
    saved(control, {'SPECIAL100': {'enabled': True}})
    control.start_live()
    assert control.start_program.call_args.kwargs['enabled_specials'] == {'SPECIAL1', 'SPECIAL99', 'SPECIAL100'}


def test_a_left_out_file_in_my_strategies_is_announced_at_live_start(control, installed):
    write(installed / '스페셜/내 전략/SPECIAL5.recipe.json', entry(5))
    saved(control, {'SPECIAL1': {'enabled': True}})
    result = control.start_live()
    notice = '내 전략/SPECIAL5.recipe.json 제외 · 내 전략은 SPECIAL100~SPECIAL999만 씁니다'
    assert result.warnings == [notice] and notice in result[1]
