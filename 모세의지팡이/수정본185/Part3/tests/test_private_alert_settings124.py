"""Settings: 개인채팅 라이브 알림 / 개인채팅 경제지표 알림 are 받기 / 받지 않음, off by default.

An older config.txt has no such lines: they show as off and are added only when saved.
"""
from pathlib import Path
from types import SimpleNamespace
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / 'Part3'), str(ROOT / 'Part1/program')]
from lab import server, storage, unified_backtest, unified_settings

KEYS = ('PRIVATE_LIVE_ALERTS_ENABLED', 'PRIVATE_ECONOMY_ALERTS_ENABLED')
OLD_CONFIG = 'SYMBOLS=XAUUSD+\nECONOMY_ENABLED=true\n'


@pytest.fixture
def config(tmp_path, monkeypatch):
    path = tmp_path / 'config.txt'
    monkeypatch.setattr(unified_settings, 'LIVE_CONFIG', path)
    monkeypatch.setattr(unified_settings, 'ROOT', tmp_path)
    monkeypatch.setattr(unified_backtest, '_part2', lambda: (SimpleNamespace(settings=lambda: {}), None))
    monkeypatch.setattr(storage, 'connections', lambda: {})
    monkeypatch.setattr(server, 'ai_settings', lambda: {'provider': 'disabled'})
    return path


def value(key):
    return {row['key']: row['value'] for row in unified_settings.read()['live']}[key]


@pytest.mark.parametrize('key', KEYS)
def test_an_older_config_shows_off_and_is_not_touched(config, key):
    config.write_text(OLD_CONFIG, encoding='utf-8')
    before = config.read_bytes()
    assert value(key) == 'false'
    assert config.read_bytes() == before


@pytest.mark.parametrize('key', KEYS)
def test_turning_it_on_adds_one_line(config, key):
    config.write_text(OLD_CONFIG, encoding='utf-8')
    unified_settings.save_live({key: 'true'})
    assert config.read_text('utf-8') == OLD_CONFIG + key + '=true\n'
    assert value(key) == 'true'


@pytest.mark.parametrize('key', KEYS)
@pytest.mark.parametrize('bad', ['받기', 'yes', '1', ''])
def test_only_true_or_false_is_accepted(config, key, bad):
    config.write_text(OLD_CONFIG, encoding='utf-8')
    before = config.read_bytes()
    with pytest.raises(ValueError):
        unified_settings.save_live({key: bad})
    assert config.read_bytes() == before
