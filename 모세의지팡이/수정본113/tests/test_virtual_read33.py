from pathlib import Path
import json,sys,csv
import numpy as np
import pytest
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'Part2'),str(ROOT/'Part1/program')]

@pytest.fixture(autouse=True)
def network_off(monkeypatch):
    import socket
    def fail(*a,**kw):raise AssertionError('network forbidden')
    monkeypatch.setattr(socket.socket,'connect',fail)
    monkeypatch.setattr(socket.socket,'sendto',fail)

def published(tmp_path):
    from test_parallel_oz import keyframe_fixture
    from event_backtest.keyframes import verify_indexed
    rows,index,root=keyframe_fixture(tmp_path)
    verify_indexed(root/'capture.delta2',{k:index[k] for k in ('bundles','bundle_sha256')})
    (root/'storage.json').write_text(json.dumps(index))
    (root/'complete.txt').write_text('VERIFIED\n')
    return rows,index,root

def test_selected_delta_bits_full_row_hb_and_unrelated_feed():
    import staff_schema as wire
    from event_backtest.delta import DeltaCodec
    from event_backtest.virtual_msd import Projection,INDICES
    times=np.array([1,2,3],dtype='<i8');values=np.ones((3,len(wire.PIPE_VALUE_COLUMNS)))
    # All five columns, including a NaN payload, EMPTY and signed zero.
    values.view('<u8')[0,INDICES[0]]=0x7ff8000000001234
    values[1,INDICES[1]]=-0.;values[2,INDICES[2]]=np.finfo('float64').max
    codec=DeltaCodec();p=Projection({('XAUUSD+','1m')})
    for i in range(5):
        kind=(wire.WIRE_FULL,wire.WIRE_ROW,wire.WIRE_HEARTBEAT,wire.WIRE_FULL,wire.WIRE_ROW)[i]
        t=times+i if i>=3 else times
        x=values.copy();x[-1,INDICES[-1]]=i+0.25
        if i==4:t=times+3
        packet=wire.pack_bundle('XAUUSD+',[
            wire.pack_v2('XAUUSD+',tf,t if kind==1 else t[-1:],np.ones(3) if kind==1 else np.ones(1),x if kind==1 else x[-1:],seq=i+1,kind=kind)
            for tf in ('1m','1d')],seq=i+1,sent_at_ms=i*1000)
        structure,bits=codec.encode(packet);p.decode(structure,bits)
        expected=codec.feeds[b'XAUUSD+1m']
        assert set(p.states)=={('XAUUSD+','1m')}
        np.testing.assert_array_equal(p.states[('XAUUSD+','1m')][0],expected[0])
        np.testing.assert_array_equal(p.states[('XAUUSD+','1m')][1],expected[1][:,INDICES+1])

def test_verified_projection_matches_checked_reader_and_skips_crc(tmp_path,monkeypatch):
    from event_backtest import virtual_source,virtual_msd
    import staff_schema as wire
    rows,index,root=published(tmp_path)
    captures=[dict(start='2025-09-01',end='2025-09-04',path='capture')]
    def collect():
        return [(stamp,{tf:(v.time.copy(),v.values.copy()) for tf,v in feeds.items()})
                for stamp,feeds in virtual_source.observations(captures,tmp_path,'XAUUSD+','5m',rows[0][0],rows[-1][0])]
    expected=collect()
    # No Wire decode/CRC or day SHA reconstruction on published input.
    from event_backtest import keyframes
    with monkeypatch.context() as patch:
        patch.setattr(wire,'decode_v2',lambda *a:pytest.fail('Wire decode on verified projection'))
        patch.setattr(keyframes.hashlib,'sha256',lambda *a:pytest.fail('SHA on verified projection'))
        actual=collect()
    assert len(actual)==len(rows)
    for (a,x),(b,y) in zip(actual,expected):
        assert a==b
        for tf in x:
            np.testing.assert_array_equal(x[tf][0],y[tf][0]);np.testing.assert_array_equal(x[tf][1].view('u8'),y[tf][1].view('u8'))
    # An unpublished file uses the checked legacy reader, never implicit trust.
    (root/'complete.txt').unlink()
    checked=collect()
    for (a,x),(b,y) in zip(actual,checked):
        assert a==b
        for tf in x:np.testing.assert_array_equal(x[tf][1].view('u8'),y[tf][1].view('u8'))


def test_mismatched_build_marker_not_trusted(tmp_path):
    from event_backtest.virtual_msd import verified_index
    _,_,root=published(tmp_path)
    metadata=json.loads((root/'storage.json').read_text())
    metadata['bundle_sha256']='0'*64
    (root/'storage.json').write_text(json.dumps(metadata))
    assert verified_index(root) is None

def test_each_needed_day_once_idle_days_skipped(tmp_path):
    from event_backtest.virtual_source import shared_observations
    rows,index,root=published(tmp_path);metrics={};needed=[rows[0][0]]
    stream=shared_observations([dict(start='2025-09-01',end='2025-09-04',path='capture')],tmp_path,
        {('XAUUSD+','1m')},rows[0][0],rows[-1][0],needed_from=lambda:needed[0],metrics=metrics)
    stamps=[]
    for stamp,feeds in stream:
        stamps.append(stamp)
        needed[0]=rows[6][0] if len(stamps)==1 else None
    assert stamps==[rows[0][0],rows[6][0]] and metrics['date_restores']==2

def test_shared_read_stable_csv_order_and_independent_completion(tmp_path,monkeypatch):
    from event_backtest import virtual_source,virtual_entry
    from test_virtual_entry import alert,view
    rows=[dict(alert(stop=0),signal_id='first',b0_time=0),dict(alert(),signal_id='second',b0_time=0)]
    path=tmp_path/'alerts.csv'
    with path.open('w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
    def stream(*a,**kw):
        market=[(0,10,10,9,9,8),(60,10,30,7,9,8),(120,10,11,9,9,8),(180,10,12,-1,9,8),(240,10,11,9,9,8)]
        for i,row in enumerate(market):yield row[0]*1000,{('XAUUSD+','1m'):view(market[:i+1])}
    monkeypatch.setattr(virtual_source,'shared_observations',stream)
    r=virtual_entry.calculate(path,[],tmp_path,dict(start='1970-01-01',end='1970-01-02',symbol='XAUUSD+',strategies=['SPECIAL1']),{'POINT_XAUUSD+':'.01'},tmp_path)
    result=list(csv.DictReader((tmp_path/'virtual_trades.csv').open(encoding='utf-8-sig')))
    assert [x['signal_id'] for x in result]==['first']*9+['second']*9
    assert r['processed_signals']==2
    for row in result[9:]:assert row['result']=='LOSS' and row['exit_time']=='60000'
