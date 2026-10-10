"""Command lifetime and cancellation parity through real Wire/LIVE/replay paths."""
from pathlib import Path
from concurrent.futures import Future
import csv
import datetime as dt
import json
import os
import struct
import subprocess
import sys

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'Part1/program'), str(ROOT / 'Part2')]
from event_backtest import runner, build_plan
from event_backtest.settings import scenario, milliseconds, relative_path, file_hash, COMMAND_CONTINUOUS_REASON
from event_backtest.warehouse import Warehouse
import staff_schema as wire

CONFIG = {'STAFF_ALLOWED_SYMBOLS': 'XAUUSD+', 'TARGET_SYMBOLS': 'XAUUSD+',
          'TELEGRAM_TOKEN': 'OFFLINE', 'TELEGRAM_CHAT_ID': 'BACKTEST', 'WONBI_SIGMA': '3'}
ONE_SHOT = '골드 1분 상단 원비 터치 알려줘'
PERSISTENT = '골드 1분 상단 원비 터치 계속 알려줘'


@pytest.mark.parametrize('cores', [1, 2, 12])
@pytest.mark.parametrize('text', [ONE_SHOT, PERSISTENT, 'parser-independent command'])
def test_every_command_plan_preserves_one_engine(cores, text):
    request = scenario(start='2026-09-01', end='2026-09-09', strategies=['WATCH'],
                       commands=[{'strategy': 'WATCH', 'chat_id': 'OFFLINE', 'text': text}], cores=cores)
    periods, details = runner.execution_plan(request)
    assert periods == [(request['start'], request['end'])]
    assert details['cores'] == 1 and details['requested_cores'] == cores
    assert details['execution_policy'] == 'COMMAND_CONTINUOUS'
    assert request['warnings'] == [COMMAND_CONTINUOUS_REASON]


def test_no_command_keeps_requested_chunk_planning():
    # 168: blocks of equal trading days (3 at least), joined where their states meet.
    request = scenario(start='2026-09-01', end='2026-09-15', strategies=['SPECIAL1'], cores=4)
    days = ['2026-09-01', '2026-09-02', '2026-09-03', '2026-09-04', '2026-09-07', '2026-09-08',
            '2026-09-09', '2026-09-10', '2026-09-11', '2026-09-14']
    periods, details = runner.execution_plan(request, captures=[{'start': '2026-09-01', 'end': '2026-09-15', 'observed_days': days}])
    assert len(periods) == 3 and details['cores'] == 3 and details['chains'] == [[0, 1, 2]]
    assert details['execution_policy'] == 'JOINED_BLOCKS'
    assert details['requested_cores'] == 4 and not request['warnings']


def make_capture(root, *, touch=True, warm_touch=False):
    directory = root / 'captures/probe'
    directory.mkdir(parents=True)
    samples = [('2026-08-27T09:00:00', False), ('2026-08-28T09:00:00', False),
               ('2026-08-31T09:00:00', warm_touch),
               ('2026-09-01T09:00:00', False), ('2026-09-01T09:01:00', touch),
               ('2026-09-02T09:00:00', False), ('2026-09-03T09:00:00', False),
               ('2026-09-04T09:00:00', False), ('2026-09-07T09:00:00', False),
               ('2026-09-08T09:00:00', False), ('2026-09-08T09:01:00', touch)]
    with (directory / 'pipe_000.bin').open('wb') as stream:
        stream.write(struct.pack('<IIII', 0x4D535033, 2, len(wire.PIPE_VALUE_COLUMNS), 650))
        for seq, (date, hit) in enumerate(samples, 1):
            stamp = milliseconds(date)
            times = np.arange(650, dtype='<i8') * 60 + stamp // 1000 - 649 * 60
            values = np.full((650, len(wire.PIPE_VALUE_COLUMNS)), 100., dtype='<f8')
            values[:, :4] = [100., 101., 99., 100.]
            values[:, wire.PIPE_VALUE_COLUMNS.index('wonbi_upper')] = 103.
            values[:, wire.PIPE_VALUE_COLUMNS.index('wonbi_lower')] = 97.
            for column in wire.PIPE_VALUE_COLUMNS:
                if column.endswith(('_lower_out', '_upper_out')):
                    values[:, wire.PIPE_VALUE_COLUMNS.index(column)] = np.nan
            if hit:values[-1, 1] = 104.
            payload = wire.pack_v2('XAUUSD+', '1m', times, np.ones(650, dtype='<i8'), values, seq=seq)
            stream.write(struct.pack('<qiI', stamp, 31, len(payload)))
            stream.write(payload)
    (directory / 'manifest.tsv').write_text('MSP3\nsymbol\tXAUUSD+\npipe_capture\tSTAFF_PIPE_V2\npipe_observation_unit\tmilliseconds\npipe_feed\t0\t1m\tpipe_000.bin\t' + str(len(samples)) + '\n', encoding='ascii')
    (directory / 'complete.txt').write_text('complete', encoding='ascii')
    row = {'capture_id': 'probe', 'symbol': 'XAUUSD+', 'mode': 'TIMER',
           'start': '2026-08-27', 'end': '2026-09-09', 'unit': 'MONTH',
           'ea_build_hash': 'test-ea', 'schema_id': wire.WIRE_SCHEMA_ID, 'timer_ms': 1000,
           'storage': 'MSP3', 'reconstruction_verified': True, 'path': 'captures/probe',
           'observed_days': sorted({date[:10] for date, _ in samples}),
           'files': {p.name: file_hash(p) for p in directory.iterdir()},
           'tick_evidence': {'actual': 'REAL_TICKS'}, 'stored_bytes': sum(p.stat().st_size for p in directory.iterdir()),
           'recorded_at': '2026-10-03T00:00:00+00:00'}
    catalog = Warehouse(root)
    catalog.register(row)
    catalog.close()
    return row


