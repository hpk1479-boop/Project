# 수정본12 — S5 STAFF 계산 제거

수정본11(S4 완료) 전체를 독립 복사하고 원본 15,991개 파일의 최초 복사 SHA256을 확인했다. 최종 원본 전체 불변 판정은 **False**이다. 작업 중 원본의 실행 로그·상태/pyc가 변경되어 전체 불변 검사는 실패로 보존했다. 소스 및 복사본 보호 파일의 불변 판정은 True이다. S0–S4 증거·골든·expected·성능 정책과 Part3는 그대로다. **S5만 진행했고 S6 이후는 시작하지 않았다.**

S5 구현은 반영했지만 다음 게이트가 실패해 검증 완료로 판정하지 않았다: original_and_goldens_frozen, G1_baseline_signature, existing_14_comparisons, performance_S5.

## 구현 범위 및 제거 목록

- STAFF의 `add_wonbi_features`, `apply_requested_features`, `validate_mt5_snapshot` DataFrame 검증 함수와 파생 계산 소유 모듈 import를 제거했다. ATR·EMA 파생·zone/regime/slope·SuperTrend·FVG·원비 계산은 기존 클라이언트 소유 함수 그대로다.
- StaffPipeCache의 `get`, `_legacy_frame`, publication별 DataFrame·frame_lock 캐시를 제거했다. 수신 스레드, numpy 파싱·검증, 불변 Snapshot, seq/epoch/validity/stale 및 공개 continuation 경계는 유지했다.
- DataServer의 legacy 데이터 준비·계산·Watch MA 이력·파생 열 검증 경로와 `_watch_ma_features`를 제거했다. legacy pickle 데이터 요청은 `SNAPSHOT_API_REQUIRED`와 **SNAPSHOT API 사용** 명시 오류를 반환한다. PING/SOURCE_HEALTH/SET_WONBI_SIGMA는 유지했다.
- STAFF는 sigma 상태만 보관하고 SNAPSHOT 헤더로 전달한다. staff_compat의 원비 계산식과 나머지 클라이언트 계산 코드는 바이트 단위로 불변이다.
- STAFF는 pandas를 import하지 않는다. staff_schema의 pandas import는 클라이언트 전용 legacy_frame 함수 안으로 옮겼다. 정규화 연산·열 순서·dtype·attrs는 동일하다. pandas와 파생 모듈 import를 차단한 독립 프로세스에서 STAFF 로드·v1 수신·SNAPSHOT 전달까지 검증한다.
- Part2 event_catalog의 STAFF 파생 함수 별칭을 기존 Fact 소유자에 직접 연결했다. continuation 검사용 DataFrame은 공개 Snapshot과 클라이언트 정규화 함수에서 만든다. 전략·매니저·SPECIAL·EA/wire v1 45열을 변경하지 않았다.
- S4의 SNAPSHOT 경로에는 feed 재수신 시 대기 로그 상태를 비우는 동작이 빠져 있었다. 원래 legacy 경로의 복구 로그·throttle 해제만 SNAPSHOT에 보존했다. 준비 상태·오류 응답·지표 판정·epoch 규칙은 변경하지 않았다.

## 변경 파일

- `Part1/audit/harness.py`
- `Part1/audit/test_ack_pressure.py`
- `Part1/audit/test_baseline.py`
- `Part1/audit/test_oz_trigger_profiles.py`
- `Part1/audit/test_slow_feed.py`
- `Part1/program/staff_schema.py`
- `Part1/program/THE STAFF OF MOSES.py`
- `Part1/watch_ma_validation/test_live_integration.py`
- `Part2/live_replay/event_catalog.py`
- `Part2/part1_host/runtime.py`
- `Part2/staff_golden/actual.py`
- `Part2/staff_golden/benchmark.py`
- `Part2/staff_golden/record.py`
- `Part2/validation_suite/test_staff_s1.py`
- `Part2/validation_suite/test_staff_s2.py`
- `Part2/validation_suite/test_staff_s3.py`
- `Part2/validation_suite/test_staff_s4.py`
- `Part2/validation_suite/test_staff_s5.py`
- `Part2/pit/adapters/legacy_staff.py`
- `tests/live_parity/test_observation_replay.py`
- `tests/sparse_events/test_events.py`
- `tests/sparse_events/test_event_catalog.py`

