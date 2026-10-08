"""Record the discovered private storage coupling; rerun only affected cases."""
import ast
import os
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from staff_s2_evidence import ROOT, OUT, S1, read, write, sha, provenance
from finalize_staff_s1 import cases


def methods(path):
    result = {}
    for node in ast.parse(path.read_bytes()).body:
        if isinstance(node,ast.FunctionDef): result[node.name]=ast.dump(node)
        elif isinstance(node,ast.ClassDef):
            for method in node.body:
                if isinstance(method,ast.FunctionDef): result[node.name+'.'+method.name]=ast.dump(method)
    return result


def main():
    before=methods(OUT/'pre_private_storage_fix_staff.py')
    after=methods(ROOT/'Part1/program/THE STAFF OF MOSES.py')
    changed=sorted(k for k in before.keys()|after.keys() if before.get(k)!=after.get(k))
    assert changed==['StaffPipeCache.export_state']
    old=read(S1/'baseline_signature_compare.json')['groups']['root']['s1_signature']
    current=cases(OUT/'regressions/root.xml')
    new_failures={k:v for k,v in current.items() if v['status'] in ('FAILED','ERROR')
                  and old.get(k,{}).get('status') not in ('FAILED','ERROR')}
    failures={node.get('classname','').split('.')[-1]+'::'+node.get('name',''):node
              for node in ET.parse(OUT/'regressions/root.xml').getroot().iter('testcase')}
    for key in new_failures:
        node=failures[key]
        failure=node.find('failure')
        assert failure is not None and "'NativeCache' object has no attribute '_cache'" in (failure.text or ''),key
    selected=[]
    for line in (OUT/'g1_root.log').read_text('utf-8').splitlines():
        if not line.startswith('FAILED '): continue
        nodeid=line[7:].split(' - ',1)[0]
        path,name=nodeid.split('::',1)
        key=Path(path).stem+'::'+name
        if key in new_failures: selected.append(nodeid)
    assert len(selected)==len(new_failures)
    write(OUT/'private_storage_diagnosis.json',{'new_failures':new_failures,'selected':selected,
        'cause':'allzone_events and event_catalog used getattr with CACHE_FIELDS, bypassing the public cache boundary',
        'fix':'public export_state/restore_state; metadata-only view avoids serializing arrays in future-state checks',
        'STAFF_changed_methods_after_G2_G3':changed,
        'parser_legacy_handler_calculation_methods_unchanged':True,
        'G2_G3_and_STAFF_performance_repeated':False})
    # The S2 unit is still under development. Preserve its first registration,
    # then update only its final after-hash; the frozen S1 anchor never changes.
    unit=ROOT/'Part1/audit/remediation/23-staff-s2-storage/changes.json'
    write(OUT/'integrity_before_storage_fix.json',read(unit))
    changes=read(unit);changes[0]['after_sha256']=sha(ROOT/'Part1/program/THE STAFF OF MOSES.py')
    write(unit,changes)
    sys.path.insert(0,str(ROOT/'Part1/audit'))
    from source_integrity import verify_sources
    integrity=verify_sources()
    assert integrity['integrity_errors']==read(OUT/'integrity_registration.json')['remaining_errors']
    write(OUT/'integrity_after_storage_fix.json',integrity)
    manifest=ROOT/'build/part1_immutable_sha256.json'
    old_hash=sha(manifest)
    inventory={p.relative_to(ROOT).as_posix():sha(p) for p in (ROOT/'Part1').rglob('*')
        if p.is_file() and '__pycache__' not in p.parts and 'results' not in p.parts
        and p.name!='special_settings.json' and p.suffix!='.ex5'}
    write(manifest,dict(sorted(inventory.items())))
    write(OUT/'immutable_after_storage_fix.json',{'before_sha256':old_hash,'after_sha256':sha(manifest),
                                               'reason':'diagnosed public storage boundary fix','files':len(inventory)})
    provenance()
    checks=['Part2/validation_suite/test_staff_s2.py::test_continuation_public_api_preserves_epoch_seq_and_readonly_arrays',
            'Part2/validation_suite/test_staff_s2.py::test_part2_staff_transport_uses_public_boundary_only']
    reruns=[{'group':'root','xml':'storage_fix_root.xml','ids':sorted(new_failures),'reason':'53 private cache lookup failures'},
            {'group':'part2','xml':'storage_fix_api.xml','ids':['test_staff_s2::'+x.split('::')[1] for x in checks],
             'reason':'metadata-only public API and string-based private coupling regression checks'}]
    write(OUT/'diagnosed_reruns.json',reruns)
    env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1',PYTHONUTF8='1',STAFF_STAGE='S2')
    for index,ids in enumerate((selected,checks)):
        entry=reruns[index]
        args=[sys.executable,'-X','utf8','-B','-m','pytest',*ids,'-q','--tb=short','-p','no:cacheprovider',
              '--basetemp='+str(OUT/('storage_fix_tmp_'+entry['group'])),'--junitxml='+str(OUT/entry['xml'])]
        print('START storage fix',entry['group'],len(ids),flush=True)
        with (OUT/('storage_fix_'+entry['group']+'.log')).open('x',encoding='utf-8') as log:
            done=subprocess.run(args,cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT)
        print('END storage fix',entry['group'],'rc='+str(done.returncode),flush=True)
        if done.returncode: return done.returncode
    return 0


if __name__=='__main__':
    from pathlib import Path
    raise SystemExit(main())
