"""Use the entire frozen host for old BEFORE trees; current host uses public APIs."""
import functools
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
from part1_host import runtime as current

ROOT=Path(__file__).resolve().parents[2]

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

@functools.lru_cache(maxsize=None)
def verified_before(path):
    program=Path(path)/'program'
    actual={p.relative_to(program).as_posix():sha(p) for p in program.rglob('*') if p.is_file()}
    manifests=[ROOT/'검증결과/staff_s0/source6_manifest.json',ROOT/'검증결과/staff_s2/s1_frozen_manifest.json']
    for manifest in manifests:
        expected={r['path'].removeprefix('Part1/program/'):r['sha256']
                  for r in json.loads(manifest.read_text('utf-8-sig'))
                  if r['path'].startswith('Part1/program/')}
        if actual==expected:
            return
    raise AssertionError('BEFORE must be a complete hash-frozen Part1/program tree')

@functools.lru_cache(maxsize=1)
def frozen_host():
    path=ROOT/'검증결과/staff_s0/baseline_input/Part2/part1_host/runtime.py'
    manifest=json.loads((ROOT/'검증결과/staff_s0/source6_manifest.json').read_text('utf-8-sig'))
    expected=next(r['sha256'] for r in manifest if r['path']=='Part2/part1_host/runtime.py')
    assert sha(path)==expected
    spec=importlib.util.spec_from_file_location('staff_golden._frozen_original_host',path)
    module=importlib.util.module_from_spec(spec)
    sys.modules[spec.name]=module
    spec.loader.exec_module(module)
    return module.Part1Runtime

def Part1Runtime(*args,**kwargs):
    part1=Path(kwargs.get('part1_root',current.DEFAULT_PART1_ROOT)).resolve()
    if part1==current.DEFAULT_PART1_ROOT.resolve():
        return current.Part1Runtime(*args,**kwargs)
    verified_before(str(part1))
    return frozen_host()(*args,**kwargs)
