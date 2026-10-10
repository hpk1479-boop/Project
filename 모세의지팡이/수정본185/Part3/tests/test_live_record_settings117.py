"""Settings: LIVE alert recording is 사용 / 사용 안 함, and there is no folder to choose.

An update keeps the user's own config.txt, so an older file has no LIVE_RECORD_ENABLED line yet:
the setting shows its default and the line is added the first time it is saved.
"""
from pathlib import Path
from types import SimpleNamespace
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / 'Part3'), str(ROOT / 'Part1/program')]
from lab import server, storage, unified_backtest, unified_settings

OLD_CONFIG = 'SYMBOLS=XAUUSD+\nECONOMY_ENABLED=true\nMAIN_ASIA=0900-1800'      # no trailing newline, no new key
NEW_CONFIG = 'SYMBOLS=XAUUSD+\nLIVE_RECORD_ENABLED=true\nECONOMY_ENABLED=true\n'


@pytest.fixture
def config(tmp_path, monkeypatch):
    path = tmp_path / 'config.txt'
    monkeypatch.setattr(unified_settings, 'LIVE_CONFIG', path)
    monkeypatch.setattr(unified_settings, 'ROOT', tmp_path)
    monkeypatch.setattr(unified_backtest, '_part2', lambda: (SimpleNamespace(settings=lambda: {}), None))
    monkeypatch.setattr(storage, 'connections', lambda: {})
    monkeypatch.setattr(server, 'ai_settings', lambda: {'provider': 'disabled'})
    return path


def live(row_key='LIVE_RECORD_ENABLED'):
    return {row['key']: row for row in unified_settings.read()['live']}.get(row_key)


def test_an_older_config_shows_the_default_without_being_touched(config):
    config.write_text(OLD_CONFIG, encoding='utf-8')
    before = config.read_bytes()
    assert live()['value'] == 'true'
    assert config.read_bytes() == before


def test_saving_adds_the_line_and_leaves_every_other_line_alone(config):
    config.write_text(OLD_CONFIG, encoding='utf-8')
    unified_settings.save_live({'LIVE_RECORD_ENABLED': 'false'})
    assert config.read_text('utf-8') == OLD_CONFIG + '\nLIVE_RECORD_ENABLED=false\n'
    assert live()['value'] == 'false'
    unified_settings.save_live({'LIVE_RECORD_ENABLED': 'true'})
    assert config.read_text('utf-8') == OLD_CONFIG + '\nLIVE_RECORD_ENABLED=true\n'


def test_a_config_that_has_the_line_is_edited_in_place(config):
    config.write_text(NEW_CONFIG, encoding='utf-8')
    assert live()['value'] == 'true'
    unified_settings.save_live({'LIVE_RECORD_ENABLED': 'false'})
    assert config.read_text('utf-8') == NEW_CONFIG.replace('LIVE_RECORD_ENABLED=true', 'LIVE_RECORD_ENABLED=false')


def test_saving_the_other_settings_does_not_add_the_line(config):
    config.write_text(OLD_CONFIG, encoding='utf-8')
    unified_settings.save_live({'ECONOMY_ENABLED': 'false'})
    assert 'LIVE_RECORD_ENABLED' not in config.read_text('utf-8')


@pytest.mark.parametrize('value', ['켜기', 'yes', '1', '', None])
def test_only_true_or_false_is_accepted(config, value):
    config.write_text(NEW_CONFIG, encoding='utf-8')
    before = config.read_bytes()
    with pytest.raises(ValueError):
        unified_settings.save_live({'LIVE_RECORD_ENABLED': value})
    assert config.read_bytes() == before


def test_an_unknown_key_is_still_refused(config):
    config.write_text(OLD_CONFIG, encoding='utf-8')
    with pytest.raises(ValueError):
        unified_settings.save_live({'LIVE_RECORD_FOLDER': 'x'})
