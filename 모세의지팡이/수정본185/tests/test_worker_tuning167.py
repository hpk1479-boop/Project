"""167: the backtest worker count measured per PC (설정 → 백테스트 → 최적화).

Candidates run from the PC's cores to its logical processors; the fewest workers within 3% of the
best throughput win; memory caps the count. The result is kept per PC beside other PCs' results in
Part2/event_backtest.json, and an explicit count still comes first.
"""
import json
import os
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'Part2'), str(ROOT / 'Part1/program'), str(ROOT / 'Part3')]
from event_backtest import runner
from event_backtest import worker_tuning as t
import process_priority


def pc(key='k1'):
    return {'key': key, 'processor': 'CPU', 'physical': 14, 'logical': 20, 'memory_gb': 32}


def result(workers=20, key='k1'):
    return {'workers': workers, 'rows': [{'workers': 14, 'bundles_per_second': 653.0}], 'day': '2026-09-30',
            'strategies': ['SPECIAL2'], 'measured_at': '2026-10-08T12:00:00+00:00', 'machine': pc(key)}


def test_candidates_run_from_cores_to_logical_processors():
    assert t.candidates(14, 20) == [14, 17, 20]
    assert t.candidates(8, 8) == [8]
    assert t.candidates(4, 8) == [4, 6, 8]


def test_machine_identity_follows_the_hardware(monkeypatch):
    monkeypatch.setattr(t, 'core_counts', lambda: (14, 20))
    monkeypatch.setattr(t, 'memory', lambda: (32 * 2**30, 0))
    monkeypatch.setattr(t, '_processor_name', lambda: 'CPU A')
    first = t.machine()['key']
    assert t.machine()['key'] == first and len(first) == 16
    for name, value in (('core_counts', lambda: (8, 16)), ('memory', lambda: (16 * 2**30, 0)),
                        ('_processor_name', lambda: 'CPU B')):
        with monkeypatch.context() as patch:
            patch.setattr(t, name, value)
            assert t.machine()['key'] != first


def test_this_pc_identity_holds_no_path():
    row = t.machine()
    assert row['logical'] >= row['physical'] >= 1 and row['memory_gb'] >= 0
    assert not any(isinstance(v, str) and ('\\' in v or '/' in v) for v in row.values())


def test_saved_count_is_used_only_on_the_pc_it_was_measured_on(tmp_path, monkeypatch):
    path = tmp_path / 'event_backtest.json'
    path.write_text(json.dumps({'warehouse': 'w', 'cores': None}), encoding='utf-8')
    monkeypatch.setattr(t, 'machine', lambda: pc('k1'))
    t.save(result(20, 'k1'), path)
    data = json.loads(path.read_text('utf-8'))
    assert data['warehouse'] == 'w' and data['worker_profiles']['k1']['workers'] == 20
    assert data['worker_profiles']['k1']['processor'] == 'CPU'
    assert t.tuned_workers(data) == 20
    monkeypatch.setattr(t, 'machine', lambda: pc('k2'))
    assert t.tuned_workers(data) is None
    t.save(result(8, 'k2'), path)
    data = json.loads(path.read_text('utf-8'))
    assert set(data['worker_profiles']) == {'k1', 'k2'} and t.tuned_workers(data) == 8
    assert not [p for p in tmp_path.iterdir() if p.suffix == '.tmp']


@pytest.mark.parametrize('row', [{'workers': 0}, {'workers': '20'}, {'workers': True}, 'x', None])
def test_unusable_saved_rows_are_ignored(row, monkeypatch):
    monkeypatch.setattr(t, 'machine', lambda: pc('k1'))
    assert t.tuned_workers({'worker_profiles': {'k1': row}}) is None


def test_worker_count_prefers_explicit_then_measured_then_cores(monkeypatch):
    monkeypatch.setattr(runner, 'physical_cores', lambda: 14)
    monkeypatch.setattr(runner, '_measured_workers', lambda: None)
    assert runner.worker_count({}) == 14
    monkeypatch.setattr(runner, '_measured_workers', lambda: 20)
    assert runner.worker_count({}) == 20
    assert runner.worker_count({'cores': 6}) == 6 and runner.worker_count({}, cores=3) == 3
    assert runner.worker_count({'cores': 6}, sequential=True) == 1


def test_measure_refuses_while_live_or_a_backtest_runs(tmp_path, monkeypatch):
    monkeypatch.setattr(process_priority, 'live_running', lambda kernel=None: True)
    with pytest.raises(ValueError, match='실시간을 끈 뒤'):
        t.measure(tmp_path, symbol='XAUUSD+', strategies=['SPECIAL2'])
    monkeypatch.setattr(process_priority, 'live_running', lambda kernel=None: False)
    from event_backtest.warehouse_cleanup import warehouse_activity
    with warehouse_activity(tmp_path):
        with pytest.raises(ValueError, match='백테스트가 끝난 뒤'):
            t.measure(tmp_path, symbol='XAUUSD+', strategies=['SPECIAL2'])


