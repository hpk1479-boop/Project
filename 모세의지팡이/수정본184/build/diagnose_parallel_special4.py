"""Observe existing SPECIAL4 logging without changing its decision functions."""
from pathlib import Path
import collections,json,logging,sys
from measure_parallel_oz import main,OUT

counts=collections.Counter();examples={}
for level in ('info','warning','error','exception'):
    original=getattr(logging,level)
    def observe(message,*args,_level=level,_original=original,**kwargs):
        if '[SPECIAL4]' in str(message):
            key=_level+': '+str(message)
            counts[key]+=1
            examples.setdefault(key,[])
            if len(examples[key])<5:
                try:examples[key].append(str(message)%args)
                except (TypeError,ValueError):examples[key].append(str(message))
        return _original(message,*args,**kwargs)
    setattr(logging,level,observe)
try:main()
finally:
    warehouse=Path(sys.argv[sys.argv.index('--warehouse')+1])
    label=sys.argv[sys.argv.index('--label')+1]
    (warehouse/'runs'/('parallel26_'+label)/'special4_flow_diagnostic.json').write_text(json.dumps(
        [{'message':k,'count':v,'examples':examples[k]} for k,v in counts.items()],
        ensure_ascii=False,indent=2),encoding='utf-8')
