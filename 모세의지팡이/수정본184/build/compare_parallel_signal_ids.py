"""Compact ID inventory from complete signal traces; no giant frame retention."""
from pathlib import Path
import argparse,collections,csv,gzip,hashlib,json
ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'검증결과/parallel_oz'
def scan(path):
    index={};counts=collections.Counter();occurrences=collections.Counter();repeated=collections.Counter()
    with gzip.open(path,'rt',encoding='utf-8') as f:
        for line in f:
            row=json.loads(line);content=row['content'];event=content.get('event') or content.get('command') or content
            family=content.get('family') or content.get('type') or row['strategy']
            counts[family]+=1
            identity=row['signal_id'];occurrences[identity]+=1;key=(identity,occurrences[identity])
            if key[1]>1:repeated[family]+=1
            metadata={'signal_id':identity,'occurrence':key[1],'time_ms':row['time'],'producer':row['strategy'],'family':family,
                      'kind':event.get('kind',event.get('action','')),'symbol':row.get('symbol',''),
                      'tf':event.get('source_tf',event.get('tf','')),'direction':event.get('direction','')}
            digest=hashlib.sha256(line.rstrip('\n').encode()).hexdigest()
            index[key]=(digest,metadata)
    return index,dict(counts),dict(repeated)

def selected_payloads(path,identities):
    result={};seen=collections.Counter()
    with gzip.open(path,'rt',encoding='utf-8') as f:
        for line in f:
            value=json.loads(line);sid=value['signal_id'];seen[sid]+=1
            if (sid,seen[sid]) in identities:result[sid,seen[sid]]=value
    return result

def field_differences(a,b,path=''):
    if isinstance(a,dict) and isinstance(b,dict):
        return [item for key in sorted(a.keys()|b.keys()) for item in field_differences(a.get(key),b.get(key),path+'/'+key)]
    if isinstance(a,list) and isinstance(b,list) and len(a)==len(b):
        return [item for i,(x,y) in enumerate(zip(a,b)) for item in field_differences(x,y,path+'/'+str(i))]
    return [] if a==b else [{'field':path,'before':a,'after':b}]
def main():
    p=argparse.ArgumentParser();p.add_argument('--warehouse',required=True);a=p.parse_args();runs=Path(a.warehouse)/'runs'
    folders=[runs/name for name in ('parallel26_before_week_all_trace','parallel26_final_seed0')]
    if not all((p/'result.json').exists() for p in folders):raise ValueError('both complete trace runs required')
    before,bc,br=scan(folders[0]/'signals.jsonl.gz');after,ac,ar=scan(folders[1]/'signals.jsonl.gz')
    removed=set(before)-set(after);added=set(after)-set(before)
    changed=[k for k in before.keys()&after.keys() if before[k][0]!=after[k][0]]
    rows=[]
    for label,keys,index in (('removed_id',removed,before),('added_id',added,after),('same_id_payload_changed',changed,after)):
        rows.extend({'difference':label,**index[key][1]} for key in sorted(keys,key=lambda k:(index[k][1]['time_ms'],k)))
    with (OUT/'changed_signal_ids.csv').open('w',encoding='utf-8-sig',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=('difference','signal_id','occurrence','time_ms','producer','family','kind','symbol','tf','direction'))
        writer.writeheader();writer.writerows(rows)
    bp=selected_payloads(folders[0]/'signals.jsonl.gz',removed|set(changed))
    ap=selected_payloads(folders[1]/'signals.jsonl.gz',added|set(changed))
    details=[]
    for key in sorted(changed):
        details.append({'before_id':key[0],'after_id':key[0],'differences':field_differences(bp[key],ap[key])})
    def identity(meta):return tuple(meta[k] for k in ('time_ms','producer','family','kind','symbol','tf','direction'))
    for key in sorted(removed):
        matches=[k for k in added if identity(after[k][1])==identity(before[key][1])]
        if len(matches)==1:
            other=matches[0]
            details.append({'before_id':key[0],'after_id':other[0],'differences':field_differences(bp[key],ap[other])})
    (OUT/'signal_id_payload_differences.json').write_text(json.dumps(details,ensure_ascii=False,indent=2),encoding='utf-8')
    summary={'before_counts':bc,'after_counts':ac,'counts_same':bc==ac,'removed_id_occurrences':len(removed),'added_id_occurrences':len(added),
             'removed_unique_ids':len({k[0] for k in before}-{k[0] for k in after}),
             'added_unique_ids':len({k[0] for k in after}-{k[0] for k in before}),
             'repeated_id_occurrences_before':br,'repeated_id_occurrences_after':ar,
             'common_id_payload_changes':len(changed),'inventory':'검증결과/parallel_oz/changed_signal_ids.csv',
             'note':'A2 sorted traversal changes touch/state and dependent watch IDs once. Refer to full trace files for payloads and final alert comparison for externally visible differences.'}
    (OUT/'signal_id_changes.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(summary,ensure_ascii=False))
if __name__=='__main__':main()
