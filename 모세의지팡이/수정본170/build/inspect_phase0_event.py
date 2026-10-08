"""Inspect a bounded event from existing traces, preserving complete payloads."""
import argparse,json
from pathlib import Path
from compare_phase0_trace import records
ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'검증결과/schema_cleanup/phase0'
def main():
    p=argparse.ArgumentParser();p.add_argument('bundle',type=int);args=p.parse_args()
    result={}
    for revision in (21,22):
        folder=OUT/f'revision{revision}'
        signals=[r for r in records(folder/'signals.jsonl.gz') if r['bundle']==args.bundle]
        causes={r['signal_id']:r for r in records(folder/'causes.jsonl.gz') if r['bundle']==args.bundle}
        result[str(revision)]=[dict(signal=r,cause=causes.get(r['payload']['signal_id'])) for r in signals]
        for row in result[str(revision)]:
            c=row['signal']['payload']['content'];family=c.get('family',c.get('type'))
            if family not in ('FVG','SWEEP','TREND'):
                print(revision,json.dumps(row,ensure_ascii=False))
    (OUT/f'event_{args.bundle}.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
if __name__=='__main__':main()
