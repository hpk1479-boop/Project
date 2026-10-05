"""State-machine and transport checks for the independent test library."""
import ast
import copy
import hashlib
import json
from pathlib import Path
import sys
import threading
import urllib.error
import urllib.request

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'Part1/program'), str(ROOT / 'Part3'), str(ROOT / 'Part2')]
from strategy_recipe import registry, user_catalog
from lab import storage, strategy_library, unified_live, backtest_jobs


@pytest.fixture
def library(tmp_path, monkeypatch):
    for part in ('Part1', 'Part2', 'Part3', 'settings'):
        (tmp_path / part).mkdir()
    target = tmp_path / 'settings/strategy_registry.json'
    target.write_bytes(registry.REGISTRY.read_bytes())
    monkeypatch.setattr(registry, 'REGISTRY', target)
    monkeypatch.setattr(storage, 'ROOT', tmp_path / 'Part3')
    monkeypatch.setattr(unified_live, 'engines', lambda: {'engines': []})
    monkeypatch.setattr(backtest_jobs, 'active_jobs', lambda: [])
    recipe = copy.deepcopy(next(iter(registry.builtin_entries().values()))['recipe'])
    recipe['name'] = '테스트 전략'
    recipe['strategy_intent']['symbols'] = ['XAUUSD+']
    recipe['symbols'] = ['XAUUSD+']
    return tmp_path, recipe


def make(library):
    root, recipe = library
    created = storage.generate(recipe)
    return root, created, created['strategy_id']


def saved(root, part):
    return json.loads(user_catalog._settings_path(part, root).read_text('utf-8'))


def test_new_strategy_is_library_only_and_uses_test_folder(library):
    root, created, identifier = make(library)
    assert Path(created['path']).parent == root / 'Part3/TEST_SPECIAL'
    assert not (root / 'Part3/generated').exists()
    assert identifier not in registry.list_presets()
    assert identifier not in registry.list_presets('Part1')
    assert identifier not in registry.list_presets('Part2')
    assert registry.preset_entry(identifier)['recipe'] == library[1]
    assert strategy_library.listing()['items'][0]['registrations'] == []
    assert not (root / 'Part1/special_settings.json').exists()
    assert not (root / 'Part2/backtest_ui.json').exists()


def test_promotion_enables_neither_host_and_does_not_move_files(library):
    root, created, identifier = make(library)
    source = Path(created['path']).read_bytes()
    result = strategy_library.change({'action': 'promote', 'ids': [identifier]})
    assert result['items'][0]['registrations'] == ['Part1', 'Part2']
    for part in user_catalog.PARTS:
        assert identifier in registry.list_presets(part)
        assert saved(root, part)['specials'][identifier]['enabled'] is False
    assert Path(created['path']).read_bytes() == source


@pytest.mark.parametrize('part,other', [('Part1', 'Part2'), ('Part2', 'Part1')])
def test_host_exclusion_leaves_other_host_and_library(library, part, other):
    root, created, identifier = make(library)
    strategy_library.change({'action': 'promote', 'ids': [identifier]})
    strategy_library.change_registration({'action': 'delete', 'part': part, 'ids': [identifier]})
    assert identifier not in registry.list_presets(part)
    assert identifier in registry.list_presets(other)
    assert identifier in registry.list_presets()
    assert identifier not in saved(root, part)['specials']
    assert identifier in saved(root, other)['specials']
    assert Path(created['path']).exists()
    assert strategy_library.listing()['items'][0]['registrations'] == [other]


def test_repromotion_restores_missing_host_only_and_preserves_existing_settings(library):
    root, created, identifier = make(library)
    builtins = registry.builtin_entries()
    user_catalog.promote_selected([identifier], builtins, root)
    settings = saved(root, 'Part1')
    settings['specials'][identifier]['enabled'] = True
    settings['specials'][identifier]['time_filters'] = {'MAIN_ASIA': {'enabled': True}}
    user_catalog.write_json(root / 'Part1/special_settings.json', settings)
    user_catalog.delete_selected([identifier], builtins, root, part='Part2')
    user_catalog.promote_selected([identifier, identifier], builtins, root)
    assert saved(root, 'Part1') == settings
    assert saved(root, 'Part2')['specials'][identifier]['enabled'] is False
    assert user_catalog.registrations(root)[identifier] == ['Part1', 'Part2']
    user_catalog.promote_selected([identifier], builtins, root)
    assert user_catalog.registrations(root)[identifier] == ['Part1', 'Part2']


