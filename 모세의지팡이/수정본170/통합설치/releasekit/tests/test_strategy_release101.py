"""Only the developer's authoritative SPECIAL folder enters new releases."""
import json
from pathlib import Path
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
    root = tmp_path / 'synthetic101'
    for directory in builder.TREES:
        (root / directory).mkdir(parents=True, exist_ok=True)
    for relative in builder.FILES:
        value = '%PDF-1.7\n% fixture\n%%EOF\n' if relative in manuals.MANUALS else (
            'VALUE = 1\n' if Path(relative).suffix in ('.py', '.pyw') else '{}')
        write(root, relative, value)
    write(root, 'settings/strategy_registry.json', json.dumps({'schema_version': 2, 'presets': []}))
    return root


def special(number=1):
    return {'id': 'SPECIAL' + str(number), 'name': '정식 스페셜 ' + str(number),
        'symbol_source': 'RECIPE', 'recipe': {'schema_version': 2, 'base': 'AI',
        'name': '정식 스페셜 ' + str(number), 'description': 'fixture', 'symbols': ['TEST'],
        'strategy_intent': {'symbols': ['TEST'], 'direction': 'LONG',
            'order_mode': 'SIMULTANEOUS', 'steps': [{'kind': 'MA_PRICE_TOUCH',
                'tfs': ['5m'], 'ma_family': 'EMA', 'slow_period': 50}],
            'final': {'kind': 'NOTIFY'}}}}


def prepared(root, destination):
    selected = list(builder.source_paths(root))
    return builder.prepare_data(root, destination, selected), selected


def test_test_library_promoted_state_and_legacy_files_cannot_enter_payload(project, tmp_path):
    blocked = ['Part3/TEST_SPECIAL/Test_SPECIAL001.py', 'Part3/TEST_SPECIAL/Test_SPECIAL001.recipe.json',
        'Part3/generated/Test_SPECIAL002.py', 'Part3/generated/Test_SPECIAL002.recipe.json',
        'Part1/program/TeSt_SpEcIaL/private.recipe.json', 'Part3/web/TEST_SPECIAL/private.txt',
        'settings/strategy_visibility.json', 'Part1/program/Strategy_Visibility.JSON']
    for relative in blocked:
        write(project, relative, 'private promoted developer strategy')
    selected = list(builder.source_paths(project))
    names = {relative.as_posix() for relative, _ in selected}
    assert not names.intersection(blocked)
    destination = tmp_path / 'payload'
    code = builder.prepare_data(project, destination, selected)
    assert (destination / 'Part3/TEST_SPECIAL').is_dir()
    assert not list((destination / 'Part3/TEST_SPECIAL').iterdir())
    assert not (destination / 'Part3/generated').exists()
    assert not any('strategy_visibility.json' == path.name.casefold() for path in destination.rglob('*'))
    assert not any('test_special' in path.casefold() for path in code)


def test_authoritative_special_files_override_stale_registry_and_preserve_original(project, tmp_path):
    stale = {'schema_version': 2, 'presets': [special(7)]}
    original = write(project, 'settings/strategy_registry.json', json.dumps(stale))
    for number in (1, 2, 3, 4):
        write(project, f'Part1/program/SPECIAL/SPECIAL{number}.recipe.json', json.dumps(special(number)))
    before = original.read_bytes()
    code, sources = prepared(project, tmp_path / 'payload')
    normalized = json.loads((tmp_path / 'payload/settings/strategy_registry.json').read_bytes())
    assert [entry['id'] for entry in normalized['presets']] == ['SPECIAL1', 'SPECIAL2', 'SPECIAL3', 'SPECIAL4']
    assert original.read_bytes() == before
    # 166: an installation reads them from 스페셜/기본, not from the development folder.
    assert (tmp_path / 'payload/스페셜/기본').is_dir()
    assert not (tmp_path / 'payload/Part1/program/SPECIAL').exists()
    assert all('TEST_SPECIAL' not in name for name in code)


