"""Diagnostic comparison on real reader/writer paths using newly generated captures."""
from pathlib import Path
import argparse
import csv
import io
import json
import tempfile
from unittest.mock import patch

from support44 import ROOT, recorded_fixture, load_comparison, clean_result
from event_backtest import virtual_entry

OUT = ROOT / '검증결과/가상진입최적화44'


def execute(module, path, captures, root, scenario, *, stop_after_entry=False):
    out = root / 'runs' / 'synthetic-run'
    out.mkdir(parents=True, exist_ok=True)
    stopped = [False]
    original = module.VirtualEntry.observe
    def observe(self, stamp, feeds, **kwargs):
        value = original(self, stamp, feeds, **kwargs)
        if stop_after_entry and stamp >= 1_756_684_860_000:
            stopped[0] = True
        return value
    with patch.object(module.VirtualEntry, 'observe', observe):
        result = module.calculate(path, captures, root, scenario, {}, out, cancel=lambda: stopped[0])
    return clean_result(result), (out / 'virtual_summary.csv').read_bytes(), (out / 'virtual_trades.csv').read_bytes()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--original', type=Path, required=True)
    args = parser.parse_args()
    before = load_comparison(args.original)
    report = {'scope': 'Synthetic file-backed virtual postprocessing; not live trading or a full strategy backtest.',
              'comparison_role': 'Change diagnostic, in addition to explicit expected-condition tests.', 'cases': []}
    for fmt, published in [('MSD1', False), ('MSD2', False), ('MSD2', True)]:
        with tempfile.TemporaryDirectory(prefix='virtual44-recorded-') as folder:
            root = Path(folder)
            path, captures, scenario, packets = recorded_fixture(root, format=fmt, published=published)
            for partial in (False, True):
                old = execute(before, path, captures, root, scenario, stop_after_entry=partial)
                new = execute(virtual_entry, path, captures, root, scenario, stop_after_entry=partial)
                assert old == new, (fmt, published, partial)
                rows = list(csv.DictReader(io.StringIO(new[2].decode('utf-8-sig'))))
                assert len(rows) == 18 and [x['signal_id'] for x in rows] == ['first'] * 9 + ['second'] * 9
                if not partial:
                    assert [x['result'] for x in rows] == ['WIN'] * 3 + ['LOSS'] * 15
                else:
                    assert [x['result'] for x in rows] == ['UNCLOSED'] * 9 + ['WAITING'] * 9
                row = {'format': fmt, 'published_projection': published, 'cancel_after_first_entry': partial,
                       'file_bundle_count': len(packets), 'observed_signals': new[0]['observed_signals'],
                       'read_bundles': new[0]['read_bundles'], 'csv_rows': len(rows),
                       'summary_and_trade_csv_equal': True, 'non_timing_result_fields_equal': True,
                       'independent_expected_results_match': True, 'reader_metrics': new[0]['reader_metrics']}
                report['cases'].append(row)
                print(row, flush=True)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / 'recorded_comparison.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