Part1 audit 전송·테스트 helper와 아래 기존 테스트도 경계를 이전했다. S5 전용 build 도구와 증거만 추가했고 이전 단계 증거는 갱신하지 않았다.

## legacy 호출부 검색 및 정적 검사

제거 전 수정본11의 Part1/Part2 전체 Python 소스를 검색해 115개 후보 행을 보존했다. Part3는 동결 대상이며 검색·이전 대상이 아니다. `legacy_callers_before.json`, `legacy_callers_review.json`에 파일·행·원문·분류·조치를 모두 기록했다.

- 실제 Part1 데이터 클라이언트는 S4에서 SNAPSHOT으로 이전되어 있었다. 남은 send_pyobj는 manager_KIM의 sigma/health 제어, 전략 이벤트 발송, 서버/manager 응답이다.
- Part2 record/actual/benchmark의 직접 legacy 데이터 검증 요청을 공개 `staff_data_request`로 이전했다. 이 함수는 SnapshotClient 디코드와 StaffCompat 계산까지 포함한다. health 요청 및 오프라인 제어 전송은 그대로다.
- Part2 event_catalog의 계산 별칭·cache.get 경로를 위 공개 경계로 이전했다.
- 사전 검색 목록에 포함됐던 pit/adapters/legacy_staff.py 후보의 구형 프로토타입 분류를 놓쳤고, 최종 전체 AST 검사가 남은 server.handle 호출을 검출했다. 이미 삭제된 replay 패키지에 의존해 import부터 불가능했고 현재 호출부도 없는 코드다. 기존 loader/서버 호출을 제거하고 생성/요청 시 E_SNAPSHOT_API_REQUIRED와 SNAPSHOT API 사용 명시 오류로 차단했다. 동작 중인 클라이언트 계산이나 PIT 계산식은 변경하지 않았다. 정적 검사에 예외를 추가하지 않았고 이 생성자 거부 assertion을 추가했다. 해당 정적 검사 1개만 재실행해 통과했으며 최초 전체 실패와 targeted overlay를 모두 보존했다.
- audit 및 S2–S4 테스트의 legacy 데이터 호출·캐시 조회는 새 클라이언트 경계로 옮겼다. immutable audit fixtures는 실행 중인 데이터 호출부가 아니며 그대로 보존했다.
- Part1/Part2 정적 AST 검사가 legacy 데이터 발송 부재, STAFF의 파생 모듈 import 부재, 기존 계산 소유자·EA·전략 파일 불변을 확인한다. 제어·이벤트 경로를 데이터 요청으로 오인해 삭제하지 않았다.

## 기존 테스트 변경 근거와 검사 보존

- `tests/live_parity/test_observation_replay.py`
- `tests/sparse_events/test_events.py`
- `tests/sparse_events/test_event_catalog.py`
- `Part2/validation_suite/test_staff_s1.py`
- `Part2/validation_suite/test_staff_s2.py`
- `Part2/validation_suite/test_staff_s3.py`
- `Part2/validation_suite/test_staff_s4.py`
- `Part1/audit/test_ack_pressure.py`
- `Part1/audit/test_baseline.py`
- `Part1/audit/test_oz_trigger_profiles.py`
- `Part1/audit/test_slow_feed.py`
- `Part1/watch_ma_validation/test_live_integration.py`

- S1은 원비/apply_requested_features AST 및 소유자 객체 검사를 staff_compat로 옮겼다. 기존 S0 AST, ATR 두 정의 분리 및 실제/합성 ATR 골든 수치 검사는 유지했다.
- S2는 같은 publication의 1회 DataFrame 생성·복사 격리·느린 소비자·수신 중 동시 요청·실제 Windows pipe·재연결·seq 재시작 검사를 클라이언트 캐시에서 수행한다. 원본의 검사 개수와 test ID를 유지했다.
- S3/S4는 **수정본11 Part1/program 전체**를 별도 host가 복사·로드한 oracle로 비교한다. 옛 STAFF에 새 소유 모듈을 섞지 않는다. 600개 무작위 요청, 오류·준비 상태, MA 이력, 값·dtype·열 순서·attrs·복사 격리 검사를 유지한다. 현재 pickle 데이터 요청의 명시 오류와 실제 ZMQ SNAPSHOT 응답 비교를 함께 검사한다.
- S4의 STAFF 전체 파일 불변 검사는 S5에 허용된 제거/변경 노드만 제외한 메서드 AST 정확 비교와 snapshot 검증 연산 정확 비교로 바꿨다. 계산식·Watch·SPECIAL 불변 검사는 유지했다.
- audit/루트 테스트는 raw DataFrame 조회와 원비 기준 함수의 소유 위치만 바꿨다. 기존 수치·미준비 예외·throttle·회복·copy·epoch assertion을 유지했다.
- test_oz_fvg_optimization.py는 바이트 불변이며 **전후 6 + LIVE↔BACKTEST 6 + 시나리오 1 + 성능 1 = 14개** 검사를 그대로 수집했다. 성능 실패가 있으면 그 검사도 실패로 남긴다.

