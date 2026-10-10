"""Compile the frozen revision-6 EA and record an actual, isolated MT5 pass."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / '검증결과/staff_s0'
sys.path.insert(0, str(ROOT / 'Part2'))
from generic_backtest import native_mt5 as native
from part1_host import capture


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    profile = json.loads((OUT / 'mt5_profile.json').read_text('utf-8-sig'))
    terminal = Path(profile['data_root'])
    work = OUT / 'actual_mt5'
    work.mkdir(parents=True, exist_ok=True)
    original = {r['path']: r['sha256'] for r in json.loads((OUT / 'source6_manifest.json').read_text('utf-8-sig'))}
    source = OUT / 'baseline_input/Part1/program/MT5'
    mql = terminal / 'MQL5'
    (mql / 'Experts').mkdir(parents=True, exist_ok=True)
    (mql / 'Indicators').mkdir(parents=True, exist_ok=True)
    editor = terminal / 'metaeditor64.exe'
    hidden = subprocess.STARTUPINFO()
    hidden.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    hidden.wShowWindow = 0
    builds = []
    for name in (*native.NATIVE_MQL_SOURCES, 'STAFF_Identity_Status.mqh'):
        origin = source / name
        assert sha(origin) == original['Part1/program/MT5/' + name], name
        folder = 'Experts' if name.startswith(('THE_STAFF', 'STAFF_Identity')) else 'Indicators'
        target = mql / folder / name
        shutil.copy2(origin, target)
    for name in native.NATIVE_MQL_SOURCES:
        target = mql / ('Experts' if name == 'THE_STAFF_OF_MOSES.mq5' else 'Indicators') / name
        args = [str(editor), '/portable', '/compile:' + str(target), '/inc:' + str(mql), '/log']
        started = time.perf_counter()
        done = subprocess.run(args, cwd=terminal, startupinfo=hidden, timeout=120,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        log = target.with_suffix('.log')
        details = native._read_compile_log(log)
        (work / (target.stem + '_compile.log')).write_text(details, encoding='utf-8')
        compiled = target.with_suffix('.ex5')
        successful = compiled.is_file() and compiled.stat().st_size > 0 and '0 errors' in details
        item = {'source': name, 'source_sha256': sha(target), 'compiler_sha256': sha(editor),
                'exit_code': done.returncode, 'wall_s': time.perf_counter() - started,
                'success': successful, 'ex5_sha256': sha(compiled) if compiled.is_file() else None}
        builds.append(item)
        (work / 'compile.json').write_text(json.dumps(builds, indent=2), encoding='utf-8')
        print('COMPILE ' + name + ': ' + str(successful), flush=True)
        if not successful:
            raise RuntimeError('Baseline MQL compile failed; see preserved compile log: ' + name)
    start = int(dt.datetime(2026, 9, 23, 12, 0, tzinfo=dt.timezone.utc).timestamp())
    end = start + 240
    launched = time.monotonic()
    events = []

    def emit(kind, value):
        events.append({'kind': kind, 'value': value})
        (work / 'progress.json').write_text(json.dumps(events, indent=2), encoding='utf-8')
        print(value.get('message_code', kind), value.get('elapsed_seconds', ''), flush=True)

    def cancel():
        if time.monotonic() - launched > 900:
            raise TimeoutError('S0 isolated MT5 pass exceeded 15 minutes')

    popen = subprocess.Popen
    def hidden_popen(*args, **kwargs):
        kwargs.setdefault('startupinfo', hidden)
        return popen(*args, **kwargs)

    with patch.object(subprocess, 'Popen', hidden_popen):
        result = native.run_native_tester(profile, 'XAUUSD+', start * 10**9, end * 10**9,
                    work, emit=emit, cancel=cancel, shutdown_terminal=True, start_timeout=120)
    export = Path(result['export'])
    retained = work / ('capture_' + result['session'])
    shutil.copytree(export, retained)
    files = {p.name: sha(p) for p in retained.iterdir() if p.is_file()}
    assert files == {p.name: sha(p) for p in export.iterdir() if p.is_file()}
    info = capture.parse_capture_manifest(retained)
    feeds = []
    for feed in info['feeds']:
        counts = {1: 0, 2: 0}
        observed = []
        for kind, second, flags, times, volumes, values in capture._read_records(feed.path, feed.records):
            assert start <= second < end
            assert len(times) == len(volumes) == len(values)
            assert values.shape[1] == 45
            counts[kind] += 1
            observed.append(second)
        assert observed and all(a < b for a, b in zip(observed, observed[1:]))
        replayed = sum(1 for _ in capture.FeedReplay(info['symbol'], feed))
        assert replayed == feed.records
        feeds.append({'tf': feed.timeframe, 'records': feed.records, 'full': counts[1],
                      'row': counts[2], 'first': observed[0], 'last': observed[-1]})
    assert len(feeds) == 19
    result.update({'retained_export': str(retained), 'files_sha256': files, 'pipe_feeds': feeds,
                   'start_s': start, 'end_s': end, 'source_revision': '수정본6 (hash-verified copy)',
                   'compiler': str(editor), 'actual_mt5_execution': True,
                   'original_terminal_untouched': True, 'validation': 'PASS'})
    (work / 'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print('ACTUAL CAPTURE VERIFIED: ' + str(retained), flush=True)


if __name__ == '__main__':
    main()
