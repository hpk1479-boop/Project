"""Reviewed source delta and portable evidence manifest; Part3 is not traversed."""
from pathlib import Path
import argparse,hashlib,json,sys,os,xml.etree.ElementTree as ET
ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'검증결과/schema_cleanup'
def sha(p):
    with p.open('rb') as h:return hashlib.file_digest(h,'sha256').hexdigest()
def write(p,v):p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(v,ensure_ascii=False,indent=2),encoding='utf-8')
def read(p):return json.loads(p.read_text('utf-8'))
def files(root,folder):
    out={}
    for base,dirs,names in os.walk(root/folder):
        dirs[:]=[d for d in dirs if d not in ('__pycache__','logs','pycache','.pytest_cache','.venv-generic','generic_runs','node_modules')]
        for n in names:
            p=Path(base)/n
            if p.suffix in ('.py','.pyw','.mq5','.mqh','.ex5','.txt','.json','.toml'):out[p.relative_to(root).as_posix()]=sha(p)
    return out
def main():
    parser=argparse.ArgumentParser();parser.add_argument('--previous',type=Path,required=True);args=parser.parse_args();previous=args.previous.resolve()
    assert previous!=ROOT and (previous/'Part1/program').is_dir()
    before={};after={}
    for folder in ('Part1/program','Part2/event_backtest','Part2/part1_host','Part2/generic_backtest','tests','build'):
        before.update(files(previous,folder));after.update(files(ROOT,folder))
    changes=[{'path':n,'before_sha256':before.get(n),'after_sha256':after.get(n)} for n in sorted(before.keys()|after.keys()) if before.get(n)!=after.get(n)]
    protected=[]
    for name in before:
        if name.startswith('Part1/program/SPECIAL/') or Path(name).name in ('config.txt','command_aliases.json','monitor_OZ.py','manager_KIM.py','event_composition.py','event_composer_domain.py'):
            if before[name]!=after.get(name):protected.append(name)
    assert not protected,protected
    sys.path.insert(0,str(ROOT/'Part1/audit'));from source_integrity import verify_sources
    recorded=verify_sources()['final_sha256'];empty=hashlib.sha256(b'').hexdigest();unit=ROOT/'Part1/audit/remediation/42-indicator-schema'
    assert not unit.exists(),'seal once after implementation and evidence completion'
    rows=[{'file':x['path'].removeprefix('Part1/'),'before_sha256':recorded.get(x['path'].removeprefix('Part1/'),empty),
           'after_sha256':x['after_sha256'],'revision22_sha256':x['before_sha256']} for x in changes if x['path'].startswith('Part1/program/')]
    write(unit/'changes.json',rows)
    write(unit/'review.json',{'scope':'Final 50-column schema; fixed MT5 3-sigma Wonbi + config-only shared scaling; EMA20; native OUT/slope; explicit recursive seeds',
        'tests':'Intended condition boundaries, old schema refusal, LIVE receive=replay, MT5 repeated captures, BAR/TIMER input and alerts',
        'protected':'Original revisions, user config, SPECIAL/COMPOSER decisions, monitor_OZ mixed line endings, Part3 untouched'})
    result=verify_sources();prior=read(ROOT/'검증결과/numpy_processors/integrity_registration.json')['remaining_errors']
    added=sorted(set(result['integrity_errors'])-set(prior));assert not added,added
    write(OUT/'integrity_registration.json',{'unit':unit.relative_to(ROOT).as_posix(),'chain_valid':not any('broken hash chain' in x for x in result['integrity_errors']),
        'remaining_errors':result['integrity_errors'],'new_errors':added})
    immutable={p.relative_to(ROOT).as_posix():sha(p) for p in (ROOT/'Part1').rglob('*') if p.is_file() and '__pycache__' not in p.parts and 'results' not in p.parts and p.name!='special_settings.json' and p.suffix!='.ex5'}
    write(ROOT/'build/part1_immutable_sha256.json',dict(sorted(immutable.items())))
    after['build/part1_immutable_sha256.json']=sha(ROOT/'build/part1_immutable_sha256.json')
    changes=[{'path':n,'before_sha256':before.get(n),'after_sha256':after.get(n)} for n in sorted(before.keys()|after.keys()) if before.get(n)!=after.get(n)]
    write(OUT/'source_inventory.json',{'changes':changes,'protected_differences':protected,'previous_revision_read_only':True,
        'monitor_OZ_bytes_preserved':before['Part1/program/monitor_OZ.py']==after['Part1/program/monitor_OZ.py'],
        'config_bytes_preserved':before['Part1/program/config.txt']==after['Part1/program/config.txt'],
        'part3_excluded':True})
    tests={}
    for name in ('logic_final.xml','native_fixture_fix.xml','schema_logic_final.xml','config_sigma_final.xml'):
        for case in ET.parse(OUT/name).getroot().iter('testcase'):
            tests[case.get('classname')+'::'+case.get('name')]=not any(c.tag in ('failure','error') for c in case)
    assert all(tests.values()),[k for k,v in tests.items() if not v]
    write(OUT/'test_summary.json',{'unique_tests':len(tests),'passed':sum(tests.values()),'failed':[],
        'reruns':'Only failure-cause corrections and added logic tests; old failed evidence preserved',
        'fixture_changes':'Replace fixed 48-column fixtures with registry columns; supply native OUT/slope. Original predicate assertions retained.'})
    print('Sealed',len(rows),'Part1 changes;',len(tests),'unique passing logic tests')
if __name__=='__main__':main()
