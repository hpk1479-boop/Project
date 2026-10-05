"""Compare completed external observations; never edit expected results."""
from pathlib import Path
import json,collections
ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'검증결과/schema_cleanup'
def read(name):return json.loads((OUT/'behavior'/name/'result.json').read_text('utf-8'))
def write(name,v):(OUT/(name+'.json')).write_text(json.dumps(v,ensure_ascii=False,indent=2),encoding='utf-8')

def main():
    results=[]
    for case in ('synthetic240','day','timer239'):
        a,b=(read('23_'+case+'_'+mode+'_final') for mode in ('live','replay'))
        item={'case':case,'bundles':a['bundles'],'signals':a['signals'],'notifications':len(a['alerts']),
              'signal_equal':a['signal_sha256']==b['signal_sha256'],'alerts_equal':a['alerts']==b['alerts'],
              'outputs_equal':a['deliveries']==b['deliveries'],'sources_equal':a['source_hashes']==b['source_hashes'],
              'source_unchanged':a['source_unchanged'] and b['source_unchanged'],'errors':a['errors']+b['errors']}
        assert all(item[k] for k in ('signal_equal','alerts_equal','outputs_equal','sources_equal','source_unchanged')) and not item['errors'],item
        results.append(item)
    write('live_replay_comparison',results)
    old,new=read('22_day_replay'),read('23_day_replay_final')
    canon=lambda xs:collections.Counter(json.dumps(x,ensure_ascii=False,sort_keys=True) for x in xs)
    a,b=canon(old['alerts']),canon(new['alerts'])
    removed=[json.loads(x) for x in (a-b).elements()];added=[json.loads(x) for x in (b-a).elements()]
    write('alert_differences',{'removed':removed,'added':added,'old_notifications':len(old['alerts']),'new_notifications':len(new['alerts']),
        'old_signals':old['signals'],'new_signals':new['signals'],'internal_signal_equal':old['signal_sha256']==new['signal_sha256'],
        'categories':{k:0 for k in ('EMA20 전환','초기값 수정','MT5 버퍼 전환','0번 수정','원비 계산 이전','기타')},'gate':False})
    assert not removed and not added,'Classify concrete alert differences before reporting'
    filtered=OUT/'behavior/23_filtered_day_replay_final/result.json'
    if filtered.exists():
        b=read('23_filtered_day_replay_final');a=new
        result={'bar_bundles':a['bundles'],'filtered_timer_bundles':b['bundles'],'signals':a['signals'],
                'signal_equal':a['signal_sha256']==b['signal_sha256'],'alerts_equal':a['alerts']==b['alerts'],
                'outputs_equal':a['deliveries']==b['deliveries'],'errors':b['errors']}
        write('bar_timer_alerts',result)
        assert result['signal_equal'] and result['alerts_equal'] and result['outputs_equal'] and not result['errors'],result
    print(results)

if __name__=='__main__':main()
