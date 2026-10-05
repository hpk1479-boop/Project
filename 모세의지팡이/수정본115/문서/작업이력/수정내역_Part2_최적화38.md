# 수정본38 — Part2 잔여 최적화 수정·검증 보고서

작성일: 2026-09-29  
작업 원본: 사용자가 제공한 **수정본37.zip**  
납품 형태: 패치가 아닌 **수정본38 전체 프로젝트**

## 1. 최종 결론

**최종 적용은 3개 최적화이며, 비교한 입력에서 알림 결과의 차이는 0건입니다.** 구독 객체 재사용, Composer의 고정 식별자 캐시, 이미 표준화한 Fact의 중복 변환 제거를 적용했습니다. Delta 재조립 후보는 성능 개선이 일관되지 않아 제외했습니다. BoardView 공유·가변 Delta 버퍼·워커 재분할은 적용하지 않았습니다.

최종 구성으로 **일반 재생 12회 + 전체 발행 출력 추적 6회 = 18회**를 실행했고, 모두 첫 시도에 완료했습니다. SPECIAL1과 ALL 각각 9개 CSV가 수정본37과 **바이트 단위로 일치**했습니다. 동일 입력의 인메모리 LIVE 수신 경로도 재생 경로와 일치했습니다.

**실제 1주 검증은 수행하지 않았습니다.** 수정본37에는 비교용 녹화가 없었으며, 앞서 제공된 수정본36 압축의 검증 자료에서 실제 하루 녹화만 추출했습니다. 수정본36의 운영 코드는 복사·복원하지 않았습니다. 이전 거래일 입력이 없어 비교 실행에만 `overlap_trading_days=0`을 사용했습니다. 운영 기본값은 변경하지 않았습니다.

## 2. 단계별 적용

### 1단계 — 완전한 구독 내용에 따른 불변 Subscriptions 재사용

`event_engine/subscription_cache.py`를 추가하고 11개 구독 메서드에서 사용합니다. 캐시 키에는 symbols, timeframes, facts, processor_states, kinds, resolution, boundaries 전체가 들어갑니다. 호출 시점의 심볼·WATCH 선택에서 키를 다시 만들기 때문에 선택 변경 시 이전 구독을 잘못 재사용하지 않습니다.

구독 객체는 원래의 불변 `Subscriptions`입니다. 캐시는 최대 256개입니다. 해시 불가능한 향후 구독 정보는 원래 생성 방식으로 처리합니다. 소비자 수락 조건, 이벤트 순서, 상태의 구독 권한은 바꾸지 않았습니다.

단계 검사: **18개 통과**.

### 2단계 — Composer 고정 식별자만 캐시

`event_composer_domain.py`에서 `_sweep_subscription_payload()`의 watch_id 계산과 `_condition_source_binding()`의 fact_scope 문자열 계산만 캐시합니다. 현재 심볼·TF·레벨·ATR 기간/배수·런던/뉴욕 설정을 매번 읽고, 그 전체 값으로 키를 구성합니다. 각 캐시는 최대 4,096개이며 숫자 타입도 키에서 구분합니다.

페이로드 딕셔너리와 levels 목록은 여전히 새로 생성합니다. `_source_bindings`는 매번 최신 것을 조회합니다. **health·epoch·age·usable·전략 평가 결과는 캐시하지 않습니다.** 세션 설정·spec·binding 교체와 현재 health 변화에 따라 판정이 달라지는 테스트를 포함했습니다.

단계 누적 검사: **35개 통과**.

### 3단계 — 최초 표준 변환과 불변화를 유지한 Fact 중복 변환 제거

`emit_facts()`에 기본값이 False인 `prepared` 옵션을 추가했습니다. FactPort.send 또는 OZ Collector에서 이미 `plain()` 변환을 마친 FVG·SWEEP·OZ·TREND·WATCH 경로만 `prepared=True`로 전달합니다. 일반 호출자는 기존 동작을 그대로 유지합니다.

