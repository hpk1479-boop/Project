"""Saving LIVE strategies restarts only the running current-copy live engine."""
from pathlib import Path
import sys
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lab import live_processes, unified_live


@pytest.fixture
def bridge(monkeypatch):
    owner = {
        'save_special_settings': Mock(),
        'list_live_engines': Mock(return_value=[]),
        'start_live': Mock(return_value=(True, '준비 완료')),
    }
    monkeypatch.setattr(unified_live, '_closing', False)
    monkeypatch.setattr(unified_live, 'control', lambda: owner)
    monkeypatch.setattr(unified_live, 'specials', lambda: {
        'items': {'SPECIAL1': {}}, 'trigger_choices': []})
    monkeypatch.setattr(live_processes, 'current_process_identity', lambda: {'pid': 20, 'created': '200'})
    return owner, {'SPECIAL1': {'enabled': True, 'trigger': None, 'time_filters': None}}


def engine(pid=10, current=True):
    return {'pid': pid, 'created': str(pid * 100), 'current_copy': current}


def test_running_engine_restarts_after_new_settings_are_saved(bridge):
    owner, items = bridge
    order = []
    owner['list_live_engines'].return_value = [engine()]
    owner['save_special_settings'].side_effect = lambda settings: order.append(('save', settings))
    owner['start_live'].side_effect = lambda **options: (order.append(('restart', options)) or True, '준비 완료')
    result = unified_live.save_specials(items)
    assert result['ok'] and '재시작 완료' in result['message']
    assert order == [('save', items), ('restart', {
        'restart': True, 'expected_engines': [{'pid': 10, 'created': '1000'}],
        'ui_owner': {'pid': 20, 'created': '200'}})]


def test_stopped_monitoring_is_not_started_by_save(bridge):
    owner, items = bridge
    assert unified_live.save_specials(items)['ok']
    owner['save_special_settings'].assert_called_once_with(items)
    owner['start_live'].assert_not_called()


def test_other_copy_is_never_automatically_restarted(bridge):
    owner, items = bridge
    owner['list_live_engines'].return_value = [engine(30, False)]
    assert unified_live.save_specials(items)['ok']
    owner['start_live'].assert_not_called()


def test_current_copy_only_is_in_confirmed_restart_identities(bridge):
    owner, items = bridge
    owner['list_live_engines'].return_value = [engine(), engine(30, False)]
    unified_live.save_specials(items)
    assert owner['start_live'].call_args.kwargs['expected_engines'] == [{'pid': 10, 'created': '1000'}]


@pytest.mark.parametrize('items', [{}, {'SPECIAL1': {'enabled': 'yes'}},
    {'SPECIAL1': {'enabled': True, 'trigger': 'unknown'}}])
def test_invalid_settings_do_not_write_or_restart(bridge, items):
    owner, _ = bridge
    with pytest.raises(ValueError):
        unified_live.save_specials(items)
    owner['save_special_settings'].assert_not_called()
    owner['start_live'].assert_not_called()


def test_write_failure_keeps_existing_engine(bridge):
    owner, items = bridge
    owner['save_special_settings'].side_effect = OSError('쓰기 실패')
    with pytest.raises(OSError):
        unified_live.save_specials(items)
    owner['list_live_engines'].assert_not_called()
    owner['start_live'].assert_not_called()


def test_restart_failure_reports_saved_settings_without_false_success(bridge):
    owner, items = bridge
    owner['list_live_engines'].return_value = [engine()]
    owner['start_live'].return_value = (False, '엔진 준비 실패')
    with pytest.raises(ValueError, match='설정은 저장했지만.*재시작.*엔진 준비 실패'):
        unified_live.save_specials(items)
    owner['save_special_settings'].assert_called_once_with(items)


def test_changed_process_list_does_not_become_automatic_confirmation(bridge, monkeypatch):
    owner, items = bridge
    owner['list_live_engines'].return_value = [engine()]
    monkeypatch.setattr(unified_live, 'start', lambda request: {
        'ok': False, 'confirmation_required': True, 'message': '엔진 목록 변경'})
    with pytest.raises(ValueError, match='설정은 저장했지만.*엔진 목록 변경'):
        unified_live.save_specials(items)
    owner['start_live'].assert_not_called()


def test_window_shutdown_blocks_save_and_restart(bridge, monkeypatch):
    owner, items = bridge
    monkeypatch.setattr(unified_live, '_closing', True)
    with pytest.raises(ValueError, match='종료 중'):
        unified_live.save_specials(items)
    owner['save_special_settings'].assert_not_called()
    owner['start_live'].assert_not_called()


def test_restart_shutdown_warning_is_preserved(bridge):
    owner, items = bridge
    class Result(tuple):
        warnings = ['종료 제한시간 후 강제 종료']
    owner['list_live_engines'].return_value = [engine()]
    owner['start_live'].return_value = Result((True, '준비 완료'))
    assert unified_live.save_specials(items)['warnings'] == Result.warnings
