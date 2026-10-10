# 수정본13 — S6 Wire v2

수정본12 전체를 독립 복사해 S6만 구현했다. 최종 판정: **완료**. S7 이후는 시작하지 않았다. 원본 수정본12 18,853개 파일과 S0–S5 골든·expected·동결 성능 정책·Part3의 해시 불변을 확인했다. 원비와 클라이언트·전략 계산 파일 29개는 수정본12와 같다.

## S5 사용자 승인 예외

복사 전에 수정본12의 S5 보고서와 status.json에 사용자 승인 예외를 기록했다. request_0 1.1667 > 1.15, request_2의 S0 자체 환경 범위 이탈, 이에 따른 existing_14/G1 및 Tk SKIP 차이, 수정본11의 로그/pyc 변동을 승인 범위로 남겼다. 원래 실패 결과·표본·status_before_user_approval.json을 보존했다. 성능 정책과 허용치는 변경하지 않았다. S5 승인을 S6의 새로운 동작 차이에 확대 적용하지 않았다.

## 적용한 검증 정책

작업 중 사용자가 명시한 `검증정책_S6이후.md`를 적용했다. 필수 판정은 SPECIAL/Watch/OZ/FVG 조건·상태 전이, 알림 발생·시각·방향·문구·수신자, source health/stale/reconnect/seq 의미, LIVE↔BACKTEST 동일성이다. 내부 DataFrame 비트·객체/캐시·private 필드·set 순서·구현 형태 차이는 진단이다.

판정 입력은 대표 샘플로 확인했다. 기존 600개 조합 검사는 수집 목록에 남기고 정책상 실행하지 않았다. 기존 4,302개 OZ 전용 내부 프레임 결과와 S0 골든은 그대로 보존했으며, 이번 단계에서 대규모 내부 조합/반복 비교를 다시 생성하지 않았다. G2 판정은 아래 대표 입력과 외부 결과 근거를 사용한다. 전체 결과 파일의 비트 동일성을 통과했다고 주장하지 않는다.

## 구현 내용

- EA 기본값 `InpWireVersion=2`, 버전 1 선택도 지원한다. 기존 45열·지표 계산·v1 pack_wire 바이트는 유지했다.
- `staff_schema.py`가 열 이름 CRC32 레지스트리를 소유하고 `build/generate_staff_wire_schema.py`가 EA 상수를 생성한다. schema_id는 `0x4D2FA0E9`이다.
- Wire v2는 magic/version/seq/schema_id/kind, payload CRC32 trailer를 갖는다. FULL은 최초·새 봉·준비 상태 변화·재연결·과거 행 정정, ROW는 진행 중 마지막 행, HEARTBEAT는 값 변화 없는 생존 신호다.
- HELLO/ACK로 schema_id와 EA 빌드 해시를 교환한다. 같은 심볼의 같은 폴링 회차 TF 변경분을 BUNDLE로 전송한다. 수신기는 전체 CRC 검증 후 한 잠금 안에서 자식 프레임을 순서대로 적용하고, 손상된 묶음은 부분 게시하지 않는다.
- EA는 실패한 관측 묶음을 보존하고 재전송한 뒤 다음 관측을 만든다. 최신 프레임만 남기는 coalescing을 추가하지 않았다. Part2 SecondFeed도 모든 MSP3 레코드를 순서대로 재생한다.
- v1/v2를 함께 받는다. 모르는 schema는 게시하지 않고 SOURCE_HEALTH=UNAVAILABLE로 알린다. 중복·역행 seq 거부, 재연결 seq 재시작, 30초 stale을 검증했다. FULL epoch 판정은 기존 규칙이며 ROW/HEARTBEAT는 epoch를 증가시키지 않는다.
- 피드별 누락 범위·재연결 횟수·수신 지연을 기록한다. 누락은 시각/심볼/TF/누락 seq 범위/연결/빌드 해시를 갖는 append-only JSONL이다. 누락된 가격을 복원했다는 의미는 아니다.
- MSP3는 관측 초·준비 flags·길이·v2 프레임을 저장한다. MSP2도 계속 읽는다. v2 캡처에 새 기록이 없는 초에는 기존 replay 생존 의미를 유지하는 HEARTBEAT를 넣고 seq offset으로 원래 누락 간격을 보존한다.
- STAFF는 pandas·파생 계산 모듈을 import하지 않는다. sigma 전달 및 staff_compat 원비 계산, ZMQ SNAPSHOT/제어 API는 그대로다.

자세한 바이트 배치·경계·누락 기록 형식은 `Wire_v2_명세.md`에 있다. EA 빌드 해시는 `1f94beda8ca91693e556bd7a1f21fa19da288e3b9f70f70435d297c77142b7f5`이다.

