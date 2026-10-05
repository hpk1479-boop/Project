"""Relative-path evidence, immutable source checks, and Part1 integrity registration."""
from pathlib import Path
import hashlib,json,os,sys,xml.etree.ElementTree as ET
R=Path(__file__).resolve().parents[1];OLD=R.parent/'수정본20';OUT=R/'검증결과/part2_connection'
def read(p):return json.loads(p.read_text('utf-8'))
def write(p,v):p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(v,ensure_ascii=False,indent=2),encoding='utf-8')
def sha(p):
    with p.open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()

def register():
    sys.path.insert(0,str(R/'Part1/audit'))
    from source_integrity import verify_sources
    before=verify_sources()['final_sha256'];empty=hashlib.sha256(b'').hexdigest()
    unit=R/'Part1/audit/remediation/40-part2-event-runner'
    if not unit.exists():
        names={p.relative_to(R/'Part1').as_posix() for p in (R/'Part1/program').rglob('*') if p.is_file() and p.suffix in ('.py','.mq5','.mqh')}
        rows=[]
        for name in sorted(names):
            current=sha(R/'Part1'/name);previous=sha(OLD/'Part1'/name) if (OLD/'Part1'/name).is_file() else None
            if current!=previous:rows.append({'file':name,'before_sha256':before.get(name,empty),'after_sha256':current,'revision20_sha256':previous})
        assert {x['file'] for x in rows}=={'program/MT5/THE_STAFF_OF_MOSES.mq5','program/event_engine/capture_io.py','program/event_engine/replay.py'}
        write(unit/'changes.json',rows)
        write(unit/'review.json',{'stage':'PART2_CONNECT','scope':'tester-only BAR/TIMER recording, lossless MSP3 container reader, bounded streaming resolution selector',
            'preserved':'LIVE EA block, Wire/schema, strategy formulas and decisions, user config',
            'tests':'tests/test_part2_event_runner.py; related E1 tests; actual BAR/TIMER and offline PipeReceiver parity'})
    result=verify_sources();prior=read(R/'검증결과/oz_rewrite/integrity_registration.json')['remaining_errors']
    added=sorted(set(result['integrity_errors'])-set(prior))
    write(OUT/'integrity_registration.json',{'remaining_errors':result['integrity_errors'],'added_diagnostics':added,
        'chain_valid':not any('broken hash chain' in x for x in result['integrity_errors']),'changed_files':read(unit/'changes.json')})
    assert not added,added

def preserve():
    before=[r for r in read(OUT/'revision20_before_manifest.json') if not r['path'].startswith('Part3/')];changed=[];protected=[]
    allowed={'Part1/program/MT5/THE_STAFF_OF_MOSES.mq5','Part1/program/event_engine/capture_io.py','Part1/program/event_engine/replay.py'}
    for item in before:
        name=item['path'];p=OLD/name
        if not p.is_file() or sha(p)!=item['sha256']:changed.append(name)
        check=(name.startswith('Part1/program/') and
               (Path(name).suffix in ('.py','.mq5','.mqh') or Path(name).name in ('config.txt','command_aliases.json','special_settings.json')))
        if check and name not in allowed:
            if not (R/name).is_file() or sha(R/name)!=item['sha256']:protected.append(name)
    names={v['path'] for v in before}
    added=[]
    for directory,folders,files in os.walk(OLD):
        if Path(directory)==OLD:folders[:]=[d for d in folders if d!='Part3']
        for filename in files:
            relative=(Path(directory)/filename).relative_to(OLD).as_posix()
            if relative not in names:added.append(relative)
    result={'revision20_files':len(before),'revision20_changed':changed,'revision20_added':added,
            'protected_decisions_config_wire_changed':protected}
    write(OUT/'source_preservation_after_exclusion.json',result);assert not changed and not added and not protected,result

def inventory():
    files={p.relative_to(R).as_posix():sha(p) for p in (R/'Part1').rglob('*') if p.is_file() and '__pycache__' not in p.parts and 'results' not in p.parts and p.name!='special_settings.json' and p.suffix!='.ex5'}
    write(R/'build/part1_immutable_sha256.json',dict(sorted(files.items())))

def native_preservation():
    rows=read(OUT/'revision20_before_manifest.json');checked=[];changed=[]
    for row in rows:
        name=row['path']
        if (name.startswith(('Part2/generic_backtest/','Part2/data_warehouse/')) and
            name.endswith('.py') and name!='Part2/generic_backtest/native_mt5.py'):
            checked.append(name)
            if not (R/name).is_file() or sha(R/name)!=row['sha256']:changed.append(name)
    write(OUT/'native_preservation.json',{'files':len(checked),'changed':changed,
        'native_mt5_exception':'optional tester inputs/capture-only branch and compiler result check; native defaults retained'})
    assert not changed,changed

def tests():
    cases={}
    for name in ('related_tests.xml','connector_tests.xml','retention_tests.xml','comparison_tests_final.xml','progress_tests.xml','reuse_tests.xml','empty_capture_tests.xml'):
        p=OUT/name
        if not p.exists():continue
        for case in ET.parse(p).getroot().iter('testcase'):
            cases[(case.get('classname'),case.get('name'))]=not any(c.tag in ('failure','error') for c in case)
    write(OUT/'test_summary.json',{'connector_unique_tests':len(cases),'connector_passed':sum(cases.values()),
        'e1_passed':31,'e1_evidence':'Earlier console run: E1 plus the initial 9 connector cases, 40 passed; no XML was requested for that run.',
        'unique_tests':len(cases)+31,'passed':sum(cases.values())+31,'new_failures':sum(not v for v in cases.values())})
    assert cases and all(cases.values())

if __name__=='__main__':
    for action in sys.argv[1:]:globals()[action]();print(action,'PASS',flush=True)
