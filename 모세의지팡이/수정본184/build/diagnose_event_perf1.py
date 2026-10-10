"""Representative input-window diagnostics, never overwrite frozen evidence."""
import sys,json,importlib.util,logging
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'Part1/program'),str(ROOT/'Part2')]
import numpy as np
import pandas as pd
from event_engine import EventEngine,IngressSequencer
from event_engine.staff_adapter import StaffIngressAdapter
from event_engine.capture_io import capture_bundles
from event_engine.history import required_rows
from staff_schema import legacy_frame
import monitor_OZ as oz
from indicator_facts import standalone_frame

spec=importlib.util.spec_from_file_location('perf1_diagnostic_staff',ROOT/'Part1/program/THE STAFF OF MOSES.py')
staff=importlib.util.module_from_spec(spec);spec.loader.exec_module(staff)
folder=ROOT/'검증결과/staff_s7/final_mt5_sigma3_v2'
info=json.loads((folder/'result.json').read_text('utf-8'))
engine=EventEngine(IngressSequencer());clock=[info['start_s']]
cache=staff.StaffPipeCache('',health_session='DIAGNOSTIC',monotonic=lambda:clock[0],gap_journal=ROOT/'검증결과/event_perf1/diagnostic.gaps.jsonl')
adapter=StaffIngressAdapter(cache,engine.ingress)
for i,(observed,wire) in enumerate(capture_bundles(folder/Path(info['retained_export']).name)):
    clock[0]=observed/1000;adapter.publish(wire,source_time=observed);engine.run()
    if i==9:break
rows=[]
for (symbol,tf),snapshot in engine.board._feeds.items():
    raw=legacy_frame(symbol,tf,snapshot)
    budgets={role:required_rows(raw,tf,None if role=='oz' else (),role=role) for role in ('oz','fvg','sweep','indicator','default','composer')}
    item={'symbol':symbol,'tf':tf,'available':len(raw),'windows':budgets}
    if tf in ('1m','5m','1d'):
        full=oz.OZSnapshotFeatures.compose(raw,tf,3.)
        short=oz.OZSnapshotFeatures.compose(raw.tail(budgets['oz']).reset_index(drop=True),tf,3.)
        cols=[c for c in full if c!='atr_14']
        pd.testing.assert_frame_equal(full[cols].tail(5).reset_index(drop=True),short[cols].tail(5).reset_index(drop=True))
        item['local_OZ_decision_rows_equal']=True
        a=full.atr_14.to_numpy()[-5:];b=short.atr_14.to_numpy()[-5:]
        item['ATR14_GENERAL']={'full650':a.tolist(),'shortened':b.tolist(),'max_absolute_difference':float(np.nanmax(np.abs(a-b)))}
        for fact in ('e50','rv','st','sar'):
            before=standalone_frame(raw,tf).get(fact).iloc[-1]
            after=standalone_frame(raw.tail(96).reset_index(drop=True),tf).get(fact).iloc[-1]
            item[fact]={'full':float(before),'96_rows':float(after),'different':bool(before!=after)}
    rows.append(item)
out=ROOT/'검증결과/event_perf1/input_diagnostics.json'
out.write_text(json.dumps(rows,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
print('Representative rows checked',len(rows))