## 변경 파일

- `Part1/audit/harness.py`
- `Part1/program/staff_schema.py`
- `Part1/program/THE STAFF OF MOSES.py`
- `Part1/program/MT5/STAFF_Wire_Schema.mqh`
- `Part1/program/MT5/STAFF_Wire_V2.mqh`
- `Part1/program/MT5/THE_STAFF_OF_MOSES.mq5`
- `Part2/generic_backtest/native_mt5.py`
- `Part2/part1_host/capture.py`
- `Part2/part1_host/engine.py`
- `Part2/part1_host/synthetic.py`
- `Part2/part1_host/wire_v2.py`
- `Part2/validation_suite/test_native_data_build.py`
- `Part2/validation_suite/test_staff_s6.py`
- `Part1/program/config.txt`: 사용자가 직접 `STAFF_ALLOWED_SYMBOLS=XAUUSD+,NAS100, BTCUSD`, `TARGET_SYMBOLS=XAUUSD+,NAS100, BTCUSD`로 수정했다. 에이전트는 이 파일을 다시 쓰지 않았다.
- `AGENTS.md.txt`, `검증정책_S6이후.md`, 본 보고서, `인계_STAFF_S6.md`, `Wire_v2_명세.md`, `Part3_레거시_참조목록.md`.
- S6 build 검증 도구·증거와 무결성 변경 단위 28/29, 현재 `build/part1_immutable_sha256.json`.

## 허용 심볼 검증

사용자 설정 파일의 공백을 포함한 원문을 보존했다. 별도 config override 없이 실제 수정본13 설정으로 세 종목을 로드해 STAFF FRESH 응답·자연어 심볼 인식·개인 SMA17 감시 등록을 모두 확인했다. 외부 Telegram 전송은 하지 않고 오프라인 수신 기록을 비교했다. `user_symbol_validation.json`에 설정 해시와 응답을 기록했다.

처음 BTC 시나리오는 이전 기본 설정에서 BTC가 차단되어 명령 등록이 실패했다. 그 0건 실행은 검증 성공에서 제외하고 원본 결과를 남겼다. 수정한 시나리오는 공개 `config_overrides`로 동결 BEFORE와 현재 양쪽에 동일하게 BTC를 허용했다. BEFORE program 전체는 수정하지 않았다. 이 실행은 사용자 파일 수정 전에 시작한 공통 BTC 전용 검증 설정이며, 실제 3종목 설정은 위 별도 검사로 확인했다.

## 외부 동작 비교

각 행은 수정본6의 **Part1/program 전체 동결본**과 현재 LIVE v2, 현재 BACKTEST MSP3를 비교했다. 옛 STAFF만 새 모듈에 섞지 않았다. 비교 항목은 finals/special/telegram/manager_events/source_health/oz_state/fvg_state/watch_state 8개다. S0와 240초 기준도 비교했다. 수신자·문구·시각·방향·상태 전이가 일치했다.

| 시나리오 | UTC 시작 / 구간 | 최종 / SPECIAL | 조건 충족 전달 | 결과 |
|---|---|---:|---:|---|
| parity_240_S6 | 2026-09-22T00:00:00+00:00 / 240초 | 5 / 1 | 8 | 일치 |
| weekday_XAU_1200 | 2026-09-24T00:00:00+00:00 / 1200초 | 5 / 1 | 9 | 일치 |
| weekend_BTC_1200_configured | 2026-09-26T00:00:00+00:00 / 1200초 | 4 / 0 | 8 | 일치 |
| actual_behavior_240 | 2026-09-23T12:00:00+00:00 / 240초 | 0 / 0 | 2 | 일치 |
| actual_BTC_weekend_600_configured | 2026-09-19T00:00:00+00:00 / 600초 | 0 / 0 | 2 | 일치 |

시나리오별 등록 확인 11건은 조건 충족 알림 수에서 제외했다. 기준 경로를 한 번씩 합산하면 최종 알림 14건, 조건 충족 Telegram 전달 29건을 비교했다. SPECIAL은 최종 알림의 부분집합이며 최종 알림과 Telegram 전달은 서로 겹칠 수 있으므로 두 수를 합산하지 않는다. 각 경로의 전체 알림 목록·수신자를 `external_behavior_summary.json`에 보존했다.

실제 XAU 구간에서는 최종 SPECIAL/Composer 알림이 0건이지만 Watch 전달 2건과 상태 전이를 비교했다. 여러 날짜의 제한된 구간을 검증했으며 연속 여러 날 전체 백테스트를 실행한 것은 아니다. 실측과 합성 시나리오를 구분해 기록했다.

