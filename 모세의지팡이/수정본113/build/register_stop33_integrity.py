from pathlib import Path
import hashlib,importlib.util,json,ast
ROOT=Path(__file__).resolve().parents[1];BEFORE=ROOT.parent/'수정본32'
OUT=ROOT/'검증결과/stop_virtual_parallel'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
def load(root,name):
    spec=importlib.util.spec_from_file_location(name,root/'Part1/audit/source_integrity.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module
old=load(BEFORE,'integrity_before33').verify_sources()
module=load(ROOT,'integrity_after33');expected=old['final_sha256'];changes=[]
for p in sorted((ROOT/'Part1/program').rglob('*.py')):
    if '__pycache__' in p.parts:continue
    rel=p.relative_to(ROOT);before=BEFORE/rel;previous=sha(before) if before.exists() else hashlib.sha256(b'').hexdigest()
    if previous==sha(p):continue
    short=p.relative_to(ROOT/'Part1').as_posix()
    if expected.get(short,hashlib.sha256(b'').hexdigest())!=previous:raise ValueError('existing source chain mismatch: '+short)
    ast.parse(p.read_bytes());changes.append({'file':short,'before_sha256':previous,'after_sha256':sha(p)})
unit=ROOT/'Part1/audit/remediation/63-stop-virtual-watch'
unit.mkdir(exist_ok=False)
(unit/'changes.json').write_text(json.dumps(changes,ensure_ascii=False,indent=2),encoding='utf-8')
(unit/'README.md').write_text('WATCH 명령 등록 결과에 따른 재생 의존성 축소, 텔레그램 토큰 미설정 단일 진단 및 전송 시도 방지.\n전략 판정·EA·Wire 변경 없음. 증거: 검증결과/stop_virtual_parallel.\n',encoding='utf-8')
new=module.verify_sources()
result={'before_errors':old['integrity_errors'],'after_errors':new['integrity_errors'],
        'same_existing_errors':old['integrity_errors']==new['integrity_errors'],'registered':changes}
(OUT/'integrity.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
inventory=ROOT/'build/part1_immutable_sha256.json';previous=sha(inventory)
items={p.relative_to(ROOT).as_posix():sha(p) for p in (ROOT/'Part1').rglob('*')
    if p.is_file() and not any(x in p.parts for x in ('__pycache__','logs','event_state','results'))
    and p.name!='special_settings.json' and p.suffix not in ('.ex5','.log','.pyc')}
inventory.write_text(json.dumps(items,ensure_ascii=False,sort_keys=True,indent=2),encoding='utf-8')
(OUT/'immutable.json').write_text(json.dumps({'before_sha256':previous,'after_sha256':sha(inventory),'files':len(items)},indent=2),encoding='utf-8')
protected=[]
for p in (ROOT/'Part1/program').rglob('*'):
    if p.is_file() and (p.suffix in ('.mq5','.mqh','.ex5') or p.name in ('staff_schema.py','config.txt') or p.parent.name=='SPECIAL' or p.name in ('event_composer_domain.py','monitor_OZ.py','manager_KIM.py')):
        rel=p.relative_to(ROOT);old_file=BEFORE/rel
        protected.append({'file':rel.as_posix(),'sha256':sha(p),'unchanged':old_file.exists() and sha(old_file)==sha(p)})
(OUT/'protected.json').write_text(json.dumps(protected,ensure_ascii=False,indent=2),encoding='utf-8')
assert result['same_existing_errors'] and all(p['unchanged'] for p in protected)
print(json.dumps({'registered':len(changes),'protected':len(protected),'new_integrity_failures':0},ensure_ascii=False))
