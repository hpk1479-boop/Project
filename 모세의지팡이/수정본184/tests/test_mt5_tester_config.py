"""Strategy Tester settings written for a recording (moved from Part2/validation_suite/test_native_data_build.py in 수정본180).

event_backtest.recording starts every recording piece through native_mt5.run_native_tester, which writes
this config and request first.
"""
from datetime import datetime, timezone
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'Part2'))
from generic_backtest.native_mt5 import _write_request, write_tester_config


def test_tester_config_uses_real_ticks_and_unified_staff(tmp_path):
    profile = {'data_root': str(tmp_path / 'data')}
    p = write_tester_config(tmp_path / 'tester.ini', profile, 'XAUUSD', 0, 2 * 86400 * 10**9, 'THE_STAFF_OF_MOSES')
    text = p.read_text('ascii')
    assert 'Expert=THE_STAFF_OF_MOSES' in text
    assert 'Symbol=XAUUSD' in text and 'Period=M1' in text
    assert 'Model=4' in text and 'Optimization=0' in text and 'Visual=0' in text
    p2 = write_tester_config(tmp_path / 'tester_shutdown.ini', profile, 'XAUUSD', 0, 2 * 86400 * 10**9,
                             'THE_STAFF_OF_MOSES', shutdown_terminal=True)
    assert 'ShutdownTerminal=1' in p2.read_text('ascii')


def test_selected_two_days_are_not_expanded_to_seed_period(tmp_path):
    def ns(date):
        return int(datetime.fromisoformat(date).replace(tzinfo=timezone.utc).timestamp()) * 10**9
    start, end = ns('2026-09-22T15:00:00'), ns('2026-09-24T15:00:00')
    path = write_tester_config(tmp_path / 'test.ini', {}, 'XAUUSD+', start, end, 'STAFF')
    text = path.read_text('ascii')
    assert 'FromDate=2026.09.22' in text
    assert 'ToDate=2026.09.25' in text  # cover final partial UTC day
    assert '2024' not in text
    request = _write_request(tmp_path / 'common', 'test_session', 'XAUUSD+', start, end)
    lines = request.read_text('ascii').splitlines()
    assert list(map(int, lines[3:])) == [start // 10**9, end // 10**9]
    assert int(lines[4]) - int(lines[3]) == 2 * 86400
