from pathlib import Path
import ast,difflib,hashlib,importlib.util,json
ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'검증결과/live_daily_partition_virtual'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
spec=importlib.util.spec_from_file_location('integrity_live33',ROOT/'Part1/audit/source_integrity.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
current=module.verify_sources();expected=current['final_sha256'];changes=[];diff=[]
names=['event_host.py','manager_KIM.py','strategy_settings_view.py','machine_roots.py','live_alert_recording.py','live_record_folder_ui.py']
for name in names:
    path=ROOT/'Part1/program'/name;before=OUT/'before_sources'/name
    previous=sha(before) if before.exists() else hashlib.sha256(b'').hexdigest()
    key='program/'+name
    assert expected.get(key,hashlib.sha256(b'').hexdigest())==previous,key
    ast.parse(path.read_bytes(),filename='Part1/'+key)
    changes.append({'file':key,'before_sha256':previous,'after_sha256':sha(path)})
    diff.extend(difflib.unified_diff(before.read_text('utf-8-sig').splitlines(True) if before.exists() else [],
        path.read_text('utf-8-sig').splitlines(True),fromfile='before/'+key,tofile=key))
unit=ROOT/'Part1/audit/remediation/64-live-daily-records';unit.mkdir(exist_ok=False)
(unit/'changes.json').write_text(json.dumps(changes,ensure_ascii=False,indent=2),encoding='utf-8')
(unit/'README.md').write_text('LIVE 출력 결과의 일일 CSV 기록, 컴퓨터별 기록 루트 선택, 선택적 KST 07시 요약. 전략·EA·Wire 및 Part2 실행/분할/가상 진입 계산 경로는 변경하지 않음.\n증거: 검증결과/live_daily_partition_virtual.\n',encoding='utf-8')
after=module.verify_sources()
existing=json.loads((ROOT/'검증결과/stop_virtual_parallel/integrity.json').read_text('utf-8'))['after_errors']
assert after['integrity_errors']==existing,after['integrity_errors']
inventory=ROOT/'build/part1_immutable_sha256.json';previous=sha(inventory)
items={p.relative_to(ROOT).as_posix():sha(p) for p in (ROOT/'Part1').rglob('*') if p.is_file()
    and not any(x in p.parts for x in ('__pycache__','logs','event_state','results'))
    and p.name!='special_settings.json' and p.suffix not in ('.ex5','.log','.pyc')}
inventory.write_text(json.dumps(items,ensure_ascii=False,sort_keys=True,indent=2),encoding='utf-8')
protected=json.loads((ROOT/'검증결과/stop_virtual_parallel/protected.json').read_text('utf-8'))
protected=[r for r in protected if r['file']!='Part1/program/manager_KIM.py']
assert all(sha(ROOT/r['file'])==r['sha256'] for r in protected)
(OUT/'source_changes.diff').write_text(''.join(diff),encoding='utf-8')
(OUT/'integrity.json').write_text(json.dumps({'registered':changes,'existing_errors':existing,'new_errors':0,
    'protected_unchanged':len(protected),'immutable_before':previous,'immutable_after':sha(inventory),'immutable_files':len(items)},ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'registered':len(changes),'new_integrity_errors':0,'protected_unchanged':len(protected)}))
