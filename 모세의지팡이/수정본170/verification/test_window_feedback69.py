"""Closing feedback must stay responsive without bypassing engine cleanup."""
from __future__ import annotations

import sys
import threading
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'Part3'))
from lab import desktop_window, unified_live
from lab import backtest_jobs, catalog, runtime_lifecycle
from lab.ai.model_runtime import RUNTIME
from common_ai import client as common_client


@pytest.fixture(autouse=True)
def isolated_runtime(monkeypatch, tmp_path):
    """Lifecycle feedback tests never inspect or stop user jobs/models."""
    monkeypatch.setattr(catalog, 'ROOT', tmp_path / 'project' / 'Part3')
    monkeypatch.setattr(common_client, 'Client', Mock(return_value=Mock(spec=['shutdown', 'close'])))
    monkeypatch.setattr(desktop_window, '_show_close_error', Mock())
    monkeypatch.setattr(desktop_window, '_show_close_warning', Mock())
    monkeypatch.setattr(runtime_lifecycle, 'prepare_shutdown', lambda: None)
    monkeypatch.setattr(desktop_window, '_confirm_force_close', Mock(return_value=False))
    monkeypatch.setattr(backtest_jobs, 'shutdown', lambda **_kwargs: {'ok': True, 'warnings': []})
    monkeypatch.setattr(RUNTIME, 'shutdown', lambda **_kwargs: None)
    monkeypatch.setattr(backtest_jobs, '_closing', False)
    monkeypatch.setattr(backtest_jobs, '_SESSION_CONTEXTS', {}, raising=False)
    monkeypatch.setattr(backtest_jobs, '_SHUTDOWN_PENDING', {}, raising=False)


def test_slow_close_reports_progress_and_preserves_window_until_saved():
    entered, release, title_shown, footer_shown = (threading.Event() for _ in range(4))
    window = Mock()
    window.set_title.side_effect = lambda title: title_shown.set()
    window.evaluate_js.side_effect = lambda script: footer_shown.set()
    closer = desktop_window._EngineCloser(window)

    def shutdown():
        entered.set()
        assert release.wait(5)
        return {'ok': True, 'warnings': []}

    with patch.object(unified_live, 'shutdown', side_effect=shutdown) as stop:
        try:
            assert closer.closing() is False
            assert entered.wait(5)
            assert title_shown.wait(5) and footer_shown.wait(5)
            assert '종료 확인 중' in window.set_title.call_args.args[0]
            assert '기록과 결과를 저장' in window.evaluate_js.call_args.args[0]
            assert 'mosesWindowClosing' in window.evaluate_js.call_args.args[0]
            assert closer.closing() is False
            window.destroy.assert_not_called()
        finally:
            release.set()
            closer.thread.join(5)
        assert closer.ready and not closer.thread.is_alive()
        assert closer.closing() is True
        stop.assert_called_once()
        window.destroy.assert_called_once()


def test_blocked_feedback_cannot_block_engine_shutdown_or_window_close():
    feedback_entered, feedback_release = threading.Event(), threading.Event()
    window = Mock()

    def blocked_title(title):
        feedback_entered.set()
        assert feedback_release.wait(5)

    window.set_title.side_effect = blocked_title
    closer = desktop_window._EngineCloser(window)
    with patch.object(unified_live, 'shutdown', return_value={'ok': True}) as stop:
        try:
            assert closer.closing() is False
            assert feedback_entered.wait(5)
            closer.thread.join(2)
            assert not closer.thread.is_alive()
            assert closer.ready
            stop.assert_called_once()
            window.destroy.assert_called_once()
        finally:
            feedback_release.set()


def test_feedback_errors_do_not_prevent_cleanup():
    window = Mock()
    window.set_title.side_effect = RuntimeError('window not ready')
    window.evaluate_js.side_effect = RuntimeError('page already unloading')
    closer = desktop_window._EngineCloser(window)
    with patch.object(unified_live, 'shutdown', return_value={'ok': True}) as stop:
        assert closer.closing() is False
        closer.thread.join(5)
        assert closer.ready
        stop.assert_called_once()
        window.destroy.assert_called_once()


def test_failed_close_keeps_window_restores_feedback_and_can_retry():
    feedback_failed = threading.Event()
    window = Mock()
    window.evaluate_js.side_effect = lambda script: feedback_failed.set() if '다시 시도' in script else None
    closer = desktop_window._EngineCloser(window)
    with patch.object(unified_live, 'shutdown', side_effect=[RuntimeError('확인 실패'), {'ok': True}]) as stop, \
            patch.object(desktop_window, '_show_close_error') as report:
        assert closer.closing() is False
        closer.thread.join(5)
        assert feedback_failed.wait(5)
        assert not closer.ready
        window.destroy.assert_not_called()
        report.assert_called_once_with('확인 실패')
        assert window.set_title.call_args.args[0] == 'THE STAFF OF MOSES'
        assert closer.closing() is False
        closer.thread.join(5)
        assert closer.ready
        assert stop.call_count == 2
        window.destroy.assert_called_once()


def test_previous_whole_stop_does_not_skip_fresh_close_verification():
    closer = desktop_window._EngineCloser(Mock())
    with patch.object(unified_live, 'stop', return_value={'ok': True}) as whole_stop, \
            patch.object(unified_live, 'shutdown', return_value={'ok': True}) as shutdown:
        unified_live.stop()
        assert closer.closing() is False
        closer.thread.join(5)
        whole_stop.assert_called_once()
        shutdown.assert_called_once()
        assert closer.ready
