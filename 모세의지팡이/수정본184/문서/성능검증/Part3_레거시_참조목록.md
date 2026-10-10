## 수정본25 정리

- 수정본25/Part3 폴더를 삭제했다. 원본과 이전 수정본은 수정하지 않았다.
- Part1·Part2 Python 실행 코드에 Part3 호출은 없다. test_staff_s1.py의 폴더 대상 두 곳에서 Part3를 제거했다.
- Part1 독립성 검사(Part2/Part3 import 금지)와 아래 과거 기록은 보존한다.

# Part3 레거시 참조 목록 — S8

Part3 전 파일 해시가 수정본14와 같다. 동기화·수정하지 않았다. 참조 정리는 S8 이후 별도 작업이다.
아래는 Part1/Part2 소스·설정·문서의 정적 검색 결과다. 로그·결과·캐시·가상환경은 제외했다. 동적 호출 부재까지 증명하는 목록은 아니다.

| 위치 | 참조 |
|---|---|
| `Part1/audit/remediation/30-staff-s1/s0_to_s1.json:46` | `"file": "Part3/calculations/common.py",` |
| `Part1/audit/remediation/30-staff-s1/s0_to_s1.json:51` | `"file": "Part3/generic_backtest/live_source.py",` |
| `Part2/validation_suite/test_staff_s1.py:53` | `assert not any(('staff' in n.lower() and n not in {'staff_snapshot', 'staff_compat'}) or n.lower().split('.')[0] in ('part2', 'part3') for n in names)` |
| `Part2/validation_suite/test_staff_s1.py:67` | `for part in ('Part2', 'Part3'):` |
| `Part2/validation_suite/test_staff_s1.py:97` | `@pytest.mark.parametrize('part', ['Part2', 'Part3'])` |

근거: `검증결과/staff_s8/part3_legacy_references.json`.
