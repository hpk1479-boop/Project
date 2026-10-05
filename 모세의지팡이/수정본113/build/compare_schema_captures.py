"""Streaming full-column repeat/BAR-TIMER and native OZ meaning evidence."""
from pathlib import Path
import sys,json,hashlib,collections,argparse,itertools
import numpy as np
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'Part1/program'))
import staff_schema as w
from event_engine.capture_io import records
OUT=ROOT/'검증결과/schema_cleanup';CAP=OUT/'captures';C={n:i for i,n in enumerate(w.PIPE_VALUE_COLUMNS)}

def feeds(path):
    result=[]
    for line in (path/'manifest.tsv').read_text('ascii').splitlines():
        p=line.split('\t')
        if p[0]=='pipe_feed':result.append((p[2],path/p[3],int(p[4])))
    return result

def snapshots(entry):
    tf,path,count=entry;state=None
    for observed,raw in records(path,count,'XAUUSD+',tf,milliseconds=True):
        f=w.decode_v2(raw)
        if f.kind==w.WIRE_FULL:state=[f.times.copy(),f.volumes.copy(),f.values.copy()]
        elif f.kind==w.WIRE_ROW:
            assert state is not None and state[0][-1]==f.times[-1]
            for a,b in zip(state,(f.times,f.volumes,f.values)):a[-1:]=b
        else:assert f.kind==w.WIRE_HEARTBEAT and state is not None
        yield observed,f.kind,state

def digest(state):
    h=hashlib.sha256()
    for a in state:h.update(a.tobytes())
    return h.hexdigest()

def write(name,result):
    (OUT/(name+'.json')).write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')

def repeat():
    result={'records':0,'differing_records':0,'differing_cells':{},'examples':[],'feeds':{}}
    for left,right in zip(feeds(CAP/'day_bar_a'),feeds(CAP/'day_bar_b')):
        assert left[0]==right[0];n=bad=0
        for a,b in itertools.zip_longest(snapshots(left),snapshots(right)):
            assert a is not None and b is not None and a[0]==b[0]
            n+=1
            if digest(a[2])==digest(b[2]):continue
            bad+=1
            for key,xa,xb in zip(('time','volume','values'),a[2],b[2]):
                unequal=xa.view('uint64')!=xb.view('uint64')
                if key=='values':
                    for col in np.flatnonzero(unequal.any(axis=0)):
                        name=w.PIPE_VALUE_COLUMNS[col];result['differing_cells'][name]=result['differing_cells'].get(name,0)+int(unequal[:,col].sum())
                elif unequal.any():result['differing_cells'][key]=result['differing_cells'].get(key,0)+int(unequal.sum())
                if unequal.any() and len(result['examples'])<30:
                    idx=tuple(np.argwhere(unequal)[0]);result['examples'].append({'tf':left[0],'observation':a[0],'field':key,'index':list(map(int,idx)),'left':str(xa[idx]),'right':str(xb[idx])})
        result['records']+=n;result['differing_records']+=bad;result['feeds'][left[0]]={'records':n,'different':bad}
        print('REPEAT',left[0],n,bad,flush=True)
    result['identical']=result['differing_records']==0;write('repeat_capture',result)

def finite(x):return np.isfinite(x)&(np.abs(x)<1e300)

def timer():
    result={'bar_records':0,'matched':0,'different':0,'missing':0,'examples':[],'native_meaning':{},'kinds':{}}
    specs={'price':('price_hma_6','price_band_lower','price_band_upper','price_regime_basis'),
           **{p:(p+'_val',p+'_db',p+'_ub',p+'_basis') for p in ('RSI','STO','DI')}}
    stats={p:collections.Counter() for p in specs};prev={}
    for bar,timer in zip(feeds(CAP/'day_bar_a'),feeds(CAP/'day_timer')):
        tf=bar[0];expected={t:digest(s) for t,k,s in snapshots(bar)};seen=set();local=collections.Counter()
        for t,kind,s in snapshots(timer):
            local[kind]+=1;x=s[2]
            if t in expected:
                seen.add(t);same=expected[t]==digest(s);result['matched' if same else 'different']+=1
                if not same and len(result['examples'])<30:result['examples'].append({'tf':tf,'observation':t})
            for p,(v,lo,hi,basis) in specs.items():
                row=x[-1];values=row[[C[v],C[lo],C[hi]]];ready=bool(finite(values).all())
                old='NA' if not ready else 'LOWER_OUT' if values[0]<values[1] else 'UPPER_OUT' if values[0]>values[2] else 'IN'
                lower,upper=row[C[p+'_lower_out']],row[C[p+'_upper_out']]
                native='NA' if not ready else 'LOWER_OUT' if finite(lower) else 'UPPER_OUT' if finite(upper) else 'IN'
                stats[p]['observations']+=1;stats[p]['out_state_mismatch']+=old!=native
                before=prev.get((tf,p));prev[tf,p]=(old,native)
                if before:
                    a=before[0] in ('LOWER_OUT','UPPER_OUT') and old=='IN'
                    b=before[1] in ('LOWER_OUT','UPPER_OUT') and native=='IN'
                    stats[p]['out_to_in']+=a;stats[p]['out_to_in_mismatch']+=a!=b
                slope=x[-1,C[basis]]-x[-2,C[basis]];native_slope=row[C[p+'_regime_slope']]
                if finite(x[-2:,C[basis]]).all():
                    stats[p]['slope_samples']+=1
                    stats[p]['slope_mismatch']+=not (finite(native_slope) and slope==native_slope)
                    stats[p]['slope_sign_mismatch']+=not (finite(native_slope) and np.sign(slope)==np.sign(native_slope))
                elif finite(native_slope):stats[p]['slope_readiness_mismatch']+=1
        result['bar_records']+=len(expected);result['missing']+=len(expected.keys()-seen);result['kinds'][tf]=dict(local)
        print('TIMER',tf,sum(local.values()),'bar',len(seen),'differences',result['different'],flush=True)
    result['native_meaning']={p:dict(v) for p,v in stats.items()}
    result['identical']=result['different']==result['missing']==0;write('bar_timer_native_meaning',result)

if __name__=='__main__':
    a=argparse.ArgumentParser();a.add_argument('case',choices=('repeat','timer'));args=a.parse_args()
    repeat() if args.case=='repeat' else timer()
