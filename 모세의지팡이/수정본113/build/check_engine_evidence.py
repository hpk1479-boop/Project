"""Summarize completed independent evidence, without rerunning markets."""
from pathlib import Path
import argparse,hashlib,json
ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'검증결과/engine_optimization'

def main():
    p=argparse.ArgumentParser();p.add_argument('--previous',type=Path,required=True);a=p.parse_args()
    comparisons={}
    for case in ('synthetic240','timer239'):
        live=json.loads((OUT/f'behavior/25_{case}_live_final/result.json').read_text('utf-8'))
        replay=json.loads((OUT/f'behavior/25_{case}_replay_final/result.json').read_text('utf-8'))
        fields=('signal_sha256','counts','alerts','deliveries','errors','source_hashes')
        row={'fields_equal':{key:live[key]==replay[key] for key in fields},'bundles':live['bundles'],
             'signals':live['signals'],'notifications':len(live['alerts']),'delivered_messages':len(live['deliveries']),
             'errors':live['errors'],'source_unchanged':live['source_unchanged'] and replay['source_unchanged']}
        assert all(row['fields_equal'].values()) and not row['errors'] and row['source_unchanged'],row
        comparisons[case]=row
    (OUT/'live_replay.json').write_text(json.dumps(comparisons,ensure_ascii=False,indent=2),encoding='utf-8')
    frozen=json.loads((OUT/'before_runtime_sha256.json').read_text('utf-8'))
    changed=[];checked=0
    for name,expected in frozen.items():
        if '/logs/' in name or name.endswith(('.log','.lock','.pyc')):continue
        checked+=1
        path=a.previous/name
        if not path.exists() or hashlib.sha256(path.read_bytes()).hexdigest()!=expected:changed.append(name)
    (OUT/'before_source_preservation.json').write_text(json.dumps({'files_checked':checked,'different':changed,
        'excluded':'runtime logs/locks/pyc only; no previous source is executed'},ensure_ascii=False,indent=2),encoding='utf-8')
    assert not changed,changed
    (OUT/'ready_for_measurement.json').write_text(json.dumps({'live_replay':True,'logic_tests':113,
        'memory_plateau':json.loads((OUT/'memory/result.json').read_text('utf-8'))['plateau'],
        'previous_source_equal':True},indent=2),encoding='utf-8')
    print(json.dumps(comparisons,ensure_ascii=False))

if __name__=='__main__':main()
