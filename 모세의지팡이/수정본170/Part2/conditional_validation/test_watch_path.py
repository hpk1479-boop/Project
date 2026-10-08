"""Real existing WATCH SDK/worker/runner path; no substituted worker callbacks."""
import json,tempfile
from pathlib import Path
import pytest
from generic_backtest.contracts import ROOT,GenericRunConfig
from generic_backtest.canonical import file_hash,read_json
from generic_backtest.runner import GenericRunCoordinator
from generic_backtest.results import read_lines,verify_result
from generic_backtest.watch.compiler import compile_watch
from validation_suite.integration_fixtures import archive,START,CAL,INSTRUMENT

@pytest.mark.parametrize('cadence',['TICK','ONE_MINUTE_CLOSE','LIVE_PARITY'])
def test_existing_primitive_watch_remains_positive_and_not_conditionally_changed(cadence):
    parent=ROOT/'generic_runs';parent.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='watch-path-validation-',dir=parent) as directory:
        root=Path(directory);archive(root/'raw',days=1/48,step_seconds=20)
        plan=compile_watch('3분봉 마감 알려줘','TEST')
        config=GenericRunConfig(mode='ALERT_ONLY',plugin_id='WATCH_UI_V1',
            plugin_sha256=file_hash(ROOT/'BACKTEST_SPECIAL/WATCH_UI_V1.py'),
            parameters={'plan_json':json.dumps(plan,ensure_ascii=False)},instrument=INSTRUMENT,
            start_ns=START,end_ns=START+1800*10**9,calendar=CAL,archive=str(root/'raw'),
            evaluation_mode=cadence,session_filter={'enabled':False},resources={
                'max_history_bars':100000,'max_occurrences':100000,'disk_cache':False,
                'worker_timeout_seconds':120})
        output=GenericRunCoordinator(config).run(root/'result')
        assert output['status']=='SUCCEEDED'
        manifest=verify_result(output['result'])
        events=read_lines(root/'result/alerts.jsonl')
        assert events and len(events)==9
        assert len({e['event_id'] for e in events})==9
        assert [e['observed_at_ns'] for e in events]==[START+i*180*10**9 for i in range(1,10)]
        assert manifest['metadata']['conditional_computation']=={}
        assert read_json(root/'result/dashboard.json')['alerts']['count']==9
