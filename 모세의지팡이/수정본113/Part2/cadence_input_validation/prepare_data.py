import json,hashlib,shutil
from pathlib import Path
import sys
root=Path(sys.argv[1]);sys.path.insert(0,str(root))
import numpy as np
from generic_backtest.history.cache import RawChunkWriter,verify_archive
from generic_backtest.canonical import identity,write_json
from pit.archive.reader import TICK_DTYPE
source=next((root/'generic_cache/raw').iterdir());original=json.loads((source/'manifest.json').read_text())
# Input subsets contain consecutive original records, unmodified, including duplicates.
chunks=[c for c in original['chunks'] if c['count']]
for n in (120,5000,50000,200000,1000000):
 target=root/f'generic_cache/cadence_input_validation/real_{n}'
 if (target/'manifest.json').exists():continue
 writer=RawChunkWriter(target,original['stream_namespace']);out=[];remaining=n;all_hash=hashlib.sha256()
 start=original['coverage_start_ns'];first=chunks[0]['coverage_start_ns']
 out.append(writer.write(0,np.empty(0,dtype=TICK_DTYPE),start,first))
 previous=first;orig_files=[]
 for number,d in enumerate(chunks,1):
  if remaining<=0:break
  a=np.load(source/d['file'],allow_pickle=False,mmap_mode='r');k=min(remaining,len(a))
  # Complete the last timestamp group to preserve same-ms tick ordering at EOF.
  while k<len(a) and a['time_msc'][k]==a['time_msc'][k-1]:k+=1
  rows=a[:k];end=int(rows['time_msc'][-1]+1)*1000000
  if k==len(a):end=d['coverage_end_ns']
  if d['coverage_start_ns']>previous:
   out.append(writer.write(len(out),np.empty(0,dtype=TICK_DTYPE),previous,d['coverage_start_ns']))
  desc=writer.write(len(out),rows,d['coverage_start_ns'],end);out.append(desc);previous=end
  all_hash.update(rows.tobytes());remaining-=k;orig_files.append({'source_file':d['file'],'source_file_sha256':d['file_sha256'],'rows':k})
 m={**original,'chunks':out,'coverage_start_ns':start,'coverage_end_ns':previous,
    'count':sum(c['count'] for c in out),'raw_sha256':all_hash.hexdigest(),
    'validation_subset':{'kind':'CONSECUTIVE_UNALTERED_BROKER_RAW_PREFIX','source_archive_identity':original['archive_identity'],'source_chunks':orig_files,'requested_rows':n,
      'note':'Metadata-only empty prehistory chunks consolidated for both benchmark arms. No native warmup bars injected.'}}
 m.pop('archive_identity',None);m['gaps']=[g for g in original.get('gaps',[]) if g['start_ns']<previous]
 m['archive_identity']=identity(m);write_json(target/'manifest.json',m);verify_archive(target)
 print(target,m['count'],flush=True)
