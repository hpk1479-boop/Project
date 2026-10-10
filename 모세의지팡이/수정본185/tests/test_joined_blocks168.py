"""168: a replay split into blocks of equal trading days, joined exactly where their states meet.

joins.py plans the blocks and stitches their alert rows; join_state.py decides when two engine states
act alike. The checks here: blocks cover each recorded period once and never cross a gap; a block
joins its follower at the first bundle where their states are equal, the rows of one continuous
replay are kept on both sides of it, and an unfinished chain falls back to each block's own range;
the comparison ignores what only depends on where a replay began (counters, ids, old history) and
still separates states that act differently.
"""
from __future__ import annotations

import csv
import datetime as dt
import json
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'Part2'), str(ROOT / 'Part1/program')]

from event_backtest import joins, join_state
from event_backtest.settings import milliseconds
from event_backtest.warehouse import FIELDS


def weekdays(start, end):
    day, last, found = dt.date.fromisoformat(start), dt.date.fromisoformat(end), []
    while day < last:
        if day.weekday() < 5:
            found.append(day.isoformat())
        day += dt.timedelta(days=1)
    return found


def capture(start, end, days=None):
    return {'start': start, 'end': end, 'observed_days': weekdays(start, end) if days is None else days}


SEPTEMBER = [capture('2026-09-01', '2026-10-01')]


# ---- planning ---------------------------------------------------------------------------------

