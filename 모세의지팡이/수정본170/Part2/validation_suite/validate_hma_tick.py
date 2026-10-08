"""Positive HMA WATCH TICK regression using synthetic raw, no native PB seed."""
import argparse,sys,json,time,traceback
from pathlib import Path
from dataclasses import replace
p=argparse.ArgumentParser();p.add_argument('--root',required=True);p.add_argument('--out',required=True);p.add_argument('--archive-parent',required=True);p.add_argument('--stage',required=True)
a=p.parse_args();sys.path.insert(0,a.root)
from integration_fixtures import archive,watch_config
from generic_backtest.watch.compiler import compile_watch
from generic_backtest.contracts import ROOT
from generic_backtest.runner import GenericRunCoordinator
from generic_backtest.results import verify_result,read_lines
raw=Path(a.archive_parent)/'synthetic1d';source=archive(raw,1);rows=[]
for direction,word in [('LONG','골든크로스'),('SHORT','데드크로스')]:
 for on in (False,True):
    plan=compile_watch(f'1분 HMA 6/17 {word} 알려줘','TEST')
    config=replace(watch_config(raw,'1m',1,on),evaluation_mode='TICK',parameters={'plan_json':json.dumps(plan,ensure_ascii=False)})
    output=ROOT/'generic_runs/validation_cumulative'/f'{a.stage}_HMA_TICK_{direction}_{int(on)}'
    row={'direction':direction,'filter_on':on,'input_sha256':source['raw_sha256'],'plan':plan,'output':str(output)}
    t=time.perf_counter()
    try:
        GenericRunCoordinator(config).run(output);m=verify_result(output)
        row.update(status='PASS',wall_s=time.perf_counter()-t,files=m['files'],alerts=len(read_lines(output/'alerts.jsonl')))
    except Exception as exc:row.update(status='FAIL',wall_s=time.perf_counter()-t,error=str(exc),traceback=traceback.format_exc())
    rows.append(row);Path(a.out).write_text(json.dumps({'scope':'synthetic1d HMA6/17 WATCH, TICK mode','rows':rows},ensure_ascii=False,indent=2));print({k:v for k,v in row.items() if k not in ('plan','files','traceback')},flush=True)
