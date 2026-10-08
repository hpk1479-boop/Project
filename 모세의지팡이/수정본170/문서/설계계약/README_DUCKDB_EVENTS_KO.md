# 원본 native Percentile / EVENT — 부분 수정·검증본

## 기준과 현재 판정

기준 ZIP: `DuckDB_EVENT_market_incremental_partial.zip`

SHA-256: `e52fe7f1f8a27882550a5f51c1551af509fc726a70689bd0f653ec2c60f8fc5a`

**원본 그룹 A 초기화와 기존 OFF/ON 불일치 3건은 미해결입니다.** 이 파일은 완성된 그룹 A 구축본이 아닙니다. 이전 ZIP·canonical·과거 테스트 결과를 가져오지 않았으며, 아래 결과는 현재 수정본을 가상환경에서 다시 실행한 값입니다. Part1 82개 파일과 SPECIAL1~7 및 대응 복사본 21개는 기준 ZIP과 바이트 단위로 같습니다. Python/PYW 401개 구문 검사에서 오류가 없습니다.

이번에 수정한 것은 전체 native history의 STAFF 전달, family 단위 publication 판정, 최초 미정 참조 진단, 그룹 A/B 혼합 선택의 격리입니다. UNKNOWN/NaN을 0으로 바꾸거나 첫 close를 새로운 seed로 넣지 않았습니다. Percentile/HMA 수치 공식과 SPECIAL 조건식은 변경하지 않았습니다.

## 실제 수정 파일 — 기존 8개, 신규 파일 없음

| 파일 | 변경 |
|---|---|
| `DataManager/calculations/percentile/series.py` | 최초 실제 read-before-write의 buffer/shift/R/P/원인만 진단하고 기존 checkpoint에 함께 보존 |
| `DataManager/calculations/percentile/kernels/base.py` | 진단값을 결과 metadata에 전달; 수치 계산 변경 없음 |
| `Part2/calculations/percentile/series.py` | DataManager와 같은 진단/복원 변경 |
| `Part2/calculations/percentile/kernels/base.py` | DataManager와 같은 결과 metadata 변경 |
| `Part2/live_replay/event_catalog.py` | 최신 두 행만 전달하던 연결을 전체 publication history로 수정; Part1 CopyBuffer family gate; history 의존량/미정 원인; A 미정 시 B 누락 방지 |
| `Part2/live_replay/__main__.py` | 기존 오류 코드 유지, 실제 native 미정 원인 추가 |
| `tests/sparse_events/test_market_pipeline.py` | 초기 호출·history 전달·forming/commit·checkpoint·순서·혼합 선택 검사 36개 추가. 기존 READY 기대값 유지 |
| `README_DUCKDB_EVENTS_KO.md` | 이번 작업의 결과와 제한으로 갱신 |

일반 원본 수집, MT5 연결, DB 경로, GUI 종료 구현을 다시 작성하지 않았습니다. 기존 EVENT별 calculation_state, checkpoint, transaction, batch read 경로를 재사용합니다.

## Part1 초기화 추적 결과

확인한 소스는 현재 ZIP의 `Part1/program/MT5/THE_STAFF_OF_MOSES.mq5`와 `PRICE_of_Moses.mq5`, `RSI_of_Moses.mq5`, `STO_of_Moses.mq5`, `DI_of_Moses.mq5`입니다.

STAFF가 자체 recurrence를 시작하는 구조가 아닙니다. iCustom/indicator handle을 만들고, BarsCalculated와 CopyBuffer 결과를 확인한 뒤 native buffer를 읽습니다. PublishFeed는 최소 250봉을 요구하며 최대 682봉(650+32)을 읽고 최대 650행을 publication합니다. CopyOneBuffer는 복사 수와 최근 20개 내 유효 값 존재를 검사하며, family의 필요한 버퍼가 실패하면 해당 family를 미정으로 전달합니다. CopyBuffer의 수신 배열을 EMPTY_VALUE로 채우는 부분은 지표 내부 recurrence buffer를 초기화하는 코드가 아닙니다.

기본 설정의 최초 전체 history 호출(P=0, R=전체 봉 수)에서, 소스 계산 루프가 값을 쓰기 전에 읽는 최초 선행 셀은 다음과 같습니다. 아래 index는 AS_SERIES shift이며 0은 현재 forming bar입니다.

| family | 최초 미작성 선행 셀 | 오래된 봉부터의 0-based 위치 |
|---|---|---:|
| PRICE | `EhlersBuffer[R-8]` | 7 |
| RSI | `smoothBuffer[R-25]` | 24 |
| STO | `smoothBuffer[R-37]` | 36 |
| DI | `smoothBuffer[R-37]` | 36 |