개발 중 관련 검사만 실행했다. 새 테스트의 pytest 예약 매개변수명 오류, Windows 전역 pytest 임시 폴더 접근 거부, 제거된 STAFF 함수를 참조하던 루트 테스트 6개를 각각 기록하고 해당 경계/경로만 수정했다. 최종 G1/G2/G3/성능은 각 1회 실행하며 진단용 재실행은 별도 원본 로그와 함께 기록한다.

## 게이트 결과

| 게이트 | 결과 |
|---|---|
| original_and_goldens_frozen | 실패 |
| original_source_and_protected_evidence_unchanged | 통과 |
| G1_baseline_signature | 실패 |
| G1_Part2_816_preserved | 통과 |
| G2_synthetic | 통과 |
| G2_actual_MT5 | 통과 |
| G2_unavailable | 통과 |
| G2_actual_OZ_4302 | 통과 |
| G3_240_and_S0 | 통과 |
| new_S5_tests | 통과 |
| integrity_chain | 통과 |
| immutable_inventory | 통과 |
| scope_only_S5 | 통과 |
| existing_14_comparisons | 실패 |
| Part3_legacy_frozen | 통과 |
| wonbi_and_client_formulas_unchanged | 통과 |
| performance_S5 | 실패 |

G2는 SNAPSHOT JSON/배열 → 클라이언트 디코드/정규화 → staff_compat 결과를 기록하고 동결 S0 SQLite 파일 SHA부터 비교했다. 합성 33,996 요청, 실제 4,542 요청 및 실제 미준비 응답 목록을 포함한다. 실제 OZ 전용 경로의 4,302개 고유 프레임 추가 비교도 유지했다. S0 골든을 생성하거나 expected를 갱신하지 않았다.

G3는 240초 전 LIVE / 후 LIVE / 후 BACKTEST를 한 번 실행하고 S0와 비교한다. BEFORE는 수정본6 전체 program 동결본이다. OZ set/frozenset 순서 규칙은 S1과 동일하다.

| G1 그룹 | 수집 | 결과 | 기존 signature 차이 |
|---|---:|---|---:|
| part2 | 824 | PASSED 777, ERROR 21, SKIPPED 20, FAILED 6 | 2 |
| watch_ma | 188 | PASSED 187, SKIPPED 1 | 0 |
| root | 279 | ERROR 1, PASSED 267, SKIPPED 6, FAILED 5 | 0 |
| Part1 audit | 177 | failures 1, errors 0 | 0 |

Part2 기존 816개 ID를 모두 유지했는지는 part2_collection_scope.json에 명시한다. 기존 기준선 실패와 새 차이는 baseline_signature_compare.json에 별도로 보존한다.

최종 Part2 수집은 **824개(기존 816 + S5 신규 8)**이며 빠진 ID는 없다. 신규 S5 8개는 모두 통과했다. 기존 signature와 남은 차이는 성능 fixture의 PASSED→ERROR, 그리고 test_mt5_plan_runs_strategy_tester_before_history_prepare의 기존 SPECIAL 경로 ERROR→Tk 환경 SKIPPED 두 건이다. 후자는 기존 fixture의 tk.TclError 처리 분기가 실행됐고, tk8.6/ttk/notebook.tcl을 찾지 못한 사유가 JUnit에 남아 있다. 이 테스트 소스와 assertion은 바꾸지 않았으며, 새 예외 허용이나 통과 판정을 추가하지 않았다. 따라서 G1 signature는 실패로 유지한다. 나머지 기존 테스트 상태·원인과 audit/root signature는 동일하다.

## 동결 성능 게이트

