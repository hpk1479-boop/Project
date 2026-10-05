"""Indexed fact reevaluation and lossless STAFF inspection reuse."""
from pathlib import Path
from types import SimpleNamespace as NS
import copy,socket,sys,threading,zlib
import numpy as np
import pytest
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'Part1/program'),str(ROOT/'Part2')]
from composer_fact_index import SpecRegistry
from event_composer_domain import ComposerManager
import staff_schema as wire

@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def deny(*a,**kw):raise AssertionError('actual network forbidden')
    monkeypatch.setattr(socket,'create_connection',deny)
    for name in ('connect','connect_ex','sendto'):monkeypatch.setattr(socket.socket,name,deny)

def spec(name,*conditions,symbol='XAUUSD+',enabled=True):
    return NS(spec_id=name,symbol=symbol,enabled=enabled,conditions=tuple(NS(kind=k,tf=tf) for k,tf in conditions))

def test_registration_dependencies_replace_remove_restore_and_fallback():
    a=spec('a',('SWEEP','1m'));b=spec('b',('FVG','5m'),('TREND_METRIC','1m'))
    unknown=spec('unknown',('FUTURE_KIND','1h'))
    registry=SpecRegistry({'a':a,'b':b,'unknown':unknown,'other':spec('other',('SWEEP','1m'),symbol='BTCUSD')})
    assert [s.spec_id for s in registry.affected('XAUUSD+','1m','SWEEP')]==['a','unknown']
    assert [s.spec_id for s in registry.affected('XAUUSD+','1m','TREND')]==['b','unknown']
    registry['a']=spec('a',('SWEEP','5m'))
    assert list(registry)==['a','b','unknown','other']
    assert [s.spec_id for s in registry.affected('XAUUSD+','1m','SWEEP')]==['unknown']
    del registry['unknown']
    restored=copy.deepcopy(registry)
    registry.clear()
    assert [s.spec_id for s in restored.affected('XAUUSD+','5m','SWEEP')]==['a']
    assert not registry.affected('XAUUSD+','5m','SWEEP')

def test_snapshot_evaluates_only_affected_specs_and_unresolved_fallback():
    m=ComposerManager.__new__(ComposerManager);m._lock=threading.RLock()
    m.official_specs={'a':spec('a',('TREND','1m')),'b':spec('b',('SWEEP','5m'))}
    m.manual_specs={'fallback':spec('fallback',('UNKNOWN','1m'))}
    m._source_bindings={};m._fact_revisions=NS(put=lambda *a:None);m.trend_facts={}
    calls=[];m._evaluate_specs_locked=lambda symbol,specs:calls.extend(x.spec_id for x in specs)
    response=m._reconcile_facts({'strategy':'TREND','symbol':'XAUUSD+','source_tf':'1m','complete':True,
        'fact_revision':[1,1],'facts':[{'symbol':'XAUUSD+','source_tf':'1m','trend':'UP'}]})
    assert response['ok'] and calls==['a','fallback']
    m.official_specs={'new':spec('new',('TREND','1m'))}
    calls.clear();m._evaluate_fact_dependents_locked('XAUUSD+','1m','TREND')
    assert calls==['new','fallback']

def packets():
    n=48;times=np.arange(20,dtype='<i8')*60+1756684800
    values=np.arange(20*len(wire.PIPE_VALUE_COLUMNS),dtype='<f8').reshape(20,-1)
    values.view('<u8')[0,4:9]=[0,0x8000000000000000,0x7ff8000000000001,0x7ff0000000000000,0x7fefffffffffffff]
    result=[]
    for seq in range(1,6):
        v=values.copy();v[-1,0]+=seq
        kind=wire.WIRE_FULL if seq in (1,4) else wire.WIRE_ROW
        t=times if kind==wire.WIRE_FULL else times[-1:]
        v=v if kind==wire.WIRE_FULL else v[-1:]
        children=[wire.pack_v2('XAUUSD+',tf,t,np.ones(len(t),dtype='<i8'),v,seq=seq,kind=kind) for tf in ('1m','5m')]
        result.append(wire.pack_bundle('XAUUSD+',children,seq=seq,sent_at_ms=seq*1000))
    return result

def test_delta_restoration_keeps_all_bits_and_build_verification(tmp_path):
    from event_backtest.delta import DeltaCodec,write_delta,verify_delta,read_delta
    raw=packets();encoder=DeltaCodec();checked=DeltaCodec();staff_checked=DeltaCodec(verify_crc=False)
    for original in raw:
        structure,bits=encoder.encode(original)
        assert checked.decode(structure,bits)==original==staff_checked.decode(structure,bits)
    path=tmp_path/'capture.delta.gz';expected=write_delta(enumerate(raw,1),path)
    assert verify_delta(path,expected)==expected
    assert list(read_delta(path,verify_crc=False))==list(enumerate(raw,1))

def test_inspection_reuses_only_same_immutable_bytes_and_staff_still_checks_seq(tmp_path,monkeypatch):
    from event_host import load_staff
    staff=load_staff();cache=staff.StaffPipeCache('',monotonic=lambda:0.,gap_journal=tmp_path/'gaps.jsonl')
    original=wire.decode_v2;calls=[]
    def decode(raw):calls.append(raw);return original(raw)
    monkeypatch.setattr(wire,'decode_v2',decode)
    raw=packets()[0];cache.inspect_publication(raw)
    publication=cache.receive_publication(raw=raw,require_observation=True)
    assert sum(value is raw for value in calls)==1 and set(publication.feeds)=={'1m','5m'}
    # Same bytes still take canonical seq rejection, even when decoding reused.
    cache.inspect_publication(raw)
    duplicate=cache.receive_publication(raw=raw,require_observation=True)
    assert not duplicate.feeds
    mutable=bytearray(packets()[1]);cache.inspect_publication(mutable);mutable[-1]^=1
    with pytest.raises(wire.WireError,match='CRC'):cache.receive_publication(raw=mutable)
    changed=bytearray(packets()[2]);cache.inspect_publication(bytes(changed));changed[-1]^=1
    with pytest.raises(wire.WireError,match='CRC'):cache.receive_publication(raw=bytes(changed))

def test_restore_can_defer_crc_only_when_staff_validates(tmp_path):
    from event_backtest.delta import DeltaCodec
    from event_host import load_staff
    raw=packets()[0];encoder=DeltaCodec();structure,bits=encoder.encode(raw)
    broken=bits.copy();broken[-1]^=np.uint64(1)
    with pytest.raises(ValueError,match='CRC'):DeltaCodec().decode(structure,broken)
    unchecked=DeltaCodec(verify_crc=False).decode(structure,broken)
    cache=load_staff().StaffPipeCache('',monotonic=lambda:0.,gap_journal=tmp_path/'gaps.jsonl')
    with pytest.raises(wire.WireError,match='CRC'):cache.receive_publication(raw=unchecked)
    assert not cache.keys()
