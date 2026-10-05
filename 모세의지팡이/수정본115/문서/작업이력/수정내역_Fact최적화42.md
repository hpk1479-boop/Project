# 수정본42 — Fact 속도 최적화 1차 묶음

작성일: 2026-09-29  
입력 프로젝트: **수정본41.zip**  
입력 압축 SHA-256: `cc95c02a841d4cd01ea8cefef9fe2cec851936c5eff335580701488a222225f2`

## 1. 완료 범위

요청한 **① Fact 중복 비교 → ② 의존정보·열 번호 사전계산 → ③ 캐시 적중 조기 반환**을 모두 적용했습니다. 제외한 구현 단계는 없습니다.

운영 코드 변경은 **`Part1/program/event_engine/facts.py` 1개 파일**입니다. Part2는 기존과 같이 이 공용 Fact 경로를 사용합니다. Part1·Part2의 전략 조건, 지표 계산식, 봉 수, 이벤트 전달, 워밍업, 워커 수·청크 분할, 가상진입, 입력 파일 해시 검증, Wire 검증은 변경하지 않았습니다. Part3는 수정·분석 대상에서 제외했습니다.

첨부된 수정본41의 파일 내용과 작업 원본을 대조했으며 원본 변경은 0건입니다. 새 프로젝트에서 기존 파일의 변경은 위 운영 파일 1개, 의도하지 않은 누락은 0건입니다. 이전 `검증결과`와 Python 임시 캐시는 새 프로젝트로 복사하지 않았습니다. 이번 실행의 증거는 `검증결과/Fact최적화42/`에 있습니다. 기존 프로젝트에 포함된 과거 보고서는 이번 검증 결과로 사용하지 않습니다.

## 2. 단계별 구현

### ① Fact 중복 배열 비교

`invalidate()`에서 같은 `(심볼, 주기)`의 이전·현재 스냅샷 쌍에 대해 `(invalidation 축, 원시 열)`별 비교 결과를 한 번만 계산합니다. 여러 Fact가 같은 열을 요구하면 정확한 비교 결과만 재사용합니다.

- `np.array_equal(..., equal_nan=True)`를 유지합니다. 허용오차 비교로 바꾸지 않았습니다.
- 진행봉 포함 비교와 확정봉 전용 비교를 분리합니다.
- 비교 캐시는 스냅샷 쌍 안에서만 존재합니다. 다른 심볼·주기·이벤트로 넘기지 않습니다.
- `source_epoch`가 달라지면 기존대로 결과를 이어받지 않습니다.
- 변경된 열을 발견하면 기존처럼 해당 Fact의 나머지 비교를 중단하며, 유지되는 Fact의 순서·값을 보존합니다.

실제 등록된 `ATR14_GENERAL`·`WONBI_BANDS`를 채운 650행 입력에서, 변경 없는 스냅샷의 배열 비교는 **18회 → 7회**였습니다. 첫 단계 신규 검사 **19개가 통과**했습니다.

### ② 의존정보·열 번호 사전계산

원시 입력 의존정보는 최초 요청 때 계산하여 제한된 캐시에 보관합니다. 이후 동일한 Fact 정의에서는 재귀적인 의존정보 구성을 반복하지 않습니다. 캐시 상한은 256개입니다.

캐시에는 루트뿐 아니라 **모든 하위 Fact의 이름과 frozen `FactSpec` 객체**를 함께 저장합니다. 사용 전 현재 레지스트리의 정의가 같은 객체인지 확인합니다. 하위 정의 교체·삭제를 발견하면 메타데이터 캐시를 비우고 현재 정의로 다시 계산합니다. 새 등록, 재등록, 없는 이름, 순환 의존성, 명시적 탐색 스택을 검사했습니다.

`time` 의존성을 유지하며, 기존 `FACT_INPUTS`를 그대로 가져와 시간축 검사를 빠뜨리는 방식을 사용하지 않았습니다. 원시 열 번호는 현재 로드된 Wire 스키마에서 한 번 구성합니다. `time`·`volume` 접근과 없는 열의 `ValueError`도 유지합니다.

**접근 권한 검사 `validate_reference()`는 수정하지 않았습니다.** 권한 검사 결과를 무기한 캐시하지 않습니다. 지표 수식이나 레지스트리 자료형도 바꾸지 않았습니다. ①을 포함한 누적 신규 검사 **27개가 통과**했습니다.

### ③ 캐시 적중 조기 반환

현재 `(심볼, 주기)`와 동일한 스냅샷에 계산 결과가 있으면 `resolving`·`frames` 및 내부 `compute()` 준비 전에 반환합니다.