@pytest.fixture
def measuring(tmp_path, monkeypatch):
    monkeypatch.setattr(process_priority, 'live_running', lambda kernel=None: False)
    monkeypatch.setattr(process_priority, '_set', lambda kernel, priority: {})
    monkeypatch.setattr(t, 'machine', lambda: pc())
    monkeypatch.setattr(t, '_template', lambda *a: {'start': '2026-09-30'})
    seen = []

    def fake_round(task, folder, count):
        seen.append(count)
        assert folder.parent == tmp_path / '.tuning'
        folder.mkdir(parents=True, exist_ok=True)
        return {'workers': count, 'bundles_per_second': {14: 653.0, 17: 750.0, 20: 757.0}[count],
                'ms_per_bundle': 21.0, 'worker_memory_mb': 300}
    monkeypatch.setattr(t, '_round', fake_round)
    return seen


def test_the_fewest_workers_near_the_best_win(tmp_path, monkeypatch, measuring):
    monkeypatch.setattr(t, 'memory', lambda: (32 * 2**30, 16 * 2**30))
    events = []
    found = t.measure(tmp_path, symbol='XAUUSD+', strategies=['SPECIAL2'], emit=lambda *a: events.append(a))
    assert measuring == [14, 17, 20]
    assert found['workers'] == 17                          # 750 is within 3% of 757
    assert [e[1]['workers'] for e in events if e[0] == 'TUNE_PROGRESS'] == [14, 17, 20]
    assert found['day'] == '2026-09-30' and found['machine']['key'] == 'k1'
    assert not (tmp_path / '.tuning').exists()            # its own temporary replays are gone


def test_memory_caps_the_counts_measured(tmp_path, monkeypatch, measuring):
    monkeypatch.setattr(t, 'memory', lambda: (8 * 2**30, 5 * 2**30))   # 0.8 × 5GB fits 13 workers of 300MB
    found = t.measure(tmp_path, symbol='XAUUSD+', strategies=['SPECIAL2'])
    assert measuring == [14]
    assert found['workers'] == 14
    assert [row.get('skipped') for row in found['rows']] == [None, '메모리 부족', '메모리 부족']


# ---- Part3 relay of the measuring subprocess --------------------------------------------------------

def test_relay_reads_progress_result_and_error(tmp_path):
    from lab import backtest_tuning as b
    class Process:
        def __init__(self, lines):
            self.stdout = iter(lines)
        def wait(self):
            return 0
    for lines, expected in (
            (['not json\n', json.dumps({'event': 'TUNE_PROGRESS', 'step': 2, 'steps': 3, 'workers': 17}) + '\n',
              json.dumps({'event': 'COMPLETE', 'result': {'workers': 20}}) + '\n'], {'workers': 20}),
            ([json.dumps({'event': 'ERROR', 'message': '실시간을 끈 뒤 다시 누르세요.'}) + '\n'], None),
            ([], None)):
        scenario = tmp_path / 'scenario.json'
        scenario.write_text('{}', encoding='utf-8')
        b._STATE.update(running=True, step=0, steps=0, workers=None, result=None, error=None)
        b._follow(Process(lines), str(scenario))
        assert b._STATE['running'] is False and b._STATE['result'] == expected and not scenario.exists()
        if expected is None:
            assert b._STATE['error'] in ('실시간을 끈 뒤 다시 누르세요.', '측정을 끝내지 못했습니다.')
        else:
            assert (b._STATE['step'], b._STATE['steps'], b._STATE['workers']) == (2, 3, 17)


def test_start_refuses_without_launching(tmp_path, monkeypatch):
    from lab import backtest_tuning as b, unified_backtest
    import subprocess
    monkeypatch.setattr(subprocess, 'Popen', lambda *a, **k: pytest.fail('no measurement may start'))
    monkeypatch.setattr(unified_backtest, 'warehouse', lambda: tmp_path)
    monkeypatch.setattr(b, '_request', lambda: {'symbol': 'XAUUSD+', 'strategies': ['SPECIAL2'], 'triggers': {}})
    b._STATE.update(running=False)
    monkeypatch.setattr(process_priority, 'live_running', lambda kernel=None: True)
    with pytest.raises(ValueError, match='실시간을 끈 뒤'):
        b.start()
    monkeypatch.setattr(process_priority, 'live_running', lambda kernel=None: False)
    from event_backtest.warehouse_cleanup import warehouse_activity
    with warehouse_activity(tmp_path):
        with pytest.raises(ValueError, match='백테스트가 끝난 뒤'):
            b.start()
    b._STATE.update(running=True)
    try:
        with pytest.raises(ValueError, match='이미 측정 중'):
            b.start()
    finally:
        b._STATE.update(running=False)
