"""Render the measured S1 gate results; no expectation files are modified."""
from staff_s1_evidence import ROOT, OUT, S0
from finalize_staff_s1 import read


def main():
    status = read(OUT / 'status.json')
    tests = read(OUT / 'baseline_signature_compare.json')
    perf = read(OUT / 'performance_compare.json')
    state = '완료' if status['s1_complete'] else '미완료 — 실패 게이트 해결 필요'
    lines = ['# 수정본8 — S1 순수 Fact 이전 검증 결과', '',
             f'**S1 {state}. S2는 시작하지 않았다.**', '',
             '수정본7의 S0 전체 4,188개 파일을 SHA256으로 고정하고 수정본8로 전체 복사했다. '
             'S0 원본·복사된 골든·기존 expected는 변경하지 않았다. '
             '성능 항목 1개는 사용자 지시에 따라 S5/S8만 판정하고 나머지 단계에서는 참고 기록한다. '
             'S1 결과는 별도 폴더에 기록했다.', '',
             '## 최종 게이트', '', '| 항목 | 결과 |', '|---|---|']
    lines += [f'| {name} | {"PASS" if passed else "FAIL"} |' for name, passed in status['gates'].items()]
    lines += ['', f"합성 {status['synthetic']['left_cases']:,}개 사례 / DataFrame {status['synthetic']['exact_frames_checked']:,}개, "
              f"실제 MT5 {status['actual']['left_cases']:,}개 사례 / DataFrame {status['actual']['exact_frames_checked']:,}개를 정확 비교했다. "
              '값·dtype·열·index·attrs 비교이며 S0 실제 캡처를 재사용했다. '
              f"기준본 일봉 EMA 미준비 응답 {status['inherited_actual_unavailable']}개도 보존해 비교했다.", '',
              '## 실제 변경', '',
              '- STAFF의 순수 함수 9개를 본문 그대로 indicator_facts / monitor_OZ / strategy_FVG로 이전하고 STAFF는 import한다.',
              '- 공용 ATR14_GENERAL을 Fact 레지스트리에 등록했다. FVG_WILDER_ATR은 다른 계산법 그대로이며 _wilder_atr 별칭을 유지한다.',
              '- Part2/Part3 common은 새 소유 모듈의 동일 함수 객체를 다시 내보낸다. derived는 기존 재내보내기를 유지한다.',
              '- 원비 함수·상수·호출·설정, Watch MA 이력·epoch 규칙, 매니저·SPECIAL·EA는 변경하지 않았다.',
              '- 동작 검증의 입력과 assertion은 유지했다. BEFORE는 수정본6 전체 프로그램을 사용하며 성능 검사만 승인된 반복 CPU 규칙을 따른다. audit 어댑터는 소유 모듈을 STAFF보다 먼저 로드하도록 import 순서를 조정했다.', '',
              '함수 AST 동일성, 공용 ATR의 S0 값 일치, 두 ATR의 차이, 독립 import, 원비·MA 보존을 검증했다. '
              '사용자의 추가 지시에 따라 무결성 체인의 21번 단위로 이번 4개 소유 파일만 실제 S0 해시에 연결하고 '
              '22번 단위로 S1 변경을 등록했다. 원래 manifest와 다른 기존 결함은 보존했다. '
              'S1 불변 목록 1,182개 파일도 재생성해 실제 파일과 일치함을 확인했다.', '',
              '## 기존 테스트 baseline signature', '',
              '| 그룹 | 통과 | 실패 | 오류 | 건너뛰기·xfail |', '|---|---:|---:|---:|---:|']
    a = tests['audit_s1']
    lines.append(f"| Part1 audit | {a['run'] - a['failures'] - a['errors']} | {a['failures']} | {a['errors']} | 0 |")
    for name, group in tests['groups'].items():
        c = group['s1_counts']
        lines.append(f"| {name} | {c.get('PASSED', 0)} | {c.get('FAILED', 0)} | {c.get('ERROR', 0)} | {c.get('SKIPPED', 0)} |")
    lines += ['', '기존 테스트의 ID·결과·실패 원인 signature를 S0와 비교했다. '
              '신규 S1 테스트는 별도로 식별한다. 기존 무결성 오류 중 등록한 4개만 해소됐고 나머지 25개는 남는다. '
              'SPECIAL 디렉터리 읽기, 누락된 GENERIC_EXAMPLE_V1/DataManager, Windows 경로 비교 문제는 정상 통과로 바꾸지 않았다. '
              '따라서 baseline과 동일하더라도 애플리케이션 전체 green을 의미하지 않는다.', '',
              '원래 실행 범위에 없던 Part2 테스트 341개(cadence 42 / conditional 96 / Watch MA 203)를 '
              '기준본과 S1에서 추가 실행했다. 전체 수집은 기준본 715개, S1 745개이며 누락 ID는 없다. '
              'S0 기록기 11개와 S1 검증 19개가 늘었다. 과거 문서의 660은 통과 수이며, 당시 원시 로그가 없어 '
              '현재 수집 수와 직접 같다고 주장하지 않는다. 새로 확인한 기존 결함은 resources 없는 테스트 설정과 '
              '누락된 baseline_ma_values.json 두 건이다.', '',
              'Tk 초기화 실패로 skip됐던 두 항목은 별도 표시한다. 실행 모드 GUI 오류는 S0에 이미 저장된 '
              '기준본 재현 로그와 같고, Dashboard는 기준본의 해당 항목만 격리 재실행해 S1과 같은 통과를 확인했다. '
              '초기 skip 기록을 덮어쓰거나 전체 suite를 다시 실행하지 않았다.', '',
              '## 14개 기존 회귀 검증의 입력 수정', '',
              'test_oz_fvg_optimization.py의 BEFORE fixture는 수정본6 Part1/program 전체 동결본을 복사하고 '
              '전체 파일 SHA256을 원본 manifest와 대조한다. 옛 OZ/FVG/STAFF를 현재 모듈과 섞는 중간 시도는 중단했고 '
              '그 결과를 채택하지 않았다. 전후 6개·LIVE/백테스트 6개·시나리오 1개의 테스트 함수 AST와 '
              'assertion은 S0와 동일하다. 성능 1개도 유지하되 S5/S8만 고정 CPU 규칙으로 판정한다. '
              '다른 단계에서는 1회 참고 기록하며 Fact 재사용 검사는 계속 적용한다. 초기 13/14 결과, 최종 oz_14_contract_final.json 및 '
              '성능 항목만 재실행한 oz_performance_protocol.xml을 함께 보존한다.', '',
              '## G3에서 발견한 기록 순서 문제', '',
              'S0 OZ 기록기는 set/frozenset의 원소를 Python 해시 순서대로 배열에 넣었다. '
              '이번 기준본 LIVE 재실행과 S1 두 경로에서 14개 집합의 DI/STO 순서만 달랐다. '
              '원시 파일과 원시 해시는 그대로 보존했다. S0의 기대값을 다시 만들거나 바꾸지 않았다.', '',
              '비교기는 OZ의 명시적 set/frozenset 태그에 한해서 집합 순서를 무시한다. '
              '다른 목록·tuple·dict 항목 순서, 구성원·중복·타입, 알림·시각·수신자 차이는 여전히 실패한다. '
              '음성 대조 3개가 통과했고, 완료된 기록을 재비교해 세 경로 모두 S0와 일치함을 확인했다. '
              'G3 실행 자체는 한 번이며, 원인 분석 후 저장된 결과만 재비교했다. '
              'parity_diagnosis.json과 parity_s0_compare.json에 원시 차이와 비교 정책을 명시했다.', '',
              '## STAFF 성능', '',
              '첫 단발 측정의 일부 STAFF 지표는 S0 +5% 기준을 넘었고, 기존 전체 시나리오 wall 검사는 '
              '117.3468초 → 118.8382초로 실패했다. 이 결과는 초기 증거로 보존한다. '
              '사용자가 측정 흔들림을 근거로 반복 CPU 비교 규칙을 정하고 S8까지 고정하도록 지시했다.', '',
              'CPU 이동을 허용한 S0 보정 5회는 최대 편차 약 47%로 실패했다. '
              '1.95의 한도가 필요했지만 1.10 상한을 넘으므로 채택하지 않았다. 원본은 performance_protocol_unpinned에 보존한다. '
              '새 방식의 S1 후보 반복 비교 전에 worker만 같은 논리 CPU에 고정한 v2 보정으로 환경 영향을 확인했다. '
              '다른 앱·MT5·전원 설정·우선순위는 바꾸지 않았으며, 표본 수·작업량·한도 산식도 그대로다.', '',
              '동결 S0를 5회 독립 측정한 뒤 후보를 보기 전에 작업별 숫자 한도를 잠갔다. '
              '각 작업의 중앙값 대비 최대 절대 편차에 2배 여유를 주고 1% 단위로 올림한다(최소 5%). '
              '고정 CPU 보정도 임시 공통 10% 상한으로는 실패했다(짧은 request_0의 최대 편차 7.407%). '
              '그 실패 lock은 수정하지 않았다. 사용자의 최종 지시에 따라 완료된 표본으로 작업별 허용치를 확정했다. '
              '큰 request_0 한도를 다른 작업에 일괄 적용하지 않는다. '
              'S5와 S8 종료에만 S0 → 후보 순으로 각각 5회 교대 측정하고 CPU 중앙값의 비율을 비교한다. '
              '비교 중 S0 자체가 보정 시점 범위를 벗어나도 실패한다.', '',
              '**S1 성능은 참고용이며 통과/실패를 판정하지 않았다. 후보의 추가 반복 측정은 실행하지 않았다.**', '',
              'STAFF 파싱 1,000회·각 요청 250회와 기존 OZ/FVG LIVE 240초 전체를 포함한다. '
              'STAFF의 입력 생성·시작·디스크 I/O는 시간에서 제외한다. GC는 켜며 동일 warmup을 사용한다. '
              '각 worker는 허용된 가장 낮은 논리 CPU에 고정한다. '
              'wall과 지연 분포는 진단용으로 보존한다. S1은 계산법을 바꾸는 속도 최적화 단계가 아니다.', '',
              '| 작업 | S0 반복 범위(CPU 초) | S0 중앙값 | 최대 편차 | 고정 허용 비율 |', '|---|---:|---:|---:|---:|']
    for name, m in perf['calibration']['calibration'].items():
        lines.append(f"| {name} | {m['min']:.5f}–{m['max']:.5f} | {m['median']:.5f} | {m['max_abs_relative_deviation']*100:.2f}% | {perf['policy']['limits'][name]:.2f} |")
    lines += ['', '## 증거와 재현', '',
              '- frozen_guard.json / s0_frozen_manifest.json: S0 원본·복사본·expected 불변 확인.',
              '- synthetic_compare.json / actual_compare.json: 고정 골든과의 정확 비교.',
              '- parity_240 / parity_s0_compare.json: 세 경로 및 S0 결과 비교.',
              '- baseline_signature_compare.json / regressions: 전체 G1 실행과 항목별 차이.',
              '- part2_collection_scope.json / part2_extra.xml / baseline_extra.xml: 전체 Part2 수집 범위와 누락분 검증.',
              '- integrity_registration.json / immutable_regeneration.json / immutable_verify.json: S1 무결성 등록 및 목록 검증.',
              '- benchmark.json / performance_initial_compare.json: 초기 성능 실패도 보존.',
              '- performance_protocol / performance_compare.json: S0 변동 폭 및 초기 보정 실패 기록; 후보 5쌍 측정 없음.',
              '- build/staff_performance_policy.json: S5/S8에만 적용하는 최종 고정 방법·허용치·해시.',
              '- source_delta.json / extraction.json: 실제 변경 파일 해시와 함수 이전 기록.', '',
              '실행 위치는 수정본8이다. 최종 전체 실행은 build/run_staff_s1_g1.py, '
              'build/run_staff_s1_g2.py, Part2의 staff_golden parity 및 benchmark 명령으로 수행했다. '
              '출력 폴더를 재사용해 결과를 덮어쓰지 않는다. 실패 분석 시 해당 검증만 새 출력에 실행한다. '
              'build/finalize_staff_s1.py는 저장된 증거를 비교하며 S0 expected를 갱신하지 않는다.', '']
    (OUT / '결과.md').write_text('\n'.join(lines), encoding='utf-8')
    print(str(OUT / '결과.md'))


if __name__ == '__main__':
    main()