권한 검사를 먼저 수행하고, 기존 반환 경로의 `freeze()`와 pandas→NumPy 변환을 그대로 사용합니다. 배열 반환 시 독립된 shape 헤더와 읽기 전용 데이터 보호를 유지합니다. 캐시를 한 번 읽은 뒤 배열 모양을 바꿔도 다음 조회의 모양은 영향을 받지 않는지 검사했습니다. 다른 스냅샷·키에는 적중시키지 않습니다.

`compute()`의 AST, 재귀 계산·recurrence 처리·계산 횟수 갱신은 수정 전과 같습니다. 이 단계 누적 신규 검사 **42개**, Wire 전달 경로 검사를 추가한 최종 신규 검사 **43개가 통과**했습니다.

## 3. 로직·전달 경로 검증

| 검사 | 확인 내용 | 결과 |
|---|---|---|
| 정확한 무효화 | 진행봉·확정봉, 과거 봉 정정, 시간축 이동, 행 수 변화, epoch, volume | 통과 |
| 숫자 경계 | 같은/다른 NaN·Infinity, +0/-0, 1행의 확정봉 빈 구간 | 통과 |
| 계산 보존 | 갱신 캐시 결과와 매번 새로 계산한 ATR·원비 결과, ATR의 pandas 전체 계산 | 통과 |
| 의존정보 갱신 | 하위 정의 교체·삭제·재등록, 없는 이름·순환, 캐시 상한 | 통과 |
| 반환 보호 | private Fact 권한, 하위 권한 변경, ndarray shape·쓰기 보호, 반환 타입 | 통과 |
| 엔진 입력 대조 | 같은 합성 입력의 직접 ingress와 `replay()` | 각 7개 입력·2개 신호, 신호·상태·Fact 계산 횟수 동일 |
| Part2 입력 대조 | 같은 합성 Wire의 `CaptureInputs(transport='live')`와 `transport='replay'` | 각 7개 입력·2개 신호, 신호·상태·Fact 계산 횟수 동일 |

Wire 검사는 현재 `PipeReceiver`에 인메모리 읽기 함수를 전달한 경로와 `StaffIngressAdapter` 경로를 사용했습니다. **Windows named pipe나 MT5를 실제 실행한 검사가 아닙니다.** 네트워크 전송과 실제 주문은 하지 않았습니다. 현재 첨부에는 전체 시간 측정에 사용할 실제 시장 녹화 파일이 없어 전체 백테스트 성능 검사는 하지 않았습니다.

## 4. 기존 검사와 막힌 항목 처리

아래 통과·실패 수는 최종 관련 검사에서 고유 테스트를 센 값입니다. 단계별 반복 실행 수를 합산하지 않았습니다.

| 검사 묶음 | 통과 | 실패 | 수집 오류 |
|---|---:|---:|---:|
| 신규 Fact42 | 43 | 0 | 0 |
| 기존 Event E1 | 29 | 2 | 0 |
| 기존 Event E2 | 16 | 2 | 0 |
| 기존 Event E3 | 9 | 2 | 0 |
| 기존 Event PERF1 | 28 | 1 | 0 |
| 기존 NumPy processors | 45 | 0 | 0 |
| 기존 engine optimization | 10 | 1 | 0 |
| 기존 Part2 runner | 0 | 0 | 1 |
| **합계** | **180** | **8** | **1** |

기존 실패는 각 항목을 **한 번 재시도**한 뒤 수정본41에서도 대조했습니다. **8개 실패와 1개 수집 오류 모두 수정본41에서 동일하게 재현**되었습니다. 해당 검사를 통과시키기 위해 전략·스키마·입력 검증을 바꾸거나 과거 구현을 복원하지 않았습니다.

| 항목 | 원인·판단 | 처리 |
|---|---|---|
| `test_native_windows_named_pipe_live_vs_replay` | Linux에 `ctypes.WinDLL`이 없습니다. | 재시도 후 네이티브 Windows 검증 제외 |
| `test_bar_close_fact_ignores_forming_updates_until_new_bar` | 테스트가 source epoch를 `session:1`에서 빈 문자열로 바꿉니다. epoch 변경에 따른 재계산으로 양쪽 모두 2회입니다. | 기존 검사 실패로 기록. 새 검사에서 동일 epoch의 진행봉 변화와 epoch 변경을 별도로 검증 |
| `test_sweep_canonical_transitions_and_resume` | 예전 테스트 대역 Board에 현재 필요한 `fact()`가 없습니다. | 재시도 후 기존 대역 불일치로 기록 |
| `test_first_oz_state_matches_entire_frozen_module_and_midnight_checkpoint` | 현재 스키마에 없는 `wonbi_sigma` 열을 테스트 입력이 요구합니다. | 재시도 후 이전 스키마 가정으로 기록 |
| `test_native_export_columns_and_subsecond_archive[1]`, `[2]` | DuckDB가 설치되지 않은 환경입니다. | 재시도 후 실제 SQL 통합 검증 제외 |
| `test_bundle_cache_copies_features_attrs_and_invalidates` | 예전 테스트 대역 Board에 `fact()`가 없습니다. | 재시도 후 기존 대역 불일치로 기록 |
| `test_part3_is_not_an_execution_dependency` | Part3 폴더 자체가 없어야 한다고 가정하지만 현재 첨부에 폴더가 있습니다. | 재시도 후 기존 패키지 구조 가정으로 기록. Part3를 삭제하지 않음 |
| `tests/test_part2_event_runner.py` 수집 | 모듈 가져오기에서 DuckDB가 필요합니다. | 전체 모듈 재시도 후 동일 오류 기록 |

