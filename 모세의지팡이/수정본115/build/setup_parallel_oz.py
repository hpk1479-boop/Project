from pathlib import Path
import hashlib,json,shutil
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'검증결과/parallel_oz'
OUT.mkdir(exist_ok=True)
def digest_tree(root):
    return {p.relative_to(root).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
            for folder in ('Part1','Part2','tests','build') for p in (root/folder).rglob('*')
            if p.is_file() and '__pycache__' not in p.parts and '.pytest_cache' not in p.parts}
if not (OUT/'source_before.json').exists():
    before=digest_tree(ROOT.parent/'수정본25')
    after=digest_tree(ROOT)
    differences=[k for k,v in before.items() if after.get(k)!=v]
    if differences:raise ValueError(differences)
    (OUT/'source_before.json').write_text(json.dumps(before,ensure_ascii=False,indent=2),encoding='utf-8')
    for folder in ('Part1/program','Part2'):
        shutil.copytree(ROOT/folder,OUT/'before_runtime'/folder,ignore=shutil.ignore_patterns('__pycache__','.pytest_cache'))
    (OUT/'copy_verification.json').write_text(json.dumps({'files':len(before),'mismatches':differences},indent=2),encoding='utf-8')
for name in ('AGENTS.md.txt',):
    path=ROOT/name;raw=path.read_bytes()
    marker='## 병렬·OZ 최적화 최신 지시 — 수정본26'
    if marker.encode() not in raw:
        text='\r\n'+marker+'\r\n- 수정본25 전체 복사본에서만 작업한다. 이전 수정본·사용자 config·EA/schema는 변경하지 않는다.\r\n- A1 연쇄 의존 누락과 A2 결정적 순회를 먼저 수정한다. 이어서 일별 키프레임, 월/2주 동적 작업 분배, OZ 선택 평가, EWM 증분 캐시를 구현한다.\r\n- 판정식·문구·임계값은 유지한다. 모든 입력은 STAFF를 거치며 실제 Telegram/네트워크는 차단한다.\r\n- 사용자 요청의 관련 시험·실제 월/주/연간 비교·측정과 무결성 갱신을 수행하고 수정내역_병렬_OZ최적화.md를 작성한다.\r\n'
        path.write_bytes(raw+text.encode('utf-8'))
print('copy verified and isolated before host ready',flush=True)
