"""Five-stage full coordinator benchmark on synthetic raw BAR WATCH.

This intentionally does not claim to exercise percentile or OZ. Real raw was
not supplied. Timings include isolated workers, preflight and verified result
files, exclude raw acquisition/fixture construction/outer GUI job-process start.
"""
import argparse,sys,json,time,traceback,platform
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--root',required=True);p.add_argument('--stage',required=True)
p.add_argument('--out',required=True);p.add_argument('--archive-parent',required=True)
p.add_argument('--trade-only',action='store_true');p.add_argument('--quick',action='store_true')
a=p.parse_args();sys.path.insert(0,a.root);sys.path.insert(1,str(Path(__file__).parent))
from integration_fixtures import archive,watch_config,trade_config
from generic_backtest.contracts import ROOT
from generic_backtest.runner import GenericRunCoordinator
from generic_backtest.results import verify_result,read_lines
from generic_backtest.canonical import file_hash
rows=[]
if a.trade_only:
 cases=[(1,'1m',on,mode) for mode in ('TICK','ONE_MINUTE_CLOSE') for on in (False,True)]
else:
 cases=[(days,tf,on,'ONE_MINUTE_CLOSE') for days in ((1,) if a.quick else (1,7))
        for tf in ('1m','3m','6m','15m') for on in (False,True)]
for days,tf,on,mode in cases:
    raw=Path(a.archive_parent)/f'synthetic{days}d'
    source=archive(raw,days)
    config=(trade_config(raw,mode,on) if a.trade_only else watch_config(raw,tf,days,on))
    name=f'{a.stage}_{"TRADE" if a.trade_only else "WATCH"}_{days}d_{tf}_{int(on)}_{mode}'
    out=ROOT/'generic_runs/validation_cumulative'/name
    row={'stage':a.stage,'days':days,'base_case':tf,'filter_on':on,'evaluation_mode':mode,
         'mode':config.mode,'input_sha256':source['raw_sha256'],'raw_count_with_45m_prefix':source['count'],
         'result_dir':str(out)}
    start=time.perf_counter()
    try:
        result=GenericRunCoordinator(config).run(out)
        elapsed=time.perf_counter()-start
        m=verify_result(out);schedule=m['metadata'].get('evaluation_schedule') or {}
        role=schedule.get('roles',{}).get('SIGNAL',{})
        alerts=read_lines(out/'alerts.jsonl')
        row.update(status='PASS',wall_s=elapsed,files=m['files'],alert_count=len(alerts),
            alert_population=m['alert_population'],entry_population=m['entry_population'],
            callbacks=role.get('strategy_callback_opportunities'),schedule=schedule,
            requirements=m['metadata']['requirements'],
            filter_stats=m['metadata'].get('trading_session_filter'),
            plan_id=json.loads(config.parameters['plan_json'])['plan_id'] if not a.trade_only else None)
        if a.trade_only:
            from collections import Counter
            outcomes=read_lines(out/'outcomes.jsonl');entries=read_lines(out/'entries.jsonl')
            row.update(entry_count=len(entries),directions=dict(Counter(e['direction'] for e in entries)),
                outcome_counts=dict(Counter(o['status'] for o in outcomes)),outcomes=len(outcomes))
    except Exception as exc:
        row.update(status='FAIL',wall_s=time.perf_counter()-start,error=str(exc),traceback=traceback.format_exc())
    rows.append(row)
    Path(a.out).write_text(json.dumps({'scope':'SYNTHETIC_NATIVE_FORMAT_30_SECOND_TICKS; BAR WATCH DOES NOT EXERCISE PERCENTILE/OZ',
        'disk_cache':False,'python':platform.python_version(),'stage':a.stage,'rows':rows},indent=2,ensure_ascii=False))
    print(json.dumps({k:v for k,v in row.items() if k not in ('files','schedule','requirements','traceback','filter_stats')},ensure_ascii=False),flush=True)
