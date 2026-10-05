"""Summarize unprofiled wall clocks and assert corresponding result equivalence."""
import argparse
import json
import re
from pathlib import Path
from statistics import median


def semantic(value):
    if isinstance(value, dict):
        return {k: semantic(v) for k, v in value.items() if not k.endswith('_seconds')}
    if isinstance(value, list):
        return [semantic(v) for v in value]
    return value


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('directory', type=Path)
    p.add_argument('--output', type=Path)
    args = p.parse_args()
    source = args.directory
    paired = []
    for path in sorted(source.glob('*_legacy.json')):
        new_path = path.with_name(path.name.replace('_legacy.json', '_current.json'))
        if not new_path.exists():
            continue
        old = json.loads(path.read_text(encoding='utf-8'))
        new = json.loads(new_path.read_text(encoding='utf-8'))
        keys = ('observations', 'archive_identity', 'last_source_ordinal', 'last_tick_hex') if old.get('scope') else (
            'plugin', 'mode', 'input_count', 'raw_observations', 'archive_identity', 'result_status',
            'result_files', 'execution', 'evaluation_schedule', 'conditional', 'worker_finish_diagnostics')
        for key in keys:
            assert semantic(old.get(key)) == semantic(new.get(key)), (path.name, key)
        paired.append({'legacy': path.name, 'current': new_path.name, 'checked_keys': list(keys), 'status': 'EXACT_EQUAL'})

    measurements = []
    for path in sorted(source.glob('*_r*_legacy.json')):
        if re.search(r'_r[0-9]+_legacy\.json$', path.name) is None:
            continue
        old = json.loads(path.read_text(encoding='utf-8'))
        newpath = path.with_name(path.name.replace('_legacy.json', '_current.json'))
        if not newpath.exists():
            continue
        new = json.loads(newpath.read_text(encoding='utf-8'))
        assert not any(x['profiled'] or x.get('phase_timing_instrumented') for x in (old, new))
        measurements.append((old, new))
    groups = sorted({(o['plugin'], o['input_count'], o['mode']) for o, _ in measurements})
    headlines = []
    for plugin, count, mode in groups:
        pairs = [(o, n) for o, n in measurements if (o['plugin'], o['input_count'], o['mode']) == (plugin, count, mode)]
        arms = {}
        for index, arm in enumerate(('legacy', 'current')):
            data = [pair[index] for pair in pairs]
            walls = [a['wall_seconds'] for a in data]
            wall = median(walls)
            arms[arm] = {'wall_seconds_median': wall, 'wall_seconds_all': walls,
                         'raw_observations_per_second_at_median': count/wall,
                         'parent_rss_mib_median': median(a['peak_memory']['parent_rss_bytes']/2**20 for a in data),
                         'process_tree_rss_mib_median': median(a['peak_memory']['process_tree_rss_bytes']/2**20 for a in data),
                         'alerts': data[0]['result_files']['alerts.jsonl']['records'],
                         'callback_count': data[0]['evaluation_schedule']['roles']['SIGNAL']['strategy_callback_opportunities']}
        old, new = arms['legacy']['wall_seconds_median'], arms['current']['wall_seconds_median']
        headlines.append(dict(plugin=plugin, raw_observations=count, mode=mode, repeats=len(pairs),
                              speedup=old/new, wall_reduction_percent=100*(1-new/old), **arms))
    phase_times = []
    for path in sorted(source.glob('*_timings_legacy.json')):
        newpath = path.with_name(path.name.replace('_legacy.json', '_current.json'))
        if newpath.exists():
            old, new = (json.loads(p.read_text(encoding='utf-8')) for p in (path, newpath))
            phase_times.append({'plugin': old['plugin'], 'raw_observations': old['raw_observations'],
                                'legacy': old['phase_times'], 'current': new['phase_times']})
    calls = []
    for path in sorted(source.glob('*_profile_legacy.json')):
        newpath = path.with_name(path.name.replace('_legacy.json', '_current.json'))
        if not newpath.exists():
            continue
        old, new = (json.loads(p.read_text(encoding='utf-8')) for p in (path, newpath))
        arms = {}
        for data, arm in ((old, 'legacy'), (new, 'current')):
            records = data['parent_profile']
            def ncalls(function, suffix=None):
                return sum(r['calls'] for r in records if r['function'] == function and (suffix is None or r['file'].endswith(suffix)))
            arms[arm] = {'TickRecord_constructor': data['tickrecord_constructor_calls'],
                         'TickRecord_from_bytes': ncalls('from_bytes', 'pit/models.py'),
                         'FrozenTickReader_next': ncalls('__next__', 'pit/archive/reader.py'),
                         'dataclasses_replace_all_types': ncalls('replace', 'dataclasses.py'),
                         'numpy_scalar_tobytes': sum(r['calls'] for r in records if 'tobytes' in r['function'] and 'numpy.generic' in r['function']),
                         'numpy_array_tobytes_all_including_preflight': sum(r['calls'] for r in records if 'tobytes' in r['function'] and 'numpy.ndarray' in r['function'])}
        calls.append(dict(plugin=old['plugin'], raw_observations=old['raw_observations'], **arms))
    overall = None
    if headlines:
        old = sum(h['legacy']['wall_seconds_median'] for h in headlines)
        new = sum(h['current']['wall_seconds_median'] for h in headlines)
        overall = {'scope': 'SUM_OF_PER_PATH_MEDIANS_NOT_FULL_ARCHIVE_OR_UNIVERSAL_SPEED',
                   'legacy_seconds': old, 'current_seconds': new, 'speedup': old/new,
                   'wall_reduction_percent': 100*(1-new/old)}
    result = {'comparison_status': 'ALL_AVAILABLE_PAIRS_EXACT_EQUAL', 'pair_count': len(paired),
              'pairs': paired, 'headline_full_backtests': headlines, 'aggregate': overall,
              'separate_instrumented_phase_times': phase_times, 'separate_cprofile_call_counts': calls}
    out = args.output or source/'benchmark_summary.json'
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'pairs': len(paired), 'paths': len(headlines), 'aggregate': overall}, ensure_ascii=False))

if __name__ == '__main__':
    main()
