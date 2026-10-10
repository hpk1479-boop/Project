"""Seal only completed optimization evidence; never invent unfinished results."""
from pathlib import Path
import argparse, json, subprocess, sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / '검증결과/engine_optimization'


def read(path):
    return json.loads(path.read_text('utf-8-sig'))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--warehouse', required=True)
    parser.add_argument('--previous', required=True)
    args = parser.parse_args()
    assert read(OUT / 'measurements_complete.json')['complete']
    assert read(OUT / 'year_build/mt5_restoration.json')['equal']
    build = read(OUT / 'year_build/complete.json')
    assert all(c['reconstruction_verified'] and not c.get('history_missing') for c in build['captures'])
    year = read(OUT / 'year_special1.json')
    assert len(year['chunks']) == 12 and year['cores'] == 14
    assert year['scenario']['start'] == '2024-10-01' and year['scenario']['end'] == '2025-10-01'
    assert all(v['bundles'] > 0 for v in year['chunks'])
    assert all(all(v['fields_equal'].values()) and not v['errors'] for v in read(OUT / 'live_replay.json').values())
    assert not read(OUT / 'before_source_preservation.json')['different']
    tests = 0
    for name in ('related_tests.xml', 'records_io_tests.xml'):
        suites = ET.parse(OUT / name).getroot()
        for suite in suites.iter('testsuite'):
            assert int(suite.get('failures', '0')) == int(suite.get('errors', '0')) == 0
            tests += int(suite.get('tests', '0'))
    def execute(script, *extra):
        subprocess.run([sys.executable, '-X', 'utf8', '-B', 'build/' + script, *extra], cwd=ROOT, check=True)
    execute('analyze_engine_alerts.py')
    analysis = read(OUT / 'alert_difference_analysis.json')
    assert all(v['content_equal'] for v in analysis['comparisons'].values()), 'alert content differences need investigation'
    assert all(v['classification'] != 'order_only_requires_analysis' for v in analysis['comparisons'].values()), 'alert ordering needs investigation'
    if not (OUT / 'integrity_registration.json').exists():
        execute('seal_engine_optimization.py', '--previous', args.previous)
    assert not read(OUT / 'integrity_registration.json')['new_errors']
    execute('report_engine_optimization.py', '--warehouse', args.warehouse)
    status = {'complete': True, 'scope': 'revision25_engine_composer_A', 'tests_passed': tests,
              'live_replay_equal': True, 'capture_hashes_verified': len(build['captures']),
              'year_run_id': year['run_id'], 'year_parallel_seconds': year['replay_seconds'],
              'mt5_restored': True, 'new_integrity_errors': 0,
              'diagnostics': 'alert_difference_analysis.json',
              'existing_limitation': 'SPECIAL4/5 standalone selection omits plugin maintenance polls; unchanged',
              'next_stage_started': False}
    (OUT / 'status.json').write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(status, ensure_ascii=False))


if __name__ == '__main__':
    main()
