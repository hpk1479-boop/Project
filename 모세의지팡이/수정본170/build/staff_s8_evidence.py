import hashlib,json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];SOURCE=ROOT.parent/'수정본14'
OUT=ROOT/'검증결과/staff_s8';S0=ROOT/'검증결과/staff_s0';S5=ROOT/'검증결과/staff_s5'
def read(p):return json.loads(p.read_text('utf-8-sig'))
def write(p,value):p.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
def sha(p):
    with p.open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
def provenance():
    before={r['path']:r['sha256'] for r in read(OUT/'s7_frozen_manifest.json')}
    rows=[]
    for folder in ('Part1','Part2','Part3','tests'):
        for p in (ROOT/folder).rglob('*'):
            if p.is_file() and p.suffix in ('.py','.mq5','.mqh') and '__pycache__' not in p.parts:
                rel=p.relative_to(ROOT).as_posix();after=sha(p)
                if before.get(rel)!=after:rows.append({'file':rel,'before_sha256':before.get(rel),'after_sha256':after})
    write(OUT/'source_delta.json',rows);return rows
def frozen_guard():
    manifest=read(OUT/'s7_frozen_manifest.json');diff=[]
    protected=('검증결과/staff_s0/','검증결과/staff_s1/','검증결과/staff_s2/','검증결과/staff_s3/',
               '검증결과/staff_s4/','검증결과/staff_s5/','검증결과/staff_s6/','검증결과/staff_s7/','Part3/','Part1/audit/fixtures/')
    policies=('build/staff_performance_policy.json','build/staff_performance_policy.py',
              'build/staff_performance_protocol.py','성능규칙_S1_S8.md')
    for r in manifest:
        rel=r['path']
        if not (SOURCE/rel).is_file() or sha(SOURCE/rel)!=r['sha256']:diff.append('source: '+rel)
        if rel.startswith(protected) or rel in policies or 'expected' in Path(rel).name.lower():
            if not (ROOT/rel).is_file() or sha(ROOT/rel)!=r['sha256']:diff.append('protected: '+rel)
    originals={p.relative_to(SOURCE).as_posix() for p in SOURCE.rglob('*') if p.is_file()}
    diff+=['source inventory: '+p for p in sorted(originals^{r['path'] for r in manifest})]
    result={'unchanged':not diff,'differences':diff,'files':len(manifest),'original_revision':'수정본14 S7 complete with user BTC config'}
    write(OUT/'frozen_guard.json',result);return result
