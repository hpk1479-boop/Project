"""166: a release carries the developer's SPECIAL folder as 스페셜/기본 and an empty 스페셜/내 전략.

The development project keeps Part1/program/SPECIAL. The installed copy reads the same
definitions from 스페셜/기본 (numbers 1~99) and the user's own from 스페셜/내 전략 (100~999).
"""
import json
from pathlib import Path
import shutil
import sys
import zipfile

import pytest

from releasekit import builder, manuals, resources, validation


def write(root, relative, content):
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding='utf-8')
    return target


@pytest.fixture
def project(tmp_path):
    root = tmp_path / 'synthetic166'
    for directory in builder.TREES:
        (root / directory).mkdir(parents=True, exist_ok=True)
    for relative in builder.FILES:
        value = '%PDF-1.7\n% fixture\n%%EOF\n' if relative in manuals.MANUALS else (
            'VALUE = 1\n' if Path(relative).suffix in ('.py', '.pyw') else '{}')
        write(root, relative, value)
    write(root, 'settings/strategy_registry.json', json.dumps({'schema_version': 2, 'presets': []}))
    return root


def special(number):
    return {'id': 'SPECIAL' + str(number), 'name': '정식 스페셜 ' + str(number),
        'symbol_source': 'RECIPE', 'recipe': {'schema_version': 2, 'base': 'AI',
        'name': '정식 스페셜 ' + str(number), 'description': 'fixture', 'symbols': ['TEST'],
        'strategy_intent': {'symbols': ['TEST'], 'direction': 'LONG',
            'order_mode': 'SIMULTANEOUS', 'steps': [{'kind': 'MA_PRICE_TOUCH',
                'tfs': ['5m'], 'ma_family': 'EMA', 'slow_period': 50}],
            'final': {'kind': 'NOTIFY'}}}}


def recipe_file(project, number):
    return write(project, f'Part1/program/SPECIAL/SPECIAL{number}.recipe.json',
                 json.dumps(special(number), ensure_ascii=False, indent=2) + '\n')


def prepared(root, destination):
    selected = list(builder.source_paths(root))
    return builder.prepare_data(root, destination, selected), selected


def reader():
    program = str(Path(builder.__file__).resolve().parents[2] / 'Part1/program')
    if program not in sys.path:
        sys.path.insert(0, program)
    from strategy_recipe import special_files
    return special_files


def test_release_ships_the_development_folder_as_the_installed_basic_folder(project, tmp_path):
    for number in (1, 2, 99):
        recipe_file(project, number)
    write(project, 'Part1/program/SPECIAL/SPECIAL8.py', 'PART3_RECIPE = ' + repr(special(8)['recipe']) + '\n')
    write(project, 'Part1/program/SPECIAL/관리안내.md', '# developer notes')
    payload = tmp_path / 'payload'
    code, sources = prepared(project, payload)
    basic = payload / '스페셜/기본'
    assert sorted(path.name for path in basic.iterdir()) == [
        'SPECIAL1.recipe.json', 'SPECIAL2.recipe.json', 'SPECIAL8.recipe.json', 'SPECIAL99.recipe.json']
    for number in (1, 2, 99):
        name = f'SPECIAL{number}.recipe.json'
        assert (basic / name).read_bytes() == (project / 'Part1/program/SPECIAL' / name).read_bytes()
    # The developer folder and its notes stay out; the recipient gets the folder guide and an empty 내 전략.
    assert not (payload / 'Part1/program/SPECIAL').exists()
    assert not any('SPECIAL' in Path(name).parts for name in code)
    assert (payload / '스페셜/내 전략').is_dir() and not list((payload / '스페셜/내 전략').iterdir())
    guide = (payload / '스페셜/안내.md').read_bytes()
    assert guide == (Path(builder.__file__).with_name('special_guide.md')).read_bytes()
    assert '내 전략' in guide.decode('utf-8') and 'SPECIAL100~SPECIAL999' in guide.decode('utf-8')
    registry = json.loads((payload / 'settings/strategy_registry.json').read_bytes())
    assert [row['id'] for row in registry['presets']] == ['SPECIAL1', 'SPECIAL2', 'SPECIAL8', 'SPECIAL99']
    # The installed reader finds the same strategies the developer's reader does.
    special_files = reader()
    assert special_files.read_special_entries(payload) == special_files.read_special_entries(project)
    assert special_files.skipped_special_files(payload) == []
    assert set(special_files.special_sources(payload).values()) == {'스페셜/기본'}
    manifest = resources.discover_resources(project, sources)
    resources.validate_payload(payload, manifest, code)
    validation.write_payload_archive(payload, tmp_path / 'payload.zip')
    with zipfile.ZipFile(tmp_path / 'payload.zip') as archive:
        assert archive.getinfo('스페셜/내 전략/').is_dir()
        assert archive.read('스페셜/기본/SPECIAL2.recipe.json') == (basic / 'SPECIAL2.recipe.json').read_bytes()
    (payload / '스페셜/내 전략').rmdir()
    with pytest.raises(ValueError, match='스페셜/내 전략'):
        resources.validate_payload(payload, manifest, code)


def test_a_number_of_the_users_range_stops_the_release(project, tmp_path):
    recipe_file(project, 1)
    recipe_file(project, 100)
    with pytest.raises(ValueError, match='SPECIAL1~SPECIAL99.*SPECIAL100.recipe.json'):
        prepared(project, tmp_path / 'payload')


@pytest.mark.parametrize('files,reason', [
    ({'SPECIAL8.py': 'PART3_RECIPE = {\n'}, '파이썬 문법 오류'),
    ({'SPECIAL8.py': 'PART3_RECIPE = ' + repr(special(8)['recipe']) + '\n',
      'SPECIAL8.recipe.json': json.dumps(special(8))}, '중복'),
])
def test_a_python_special_that_cannot_become_a_recipe_stops_the_release(project, tmp_path, files, reason):
    for name, content in files.items():
        write(project, 'Part1/program/SPECIAL/' + name, content)
    with pytest.raises(ValueError, match='기본 스페셜을 읽지 못했습니다: SPECIAL8.py 제외 · .*' + reason):
        prepared(project, tmp_path / 'payload')


def test_a_development_project_with_an_installed_folder_is_refused(project, tmp_path):
    recipe_file(project, 1)
    (project / '스페셜/내 전략').mkdir(parents=True)
    with pytest.raises(ValueError, match='개발본에는 스페셜 폴더'):
        prepared(project, tmp_path / 'payload')


def test_a_project_without_a_special_folder_keeps_the_old_registry(project, tmp_path):
    registry = {'schema_version': 2, 'presets': [special(3)]}
    write(project, 'settings/strategy_registry.json', json.dumps(registry))
    payload = tmp_path / 'payload'
    code, sources = prepared(project, payload)
    assert json.loads((payload / 'settings/strategy_registry.json').read_text('utf-8')) == registry
    assert not (payload / '스페셜').exists()
    assert '스페셜/기본' not in resources.discover_resources(project, sources).directories


def test_the_installed_copy_reads_the_same_after_the_folder_moves(project, tmp_path):
    recipe_file(project, 4)
    payload = tmp_path / 'payload'
    prepared(project, payload)
    (tmp_path / '다른 위치').mkdir()
    moved = Path(shutil.move(str(payload), str(tmp_path / '다른 위치' / 'MOSES')))
    special_files = reader()
    assert special_files.read_special_entries(moved) == special_files.read_special_entries(project)
    assert special_files.special_sources(moved) == {'SPECIAL4': '스페셜/기본'}
