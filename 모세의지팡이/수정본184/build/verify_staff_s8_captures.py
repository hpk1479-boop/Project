from itertools import zip_longest
from collections import Counter
import sys
from staff_s8_evidence import *
sys.path.insert(0,str(ROOT/'Part2'))
from part1_host.capture import parse_capture_manifest,FeedReplay,read_v2_records
results=[]
for name in ('final_mt5','btc_weekend_previous'):
    a=read(OUT/f'before_{name}_sigma3_v2/result.json');b=read(OUT/f'{name}_sigma3_v2/result.json')
    before=parse_capture_manifest(a['retained_export']);after=parse_capture_manifest(b['retained_export'])
    report={'symbol':before['symbol'],'observations':0,'differences':[], 'S7_kinds':Counter(),'S8_kinds':Counter()}
    assert before['symbol']==after['symbol']
    for x,y in zip_longest(before['feeds'],after['feeds']):
        assert x is not None and y is not None and x.timeframe==y.timeframe
        for label,f in (('S7_kinds',x),('S8_kinds',y)):
            report[label].update(str(r[2].kind) for r in read_v2_records(f.path,f.records))
        for left,right in zip_longest(FeedReplay(before['symbol'],x),FeedReplay(after['symbol'],y)):
            report['observations']+=1
            same=left is not None and right is not None and left[0]==right[0] and all(u.tobytes()==v.tobytes() and u.shape==v.shape for u,v in zip(left[1:],right[1:]))
            if not same:
                report['differences'].append({'tf':x.timeframe,'before_observed':left[0] if left else None,'after_observed':right[0] if right else None})
    report['passed']=not report['differences'];results.append(report)
write(OUT/'ea_output_equality.json',{'passed':all(r['passed'] for r in results),'cases':results})
print([(r['symbol'],r['observations'],r['passed']) for r in results])
assert all(r['passed'] for r in results)
