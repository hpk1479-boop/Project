"""One-off revision25 setup; never executes the source revision."""
from pathlib import Path
import hashlib,json,shutil

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'검증결과/engine_optimization'

def main():
    OUT.mkdir(parents=True,exist_ok=True)
    old=('## Part3 레거시 제외\n- Part3는 레거시다. 읽기·검토·시험·실행·무결성 대조를 하지 않는다.\n'
         '- Part3 관련 실패·차이는 보고하지 않는다. 사용자가 직접 지시할 때만 다룬다.\n'
         '- Part1/Part2가 Part3를 참조하는 곳을 발견하면 수정하지 말고 보고서에 한 줄만 적는다.')
    new=('## Part3 전략생성기 자리\n- Part3는 전략생성기 자리이며 현재 비어 있다.\n'
         '- Part1·Part2는 Part3를 호출하지 않는다. 이번 수정본25에서는 전략생성기를 구현하지 않는다.\n'
         '- 이전 수정본과 개피곤 원본 Part3는 수정하지 않는다. 과거 기록용 참조는 보존한다.')
    evidence=[]
    for path in (ROOT.parent/'AGENTS.md',ROOT.parent/'CLAUDE.md',ROOT/'AGENTS.md.txt'):
        raw=path.read_bytes();ending='\r\n' if b'\r\n' in raw else '\n'
        text=raw.decode('utf-8').replace('\r\n','\n')
        assert old in text,path.name
        text=text.replace(old,new)
        path.write_bytes(text.replace('\n',ending).encode('utf-8'))
        evidence.append({'path':path.name if path.parent==ROOT.parent else '수정본25/'+path.name,
                         'before_sha256':hashlib.sha256(raw).hexdigest(),'after_sha256':hashlib.sha256(path.read_bytes()).hexdigest()})
    path=ROOT/'Part2/validation_suite/test_staff_s1.py'
    raw=path.read_bytes()
    raw=raw.replace(b"for part in ('Part2', 'Part3'):",b"for part in ('Part2',):")
    raw=raw.replace(b"@pytest.mark.parametrize('part', ['Part2', 'Part3'])",b"@pytest.mark.parametrize('part', ['Part2'])")
    path.write_bytes(raw)
    with (ROOT/'AGENTS.md.txt').open('ab') as f:
        f.write(('\r\n## 엔진 최적화 최신 지시 — 수정본25\r\n'
            '- 수정본24 및 이전 수정본은 수정하지 않는다. Part3 삭제는 수정본25에만 적용한다.\r\n'
            '- 엔진 기본 비용, 공통 STAFF 디코드, COMPOSER A만 최적화한다. 판정·문구·ID·순서는 유지한다.\r\n'
            '- EA/schema/저장 형식/config/SPECIAL 구조는 변경하지 않는다. Telegram 전송은 차단한다.\r\n'
            '- XAUUSD+ 2024-10-01~2025-10-01 BAR 녹화와 SPECIAL1 월별 병렬 실행은 사용자가 승인했다. 기존 유효 조각은 재사용한다.\r\n').encode('utf-8'))
    note=ROOT/'Part3_레거시_참조목록.md'
    raw=note.read_bytes()
    note.write_bytes(('## 수정본25 정리\r\n\r\n- 수정본25/Part3 폴더를 삭제했다. 원본과 이전 수정본은 수정하지 않았다.\r\n'
        '- Part1·Part2 Python 실행 코드에 Part3 호출은 없다. test_staff_s1.py의 폴더 대상 두 곳에서 Part3를 제거했다.\r\n'
        '- Part1 독립성 검사(Part2/Part3 import 금지)와 아래 과거 기록은 보존한다.\r\n\r\n').encode('utf-8')+raw)
    # A complete Part1 program, including its startup/config/state files. No mixed old/new modules.
    frozen=OUT/'before_runtime'
    assert not frozen.exists()
    shutil.copytree(ROOT/'Part1/program',frozen/'Part1/program')
    for name in ('event_backtest','generic_backtest','part1_host','staff_golden','data_warehouse','calculations','pit','live_replay'):
        shutil.copytree(ROOT/'Part2'/name,frozen/'Part2'/name)
    hashes={p.relative_to(frozen).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
            for p in frozen.rglob('*') if p.is_file() and '__pycache__' not in p.parts}
    (OUT/'before_runtime_sha256.json').write_text(json.dumps(hashes,indent=2),encoding='utf-8')
    (OUT/'part3_cleanup.json').write_text(json.dumps({'folder_absent':not(ROOT/'Part3').exists(),'rules':evidence,
        'removed_test_targets':2,'historical_records_preserved':True,'frozen_runtime_files':len(hashes)},ensure_ascii=False,indent=2),encoding='utf-8')
    print('SETUP_COMPLETE',len(hashes),flush=True)

if __name__=='__main__':main()
