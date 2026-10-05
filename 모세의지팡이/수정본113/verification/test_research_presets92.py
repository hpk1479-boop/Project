"""Template loading preserves canonical meaning and never writes its source."""
import copy
import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from test_research_modes80 import conversation
from test_research79 import fixture, plan, step, research_reply
from lab import catalog as project_catalog, storage
from lab.ai import research_presets
from lab.ai.research_backtest import Session as Plan
from strategy_recipe import registry


def entry(meaning=None, identifier='MY_RESEARCH_TEMPLATE'):
    meaning = copy.deepcopy(meaning or fixture().original()['interpretation'])
    return {'id': identifier, 'name': '사용자 연구 템플릿', 'symbol_source': 'CONFIG',
        'time_filters': ['MAIN_ASIA', 'MAIN_LONDON'],
        'final_time_filters': {'MAIN_NEWYORK': {'enabled': True, 'start': '20:00', 'end': '23:00'}},
        'recipe': {'schema_version': 2, 'base': 'AI', 'name': '사용자 연구 템플릿',
            'description': '원본 설명', 'symbols': [], 'strategy_intent': meaning}}


def source(tmp_path, monkeypatch, entries):
    path = tmp_path / 'strategy_registry.json'
    def save(rows):
        path.write_text(json.dumps({'schema_version': 2, 'presets': rows}, ensure_ascii=False), encoding='utf-8')
    save(entries)
    monkeypatch.setattr(registry, 'REGISTRY', path)
    return path, save


def state(research, agent):
    return (agent.checkpoint(), research.revision, research.number, copy.deepcopy(research.history),
        research.kind, research.pending, research.question, copy.deepcopy(research.editor_strategy),
        copy.deepcopy(research.editor_plan), copy.deepcopy(research.editor_errors), research.editor_invalid,
        research.backtest, research.backtest.checkpoint() if research.backtest else None)


def test_dynamic_registry_listing_add_and_delete_has_no_numbered_dispatch(tmp_path, monkeypatch):
    first = entry()
    path, save = source(tmp_path, monkeypatch, [first])
    assert [row['id'] for row in research_presets.catalog()] == ['MY_RESEARCH_TEMPLATE']
    second = entry(identifier='ANOTHER_TEMPLATE')
    second['name'] = '새 템플릿'
    save([first, second])
    assert [row['id'] for row in research_presets.catalog()] == ['MY_RESEARCH_TEMPLATE', 'ANOTHER_TEMPLATE']
    save([second])
    assert [row['id'] for row in research_presets.catalog()] == ['ANOTHER_TEMPLATE']
    with pytest.raises(ValueError, match='등록되지 않은 preset'):
        research_presets.load(first['id'])


def test_catalog_and_load_do_not_ask_provider_or_apply_write_start(tmp_path, monkeypatch):
    row = entry()
    path, _ = source(tmp_path, monkeypatch, [row])
    original = path.read_bytes()
    generate = Mock()
    monkeypatch.setattr(storage, 'generate', generate)
    research, agent, provider, apply = conversation([])
    first = research.presets()
    assert first['revision'] == research.revision
    assert len(first['items']) == 1 and first['items'][0]['example']
    loaded = research.load_preset(row['id'], first['revision'])
    assert loaded['ok'] and loaded['can_apply'] and loaded['kind'] == 'STRATEGY'
    assert loaded['editor_contract']['strategy'] == loaded['editor_strategy'] == agent.last_intent
    assert loaded['editor_contract']['revision'] == loaded['revision']
    assert not provider.exchanges and not provider.seen
    apply.assert_not_called(); generate.assert_not_called()
    assert path.read_bytes() == original


