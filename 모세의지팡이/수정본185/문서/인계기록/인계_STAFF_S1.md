# 수정본8 인계 — S1 완료

## 현재 상태

- 구현 범위는 S1 순수 Fact 이전이다. S2는 아직 구현하지 않았다.
- `검증결과/staff_s1/status.json`: s1_complete=true. 원비 변경 없음. S0 expected 갱신 없음.
- 수정본6, 수정본7 원본은 그대로다. 수정본7 4,188개 파일 전체와 수정본8에 복사한 S0 증거를 SHA256으로 확인했다.
- 다음 단계는 이 완료본을 보존하고 새 독립 수정본에서 진행한다.

## 사용자 최종 검증 규칙

`성능규칙_S1_S8.md`와 `build/staff_performance_policy.json`이 기존 설계서의 매 단계 성능 게이트를 대체한다.

- S1–S4, S6–S7: 골든 해시 우선 비교, 240초 3-way 1회, 전체 테스트 1회, 새 실패 0. 성능은 1회 참고만 기록한다.
- 성능 판정은 S5 종료와 S8 종료에만 한다. CPU 고정 S0 5회는 완료했고, 이 보정과 후보 반복 측정은 S1에서 더 실행하지 않는다.
- 단계마다 수치·방법을 바꾸거나 S0를 다시 생성하지 않는다. 실패 원인을 분석할 때만 관련 테스트를 새 출력 경로로 재실행한다.
- S0 합성/실제 MT5 골든과 기존 expected는 절대 갱신하지 않는다.

## 구현

STAFF 순수 함수 9개의 본문 AST를 그대로 indicator_facts / monitor_OZ / strategy_FVG로 옮겼다. Part2/Part3 common은 동일 LIVE 함수 객체를 재내보낸다. ATR14_GENERAL과 FVG_WILDER_ATR을 분리했고 원비는 S7까지 그대로다. Watch MA 이력·epoch, 매니저, SPECIAL, EA도 변경하지 않았다.

## 완료 증거

- 합성 33,996개 사례, 정확 DataFrame 19,160개 동일.
- 실제 MT5 골든 4,542개 사례, 정확 DataFrame 4,302개 동일. S0 일봉 EMA 미준비 239개 응답도 동일하다.
- 3-way 8개 필드 및 nonempty 확인. S0 set/frozenset 기록 순서 차이는 타입이 명시된 OZ 집합에만 순서 무시 비교를 적용하고 원시 차이를 보존했다.
- BEFORE는 수정본6 Part1/program 전체 동결본이다. 부분 모듈 혼합은 사용하지 않는다.
- 기존 회귀 14개: 전후 6, LIVE/BACKTEST 6, 시나리오 1, 성능 1을 유지했다. 동작 13개 AST는 그대로다. 성능 1개는 최종 단계 정책에 따라 S1에서는 참고 기록이며 Fact 재사용은 계속 검사한다.
- ATR 분리 3종, 함수 AST/독립 import/원비 보존을 포함한 S1 테스트 16개, 집합 비교 음성 대조 3개 통과.
- 무결성 체인 21/22 등록. 기존 오류 29개 중 이번 소유 파일 4개만 해소, 나머지 25개 보존, 새 진단 0.
- S1 Part1 불변 목록 1,182개 확인.

## Part2 전체 범위

기준본 715개, S1 745개 수집, 기존 ID 누락 0. validation_suite만 실행하면 341개가 빠진다.
대상은 validation_suite + cadence_input_validation + conditional_validation + watch_ma_validation 전체다.
745개 최종 signature: 699 pass / 6 fail / 21 error / 19 skip. 기존 실패 원인이 기준본과 같고, Tk 초기화 skip 변동 2개는 별도 S0 재현 근거가 있다. 전체 프로그램의 모든 테스트가 green이라는 뜻은 아니다.
과거 문서의 660은 pass 수이며 원시 로그가 없어 현재 수집 수와 동일시하지 않는다.

## 성능 증거와 동결값

- 첫 단발 wall: S0 117.3468초 / S1 118.8382초. 최초 실패 기록 보존, 최종 S1에서는 참고용이다.
- CPU 비고정 보정 5회는 최대 편차 약 47%로 실패했다. CPU 고정 보정 5회의 임시 공통 10% 상한 실패 기록도 그대로다.
- 최종 정책은 CPU 고정 표본의 작업별 최대 편차에 두 배 여유(최소 5%)를 주고 1% 단위로 올림한다.
- 고정 비율: parser 1.05 / request_0 1.15 / request_1 1.09 / request_2 1.06 / request_3 1.09 / 전체 240초 1.06.
- 전체 시나리오 S0 CPU 73.8125–76.046875초, 중앙값 74.171875초, 최대 편차 2.528%.
- 후보의 추가 반복 측정은 하지 않았다. S5/S8에서 S0와 후보를 교대로 각 5회 실행한다. 모든 작업별 CPU 중앙값 비율과 S0 환경 이동을 검사한다.

## 실행 도구

- `build/compare_staff_golden.py`: 파일 SHA256 우선, 다를 때 기존 정확 비교. 입력은 검증된 불변 S0여야 한다.
- `build/run_staff_performance_stage.py --stage S5 --out <새 폴더>`: S5/S8만 허용. 정책 파일의 수치와 CPU 고정 worker 코드를 유지한다.
- `STAFF_STAGE` / `STAFF_PERFORMANCE_RESULTS`: 기존 성능 테스트의 해당 단계 결과 지정.
- `build/finalize_staff_s1.py`: 저장된 S1 증거만 대조한다. 실제 테스트를 재실행하지 않는다.
- S1의 one-shot 생성/등록 스크립트는 완료했다. 재실행하지 않는다.

자세한 증거와 기존 실패 목록은 `검증결과/staff_s1/결과.md` 및 baseline_signature_compare.json에 있다.
