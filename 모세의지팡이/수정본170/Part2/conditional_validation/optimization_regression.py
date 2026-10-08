"""Cross-check one code snapshot using actual isolated strategy workers.

Run against the untouched supplied Part2 and each accepted stage, then compare
JSON files with --compare. Source trees are selected explicitly; no historical
implementation is restored. Synthetic component/replay fixtures are NOT a claim
of nonempty fully warmed SPECIAL1-7 market signals.
"""
from pathlib import Path
import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--output', type=Path)
    parser.add_argument('--compare', type=Path, nargs=2)
    args = parser.parse_args()
    if args.compare:
        left, right = (json.loads(p.read_text()) for p in args.compare)
        differences = [k for k in sorted(set(left['cases']) | set(right['cases']))
                       if left['cases'].get(k) != right['cases'].get(k)]
        result = {'equal': not differences, 'case_count': len(left['cases']), 'differences': differences}
        print(json.dumps(result, indent=2))
        if args.output:
            args.output.write_text(json.dumps(result, indent=2))
        raise SystemExit(bool(differences))
    root = args.root.resolve()
    sys.path[:0] = [str(root), str(root/'validation_suite')]
    import numpy as np
    from dataclasses import replace
    from generic_backtest import runner
    from generic_backtest.contracts import ROOT
    from generic_backtest.canonical import encode, identity, write_json, read_json
    from generic_backtest.results import verify_result
    from generic_backtest.ipc import Cancelled
    from cadence_input_validation.test_input import archive_from_parts, native_rows
    from cadence_input_validation.test_integration import config_for, semantic, GENERIC_SOURCE
    assert ROOT == root
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    report = {'scope': 'actual coordinator/workers; short synthetic prefix; nonempty generic/WATCH and separate component oracles', 'cases': {}}
    original_reader = runner.GenericArchiveReader
    original_payload = runner.context_payload
    plugin = root/'BACKTEST_SPECIAL/OPTIMIZATION_CONTRACT_TEST_ONLY.py'
    if plugin.exists():
        raise RuntimeError('Refusing to overwrite a supplied strategy fixture')
    parent = root/'generic_runs'; parent.mkdir(exist_ok=True)
    plugin.write_text(GENERIC_SOURCE.replace('INPUT_CONTRACT_TEST_ONLY','OPTIMIZATION_CONTRACT_TEST_ONLY'), encoding='utf-8')
    try:
        with tempfile.TemporaryDirectory(prefix='optimization-parity-', dir=parent) as directory:
            directory = Path(directory)
            ms = sorted([*range(0, 3_660_000, 30_000), 60_000, 60_000, 300_000, 900_000, 1_800_000, 3_600_000])
            rows = native_rows(ms)
            rows['bid'] = 100. + np.sin(np.arange(len(rows))*.8)*2
            rows['ask'] = rows['bid']+.1; rows['last'] = rows['bid']
            raw, _ = archive_from_parts(directory/'raw', [rows], [0, 3_660*10**9])
            manifest = read_json(raw/'manifest.json')
            ns = 10**9
            manifest['gaps'] = [
                {'status':'EMPTY_UNVERIFIED', 'start_ns':0, 'end_ns':3_660*ns},
                {'status':'KNOWN_MISSING', 'start_ns':120*ns, 'end_ns':180*ns},
                {'status':'ACQUISITION_ERROR', 'start_ns':150*ns, 'end_ns':240*ns},
                {'status':'KNOWN_MISSING', 'start_ns':160*ns, 'end_ns':170*ns},
                {'status':'ACQUISITION_ERROR', 'start_ns':240*ns, 'end_ns':300*ns},
                {'status':'EMPTY_UNVERIFIED', 'start_ns':300*ns, 'end_ns':600*ns},
                {'status':'KNOWN_MISSING', 'start_ns':5_000*ns, 'end_ns':6_000*ns},
            ]
            manifest.pop('archive_identity'); manifest['archive_identity'] = identity(manifest)
            (raw/'manifest.json').unlink()  # disposable fixture; production writer remains exclusive
            write_json(raw/'manifest.json', manifest)
            cases = []
            for mode in ('TICK','ONE_MINUTE_CLOSE','LIVE_PARITY'):
                for name in ['WATCH_OZ','WATCH_TREND_OZ','WATCH_BAR']:
                    cases.append((name, mode, False, None, 'DELTA_V1'))
                for trade in (False, True):
                    for cutoff in (None,40):
                        for transport in ('DELTA_V1','FULL'):
                            cases.append((plugin.stem, mode, trade, cutoff, transport))
            for number, (name, mode, trade, cutoff, transport) in enumerate(cases):
                config = replace(config_for(raw,name,mode,trade), end_ns=3_660*ns)
                config = replace(config, resources=dict(config.resources, worker_transport=transport),
                                 live_parity={'producer_ms':10000,'poll_ms':5000,
                                              'producer_phase_ms':0,'poll_phase_ms':2500})
                if cutoff is None:
                    runner.GenericArchiveReader = original_reader
                else:
                    class CutoffReader(original_reader):
                        def __iter__(self):
                            for n, tick in enumerate(super().__iter__(),1):
                                if n == cutoff:
                                    raise Cancelled()
                                yield tick
                    runner.GenericArchiveReader = CutoffReader
                payloads = []
                def capture(*a, **k):
                    value = original_payload(*a, **k)
                    payloads.append(hashlib.sha256(encode(value)).hexdigest())
                    return value
                runner.context_payload = capture
                destination = directory/f'case-{number}'
                key = '|'.join(map(str,(name,mode,trade,cutoff,transport)))
                try:
                    result = runner.GenericRunCoordinator(config).run(destination)
                    verified = verify_result(destination)
                    meta = verified['metadata']
                    files = {p.name: {'sha256':hashlib.sha256(p.read_bytes()).hexdigest(), 'rows':len(p.read_text().splitlines())}
                             for p in sorted(destination.glob('*.jsonl'))}
                    value = {'status':result['status'], 'payloads':payloads, 'files':files,
                             'metadata':{k:semantic(meta.get(k)) for k in ('execution','evaluation_schedule','last_token','requirements','conditional_computation','worker_finish_diagnostics')},
                             'dashboard': read_json(destination/'dashboard.json')}
                    expected = 'PARTIAL' if cutoff else 'SUCCEEDED'
                    assert value['status'] == expected, value['status']
                    if name==plugin.stem:
                        assert files['alerts.jsonl']['rows']>0, 'Nonempty generic oracle required'
                    report['cases'][key] = value
                except Exception as exc:
                    report['cases'][key] = {'error_type':type(exc).__name__, 'error':str(exc)}
                output.write_text(json.dumps(report,ensure_ascii=False,indent=2))
                print(key,report['cases'][key].get('status',report['cases'][key].get('error')),flush=True)
                if destination.exists():
                    shutil.rmtree(destination)
    finally:
        runner.GenericArchiveReader = original_reader
        runner.context_payload = original_payload
        plugin.unlink(missing_ok=True)
    failures = [k for k,v in report['cases'].items() if 'error' in v]
    print('cases',len(report['cases']),'execution errors',len(failures))
    raise SystemExit(bool(failures))


if __name__ == '__main__':
    main()
