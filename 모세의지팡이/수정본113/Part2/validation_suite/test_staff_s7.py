"""S7 mandatory new tests; independent of excluded Part2 regression collection."""
import ast
import json
import sys
from pathlib import Path
from types import SimpleNamespace
import numpy as np
import pytest

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'Part2'))
from part1_host.runtime import Part1Runtime
from part1_host.capture import CaptureWriter,pack_wire
from part1_host.engine import SecondFeed

@pytest.fixture
def host():
    with Part1Runtime(symbols=['BTCUSD'],specials=['SPECIAL7'],start_epoch=1790035200) as rt:
        yield rt

def payload(sigma=3.0,columns=48):
    t=np.arange(8,dtype='<i8')+1790035200;v=np.arange(8,dtype='<i8')
    x=np.full((8,48),10.,dtype='<f8')
    # Deliberately unrelated to OHLC: transport must never recompute these.
    x[:,12]=55.;x[:,17]=33.;x[:,18]=77.
    x[:,45]=99.;x[:,46]=22.;x[:,47]=sigma
    return t,v,x[:,:columns]

def server(host,expected=3.):
    s=host.modules['the_staff_of_moses'];cache=s.StaffPipeCache('',health_session='S7',monotonic=lambda:4.)
    state=SimpleNamespace(get_sigma=lambda:expected)
    return s,s.DataServer({},state,cache=cache,allowed_symbols=['BTCUSD'],allowed_timeframes=['1m','5m'])

@pytest.mark.parametrize('sigma',[3.,2.5])
def test_mt5_values_are_authoritative_alias_and_compat(host,sigma):
    s,srv=server(host,sigma);data=payload(sigma)
    srv.cache.publish_frame(s.wire.pack_v2('BTCUSD','1m',*data,seq=1))
    from staff_compat import StaffCompat
    from staff_snapshot import SnapshotClient
    client=SnapshotClient(transport=srv.dispatch_multipart)
    raw=client.raw_frames({'symbol':'BTCUSD','timeframes':['1m']})['1m']
    assert raw['wonbi_mid'].tolist()==[55.]*8
    assert raw['wonbi_upper'].tolist()==[99.]*8 and raw['wonbi_lower'].tolist()==[22.]*8
    import staff_compat
    out=staff_compat.apply_requested_features(raw,['WONBI'])
    assert out[['wonbi_upper','wonbi_lower','wonbi_sigma']].to_numpy().tobytes()==data[2][:,45:48].tobytes()
    assert not hasattr(staff_compat,'add_wonbi_features')

def test_sigma_warning_preserves_values_and_health(host,caplog):
    s,srv=server(host,3.);srv.cache.publish_frame(s.wire.pack_v2('BTCUSD','1m',*payload(2.5),seq=1))
    from staff_snapshot import SnapshotClient
    client=SnapshotClient(transport=srv.dispatch_multipart)
    assert client.raw_frames({'symbol':'BTCUSD','timeframes':['1m']})['1m'].wonbi_sigma.iloc[-1]==2.5
    health=srv.handle({'kind':'SOURCE_HEALTH','symbol':'BTCUSD','timeframes':['1m']})['feeds']['1m']
    assert health['status']=='FRESH'
    assert health['warnings'][0]['code']=='WONBI_SIGMA_MISMATCH'
    assert health['warnings'][0]['applied_sigma']==2.5
    assert 'WONBI_SIGMA_MISMATCH' in caplog.text

@pytest.mark.parametrize('version',[1,2])
def test_legacy_mt5_mapping_and_nondefault_sigma_rejected(host,version):
    s,srv=server(host);data=payload(columns=45)
    raw=pack_wire('BTCUSD','1m',*data,snapshot=1) if version==1 else s.wire.pack_v2('BTCUSD','1m',*data,seq=1)
    srv.cache.publish_frame(raw)
    from staff_snapshot import SnapshotClient
    client=SnapshotClient(transport=srv.dispatch_multipart)
    frame=client.raw_frames({'symbol':'BTCUSD','timeframes':['1m']})['1m']
    assert frame.wonbi_upper.tolist()==[77.]*8 and frame.wonbi_lower.tolist()==[33.]*8
    assert frame.wonbi_sigma.tolist()==[3.]*8
    srv.wonbi_state=SimpleNamespace(get_sigma=lambda:2.5)
    result=client.raw_frames({'symbol':'BTCUSD','timeframes':['1m']})
    assert 'LEGACY_WONBI_SIGMA' in result['error']

