"""Strategy list reads all real sources, independent of host promotion."""
import hashlib
import json
from pathlib import Path
import sys
import threading
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'tests'), str(ROOT / 'Part3'), str(ROOT / 'Part1/program')]
from test_strategy_lifecycle101 import library, make
from test_research_modes80 import conversation
from lab import server
from lab.ai import research_presets
from strategy_recipe import registry, user_catalog


def files(root):
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob('*') if p.is_file()}


def test_unpromoted_strategy_is_listed_and_loaded_without_source_changes(library):
    root, created, identifier = make(library)
    before = files(root)
    rows = research_presets.catalog()
    row = next(item for item in rows if item['id'] == identifier)
    assert row['source'] == 'generated' and row['registrations'] == []
    assert row['name'] == library[1]['name'] and row['example']
    assert identifier not in registry.list_presets()
    loaded = research_presets.load(identifier)
    assert loaded['name'] == row['name']
    assert loaded['strategy']['interpretation']['symbols'] == ['XAUUSD+']
    assert files(root) == before


def test_builtin_hidden_from_part1_remains_in_part3_list(library):
    root, recipe = library
    identifier = next(iter(registry.builtin_entries()))
    user_catalog.delete_selected([identifier], registry.builtin_entries(), root, part='Part1')
    assert identifier not in registry.list_presets('Part1')
    assert next(row for row in research_presets.catalog() if row['id'] == identifier)['source'] == 'builtin'


def test_old_or_invalid_files_do_not_appear_as_new_user_strategies(library):
    root, _, identifier = make(library)
    (root / 'Part3/TEST_SPECIAL/Test_SPECIAL099.py').write_text('# legacy test', encoding='utf-8')
    rows = research_presets.catalog()
    assert [row['id'] for row in rows if row['source'] == 'generated'] == [identifier]


def test_rename_delete_and_registrations_are_fresh_on_each_listing(library):
    root, created, identifier = make(library)
    user_catalog.rename_generated(identifier, '바뀐 이름', root)
    user_catalog.promote_selected([identifier], registry.builtin_entries(), root)
    row = next(row for row in research_presets.catalog() if row['id'] == identifier)
    assert row['name'] == '바뀐 이름' and set(row['registrations']) == {'Part1', 'Part2'}
    user_catalog.delete_library([identifier], root, confirmed=True)
    assert identifier not in {row['id'] for row in research_presets.catalog()}


def test_actual_http_loads_unpromoted_strategy_without_model_or_execution(library, monkeypatch):
    root, _, identifier = make(library)
    research, agent, provider, apply = conversation([])
    monkeypatch.setattr(server, 'research_session', lambda session: research)
    before = files(root)
    host = server.LabServer(0)
    worker = threading.Thread(target=host.serve_forever, daemon=True)
    worker.start()
    def post(route, body):
        request = urllib.request.Request('http://127.0.0.1:' + str(host.server_port) + '/api/' + route,
            data=json.dumps({'session':'list103-fixture', 'research':True, **body}).encode(),
            headers={'Content-Type':'application/json', 'X-Lab-Token':host.token})
        with urllib.request.urlopen(request, timeout=5) as response:
            return json.load(response)
    try:
        listed = post('ai/presets', {})
        assert identifier in {row['id'] for row in listed['items']}
        loaded = post('ai/preset/load', {'preset_id':identifier, 'revision':listed['revision']})
        assert loaded['ok'] and loaded['can_apply'] and loaded['editor_contract']['schema']
        assert loaded['result']['interpretation']['symbols'] == ['XAUUSD+']
        assert not provider.exchanges
        apply.assert_not_called()
        assert files(root) == before
    finally:
        host.shutdown(); host.server_close(); worker.join(5)