def live_rows(root, request, capture, *, backtest=False, canonical_persistent=False):
    from event_application import create_event_engine
    from event_host import load_staff
    from event_engine import Kind
    from event_backtest.bridge import CaptureInputs
    from event_backtest.calendar import warm_start
    from event_backtest.recording_time import clocks_of, recording_clock
    engine = create_event_engine(CONFIG, symbols=('XAUUSD+',), selection=['WATCH'], backtest=backtest)
    # LIVE receives real time (TimeGMT) and the STAFF boundary converts the bar times: the recording is
    # read with its broker clock, as a backtest reads it (수정본172). The dates name recorded days.
    real = recording_clock([capture]).to_utc_ms
    start = milliseconds(warm_start(request['start'], request['overlap_trading_days'], [capture]))
    if canonical_persistent:
        engine.ingress.post(Kind.SIGNAL, source='canonical_watch_fixture', source_seq=0, source_time=real(start) - 1,
            payload={'symbol': 'XAUUSD+', 'strategy': 'COMPOSER', 'signal_id': 'registration',
                     'content': {'type': 'WATCH_COMMAND', 'command': {
                         'action': 'GENERIC_WATCH', 'watch_id': 'GEN:persistent-fixture',
                         'watch_type': 'WONBI_TOUCH', 'evaluation_mode': 'LIVE', 'symbol': 'XAUUSD+',
                         'timeframes': ['1m'], 'level_side': 'HIGH', 'direction': 'SHORT',
                         'persistent': True, 'request_chat_id': 'BACKTEST', 'silent': False}}})
    else:
        for seq, command in enumerate(request['commands']):
            engine.ingress.post(Kind.COMMAND, source='scenario', source_seq=seq, source_time=real(start) - 1,
                                payload={'symbol': 'XAUUSD+', **command})
    engine.run()
    clock = [0.]
    staff = load_staff()
    cache = staff.StaffPipeCache('', health_session='BACKTEST', monotonic=lambda: clock[0], gap_journal=root / 'live_gaps.jsonl')
    for item in CaptureInputs(staff, cache, [root / capture['path']], transport='replay' if backtest else 'live', clock=clock,
                              start_ms=start, end_ms=milliseconds(request['end']), server_times=clocks_of([capture])):
        engine.ingress.post(item.kind, source=item.source, source_seq=item.source_seq,
                            source_time=item.source_time, payload=item.payload)
        engine.run()
    assert not engine.error_log
    return [(str(signal.source_time), 'NOTIFICATION', signal.payload['content']['message'], signal.payload['signal_id'])
            for signal in engine.signals
            if signal.payload['content'].get('type') == 'NOTIFICATION'
            and real(milliseconds(request['start'])) <= signal.source_time < real(milliseconds(request['end']))
            and '상단 원비' in signal.payload['content'].get('message', '')
            and '감시' not in signal.payload['content'].get('message', '')]


