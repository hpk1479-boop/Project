"""Actual paired captures: raw IEEE bits, STAFF snapshots, both real client paths."""
import hashlib,itertools,json,logging,sys,time
from pathlib import Path
import pandas as pd
ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'검증결과/staff_s6'
sys.path.insert(0,str(ROOT/'Part2'))
from part1_host import capture
from part1_host.runtime import Part1Runtime
from staff_golden.scenario import INDICATORS
from staff_golden.contracts import normalize

a=next((OUT/'actual_mt5_v1').glob('capture_*'))
b=next((OUT/'actual_mt5_v2').glob('capture_*'))
infos=[capture.parse_capture_manifest(p) for p in (a,b)]
assert [i['wire_version'] for i in infos]==[1,2]
assert len(infos[0]['feeds'])==len(infos[1]['feeds'])==19
count=0;errors=0;oz_count=0;digest=hashlib.sha256();kinds={};bytes_v1=0;bytes_v2=0
with Part1Runtime(symbols=['XAUUSD+'],specials=['SPECIAL7'],start_epoch=1790035200) as rt:
    staff=rt.modules['the_staff_of_moses'];schema=rt.modules['staff_schema']
    caches=[staff.StaffPipeCache('',health_session='S6_PAIR',monotonic=lambda:rt.clock.mono) for _ in range(2)]
    servers=[staff.DataServer({},staff.WonbiState(3),cache=c) for c in caches]
    clients=[rt.modules['staff_compat'].StaffCompat(rt.modules['staff_snapshot'].SnapshotClient(transport=s.dispatch_multipart)) for s in servers]
    oz=[rt.modules['monitor_OZ'].StaffClient('',transport=s.dispatch_multipart) for s in servers]
    for x,y in zip(infos[0]['feeds'],infos[1]['feeds'],strict=True):
        assert (x.timeframe,x.records)==(y.timeframe,y.records)
        for seq,(p,q,record) in enumerate(zip(capture.FeedReplay('XAUUSD+',x),capture.FeedReplay('XAUUSD+',y),
                                         capture.read_v2_records(y.path,y.records),strict=True),1):
            assert p[0]==q[0]==record[0]
            for left,right in zip(p[1:],q[1:]):
                assert left.tobytes()==right.tobytes(),(x.timeframe,p[0],'raw bits')
                digest.update(left.tobytes())
            raw=capture.pack_wire('XAUUSD+',x.timeframe,*p[1:],snapshot=seq)
            bytes_v1+=len(raw);bytes_v2+=len(record[3]);kind=record[2].kind
            kinds[str(kind)]=kinds.get(str(kind),0)+1
            rt.clock.set(p[0]);caches[0].publish_frame(raw);caches[1].publish_frame(record[3])
            left,right=[c.snapshot('XAUUSD+',x.timeframe) for c in caches]
            for field in ('time','volume','values'):
                assert getattr(left,field).tobytes()==getattr(right,field).tobytes(),(x.timeframe,p[0],field)
            assert left.source_epoch==right.source_epoch and left.indicator_validity==right.indicator_validity
            request={'symbol':'XAUUSD+','timeframes':[x.timeframe],'indicators':list(INDICATORS)+['SMA20','HMA17']}
            replies=[c.request(request) for c in clients]
            assert replies[0].keys()==replies[1].keys()
            for key,value in replies[0].items():
                if isinstance(value,pd.DataFrame):
                    pd.testing.assert_frame_equal(value,replies[1][key],check_exact=True)
                    assert value.attrs==replies[1][key].attrs
                else:assert value==replies[1][key],(p[0],x.timeframe,replies)
            if 'error' in replies[0]:errors+=1
            oreplies=[c.request('XAUUSD+',[x.timeframe]) for c in oz]
            assert oreplies[0].keys()==oreplies[1].keys()
            for key,value in oreplies[0].items():
                if isinstance(value,pd.DataFrame):
                    pd.testing.assert_frame_equal(value,oreplies[1][key],check_exact=True)
                    assert value.attrs==oreplies[1][key].attrs;oz_count+=1
                else:assert value==oreplies[1][key]
            count+=1
        print('paired exact',x.timeframe,count,flush=True)
result={'equal':True,'actual_mt5_execution':True,'frames':count,'unavailable_equal':errors,
        'oz_frames':oz_count,'raw_payload_sha256':digest.hexdigest(),'v2_kinds':kinds,
        'v1_full_wire_bytes':bytes_v1,'v2_wire_bytes':bytes_v2,'byte_ratio':bytes_v2/bytes_v1,
        'same_interval':True,'client_paths':['StaffCompat','OZ StaffClient'],
        'scope':'raw bytes, immutable Snapshot, all client values/dtypes/index/columns/attrs/errors'}
(OUT/'actual_mt5_pair.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
print(result,flush=True)
