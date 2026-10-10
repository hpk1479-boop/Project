"""Summarize completed evidence without rerunning simulations or changing inputs."""
import csv,json
from collections import Counter
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'검증결과/numpy_processors'
def read(name):return json.loads((OUT/name).read_text('utf-8'))
def write(name,data):(OUT/name).write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
def parity():
    results=[]
    for case in ('synthetic240','actualXAU'):
        a,b=(read(f'{case}_{mode}_final.json') for mode in ('live','replay'))
        item=dict(case=case,bundles=a['bundles'],signals=len(a['signals']),notifications=len(a['telegram']),
            signal_equal=a['signals']==b['signals'],outputs_equal=a['output_deliveries']==b['output_deliveries'],
            errors=a['errors']+b['errors'],source_equal=a['source_hashes']==b['source_hashes'],
            checkpoint_equal=b.get('checkpoint_equal'))
        results.append(item)
        assert item['signal_equal'] and item['outputs_equal'] and not item['errors'] and item['source_equal'],item
    write('live_replay_comparison.json',results);print(results)
def alerts():
    results=[]
    for case in ('day','week'):
        if not all((OUT/f'revision{r}_{case}/result.json').exists() for r in (21,22)):continue
        counts=[]
        for revision in (21,22):
            with (OUT/f'revision{revision}_{case}/alerts.csv').open(encoding='utf-8',newline='') as h:
                counts.append(Counter(json.dumps({k:v for k,v in row.items() if k!='run_id'},ensure_ascii=False,sort_keys=True) for row in csv.DictReader(h)))
        before,after=counts
        removed=[json.loads(x) for x in (before-after).elements()];added=[json.loads(x) for x in (after-before).elements()]
        write(f'alert_differences_{case}.json',dict(removed=removed,added=added,gate=False))
        results.append(dict(case=case,before=sum(before.values()),after=sum(after.values()),removed=len(removed),added=len(added),
            before_measurement=read(f'revision21_{case}/result.json'),after_measurement=read(f'revision22_{case}/result.json')))
    write('alert_comparison.json',results)
    print([{k:v for k,v in r.items() if not k.endswith('measurement')} for r in results])
if __name__=='__main__':parity();alerts()
