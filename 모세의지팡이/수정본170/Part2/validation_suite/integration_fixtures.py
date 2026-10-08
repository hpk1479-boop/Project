"""Explicit synthetic native-format ticks; never presented as real broker data."""
from pathlib import Path
import hashlib
import math
import numpy as np
from generic_backtest.canonical import plain, identity, write_json, file_hash
from generic_backtest.contracts import InstrumentSpec, GenericRunConfig, ROOT
from generic_backtest.history.cache import RawChunkWriter, verify_archive
from generic_backtest.session_filter import default_definition
from pit.archive.reader import TICK_DTYPE

START=1736726400*10**9  # Monday 2025-01-13 00:00 UTC
CAL={'kind':'UTC_GRID_RESEARCH_V1','explicit_research_choice':True}
INSTRUMENT=plain(InstrumentSpec('TEST','SYNTHETIC_NOT_BROKER',trade_tick_size=.01))

def archive(root, days=7, step_seconds=30):
    root=Path(root)
    if (root/'manifest.json').is_file():return verify_archive(root)
    start=START-45*60*10**9;end=START+days*86400*10**9
    seconds=np.arange(start//10**9,end//10**9,step_seconds,dtype=np.int64)
    rows=np.zeros(len(seconds),dtype=TICK_DTYPE)
    i=np.arange(len(seconds),dtype=np.float64)
    rows['time']=seconds;rows['time_msc']=seconds*1000
    rows['bid']=100+2*np.sin(i*.31)+np.sin(i*.053)*.5
    rows['ask']=rows['bid']+.1;rows['last']=rows['bid']
    rows['volume']=1;rows['volume_real']=1.;rows['flags']=2
    stream='SYNTHETIC_FIXED_30S_V1'
    chunk=RawChunkWriter(root,stream).write(0,rows,start,end)
    m={'kind':'GENERIC_RAW_ARCHIVE_V1','status':'READY','instrument':INSTRUMENT,
       'stream_namespace':stream,'coverage_start_ns':start,'coverage_end_ns':end,
       'count':len(rows),'raw_sha256':hashlib.sha256(rows.tobytes()).hexdigest(),'chunks':[chunk],
       'gaps':[],'coverage':{'completeness':'UNVERIFIED','broker_history_completeness':'NOT_APPLICABLE_SYNTHETIC'},
       'input_kind':'SYNTHETIC_VALIDATION_ONLY','source_build':['DETERMINISTIC_FIXTURE_NO_BROKER']}
    m['archive_identity']=identity(m);write_json(root/'manifest.json',m);return verify_archive(root)


def watch_config(archive_root, tf, days=1, on=False, *, end_ns=None, profile_ipc=False):
    from generic_backtest.watch.compiler import compile_watch
    import json
    minutes={'1m':1,'3m':3,'6m':6,'15m':15}[tf]
    plan=compile_watch(f'{minutes}분봉 마감 알려줘','TEST')
    sessions=default_definition();sessions['enabled']=on
    return GenericRunConfig(mode='ALERT_ONLY',plugin_id='WATCH_UI_V1',
        plugin_sha256=file_hash(ROOT/'backtest_specials/WATCH_UI_V1.py'),
        parameters={'plan_json':json.dumps(plan,ensure_ascii=False)},instrument=INSTRUMENT,
        start_ns=START,end_ns=end_ns or START+days*86400*10**9,calendar=CAL,archive=str(archive_root),
        session_filter=sessions,evaluation_mode='ONE_MINUTE_CLOSE',resources={
            'max_history_bars':100000,'max_occurrences':1000000,'worker_timeout_seconds':60,
            'max_ipc_bytes':32*1024*1024,'disk_cache':False,'profile_ipc':profile_ipc})


def trade_config(archive_root, mode='TICK', on=False, *, hours=24):
    sessions=default_definition();sessions['enabled']=on
    return GenericRunConfig(mode='TRADE',plugin_id='GENERIC_EXAMPLE_V1',
        plugin_sha256=file_hash(ROOT/'backtest_specials/GENERIC_EXAMPLE_V1.py'),
        parameters={'every_ticks':7,'timeframe':'1m'},instrument=INSTRUMENT,
        start_ns=START,end_ns=START+hours*3600*10**9,calendar=CAL,archive=str(archive_root),
        session_filter=sessions,evaluation_mode=mode,
        stop_variants=({'id':'price','kind':'PRICE_DISTANCE','value':.5},
                       {'id':'ticks','kind':'TICK_DISTANCE','value':70},
                       {'id':'pct','kind':'PERCENT_DISTANCE','value':.7},
                       {'id':'anchor','kind':'STRATEGY_ANCHOR','name':'example_stop'},
                       {'id':'extreme','kind':'N_COMPLETED_EXTREME','n':2,'tf':'1m'}),
        target_variants=({'id':'rr','kind':'RR_MULTIPLE','value':1.5},
                         {'id':'price','kind':'PRICE_DISTANCE','value':.9},
                         {'id':'none','kind':'NONE'}),outcome_price_basis='CHART_PRICE',
        resources={'max_history_bars':100000,'max_occurrences':1000000,
            'worker_timeout_seconds':60,'max_ipc_bytes':32*1024*1024,'disk_cache':False})