def test_name_change_updates_all_views_and_preserves_id_filename_meaning(library):
    root, created, identifier = make(library)
    user_catalog.promote_selected([identifier], registry.builtin_entries(), root)
    code = Path(created['path'])
    old = code.read_text('utf-8')
    old_intent = old.split('_INTENT_PLAN = ', 1)[1]
    strategy_library.change({'action': 'rename', 'id': identifier, 'name': '새 이름 🟡'})
    entry = registry.preset_entry(identifier)
    assert entry['name'] == '새 이름 🟡'
    assert entry['recipe']['strategy_intent'] == library[1]['strategy_intent']
    for part in user_catalog.PARTS:
        assert registry.entries(part)[identifier]['name'] == '새 이름 🟡'
    assert code.name == created['filename'] and code.exists()
    changed = code.read_text('utf-8')
    assert changed.split('_INTENT_PLAN = ', 1)[1] == old_intent
    tree = ast.parse(changed)
    recipe = next(ast.literal_eval(n.value) for n in tree.body if isinstance(n, ast.Assign)
                  and any(isinstance(t, ast.Name) and t.id == 'PART3_RECIPE' for t in n.targets))
    assert recipe['name'] == '새 이름 🟡'
    assert not user_catalog.generated_errors(root)


@pytest.mark.parametrize('title', ['', '  ', 'x' * 121, 'line\nbreak', None])
def test_invalid_name_cannot_change_files(library, title):
    root, created, identifier = make(library)
    code = Path(created['path'])
    before = code.read_bytes(), code.with_suffix('.recipe.json').read_bytes()
    with pytest.raises(ValueError):
        user_catalog.rename_generated(identifier, title, root)
    assert before == (code.read_bytes(), code.with_suffix('.recipe.json').read_bytes())


@pytest.mark.parametrize('confirmed', [False, None, 1, 'yes'])
def test_registered_library_delete_requires_real_confirmation(library, confirmed):
    root, created, identifier = make(library)
    strategy_library.change({'action': 'promote', 'ids': [identifier]})
    before = user_catalog.visibility(root)
    with pytest.raises(ValueError, match='모두 제거'):
        strategy_library.change({'action': 'delete', 'ids': [identifier], 'confirmed': confirmed})
    assert Path(created['path']).exists()
    assert user_catalog.visibility(root) == before


def test_confirmed_library_delete_removes_both_registrations_and_settings(library):
    root, created, identifier = make(library)
    strategy_library.change({'action': 'promote', 'ids': [identifier]})
    result = strategy_library.change({'action': 'delete', 'ids': [identifier], 'confirmed': True})
    assert result['items'] == []
    assert not Path(created['path']).exists()
    assert not Path(created['path']).with_suffix('.recipe.json').exists()
    for part in user_catalog.PARTS:
        assert identifier not in registry.list_presets(part)
        assert identifier not in saved(root, part)['specials']
    assert identifier not in user_catalog.registrations(root)
    user_catalog.reset_builtins(root)
    assert identifier not in registry.list_presets()


def test_unpromoted_library_delete_needs_no_cross_host_confirmation(library):
    root, created, identifier = make(library)
    strategy_library.change({'action': 'delete', 'ids': [identifier]})
    assert not Path(created['path']).exists()


@pytest.mark.parametrize('part,other', [('Part1', 'Part2'), ('Part2', 'Part1')])
def test_reset_restores_only_builtins_in_chosen_host(library, part, other):
    root, created, identifier = make(library)
    builtins = registry.builtin_entries()
    builtin = next(iter(builtins))
    user_catalog.promote_selected([identifier], builtins, root)
    user_catalog.delete_selected([builtin], builtins, root, part=part)
    assert builtin not in registry.list_presets(part)
    assert builtin in registry.list_presets(other)
    user_catalog.reset_builtins(root, part=part)
    assert builtin in registry.list_presets(part)
    assert identifier not in registry.list_presets(part)
    assert identifier in registry.list_presets(other)
    assert Path(created['path']).exists()
    assert identifier not in saved(root, part)['specials']


def test_version100_visibility_and_existing_promotions_migrate_without_data_loss(library):
    root, created, identifier = make(library)
    meta_path = Path(created['path']).with_suffix('.recipe.json')
    meta = json.loads(meta_path.read_text('utf-8'))
    meta.pop('catalog_scope')
    user_catalog.write_json(meta_path, meta)
    builtin = next(iter(registry.builtin_entries()))
    old = {'schema_version': 1, 'hidden_builtins': [builtin]}
    path = root / 'settings/strategy_visibility.json'
    user_catalog.write_json(path, old)
    before = path.read_bytes()
    assert identifier in registry.list_presets('Part1') and identifier in registry.list_presets('Part2')
    assert builtin not in registry.list_presets()
    assert path.read_bytes() == before  # GET/read paths never rewrite user state.
    user_catalog.delete_selected([identifier], registry.builtin_entries(), root, part='Part1')
    assert user_catalog.visibility(root)['schema_version'] == 2
    assert identifier not in registry.list_presets('Part1') and identifier in registry.list_presets('Part2')
    assert user_catalog.hidden(root, 'Part1') == user_catalog.hidden(root, 'Part2') == {builtin}


def test_new_library_generation_does_not_change_old_migrated_promotions(library):
    root, created, old_identifier = make(library)
    path = Path(created['path']).with_suffix('.recipe.json')
    old = json.loads(path.read_text('utf-8'))
    old.pop('catalog_scope')
    user_catalog.write_json(path, old)
    new = storage.generate(library[1])['strategy_id']
    assert old_identifier in registry.list_presets()
    assert new not in registry.list_presets()


