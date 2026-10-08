from staff_s8_evidence import *
import sys,numpy as np
sys.path.insert(0,str(ROOT/'Part2'))
from part1_host.capture import *
out=[]
for name in ('final_mt5','btc_weekend_previous'):
    a=parse_capture_manifest(read(OUT/f'before_{name}_sigma3_v2/result.json')['retained_export']);b=parse_capture_manifest(read(OUT/f'{name}_sigma3_v2/result.json')['retained_export'])
    for x,y in zip(a['feeds'],b['feeds']):
        for l,r in zip(FeedReplay(a['symbol'],x),FeedReplay(b['symbol'],y)):
            if any(u.tobytes()!=v.tobytes() for u,v in zip(l[1:],r[1:])):
                d={'symbol':a['symbol'],'tf':x.timeframe,'observed':l[0],'shapes':[[u.shape,v.shape] for u,v in zip(l[1:],r[1:])],'cells':[]}
                for index,(u,v) in enumerate(zip(l[1:],r[1:])):
                    if u.shape==v.shape:
                        changed=np.argwhere(u.view('u8')!=v.view('u8'))
                        d['cells'].append({'array':index,'count':len(changed),'examples':[(z.tolist(),float(u[tuple(z)]),float(v[tuple(z)])) for z in changed[:4]]})
                out.append(d);break
write(OUT/'capture_difference_diagnosis.json',out);print(json.dumps(out,indent=2))