def test_config_symbols_are_current_setting_without_invented_fallback(tmp_path, monkeypatch):
    row = entry()
    row['recipe']['strategy_intent']['symbols'] = []
    source(tmp_path, monkeypatch, [row])
    symbols = project_catalog.SYMBOLS
    assert symbols
    monkeypatch.setattr(project_catalog, 'current_symbols', lambda: [symbols[-1]])
    assert research_presets.load(row['id'])['strategy']['interpretation']['symbols'] == [symbols[-1]]
    monkeypatch.setattr(project_catalog, 'current_symbols', lambda: [])
    with pytest.raises(ValueError, match='현재 설정에 종목이 없습니다'):
        research_presets.load(row['id'])


def test_explicit_symbols_are_kept_without_using_configuration(tmp_path, monkeypatch):
    row = entry()
    del row['symbol_source']
    source(tmp_path, monkeypatch, [row])
    monkeypatch.setattr(project_catalog, 'current_symbols', lambda: [])
    assert research_presets.load(row['id'])['strategy']['interpretation']['symbols'] == row['recipe']['strategy_intent']['symbols']


def test_every_current_preset_passes_the_existing_ai_recipe_contract():
    rows = registry.entries()
    assert rows
    for identifier, original in rows.items():
        loaded = research_presets.load(identifier)
        expected = registry.preset_meaning(identifier,
            symbols=project_catalog.current_symbols() if original.get('symbol_source') == 'CONFIG' else None)
        assert loaded['strategy']['interpretation'] == expected
        assert loaded['example'] == research_presets.example(expected, original['name'])
        assert registry.preset_entry(identifier) == original


def test_branches_refs_lifetimes_and_time_filters_are_not_reduced_to_a_summary(tmp_path, monkeypatch):
    original = fixture().original()['interpretation']
    unit = {'direction': 'LONG', 'steps': [
        {'kind': 'FVG_NEW', 'tfs': ['5m'], 'side': 'BULL', 'capture': 'new_gap'},
        {'kind': 'FVG_TOUCH', 'tfs': ['1m'], 'side': 'BULL', 'ref': 'new_gap'}],
        'order_mode': 'SEQUENTIAL', 'within_sec': 1800, 'final_window_sec': 600,
        'final_after': 'FVG_TOUCH', 'final': {'kind': 'OZ', 'tfs': ['1m'],
            'validation_mode': 'BLIND', 'trigger_mode': 'BREAKER', 'scope_ref': 'new_gap'},
        'after_conditions': [{'kind': 'TREND', 'tfs': ['15m'], 'direction': 'LONG'}],
        'final_conditions': [{'kind': 'MA_PRICE_STATE', 'tfs': ['1m'], 'ma_family': 'EMA', 'slow_period': 50}],
        'cancel_conditions': [{'kind': 'TREND', 'tfs': ['15m'], 'direction': 'SHORT'}],
        'lifecycle': {'expires': {'bars': 10, 'tf': '1m'}, 'invalidate_refs': True,
            'first_success': True, 'replace': {'scope': 'SYMBOL_DIRECTION'},
            'snapshots': {'start_price': {'tf': '1m', 'field': 'open', 'bar_state': 'CLOSED'}},
            'restart_on': [{'kind': 'BAR_CLOSE', 'tfs': ['30m']}]}}
    meaning = {**original, 'steps': [], 'branches': [unit, copy.deepcopy(unit)], 'persistent': False}
    row = entry(meaning)
    path, _ = source(tmp_path, monkeypatch, [row])
    research, agent, provider, apply = conversation([])
    loaded = research.load_preset(row['id'], research.revision)
    actual = loaded['result']['interpretation']
    assert actual['branches'] == meaning['branches']
    assert actual['time_filters'] == row['time_filters']
    assert actual['final_time_filters'] == row['final_time_filters']
    text = loaded['example']
    for fragment in ('분기 1', '분기 2', '1800', '600', 'new_gap', '30분봉', '봉 수: 10', '무지성', '브레이커', '20:00', '23:00'):
        assert fragment in text
    assert not provider.exchanges
    apply.assert_not_called()


