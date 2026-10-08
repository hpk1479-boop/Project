"""Strategy editors must use the existing engine's clocks and settings keys."""
from __future__ import annotations

import sys
import shutil
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'Part3'), str(ROOT / 'Part1/program')]
from lab import unified_backtest, unified_live, unified_settings
from special_settings_model import SESSIONS, source_defaults


def test_metadata_reuses_real_source_defaults_and_only_exposes_session_clocks(tmp_path):
    config = tmp_path / 'config.txt'
    original = ('MAIN_ASIA=0900-1500\nMAIN_LONDON=1600-2000\nMAIN_NEWYORK=2100-2400\n'
                'TELEGRAM_TOKEN=must-not-be-returned\nGEMINI_API_KEY=must-not-be-returned\n')
    config.write_text(original, encoding='utf-8')
    with patch.object(unified_settings, 'LIVE_CONFIG', config):
        data = unified_live.strategy_settings_metadata(['SPECIAL1', 'SPECIAL2'])
    assert data['session_labels'] == SESSIONS
    assert data['session_times'] == {'MAIN_ASIA': '0900-1500', 'MAIN_LONDON': '1600-2000',
                                     'MAIN_NEWYORK': '2100-2400'}
    assert data['default_time_filters'] == {
        name: source_defaults(ROOT / 'Part1/program/SPECIAL', name)[1] for name in ('SPECIAL1', 'SPECIAL2')}
    assert 'must-not-be-returned' not in str(data)
    assert config.read_text('utf-8') == original


def test_live_saved_main_session_clocks_survive_preview_without_rewriting():
    filters = {'MAIN_ASIA': {'enabled': True, 'start': '0915', 'end': '1430'},
               'MAIN_LONDON': {'enabled': False}, 'MAIN_NEWYORK': {'enabled': False}}
    owner = {'discover_live_specials': lambda: ['SPECIAL1'],
             'load_special_settings': lambda: {'SPECIAL1': {'enabled': True, 'time_filters': filters}},
             'load_oz_profiles': lambda: None, 'special_code_default_trigger': lambda name: '올존'}
    with patch.object(unified_live, 'control', return_value=owner):
        result = unified_live.specials()
    assert result['items']['SPECIAL1']['time_filters'] == filters
    assert result['session_labels'] == SESSIONS
    assert result['items']['SPECIAL1']['default_time_filters'] == result['default_time_filters']['SPECIAL1']
    assert unified_live._validate_time_filters(filters) == filters


def test_backtest_preview_supplies_same_metadata_without_saving_live_or_backtest():
    settings, ui_model, _ = unified_backtest._part2()
    with patch.object(unified_backtest, 'warehouse', return_value=ROOT.parent / 'synthetic warehouse'), \
            patch.object(settings, 'stored_symbols', return_value=[]), \
            patch.object(ui_model, 'load', return_value={'specials': {}}), \
            patch.object(ui_model, 'save') as save:
        result = unified_backtest.options()
    assert result['session_labels'] == SESSIONS
    assert set(result['default_time_filters']) == set(result['specials'])
    assert set(result['session_times']) == set(SESSIONS)
    save.assert_not_called()


def test_strategy_metadata_remains_the_same_after_project_relocation(tmp_path):
    program = tmp_path / '이동한 프로젝트' / 'Part1' / 'program'
    program.mkdir(parents=True)
    from strategy_recipe import registry
    moved_registry = program.parents[1] / 'settings/strategy_registry.json'
    moved_registry.parent.mkdir()
    shutil.copyfile(registry.REGISTRY, moved_registry)
    config = program / 'config.txt'
    config.write_text('MAIN_ASIA=0900-1500\nMAIN_LONDON=1600-2000\nMAIN_NEWYORK=2100-2400\n', encoding='utf-8')
    with patch.object(unified_settings, 'LIVE_CONFIG', config):
        before = unified_live.strategy_settings_metadata(['SPECIAL1', 'SPECIAL2'])
        with patch.object(unified_live, 'PROGRAM', program), patch.object(registry, 'REGISTRY', moved_registry):
            relocated = unified_live.strategy_settings_metadata(['SPECIAL1', 'SPECIAL2'])
    assert relocated == before
    assert '이동한 프로젝트' not in str(relocated)