def test_empty_authoritative_folder_means_zero_and_next_manual_addition_is_current(project, tmp_path):
    write(project, 'settings/strategy_registry.json', json.dumps({'schema_version': 2, 'presets': [special()]}))
    folder = project / 'Part1/program/SPECIAL'
    folder.mkdir()
    prepared(project, tmp_path / 'first')
    assert json.loads((tmp_path / 'first/settings/strategy_registry.json').read_bytes())['presets'] == []
    assert (tmp_path / 'first/스페셜/기본').is_dir() and not list((tmp_path / 'first/스페셜/기본').iterdir())
    write(project, 'Part1/program/SPECIAL/SPECIAL8.recipe.json', json.dumps(special(8)))
    prepared(project, tmp_path / 'second')
    assert [row['id'] for row in json.loads((tmp_path / 'second/settings/strategy_registry.json').read_bytes())['presets']] == ['SPECIAL8']


def test_manually_added_special_python_ships_as_its_literal_recipe_without_execution(project, tmp_path):
    definition = special(8)['recipe']
    write(project, 'Part1/program/SPECIAL/SPECIAL8.py', 'PART3_RECIPE = ' + repr(definition) +
          '\nraise RuntimeError("Reading this definition must not execute code")\n')
    code, sources = prepared(project, tmp_path / 'payload')
    # 166: the installed 스페셜/기본 holds data only; the Python file is neither compiled nor copied.
    assert not any('SPECIAL8' in name for name in code)
    assert not list((tmp_path / 'payload').rglob('SPECIAL8.py'))
    shipped = json.loads((tmp_path / 'payload/스페셜/기본/SPECIAL8.recipe.json').read_text('utf-8'))
    assert shipped['id'] == 'SPECIAL8' and shipped['recipe'] == definition
    normalized = json.loads((tmp_path / 'payload/settings/strategy_registry.json').read_bytes())
    assert normalized['presets'][0]['id'] == 'SPECIAL8'


def test_empty_library_and_special_directory_are_required_archive_entries(project, tmp_path):
    (project / 'Part1/program/SPECIAL').mkdir()
    code, sources = prepared(project, tmp_path / 'payload')
    manifest = resources.discover_resources(project, sources)
    assert {'Part3/TEST_SPECIAL', '스페셜/기본', '스페셜/내 전략'} <= set(manifest.directories)
    assert 'Part1/program/SPECIAL' not in manifest.directories
    validation.write_payload_archive(tmp_path / 'payload', tmp_path / 'payload.zip')
    with zipfile.ZipFile(tmp_path / 'payload.zip') as archive:
        assert archive.getinfo('Part3/TEST_SPECIAL/').is_dir()
        assert archive.getinfo('스페셜/기본/').is_dir()
        assert archive.getinfo('스페셜/내 전략/').is_dir()
    (tmp_path / 'payload/Part3/TEST_SPECIAL').rmdir()
    with pytest.raises(ValueError, match='TEST_SPECIAL'):
        resources.validate_payload(tmp_path / 'payload', manifest, code)


def test_explicit_import_cannot_smuggle_case_variant_library_code(project):
    write(project, 'Part1/program/TeSt_SpEcIaL/private.py', 'SECRET = True\n')
    write(project, 'Part1/program/smuggle.py', 'from TeSt_SpEcIaL import private\n')
    with pytest.raises(ValueError, match='배포 제외'):
        list(builder.source_paths(project))


@pytest.mark.parametrize('relative', ['Part3/TEST_SPECIAL/Test_SPECIAL001.py',
    'Part3/GeNeRaTeD/private.json', 'settings/STRATEGY_VISIBILITY.JSON'])
def test_direct_payload_preparation_rejects_forbidden_user_state(project, tmp_path, relative):
    source = write(project, relative, '{}')
    with pytest.raises(ValueError, match='배포할 수 없습니다'):
        builder.prepare_data(project, tmp_path / 'payload', [(Path(relative), source)])


def test_structure_rejects_strategies_hidden_in_payload_or_bundle_paths(tmp_path):
    write(tmp_path, 'Part3/TeSt_SpEcIaL/private.json', '{}')
    with pytest.raises(ValueError, match='private.json'):
        validation.validate_structure(tmp_path, resources.ResourceManifest((), (), ()), [])
    (tmp_path / 'Part3/TeSt_SpEcIaL/private.json').unlink()
    with pytest.raises(ValueError, match='Test_SPECIAL001.py'):
        validation.validate_structure(tmp_path, resources.ResourceManifest((), (), ()),
            ['Part3/TEST_SPECIAL/Test_SPECIAL001.py'])
