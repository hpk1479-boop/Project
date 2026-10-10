"""Record this revision's authorized Part1 delta, without rewriting old units."""
from pathlib import Path
import ast,hashlib,importlib.util,json
ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'검증결과/shared_oz_composer_input'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
spec=importlib.util.spec_from_file_location('integrity',ROOT/'Part1/audit/source_integrity.py')
integrity=importlib.util.module_from_spec(spec);spec.loader.exec_module(integrity)
before=json.loads((OUT/'before_sources.json').read_text('utf-8'))
unit=ROOT/'Part1/audit/remediation/62-shared-oz-composer-input'
if unit.exists():raise FileExistsError('review source changes before regenerating this unit')
expected=integrity.verify_sources()['final_sha256'];empty=hashlib.sha256(b'').hexdigest();changes=[]
for path in sorted((ROOT/'Part1/program').rglob('*.py')):
    if '__pycache__' in path.parts:continue
    name=path.relative_to(ROOT).as_posix();current=sha(path)
    if before.get(name)==current:continue
    relative=path.relative_to(ROOT/'Part1').as_posix()
    previous=before.get(name,empty)
    if expected.get(relative,empty)!=previous:raise ValueError('pre-existing chain discrepancy: '+relative)
    ast.parse(path.read_bytes(),filename=name)
    changes.append({'file':relative,'before_sha256':previous,'after_sha256':current})
unit.mkdir(parents=True)
(unit/'changes.json').write_text(json.dumps(changes,ensure_ascii=False,indent=2),encoding='utf-8')
(unit/'README.md').write_text('공유 OZ 전략별 독립 판정, 김비서 출력 정책, 사실 의존 대응표, STAFF 불변 바이트 검사 재사용.\n검증 증거: 프로젝트 루트 `검증결과/shared_oz_composer_input`.\nEA·Wire 스키마·녹화 형식·SPECIAL 판정식 변경 없음.\n',encoding='utf-8')
result=integrity.verify_sources()
(OUT/'integrity_verification.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
inventory=ROOT/'build/part1_immutable_sha256.json';old_hash=sha(inventory)
items={p.relative_to(ROOT).as_posix():sha(p) for p in (ROOT/'Part1').rglob('*')
       if p.is_file() and not any(x in p.parts for x in ('__pycache__','logs','event_state','results'))
       and p.name!='special_settings.json' and p.suffix not in ('.ex5','.log','.pyc')}
inventory.write_text(json.dumps(items,ensure_ascii=False,sort_keys=True,indent=2),encoding='utf-8')
(OUT/'immutable_regeneration.json').write_text(json.dumps({'file':'build/part1_immutable_sha256.json',
    'before_sha256':old_hash,'after_sha256':sha(inventory),'files':len(items)},indent=2),encoding='utf-8')
protected=[]
for p in (ROOT/'Part1/program').rglob('*'):
    if p.is_file() and (p.suffix in ('.mq5','.mqh','.ex5') or p.name in ('staff_schema.py','config.txt')):
        other=ROOT.parent/'수정본31'/p.relative_to(ROOT)
        protected.append({'file':p.relative_to(ROOT).as_posix(),'unchanged':other.is_file() and p.read_bytes()==other.read_bytes()})
(OUT/'protected_files.json').write_text(json.dumps(protected,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'changed_files':len(changes),'integrity_errors':result['integrity_errors'],
                  'protected_files':len(protected),'protected_unchanged':all(v['unchanged'] for v in protected)},ensure_ascii=False))