## 대표 입력 및 실제 MT5

- 실제 v1/v2 XAU 캡처 19개 TF에서 첫/두 번째/60번째/120번째/마지막 관측, 총 95개를 비교했다. 전체 time/volume/OHLC, 최신 20행의 MT5 값·ATR14_GENERAL·regime·원비 등이 동일하다. 이 중 미준비 응답 5개도 같은 요청 응답으로 일치한다.
- 동결 실제 S0 골든에서 TF별 대표 18개를 SNAPSHOT + 실제 OZ 클라이언트/staff_compat 경계로 비교했다. S1 합성·실제 ATR 및 혼입 방지 검사는 G1에서 유지했다.
- EA와 PRICE/RSI/STO/DI 5개를 격리된 MetaEditor에서 컴파일했다. 모두 오류 0·경고 0이다. `compile_attempt_2`와 `compile_final.log`를 보존했다.
- XAUUSD+ 2026-09-23 12:00–12:04 UTC와 BTCUSD 2026-09-19 00:00–00:10 UTC를 각각 v1/MSP2와 v2/MSP3로 실제 캡처했다. `manifest.tsv`, `complete.txt`, `pipe_*.bin` 및 파일 해시가 각 capture 폴더에 있다.
- 주말 BTC 실제 LIVE 파이프를 31.395초 확인했다. HELLO 포함 29개 프레임, 19개 TF 수신을 확인했다. 별도 파이프·격리 터미널을 사용했다. `live_BTC/live_frames.bin`, `result.json`이 근거다.

## 전체 검사와 진단 재실행

전체 G1은 1회 수행했다. Part2는 기존 824개를 모두 유지하고 S6 신규 14개를 더해 838개를 수집했다. 정책상 내부 600조합 1개를 제외했다. 늦게 추가된 reconnect/stale 검사와 실행 환경 간섭 항목은 아래 원인별 재검증으로 합쳤다. 최초 로그/XML을 덮어쓰지 않았다.

| 그룹 | S5 → S6 항목 수 | 최종 signature 상태 | 미해결 새 차이 |
|---|---:|---|---:|
| part2 | 824 → 838 | PASSED 787, ERROR 21, FAILED 10, SKIPPED 19, POLICY_DIAGNOSTIC_NOT_RUN 1 | 0 |
| watch_ma | 188 → 188 | PASSED 187, SKIPPED 1 | 0 |
| root | 279 → 279 | ERROR 1, PASSED 266, SKIPPED 6, FAILED 6 | 0 |

audit 최종 상태: {'PASS': 176, 'FAIL': 1}. 무결성 원본 목록에서 이어진 기존 진단 22건은 그대로 남아 있으며 새로운 무결성 진단은 없다.

- audit 격리 harness가 STAFF보다 staff_schema를 늦게 로드해 145개 검사가 ImportError로 실행되지 않았다. 같은 모듈들의 로딩 순서만 수정하고 해당 145개만 재실행했다. 144개 통과, 기존 source-integrity 실패 1개다. assertion을 삭제하지 않았다.
- `test_native_data_build.py` fixture에 새 .mqh 두 파일을 추가하고 배포 확인 assertion도 추가했다. 기존 검사를 줄이지 않았다.
- S1/S4/S5의 EA 파일/STAFF AST 고정 검사 4개는 S6에서 허용된 구현 변경 때문에 실패했다. 원래 테스트를 수정하거나 실패를 지우지 않았다. 최신 정책에 따라 내부 진단으로 구분하고, 그 뒤의 원비·client normalizer·계산 모듈·제어/health 불변 항목은 `scope_verification.json`으로 별도 확인했다.
- Part2의 전역 임시폴더 목록 검사가 동시에 실행한 audit의 `moses-*` 생성/삭제를 감지했다. 독립 TEMP/TMP에서 그 검사만 재실행해 통과했다. 생산 코드 변경은 없다.
- 현재 불변 목록 재생성 전 루트 manifest 검사 결과와 재생성 후 해당 검사 결과를 모두 보존했다. 기존 검사는 Windows 역슬래시와 목록의 POSIX 슬래시를 그대로 비교하여 계속 실패한다(기존 기준선 결함). 동일한 파일 집합과 해시를 정규화된 상대 경로로 비교한 `immutable_verify.json`이 실제 무결성 판정 근거다.
- 자정 재시작 검사는 미완료 후보·조기 알림 0건·재시작 후 동일 이벤트 1건 assertion을 통과한 뒤 압축 checkpoint 바이트 비교에서 실패했다. 관련 검사 1개를 진단해 set/frozenset 나열 순서만 정규화하면 전체 payload가 동일함을 확인했다. 값·상태·epoch·event를 제거하지 않았다. 원래 실패와 두 checkpoint를 보존하고 최신 정책의 내부 순서 진단으로 분류했다.
- `test_oz_fvg_optimization.py`는 바이트 불변이며 전후 6 + LIVE↔BACKTEST 6 + 시나리오 1 + 성능 1, 총 14개를 유지했다. S6 성능은 참고만 기록하고 판정하지 않는다.

