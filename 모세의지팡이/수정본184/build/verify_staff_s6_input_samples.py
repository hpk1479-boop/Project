"""Representative decision-input samples, per user's S6+ policy. No golden writes."""
import hashlib,itertools,json,logging,sqlite3,sys
from types import SimpleNamespace
import numpy as np
import pandas as pd
from staff_s6_evidence import *
sys.path.insert(0,str(ROOT/'Part2'))
from part1_host import capture
from part1_host.runtime import Part1Runtime
from staff_golden.scenario import INDICATORS
from staff_golden.contracts import read_frame

left=next((OUT/'actual_mt5_v1').glob('capture_*'))
right=next((OUT/'final_mt5_v2').glob('capture_*'))
infos=[capture.parse_capture_manifest(p) for p in (left,right)]
assert [v['wire_version'] for v in infos]==[1,2]
samples=[];golden_samples=[]
with Part1Runtime(symbols=['XAUUSD+'],specials=['SPECIAL7'],start_epoch=1790035200) as rt:
    logging.disable(logging.CRITICAL)
    staff=rt.modules['the_staff_of_moses'];schema=rt.modules['staff_schema'];lib=rt.modules['staff_snapshot']
    caches=[staff.StaffPipeCache('',health_session='SAMPLE',monotonic=lambda:rt.clock.mono) for _ in range(2)]
    servers=[staff.DataServer({},staff.WonbiState(3),cache=c) for c in caches]
    clients=[rt.modules['staff_compat'].StaffCompat(lib.SnapshotClient(transport=s.dispatch_multipart)) for s in servers]
    for a,b in zip(infos[0]['feeds'],infos[1]['feeds'],strict=True):
        assert (a.timeframe,a.records)==(b.timeframe,b.records)
        selected={0,1,59,119,a.records-1}
        for index,(x,y) in enumerate(zip(capture.FeedReplay('XAUUSD+',a),capture.FeedReplay('XAUUSD+',b),strict=True)):
            if index not in selected:continue
            assert x[0]==y[0]
            # Full time/volume/OHLC and current decision-ready MT5 buffers. The
            # oldest indicator warm-up cells are diagnosed separately: S0 and
            # repeated unchanged v1 runs already contain process-dependent
            # subnormal values there. Never normalize or alter those inputs.
            for u,v in zip(x[1:3],y[1:3]):assert np.array_equal(u,v),(a.timeframe,index)
            assert np.array_equal(x[3][:,:4],y[3][:,:4]),(a.timeframe,index,'OHLC')
            assert np.array_equal(x[3][-20:],y[3][-20:],equal_nan=True),(a.timeframe,index,'current MT5')
            rt.clock.set(max(rt.clock.time(),x[0]));seq=index+1
            caches[0].publish_frame(capture.pack_wire('XAUUSD+',a.timeframe,*x[1:],snapshot=seq))
            caches[1].publish_frame(schema.pack_v2('XAUUSD+',a.timeframe,*y[1:],seq=seq))
            req={'symbol':'XAUUSD+','timeframes':[a.timeframe],'indicators':list(INDICATORS)+['SMA20','HMA17']}
            replies=[c.request(req) for c in clients]
            assert replies[0].keys()==replies[1].keys()
            if 'error' in replies[0]:assert replies[0]==replies[1]
            else:
                a_df,b_df=[r[a.timeframe] for r in replies]
                columns=[c for c in a_df if c=='atr_14' or c.startswith('wonbi_') or 'regime' in c or c in schema.PIPE_VALUE_COLUMNS]
                for col in columns:assert np.array_equal(a_df[col].to_numpy()[-20:],b_df[col].to_numpy()[-20:],equal_nan=True),col
            samples.append({'tf':a.timeframe,'record':index,'second':x[0],'ready':'error' not in replies[0]})
    # The actual S0 golden is read-only. One representative per timeframe,
    # including ATR14_GENERAL, OZ states/regime and Wonbi at the real client edge.
    path=S0/'actual_run1/golden.sqlite';original_hash=sha(path)
    with sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True) as db:
        selected={}
        for response, in db.execute('SELECT response FROM cases ORDER BY rowid'):
            for tf,item in json.loads(response).items():
                if isinstance(item,dict) and 'frame_sha256' in item:selected.setdefault(tf,item['frame_sha256'])
        wire=[];client=rt.modules['monitor_OZ'].StaffClient('',transport=lambda parts:wire)
        for tf,digest in selected.items():
            blob=db.execute('SELECT data FROM frames WHERE sha=?',(digest,)).fetchone()[0]
            expected=read_frame(db,blob)
            snap=SimpleNamespace(time=pd.DatetimeIndex(expected.time).as_unit('s').asi8,
                volume=expected.volume.to_numpy(dtype='<i8'),values=expected[schema.PIPE_VALUE_COLUMNS].to_numpy(dtype='<f8'),
                seq=1,source_epoch=expected.attrs['source_epoch'],indicator_validity=expected.attrs['indicator_validity'],received_at=1.)
            wire=lib.encode_reply([('XAUUSD+',tf,snap)],sigma=float(expected.wonbi_sigma.iloc[-1]))
            actual=client.request('XAUUSD+',[tf])[tf]
            columns=[c for c in actual if c=='atr_14' or c.startswith('wonbi_') or 'regime' in c or 'percentile' in c or c in schema.PIPE_VALUE_COLUMNS]
            for col in columns:assert np.array_equal(expected[col].to_numpy(),actual[col].to_numpy(),equal_nan=True),(tf,col)
            golden_samples.append({'tf':tf,'reference_frame':digest,'columns':columns})
    assert sha(path)==original_hash
write(OUT/'decision_input_samples.json',{'equal':True,'actual_paired_samples':samples,'golden_samples':golden_samples,
    'golden_sha256_unchanged':original_hash,'sample_policy':'per TF records 0,1,59,119,last; S0 actual one successful representative per TF',
    'internal_dataframe_bitwise_gate':False,'large_combination_repetition':False,
    'paired_scope':'all time/volume/OHLC; latest 20 MT5/derived rows per selected observation',
    'warmup_diagnostic':'existing_MT5_history_diagnostic.json'})
print('Representative inputs PASS',len(samples),len(golden_samples),flush=True)
