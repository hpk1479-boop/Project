# 수정본16 — Event Engine E1 뼈대

**판정: 완료**. E2는 시작하지 않았다. 기본 폴링 LIVE는 그대로다.

## 변경 파일과 구현

- Part1/program/event_engine/: model, ingress, engine, board, facts, scheduler, interfaces, replay, staff_adapter, capture_io, metrics, static_rules 및 opt-in __init__.
- indicator_facts.py: 기존 FactSpec/fact 등록부에 owner·invalidation·shared 필드 추가. 기존 ATR14_GENERAL 함수와 계산식은 그대로 사용한다.
- MT5/THE_STAFF_OF_MOSES.mq5: STAFF_TIMER_MS를 input으로 변경, 기본 1000 유지. STAFF_Wire_Schema.mqh는 빌드 해시만 갱신. schema는 0x36c28f68 그대로다.
- tests/test_event_e1.py: 원자성·순번·타이머·오류 격리·해상도·ATR 비트 일치·실제 Windows Named Pipe·정적 위반 검출 시험. 기존 테스트 검사 항목을 줄이지 않았다.
- tests/sparse_events/test_events.py: midnight checkpoint 시험의 압축 바이트 비교를 진단으로 보존하고, 디코드한 전체 값·타입·배열과 순서 있는 목록의 동일성을 검사한다. dict/set/frozenset 직렬화 순서만 정규화한다. 기존 미완료 후보·발생 이벤트 검사는 모두 유지했다.
- 무결성 단위32와 build/part1_immutable_sha256.json, 검증 도구·문서.

설계 항목별 대응표와 모호성/다른 구현은 설계대응_모호성_EVENT_E1.md에 분리했다. 특히 순번은 Ingress의 도착 FIFO에서 타이머를 포함한 처리 순서를 확정할 때 부여한다. 이는 번호를 받은 시장 이벤트 앞에 뒤늦게 발견한 타이머를 끼워 넣는 모순을 피하기 위한 선택이다.

## 검증 결과

| 항목 | 결과 |
|---|---|
| new_tests | PASS |
| polling_behavior_S8 | PASS |
| G1_no_new_failures | PASS |
| compile | PASS |
| source_scope | PASS |
| integrity | PASS |
| actual_capture_replay | PASS |
| final_focused_tests | PASS |

전체 root 결과: {'ERROR': 1, 'PASSED': 294, 'SKIPPED': 6, 'FAILED': 6}. Part1 audit 결과: {'PASS': 176, 'FAIL': 1}. 각 전체 묶음 1회 실행.
E1 시험: 전체 실행 당시 28개 통과. 최종 검토 보완 후 E1 31개와 기존 checkpoint 시험 1개를 관련 재실행하여 총 32개 통과. 최종 새 root 실패 0, 새 audit 실패 0.
전체 실행 원시 결과에는 checkpoint 압축 바이트 차이 1건이 추가로 있었다. E1 seed 0·1·2 및 동결 S8 seed 0·1의 해당 시험 단독 진단에서는 재현되지 않았으므로 원인이 확정되었다거나 S8에서 재현되었다고 판단하지 않는다. S6 이후 정책에 맞게 내부 직렬화 바이트는 진단으로 남기고 전체 디코드 상태와 외부 이벤트를 검사하도록 해당 테스트만 수정했다. 원시 실패·signature·재실행 XML을 모두 보존했다. root_effective_counts는 관련 재실행 결과를 합친 것이며 전체 묶음 재실행 결과가 아니다.
Part2 일반 회귀 4묶음은 실행하지 않았다. 실제 Telegram은 보내지 않았으며 네트워크 연결은 테스트/호스트 경계에서 차단했다.

| 기존 폴링 LIVE 시나리오 | 구간(초) | 최종 알림 | 조건 전달 | S8 동일 |
|---|---:|---:|---:|---|
| parity_240 | 240 | 5 | 8 | True |
| weekday_1200 | 1200 | 5 | 9 | True |
| weekend_1200 | 1200 | 4 | 8 | True |
| actual_actual | 240 | 0 | 2 | True |
| actual_btc | 600 | 0 | 2 | True |

