"""Refresh the Part1 inventory after the reviewed module-status UI edit."""
from pathlib import Path
import hashlib,json,sys

root=Path(__file__).resolve().parents[1]
def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()

sys.path.insert(0,str(root/'Part1/audit'))
from source_integrity import verify_sources

units=(root/'Part1/audit/remediation/51-module-status-navigation/changes.json',
       root/'Part1/audit/remediation/52-system-control-dedup/changes.json',
       root/'Part1/audit/remediation/53-module-display-names/changes.json',
       root/'Part1/audit/remediation/54-direct-module-logs/changes.json')
changes=json.loads(units[-1].read_text('utf-8'))
for item in changes:
    assert digest(root/'Part1'/item['file'])==item['after_sha256'],item['file']
result=verify_sources()
previous=json.loads((root/'검증결과/virtual_entry/integrity.json').read_text('utf-8'))['remaining_errors']
assert sorted(result['integrity_errors'])==sorted(previous),result['integrity_errors']

inventory={}
for path in (root/'Part1').rglob('*'):
    if not path.is_file() or set(path.parts)&{'logs','__pycache__','results'}:continue
    if path.suffix=='.ex5' or path.name=='special_settings.json':continue
    inventory[path.relative_to(root).as_posix()]=digest(path)
target=root/'build/part1_immutable_sha256.json'
target.write_text(json.dumps(inventory,ensure_ascii=False,indent=2),encoding='utf-8')

evidence=root/'검증결과/module_status_navigation/integrity.json'
evidence.write_text(json.dumps({'units':[unit.parent.name for unit in units],
                                'changed_files':[x['file'] for x in changes],
                                'new_errors':[],'existing_errors':previous,'inventory_files':len(inventory)},
                               ensure_ascii=False,indent=2),encoding='utf-8')
print('registered:',len(changes),'new errors:',0,'inventory:',len(inventory))