측정 전 `성능비교경계_STAFF_S5.md`에 request_0~3의 전체 클라이언트 비용 경계를 고정했다. S0는 legacy 요청+서버 계산, S5는 SNAPSHOT 생성/전달+디코드+정규화+staff_compat+최종 복사 비용이다. 양쪽 모두 원래 측정과 동일한 host 내 직접 전송을 사용한다. 서버 비용만 비교하지 않는다.

동결 protocol.py의 worker 루프·횟수·warmup·GC를 그대로 사용하고, 별도 어댑터가 S0에 전체 동결 BEFORE host를 선택하며 S5 요청 함수만 최종 DataFrame 경계로 연결한다. 정책·표본 수·산식·허용치는 불변이다. 동일 논리 CPU에 고정한 S0→S5 5쌍 전체 표본을 보존했다. 다른 테스트와 겹쳐 측정하지 않았다.

| 작업 | S0 CPU 중앙값(초) | S5 CPU 중앙값(초) | S5/S0 | 허용치 | S0/교정 중앙값 | 판정 |
|---|---:|---:|---:|---:|---:|---|
| parser_650_rows | 1.968750 | 0.093750 | 0.0476 | 1.05 | 0.9618 | 통과 |
| request_0 | 0.375000 | 0.437500 | 1.1667 | 1.15 | 0.8889 | 실패 |
| request_1 | 2.531250 | 2.546875 | 1.0062 | 1.09 | 0.9310 | 통과 |
| request_2 | 3.093750 | 3.140625 | 1.0152 | 1.06 | 0.9296 | 실패 |
| request_3 | 3.671875 | 3.625000 | 0.9872 | 1.09 | 0.9438 | 통과 |
| oz_fvg_live_seconds | 71.437500 | 59.359375 | 0.8309 | 1.06 | 0.9631 | 통과 |

환경 필드 일치: True. 최종 성능 판정: **FAIL**.
후보 비율 초과: ['request_0']. S0 자체 범위 이탈: ['request_2'].
실패 시 허용치를 완화하거나 표본을 다시 골라 통과시키지 않았다. S0 자체 범위 이탈은 후보 코드 속도 비율과 별개의 환경 유효성 실패다. 새 성능 게이트 실패는 G1의 기존 성능 검사에도 그대로 반영되며 기준선 결함으로 숨기지 않는다.

같은 세션의 전체 5회 관측 범위(제외 표본 없음):

- parser_650_rows: S0 1.750000~2.312500초, S5 0.093750~0.125000초
- request_0: S0 0.375000~0.453125초, S5 0.375000~0.468750초
- request_1: S0 2.234375~2.828125초, S5 2.468750~2.593750초
- request_2: S0 2.906250~3.140625초, S5 3.078125~3.203125초
- request_3: S0 3.375000~3.718750초, S5 3.578125~3.765625초
- oz_fvg_live_seconds: S0 68.171875~75.781250초, S5 55.187500~60.781250초

### 실패 원인 분석

request_0의 공식 표본은 S0 중앙값 0.375초, S5 중앙값 0.4375초/250회였다. 추가 비용은 회당 약 0.25ms이며 1.15 허용치에 해당하는 0.43125초를 0.00625초 초과했다. 이를 반올림하거나 허용치를 바꿔 통과시키지 않았다.

실패한 request_0만 진단용 cProfile로 각 250회 관찰했다. 새 경로는 매 요청 JSON/3개 배열 전달·decode·클라이언트 frame 복사를 수행한다. 프로파일에서 250회 SnapshotClient.request 약 0.07초, frame 약 0.04초의 경로 비용이 확인되었고, ATR/원비 계산은 여전히 요청마다 250회다. 서버 계산을 없애도 최종 DataFrame 전체 비용이 모두 없어지는 것은 아니다. 이 수치는 프로파일러 오버헤드가 있는 진단값이며 공식 성능 표본을 대체하지 않는다. 진단 실행의 총시간은 공식 비율을 재현하지 않았으므로 전체 16.67% 증가를 특정 함수 하나의 회귀로 단정하지 않는다. 동결 S0의 세션 내 변동과 전송/디코드 고정비를 분리해 확정하려면 별도 측정 판단이 필요하다.

