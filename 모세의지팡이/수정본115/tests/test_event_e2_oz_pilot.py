"""First OZ-state migration, current direct decision function oracle and midnight resume."""
import importlib.util
import json
import logging
import shutil
import socket
import sys
from pathlib import Path
from types import SimpleNamespace
import numpy as np
import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'Part1/program'))
from event_engine import EventEngine,IngressSequencer,Kind,FeedSnapshot
from event_engine.oz_state import OZCandidateProcessor
from staff_schema import PIPE_VALUE_COLUMNS,legacy_frame


@pytest.fixture
def modules(tmp_path,monkeypatch):
    def deny(*a,**k):raise AssertionError('No network in E2 tests')
    monkeypatch.setattr(socket.socket,'connect',deny)
    monkeypatch.setattr(socket.socket,'connect_ex',deny)
    monkeypatch.setattr(socket.socket,'sendto',deny)
    result=[]
    for label,source in [('old',ROOT),('new',ROOT)]:
        folder=tmp_path/label;folder.mkdir()
        for path in (source/'Part1/program').glob('*.py'):shutil.copyfile(path,folder/path.name)
        name='e2_pilot_'+label
        spec=importlib.util.spec_from_file_location(name,folder/'monitor_OZ.py')
        m=importlib.util.module_from_spec(spec);monkeypatch.setitem(sys.modules,name,m)
        spec.loader.exec_module(m);result.append(m)
    yield result
    for handler in list(logging.getLogger().handlers):
        if str(tmp_path) in str(getattr(handler,'baseFilename','')):
            logging.getLogger().removeHandler(handler);handler.close()


def observations():
    # Genuine OUT -> IN and HMA crossing through midnight, with same-bar changes.
    end=1790380680  # 2026-09-25 23:58:00 UTC
    for i in range(240):
        times=np.arange(30,dtype='<i8')*60+end-29*60+(i//60)*60
        a=np.ones((30,48),dtype='<f8')*100
        index={n:j for j,n in enumerate(PIPE_VALUE_COLUMNS)}
        for key,val in [('open',100),('close',100),('high',102),('low',98),
                        ('hma_6',99 if i<12 else 101),('hma_17',100),
                        ('price_hma_6',98 if i<10 else 100),
                        ('price_band_lower',99),('price_band_upper',101),
                        ('wonbi_upper',103),('wonbi_lower',97),('wonbi_sigma',3)]:a[:,index[key]]=val
        for family in ('RSI','STO','DI'):
            for suffix,val in [('val',98 if i<10 else 100),('db',99),('ub',101)]:a[:,index[family+'_'+suffix]]=val
        yield (end+i)*1000,FeedSnapshot(times,np.ones(30,dtype='<i8'),a,i+1,'pilot',{})


def drive(engine,t,snapshot):
    engine.ingress.post(Kind.MARKET_BUNDLE,source='staff',source_seq=snapshot.seq,source_time=t,
                        payload={'symbol':'XAUUSD+','feeds':{'1m':snapshot}})
    engine.run()


def normalized(value):
    def canonical(item):
        if isinstance(item,dict):
            result={k:canonical(v) for k,v in item.items()}
            if item.get('type') in ('set','frozenset'):
                result['value']=sorted(result['value'],key=lambda v:json.dumps(v,sort_keys=True,default=str))
            return result
        if isinstance(item,(list,tuple)):return [canonical(v) for v in item]
        return item
    return json.dumps(canonical(value),sort_keys=True,default=str,allow_nan=True)


def test_first_oz_state_matches_entire_frozen_module_and_midnight_checkpoint(modules):
    old,new=modules
    oracle=old.OZMonitor('XAUUSD+',{},None,None,staff_client=SimpleNamespace())
    oracle.base_tfs=['1m'];oracle._checkpoint=lambda:None
    engine=EventEngine(IngressSequencer(),processors=[OZCandidateProcessor(new,{},timeframes=('1m',))])
    observed_candidate=False;resumed=None
    for i,(t,snapshot) in enumerate(observations()):
        raw=legacy_frame('XAUUSD+','1m',snapshot)
        frame=old.OZSnapshotFeatures.compose(raw,'1m',3.)
        oracle._in_cycle=True
        oracle._process_hma_cross('1m',frame);oracle._process_out_in('1m',frame)
        for d in ('LONG','SHORT'):oracle._maintain_base_candidate('1m',d,frame)
        oracle._in_cycle=False
        drive(engine,t,snapshot)
        state=engine.processor_state['OZ_CANDIDATE']['XAUUSD+']
        for field in old.OZMonitor._checkpoint_fields:
            assert normalized(state[field])==normalized(old._observed_encode(getattr(oracle,field))),field
        observed_candidate |= any(v is not None for v in oracle.candidates.values())
        if i==119:
            resumed=EventEngine(IngressSequencer(),processors=[OZCandidateProcessor(new,{},timeframes=('1m',))])
            resumed.restore(engine.checkpoint())
        elif resumed is not None:
            drive(resumed,t,snapshot)
            assert normalized(resumed.processor_state)==normalized(engine.processor_state)
    assert observed_candidate,'must exercise nonempty candidate state'
    assert engine.metrics.bundle_count==240
