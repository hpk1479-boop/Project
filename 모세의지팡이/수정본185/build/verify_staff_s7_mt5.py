"""Representative actual EA values; no comparison to old Python Wonbi."""
import sys
import numpy as np
from staff_s7_evidence import *
sys.path.insert(0,str(ROOT/'Part2'))
from part1_host.capture import parse_capture_manifest,FeedReplay,read_v2_records,wire_schema
from part1_host.runtime import Part1Runtime

def capture(sigma):
    folder=OUT/f'final_mt5_sigma{sigma:g}_v2'
    return Path(read(folder/'result.json')['retained_export'])

records={};samples=[]
for sigma in (3.,2.5):
    info=parse_capture_manifest(capture(sigma));records[sigma]={}
    for feed in info['feeds']:
        items=list(FeedReplay(info['symbol'],feed)); assert items
        # Representative start, middle, end, through actual FULL/ROW reconstruction.
        selected=[items[i] for i in sorted({0,len(items)//2,len(items)-1})]
        records[sigma][feed.timeframe]={x[0]:x for x in selected}
        for observed,t,v,x in selected:
            assert x.shape[1]==48
            assert np.all(x[:,47]==sigma)
            good=np.isfinite(x[:,45:47]).all(axis=1)&(np.abs(x[:,45:47])<1e300).all(axis=1)
            assert good.any()
            assert np.all(x[good,45]>=x[good,12]) and np.all(x[good,46]<=x[good,12])
            if sigma==3.:
                assert x[good,45].tobytes()==x[good,18].tobytes()
                assert x[good,46].tobytes()==x[good,17].tobytes()
            samples.append({'sigma':sigma,'tf':feed.timeframe,'observed':observed,'mid_upper_lower_sigma':x[-1,[12,45,46,47]].tolist(),'rows':len(t)})

width_checks=0
for tf,feeds in records[3.].items():
    for observed,(_,t,v,a) in feeds.items():
        other=records[2.5][tf].get(observed)
        if other is None:continue
        b=other[3]; good=(np.abs(a[:,45:47])<1e300).all(axis=1)&(np.abs(b[:,45:47])<1e300).all(axis=1)
        assert np.array_equal(a[good,12],b[good,12])
        assert np.allclose((b[good,45]-b[good,46])*3.,(a[good,45]-a[good,46])*2.5,rtol=1e-10,atol=1e-10)
        width_checks+=1
assert width_checks>=19

# Actual EA bytes -> STAFF -> Snapshot client -> compatibility output (one sample / TF).
delivered=0
with Part1Runtime(symbols=['XAUUSD+'],specials=['SPECIAL7'],start_epoch=1790164800) as rt:
    from staff_snapshot import SnapshotClient
    import staff_compat
    info=parse_capture_manifest(capture(2.5));w=wire_schema()
    client=SnapshotClient(transport=rt.staff_server.dispatch_multipart)
    for feed in info['feeds']:
        observed,t,v,x=next(iter(FeedReplay(info['symbol'],feed)))
        rt.clock.set(observed);rt.publish(w.pack_v2(info['symbol'],feed.timeframe,t,v,x,seq=1))
        df=client.raw_frames({'symbol':info['symbol'],'timeframes':[feed.timeframe]})[feed.timeframe]
        out=staff_compat.apply_requested_features(df,[])
        assert out[['wonbi_upper','wonbi_lower','wonbi_sigma']].iloc[-1].to_numpy().tobytes()==x[-1,45:48].tobytes()
        assert out.wonbi_mid.iloc[-1]==x[-1,12]
        delivered+=1
result={'passed':True,'schema_id':hex(wire_schema().WIRE_SCHEMA_ID),'actual_samples':len(samples),'width_checks':width_checks,'client_delivery_feeds':delivered,'samples':samples,'old_python_wonbi_compared':False}
write(OUT/'mt5_wonbi_verification.json',result)
print('Actual EA Wonbi checks:',len(samples),'samples;',width_checks,'paired widths;',delivered,'client feeds')
