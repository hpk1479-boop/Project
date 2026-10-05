"""Replay the uploaded final and UI-only revision with identical internal bounds.

Example (separate interpreters are required):
  python validation_suite/validate_ui_regression.py --root ORIGINAL --out before.json --raw-root RAW
  python validation_suite/validate_ui_regression.py --root MODIFIED --out after.json --raw-root RAW --ui-dates --seed-cache-from ORIGINAL

Synthetic inputs are not evidence of a real Windows/MT5 connection. No retained
historical baseline/step ZIP is loaded or restored by this script.
"""
from pathlib import Path
from types import SimpleNamespace
import argparse
import hashlib
import json
import os
import shutil
import sys
import time
import traceback


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True)
    parser.add_argument('--out', required=True)
    parser.add_argument('--raw-root', required=True)
    parser.add_argument('--ui-dates', action='store_true')
    parser.add_argument('--seed-cache-from')
    args = parser.parse_args()
    root = Path(args.root).resolve()
    sys.path.insert(0, str(root))
    sys.path.insert(1, str(root / 'validation_suite'))
    import numpy as np
    from dataclasses import replace
    from generic_backtest.canonical import plain, identity, write_json, read_json
    from generic_backtest.contracts import GenericRunConfig
    from generic_backtest.gui import GenericPanel, parse_kst_ns
    from generic_backtest.plugins import parse_literal_metadata
    from generic_backtest.history.cache import RawChunkWriter, verify_archive
    from generic_backtest.runner import GenericRunCoordinator, prepare_plan
    from generic_backtest.results import verify_result, read_lines
    from generic_backtest.fast.disk_cache import calculation_version
    from generic_backtest.watch.compiler import compile_watch
    from generic_backtest.session_filter import default_definition
    from generic_backtest.quality import QUALITY_LABELS as EVALUATION_LABELS
    from pit.archive.reader import TICK_DTYPE
    from integration_fixtures import INSTRUMENT, CAL, trade_config

    end = parse_kst_ns('2026-09-23')
    raw_root = Path(args.raw_root).resolve(); raw_root.mkdir(parents=True, exist_ok=True)
    output_json = Path(args.out).resolve()
    tag = 'after' if args.ui_dates else 'before'
    run_root = root / 'generic_runs' / 'ui_request_regression' / tag
    if run_root.exists():
        raise RuntimeError(f'Refusing to overwrite existing evidence: {run_root}')
    cache_rel = Path('generic_cache/ui_request_regression')
    if args.seed_cache_from:
        source = Path(args.seed_cache_from).resolve() / cache_rel
        if (root / cache_rel).exists():
            raise RuntimeError('Target regression cache already exists')
        shutil.copytree(source, root / cache_rel)

    def archive(start, step):
        folder = raw_root / f'{start}_{step}'
        if (folder / 'manifest.json').exists():
            return folder, verify_archive(folder)
        coverage_start = start - 4 * 3600 * 10**9
        coverage_end = end + 120 * 10**9
        stamps = np.arange(coverage_start // 10**6, coverage_end // 10**6, step * 1000, dtype=np.int64)
        stamps = np.unique(np.r_[stamps, end // 10**6 - 1, end // 10**6, end // 10**6 + 1])
        rows = np.zeros(len(stamps), dtype=TICK_DTYPE)
        rows['time_msc'] = stamps; rows['time'] = stamps // 1000
        i = np.arange(len(stamps), dtype=np.float64)
        rows['bid'] = 100 + 2 * np.sin(i * .31) + .5 * np.sin(i * .053)
        rows['ask'] = rows['bid'] + .1; rows['last'] = rows['bid']
        rows['volume'] = 1; rows['volume_real'] = 1.; rows['flags'] = 2
        stream = 'UI_REQUEST_SYNTHETIC_BOUNDARY_V1'
        chunk = RawChunkWriter(folder, stream).write(0, rows, coverage_start, coverage_end)
        manifest = dict(kind='GENERIC_RAW_ARCHIVE_V1', status='READY', instrument=INSTRUMENT,
            stream_namespace=stream, coverage_start_ns=coverage_start, coverage_end_ns=coverage_end,
            count=len(rows), raw_sha256=hashlib.sha256(rows.tobytes()).hexdigest(), chunks=[chunk],
            gaps=[], coverage={'completeness': 'UNVERIFIED', 'broker_history_completeness': 'NOT_APPLICABLE_SYNTHETIC'},
            input_kind='SYNTHETIC_VALIDATION_ONLY', source_build=['DETERMINISTIC_UI_BOUNDARY_FIXTURE'])
        manifest['archive_identity'] = identity(manifest)
        write_json(folder / 'manifest.json', manifest)
        return folder, verify_archive(folder)

    cases = []
    for mode in ('TICK', 'ONE_MINUTE_CLOSE', 'LIVE_PARITY'):
        for filtered in (False, True):
            cases.append((f'TRADE_{mode}_filter{int(filtered)}', 'TRADE', mode, filtered, False, None, False))
    for tf in (1, 3, 6, 15):
        cases.append((f'WATCH_BAR_{tf}m_CLOSE', 'WATCH', 'ONE_MINUTE_CLOSE', False, False, f'{tf}분봉 마감 알려줘', False))
    cases.extend([
        ('WATCH_BAR_TICK', 'WATCH', 'TICK', False, False, '1분봉 마감 알려줘', False),
        ('WATCH_BAR_CLOSE_filter1', 'WATCH', 'ONE_MINUTE_CLOSE', True, False, '1분봉 마감 알려줘', False),
        ('WATCH_HMA_LONG_TICK', 'WATCH', 'TICK', False, False, '1분 HMA 6/17 골든크로스 알려줘', False),
        ('WATCH_HMA_SHORT_TICK', 'WATCH', 'TICK', False, False, '1분 HMA 6/17 데드크로스 알려줘', False),
        ('WATCH_HMA_LONG_CLOSE', 'WATCH', 'ONE_MINUTE_CLOSE', False, False, '1분 HMA 6/17 골든크로스 알려줘', False),
        ('WATCH_HMA_LONG_PARITY', 'WATCH', 'LIVE_PARITY', False, False, '1분 HMA 6/17 골든크로스 알려줘', False),
        ('FULL_22D_TRADE_TICK', 'TRADE', 'TICK', False, True, None, False),
        ('FULL_22D_WATCH_15M_CLOSE', 'WATCH', 'ONE_MINUTE_CLOSE', False, True, '15분봉 마감 알려줘', False),
        ('CACHE_TRADE_CLOSE_first', 'TRADE', 'ONE_MINUTE_CLOSE', False, False, None, True),
        ('CACHE_TRADE_CLOSE_repeat', 'TRADE', 'ONE_MINUTE_CLOSE', False, False, None, True),
    ])
    report = {'scope': 'UPLOADED_FINAL_VS_UI_ONLY_REVISION_SYNTHETIC', 'side': tag,
              'python': sys.version, 'calculation_version': calculation_version(),
              'live_parity_test_timers': {'producer_ms': 60000, 'poll_ms': 60000},
              'rows': [], 'normalization': {
                  'result_files': 'NONE: compare all listed file SHA-256 values byte-for-byte',
                  'manifest': 'Source provenance, execution timestamps and cache I/O telemetry are not calculation outputs; retained separately'}}

    def var(value): return SimpleNamespace(get=lambda: str(value))
    for name, kind, mode, filtered, full, command, cache in cases:
        row = {'name': name}; started = time.perf_counter()
        try:
            start_text = '2026-09-01' if full else '2026-09-22'
            start = parse_kst_ns(start_text)
            raw, raw_manifest = archive(start, 3600 if full else 60)
            base = trade_config(raw, mode, filtered)
            plugin = 'GENERIC_EXAMPLE_V1' if kind == 'TRADE' else 'WATCH_UI_V1'
            params = {'every_ticks': 7, 'timeframe': '1m'}
            if command:
                plan = compile_watch(command, 'TEST')
                params = {'plan_json': json.dumps(plan, ensure_ascii=False)}
                row['watch_plan_sha256'] = identity(plan)
            record = parse_literal_metadata(root / 'backtest_specials' / (plugin + '.py'))
            panel = GenericPanel.__new__(GenericPanel)
            panel.records = {plugin: {'metadata': record.metadata, 'sha256': record.sha256}}
            panel.plugin = var(plugin); panel.variables = {k: var(v) for k, v in params.items()}
            panel.start = var(start_text if args.ui_dates else start_text + ' 00:00:00')
            panel.end = var('2026-09-22' if args.ui_dates else '2026-09-23 00:00:00')
            panel.mode = var('TRADE' if kind == 'TRADE' else 'ALERT_ONLY')
            panel.evaluation = var(next(k for k, v in EVALUATION_LABELS.items() if v == mode))
            panel.risk = SimpleNamespace(stops=base.stop_variants, targets=base.target_variants, basis=var(base.outcome_price_basis))
            session = default_definition(); session['enabled'] = filtered
            panel.session_editor = SimpleNamespace(value=lambda: session)
            values = panel._config_from_controls()
            assert values['start_ns'] == start and values['end_ns'] == end
            values['instrument'] = INSTRUMENT; values['archive'] = str(raw)
            values.pop('live_parity', None)  # this regression pins its own timers below
            resources = dict(base.resources)
            if cache:
                resources.update(disk_cache=True, calculation_cache_dir=str(cache_rel / 'tapes'),
                                 common_feature_cache_dir=str(cache_rel / 'features'))
            cfg = GenericRunConfig(**values, resources=resources,
                live_parity={'producer_ms': 60000, 'poll_ms': 60000} if mode == 'LIVE_PARITY' else None)
            plan = prepare_plan(cfg)
            assert plan['end_ns'] == end and plan['start_ns'] <= start
            output = run_root / name
            GenericRunCoordinator(cfg).run(output)
            manifest = verify_result(output)
            alerts = read_lines(output / 'alerts.jsonl')
            assert alerts, 'Fixture must produce nonempty positive results'
            assert all(start <= alert['observed_at_ns'] < end for alert in alerts)
            events = read_lines(output / 'events.jsonl')
            last = manifest['metadata']['last_token']
            assert last is not None and last['now_ns'] < end
            counts = {'alerts': len(alerts), 'events': len(events)}
            for file in ('entries', 'entry_decisions', 'stop_resolutions', 'target_policies', 'outcomes'):
                path = output / (file + '.jsonl')
                if path.exists(): counts[file] = len(read_lines(path))
            row.update(status='PASS', start_ns=start, end_ns=end, prepare_plan=plain(plan),
                raw_sha256=raw_manifest['raw_sha256'], raw_count=raw_manifest['count'],
                files=manifest['files'], counts=counts, alert_population=manifest['alert_population'],
                entry_population=manifest['entry_population'], result_directory=str(output),
                evaluation_schedule=manifest['metadata'].get('evaluation_schedule'),
                trading_session_filter=manifest['metadata'].get('trading_session_filter'),
                cache=manifest['metadata']['calculation_cache'],
                source_hash=manifest['metadata']['source_provenance']['source_hash'],
                last_token=last, manifest_sha256=hashlib.sha256((output/'generic_manifest.json').read_bytes()).hexdigest())
        except Exception as exc:
            row.update(status='FAIL', error=str(exc), traceback=traceback.format_exc())
        row['wall_seconds'] = time.perf_counter() - started
        report['rows'].append(row)
        output_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf8')
        print(json.dumps({k: v for k, v in row.items() if k in ('name', 'status', 'counts', 'error', 'wall_seconds')}, ensure_ascii=False), flush=True)
    report['cache_identities'] = []
    for path in sorted((root / cache_rel).rglob('manifest.json')):
        data = read_json(path)
        report['cache_identities'].append({'path': str(path.relative_to(root)),
            'identity': data.get('identity'), 'cache_key': data.get('cache_key'),
            'descriptor': data.get('descriptor')})
    output_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf8')
    return 0 if all(r['status'] == 'PASS' for r in report['rows']) else 1


if __name__ == '__main__':
    raise SystemExit(main())