request_2는 S0 중앙값 3.09375초가 동결 허용 구간 3.139740566~3.5278125초 밖에 있어 환경 유효성에서 실패했다. 프로그램 해시·버전·CPU affinity는 일치했지만 교정 당시 CPU 시간 분포와 같다고 볼 수 없었다. CPU 주파수/스케줄러 등 구체적 외부 원인은 이번 증거로 확정하지 못했다. 성능 정책 변경, 공식 표본 재선정, 추가 5쌍 게이트 재실행은 하지 않았다. 근거는 performance_failure_analysis.json과 request0_failure_profile.json이다.

## 무결성·기존 결함·인계

`27-staff-s5-server-calculation-removal`에 STAFF와 staff_schema 변경을 등록했다. 기존 무결성 진단 23개는 유지하고 새 진단과 broken chain 여부를 별도로 검사했다. 현재 Part1 불변 목록을 재생성했다. 이전 체인·S0 골든·expected·성능 정책 파일은 변경하지 않았다.

전체 복사 시 pytest current 디렉터리 링크 1,257개 파일이 독립 파일로 실체화됐다. 각 대상이 수정본11 내부 동결 manifest 항목으로 해석되고 대상/복사 파일 SHA가 동일함을 검증했다. 임의 추가 파일을 허용한 것이 아니며 materialized_frozen_aliases.json에 목록을 남겼다.

Part3 전체 해시는 그대로이며 참조 목록만 유지했다. 새로 확인한 이전 경계의 결함은 SNAPSHOT 경로의 대기 로그 해제 누락과, 제거된 STAFF 내부 함수에 결합된 검증 호출들이다. 계산식이나 전략 결정을 바꾸는 방식으로 해결하지 않았다.

추가로 Part2 PIT의 사용되지 않는 legacy 어댑터가 이미 없는 replay 패키지를 import하면서 옛 STAFF 데이터 요청을 남기고 있었다. 전체 정적 검사에서 확인한 뒤 명시적 폐기 오류 경계로 차단했다. baseline_signature_compare.json은 최초 전체 결과에 static_diagnosed.xml의 해당 1개 진단 결과만 합친다. 그 외 기존 실패와 성능 실패는 유지한다.

### 원본 실행 상태 변동

G2 최초 진입 전 불변 검사에서 수정본11의 Part1/logs, Part1/program/logs, pyc 경로 변경을 발견했다. 원본 로그에는 2026-09-26 17:55:52~17:56:15의 서비스 기동/상태 갱신이 남아 있다. 그 실행의 주체는 확인하지 못했고 현재 해당 경로의 Python 프로세스도 조회되지 않았다. 이 작업의 host는 임시 복사본에서 실행하며 공식 worker는 -B 및 PYTHONDONTWRITEBYTECODE를 사용한다. 원본 실행 상태를 임의로 복원·삭제하지 않았다.

최초 G2는 사전 검사에서 종료되어 골든 계산을 실행하지 않았다. 원본 전체 불변 실패를 유지하면서, 소스/골든/보호 파일이 일치하는 경우에 한해 독립 복사본의 결과 검사를 계속하도록 분리했다. 이후 G2 계산은 처음으로 1회 실행했다. 초기 실패 로그와 original_runtime_drift_detected.json을 보존했다. 이 분리는 원본 불변 게이트의 통과나 동결 manifest 재생성을 뜻하지 않는다. final status의 해당 게이트는 실패다.

근거: `검증결과/staff_s5/status.json`, `baseline_signature_compare.json`, `performance/comparison.json`, `frozen_guard.json`.


## 사용자 승인 예외 (S6 착수 전)

사용자는 request_0의 1.1667 > 1.15 초과와 request_2의 S0 환경 범위 이탈을 예외 승인했다. 해당 비용은 폴링 요청 경로의 고정 비용이며, 이후 이벤트 방식에서 이 경로를 폐기할 예정이다. G2·G3 결과 동일성은 통과했고 전체 시나리오 CPU는 약 17% 감소했다.

existing_14_comparisons 및 G1 signature의 성능 실패와 Tk 환경 SKIP, 수정본11의 로그/pyc 변동(소스·골든 불변 확인)도 같은 승인 범위다. S5는 **사용자 승인 예외를 포함한 완료**로 처리한다. 원래 실패 판정·표본·기록은 그대로 보존하며, 성능 정책·허용치는 변경하지 않았다. 승인 전 status.json 원문은 `검증결과/staff_s5/status_before_user_approval.json`, 승인 근거는 `user_approval.json`에 보존했다.
