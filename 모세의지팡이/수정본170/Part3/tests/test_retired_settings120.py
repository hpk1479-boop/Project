"""Settings: an older config.txt that still has the two retired economy file lines.

The economy worker keeps its history in the event_state folder and reads neither key. An update
keeps the user's own config.txt, so the file is not rewritten just because the lines exist.
(수정본162: that such lines are hidden and refused is checked once, generally, in
tests/test_settings_cleanup138.py.)
"""
from pathlib import Path
from types import SimpleNamespace
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / 'Part3'), str(ROOT / 'Part1/program')]
from lab import server, storage, unified_backtest, unified_settings

RETIRED = ('ECONOMY_ALERTED_FILE', 'ECONOMY_BRIEFING_FILE')
OLD_CONFIG = ('SYMBOLS=XAUUSD+\nECONOMY_ENABLED=true\nECONOMY_POLL_SEC=30\n'
              'ECONOMY_ALERTED_FILE=alerted_events.txt\nECONOMY_BRIEFING_FILE=last_briefing.txt\n')


@pytest.fixture
def config(tmp_path, monkeypatch):
    path = tmp_path / 'config.txt'
    path.write_text(OLD_CONFIG, encoding='utf-8')
    monkeypatch.setattr(unified_settings, 'LIVE_CONFIG', path)
    monkeypatch.setattr(unified_settings, 'ROOT', tmp_path)
    monkeypatch.setattr(unified_backtest, '_part2', lambda: (SimpleNamespace(settings=lambda: {}), None))
    monkeypatch.setattr(storage, 'connections', lambda: {})
    monkeypatch.setattr(server, 'ai_settings', lambda: {'provider': 'disabled'})
    return path


def test_saving_another_setting_leaves_the_old_lines_alone(config):
    unified_settings.save_live({'ECONOMY_POLL_SEC': '60'})
    assert config.read_text('utf-8') == OLD_CONFIG.replace('ECONOMY_POLL_SEC=30', 'ECONOMY_POLL_SEC=60')


def test_the_economy_worker_keeps_its_history_in_the_state_folder(tmp_path):
    from event_economy_host import EconomyWorker
    import threading
    worker = EconomyWorker({key: 'x.txt' for key in RETIRED}, threading.Event(),
                           SimpleNamespace(send=lambda text: True), state_directory=tmp_path / 'event')
    assert worker.alerted_file == tmp_path / 'event/economy_alerted.txt'
    assert worker.briefing_file == tmp_path / 'event/economy_briefing.txt'
