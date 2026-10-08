"""Record reversible, ordered remediation units without altering initial evidence."""
import difflib
import hashlib
import json
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'audit' / 'remediation'

def files():
    return sorted(p for base in ('program', 'audit') for p in (ROOT/base).rglob('*')
                  if p.is_file() and p.suffix in {'.py', '.mq5', '.json'}
                  and 'remediation' not in p.relative_to(ROOT).parts
                  and 'results' not in p.relative_to(ROOT).parts
                  and 'fixtures' not in p.relative_to(ROOT).parts)

def sha(data): return hashlib.sha256(data).hexdigest()

mode, unit = sys.argv[1:3]
path = OUT/unit
path.mkdir(parents=True, exist_ok=True)
if mode == 'begin':
    target = path/'before.zip'
    if target.exists(): raise SystemExit('Unit already started; refusing to overwrite evidence')
    with zipfile.ZipFile(target, 'w', zipfile.ZIP_DEFLATED) as archive:
        for f in files(): archive.write(f, f.relative_to(ROOT).as_posix())
elif mode == 'finish':
    changes, patches = [], []
    with zipfile.ZipFile(path/'before.zip') as archive:
        names = set(archive.namelist()) | {f.relative_to(ROOT).as_posix() for f in files()}
        for name in sorted(names):
            before = archive.read(name) if name in archive.namelist() else b''
            after = (ROOT/name).read_bytes() if (ROOT/name).exists() else b''
            if before == after: continue
            changes.append({'file':name,'before_sha256':sha(before),'after_sha256':sha(after)})
            patches.extend(difflib.unified_diff(before.decode('utf-8-sig').splitlines(True),
                after.decode('utf-8-sig').splitlines(True), fromfile='a/'+name, tofile='b/'+name))
    (path/'changes.json').write_text(json.dumps(changes,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    (path/'changes.patch').write_text(''.join(patches),encoding='utf-8')
    print(json.dumps(changes,ensure_ascii=False))
else: raise SystemExit('begin or finish required')
