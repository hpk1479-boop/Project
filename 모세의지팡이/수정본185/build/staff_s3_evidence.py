"""S3 provenance with whole S2 source and all prior evidence frozen."""
import hashlib
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT.parent/'수정본9'
OUT=ROOT/'검증결과/staff_s3'
S0=ROOT/'검증결과/staff_s0'
S1=ROOT/'검증결과/staff_s1'
S2=ROOT/'검증결과/staff_s2'

def read(path): return json.loads(path.read_text('utf-8-sig'))
def write(path,value): path.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
def sha(path):
    with path.open('rb') as stream: return hashlib.file_digest(stream,'sha256').hexdigest()

def frozen_guard():
    manifest=read(OUT/'s2_frozen_manifest.json')
    expected={r['path'] for r in manifest}
    actual={p.relative_to(SOURCE).as_posix() for p in SOURCE.rglob('*') if p.is_file()}
    differences=['original inventory: '+p for p in sorted(expected^actual)]
    protected_count=0
    for item in manifest:
        rel=item['path']
        if not (SOURCE/rel).is_file() or sha(SOURCE/rel)!=item['sha256']:
            differences.append('original changed: '+rel)
        protected=(rel.startswith(('검증결과/staff_s0/','검증결과/staff_s1/','검증결과/staff_s2/',
                                   'Part3/','Part1/audit/fixtures/'))
            or 'expected' in Path(rel).name.lower() or Path(rel).name.startswith('test_')
            or rel in {'build/staff_performance_policy.json','build/staff_performance_policy.py',
                       'build/staff_performance_protocol.py','성능규칙_S1_S8.md'})
        if protected:
            protected_count+=1
            if not (ROOT/rel).is_file() or sha(ROOT/rel)!=item['sha256']:
                differences.append('protected copy changed: '+rel)
    result={'stage':'S3','unchanged':not differences,'original_files':len(manifest),
        'protected_copied_files':protected_count,'differences':differences}
    write(OUT/'frozen_guard.json',result)
    assert not differences,differences
    return result

def provenance():
    before={r['path']:r['sha256'] for r in read(OUT/'s2_frozen_manifest.json')}
    changes=[]
    for prefix in ('Part1/program','Part2','Part3','tests'):
        for p in (ROOT/prefix).rglob('*'):
            if p.is_file() and p.suffix in ('.py','.mq5','.mqh'):
                rel=p.relative_to(ROOT).as_posix(); after=sha(p)
                if before.get(rel)!=after:
                    changes.append({'file':rel,'before_sha256':before.get(rel),'after_sha256':after})
    write(OUT/'source_delta.json',{'stage':'S3','changes':changes})
    return changes
