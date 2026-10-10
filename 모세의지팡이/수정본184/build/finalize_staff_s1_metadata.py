"""Append evidence for the user's final test/protocol changes; preserve earlier records."""
import ast
import json
import shutil
import sys
from staff_s1_evidence import ROOT, OUT, sha, provenance, verify_authorized_test_adapter


def write_new(path,value):
    with path.open('x',encoding='utf-8') as stream:
        json.dump(value,stream,ensure_ascii=False,indent=2)


if __name__=='__main__':
    test=ROOT/'Part2/validation_suite/test_oz_fvg_optimization.py'
    original=ROOT.parent/'수정본7'/test.relative_to(ROOT)
    verify_authorized_test_adapter(original,test)
    old,new=ast.parse(original.read_bytes()),ast.parse(test.read_bytes())
    functions=lambda tree:{n.name:n for n in tree.body if isinstance(n,ast.FunctionDef)}
    a,b=functions(old),functions(new)
    names=['test_before_and_after_identical','test_live_and_backtest_identical_after_optimization','test_scenario_is_not_vacuous']
    assert all(ast.dump(a[n])==ast.dump(b[n]) for n in names)
    fields=next(ast.literal_eval(n.value) for n in new.body if isinstance(n,ast.Assign) and
                any(isinstance(t,ast.Name) and t.id=='FIELDS' for t in n.targets))
    assert len(fields)==6
    write_new(OUT/'oz_14_contract_final.json',{
        'case_count':14,'before_after':6,'live_backtest':6,'scenario':1,'performance':1,
        'fields':fields,'behavior_13_AST_identical':True,'reuse_assertions_retained':True,
        'timing_assertion_changed_by_user_request':True,
        'old_rule':'one wall sample, after < before',
        'new_rule':'S5/S8: five CPU pairs under frozen limits; S1-S4/S6-S7: one reference timing, no speed judgment',
        'before_scope':'entire frozen revision6 Part1/program, SHA256 validated',
        'initial_13_of_14_result_preserved':'oz_full_baseline.xml',
        'scenario_sha256':sha(test)})
    for path in (OUT/'source_delta.json', ROOT/'build/part1_immutable_sha256.json', OUT/'immutable_regeneration.json'):
        dest=OUT/(path.stem+'_before_performance_rule'+path.suffix)
        if dest.exists(): raise FileExistsError(dest)
        shutil.copyfile(path,dest)
    provenance()
    path=ROOT/'build/part1_immutable_sha256.json'
    before=sha(path)
    inventory={p.relative_to(ROOT).as_posix():sha(p) for p in (ROOT/'Part1').rglob('*')
               if p.is_file() and '__pycache__' not in p.parts and 'results' not in p.parts
               and p.name!='special_settings.json' and p.suffix!='.ex5'}
    path.write_text(json.dumps(inventory,ensure_ascii=False,sort_keys=True,indent=2),encoding='utf-8')
    write_new(OUT/'immutable_regeneration_final.json',{'before_sha256':before,'after_sha256':sha(path),
        'files':len(inventory),'reason':'Authorized performance test change recorded in Part1 audit provenance'})
    sys.path.insert(0,str(ROOT/'Part1/audit'))
    from source_integrity import verify_sources
    result=verify_sources()
    registered=json.loads((OUT/'integrity_registration.json').read_text('utf-8'))
    assert result['integrity_errors']==registered['remaining_errors']
    write_new(OUT/'integrity_final.json',{'chain_valid':True,'remaining_errors':result['integrity_errors'],
        'new_diagnostics':[]})
    files=['build/staff_performance_protocol.py','build/run_staff_performance_stage.py',
           'build/staff_performance_policy.py','build/staff_performance_policy.json',
           'build/test_staff_performance_protocol.py',str(test.relative_to(ROOT).as_posix()),
           '검증결과/staff_s1/performance_protocol/locked_rule.json']
    write_new(OUT/'performance_protocol_manifest.json',{name:sha(ROOT/name) for name in files})
    print('14 checks preserved; user-authorized CPU rule and updated provenance registered')