PRICE에는 이전 Ehlers 값이 실제 0 또는 EMPTY_VALUE일 때 close를 사용하는 소스 분기가 있습니다. 그 선행 셀이 0/EMPTY인지 현재 소스와 Linux 실행으로 확정하지 못했으므로, UNKNOWN에 이 분기를 임의 적용하지 않았습니다. RSI/STO/DI의 이전 smooth 참조에도 확정된 초기 대입이 없습니다. oscillator basis의 `R-i == BandPeriod` seed 분기는 최초 계산 범위에 들어오지 않습니다.

공식 MQL5 문서도 indicator buffer가 특정 값으로 초기화된다고 보장하지 않습니다. 근거: MQL5 AlgoBook의 SetIndexBuffer 절(“The indicator buffer is not initialized with any values.”). 실제 Windows MT5에서 이 셀의 값과 첫 native buffer publication을 관측하지 못했으므로, 실제 LIVE가 UNKNOWN이라는 주장도 하지 않습니다. 현재 소스만으로 native 초기 값을 확정하지 못한 상태입니다.

원본 replay는 보유 원본의 측정 이전 구간을 계산에 사용합니다. 단순 tick cold-start 문제인지 분리하기 위해 110/200/400/7501봉을 한 번의 P=0 호출로 넣어도 검사했으며, 동일한 미작성 선행 참조가 남았습니다. 따라서 history 길이 증가만으로 해결됐다고 표시하지 않습니다.

원본 READY 회귀 fixture는 Part1의 실제 최소 publication 조건에 맞춰 110봉에서 250봉으로 바꿨지만, `4종 모두 READY > 0` 기대값은 유지했고 계속 실패합니다. 실제 TF_MAP 의존성을 포함하면 기본 그룹 A의 NORMAL 확인 TF는 30m까지 확장되며, 정렬된 완전한 봉 기준 250×30=7500 M1봉이 publication의 history 하한입니다. 이는 recurrence의 초기 값을 보장하는 수가 아닙니다.

## 반영된 publication/연결 보정

기존 kernel 결과를 재사용하여 최대 650행 전체를 STAFF frame에 전달합니다. 기존 최신 2행만 전달하던 방식은 과거 divergence/basis 문맥을 잃었습니다. family의 복사 결과를 하나로 검사하며, 원본 NativeCell의 비트·UNKNOWN·NaN·EMPTY·실제 0은 수정하지 않습니다. 실패 family의 STAFF 표현만 NaN으로 전달합니다. OHLC 또는 HMA만 유효하다는 이유로 Percentile READY를 표시하지 않습니다.

그룹 A의 한 family가 미정일 때 같은 TF 또는 다른 TF의 정상 그룹 B까지 빈 응답으로 사라지는 문제를 수정했습니다. A가 미정인 시각은 OUT/OUT→IN의 선행 유효 상태로 취급하지 않습니다. 최초 유효 OUT 관측도 기존 원칙대로 seed일 뿐 새 OUT EVENT로 만들지 않습니다.

MARKET_TICKS의 그룹 A에만 `FULL_HISTORY_COPYBUFFER_V2` 정의 표식을 추가했습니다. 기존 native fixture/그룹 B 정의는 유지됩니다. 이전 원본 그룹 A 상태를 새 정의와 자동 혼용하거나 전체 자동 재구축하지 않습니다. 기존 definition mismatch 보호에 따라 해당 선택 항목의 명시적 재구축이 필요할 수 있으며, 재구축 자체가 미해결 seed 문제를 해결하지는 않습니다.

## 이번 최종 회귀 실행

환경: Python 3.13.5 / NumPy 2.3.5 / pandas 3.0.2 / pytest 9.0.2. 지정 pandas 3.0.1과 Windows MT5를 포함한 배포 환경은 미검증입니다. Linux GUI는 Xvfb에서 검사했습니다. DuckDB 패키지 설치/다운로드가 실패하여 DB 합성 검사는 명시적 SQLite 시험 대역을 사용했습니다. 제품에 SQLite 자동 대체 기능은 없습니다.

기존 xfail 3건도 `--runxfail`로 실행하여 실제 실패로 집계했습니다. 중간 재시도·중복 실행 수를 합산하지 않았습니다.

| 그룹 | 통과 | 실패 | 건너뜀 |
|---|---:|---:|---:|
| `tests/live_parity` | 50 | 0 | 3 |
| `tests/special2_7` | 94 | 0 | 0 |
| `tests/sparse_events/test_event_catalog.py` | 54 | 0 | 1 |
| `tests/sparse_events/test_market_pipeline.py` | 66 | 1 | 1 |
| `tests/sparse_events/test_events.py` | 80 | 3 | 1 |
| **합계** | **344** | **4** | **6** |

재실행은 프로젝트 루트에서 각 위 경로에 `python -m pytest <경로> --runxfail`을 적용합니다. 가상 GUI 검사는 DISPLAY가 연결된 Xvfb에서 실행했습니다.

