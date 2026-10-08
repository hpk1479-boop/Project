"""Register only S2 STAFF delta and regenerate the current source inventory."""
import sys
from staff_s2_evidence import ROOT, OUT, S1, read, write, sha, provenance, frozen_guard


def main():
    frozen_guard()
    deltas = provenance()
    change = next(r for r in deltas if r['file']=='Part1/program/THE STAFF OF MOSES.py')
    unit = ROOT/'Part1/audit/remediation/23-staff-s2-storage'
    unit.mkdir(exist_ok=False)
    write(unit/'changes.json',[{**change,'file':'program/THE STAFF OF MOSES.py'}])
    write(unit/'review.json',{'stage':'S2','authorization':'User requested S2 Snapshot storage and public injection APIs',
        'baseline_manifest_sha256':sha(OUT/'s1_frozen_manifest.json'),
        'original_manifest_modified':False,'golden_expected_modified':False})
    sys.path.insert(0,str(ROOT/'Part1/audit'))
    from source_integrity import verify_sources
    result = verify_sources()
    old = read(S1/'integrity_registration.json')['remaining_errors']
    report = {'units':[unit.name], 'chain_valid':not any('broken hash chain' in x for x in result['integrity_errors']),
              'remaining_errors':result['integrity_errors'],
              'added_diagnostics':sorted(set(result['integrity_errors'])-set(old)),
              'removed_diagnostics':sorted(set(old)-set(result['integrity_errors']))}
    write(OUT/'integrity_registration.json',report)
    assert report['chain_valid'] and not report['added_diagnostics'] and not report['removed_diagnostics']
    inventory_path = ROOT/'build/part1_immutable_sha256.json'
    old_hash = sha(inventory_path)
    inventory = {p.relative_to(ROOT).as_posix():sha(p) for p in (ROOT/'Part1').rglob('*')
        if p.is_file() and '__pycache__' not in p.parts and 'results' not in p.parts
        and p.name!='special_settings.json' and p.suffix!='.ex5'}
    write(inventory_path,dict(sorted(inventory.items())))
    write(OUT/'immutable_regeneration.json',{'files':len(inventory),'before_sha256':old_hash,
        'after_sha256':sha(inventory_path),'scope':'current S2 source inventory only; no expected/golden update'})
    print('S2 registered; inherited integrity diagnostics:',len(report['remaining_errors']))


if __name__=='__main__': main()