최초 `plain()` 및 `Signal.freeze()`는 제거하지 않았습니다. 가변 표준 데이터는 Signal에서 계속 독립적으로 불변화되고, Board가 이미 불변화한 Fact만 안전하게 재사용합니다. 발행 후 원본 데이터가 변경되어도 과거 출력이 바뀌지 않는지 검사했습니다.

단계 누적 검사: **48개 통과**.

### 4단계 — Delta 후보 검사 후 제외

가변 버퍼 공유가 아니라 최종 불변 bytes 조립 과정의 중간 복사만 줄이는 후보를 시험했습니다. 실제 1,378개 번들의 복원 바이트와 6개 신규 codec 검사는 일치했습니다.

격리 측정 첫 결과는 **0.248402초 → 0.275103초, 10.75% 느려짐**이었고, 재측정은 **0.264919초 → 0.258548초, 2.40% 빨라짐**이었습니다. 일관된 시간 개선이 확인되지 않아 **해당 변경을 수정본37 코드로 되돌리고 최종본에서 제외**했습니다. 최종 `Part2/event_backtest/delta.py`는 수정본37과 동일합니다.

Delta 기본 동작의 6개 회귀검사는 대조 검사로 남겨 최종 신규 검사는 **54개 모두 통과**했습니다. 후보 소스·diff·두 측정 결과는 `excluded/delta/`에만 보관합니다.

## 3. 실제 입력 재생 — 최종 구성만 집계

입력: XAUUSD+ / BAR / 2026-09-01 UTC / MARKET_BUNDLE **1,378개**. 요청 구간은 2026-09-01 이상, 2026-09-02 미만입니다. 기록된 첫 번들 시각은 1788224400000ms, 마지막은 1788307020000ms입니다.

각 전략에 대해 원본·수정본을 번갈아 3회씩 실행했습니다. 아래 시간은 **실제 워커 재생 시간의 중앙값**이며, DB 준비·계획·부모 병합과 프로세스 시작 시간은 포함하지 않습니다. 타이밍 실행은 profiler·출력 추적을 끄고 측정했습니다.

| 선택 | 수정본37 | 수정본38 | 중앙값 감소율 | 알림 CSV |
|---|---:|---:|---:|---|
| SPECIAL1 | 28.895361초 | 24.391846초 | 15.59% | 1행, 9개 CSV 전체 일치 |
| ALL | 90.895840초 | 81.735217초 | 10.08% | 2행, 9개 CSV 전체 일치 |

### 3회 원시 시간 표본

| 선택 | 수정본37 재생 시간(초) | 수정본38 재생 시간(초) |
|---|---|---|
| SPECIAL1 | 28.895361, 25.054004, 28.953062 | 28.338355, 23.994073, 24.391846 |
| ALL | 93.884031, 90.895840, 90.766484 | 83.650421, 81.133659, 81.735217 |

Linux / Python 3.13.5 / 단일 워커 / 타이밍 CPU affinity 0에서 실행했습니다. 작업 일부 구간에는 별도 CPU 2에서 추적 검사가 동시에 진행됐습니다. 전용 유휴 장비에서 측정한 수치는 아니며, 실행 순서를 번갈아 배치하고 3회 원시 표본을 모두 공개했습니다. 이 수치는 해당 환경의 관측값이고 다른 PC·기간의 단축률을 보장하지 않습니다. 이전 수정본37 보고서의 절댓값과 이번 절댓값을 서로 직접 비교하지 않습니다.

### 메모리 관측값

워커에서 관측한 최대 RSS의 3회 중앙값입니다. 시간 개선과 함께 메모리 증가도 기록합니다.

| 선택 | 수정본37 | 수정본38 | 차이 |
|---|---:|---:|---:|
| SPECIAL1 | 352.07 MiB | 362.59 MiB | +10.53 MiB |
| ALL | 255.58 MiB | 300.45 MiB | +44.87 MiB |

구독 캐시는 256개, 식별자 캐시 두 개는 각각 4,096개로 제한되어 있습니다. 위 RSS 증가가 모두 캐시 때문이라고 단정하지 않으며, 장기·다중 워커 최대 메모리는 이번 범위에서 측정하지 않았습니다.

## 4. 결과 동일성

