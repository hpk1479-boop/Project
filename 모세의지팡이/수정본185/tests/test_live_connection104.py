"""Offline regressions for connection cards independent of watch activity."""
from copy import deepcopy
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
PROGRAM = ROOT / 'Part1' / 'program'
sys.path.insert(0, str(PROGRAM))
import module_diagnostics
from module_status_state import MAIN_MODULES, display_state


def connected_snapshot(status='정상'):
    return {
        'modules': {name: {'status': status, 'monitoring': False,
                           'last': None, 'errors': 0} for name in MAIN_MODULES},
        'pipe_connected': True,
        'pipe_seen': True,
        'telegram_attempted': True,
        'telegram_connected': True,
    }


@pytest.fixture(autouse=True)
def forbid_network(monkeypatch):
    import socket

    def denied(*args, **kwargs):
        raise AssertionError('This regression must not contact a real server')

    for name in ('connect', 'connect_ex', 'sendto'):
        monkeypatch.setattr(socket.socket, name, denied)


@pytest.mark.parametrize('module', MAIN_MODULES)
@pytest.mark.parametrize('status', ('정상', '대기'))
def test_connected_idle_modules_are_ready_without_processing_history(module, status):
    data = connected_snapshot(status)
    assert display_state(module, data) == '연결중'
    data['modules'][module].update(monitoring=True, last=123)
    assert display_state(module, data) == '연결중'


@pytest.mark.parametrize('module', MAIN_MODULES)
def test_host_not_running_stays_waiting(module):
    assert display_state(module, None) == '연결 대기'
    assert display_state(module, None, OSError('host stopped')) == '연결 대기'


@pytest.mark.parametrize('module', ('KIM', 'WATCH'))
def test_telegram_dependency_waits_before_attempt_and_reports_failure(module):
    data = connected_snapshot()
    data.update(telegram_attempted=False, telegram_connected=False)
    data['modules'][module].update(monitoring=True, last=123, status='지연')
    assert display_state(module, data) == '연결 대기'
    data['telegram_attempted'] = True
    assert display_state(module, data) == '오류'
    data.update(telegram_connected=True)
    data['modules'][module]['status'] = '정상'
    assert display_state(module, data) == '연결중'


def test_kim_telegram_is_independent_of_mt5_but_watch_requires_both():
    data = connected_snapshot()
    data.update(pipe_connected=False, pipe_seen=False)
    assert display_state('KIM', data) == '연결중'
    assert display_state('WATCH', data) == '연결 대기'
    assert display_state('INDICATOR', data) == '연결 대기'
    data.update(pipe_seen=True)
    assert display_state('STAFF', data) == '오류'
    assert display_state('KIM', data) == '연결중'


@pytest.mark.parametrize('module', MAIN_MODULES)
@pytest.mark.parametrize('status', ('지연', '오류·끊김', '끊김', '오류'))
def test_current_processing_error_is_visible_even_when_connections_succeed(module, status):
    data = connected_snapshot()
    data['modules'][module]['status'] = status
    # The engine reports '지연' while it catches up; only the other modules treat a delay as an error.
    assert display_state(module, data) == ('연결중' if (module, status) == ('ENGINE', '지연') else '오류')
    data['modules'][module].update(status='정상', errors=5)
    assert display_state(module, data) == '연결중'


def test_connection_projection_does_not_change_engine_snapshot():
    data = connected_snapshot('대기')
    before = deepcopy(data)
    assert {display_state(name, data) for name in MAIN_MODULES} == {'연결중'}
    assert data == before


@pytest.mark.parametrize('name,module', (('WATCH_CONDITIONS', 'WATCH'), ('INDICATOR', 'INDICATOR')))
def test_empty_and_registered_watch_counts_stay_in_core_logs_without_changing_connection(
        tmp_path, monkeypatch, name, module):
    monkeypatch.setattr(module_diagnostics, 'module_names', lambda: MAIN_MODULES)
    sink = module_diagnostics.Diagnostics(tmp_path)
    event = SimpleNamespace(kind=SimpleNamespace(value='MARKET_BUNDLE'),
                            payload={'symbol': 'OFFLINE'}, engine_seq=1)
    watches = {}
    state = ({'runtime': SimpleNamespace(controller=SimpleNamespace(_watches=watches))}
             if module == 'WATCH' else {'watches': watches})
    try:
        sink.pipe_state(True)
        sink.telegram_state(True)
        sink.begin(name)
        sink.success(name, event, state)
        empty = sink.snapshot(module)
        assert empty['modules'][module]['monitoring'] is False
        assert any('등록 감시 없음' in line for _, line in empty['lines'])
        assert display_state(module, empty) == '연결중'
        sink.begin(name)
        sink.success(name, event, state)
        assert sink.snapshot(module)['lines'] == empty['lines']
        watches['offline-condition'] = object()
        sink.begin(name)
        sink.success(name, event, state)
        active = sink.snapshot(module)
        assert active['modules'][module]['monitoring'] is True
        assert any('감시 1건' in line for _, line in active['lines'])
        assert display_state(module, active) == '연결중'
    finally:
        sink.close()


def test_actual_unconfigured_telegram_warning_does_not_turn_kim_or_watch_red(tmp_path, monkeypatch):
    monkeypatch.setattr(module_diagnostics, 'module_names', lambda: MAIN_MODULES)
    sink = module_diagnostics.Diagnostics(tmp_path)
    try:
        sink.pipe_state(True)
        sink.telegram_unconfigured()
        data = sink.snapshot('ALL')
        assert data['modules']['KIM']['status'] == '지연'
        assert any('텔레그램 토큰 미설정' in line for _, line in data['lines'])
        assert display_state('KIM', data) == display_state('WATCH', data) == '연결 대기'
        assert display_state('INDICATOR', data) == '연결중'
    finally:
        sink.close()
