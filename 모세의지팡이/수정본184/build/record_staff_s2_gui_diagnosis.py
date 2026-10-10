"""Record the one Tcl skip and its isolated reproduction of the existing error."""
import xml.etree.ElementTree as ET
from staff_s2_evidence import OUT, S1, read, write
from finalize_staff_s1 import cases

key='test_ui_dates::test_calendar_invalid_entry_falls_back_to_kst_month[]'
before=read(S1/'baseline_signature_compare.json')['groups']['part2']['s1_signature'][key]
isolated=cases(OUT/'gui_signature.xml')[key]
assert before==isolated
node=next(n for n in ET.parse(OUT/'regressions/part2.xml').getroot().iter('testcase')
          if n.get('name')==key.split('::')[1])
assert node.find('skipped') is not None
write(OUT/'gui_signature_diagnosis.json',{'id':key,'initial':'SKIPPED',
    'initial_reason':node.find('skipped').get('message'), 's1':before,'s2_isolated':isolated,
    'conclusion':'Transient Tcl initialization failure; isolated case reproduces the unchanged SPECIAL directory error.',
    'code_or_expected_modified':False})
entries=read(OUT/'diagnosed_reruns.json')
assert not any(e['xml']=='gui_signature.xml' for e in entries)
entries.append({'group':'part2','xml':'gui_signature.xml','ids':[key],
                'reason':'One Tcl initialization skip; isolated reproduction matches S1 error exactly'})
write(OUT/'diagnosed_reruns.json',entries)
