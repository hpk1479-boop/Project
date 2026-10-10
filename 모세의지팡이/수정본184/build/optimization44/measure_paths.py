"""AB/BA benchmarks of the supplied input and revision44, without profiling."""
from pathlib import Path
import argparse
import json
import platform
import statistics
import sys
import tempfile
import time
from unittest.mock import patch

from support44 import ROOT, alert, frame, write_alerts, config, load_comparison, clean_result
from event_backtest import virtual_entry as current, virtual_source

OUT = ROOT / '검증결과/가상진입최적화44'


def measure(setups, repeat, batch):
    samples = {name: [] for name in setups}
    for iteration in range(-1, repeat):
        names = list(setups)
        if iteration % 2:
            names.reverse()
        results = {}
        for name in names:
            invokes = [setups[name]() for _ in range(batch)]
            began = time.perf_counter()
            for invoke in invokes:
                value = invoke()
            elapsed = (time.perf_counter() - began) / batch
            results[name] = value
            if iteration >= 0:
                samples[name].append(elapsed)
        assert results['input43'] == results['revision44'], 'Diagnostic output difference'
    medians = {name: statistics.median(values) for name, values in samples.items()}
    return {'samples_seconds': samples, 'median_seconds': medians,
            'reduction_percent': 100 * (1 - medians['revision44'] / medians['input43']),
            'diagnostic_outputs_equal': True}


def exits_setup(module):
    # All nine experiments stay open; 648 completed minutes are evaluated after entry.
    rows = [(i * 60, 10., 11., 9., 9., 8.) for i in range(650)]
    view = frame(rows, projected=True)
    inputs = [alert(str(i), stop=-100.) for i in range(48)]
    def setup():
        calculator = module.VirtualEntry(inputs)
        calculator.observe(0, {'1m': view}, end_ms=86_400_000)
        def invoke():
            calculator.observe(649 * 60_000, {'1m': view}, end_ms=86_400_000)
            return (len(calculator.open), calculator.last_m1,
                    tuple((t['entry_price'], t['risk'], len(t['exits'])) for t in calculator.trades))
        return invoke
    return setup


def results_setup(module):
    calculator = module.VirtualEntry([])
    statuses = ('WAITING', 'PASS_CROSS', 'PASS_RISK', 'ENTERED', 'ENTERED')
    for i in range(2400):
        status = statuses[i % len(statuses)]
        entered = status == 'ENTERED'
        exits = {rr: dict(result='WIN' if i % 2 else 'LOSS', r=rr if i % 2 else -1.,
                          exit_time=120_000, target=10. + rr * 2.)
                 for rr in module.RATIOS[:(i % 10)]} if entered else {}
        calculator.trades.append(dict(alert=alert(str(i), strategy='SPECIAL' + str(1 + i % 7)),
                                      status=status, entry_time=60_000 if entered else None,
                                      entry_price=10. if entered else None, stop_price=8., exits=exits))
    return lambda: calculator.results