def test_legacy_folder_is_read_then_migrated_without_changing_files(library):
    root, created, identifier = make(library)
    current = root / 'Part3/TEST_SPECIAL'
    legacy = root / 'Part3/generated'
    expected = {p.name: p.read_bytes() for p in current.iterdir()}
    current.replace(legacy)
    assert user_catalog.test_directory(root) == legacy
    assert registry.preset_entry(identifier)['name'] == '테스트 전략'
    storage.generate(library[1])
    assert not legacy.exists() and current.is_dir()
    assert all((current / name).read_bytes() == data for name, data in expected.items())


def test_promotion_failure_rolls_back_every_host_file(library, monkeypatch):
    root, created, identifier = make(library)
    original = user_catalog.write_json
    count = 0
    def fail_second(path, data):
        nonlocal count
        count += 1
        if count == 2:
            raise OSError('second host write failed')
        return original(path, data)
    monkeypatch.setattr(user_catalog, 'write_json', fail_second)
    with pytest.raises(OSError):
        user_catalog.promote_selected([identifier], registry.builtin_entries(), root)
    assert not (root / 'Part1/special_settings.json').exists()
    assert not (root / 'Part2/backtest_ui.json').exists()
    assert not (root / 'settings/strategy_visibility.json').exists()
    assert identifier not in registry.list_presets()
    assert Path(created['path']).exists()


def test_rename_failure_restores_source_and_metadata(library, monkeypatch):
    root, created, identifier = make(library)
    code = Path(created['path'])
    before = code.read_bytes(), code.with_suffix('.recipe.json').read_bytes()
    def fail(*args):
        raise OSError('sidecar write failed')
    monkeypatch.setattr(user_catalog, 'write_json', fail)
    with pytest.raises(OSError):
        user_catalog.rename_generated(identifier, '실패한 이름', root)
    assert before == (code.read_bytes(), code.with_suffix('.recipe.json').read_bytes())


def test_library_lists_invalid_or_legacy_files_and_allows_their_deletion(library):
    root, _ = library
    folder = user_catalog.test_directory(root, for_write=True)
    code = folder / 'Test_SPECIAL210.py'
    code.write_text('# legacy preserved source\n', encoding='utf-8')
    user_catalog.write_json(code.with_suffix('.recipe.json'), {'recipe': {'schema_version': 1}})
    row = strategy_library.listing()['items'][0]
    assert row['valid'] is False and row['id'] == 'TEST_SPECIAL210'
    with pytest.raises(ValueError):
        strategy_library.change({'action': 'promote', 'ids': [row['id']]})
    strategy_library.change({'action': 'delete', 'ids': [row['id']]})
    assert not code.exists()


@pytest.mark.parametrize('busy', ['live', 'backtest'])
def test_mutation_is_blocked_while_runtime_uses_strategies(library, monkeypatch, busy):
    root, created, identifier = make(library)
    if busy == 'live':
        monkeypatch.setattr(unified_live, 'engines', lambda: {'engines': [{'pid': 1}]})
    else:
        monkeypatch.setattr(backtest_jobs, 'active_jobs', lambda: [{'id': 'running'}])
    with pytest.raises(ValueError, match='종료'):
        strategy_library.change({'action': 'promote', 'ids': [identifier]})
    assert identifier not in registry.list_presets()
    assert Path(created['path']).exists()


def test_invalid_host_cannot_change_shared_visibility(library):
    root, created, identifier = make(library)
    with pytest.raises(ValueError):
        strategy_library.change_registration({'action': 'reset'})
    assert not (root / 'settings/strategy_visibility.json').exists()


def test_authorized_http_library_routes_use_the_same_lifecycle(library):
    from lab import server
    root, created, identifier = make(library)
    host = server.LabServer(port=0)
    thread = threading.Thread(target=host.serve_forever, daemon=True)
    thread.start()
    base = 'http://127.0.0.1:' + str(host.server_port)
    def request(path, data=None, token=True):
        headers = {'X-Lab-Token': host.token} if token else {}
        body = None if data is None else json.dumps(data).encode('utf-8')
        if body is not None:
            headers['Content-Type'] = 'application/json'
        return json.loads(urllib.request.urlopen(urllib.request.Request(base + path, data=body, headers=headers), timeout=5).read())
    try:
        assert request('/api/strategies')['items'][0]['registrations'] == []
        with pytest.raises(urllib.error.HTTPError) as denied:
            request('/api/strategies', token=False)
        assert denied.value.code == 403
        assert request('/api/strategies', {'action': 'promote', 'ids': [identifier]})['ok']
        assert request('/api/mo/strategies', {'action': 'delete', 'part': 'Part1', 'ids': [identifier]})['items'][0]['registrations'] == ['Part2']
        assert request('/api/strategies', {'action': 'rename', 'id': identifier, 'name': 'HTTP 새 이름'})['items'][0]['name'] == 'HTTP 새 이름'
        assert request('/api/strategies', {'action': 'delete', 'ids': [identifier], 'confirmed': True})['items'] == []
    finally:
        host.shutdown()
        host.server_close()
        thread.join(timeout=5)