def test_canonical_persistent_watch_rearms_equally_in_live_and_replay(tmp_path, monkeypatch):
    root = tmp_path / 'w'
    capture = make_capture(root)
    request = scenario(start='2026-09-01', end='2026-09-09', mode='TICK', strategies=['WATCH'], overlap_trading_days=3)
    monkeypatch.setenv('MOSES_LOG_DIRECTORY', str(root / 'logs'))
    live = live_rows(root, request, capture, canonical_persistent=True)
    replay = live_rows(root, request, capture, backtest=True, canonical_persistent=True)
    assert len(live) == 2 and replay == live


class IsolatedPool:
    """Real run_chunk in an isolated process; only executor scheduling is synchronous."""
    def __init__(self, max_workers, initializer=None):
        self.max_workers = max_workers
        self.submitted = []
    def __enter__(self):return self
    def __exit__(self, *args):return False
    def submit(self, function, task):
        self.submitted.append(task)
        path = Path(task['out']).parent / (Path(task['out']).name + '_input.json')
        # Paths relative to this temporary input file: the run folder may lie outside the project.
        portable = {**task, 'out': os.path.relpath(task['out'], path.parent),
                    'warehouse': os.path.relpath(task['warehouse'], path.parent)}
        path.write_text(json.dumps(portable, ensure_ascii=False), encoding='utf-8')
        completed = subprocess.run([sys.executable, '-B', '-X', 'utf8', str(Path(__file__).resolve()),
                                    'worker', str(path)], cwd=ROOT, capture_output=True,
                                   text=True, encoding='utf-8', timeout=60,
                                   creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        future = Future()
        if completed.returncode:
            # Keep external workstation locations out of generated evidence.
            from verify_current import portable_text
            future.set_exception(AssertionError(portable_text(completed.stdout + completed.stderr)))
        else:
            future.set_result(json.loads((Path(task['out']) / 'result.json').read_text('utf-8')))
        return future


@pytest.mark.parametrize('cores', [1, 4])
@pytest.mark.parametrize('text,warmup,touch,warm_touch,expected', [
    (ONE_SHOT, 3, True, False, 1), (PERSISTENT, 3, True, False, 2),
    (ONE_SHOT, 3, False, False, 0), (ONE_SHOT, 0, True, False, 1),
    (ONE_SHOT, 3, True, True, 0)])
def test_live_and_requested_core_counts_have_identical_notifications(tmp_path, monkeypatch,
        cores, text, warmup, touch, warm_touch, expected):
    root = tmp_path / 'w'
    capture = make_capture(root, touch=touch, warm_touch=warm_touch)
    request = scenario(start='2026-09-01', end='2026-09-09', mode='TICK', strategies=['WATCH'],
                       overlap_trading_days=warmup, cores=cores,
                       commands=[{'strategy': 'WATCH', 'chat_id': 'BACKTEST', 'text': text}])
    monkeypatch.setenv('MOSES_LOG_DIRECTORY', str(root / 'live_logs'))
    baseline = live_rows(root, request, capture)
    assert len(baseline) == expected
    monkeypatch.setattr(runner, 'runtime_config', lambda s: dict(CONFIG))
    monkeypatch.setattr(runner, 'code_hash', lambda: 'release-test')
    monkeypatch.setattr(build_plan, 'current_build', lambda root: 'test-ea')
    monkeypatch.setattr(runner.concurrent.futures, 'ProcessPoolExecutor', IsolatedPool)
    events = []
    result = runner.run(request, root, captures=[capture], emit=lambda kind, data: events.append((kind, data)))
    with (root / result['alerts_csv']).open(encoding='utf-8', newline='') as stream:
        actual = [(row['time_ms'], 'NOTIFICATION', row['message'], row['signal_id']) for row in csv.DictReader(stream)
                  if '상단 원비' in row['message'] and '감시' not in row['message']]
    assert actual == baseline
    assert result['status'] == 'COMPLETE' and len(result['chunks']) == 1
    assert result['cores'] == 1 and result['requested_cores'] == cores
    assert result['captures'] == ['probe']
    start_event = next(data for kind, data in events if kind == 'RUN_START')
    assert start_event['cores'] == 1 and start_event['execution_reason'] == COMMAND_CONTINUOUS_REASON


def test_worker_already_cancelled_does_not_initialize_engine_or_read_capture(tmp_path):
    root = tmp_path / 'w'
    out = root / 'runs/cancelled/chunk_000'
    out.parent.mkdir(parents=True)
    (out.parent / 'stop.request').touch()
    request = scenario(start='2026-09-01', end='2026-09-09', strategies=['WATCH'], overlap_trading_days=0,
                       commands=[{'strategy': 'WATCH', 'chat_id': 'BACKTEST', 'text': ONE_SHOT}])
    task = {'scenario': request, 'config': CONFIG, 'start': request['start'], 'end': request['end'],
            'warm_start': request['start'], 'captures': [{'path': 'does_not_exist'}],
            'run_id': 'cancelled', 'out': str(out), 'warehouse': str(root), '_assert_no_engine': True}
    with IsolatedPool(1) as pool:
        result = pool.submit(runner.run_chunk, task).result()
    assert result['cancelled'] and result['not_started'] and result['bundles'] == 0
    assert result['processor_timings'] == {} and result['processed_end_ms'] is None
    with (root / result['alerts_csv']).open(encoding='utf-8', newline='') as stream:
        assert list(csv.DictReader(stream)) == []


def test_cancel_before_first_submission_keeps_empty_real_result(tmp_path, monkeypatch):
    root = tmp_path / 'w'
    capture = make_capture(root)
    request = scenario(start='2026-09-01', end='2026-09-09', mode='TICK', strategies=['WATCH'],
                       overlap_trading_days=3, cores=4,
                       commands=[{'strategy': 'WATCH', 'chat_id': 'BACKTEST', 'text': ONE_SHOT}])
    monkeypatch.setattr(runner, 'runtime_config', lambda s: dict(CONFIG))
    monkeypatch.setattr(runner, 'code_hash', lambda: 'release-test')
    monkeypatch.setattr(build_plan, 'current_build', lambda root: 'test-ea')
    class NoSubmit(IsolatedPool):
        def submit(self, *args, **kwargs):pytest.fail('cancelled before submission')
    monkeypatch.setattr(runner.concurrent.futures, 'ProcessPoolExecutor', NoSubmit)
    result = runner.run(request, root, captures=[capture], cancel=lambda: True)
    assert result['status'] == 'CANCELLED' and result['chunks'] == []
    assert result['processed_periods'] == [] and result['alert_statistics']['total'] == 0
    assert result['worker_scheduling']['submitted_chunks'] == 0
    assert result['unprocessed_periods'] == [{'start': request['start'], 'end': request['end'],
                                               'start_exclusive': False, 'reason': 'NOT_STARTED'}]
    with (root / result['alerts_csv']).open(encoding='utf-8', newline='') as stream:
        assert list(csv.DictReader(stream)) == []


def test_remaining_ranges_keep_last_observation_and_unstarted_interval_distinct():
    # A recording of the default broker (수정본172): its periods name server-clock days, while the engine's
    # last observation is real time. 12:00 UTC in September is 15:00 on the server clock (UTC+3).
    recorded = [{'path': 'capture'}]
    tasks = [{'start': '2026-09-01', 'end': '2026-09-02', 'captures': recorded},
             {'start': '2026-09-02', 'end': '2026-09-03', 'captures': recorded},
             {'start': '2026-09-03', 'end': '2026-09-04', 'captures': recorded}]
    rows = [{'task_start': tasks[0]['start'], 'task_end': tasks[0]['end'], 'cancelled': False},
            {'task_start': tasks[1]['start'], 'task_end': tasks[1]['end'], 'cancelled': True,
             'processed_end_ms': milliseconds('2026-09-02T12:00:00')}]
    assert runner.unprocessed_periods(tasks, rows) == [
        {'start': '2026-09-02T15:00:00.000+00:00', 'end': '2026-09-03',
         'start_exclusive': True, 'reason': 'INTERRUPTED'},
        {'start': '2026-09-03', 'end': '2026-09-04', 'start_exclusive': False, 'reason': 'NOT_STARTED'}]


if __name__ == '__main__':
    if sys.argv[1:2] != ['worker']:
        raise SystemExit('Use pytest or the isolated worker entry point')
    source = Path(sys.argv[2])
    task = json.loads(source.read_text('utf-8'))
    task['out'] = str((source.parent / task['out']).resolve())
    task['warehouse'] = str((source.parent / task['warehouse']).resolve())
    if task.pop('_assert_no_engine', False):
        import event_application
        def forbidden(*args, **kwargs):raise AssertionError('cancelled worker initialized an engine')
        event_application.create_event_engine = forbidden
    runner.run_chunk(task)