@pytest.mark.parametrize('failure', ['missing', 'invalid', 'stale', 'empty_symbols', 'accept', 'contract'])
def test_failed_load_preserves_conversation_and_native_approval(tmp_path, monkeypatch, failure):
    row = entry()
    source(tmp_path, monkeypatch, [row])
    research, agent, provider, apply = conversation([research_reply('STRATEGY', strategy=fixture().original())])
    first = research.send('초기 전략')
    before = state(research, agent)
    identifier, revision = row['id'], first['revision']
    if failure == 'missing': identifier = 'DOES_NOT_EXIST'
    if failure == 'invalid':
        row['recipe']['strategy_intent']['steps'][0]['tfs'] = ['BAD']
        source(tmp_path, monkeypatch, [row])
    if failure == 'empty_symbols': monkeypatch.setattr(project_catalog, 'current_symbols', lambda: [])
    if failure == 'stale': revision = 'old:0'
    if failure == 'accept': monkeypatch.setattr(agent, 'accept', Mock(side_effect=ValueError('오류')))
    if failure == 'contract':
        from lab.ai import research_editor
        monkeypatch.setattr(research_editor, 'contract', Mock(side_effect=ValueError('오류')))
    if failure == 'stale':
        result = research.load_preset(identifier, revision)
        assert not result['ok'] and result['errors'][0]['path'] == ['revision']
    else:
        with pytest.raises(ValueError): research.load_preset(identifier, revision)
    assert state(research, agent) == before
    assert len(provider.exchanges) == 1
    apply.assert_not_called()
    research.confirm(first['revision'])
    apply.assert_called_once()


def test_success_replaces_old_conversation_and_backtest_draft_only(tmp_path, monkeypatch):
    row = entry()
    source(tmp_path, monkeypatch, [row])
    research, agent, provider, apply = conversation([
        research_reply('STRATEGY', strategy=fixture().original()),
        research_reply('BACKTEST', research_plan=plan(step(draft=True, target_mode='GENERATED', specials=[], filename=None)))])
    first = research.send('기존 전략')
    executor = Mock()
    research.backtest = Plan(provider, lambda: agent, context_loader=research.context_loader, executor=executor)
    second = research.send('이 전략 백테스트')
    old = research.backtest
    assert old.pending and second['kind'] == 'BACKTEST'
    loaded = research.load_preset(row['id'], second['revision'])
    assert research.backtest is None and old.pending is None
    assert len(research.history) == 2 and research.history[0]['content'] == loaded['example']
    assert agent.original_text == loaded['example'] and not loaded['is_revision']
    for revision in (first['revision'], second['revision']):
        with pytest.raises(ValueError): research.confirm(revision)
    executor.assert_not_called(); apply.assert_not_called()


def test_manual_and_natural_edits_use_loaded_full_intent_and_confirm_new_recipe(tmp_path, monkeypatch):
    row = entry()
    path, _ = source(tmp_path, monkeypatch, [row])
    original_bytes = path.read_bytes()
    correction = research_presets.load(row['id'])['strategy']
    correction['interpretation']['steps'][1]['tfs'] = ['15m']
    research, agent, provider, apply = conversation([research_reply('STRATEGY', strategy=correction)])
    loaded = research.load_preset(row['id'], research.revision)
    manually_changed = copy.deepcopy(loaded['result'])
    manually_changed['interpretation']['steps'][0]['tfs'] = ['1h']
    edited = research.edit(loaded['revision'], manually_changed, 'STRATEGY')
    assert edited['ok']
    corrected = research.send('원비를 15분으로 바꿔줘')
    assert json.loads(provider.seen[-1]['content'])['context']['current_strategy'] == manually_changed
    assert corrected['can_apply'] and corrected['is_revision']
    assert path.read_bytes() == original_bytes
    apply.assert_not_called()
    confirmed = research.confirm(corrected['revision'])
    assert confirmed['recipe']['strategy_intent'] == correction['interpretation']
    assert 'preset' not in confirmed['recipe']['strategy_intent']
    assert path.read_bytes() == original_bytes
    apply.assert_called_once()