`SYNTHETIC_PASS`: 전체 known-buffer publication 전달, source raw/HMA의 forming/완료 commit/checkpoint, 측정 이전 preload와 이전 EVENT 미저장(그룹 B), M1~M6 순서와 family 공통 계산 1회, 그룹 B의 non-empty EVENT·중복·새 종류/TF·앞뒤 증분·경계·재개, 진행률/pause/저장 후 종료/강제 종료/상대 DB 경로/MT5 mock 회귀. 실제 DuckDB crash 복구·실제 MT5 LIVE parity를 의미하지 않습니다.

기존 native publication 합성 fixture의 non-empty Percentile/ALLZONE 및 SPECIAL OFF/ON 사례는 원본 Group A의 positive case가 아닙니다. 이번에 추가한 미정 원인 보존 검사도 수치 READY 통과로 해석하지 않습니다. 원본 1m~6m에서 4종 OUT/OUT→IN과 ALLZONE 5종을 모두 non-empty로 생성하는 요구는 미완료입니다.

## 실패 기록과 재실행 결과

| 검사 | 실제 값 / 원인 | 수정 여부 / 최종 결과 |
|---|---|---|
| `test_market_native_four_percentiles_must_be_finite_after_warmup` | 250 M1 / 500 ticks: 4종 READY 0, 각 undefined 498. 최초 미작성 native 선행 셀에서 recurrence 미정 전파 | publication/진단 수정, seed 미해결: **FAIL** |
| `test_known_special2_early_external_confirmation[LONG]` | OFF Alert 1 / ON 0. 완료 전 OFF만 외부 watch를 CONFIRMED로 변경 | 전략/의미 변경 없음: **FAIL** |
| `test_known_special2_early_external_confirmation[SHORT]` | OFF Alert 1 / ON 0. LONG과 같은 precompletion 상태 결합 | 전략/의미 변경 없음: **FAIL** |
| `test_known_mid_confirmation_invalidation_before_completion` | OFF Alert 0 / ON 1. OFF는 완료 전 middle-TF 실패를 후보에 확정 기록, passive EVENT 경로는 기록하지 않음 | 전략/의미 변경 없음: **FAIL** |
| `test_raw_off_fails_explicitly_for_undefined_native_state` | 중간 수정에서 기존 오류 prefix를 바꾸어 기대된 오류 코드와 불일치 | 기존 prefix 복원 + 상세 원인 추가: `SYNTHETIC_PASS` |
| `test_virtual_event_progress_pause_close_choices_countdown_custom_db`, `test_virtual_force_close_terminates_actual_child`, `test_virtual_exact_shutdown_dialog_labels`, `test_virtual_part2_exact_period_and_owner_close`, `test_virtual_auto_connection_failure_is_nonmodal_and_manual_available` | 최초 실행은 DISPLAY=:0 연결 불가 | Xvfb=:99에서 재실행: 5건 `SYNTHETIC_PASS`; Windows GUI 통과 아님 |

추가 원본 길이 점검: 110/200/400 M1봉에서 4종 READY는 각각 모두 0, family별 undefined는 각각 218/398/798입니다. HMA는 finite입니다.

OFF/ON은 같은 watch identity와 시간순 batch 공급을 대조했습니다. SPECIAL2의 OFF candidate 평가가 최종 HMA/완료 판정 전에 `validate_external_true_b0`를 호출하여 외부 watch를 CONFIRMED로 바꿉니다. 이후 ATR 생존 조건이 악화되어도 OFF는 그 watch를 재무효화하지 않는 반면 ON은 완료 EVENT를 받기 전 ACTIVE 상태이므로 INVALID가 됩니다. NORMAL은 OFF candidate 평가에서 중간 TF 실패를 확정 invalidation으로 보존하지만, passive collector의 `commit_validation=False` 경로는 그러지 않습니다. 이를 SQL 순서·watch ID 오류로 판단하거나 EVENT 시각을 앞당겨 임의 보정하지 않았습니다.

## 남은 미검증 / 제한

**FAIL/미완료:** 원본 Percentile 4종 READY/finite, 원본 그룹 A 13종×6TF non-empty 구축, 원본 ALLZONE 연결 경계, 위 OFF/ON 3건. 실제 seed를 확정하지 않은 채 fallback으로 통과시키지 않았습니다.

**ENVIRONMENT_LIMITATION:** 실제 DuckDB 엔진 및 crash rollback, Windows MT5 handle 생성·첫 buffer·원본 다운로드·LIVE publication, 실제 원본 SPECIAL1~7 OFF/ON Alert 동등성, 정확한 지정 pandas 버전 환경. 건너뜀 6건은 PASS에 포함하지 않습니다.

기준 작업본의 Part2 메인 ON 연결은 여전히 SPECIAL SIGNAL/Alert 범위이며 TRADE/LIVE_PARITY와 임의 파라미터/세션 전달의 완성은 이번 수정 범위에서 해결되지 않았습니다. 장기 성능·실제 브로커 시간 경계도 미검증입니다.
