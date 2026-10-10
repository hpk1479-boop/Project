"""Follow the differing final OZ watches to the commands that registered them."""
from pathlib import Path
import json
from compare_phase0_trace import records
ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'검증결과/schema_cleanup/phase0'
def main():
    identities=json.loads((OUT/'identity_diagnostic.json').read_text('utf-8'));result={}
    for revision,entries in identities.items():
        wanted=set()
        for entry in entries:
            for row in entry['chain']:
                content=(row['cause'].get('parent_payload') or {}).get('content',{})
                wanted.update(content.get('event',{}).get('watch_ids',[]))
        events=[]
        for r in records(OUT/f'revision{revision}/signals.jsonl.gz'):
            c=r['payload']['content'];cmd=c.get('command',{})
            if cmd.get('watch_id') in wanted and cmd.get('action')=='MANUAL_WATCH':events.append(r)
        result[revision]=events
        for r in events:
            c=r['payload']['content']['command']
            print(revision,r['time'],r['bundle'],c['watch_id'],c.get('source_spec_id'))
    (OUT/'watch_identity_origins.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
if __name__=='__main__':main()
