"""Nonzero synthetic LIVE/replay checks; external services are never exercised."""
from pathlib import Path
import socket
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'build/optimization43'))
from probe_integration import direct_probe, wire_probe


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('network is not permitted in synthetic tests')
    for name in ('connect', 'connect_ex', 'sendto'):
        monkeypatch.setattr(socket.socket, name, forbidden)
    monkeypatch.setattr(socket, 'create_connection', forbidden)


def test_direct_unwrapped_wrapped_and_replay_keep_decisions_and_b0(tmp_path):
    result = direct_probe(tmp_path)
    assert result['input_count'] == 12 and result['csv_row_count'] == 4
    assert result['unwrapped_vs_wrapped_decisions_equal']


def test_real_wire_transports_keep_decisions_and_csv_fields(tmp_path):
    result = wire_probe(tmp_path)
    assert result['input_count'] == 6 and result['notification_count'] == 2
    assert result['live_transport_vs_replay_transport_equal']