검사 도구에서 수집 오류의 빈 classname을 잘못된 재시도 경로로 변환한 문제가 한 번 있었습니다. 신규 검사 도구만 수정한 뒤 실제 모듈을 다시 실행해 DuckDB 미설치를 확인했습니다. 잘못된 호출 로그도 `part2_retry_selector_error.*`로 남겼습니다. 운영 코드의 예외를 숨기거나 통과로 바꾸지 않았습니다.

기존 `Part1/audit/source_integrity.py`의 전체 검사에서도 **수정본41과 42에 동일한 7개 불일치**가 남아 있습니다. 재검사 및 대조 결과는 `integrity_retry_comparison.json`에 있습니다. 이번 Fact 파일 변경만 `72-fact-optimization42/changes.json`에 추가했고, 기존 불일치를 임의로 승인하거나 해시 기준을 덮어쓰지 않았습니다.

남아 있는 7개 항목: `AGENTS.md.txt`, `program/MT5/THE_STAFF_OF_MOSES.mq5`, 이미지·안내 파일 3개, 미등록 `program/indicator_score.py`, 미등록 `program/oz_profiles.py`. **전체 기존 무결성 검사를 통과했다고 표시하지 않습니다.** 이번 패치 범위 검사에서는 요청 외 운영 파일 변경·누락이 없습니다.

## 5. 구간별 성능 측정

환경: Linux / Python 3.13.5 / NumPy 2.3.5 / pandas 2.2.3. 650행 합성 입력, 각 경로 사전 실행 후 수정 전·후를 AB/BA 순서로 번갈아 **각 5회** 측정한 중앙값입니다. 계측용 비교 횟수 집계와 타이밍 실행을 분리했습니다. 프로파일러는 사용하지 않았습니다.

| 측정 구간 | 수정본41 | 수정본42 | 시간 감소율 |
|---|---:|---:|---:|
| 변경 없는 입력의 invalidate 1,200회 | 0.212604초 | 0.103156초 | 51.48% |
| ATR 캐시 조회 20,000회 | 0.058340초 | 0.038545초 | 33.93% |
| ATR 원시 의존정보 조회 20,000회 | 0.039025초 | 0.012629초 | 67.64% |
| 원비 상단 열 조회 50,000회 | 0.026532초 | 0.010823초 | 59.21% |
| Fact 갱신·조회 혼합 240개 입력 | 0.094980초 | 0.070668초 | 25.60% |

**위 수치는 Fact 내부 구간의 측정입니다. 전체 백테스트, 실제 전략 전체, 디스크 읽기·SQL 저장·워밍업·프로세스 시작을 포함한 속도 개선율이 아닙니다.** 실제 전체 개선율은 해당 경로가 원래 실행시간에서 차지하는 비중에 따라 달라집니다. 과거 수정본의 속도 개선율과 누적·곱셈하지 않습니다.

## 6. 파일과 재실행

- 운영 수정: `Part1/program/event_engine/facts.py`
- 신규 회귀검사: `tests/test_fact_optimization42.py`
- 신규 검사·측정 도구: `build/optimization42/`
- 이번 변경 무결성 기록: `Part1/audit/remediation/72-fact-optimization42/changes.json`
- 증거: `검증결과/Fact최적화42/`의 XML·로그·JSON·diff

수정본42 프로젝트 폴더에서 실행합니다. `수정본41_폴더`에는 별도로 보관한 수정본41 프로젝트의 실제 위치를 지정합니다. 기존 설정·실행 진입점은 변경하지 않았습니다.

```bash
python -m pytest tests/test_fact_optimization42.py -q -p no:cacheprovider
python build/optimization42/run_checks.py
python build/optimization42/compare_original_failures.py --original "수정본41_폴더"
python build/optimization42/measure_fact_paths.py --original "수정본41_폴더"
```

`measurements.json`에는 모든 원시 시간 표본과 원본·수정본 Fact 파일 SHA-256을 보관했습니다. `scope.json`은 입력 압축과 파일 변경 범위, `ast_scope.json`은 권한·실제 계산 함수 보존, `completion.json`은 완료·제외·미검증 범위를 기록합니다.