비교한 최종 알림 합계 14건, 조건 전달 29건. 알림의 발생·시각·방향·문구·수신자 및 상태 전이 전체를 비교했다.
기존 폴링 회귀는 S8 동결 LIVE 결과와 현재 LIVE 결과 비교다. 새 엔진의 LIVE↔재생은 실제 Named Pipe 시험과 보존 MSP3 입력의 시험 SIGNAL 비교로 검증했다. 내부 OZ set/frozenset 순서는 기존 정책을 적용했다.

## 참고 측정 — 합격 기준 아님

| 보존 MSP3 | 묶음 수 | 평균 ms | p99 ms | 시험 SIGNAL 수 |
|---|---:|---:|---:|---:|
| XAUUSD+ | 239 | 2.2417 | 3.1023 | 239 |
| BTCUSD | 312 | 3.0500 | 5.5970 | 312 |

범위는 Board commit·Fact 무효화/계산·시험 전략·SIGNAL 처리까지다. 캡처 파일 읽기와 STAFF 디코드는 측정 밖이다. 이 수치를 파이프 전체 지연이나 완성 전략 엔진 처리량으로 해석하지 않는다. 표는 최종 코드에서 캡처별 1회 참고 측정이며 S0 대비 5쌍 게이트는 실행하지 않았다. 검토 보완 전 개발 측정은 reference_measurement_development.json에 별도로 보존하고 표에는 합산하지 않았다.

## MT5와 보존 사항

- EA 컴파일: 오류 0·경고 0. 로그: 검증결과/event_e1/compile/compiler_log.txt. 실행 간 비교 캡처와 Strategy Tester 재실행은 하지 않았다.
- Part2 운영 코드·Part3·사용자 BTCUSD config·S0~S8 기준선·성능 정책 해시 차이 0. EA 테스터 추출/dispatch·Wire 형식·클라이언트/전략 계산식은 보존했다.
- S8 초기 일봉 버퍼 결함은 수정하지 않았다. 별도 실행 비교에서만 적용하는 정확한 48셀 제외 범위는 build/mt5_known_defect_exclusions.json에 있다. 이번 동일 캡처 재생에는 이 제외를 적용하지 않았다.
- 수정본15 승인 메타데이터 변경은 복원했고, 승인 기록·baseline_S8은 수정본16에 보관한다. 8개 복원 해시 및 신규 파일 삭제 목록은 검증결과/event_e1/restoration_evidence.json 및 작업공간 reports/revision15_restoration/에 있다. 로그/실행 상태 12개 차이는 사용자 지시로 추가 조사 없이 승인 기록에 목록만 남겼다.

## 기존 결함과 한계

- S8의 기존 root 실패·collection error 및 audit 무결성 진단은 기준선으로 보존한다. 원래 실패 기록을 통과로 덮어쓰지 않았다.
- 실제 MT5 캡처는 XAU 4분, BTC 주말 10분 구간이다. 하루 이상 내구 시험 또는 전체 실제 전략의 이벤트 이전 검증을 완료했다는 뜻은 아니다.
- E1의 시험 전략 결과는 실제 SPECIAL/OZ/FVG/Watch 전략 이전 결과가 아니다. 그 이전은 E2 범위이며 사용자 검토를 기다린다.
- Journal·Outbox·김매니저 분리·섀도·Part2 창고·실제 서비스 연결은 만들지 않았다. 체크포인트는 메모리 API만 제공한다.

## 산출 위치

- 검증결과/event_e1/status.json: 최종 판정과 게이트.
- 검증결과/event_e1/baseline_signature_compare.json: 전체 테스트 signature.
- 검증결과/event_e1/polling_comparison.json: 기존 폴링 외부 결과.
- 검증결과/event_e1/reference_measurement.json: 참고 측정과 캡처 해시.
- 인계_EVENT_E1.md: 인터페이스 사용법·OZ 상태 하나의 E2 이전 초안.
- 설계대응_모호성_EVENT_E1.md: 설계 대응과 임의 결정 근거.