def test_new_schema_append_only_and_v1_bytes(host):
    w=host.modules['the_staff_of_moses'].wire
    assert len(w.PIPE_VALUE_COLUMNS)==48 and tuple(w.PIPE_VALUE_COLUMNS[:45])==w.LEGACY_PIPE_VALUE_COLUMNS
    assert w.LEGACY_WIRE_SCHEMA_ID==0x4d2fa0e9
    assert w.PIPE_VALUE_COLUMNS[45:]==['wonbi_upper','wonbi_lower','wonbi_sigma']
    assert w.COLUMN_ALIASES=={'wonbi_mid':'open_band_4_mid'}
    import importlib.util
    path=ROOT/'검증결과/staff_s0/baseline_input/Part2/part1_host/capture.py'
    spec=importlib.util.spec_from_file_location('s7_original_capture',path);m=importlib.util.module_from_spec(spec);sys.modules[spec.name]=m
    try:
        spec.loader.exec_module(m)
        assert pack_wire('BTCUSD','1m',*payload(columns=45),snapshot=31)==m.pack_wire('BTCUSD','1m',*payload(columns=45),snapshot=31)
    finally:sys.modules.pop(spec.name,None)

def test_full_row_heartbeat_and_reconnect_preserve_wonbi(host,tmp_path):
    s,srv=server(host);w=s.wire;data=payload();c=srv.cache
    c.publish_frame(w.pack_v2('BTCUSD','1m',*data,seq=1));epoch=c.snapshot('BTCUSD','1m').source_epoch
    data[2][-1,45]=123.
    c.publish_frame(w.pack_v2('BTCUSD','1m',*(x[-1:] for x in data),seq=2,kind=w.WIRE_ROW))
    c.publish_frame(w.pack_v2('BTCUSD','1m',seq=3,kind=w.WIRE_HEARTBEAT))
    assert c.snapshot('BTCUSD','1m').values.tobytes()==data[2].tobytes()
    assert c.snapshot('BTCUSD','1m').source_epoch==epoch
    c.reconnect();c.publish_frame(w.pack_v2('BTCUSD','1m',*data,seq=1))
    assert c.snapshot('BTCUSD','1m').source_epoch!=epoch

@pytest.mark.parametrize('columns',[45,48])
def test_msp3_secondfeed_new_and_old_schema(host,tmp_path,columns):
    s,srv=server(host);data=payload(columns=columns)
    writer=CaptureWriter(tmp_path,'BTCUSD',['1m'],wire_version=2,value_columns=columns)
    for sec in range(4):
        if sec==1:data[2][-1,3]+=1.
        writer.write(0,1790035200+sec,*data)
    writer.close();feed=SecondFeed(tmp_path)
    for sec in range(4):
        feed.advance_to(1790035200+sec)
        for tf,raw in feed.wires():srv.cache.publish_frame(raw)
    assert srv.cache.snapshot('BTCUSD','1m').values.tobytes()==s.wire.mt5_values(data[2]).tobytes()

def test_synthetic_ea_two_pass_and_forming_sigma():
    from part1_host.synthetic import ea_open4_synthetic,SyntheticMarket
    mid,std=ea_open4_synthetic(np.array([1.,2.,3.,4.]))
    assert mid[-1]==2.5 and std[-1]==np.sqrt(1.25)
    market=SyntheticMarket('BTCUSD',1790035200,1790035203,wonbi_sigma=2.5)
    for sec in range(market.start,market.end):
        t,v,x=market.payload('1m',sec)
        assert x[-1,47]==2.5 and x[-1,45]>=x[-1,12]>=x[-1,46]