CSV는 알림 개수만 비교하지 않았습니다. 동일한 고정 run_id로 실행하고 헤더·행 순서·모든 필드를 포함한 파일 전체를 비교했습니다. 신호 시각, 등급, 방향, 메시지, 수신자, signal_id, B0 가격/시각 등도 비교에 포함됩니다.

| 선택 | 알림 행 수 | 비교 CSV | 전체 발행 출력 | 비교 결과 |
|---|---:|---:|---:|---|
| SPECIAL1 | 1 | 9개 | 5,527개 | 원본 재생 = 최종 재생 = 최종 인메모리 LIVE |
| ALL | 2 | 9개 | 35,705개 | 원본 재생 = 최종 재생 = 최종 인메모리 LIVE |

추적은 전략명·논리 이벤트 순번·source_time·출력 종류·출력 내용을 JSON으로 표준화해 **발행 순서대로** SHA-256에 누적했습니다. 내부 출력에는 Fact, WATCH 명령, 타이머 요청, 알림이 포함됩니다. 6개 추적 실행에서 전략 오류는 0건입니다. 내부 상태 전체의 메모리 덤프를 비교했다는 의미는 아닙니다.

| 선택 | CSV SHA-256 | 발행 출력 SHA-256 |
|---|---|---|
| SPECIAL1 | `bba64778e631902f908b8ad5bd4bc7b432acb6d3817f79d5817e549eb99e4f0a` | `4386c9668935711c5268d9d11b95ae53ecf354eb6ea743e1870e5b53669af8f3` |
| ALL | `9de9ba6fb3a0bfd43e430db84f2edcb10a10a96f30367bff4acb20d7b4a0cae7` | `5dccdf07400a6efec0b264034bb313ae346da6ab881c749171ed1b0b079afce6` |

인메모리 LIVE 검사는 동일 Wire 데이터를 BytesIO → PipeReceiver → 실제 STAFF/이벤트 엔진으로 전달한 검사입니다. 실제 Windows named pipe·MT5 프로세스·네트워크·실시간 외부 서비스 검사를 완료했다는 뜻은 아닙니다.

## 5. 항목별 하위 작업 시간

같은 프로세스에서 변경 전후 함수를 번갈아 7회 측정한 중앙값입니다. 최종에 남긴 작업만 표에 넣었습니다. 원본 함수는 수정본37 실제 소스에서 로드했습니다. 16개 중첩 fact를 포함하는 합성 입력을 Fact 미세 측정에 사용했습니다.

| 항목 | 1회 배치 작업량 | 변경 전 | 변경 후 | 감소율 |
|---|---:|---:|---:|---:|
| 구독 객체 재사용 | 100,000회 | 0.304910초 | 0.070688초 | 76.82% |
| Composer 조건 식별자 | 60,000회 | 0.336190초 | 0.035973초 | 89.30% |
| 변환 완료한 가변 Fact의 발행 | 2,000회 | 1.038844초 | 0.593707초 | 42.85% |
| 이미 불변화된 Fact의 발행 | 2,000회 | 1.106301초 | 0.007646초 | 99.31% |

첫 측정 스크립트의 지연 타입 주석 추출 오류를 수정 후 재시도했습니다. 같은 CPU의 다른 테스트와 겹친 측정은 구분 보관하고, 위 표는 별도 CPU 3에서 다시 측정한 값입니다. 하위 작업의 감소율을 전체 백테스트 감소율로 해석하거나 서로 더하면 안 됩니다.

## 6. 회귀검사와 재시도

| 구분 | 수정본37 | 최종 수정본38 | 판정 |
|---|---:|---:|---|
| 현재 엔진 기존 365개 | 353 통과 / 12 실패 | 353 통과 / 12 실패 | 실패 항목 동일, 신규 실패 0 |
| 신규 검사 54개 | 대상 아님 | 54 통과 / 0 실패 | 통과 |
| 과거 live_replay 계열 | 12개 수집 오류 | 12개 수집 오류 | 현재 없는 과거 모듈 의존, 양쪽 재시도 후 별도 기록 |

