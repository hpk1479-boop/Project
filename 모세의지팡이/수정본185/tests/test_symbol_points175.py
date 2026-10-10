"""수정본175: every configured symbol has a 1-point price field on the settings screen.

Only the virtual-entry spread uses the price (spread points × point, Part2 virtual_entry.pricing). A symbol
whose price is not in config.txt yet shows an empty field; its first save adds the line and keeps every
other line as it was.
"""
from __future__ import annotations

from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'Part3'), str(ROOT / 'Part2'), str(ROOT / 'Part1/program')]
from lab import unified_settings as settings

BEFORE = 'SYMBOLS=XAUUSD+,NAS100\n# Virtual entry point: user confirmed\nPOINT_XAUUSD+=0.01\n'


@pytest.fixture
def config(tmp_path, monkeypatch):
    path = tmp_path / 'config.txt'
    path.write_text(BEFORE, encoding='utf-8')
    monkeypatch.setattr(settings, 'LIVE_CONFIG', path)
    return path


def points():
    return {row['key']: row['value'] for row in settings.read()['live'] if row['key'].startswith('POINT_')}


def test_each_symbol_shows_its_price_or_an_empty_field(config):
    assert points() == {'POINT_XAUUSD+': '0.01', 'POINT_NAS100': ''}
    assert config.read_text('utf-8') == BEFORE                        # showing writes nothing


def test_the_first_save_adds_the_line_and_keeps_the_others(config):
    settings.save_live({'POINT_NAS100': '0.01'})
    assert config.read_text('utf-8') == BEFORE + 'POINT_NAS100=0.01\n'
    assert points() == {'POINT_XAUUSD+': '0.01', 'POINT_NAS100': '0.01'}
    settings.save_live({'POINT_NAS100': '0.1'})                       # later saves change that line
    assert config.read_text('utf-8') == BEFORE + 'POINT_NAS100=0.1\n'


@pytest.mark.parametrize('value', ['0', '-0.01', 'abc', '', 'nan'])
def test_a_price_must_be_a_positive_number(config, value):
    with pytest.raises(ValueError):
        settings.save_live({'POINT_NAS100': value})
    assert config.read_text('utf-8') == BEFORE


def test_only_configured_symbols_whose_name_fits_a_settings_line(config):
    config.write_text('SYMBOLS=XAUUSD+,US30.cash\n', encoding='utf-8')
    assert points() == {'POINT_XAUUSD+': ''}                         # "POINT_US30.cash" is no KEY=VALUE line here
    for key in ('POINT_US30.cash', 'POINT_NAS100'):                  # not writable / not a configured symbol
        with pytest.raises(ValueError, match='저장할 수 없는 설정'):
            settings.save_live({key: '0.01'})


def test_the_saved_price_turns_the_spread_into_a_price(config):
    from event_backtest.virtual_entry import pricing
    from event_composer_domain import load_config
    scenario = {'symbol': 'NAS100', 'spread_points': {'NAS100': 150}}
    with pytest.raises(ValueError, match=r'종목 point \(NAS100\)'):
        pricing(scenario, [], load_config(str(config)))
    settings.save_live({'POINT_NAS100': '0.01'})
    priced = pricing(scenario, [], load_config(str(config)))
    assert priced['point'] == 0.01 and priced['point_source'] == 'config'
    assert priced['spread_price'] == pytest.approx(1.5)
