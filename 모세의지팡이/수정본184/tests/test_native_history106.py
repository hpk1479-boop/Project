"""Only fresh, symbol-scoped Tester evidence may bound an unavailable tail."""
from datetime import date, datetime, timezone
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import patch
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'Part2'), str(ROOT / 'Part1/program')]

from generic_backtest.history import tester_evidence as evidence
from generic_backtest.history.tester_evidence import NativeHistoryUnavailable
from generic_backtest import native_mt5 as native
from event_backtest.terminal_lifecycle import launch_once_retry


def ns(day):
    return int(datetime.fromisoformat(day).replace(tzinfo=timezone.utc).timestamp()) * 10**9


START, END = ns('2026-10-03'), ns('2026-10-04')
ACTUAL_LINES = [
    'RM\t0\t23:44:31.102\tTester\tXAUUSD+: found history data from 2018.11.20 00:00 to 2026.10.02 00:00, specified period is out of this range',
    'LS\t3\t23:44:31.102\tTester\tXAUUSD+: no history data from 2026.10.03 00:00 to 2026.10.04 00:00',
    'CP\t3\t23:44:31.102\tTester\tno history data, stop testing',
]


def classify(lines=ACTUAL_LINES, **kwargs):
    return evidence.history_unavailable(lines, 'XAUUSD+', START, END,
                                        today=date(2026, 10, 4), **kwargs)


def test_real_terminal_diagnostic_preserves_last_available_whole_day():
    error = classify()
    assert isinstance(error, NativeHistoryUnavailable)
    assert (error.symbol, error.start_ns, error.end_ns) == ('XAUUSD+', START, END)
    assert (error.start, error.end) == ('2026-10-03', '2026-10-04')
    assert error.available_start == '2018-11-20'
    assert error.available_end == '2026-10-03'  # Includes Friday, excludes Saturday.
    assert 'DID_NOT_START' not in str(error) and '이미 켜진 MT5' not in str(error)
    assert len(error.evidence) == 2


@pytest.mark.parametrize('symbol', ['EURUSD', 'XAUUSD', 'XAUUSD++', 'BTCUSD'])
def test_other_symbol_cannot_approve_this_request(symbol):
    assert classify([line.replace('XAUUSD+', symbol) for line in ACTUAL_LINES]) is None


@pytest.mark.parametrize('first,last', [('2026.10.01', '2026.10.02'),
                                       ('2026.10.03', '2026.10.05')])
def test_other_request_range_cannot_approve_this_request(first, last):
    lines = [ACTUAL_LINES[0], 'XAUUSD+: no history data from ' + first + ' 00:00 to ' + last + ' 00:00']
    assert classify(lines) is None


@pytest.mark.parametrize('failure', [
    'XAUUSD+,M1: history synchronization failed',
    'XAUUSD+: download failed',
    'connection failed',
    'history data error',
    'failed to download XAUUSD+ history',
])
def test_download_or_sync_failure_diagnoses_history_without_tail_claim(failure):
    error = classify([*ACTUAL_LINES, failure])
    assert isinstance(error, NativeHistoryUnavailable)
    assert error.available_start is None and error.available_end is None


@pytest.mark.parametrize('line', [
    'XAUUSD+: no history data',
    'XAUUSD+: no history data, stop testing',
])
def test_range_free_history_absence_is_meaningful_and_cannot_shorten(line):
    error = classify([line])
    assert isinstance(error, NativeHistoryUnavailable)
    assert error.available_start is None and error.available_end is None
    assert '이력이 없습니다' in str(error)


def test_no_available_range_does_not_invent_a_holiday():
    error = classify(ACTUAL_LINES[1:])
    assert isinstance(error, NativeHistoryUnavailable) and error.available_end is None


def test_available_range_report_must_precede_this_missing_request():
    error = classify([ACTUAL_LINES[1], ACTUAL_LINES[0]])
    assert isinstance(error, NativeHistoryUnavailable) and error.available_end is None


def test_past_interior_missing_date_is_not_a_tail():
    lines = [ACTUAL_LINES[0].replace('2026.10.02', '2026.10.05'), ACTUAL_LINES[1]]
    error = evidence.history_unavailable(lines, 'XAUUSD+', START, END,
                                         today=date(2026, 10, 7))
    assert error.available_end == '2026-10-06'
    assert error.start < error.available_end  # Recording must retain its failure.


def test_latest_available_day_never_includes_the_current_incomplete_day():
    lines = [ACTUAL_LINES[0].replace('2026.10.02', '2026.10.04'), ACTUAL_LINES[1]]
    assert classify(lines).available_end == '2026-10-04'


@pytest.mark.parametrize('encoding', ['utf-8-sig', 'utf-16'])
def test_append_offsets_exclude_stale_ranges_and_handle_journal_encoding(tmp_path, encoding):
    profile = {'data_root': str(tmp_path)}
    folder = tmp_path / 'Tester/logs'
    folder.mkdir(parents=True)
    log = folder / '20261004.log'
    log.write_text('\n'.join(ACTUAL_LINES) + '\n', encoding=encoding)
    before = evidence.journal_positions(profile)
    assert evidence.fresh_lines(profile, before) == []
    with log.open('a', encoding=encoding) as stream:
        stream.write('fresh launch line\n')
    assert evidence.fresh_lines(profile, before) == ['fresh launch line']
    assert classify(evidence.fresh_lines(profile, before)) is None


