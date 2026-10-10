"""Preserve the one GUI skip and its isolated exact baseline-error reproduction."""
import xml.etree.ElementTree as ET
from staff_s3_evidence import *
from finalize_staff_s1 import cases

old=read(S2/'baseline_signature_compare.json')['groups']['part2']['s2_signature']
full=cases(OUT/'regressions/part2.xml'); isolated=cases(OUT/'gui_signature_isolated.xml')
assert len(isolated)==1
key=next(iter(isolated))
assert isolated[key]==old[key]
assert full[key]['status']=='SKIPPED' and old[key]['status']=='ERROR'
assert isolated[key]['cause']=='PermissionError:SPECIAL directory read as file'
node=next(n for n in ET.parse(OUT/'regressions/part2.xml').getroot().iter('testcase')
    if n.get('name')=='test_data_build_run_never_starts_backtest')
skipped=node.find('skipped')
write(OUT/'gui_signature_diagnosis.json',{'id':key,'baseline':old[key],
    'full_run':full[key],'full_skip_detail':skipped.text,'isolated':isolated[key],
    'first_isolated_attempt':cases(OUT/'gui_signature.xml'),
    'first_attempt_reason':'Global pytest temp folder access denied before GUI fixture; reran same case with new workspace-local basetemp.',
    'code_or_expected_changed':False,'reason':'Isolated case reproduced exact S2 baseline error after full-run Tk initialization skip.'})
reruns=read(OUT/'diagnosed_reruns.json')
assert not any(r['xml']=='gui_signature_isolated.xml' for r in reruns)
reruns.append({'group':'part2','xml':'gui_signature_isolated.xml','ids':[key],'allow_status_change':True,
    'reason':'One full-run Tk skip; isolated reproduction exactly matches S2 baseline PermissionError.'})
write(OUT/'diagnosed_reruns.json',reruns)
print('GUI baseline error reproduced; original skip retained')
