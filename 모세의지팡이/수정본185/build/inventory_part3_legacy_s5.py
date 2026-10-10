"""Read-only Part1/Part2 reference inventory; Part3 remains legacy and untouched."""
import re
from staff_s5_evidence import ROOT, OUT, S1, read, write, sha

TEXT = {'.py','.md','.txt','.json','.toml','.bat','.ps1','.ini','.cfg','.yaml','.yml','.mq5','.mqh'}
EXCLUDED = {'__pycache__','.pytest_cache','generic_runs','results','logs','.git','node_modules'}


def main():
    expected={r['path']:r['sha256'] for r in read(OUT/'s4_frozen_manifest.json') if r['path'].startswith('Part3/')}
    actual={p.relative_to(ROOT).as_posix():sha(p) for p in (ROOT/'Part3').rglob('*') if p.is_file()}
    assert expected==actual, 'Legacy Part3 must remain completely unchanged'
    references = []
    for part in ('Part1','Part2'):
        for path in sorted((ROOT/part).rglob('*')):
            if not path.is_file() or path.suffix.lower() not in TEXT or set(path.parts)&EXCLUDED:
                continue
            if any(p.startswith(('.venv','tmp_')) for p in path.relative_to(ROOT).parts):
                continue
            try: lines = path.read_text('utf-8-sig').splitlines()
            except (UnicodeError,OSError): continue
            for number,line in enumerate(lines,1):
                if re.search('part3',line,re.I):
                    relative = path.relative_to(ROOT).as_posix()
                    category = ('historical provenance' if '/audit/remediation/' in relative else
                                'test' if path.name.startswith('test_') else 'source/config/document')
                    references.append({'file':relative,'line':number,'category':category,'text':line.strip()})
    signature = read(S1/'baseline_signature_compare.json')['groups']
    known = {group:{k:v for k,v in data['s1_signature'].items() if 'part3' in k.lower()}
             for group,data in signature.items()}
    failures = {group:{k:v for k,v in values.items() if v['status'] in ('FAILED','ERROR')}
                for group,values in known.items()}
    report = {'policy':'Part3 is legacy. No synchronization or edits. Reference cleanup deferred until after S8.',
              'gate_policy':'Existing Part3 failures remain baseline defects; do not silently excuse newly introduced Part1/Part2 failures.',
              'scope':['Part1','Part2'],'method':'case-insensitive literal Part3 search in source/config/document text',
              'excluded':'generated runs, runtime logs/results, caches, virtual environments and binary files',
              'references':references,'explicit_Part3_baseline_tests':known,
              'explicit_Part3_existing_failures':failures,'Part3_modified':False,
              'Part3_all_files_hash_identical':True,'Part3_files_checked':len(expected)}
    write(OUT/'part3_legacy_references.json',report)
    rows = '\n'.join(f"| `{r['file']}:{r['line']}` | {r['category']} | `{r['text'].replace('|',' / ')}` |" for r in references)
    (ROOT/'Part3_레거시_참조목록.md').write_text('''# Part3 레거시 고정 및 참조 목록

사용자 후속 지시: Part3는 레거시다. S2 및 이후 단계에서 Part3를 동기화하거나 수정하지 않는다.
Part1/Part2에 있는 참조는 기록만 하며 정리는 S8 종료 이후의 별도 작업이다.
Part3 관련 **기존** 실패는 기준선 결함으로 구분한다. 새 Part1/Part2 회귀를 Part3 문제로 간주해 통과시키지 않는다.
골든/expected/동결 성능 정책은 변경하지 않는다.

아래는 Part1/Part2의 소스·설정·문서에 대한 대소문자 무관 `Part3` 직접 참조 검색 결과다.
생성 실행 폴더·로그·검증 결과·캐시·가상환경·바이너리는 제외했다. 동적으로 만들어지는 경로까지 증명하는 실행 추적은 아니다.
현재 직접 참조는 S1 검사와 과거 변경 기록에 있으며, 실행 코드의 직접 호출/import는 검색되지 않았다.

| 위치 | 구분 | 참조 |
|---|---|---|
'''+rows+'''

S1 signature의 명시적 Part3 매개변수 검사 상태도 JSON에 보존했다. 현재 해당 검사는 PASSED이며,
명시적 Part3 ID의 기존 FAILED/ERROR 항목은 없다. Part3 관련 기존 검증 항목과 Part3 파일을 유지했다.

근거: `검증결과/staff_s5/part3_legacy_references.json`.
''',encoding='utf-8')
    print('Part3 references recorded:',len(references))


if __name__=='__main__': main()
