"""STAFF EA source checks that run without MT5 (moved from Part1/audit/test_slow_feed.py and
Part2/validation_suite/test_native_data_build.py in 수정본180).

* Indicator buffers are copied only after they are calculated; partial history and a fair timer.
* A Strategy Tester run never touches the live pipe, and live publishing never writes tester files.
"""
from pathlib import Path

EA = Path(__file__).resolve().parents[1] / 'Part1/program/MT5/THE_STAFF_OF_MOSES.mq5'


def source():
    return EA.read_text(encoding='utf-8-sig')


def test_mql_skips_uncalculated_buffers_before_copy():
    body = source().split('bool CopyOneBuffer(')[1].split('int CreateCustom(')[0]
    assert 'BarsCalculated(handle) < count' in body
    assert body.index('BarsCalculated(handle)') < body.index('CopyBuffer(handle')
    assert 'ArrayInitialize(out, EMPTY_VALUE)' in body


def test_mql_partial_history_and_fair_timer_contract():
    code = source()
    # The LIVE payload builder is shared by PublishFeed and the backtest pipe capture.
    publish = code.split('bool BuildStaffPayload(')[1].split('int OnInit()')[0]
    assert 'BuildStaffPayload(' in code.split('bool PublishFeed(')[1].split('int OnInit()')[0]
    assert 'SERIES_BARS_COUNT' in publish
    assert 'MathMin(count,available)' in publish
    assert 'if(got < 250)' in publish
    timer = code.split('void OnTimer()')[1]
    assert timer.index('g_feed_cursor=') < timer.index('PublishFeed(')
    assert 'STAFF_WORK_BUDGET_MS' in timer
    assert 'next_poll_ms' in timer
    assert 'Sleep(' not in timer


def test_unified_staff_native_export_is_backtest_only():
    staff = source()
    assert 'STAFF_NATIVE_REQUEST_FILE' in staff
    assert 'NativeBeginExport()' in staff and 'NativeWriteSnapshot(i,g_backtest_inputs[i])' in staff
    live_publish = staff[staff.index('bool PublishFeed('):staff.index('// BACKTEST INPUT SNAPSHOT')]
    assert 'NativeWriteSnapshot' not in live_publish and 'MosesDataBuild' not in live_publish
    timer = staff[staff.index('void OnTimer()'):]
    assert 'if(StaffBacktestRuntime()) return;' in timer


def test_current_staff_tester_path_does_not_use_named_pipe():
    staff = source()
    backtest = staff[staff.index('int BacktestOnInit()'):staff.index('int OnInit()')]
    assert 'NativeBeginExport()' in backtest
    assert 'EnsureStaffPipe' not in backtest and 'WritePipeSnapshot' not in backtest
    assert 'if(StaffBacktestRuntime())' in staff[staff.index('int OnInit()'):]