def test_truncated_or_global_agent_log_cannot_supply_fresh_proof(tmp_path):
    profile = {'data_root': str(tmp_path)}
    folder = tmp_path / 'Tester/logs'
    folder.mkdir(parents=True)
    log = folder / '20261004.log'
    log.write_text('old contents' * 200, encoding='utf-8')
    before = evidence.journal_positions(profile)
    log.write_text('\n'.join(ACTUAL_LINES), encoding='utf-8')
    agent = tmp_path / 'Tester/Agent-127.0.0.1-3000/logs'
    agent.mkdir(parents=True)
    (agent / '20261004.log').write_text('\n'.join(ACTUAL_LINES), encoding='utf-8')
    assert evidence.fresh_lines(profile, before) == []


def test_new_rotated_journal_is_read_as_fresh(tmp_path):
    profile = {'data_root': str(tmp_path)}
    before = evidence.journal_positions(profile)
    folder = tmp_path / 'Tester/logs'
    folder.mkdir(parents=True)
    (folder / '20261005.log').write_text('\n'.join(ACTUAL_LINES), encoding='utf-16')
    assert classify(evidence.fresh_lines(profile, before)).available_end == '2026-10-03'


def native_launch(tmp_path, *, journal_lines=(), complete=False, active=False, stale=False,
                  symbol='XAUUSD+'):
    executable = tmp_path / 'terminal64.exe'
    executable.touch()
    profile = {'executable': str(executable), 'data_root': str(tmp_path / 'terminal'),
               'account_server': 'test-server'}
    journal = Path(profile['data_root']) / 'Tester/logs/20261004.log'
    journal.parent.mkdir(parents=True)
    if stale:
        journal.write_text('\n'.join(journal_lines) + '\n', encoding='utf-16')
    calls = []

    def process(args, **kwargs):
        calls.append(args)
        if not stale and journal_lines:
            with journal.open('a', encoding='utf-16') as stream:
                stream.write('\n'.join(journal_lines) + '\n')
        if complete or active:
            request = tmp_path / 'common/MosesDataBuild/native_request.txt'
            session = request.read_text('ascii').splitlines()[1]
            output = request.parent / session
            output.mkdir()
            (output / 'started.txt').touch()
            if complete:
                (output / 'complete.txt').touch()
                (output / 'manifest.tsv').write_text('pipe_feed\t' + symbol + '\t1m\tpipe.bin\t1\n', encoding='ascii')
        return SimpleNamespace(poll=lambda: 0)

    clock = [0.]
    patches = [
        patch.object(native, 'common_files_root', return_value=tmp_path / 'common'),
        patch.object(native, 'locate_compiled_expert', return_value=tmp_path / 'ea.ex5'),
        patch.object(native, 'tester_expert_name', return_value='THE_STAFF_OF_MOSES'),
        patch.object(native.subprocess, 'Popen', process),
        patch.object(native.time, 'monotonic', lambda: clock[0]),
        patch.object(native.time, 'sleep', lambda seconds: clock.__setitem__(0, clock[0] + seconds)),
    ]

    def run():
        from contextlib import ExitStack
        with ExitStack() as scope:
            for context in patches:
                scope.enter_context(context)
            return launch_once_retry(lambda: native.run_native_tester(profile, symbol, START, END,
                tmp_path / 'work', capture_only=True, shutdown_terminal=True), profile,
                wait=lambda *args, **kwargs: None)
    return run, calls, profile


def test_fresh_no_history_exits_once_without_export_or_startup_retry(tmp_path):
    run, calls, profile = native_launch(tmp_path, journal_lines=ACTUAL_LINES)
    with pytest.raises(NativeHistoryUnavailable) as caught:
        run()
    assert len(calls) == 1
    assert caught.value.available_end == '2026-10-03'
    assert not (tmp_path / 'common/MosesDataBuild/native_request.txt').exists()
    assert not list((tmp_path / 'common').rglob('complete.txt'))


def test_stale_no_history_does_not_override_real_startup_failure(tmp_path):
    run, calls, profile = native_launch(tmp_path, journal_lines=ACTUAL_LINES, stale=True)
    with pytest.raises(RuntimeError, match='DID_NOT_START'):
        run()
    assert len(calls) == 2


def test_started_export_cannot_be_disguised_as_normal_history_tail(tmp_path):
    run, calls, profile = native_launch(tmp_path, journal_lines=ACTUAL_LINES, active=True)
    with pytest.raises(ValueError, match='NATIVE_TESTER_INCOMPLETE'):
        run()
    assert len(calls) == 1


def test_btc_saturday_completion_is_not_skipped_or_changed(tmp_path):
    run, calls, profile = native_launch(tmp_path, complete=True, symbol='BTCUSD')
    result = run()
    assert len(calls) == 1
    assert (result['symbol'], result['records'], result['empty']) == ('BTCUSD', 1, False)
    assert not (tmp_path / 'common/MosesDataBuild/native_request.txt').exists()
