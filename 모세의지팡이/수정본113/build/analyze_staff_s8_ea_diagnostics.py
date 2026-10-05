from collections import Counter
import numpy as np,sys
from staff_s8_evidence import *
sys.path.insert(0,str(ROOT/'Part2'))
from part1_host.capture import *
result=[]
for name in ('final_mt5','btc_weekend_previous'):
    paths=[read(OUT/f'{p}{name}_sigma3_v2/result.json')['retained_export'] for p in ('before_','diagnosis_before_','')]
    infos=[parse_capture_manifest(p) for p in paths];differences=[];same_mask=True;count=0
    for feeds in zip(*(info['feeds'] for info in infos)):
        for obs in zip(*(FeedReplay(infos[i]['symbol'],f) for i,f in enumerate(feeds))):
            # Separate independent EA runs may expose pre-existing undefined
            # indicator warmup storage. Never rewrite or normalize that input.
            old,repeat,new=obs
            assert old[0]==repeat[0]==new[0]
            masks=[old[3].view('u8')!=x[3].view('u8') for x in (repeat,new)]
            if masks[1].any():
                count+=1
                if len(differences)<3:differences.append({'tf':feeds[0].timeframe,'observed':old[0],'rows':np.unique(np.argwhere(masks[1])[:,0]).tolist(),'columns':np.unique(np.argwhere(masks[1])[:,1]).tolist()})
            if np.any(masks[1]&~masks[0]):same_mask=False
    result.append({'symbol':infos[0]['symbol'],'differing_observations':count,'all_S8_difference_cells_also_differ_in_S7_repeat':same_mask,'examples':differences})
rows=[]
for label in ('btc_weekend_previous','final_mt5'):
    path=OUT/f'probe_{label}_sigma3_v2/result.json'
    if path.exists():
        probe=read(path)
        rows.extend(line.split('\t') for line in (Path(probe['retained_export'])/'live_path_probe.tsv').read_text('ascii').splitlines())
bad=[r for r in rows if int(r[3])!=0]
report={'S7_repeat_diagnosis':result,'live_fast_vs_full_same_observation':{'observations':len(rows),'kinds':dict(Counter(r[2] for r in rows)),'differences':len(bad),'first_differences':bad[:20]},
        'raw_EA_output_gate_passed':read(OUT/'ea_output_equality.json')['passed'],
        'automatic_exception_approved':False}
write(OUT/'ea_diagnosis.json',report);print(json.dumps(report,indent=2))