## 게이트 결과

| 게이트 | 결과 |
|---|---|
| G1_no_new_external_regression | 통과 |
| G1_Part2_824_preserved | 통과 |
| G2_representative_decision_inputs | 통과 |
| G3_240_and_S0 | 통과 |
| expanded_actual_and_weekend_external_behavior | 통과 |
| G4_wire | 통과 |
| actual_MT5_compile | 통과 |
| actual_BTC_live_v2 | 통과 |
| user_symbols | 통과 |
| integrity_chain | 통과 |
| immutable_inventory | 통과 |
| original_and_goldens_frozen | 통과 |
| Part3_frozen | 통과 |
| wonbi_clients_and_calculations_unchanged | 통과 |
| performance_reference_recorded_once | 통과 |

## 성능 참고 — 각 1회, 판정 없음

실제 BTC LIVE에서 받은 동일 관측을 v1 FULL로 복원한 입력과 원래 v2 입력을 비교했다. 입력 준비·host 시작·파이프 대기 시간을 제외하고 수신 파싱·CRC·검증·Snapshot 저장 CPU를 각 1회 측정했다. 이는 EA의 v1 LIVE 별도 실측이 아니라 **동일 관측의 v1 환산 비교**다. 허용치·표본 산식·정책 파일은 바꾸지 않았다.

| 항목 | S5/v1 환산 | S6/v2 | v2/v1 |
|---|---:|---:|---:|
| 파이프 바이트 | 129,905,384 | 9,602,508 | 0.073919 |
| 수신 CPU 초 | 0.078125 | 0.046875 | 0.600000 |
| 초당 데이터 쓰기 | 16.945 | 0.892 | — |

피드 관측 532개, 묶음 28개. HELLO는 바이트/전체 프레임 수에는 포함하고 데이터 묶음 빈도에서는 제외했다. 종류별 수와 측정 경계는 `performance_reference.json`에 있다. 짧은 실측이므로 장시간 성능 보장이나 S5/S8 공식 성능 판정으로 사용하지 않는다.

## 기존 결함 및 한계

- 실제 XAU 1d의 가장 오래된 워밍업 지표 셀에 프로세스마다 달라지는 subnormal 극소값이 존재했다. 동결 S0 및 수정하지 않은 v1에도 재현된다. 최신 판정 입력은 동일하며, 이 문제를 S6에서 임의로 정규화하거나 지표식을 바꾸지 않았다. 좌표와 값은 `existing_MT5_history_diagnostic.json`에 남겼다.
- 당일 토요일 캡처는 MT5가 종료일=다음 날을 거부해 실패했다. 최초 실패 로그를 보존하고 전주 토요일의 실제 BTC 구간을 사용했다.
- 기존 파일 누락/GUI의 SPECIAL 디렉터리 읽기/조건부 fixture/레거시 Part3 관련 기준선 실패를 S6 회귀와 구분했다. 새 차이는 숨기지 않았으며 raw/effective signature를 함께 남겼다.
- EA TF 묶음은 같은 폴링 회차의 일관된 게시 단위다. TF 계산 자체가 같은 CPU 시각에 동시에 수행된다는 의미는 아니다. 수신 지연은 EA TimeGMT의 초 단위 정밀도 영향을 받는다.
- 이벤트 엔진·Fact DAG·원비 이관은 구현하지 않았다. 허용 심볼 추가는 사용자 설정 변경이며 계산식/전략 의미 변경은 없다.

## 무결성과 인계

변경 단위 `28-staff-s6-wire-v2`, 사용자 설정 단위 `29-staff-s6-user-symbol-config`를 체인에 등록했다. EA의 기존 미등록 기준선 해시도 observed_s5_before_sha256으로 구분해 보존했다. 원래 source_manifest와 이전 단계 불변 목록은 그대로 두고 현재 목록만 재생성했다. Part3 참조 5곳은 목록만 기록했다.

핵심 근거: `검증결과/staff_s6/status.json`, `external_behavior_summary.json`, `baseline_signature_compare.json`, `decision_input_samples.json`, `performance_reference.json`, `frozen_guard.json`, `final_integrity_verify.json`, `immutable_verify.json`. S7은 별도 사용자 지시 후 진행한다.
