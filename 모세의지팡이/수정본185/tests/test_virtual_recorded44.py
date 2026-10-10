"""Exercise real virtual readers and CSV writers with newly generated recordings."""
from pathlib import Path
import csv
import shutil
import socket
import sys
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'build/optimization44'))
from support44 import recorded_fixture, clean_result
from event_backtest.virtual_entry import calculate


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('No external service may be used in fixture checks.')
    for name in ('connect', 'connect_ex', 'sendto'):
        monkeypatch.setattr(socket.socket, name, forbidden)
    monkeypatch.setattr(socket, 'create_connection', forbidden)


@pytest.mark.parametrize('format,published', [('MSD1', False), ('MSD2', False), ('MSD2', True)])
def test_recording_to_virtual_csv_including_forming_row_and_heartbeat(tmp_path, format, published):
    path, captures, scenario, packets = recorded_fixture(tmp_path, format=format, published=published)
    out = tmp_path / 'runs' / 'synthetic'
    out.mkdir(parents=True)
    result = calculate(path, captures, tmp_path, scenario, {}, out)
    assert result['read_bundles'] == 7 and result['processed_signals'] == 2
    assert result['observed_signals'] == 2 and not result['cancelled']
    assert result['last_closed_m1_ms'] == 1_756_684_980_000
    with (tmp_path / result['trades_csv']).open(encoding='utf-8-sig') as stream:
        rows = list(csv.DictReader(stream))
    assert [r['signal_id'] for r in rows] == ['first'] * 9 + ['second'] * 9
    assert [r['result'] for r in rows[:9]] == ['WIN'] * 3 + ['LOSS'] * 6
    assert all(r['result'] == 'LOSS' and r['exit_time'] == '1756684860000' for r in rows[9:])
    assert all(r['entry_time'] == '1756684860000' for r in rows)
    assert all(r['exit_time'] == '1756684980000' for r in rows[3:9])
    assert [s['wins'] for s in result['summary']] == [1, 1, 1, 0, 0, 0, 0, 0, 0]
    assert [s['losses'] for s in result['summary']] == [1, 1, 1, 2, 2, 2, 2, 2, 2]
    assert not any(s['unclosed'] for s in result['summary'])
    if format == 'MSD2' and published:
        assert result['reader_metrics'] == {'date_restores': 1, 'delta_records': 7}
    else:
        assert not result['reader_metrics']


def test_relocated_warehouse_uses_only_relative_capture_and_result_paths(tmp_path):
    first = tmp_path / 'before'
    path, captures, scenario, _ = recorded_fixture(first)
    out = first / 'runs' / 'same-run'
    out.mkdir(parents=True)
    a = calculate(path, captures, first, scenario, {}, out)
    original_csv = {name: (out / name).read_bytes() for name in ('virtual_summary.csv', 'virtual_trades.csv')}
    after = tmp_path / 'moved' / 'warehouse'
    after.parent.mkdir()
    shutil.move(str(first), str(after))
    b = calculate(after / 'alerts.csv', captures, after, scenario, {}, after / 'runs' / 'same-run')
    assert clean_result(a) == clean_result(b)
    assert a['summary_csv'] == b['summary_csv'] == 'runs/same-run/virtual_summary.csv'
    assert a['trades_csv'] == b['trades_csv'] == 'runs/same-run/virtual_trades.csv'
    for name, data in original_csv.items():
        assert (after / 'runs' / 'same-run' / name).read_bytes() == data


def test_real_reader_cancel_after_entry_keeps_unclosed_rows(tmp_path):
    path, captures, scenario, _ = recorded_fixture(tmp_path)
    out = tmp_path / 'run'
    out.mkdir()
    stopped = [False]
    # Cancel on the stream's next check after the forming minute reaches entry.
    from event_backtest import virtual_entry
    original = virtual_entry.VirtualEntry.observe
    from unittest.mock import patch
    def observing(self, stamp, feeds, **kwargs):
        result = original(self, stamp, feeds, **kwargs)
        if stamp >= 1_756_684_860_000:
            stopped[0] = True
        return result
    with patch.object(virtual_entry.VirtualEntry, 'observe', observing):
        result = calculate(path, captures, tmp_path, scenario, {}, out, cancel=lambda: stopped[0])
    assert result['cancelled'] and result['observed_signals'] == 2 and result['processed_signals'] == 0
    with (out / 'virtual_trades.csv').open(encoding='utf-8-sig') as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) == 18
    assert all(row['result'] == 'UNCLOSED' and row['target'] == '' for row in rows[:9])
    assert all(row['result'] == 'WAITING' for row in rows[9:])
