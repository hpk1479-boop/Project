"""Real full coordinator, not a state-machine microbenchmark.

Two reader arms use identical raw records, parameters, cadence, worker isolation,
state machines and disabled existing disk caches. Profiling is a SEPARATE run:
its inflated wall time is never used as the unprofiled speed measurement.
"""
import argparse
import cProfile
import hashlib
import json
import os
from pathlib import Path
try:
    import resource
except ImportError:  # Windows: psutil sampling still works when installed.
    resource = None
import sys
import threading
import time
import uuid


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    p.add_argument('--reader', choices=('legacy', 'current'), default='current')
    p.add_argument('--plugin', default='WATCH_OZ')
    p.add_argument('--mode', default='ONE_MINUTE_CLOSE')
    p.add_argument('--rows', type=int, default=50000)
    p.add_argument('--archive', default=None)
    p.add_argument('--profile', action='store_true')
    p.add_argument('--timings', action='store_true', help='Separate instrumented phase-timing run; not used for headline wall-clock speed')
    p.add_argument('--allow-original-close', action='store_true', help='Test-only guard bypass for the supplied, otherwise blocked baseline')
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    root = args.root.resolve()
    sys.path[:0] = [str(root), str(Path(__file__).resolve().parent)]
    import numpy as np
    import pandas as pd
    try:
        import psutil
    except ImportError:
        psutil = None
    from generic_backtest import runner, conditional
    from generic_backtest.contracts import GenericRunConfig
    from generic_backtest.canonical import file_hash, read_json
    from generic_backtest.watch.compiler import compile_watch
    from generic_backtest.results import verify_result, read_lines
    from reference_reader import LegacyArchiveReader
    from pit.models import TickRecord
    if args.reader == 'legacy':
        runner.GenericArchiveReader = LegacyArchiveReader
    if args.allow_original_close:
        conditional.validate_cadence = lambda config: None
    phase_times = {}
    if args.timings:
        from pit.archive.reader import FrozenTickReader
        from generic_backtest.market import GenericMarketCore
        from generic_backtest.worker import StrategyWorker
        phase_times = dict(archive_preflight_s=0., iteration_chunk_open_s=0.,
                           reader_next_s=0., market_state_s=0., strategy_roundtrip_s=0.)
        inside_iteration = [False]
        open_original = FrozenTickReader.open
        def timed_open(cls, *a, **k):
            t = time.perf_counter()
            try: return open_original(*a, **k)
            finally:
                if inside_iteration[0]: phase_times['iteration_chunk_open_s'] += time.perf_counter()-t
        FrozenTickReader.open = classmethod(timed_open)
        reader_class = runner.GenericArchiveReader
        class TimedReader(reader_class):
            def __init__(self, *a, **k):
                t = time.perf_counter()
                try: super().__init__(*a, **k)
                finally: phase_times['archive_preflight_s'] += time.perf_counter()-t
            def __iter__(self):
                iterator = iter(super().__iter__())
                while True:
                    t = time.perf_counter(); inside_iteration[0] = True
                    try: tick = next(iterator)
                    except StopIteration: return
                    finally:
                        phase_times['reader_next_s'] += time.perf_counter()-t
                        inside_iteration[0] = False
                    yield tick
        runner.GenericArchiveReader = TimedReader
        step_original = GenericMarketCore.step
        def timed_step(self, *a, **k):
            t = time.perf_counter()
            try: return step_original(self, *a, **k)
            finally: phase_times['market_state_s'] += time.perf_counter()-t
        GenericMarketCore.step = timed_step
        observe_original = StrategyWorker.observe
        def timed_observe(self, *a, **k):
            t = time.perf_counter()
            try: return observe_original(self, *a, **k)
            finally: phase_times['strategy_roundtrip_s'] += time.perf_counter()-t
        StrategyWorker.observe = timed_observe
    raw = root / (args.archive or f'generic_cache/cadence_input_validation/real_{args.rows}')
    m = read_json(raw / 'manifest.json')
    first = next(d for d in m['chunks'] if d['count'])
    start = int(np.load(raw/first['file'], mmap_mode='r')['time_msc'][0])*1_000_000
    parameters = {}
    plugin = args.plugin
    if plugin.startswith('WATCH'):
        command = {'WATCH_BAR': '5분봉 마감 알려줘',
                   'WATCH_OZ': '1시간 추세 상승이고 5분 올존 알려줘',
                   'WATCH_TREND_OZ': '15분 추세 상승이고 1분 올존 알려줘'}[plugin]
        plan = compile_watch(command, m['instrument']['broker_symbol'])
        parameters = {'plan_json': json.dumps(plan, ensure_ascii=False)}
        plugin = 'WATCH_UI_V1'
    config = GenericRunConfig(mode='ALERT_ONLY', plugin_id=plugin,
        plugin_sha256=file_hash(root/'BACKTEST_SPECIAL'/f'{plugin}.py'),
        parameters=parameters, instrument=m['instrument'], start_ns=start,
        end_ns=m['coverage_end_ns'], calendar={'kind': 'UTC_GRID_RESEARCH_V1', 'explicit_research_choice': True},
        archive=str(raw), evaluation_mode=args.mode, session_filter={'enabled': False},
        resources={'max_history_bars': 100000, 'max_occurrences': 1000000,
                   'worker_timeout_seconds': 120, 'max_ipc_bytes': 32*1024*1024,
                   'disk_cache': False, 'profile_stages': args.profile, 'profile_ipc': args.profile})
    result_dir = root/'generic_runs/cadence_input_validation'/('bench-'+uuid.uuid4().hex)
    prof = cProfile.Profile() if args.profile else None
    stop = threading.Event()
    peak = {'parent_rss_bytes': 0, 'process_tree_rss_bytes': 0}
    process = psutil.Process() if psutil is not None else None
    def sample():
        while process is not None and not stop.is_set():
            try:
                own = process.memory_info().rss
                total = own + sum(c.memory_info().rss for c in process.children(recursive=True) if c.is_running())
                peak['parent_rss_bytes'] = max(peak['parent_rss_bytes'], own)
                peak['process_tree_rss_bytes'] = max(peak['process_tree_rss_bytes'], total)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
            stop.wait(.02)
    sampler = threading.Thread(target=sample, daemon=True)
    sampler.start()
    t = time.perf_counter()
    try:
        if prof: prof.enable()
        result = runner.GenericRunCoordinator(config).run(result_dir)
        if prof: prof.disable()
        wall = time.perf_counter()-t
    finally:
        if prof: prof.disable()
        stop.set(); sampler.join()
    manifest = verify_result(result_dir)
    meta = manifest['metadata']
    execution = meta['execution']
    actual = execution['last_committed_raw_cursor']
    result_files = {}
    for path in result_dir.glob('*.jsonl'):
        result_files[path.name] = {'sha256': file_hash(path), 'records': len(read_lines(path))}
    if phase_times:
        phase_times['row_object_conversion_s'] = phase_times['reader_next_s']-phase_times['iteration_chunk_open_s']
        phase_times['data_loading_and_verification_s'] = phase_times['archive_preflight_s']+phase_times['iteration_chunk_open_s']
    out = {'plugin': args.plugin, 'reader': args.reader, 'mode': args.mode, 'profiled': args.profile,
           'phase_timing_instrumented': args.timings, 'phase_times': phase_times,
           'original_close_guard_bypassed': args.allow_original_close,
           'wall_seconds': wall, 'raw_observations': actual, 'raw_observations_per_second': actual/wall,
           'peak_memory': dict(peak, parent_ru_maxrss_bytes=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*1024 if resource is not None else None),
           'result_status': result['status'], 'result_dir': str(result_dir), 'result_files': result_files,
           'archive_identity': m['archive_identity'], 'input_count': m['count'],
           'input_kind': m['input_kind'], 'validation_subset': m.get('validation_subset'),
           'evaluation_schedule': meta.get('evaluation_schedule'),
           'execution': execution, 'conditional': meta.get('conditional_computation'),
           'worker_finish_diagnostics': meta.get('worker_finish_diagnostics'),
           'worker_ipc_metrics': meta.get('worker_ipc_metrics'),
           'environment': {'python': sys.version, 'numpy': np.__version__, 'pandas': pd.__version__}}
    if prof:
        entries = prof.getstats()
        rows = [{'file': getattr(e.code, 'co_filename', '<built-in>'),
                 'line': getattr(e.code, 'co_firstlineno', 0),
                 'function': getattr(e.code, 'co_name', str(e.code)),
                 'calls': e.callcount, 'self_seconds': e.inlinetime, 'cumulative_seconds': e.totaltime}
                for e in entries]
        rows.sort(key=lambda r:r['self_seconds'], reverse=True)
        out['parent_profile'] = rows
        out['tickrecord_constructor_calls'] = sum(e.callcount for e in entries if e.code is TickRecord.__init__.__code__)
        out['worker_profiles'] = meta.get('worker_profiles')
        prof.dump_stats(str(args.output.with_suffix('.prof')))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({k:out[k] for k in ('plugin','reader','mode','profiled','wall_seconds','raw_observations','raw_observations_per_second','result_status')}, ensure_ascii=False), flush=True)

if __name__ == '__main__':
    main()