def test_active_python_wonbi_calculation_removed():
    for name in ('Part1/program/staff_compat.py','Part1/program/monitor_OZ.py','Part2/calculations/common.py'):
        text=(ROOT/name).read_text('utf-8-sig')
        assert 'add_wonbi_features' not in text
    for name in ('Part1/program/staff_compat.py','Part2/live_replay/event_catalog.py'):
        tree=ast.parse((ROOT/name).read_text('utf-8-sig'))
        for node in ast.walk(tree):
            if isinstance(node,(ast.Assign,ast.AnnAssign)):
                target=ast.unparse(node.targets if isinstance(node,ast.Assign) else node.target)
                if any(x in target for x in ("'wonbi_upper'","'wonbi_lower'","'wonbi_mid'","'wonbi_std'")):
                    assert not any(isinstance(x,ast.BinOp) for x in ast.walk(node.value)),target

@pytest.mark.parametrize('defect',['crc','truncated','unknown_schema','duplicate','reverse'])
def test_new_columns_do_not_weaken_wire_rejection(host,defect):
    s,srv=server(host);w=s.wire;c=srv.cache
    c.publish_frame(w.pack_v2('BTCUSD','1m',*payload(),seq=2));before=c.snapshot('BTCUSD','1m')
    raw=w.pack_v2('BTCUSD','1m',*payload(2.5),seq=3)
    if defect=='crc':raw=raw[:-1]+bytes([raw[-1]^1])
    if defect=='truncated':raw=raw[:-9]
    if defect=='unknown_schema':raw=w.pack_v2('BTCUSD','1m',*payload(),seq=3,schema_id=123)
    if defect in ('duplicate','reverse'):
        raw=w.pack_v2('BTCUSD','1m',*payload(),seq=2 if defect=='duplicate' else 1)
        c.publish_frame(raw)
    else:
        with pytest.raises((RuntimeError,ValueError,EOFError)):c.publish_frame(raw)
    assert c.snapshot('BTCUSD','1m') is before
    if defect=='unknown_schema':assert c.health('BTCUSD',['1m'])['1m']['status']=='UNAVAILABLE'

def test_missing_mt5_wonbi_never_recomputed_for_ohlcv():
    from live_replay.event_catalog import build_ohlcv,ReplayError
    with pytest.raises(ReplayError,match='MT5_WONBI_REQUIRED'):
        build_ohlcv(None,'BTCUSD',event_types=('WONBI',))

@pytest.mark.parametrize('source',['golden_run1','actual_run1'])
def test_representative_atr_input_still_matches_frozen_s0(host,source):
    import sqlite3
    import pandas as pd
    from staff_golden.contracts import read_frame
    path=ROOT/'검증결과/staff_s0'/source/'golden.sqlite'
    with sqlite3.connect(path.as_uri()+'?mode=ro',uri=True) as db:
        frame=read_frame(db,db.execute('SELECT data FROM frames LIMIT 1').fetchone()[0])
    facts=host.modules['indicator_facts']
    got=facts.standalone_frame(frame,'1m').get('ATR14_GENERAL')
    pd.testing.assert_series_equal(got.rename('atr_14'),frame['atr_14'],check_exact=True)

def test_atr_families_are_separate_from_wonbi(host):
    import pandas as pd
    facts=host.modules['indicator_facts'];fvg=host.modules['strategy_FVG']
    assert 'ATR14_GENERAL' in facts.FACTS and 'FVG_WILDER_ATR' not in facts.FACTS
    assert fvg._wilder_atr is fvg.FVG_WILDER_ATR
    frame=pd.DataFrame({'high':np.arange(60.)**1.2+5,'low':np.arange(60.)**1.1,'close':np.arange(60.)**1.15+1})
    general=facts.standalone_frame(frame,'1m').get('ATR14_GENERAL')
    internal=fvg.FVG_WILDER_ATR(frame.high,frame.low,frame.close,14)
    assert np.any(general.dropna().to_numpy()!=internal.dropna().to_numpy())
    changed=frame.assign(wonbi_upper=123456.,wonbi_lower=-789.,wonbi_sigma=2.5)
    pd.testing.assert_series_equal(general,facts.standalone_frame(changed,'1m').get('ATR14_GENERAL'),check_exact=True)