def pipeline_setup(module, folder, source):
    alerts = [alert(str(i), stop=-100.) for i in range(48)]
    path = write_alerts(folder / 'alerts.csv', alerts)
    out = folder / 'run'
    out.mkdir(exist_ok=True)
    def invoke():
        with patch.object(virtual_source, 'shared_observations', source):
            result = module.calculate(path, [], folder, config(), {'POINT_XAUUSD+': '.01'}, out)
        return (clean_result(result), (out / 'virtual_summary.csv').read_bytes(),
                (out / 'virtual_trades.csv').read_bytes())
    return lambda: invoke


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--original', type=Path, required=True)
    parser.add_argument('--samples', type=int, default=7)
    parser.add_argument('--batch', type=int, default=5)
    args = parser.parse_args()
    if args.batch < 1:
        parser.error('Batch size must be positive.')
    if args.samples < 3:
        parser.error('At least three timing samples are required.')
    original = load_comparison(args.original)
    modules = {'input43': original, 'revision44': current}
    report = {'environment': {'python': platform.python_version(), 'platform': platform.system()},
              'samples_per_case': args.samples, 'calls_per_sample': args.batch, 'seconds_normalized_per_call': True, 'order': 'Warmup then alternating AB/BA',
              'scope': 'Synthetic in-memory inputs and local CSV only. No actual full backtest speed claim.',
              'cases': {}}
    report['cases']['open_exits_48_trades_650_rows'] = measure(
        {name: exits_setup(module) for name, module in modules.items()}, args.samples, args.batch)
    print('open exits', report['cases']['open_exits_48_trades_650_rows']['reduction_percent'], flush=True)
    report['cases']['results_2400_trades_7_strategies'] = measure(
        {name: results_setup(module) for name, module in modules.items()}, args.samples, args.batch)
    print('results', report['cases']['results_2400_trades_7_strategies']['reduction_percent'], flush=True)
    feeds = []
    history = [(i * 60, 10., 11., 9., 9., 8.) for i in range(-649, 0)]
    tfs = ('1m', '3m', '5m', '10m', '15m', '20m', '30m', '1h', '2h', '4h', '8h', '1d')
    for i in range(128):
        history.append((i * 60, 10., 11., 9., 9., 8.))
        view = frame(history[-650:], projected=True)
        feeds.append((i * 60_000, {('XAUUSD+', tf): view for tf in tfs}))
    def source(*args, check=lambda: None, **kwargs):
        for item in feeds:
            check()
            yield item
    with tempfile.TemporaryDirectory(prefix='virtual44-measure-') as folder:
        setups = {}
        for name, module in modules.items():
            place = Path(folder) / name
            place.mkdir()
            setups[name] = pipeline_setup(module, place, source)
        report['cases']['calculate_48_alerts_128_observations_12_feeds'] = measure(setups, args.samples, args.batch)
    print('calculate', report['cases']['calculate_48_alerts_128_observations_12_feeds']['reduction_percent'], flush=True)
    # Count work separately from wall time; this is not a general input-frequency claim.
    counts = {}
    for name, module in modules.items():
        scans = []
        multiplied = []
        class Feeds(dict):
            def items(self):
                scans.append(1)
                return super().items()
        class Ratio(float):
            def __rmul__(self, other):
                multiplied.append(1)
                return other * float(self)
        def counted_source(*args, **kwargs):
            yield 0, Feeds(feeds[0][1])
        with tempfile.TemporaryDirectory(prefix='virtual44-count-') as folder:
            pipeline_setup(module, Path(folder), counted_source)()()
        with patch.object(module, 'RATIOS', tuple(Ratio(rr) for rr in module.RATIOS)):
            exits_setup(module)()()
        counts[name] = {'feed_dictionary_builds_48_same_symbol_alerts_one_observation': len(scans),
                        'rr_multiplications_48_open_trades_648_minutes': len(multiplied)}
    report['operation_counts'] = counts
    # Cached targets add storage per entered trade; retain it in the report.
    example = current.VirtualEntry([alert()])
    example.observe(0, {'1m': frame([(0, 10., 11., 9., 9., 8.)])}, end_ms=1_000_000)
    example.observe(60_000, {'1m': frame([(0, 10., 11., 9., 9., 8.), (60, 10., 11., 9., 9., 8.)])}, end_ms=1_000_000)
    targets = example.trades[0]['targets']
    report['target_cache_storage'] = {'values_per_entered_trade': len(targets),
        'shallow_dict_and_float_bytes_in_this_python': sys.getsizeof(targets) + sum(sys.getsizeof(v) for v in targets.values()),
        'excludes_parent_trade_dict_and_allocator_overhead': True,
        'lifetime': 'Only until all nine exits close; cache then deleted, never emitted to CSV.'}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / 'measurements.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print('counts', counts, flush=True)


if __name__ == '__main__':
    main()