기존 365개 검사는 원본과 최종본을 각각 재시도했고 같은 실패가 유지되었습니다. 실패를 통과로 바꾸거나 이전 모듈을 복원하지 않았습니다. 일부 실패는 환경상 실행 불가, 일부는 기존 테스트의 현재 구현과 맞지 않는 전제이며 아래에 원인을 그대로 기록합니다.

| 실패 항목 | 최종 실행 첫 오류 줄 |
|---|---|
| `test_engine_optimization::test_part3_is_not_an_execution_dependency` | AssertionError: assert not True |
| `test_event_perf1::test_bundle_cache_copies_features_attrs_and_invalidates` | AttributeError: 'types.SimpleNamespace' object has no attribute 'fact' |
| `test_event_e2_domains::test_sweep_canonical_transitions_and_resume` | AttributeError: 'types.SimpleNamespace' object has no attribute 'fact' |
| `test_event_e1::test_native_windows_named_pipe_live_vs_replay` | AttributeError: module 'ctypes' has no attribute 'WinDLL' |
| `test_event_e1::test_bar_close_fact_ignores_forming_updates_until_new_bar` | assert 2 == 1 |
| `test_event_e2_oz_pilot::test_first_oz_state_matches_entire_frozen_module_and_midnight_checkpoint` | KeyError: 'wonbi_sigma' |
| `test_event_e3::test_native_export_columns_and_subsecond_archive[1]` | ModuleNotFoundError: No module named 'duckdb' |
| `test_event_e3::test_native_export_columns_and_subsecond_archive[2]` | ModuleNotFoundError: No module named 'duckdb' |
| `test_data_selection::test_result_csv_destination_and_rows_are_not_query_parameters` | RuntimeError: DuckDB is unavailable: database integration is NOT being tested |
| `test_data_selection::test_public_runner_final_export_stays_in_run_directory` | RuntimeError: DuckDB is unavailable: database integration is NOT being tested |
| `test_data_selection::test_build_replace_only_after_verification_and_restore` | RuntimeError: DuckDB is unavailable: database integration is NOT being tested |
| `test_parallel_oz::test_reused_worker_moves_write_boundary_before_creating_next_job` | AssertionError: Traceback (most recent call last): |

### 막힌 단계와 처리

| 단계 | 첫 문제 | 재시도/처리 | 최종 상태 |
|---|---|---|---|
| DuckDB 의존성 | 설치 가능한 패키지를 확보하지 못함 | 기본 설치 후 명시적 PyPI 설치 재시도 | 두 번 실패. 부모 SQL 계획·병합 미실행 |
| 최초 원본 재생 | 출력이 창고 밖이라 상대 경로 규칙 위반 | 검증 출력만 창고 하위로 이동 | 성공, 운영 코드 변경 없음 |
| 구모듈 검사 수집 | 현재 없는 live_replay import | 원본·수정본 모두 다시 실행 | 동일 오류. 과거 코드 복원 없이 다음 검사 진행 |
| 미세 측정 | AST 추출 함수의 지연 주석 처리 누락 | 검증 스크립트에 future annotations 적용 | 재시도 성공 |
| 미세 측정 부하 | 다른 검사와 CPU가 겹침 | 별도 CPU에서 다시 측정 | 분리된 측정값 사용 |
| Delta 성능 | 첫 측정에서 느려짐 | 동일성 확인 후 성능 재측정 | 개선 불일치로 후보 제외 |
| 최종 18회 재생 | 실패 없음 | 재시도 불필요 | 모두 완료 |

DuckDB가 없는 검증 프로세스는 수정본37에 이미 포함된 `build/optimization37/support.py`의 검증 로더를 사용했습니다. 미사용 DuckDB import만 검사 프로세스 안에서 분리하고 실제 run_chunk·ResultWriter 본문을 실행했으며, Warehouse SQL 생성은 명시적으로 거절합니다. 운영 소스에 우회 기능을 넣지 않았습니다.

## 7. 변경 범위와 무결성

운영 Python 파일 **10개(기존 9개 수정 + 신규 1개)**이며 모두 Part1 공용 이벤트 엔진/Composer에 속합니다. 해당 경로는 Part2 재생에서도 사용됩니다. **최종 Part2 전체 파일은 수정본37과 동일합니다.**