@pytest.mark.parametrize('workers', [1, 2, 4, 7, 24])
def test_blocks_have_equal_trading_days_and_cover_the_period_once(workers):
    days = weekdays('2026-09-01', '2026-10-01')
    blocks, chains = joins.plan_periods([{'start': '2026-09-01', 'end': '2026-10-01'}], SEPTEMBER, workers)
    assert chains == [list(range(len(blocks)))]
    assert len(blocks) == min(workers, len(days) // joins.MIN_BLOCK_DAYS)
    assert blocks[0][0] == '2026-09-01' and blocks[-1][1] == '2026-10-01'
    assert all(first[1] == second[0] for first, second in zip(blocks, blocks[1:]))
    counts = [len([day for day in days if start <= day < end]) for start, end in blocks]
    assert max(counts) - min(counts) <= 1 and min(counts) >= joins.MIN_BLOCK_DAYS


def test_recorded_periods_share_the_workers_and_no_chain_crosses_a_gap():
    periods = [{'start': '2026-09-01', 'end': '2026-09-22'}, {'start': '2026-09-28', 'end': '2026-10-07'}]
    captures = [capture('2026-09-01', '2026-09-22'), capture('2026-09-28', '2026-10-07')]
    blocks, chains = joins.plan_periods(periods, captures, 4)
    assert len(chains) == 2 and len(blocks) <= 4 and sorted(i for chain in chains for i in chain) == list(range(len(blocks)))
    for period, chain in zip(periods, chains):
        assert blocks[chain[0]][0] == period['start'] and blocks[chain[-1]][1] == period['end']
    assert len(chains[0]) > len(chains[1])          # 15 trading days against 7


def test_more_periods_than_workers_keep_one_block_each():
    periods = [{'start': f'2026-09-{day:02d}', 'end': f'2026-09-{day + 1:02d}'} for day in (1, 3, 7, 9)]
    blocks, chains = joins.plan_periods(periods, SEPTEMBER, 2)
    assert chains == [[0], [1], [2], [3]] and blocks == [(p['start'], p['end']) for p in periods]


def test_without_observed_days_the_period_is_one_block():
    assert joins.plan_periods([{'start': '2026-09-01', 'end': '2026-10-01'}], [], 8) == ([('2026-09-01', '2026-10-01')], [[0]])


def test_join_plans_bound_each_overrun_and_what_followers_record():
    blocks, chains = joins.plan_periods([{'start': '2026-09-01', 'end': '2026-10-01'}], SEPTEMBER, 3)
    plans = joins.join_plans(blocks, chains, SEPTEMBER)
    days = weekdays('2026-09-01', '2026-10-01')
    for index in (0, 1):
        after = [day for day in days if day >= blocks[index][1]]
        assert plans[index]['until'] == after[joins.CAP_DAYS]
    assert plans[2]['until'] is None and plans[2]['until_ms'] is None
    assert plans[0]['record_until_ms'] == 0
    assert plans[1]['record_until_ms'] == milliseconds(plans[0]['until'])
    assert plans[2]['record_until_ms'] == max(milliseconds(plans[0]['until']), milliseconds(plans[1]['until']))
    assert [plan['position'] for plan in plans.values()] == [0, 1, 2]
    assert all(plan['chain'] == ['chunk_000', 'chunk_001', 'chunk_002'] for plan in plans.values())
    assert joins.join_plans(blocks, [[0], [1], [2]], SEPTEMBER) == {}


# ---- stitching ----------------------------------------------------------------------------------

def write_block(folder, times, *, probes=(), stop=None):
    """A block folder: alerts.csv with one row per time (multi-line message), alert_rows.json, probes, join.json."""
    folder.mkdir(parents=True, exist_ok=True)
    with (folder / 'alerts.csv').open('w', encoding='utf-8', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        for index, stamp in enumerate(times):
            writer.writerow({'run_id': 'r', 'time_ms': stamp, 'strategy': 'S', 'signal_id': f'{folder.name}-{index}',
                             'message': f'alert {stamp}\nsecond line'})
    (folder / joins.ROWS).write_text(json.dumps([[stamp, f'{folder.name}-{index}', 1] for index, stamp in enumerate(times)]),
                                     encoding='utf-8')
    with (folder / joins.PROBES).open('w', encoding='utf-8') as handle:
        for stamp, rows in probes:
            handle.write(json.dumps({'t': stamp, 'd': 'x', 'rows': rows, 'g': 1}) + '\n')
    if stop is not None:
        (folder / joins.STOP).write_text(json.dumps(stop), encoding='utf-8')


def kept(segments, tmp_path):
    rows = []
    for folder, first, end in segments:
        out = tmp_path / f'kept_{folder.name}.csv'
        joins.kept_rows(folder, first, end, out)
        with out.open(encoding='utf-8', newline='') as handle:
            rows += [int(row['time_ms']) for row in csv.DictReader(handle)]
    return rows


def test_stitch_keeps_the_earlier_block_up_to_the_join_and_the_follower_after_it(tmp_path):
    first, second = tmp_path / 'run/chunk_000', tmp_path / 'run/chunk_001'
    # The first block's own rows 10, 20 and its overrun 30, 40, 50; it met the follower after bundle 40.
    write_block(first, [10, 20, 30, 40, 50], stop={'status': 'CONVERGED', 't': 40, 'rows': 4, 'rows_at_end': 2})
    # The follower, from its own start: 31 (before its state matched), 40, 60, 70.
    write_block(second, [31, 40, 60, 70], probes=[(30, 0), (40, 2)], stop={'status': 'END', 't': None, 'rows': None})
    segments, found = joins.stitch([first, second])
    assert segments == [(first, 0, 4), (second, 2, None)]
    assert found == [{'status': 'CONVERGED', 'at_ms': 40, 'from_block': 0, 'to_block': 1}]
    assert kept(segments, tmp_path) == [10, 20, 30, 40, 60, 70]


def test_stitch_passes_over_a_follower_that_stopped_before_the_join(tmp_path):
    folders = [tmp_path / f'run/chunk_{i:03d}' for i in range(3)]
    write_block(folders[0], [10, 50], stop={'status': 'CONVERGED', 't': 50, 'rows': 2})
    write_block(folders[1], [20, 30], probes=[(20, 0), (30, 2)], stop={'status': 'CONVERGED', 't': 30, 'rows': 2})
    write_block(folders[2], [40, 50, 60], probes=[(30, 0), (50, 2)], stop={'status': 'END', 't': None})
    segments, found = joins.stitch(folders)
    assert segments == [(folders[0], 0, 2), (folders[2], 2, None)]
    assert [(j['from_block'], j['to_block']) for j in found] == [(0, 2)]
    assert kept(segments, tmp_path) == [10, 50, 60]


def test_a_capped_join_hands_over_at_its_last_probe_and_is_reported(tmp_path):
    first, second = tmp_path / 'run/chunk_000', tmp_path / 'run/chunk_001'
    write_block(first, [10, 20, 30], stop={'status': 'CAPPED', 't': 30, 'rows': 3})
    write_block(second, [25, 30, 35], probes=[(30, 2)], stop={'status': 'END', 't': None})
    segments, found = joins.stitch([first, second])
    assert segments == [(first, 0, 3), (second, 2, None)] and found[0]['status'] == 'CAPPED'
    assert kept(segments, tmp_path) == [10, 20, 30, 35]


@pytest.mark.parametrize('status', ['CANCELLED', 'FAILED', None])
def test_an_unfinished_chain_keeps_each_block_range(tmp_path, status):
    first, second = tmp_path / 'run/chunk_000', tmp_path / 'run/chunk_001'
    write_block(first, [10, 20, 30], stop={'status': 'CONVERGED', 't': 30, 'rows': 3, 'rows_at_end': 2})
    write_block(second, [25, 30, 35], probes=[(30, 2)], stop=None if status is None else
                {'status': status, 't': None, 'rows': None, 'rows_at_end': 3})
    segments, found = joins.stitch([first, second])
    assert segments == [(first, 0, 2), (second, 0, 3 if status else None)]
    assert found == [{'status': 'UNJOINED', 'after_block': 0}]


# ---- the protocol between two running blocks ------------------------------------------------

HOUR = joins.PROBE_MS


def two_lanes(tmp_path, *, cancelled=lambda: False):
    """Two blocks of one chain: hours 0-9 and 10-19, overrun up to hour 15."""
    base = tmp_path / 'run'
    chain = ['chunk_000', 'chunk_001']
    plans = [{'chain': chain, 'position': 0, 'start_ms': 0, 'end_ms': 10 * HOUR, 'until_ms': 15 * HOUR, 'record_until_ms': 0},
             {'chain': chain, 'position': 1, 'start_ms': 10 * HOUR, 'end_ms': 20 * HOUR, 'until_ms': None,
              'record_until_ms': 15 * HOUR}]
    lanes = []
    for name, plan in zip(chain, plans):
        (base / name).mkdir(parents=True)
        lane = joins.JoinLane(base / name, plan, 1, cancelled)
        lane.running()
        lanes.append(lane)
    return lanes


def state(value):
    return lambda: (value, {'part': value})


def test_the_follower_records_and_the_leader_joins_at_the_first_equal_state(tmp_path):
    leader, follower = two_lanes(tmp_path)
    for hour in range(10, 17):   # the follower's state matches the continuous one from hour 12
        follower.after_bundle(hour * HOUR, hour, state('same' if hour >= 12 else f'warm{hour}'))
    follower.close(rows=99)
    assert [json.loads(line)['t'] for line in (tmp_path / 'run/chunk_001' / joins.PROBES).read_text().splitlines()] == \
        [hour * HOUR for hour in range(10, 15)]                       # within the leader's overrun only
    assert not leader.after_bundle(9 * HOUR, 5, state('own'))        # its own range: no probe
    assert not leader.after_bundle(10 * HOUR, 6, state('same'))      # differs from warm10
    assert leader.differences == ['part']
    assert not leader.after_bundle(11 * HOUR, 7, state('same'))
    assert leader.after_bundle(12 * HOUR, 8, state('same')) and leader.stopped == 'CONVERGED'
    leader.close(rows=8)
    summary = leader.summary()
    assert summary['status'] == 'CONVERGED' and summary['t'] == 12 * HOUR and summary['rows'] == 8
    segments, found = joins.stitch([tmp_path / 'run/chunk_000', tmp_path / 'run/chunk_001'])
    assert segments[1] == (tmp_path / 'run/chunk_001', 12, None) and found[0]['status'] == 'CONVERGED'


def test_the_leader_waits_for_a_follower_still_behind(tmp_path):
    leader, follower = two_lanes(tmp_path)
    def late():
        time.sleep(0.5)
        follower.after_bundle(10 * HOUR, 0, state('same'))
    thread = threading.Thread(target=late)
    thread.start()
    began = time.monotonic()
    assert leader.after_bundle(10 * HOUR, 3, state('same'))
    thread.join()
    assert time.monotonic() - began >= 0.4 and leader.stopped == 'CONVERGED'


def test_a_failed_follower_releases_the_leader_which_caps_at_its_last_probe(tmp_path):
    leader, follower = two_lanes(tmp_path)
    follower.close(failed=True, rows=0)
    assert not leader.after_bundle(10 * HOUR, 3, state('same'))
    assert not leader.after_bundle(11 * HOUR, 4, state('same'))
    leader.close(rows=4)
    assert leader.summary()['status'] == 'CAPPED' and leader.summary()['t'] == 11 * HOUR


def test_cancellation_stops_the_wait(tmp_path):
    stop = [False]
    leader, follower = two_lanes(tmp_path, cancelled=lambda: stop[0])
    threading.Timer(0.3, lambda: stop.__setitem__(0, True)).start()
    assert not leader.after_bundle(10 * HOUR, 3, state('same'))
    leader.close(interrupted=True, rows=3)
    assert leader.summary()['status'] == 'CANCELLED'


def test_a_new_attempt_starts_a_fresh_record(tmp_path):
    # A follower replayed again with every feed (runner._EveryFeedNeeded) replaces its record.
    leader, follower = two_lanes(tmp_path)
    follower.after_bundle(10 * HOUR, 0, state('limited'))
    follower.close(failed=True, rows=0)
    again = joins.JoinLane(tmp_path / 'run/chunk_001', follower.plan, 2, lambda: False)
    again.running()
    again.after_bundle(10 * HOUR, 0, state('same'))
    assert leader.after_bundle(10 * HOUR, 1, state('same'))


# ---- what the comparison ignores and what it does not -------------------------------------

def engine(feeds=None, **states):
    board = SimpleNamespace(_feeds=feeds or {}, _observed={}, _health={})
    return SimpleNamespace(board=board, strategy_state=states.get('strategies', {}),
                           processor_state=states.get('processors', {}),
                           scheduler=states.get('scheduler'), failures={}, disabled=set(), degraded=set(),
                           status='HEALTHY', _configured=True)


def canon(value, **kwargs):
    return join_state.Canon(engine(**kwargs))(value)


def test_timer_order_counts_only_relative_to_the_current_counter():
    from event_engine.scheduler import Scheduler
    from event_engine.model import TimerRequest
    def scheduler(offset, swap=False):
        value = Scheduler()
        value._order = offset
        first, second = TimerRequest('X', 2000, 'a'), TimerRequest('X', 2000, 'b')
        for request in ((second, first) if swap else (first, second)):
            value.request('S', request)
        return value
    assert canon(scheduler(0)) == canon(scheduler(500))
    assert canon(scheduler(0)) != canon(scheduler(0, swap=True))   # equal deadlines keep request order


class IntentPort:
    """Stand-in with the fields join_state reads from strategy_recipe.port.IntentPort."""
    def __init__(self, sequence, used_token, observed_token, percentile_publication):
        from strategy_recipe.runtime import IntentMachine
        steps = [{'kind': 'OZ_ALERT', 'tfs': ['5m']}, {'kind': 'PERCENTILE_OUT_IN', 'tfs': ['1m']}]
        machine = IntentMachine({'steps': steps}, 'X', 'LONG', 0)
        machine.used = {1: ('pct', ((1, '1m', 60, 120, percentile_publication, ('RSI',)),))}
        if used_token is not None:
            machine.used[0] = ('step', (used_token,))
        machine.sequence = sequence   # the alert-id counter
        self.machines = [machine]
        self.oz_sequence = sequence
        self.oz_events = {('X', '5m', 'BLIND', 'OZ', 'LONG'): (observed_token, 100.0, {'kind': 'OZ'})}
        self._restart_tokens = {}
        self.observations = {0: [{'matched': True, 'token': ('step', (observed_token,))}, {'matched': False, 'token': None}]}
        self.final_observations = {}
        self.metric_frames = {}


def test_oz_and_percentile_tokens_compare_relative_to_their_counters():
    # Two ports 40 OZ events apart that consumed the latest OZ event of their key and observe it.
    near = IntentPort(10, 9, 9, 111)
    far = IntentPort(50, 49, 49, 222)
    assert canon(near) == canon(far)
    # A consumed event that is still the latest of its key may be observed again: it counts.
    assert canon(near) != canon(IntentPort(10, None, 9, 111))


def test_a_consumed_oz_token_that_can_never_return_counts_as_none():
    # Event 7 was replaced by event 9 for its key: no later observation can carry 7 again.
    assert canon(IntentPort(10, 7, 9, 111)) == canon(IntentPort(10, None, 9, 111))
    assert canon(IntentPort(10, 7, 9, 111)) == canon(IntentPort(50, 30, 49, 222))


class FVGRuntime:
    """Stand-in with the fields join_state reads from event_engine.fvg_runtime.FVGRuntime."""


class OZProfile:
    """Stand-in with the fields join_state reads from oz_engine.profile.OZProfile."""


def test_fvg_zones_noted_up_to_the_last_closed_bar_are_no_longer_compared():
    def fvg(seen):
        runtime = FVGRuntime()
        runtime._seen_created, runtime._last_closed_time = set(seen), {('X', '5m'): 600.0}
        return runtime
    old = fvg({'X|5m|BULL|300|1|2', 'X|5m|BULL|600|1|2'})
    new = fvg({'X|5m|BULL|600|1|2'})
    plain = lambda runtime: join_state.Canon(engine(processors={'FVG_STATE': {'runtime': runtime}}))(runtime._seen_created)
    assert plain(old) == plain(new) == {'set': []}
    later = fvg({'X|5m|BULL|660|1|2'})
    assert plain(later) == {'set': ['"X|5m|BULL|660|1|2"']}


def test_oz_alert_keys_count_while_a_candidate_could_still_complete():
    from event_engine.model import FeedSnapshot
    from staff_schema import PIPE_VALUE_COLUMNS
    times = np.arange(30, dtype='<i8') * 300
    feeds = {('X', '5m'): FeedSnapshot(times, np.ones(30), np.zeros((30, len(PIPE_VALUE_COLUMNS))))}
    profile = OZProfile()
    profile.__dict__.update(symbol='X', max_bars=10, watch_generation=3, validation_mode='BLIND', trigger_mode='OZ',
                            watch=SimpleNamespace(_revision={'BLIND:OZ': 3}))
    def keys(*b0):
        profile.alert_keys = {('5m', 'LONG', value) for value in b0}
        return join_state.Canon(engine(feeds=feeds))(profile)
    cutoff = float(times[-1 - (10 + 3)])
    assert keys(cutoff - 300, cutoff) == keys(cutoff)          # older than max_bars + margin: dropped
    assert keys(cutoff + 300) != keys(cutoff)                  # within it: compared


def test_fact_positions_are_ids_but_fact_contents_count():
    from durable_protocol import identity
    def fact(sequence, price):
        return {'strategy': 'SWEEP', 'kind': 'SWEEP_TOUCH', 'level_price': price,
                'fact_revision': [1, sequence], 'event_id': identity('SWEEP', 1, sequence)}
    assert canon(fact(5, 10.0)) == canon(fact(5000, 10.0))
    assert canon(fact(5, 10.0)) != canon(fact(5, 11.0))
    unrelated = {**fact(5, 10.0), 'event_id': 'content-made'}   # an id not made from the position
    assert canon(unrelated) != canon({**fact(6, 10.0), 'event_id': 'content-made'})


def test_a_replay_with_an_approximate_join_is_reused_only_with_the_same_blocks(tmp_path):
    from event_backtest.warehouse import Warehouse
    from event_backtest.settings import scenario
    root = tmp_path / 'w'
    catalog = Warehouse(root, results=True)
    try:
        s = scenario(start='2026-09-01', end='2026-09-02', strategies=['SPECIAL8'])
        for run_id, exact, partition in (('a' * 32, False, 'blocks-1'), ('b' * 32, True, 'blocks-2')):
            (root / 'runs' / run_id).mkdir(parents=True)
            (root / 'runs' / run_id / 'result.json').write_text('{}', encoding='utf-8')
            catalog.run(run_id, 'COMPLETE', {'scenario': s, 'code_hash': 'c', 'config_hash': 'f', 'cores': 2,
                                             'replay_key': 'key-' + run_id[0], 'replay_partition': partition,
                                             'joins_exact': exact, 'alert_rows': 0, 'chunks': [{}],
                                             'completed_at': '2026-10-08T00:00:00'})
        assert catalog.finished_replay('key-a', 'c', 'f', partition='blocks-9') is None
        assert catalog.finished_replay('key-a', 'c', 'f', partition='blocks-1')[0] == 'a' * 32
        assert catalog.finished_replay('key-b', 'c', 'f', partition='blocks-9')[0] == 'b' * 32
    finally:
        catalog.close()


def test_absent_and_empty_history_compare_alike():
    class ComposerManager:
        pass
    idle, never = ComposerManager(), ComposerManager()
    idle._oz_dispatch_transaction = None      # composer_oz_dispatch resets it after a dispatch
    assert canon(idle) == canon(never)
    class OZRuntime:
        pass
    def runtime(memory):
        value = OZRuntime()
        value.memory, value.config = memory, {'MAX_BARS_AFTER_B0': '10'}
        return value
    from event_engine.model import FeedSnapshot
    from staff_schema import PIPE_VALUE_COLUMNS
    times = np.arange(30, dtype='<i8') * 300
    feeds = {('X', '5m'): FeedSnapshot(times, np.ones(30), np.zeros((30, len(PIPE_VALUE_COLUMNS))))}
    old = json.dumps({'version': 1, 'records': {'k': {'symbol': 'X', 'source_tf': '5m', 'b0_time': 0}}})
    recent = json.dumps({'version': 1, 'records': {'k': {'symbol': 'X', 'source_tf': '5m', 'b0_time': float(times[-2])}}})
    plain = lambda value: join_state.Canon(engine(feeds=feeds))(value)
    # Records._load reads a missing file as no records: only records that may match again count.
    assert plain(runtime({'oz_outgoing_events.json': old})) == plain(runtime({}))
    assert plain(runtime({'oz_outgoing_events.json': recent})) != plain(runtime({}))


def test_digests_name_the_parts_that_differ():
    from event_engine.scheduler import Scheduler
    first = engine(strategies={'A': {'x': 1}}, processors={'P': {'y': [1, 2]}}, scheduler=Scheduler())
    second = engine(strategies={'A': {'x': 1}}, processors={'P': {'y': [1, 3]}}, scheduler=Scheduler())
    whole_a, parts_a = join_state.digests(first)
    whole_b, parts_b = join_state.digests(second)
    assert whole_a != whole_b
    assert {name for name in parts_a if parts_a[name] != parts_b[name]} == {'processor:P'}