def test_registry_move_keeps_relative_recipe_loading(tmp_path, monkeypatch):
    row = entry()
    path, _ = source(tmp_path, monkeypatch, [row])
    expected = research_presets.load(row['id'])
    moved = tmp_path / 'moved' / 'settings'
    moved.mkdir(parents=True)
    replacement = moved / path.name
    replacement.write_bytes(path.read_bytes())
    monkeypatch.setattr(registry, 'REGISTRY', replacement)
    assert research_presets.load(row['id']) == expected


def test_language_renderer_keeps_reference_identifiers_and_relation_meaning():
    text = research_presets.example({'symbols': ['SOURCE'], 'steps': [
        {'kind': 'MA_PRICE_CROSS', 'tfs': ['15m'], 'relation': 'BOTH', 'ma_family': 'EMA', 'slow_period': 200},
        {'kind': 'FVG_NEW', 'tfs': ['5m'], 'capture': 'LONG'},
        {'kind': 'FVG_TOUCH', 'tfs': ['1m'], 'ref': 'LONG'}], 'final_time_filters': 0}, '연구 예문')
    assert '종목: SOURCE' in text and '동일 사건 참조: LONG' in text and '사건 기록 이름: LONG' in text
    assert '상향 또는 하향 교차' in text and '거래시간 제한 없음' in text


def test_expanded_inherited_preset_is_independent_after_source_deletion(tmp_path, monkeypatch):
    base = entry(identifier='BASE_TEMPLATE')
    derived = entry({'preset': base['id'], 'symbols': []}, identifier='DERIVED_TEMPLATE')
    path, save = source(tmp_path, monkeypatch, [base, derived])
    research, agent, provider, apply = conversation([])
    loaded = research.load_preset(derived['id'], research.revision)
    assert 'preset' not in loaded['result']['interpretation']
    assert loaded['result']['interpretation']['steps'] == base['recipe']['strategy_intent']['steps']
    save([])
    changed = copy.deepcopy(loaded['result'])
    changed['interpretation']['steps'][0]['tfs'] = ['1h']
    edited = research.edit(loaded['revision'], changed, 'STRATEGY')
    assert edited['ok'] and edited['result'] == changed
    confirmed = research.confirm(edited['revision'])
    assert confirmed['recipe']['strategy_intent'] == changed['interpretation']
    assert not provider.exchanges


def test_loaded_edited_confirmed_strategy_saves_a_new_number_and_preserves_originals(tmp_path, monkeypatch):
    row = entry()
    path, _ = source(tmp_path, monkeypatch, [row])
    registry_bytes = path.read_bytes()
    root = tmp_path / 'Part3'
    generated = root / 'generated'
    generated.mkdir(parents=True)
    original = generated / 'Test_SPECIAL001.py'
    original.write_bytes(b'# Previously saved strategy is preserved.\n')
    original_bytes = original.read_bytes()
    monkeypatch.setattr(storage, 'ROOT', root)
    research, agent, provider, apply = conversation([])
    loaded = research.load_preset(row['id'], research.revision)
    assert list(generated.iterdir()) == [original]
    changed = copy.deepcopy(loaded['result'])
    changed['interpretation']['steps'][0]['tfs'] = ['1h']
    edited = research.edit(loaded['revision'], changed, 'STRATEGY')
    assert edited['ok'] and list(generated.iterdir()) == [original]
    confirmed = research.confirm(edited['revision'])
    saved = storage.generate(confirmed['recipe'])
    assert saved['filename'] == 'Test_SPECIAL002.py'
    current = Path(saved['path']).parent
    compile((current / saved['filename']).read_text('utf-8'), saved['filename'], 'exec')
    metadata = json.loads((current / 'Test_SPECIAL002.recipe.json').read_text('utf-8'))
    assert metadata['recipe']['strategy_intent'] == changed['interpretation']
    assert (current / original.name).read_bytes() == original_bytes and path.read_bytes() == registry_bytes
    assert not provider.exchanges
