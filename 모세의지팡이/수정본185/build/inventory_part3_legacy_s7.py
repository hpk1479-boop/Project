"""Inventory references only. Legacy Part3 is never synchronized."""
import re
from staff_s7_evidence import *
expected={r['path']:r['sha256'] for r in read(OUT/'s6_frozen_manifest.json') if r['path'].startswith('Part3/')}
actual={p.relative_to(ROOT).as_posix():sha(p) for p in (ROOT/'Part3').rglob('*') if p.is_file()}
assert expected==actual
text_ext={'.py','.md','.txt','.json','.toml','.bat','.ps1','.ini','.cfg','.yaml','.yml','.mq5','.mqh'}
excluded={'__pycache__','.pytest_cache','generic_runs','results','logs','.git','node_modules'}
refs=[]
for part in ('Part1','Part2'):
    for p in sorted((ROOT/part).rglob('*')):
        if not p.is_file() or p.suffix.lower() not in text_ext or set(p.parts)&excluded:continue
        if any(x.startswith(('.venv','tmp_')) for x in p.relative_to(ROOT).parts):continue
        try:lines=p.read_text('utf-8-sig').splitlines()
        except (UnicodeError,OSError):continue
        for num,line in enumerate(lines,1):
            if re.search('part3',line,re.I):refs.append({'file':p.relative_to(ROOT).as_posix(),'line':num,'text':line.strip()})
write(OUT/'part3_legacy_references.json',{'Part3_all_files_hash_identical':True,'Part3_files_checked':len(expected),
    'Part3_modified':False,'references':refs,'policy':'References only; cleanup after S8; existing failures remain baseline defects.'})
lines=['# Part3 레거시 참조 목록 — S7','','Part3 전 파일 해시가 수정본13와 같다. 동기화·수정하지 않았다. 참조 정리는 S8 이후 별도 작업이다.',
       '아래는 Part1/Part2 소스·설정·문서의 정적 검색 결과다. 로그·결과·캐시·가상환경은 제외했다. 동적 호출 부재까지 증명하는 목록은 아니다.',
       '', '| 위치 | 참조 |','|---|---|']
lines += [f"| `{r['file']}:{r['line']}` | `{r['text'].replace('|',' / ')}` |" for r in refs]
lines+=['','근거: `검증결과/staff_s7/part3_legacy_references.json`.']
(ROOT/'Part3_레거시_참조목록.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
print('Part3 unchanged;',len(refs),'references recorded')
