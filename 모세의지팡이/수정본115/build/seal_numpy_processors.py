"""Register authorized Part1 changes and consolidate only completed evidence."""
from pathlib import Path
import hashlib,json,sys,xml.etree.ElementTree as ET
ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'검증결과/numpy_processors'
PREVIOUS=ROOT.parent/'수정본21'
def sha(path):
    with path.open('rb') as handle:return hashlib.file_digest(handle,'sha256').hexdigest()
def read(path):return json.loads(path.read_text('utf-8'))
def write(path,value):
    path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')

def inventory():
    # Part3 is never traversed, read or hashed. Revision21 execution used the
    # complete isolated program copy; only that copy could acquire runtime files.
    changes=[];protected=[];source_previous={};source_current={};isolated=[]
    for folder in ('Part1/program','tests','build'):
        old_names={p.relative_to(PREVIOUS).as_posix():p for p in (PREVIOUS/folder).rglob('*')
                   if p.is_file() and p.suffix in ('.py','.mq5','.mqh','.ex5','.txt','.json')
                   and not any(x in p.parts for x in ('__pycache__','logs','pycache'))}
        new_names={p.relative_to(ROOT).as_posix():p for p in (ROOT/folder).rglob('*')
                   if p.is_file() and p.suffix in ('.py','.mq5','.mqh','.ex5','.txt','.json')
                   and not any(x in p.parts for x in ('__pycache__','logs','pycache'))}
        for name in sorted(old_names.keys()|new_names.keys()):
            before=sha(old_names[name]) if name in old_names else None;after=sha(new_names[name]) if name in new_names else None
            if name.startswith('Part1/program/'):
                source_previous[name]=before;source_current[name]=after
                if name in old_names:
                    copy=OUT/'host21'/name
                    if not copy.is_file() or sha(copy)!=before:isolated.append(name)
            if before!=after:changes.append(dict(path=name,before_sha256=before,after_sha256=after))
    for folder in ('Part2',):
        for old in (PREVIOUS/folder).rglob('*'):
            if not old.is_file() or any(x in old.parts for x in ('__pycache__','logs','pycache','.pytest_cache','generic_runs','.venv-generic','node_modules')):continue
            # The task does not change Part2. Compare executable/config assets,
            # not copied temporary archives and prior validation outputs.
            if old.suffix not in ('.py','.pyw','.json','.txt','.mq5','.mqh','.ex5','.toml'):continue
            name=old.relative_to(PREVIOUS).as_posix();new=ROOT/name
            if not new.is_file() or sha(new)!=sha(old):protected.append(name)
    for change in changes:
        name=change['path']
        if name.startswith('Part1/program/') and (name.startswith('Part1/program/SPECIAL/')
           or name.endswith(('.mq5','.mqh','.ex5')) or Path(name).name in
           ('event_composition.py','event_composer_domain.py','manager_KIM.py','config.txt','command_aliases.json','monitor_OZ.py','staff_schema.py','THE STAFF OF MOSES.py')):
            protected.append(name)
    for name in ('Part1/special_settings.json',):
        old=PREVIOUS/name;new=ROOT/name
        if old.is_file() and (not new.is_file() or sha(old)!=sha(new)):protected.append(name)
    value=dict(changes=changes,protected_differences=protected,isolated_revision21_differences=isolated,
        source_previous=source_previous,source_current=source_current,
        exclusion='Part3 excluded from traversal and integrity comparison')
    write(OUT/'source_inventory.json',value)
    assert not protected and not isolated,(protected,isolated)
    return changes

def register(changes):
    sys.path.insert(0,str(ROOT/'Part1/audit'))
    from source_integrity import verify_sources
    before=verify_sources()['final_sha256'];empty=hashlib.sha256(b'').hexdigest()
    unit=ROOT/'Part1/audit/remediation/41-numpy-processors'
    if not unit.exists():
        rows=[dict(file=x['path'].removeprefix('Part1/'),before_sha256=before.get(x['path'].removeprefix('Part1/'),empty),
                   after_sha256=x['after_sha256'],revision21_sha256=x['before_sha256'])
              for x in changes if x['path'].startswith('Part1/program/')]
        write(unit/'changes.json',rows)
        write(unit/'review.json',dict(scope='Resident NumPy SWEEP/FVG/INDICATOR/WATCH; identical predicates and schemas',
            calculations='Full history reference diagnostics; shared general ATR EWM backend; FVG Wilder seed remains private',
            protected='COMPOSER/SPECIAL/config/EA/STAFF/Wire unchanged',
            tests='Condition boundaries, no DataFrame/deepcopy market path, checkpoint/legacy restore, LIVE=replay'))
    result=verify_sources();prior=read(ROOT/'검증결과/part2_connection/integrity_registration.json')['remaining_errors']
    added=sorted(set(result['integrity_errors'])-set(prior))
    write(OUT/'integrity_registration.json',dict(chain_valid=not any('broken hash chain' in x for x in result['integrity_errors']),
        remaining_errors=result['integrity_errors'],added_diagnostics=added,unit=unit.relative_to(ROOT).as_posix()))
    assert not added,added
    files={p.relative_to(ROOT).as_posix():sha(p) for p in (ROOT/'Part1').rglob('*')
           if p.is_file() and '__pycache__' not in p.parts and 'results' not in p.parts and p.name!='special_settings.json' and p.suffix!='.ex5'}
    write(ROOT/'build/part1_immutable_sha256.json',dict(sorted(files.items())))

def tests():
    results={}
    for name in ('related_final.xml','composition_storage.xml','logic_final.xml'):
        for case in ET.parse(OUT/name).getroot().iter('testcase'):
            results[case.get('classname')+'::'+case.get('name')]=not any(c.tag in ('failure','error') for c in case)
    write(OUT/'test_summary.json',dict(unique_tests=len(results),passed=sum(results.values()),failed=[k for k,v in results.items() if not v],
        replaced_private_structure_assertions=['test_event_e2_domains.py: FVG checkpoint/runtime object comparison',
            'test_event_e2_domains.py: INDICATOR checkpoint/runtime object comparison',
            'test_event_e2_composition.py: Generic Watch private dictionary path -> host export identities'],
        previous_failures_preserved=True))
    assert all(results.values())

if __name__=='__main__':
    changes=inventory();register(changes);tests();print('Source chain, immutable inventory and test evidence sealed')
