"""Exact joins of replay blocks (수정본168).

The period is split into blocks of equal trading days, one per worker. Every block but the first
starts after the usual warm-up, so its state at its own start only approximates one continuous
replay. A block with a follower keeps replaying past its end (the overrun) and, at the first bundle
of every UTC hour, compares its state (join_state) with the state the follower recorded after the
same bundle. Where they are equal, every later input gives both the same decisions and alerts: from
that bundle on the follower is the continuous replay, so the block stops, and its alerts up to and
including that bundle replace the follower's.

Where the states never become equal within CAP_DAYS trading days (state some strategies keep for
good, such as the OZ tokens a recipe machine consumed), the block stops there and the follower
continues from that point. That join is approximate, as every block boundary was before: exact up to that
point, and after it a follower with CAP_DAYS more warm-up. It is reported with the result.

Files in each block's folder (one per lane):
  join_probes.jsonl  the follower's record: {"t": bundle time, "d": state digest, "rows": alert rows}
  join.json          how the block ended: status, the stop bundle and its alert rows
  alert_rows.json    [time_ms, signal_id, NOTIFICATION or not] for every alert row written
"""
from __future__ import annotations

import csv
import json
import os
from pathlib import Path
import time

CAP_DAYS = 5
MIN_BLOCK_DAYS = 3
PROBE_MS = 3_600_000
WAIT_SECONDS = 1800
PROBES = 'join_probes.jsonl'
STOP = 'join.json'
ROWS = 'alert_rows.json'


def trading_days(captures, start, end):
    """Observed trading days in [start, end) (calendar.capture_calendar)."""
    return sorted({day for c in captures for day in c.get('observed_days', ())
                   if start <= day < end and c.get('start', day) <= day < c.get('end', end)})