```text
Part1/program/event_composer_domain.py
Part1/program/event_engine/composition_consumer.py
Part1/program/event_engine/domain_support.py
Part1/program/event_engine/fvg_state.py
Part1/program/event_engine/indicator_consumer.py
Part1/program/event_engine/oz_processor.py
Part1/program/event_engine/oz_state.py
Part1/program/event_engine/subscription_cache.py
Part1/program/event_engine/sweep_state.py
Part1/program/event_engine/watch_consumer.py
```

수정본37의 ResultWriter·파일 해시 캐시·워커 관리 루프·STAFF 열 인덱스 최적화는 그대로 유지했습니다. SPECIAL1~7 전략, config, EA/Wire, BoardView, freeze, 워밍업 기본값, 650봉, 타이머 처리, 기간 분할, Part3를 변경하지 않았습니다.

Part1 변경 이력을 `Part1/audit/remediation/71-part2-optimization38`에 등록하고 `build/part1_immutable_sha256.json`을 갱신했습니다. 최종 인벤토리 **265개 항목의 해시 불일치 0건**, 이번 변경으로 추가된 소스 이력 진단 **0건**입니다. 기존에 있던 소스 이력 진단 6건은 임의 승인하거나 지우지 않고 유지했습니다.

원본 보존 검사에서 수정본37 압축의 원본 파일 1,272개와 작업용 원본 복사본을 대조해 변경·누락 0건을 확인했습니다. 원본 압축 SHA-256은 `f9bf6cc2487c29e5a5e7a2980e3a68f76b77fea293f18ade02a4f4d7f65a8d03`입니다.

## 8. 제외한 변경

| 후보 | 제외 이유 |
|---|---|
| Delta 중간 bytes 조립 축소 | 결과는 같았으나 반복 측정에서 일관된 시간 개선 없음 |
| 가변 Delta 출력 버퍼 공유 | 다음 입력이 이전 Snapshot을 바꿀 위험 |
| BoardView/health 맵 공유 | reconnect·제자리 갱신에서 독립 스냅샷 의미를 바꿀 위험 |
| 워커 수·기간 재분할 | 초기화·워밍업 경계와 상태 누적에 영향, 실제 1주/경계 입력 부족 |
| 650봉·워밍업 축소, 이벤트/타이머 생략 | 전략 결과에 영향을 줄 수 있어 적용하지 않음 |
| CRC·FULL/ROW·health 검사 제거 | 입력 및 상태 안전성 저하 위험 |

## 9. 포함 자료와 실행 범위

`검증결과/part2_optimization38`는 이번 수정본에서 새로 생성한 자료만 포함합니다. 이전 수정본의 검증결과 폴더는 복사하지 않았습니다. 실패/재시도 로그, 최종 CSV, 전체 출력 해시, 원시 시간 표본, 회귀검사 XML, 변경 diff, 무결성 결과, 제외 후보 기록을 포함합니다. 재생 입력 녹화는 기존 첨부에서 가져온 검증 입력이며 새 코드로 합성하거나 늘리지 않았습니다.

최종 자료는 `final_comparison.json`, `final_regression_comparison.json`, `final_release_audit.json`, `measurements/retained_measurements.json`에서 확인합니다. `candidate_replay`와 `excluded/delta`는 **제외한 중간 후보**의 참고 기록입니다. 최종 통계에 섞지 않았습니다. 테스트가 생성한 임시 fixture 복제본과 캐시는 포장 대상에서 제외했으며 실행 로그·XML·검증 결과는 남겼습니다.

검증 도구와 재실행 예시는 `build/optimization38/README.md`에 있습니다. 1주 실제 입력 및 필요한 워밍업을 갖춘 환경의 후속 검사용으로 기존 `build/optimization37/verify_week.py`를 보존했습니다. 이번에는 **실제 1주·정상 부모 DuckDB 경로·Windows/MT5 네이티브 실행은 미검증**입니다. 확인한 입력과 단위 검사 범위를 넘어서 모든 미래 입력의 동일성을 보장한 것으로 해석하지 않습니다.