def plan_blocks(start, end, captures, blocks):
    """At most `blocks` blocks of equal trading days (at least MIN_BLOCK_DAYS each), first from `start`."""
    days = trading_days(captures, start, end)
    count = max(1, min(int(blocks), len(days) // MIN_BLOCK_DAYS))
    if count == 1:
        return [(start, end)]
    bounds = [start] + [days[(index * len(days)) // count] for index in range(1, count)] + [end]
    return [(bounds[i], bounds[i + 1]) for i in range(count)]


def plan_periods(periods, captures, workers):
    """Blocks for each recorded period, at most `workers` in all so every block of a chain runs at once
    (a block in its overrun may wait for its follower). Returns (blocks, chains of block indices)."""
    sizes = [max(1, len(trading_days(captures, p['start'], p['end']))) for p in periods]
    if len(periods) >= workers:
        shares = [1] * len(periods)
    else:
        total = sum(sizes)
        exact = [workers * size / total for size in sizes]
        shares = [max(1, int(value)) for value in exact]
        order = sorted(range(len(periods)), key=lambda i: exact[i] - int(exact[i]), reverse=True)
        for index in order:
            if sum(shares) >= workers:
                break
            shares[index] += 1
        while sum(shares) > workers:
            index = max((i for i in range(len(shares)) if shares[i] > 1), key=lambda i: shares[i])
            shares[index] -= 1
    blocks, chains = [], []
    for period, share in zip(periods, shares):
        planned = plan_blocks(period['start'], period['end'], captures, share)
        chains.append(list(range(len(blocks), len(blocks) + len(planned))))
        blocks.extend(planned)
    return blocks, chains


def overrun_until(block_end, chain_end, captures):
    """Where a block's overrun stops at the latest: CAP_DAYS trading days after its end, within its chain."""
    days = trading_days(captures, block_end, chain_end)
    return days[CAP_DAYS] if len(days) > CAP_DAYS else chain_end


def join_plans(periods, chains, captures):
    """{block index: its join plan} for the blocks of chains with more than one block.

    chain           the chain's block folders, in order (chunk_NNN beside this block's folder)
    position        this block's place in the chain
    start/end_ms    its own range
    until(_ms)      where its overrun ends at the latest (None for the chain's last block)
    record_until_ms up to where a block before may compare with this one (the latest of their overruns)"""
    from .settings import milliseconds
    plans = {}
    for chain in chains or ():
        if len(chain) < 2:
            continue
        chain_end = periods[chain[-1]][1]
        until = {index: overrun_until(periods[index][1], chain_end, captures) if position < len(chain) - 1 else None
                 for position, index in enumerate(chain)}
        for position, index in enumerate(chain):
            start, end = periods[index]
            before = [until[other] for other in chain[:position]]
            plans[index] = {'chain': [f'chunk_{other:03d}' for other in chain], 'position': position,
                            'start_ms': milliseconds(start), 'end_ms': milliseconds(end), 'until': until[index],
                            'until_ms': milliseconds(until[index]) if until[index] else None,
                            'record_until_ms': max((milliseconds(value) for value in before), default=0)}
    return plans


def real_plan(plan, real):
    """A join plan's bounds in the engine's real time (수정본172); its dates stay the recorded days."""
    converted = {key: real(plan[key]) for key in ('start_ms', 'end_ms') if plan.get(key) is not None}
    if plan.get('until_ms') is not None:
        converted['until_ms'] = real(plan['until_ms'])
    if plan.get('record_until_ms'):
        converted['record_until_ms'] = real(plan['record_until_ms'])
    return {**plan, **converted}


def _write_json(path, value):
    """Replace a small file whole; another block may be reading it (Windows refuses the replace then)."""
    path = Path(path)
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(json.dumps(value), encoding='utf-8')
    for attempt in range(100):
        try:
            os.replace(temporary, path)
            return
        except PermissionError:
            if attempt == 99:
                raise
            time.sleep(0.02)


class ProbeReader:
    """The probes a following block wrote so far, and how it ended once it has."""

    def __init__(self, folder):
        self.folder = Path(folder)
        self.reset()

    def reset(self):
        self.offset = 0
        self.buffer = b''
        self.probes = {}
        self.last = None
        self.generation = None

    def poll(self):
        path = self.folder / PROBES
        try:
            size = path.stat().st_size
        except OSError:
            size = 0
        if size < self.offset:
            self.reset()
        if size > self.offset:
            with path.open('rb') as handle:
                handle.seek(self.offset)
                data = handle.read(size - self.offset)
            self.offset += len(data)
            self.buffer += data
            *lines, self.buffer = self.buffer.split(b'\n')
            for line in lines:
                if not line.strip():
                    continue
                row = json.loads(line)
                if self.generation is not None and row.get('g') != self.generation:
                    self.probes = {}
                    self.last = None
                self.generation = row.get('g')
                self.probes[row['t']] = row
                self.last = row['t']
        return self.stop()

    def stop(self):
        try:
            return json.loads((self.folder / STOP).read_text('utf-8'))
        except (OSError, ValueError):
            return None


class JoinLane:
    """One lane's join work in one block: record for the blocks before, join to the blocks after."""

    def __init__(self, folder, plan, generation, cancelled):
        self.folder = Path(folder)
        self.plan = plan
        self.generation = generation
        self.cancelled = cancelled
        chain = [self.folder.parent / name for name in plan['chain']]
        position = plan['position']
        self.before = chain[:position]
        self.readers = [ProbeReader(folder) for folder in chain[position + 1:]]
        self.start_ms, self.end_ms = plan['start_ms'], plan['end_ms']
        self.until_ms = plan['until_ms']
        self.record_until_ms = plan['record_until_ms']
        self.hour = None
        self.stopped = None
        self.last_probe = None
        self.rows_at_end = None
        self.overrun_bundles = 0
        self.probes = 0
        self.digest_seconds = 0.0
        self.differences = None
        # A new attempt (runner._EveryFeedNeeded) starts a fresh record; a block still reading the old
        # one meanwhile sees it shrink (ProbeReader.reset) or the new generation.
        try:
            (self.folder / STOP).unlink()
        except OSError:
            pass
        self.handle = (self.folder / PROBES).open('w', encoding='utf-8', buffering=1)

    @property
    def joins(self):
        return bool(self.readers) and self.until_ms is not None

    def before_input(self, time_ms, rows):
        """Called before each input of this lane: notes the alert rows at the end of the block's own range."""
        if self.rows_at_end is None and time_ms >= self.end_ms:
            self.rows_at_end = rows
        if time_ms >= self.end_ms:
            self.overrun_bundles += 1

    def after_bundle(self, time_ms, rows, digest):
        """After the engine processed a market bundle; True when this lane is done."""
        hour = time_ms // PROBE_MS
        if hour == self.hour:
            return False
        self.hour = hour
        # Every bundle a block before may compare with: up to where the last of their overruns may end.
        # The record is kept even after they stopped: the join reads this block's rows at its bundle.
        recording = bool(self.before) and self.start_ms <= time_ms < self.record_until_ms
        overrun = self.joins and time_ms >= self.end_ms
        if not (recording or overrun):
            return False
        began = time.perf_counter()
        value, parts = digest()
        self.digest_seconds += time.perf_counter() - began
        self.probes += 1
        if recording:
            self.handle.write(json.dumps({'t': time_ms, 'd': value, 'p': parts, 'rows': rows, 'g': self.generation}) + '\n')
        if overrun:
            self.last_probe = (time_ms, rows)
            reference = self.reference(time_ms)
            if reference is not None and reference['d'] == value:
                self.finish('CONVERGED', time_ms, rows)
                return True
            # Which parts still differ (for the result): the state the join waits for.
            self.differences = (None if reference is None else
                                sorted(name for name in set(parts) | set(reference.get('p', {}))
                                       if parts.get(name) != reference.get('p', {}).get(name)))
        return False

    def reference(self, time_ms):
        """A following block's record after the bundle at time_ms: the first one that recorded it.

        This block replays continuously (the blocks before it met it), so any block whose state equals
        this block's after the same bundle is the continuous replay from there on; stitch() chooses the
        same block. A follower that stopped, ended or passed that bundle without recording it is
        skipped; one still behind is waited for."""
        for reader in self.readers:
            waited = time.monotonic()
            seen = None
            while True:
                stop = reader.poll()
                row = reader.probes.get(time_ms)
                if row is not None:
                    return row
                if stop is not None and stop.get('status') in ('FAILED', 'CANCELLED'):
                    return None
                if stop is not None and stop.get('status') != 'RUNNING':
                    break
                if reader.last is not None and reader.last > time_ms:
                    break
                if self.cancelled():
                    return None
                if reader.last != seen:
                    seen, waited = reader.last, time.monotonic()
                elif time.monotonic() - waited > WAIT_SECONDS:
                    return None
                time.sleep(0.2)
        return None

    def finish(self, status, time_ms=None, rows=None):
        if self.stopped is not None:
            return
        self.stopped = status
        _write_json(self.folder / STOP, {'status': status, 't': time_ms, 'rows': rows, 'rows_at_end': self.rows_at_end,
                                         'g': self.generation})

    def running(self):
        _write_json(self.folder / STOP, {'status': 'RUNNING', 't': None, 'rows': None, 'g': self.generation})

    def close(self, *, interrupted=False, failed=False, rows=None):
        """End of the block's input: a join that did not converge stops at its last probe (CAPPED)."""
        if self.rows_at_end is None:
            self.rows_at_end = rows
        if failed:
            self.finish('FAILED')
        elif interrupted:
            self.finish('CANCELLED')
        elif self.joins and self.last_probe is not None:
            self.finish('CAPPED', *self.last_probe)
        else:
            self.finish('END')
        self.handle.close()

    def summary(self):
        stop = json.loads((self.folder / STOP).read_text('utf-8'))
        return {**stop, 'overrun_bundles': self.overrun_bundles, 'probes': self.probes,
                'digest_seconds': round(self.digest_seconds, 3), 'differing_parts': self.differences}


# ---- after the replay: which block's rows the run keeps ---------------------------------------
def _probes(folder):
    reader = ProbeReader(folder)
    reader.poll()
    return reader.probes


def stitch(folders):
    """[(folder, first row, end row or None)] of one chain, and its joins.

    Follows the continuous replay: a block up to its join, then the first following block that
    recorded that bundle and had not stopped before it, from that block's rows after the bundle.
    A chain whose blocks did not all finish their joins keeps each block's own range."""
    stops = []
    for folder in folders:
        try:
            stops.append(json.loads((Path(folder) / STOP).read_text('utf-8')))
        except (OSError, ValueError):
            stops.append({'status': 'MISSING'})
    if any(stop.get('status') not in ('CONVERGED', 'CAPPED', 'END') for stop in stops) or \
            any(stop.get('status') == 'END' for stop in stops[:-1]):
        return ([(folder, 0, stop.get('rows_at_end')) for folder, stop in zip(folders, stops)],
                [{'status': 'UNJOINED', 'after_block': index} for index in range(len(folders) - 1)])
    probes = {}
    segments, seams = [], []
    current, first = 0, 0
    while True:
        stop = stops[current]
        if current == len(folders) - 1 or stop['status'] == 'END':
            segments.append((folders[current], first, None))
            break
        segments.append((folders[current], first, stop['rows']))
        following = None
        for index in range(current + 1, len(folders)):
            if index not in probes:
                probes[index] = _probes(folders[index])
            later = stops[index]
            if stop['t'] in probes[index] and not (later.get('t') is not None and later['t'] < stop['t']):
                following = index
                break
        if following is None:
            raise ValueError('이어 붙일 다음 블록의 기록이 없습니다: ' + str(folders[current]))
        seams.append({'status': stop['status'], 'at_ms': stop['t'], 'from_block': current, 'to_block': following})
        first = probes[following][stop['t']]['rows']
        current = following
    return segments, seams


def kept_rows(folder, first, end, out):
    """Write the rows [first, end) of a block's alerts.csv to `out`; return their alert_rows entries."""
    folder = Path(folder)
    with (folder / 'alerts.csv').open(encoding='utf-8', newline='') as source, \
            Path(out).open('w', encoding='utf-8', newline='') as target:
        reader = csv.reader(source)
        writer = csv.writer(target)
        writer.writerow(next(reader))
        for index, row in enumerate(reader):
            if index >= first and (end is None or index < end):
                writer.writerow(row)
    try:
        rows = json.loads((folder / ROWS).read_text('utf-8'))
    except (OSError, ValueError):
        rows = []
    return rows[first:end]
