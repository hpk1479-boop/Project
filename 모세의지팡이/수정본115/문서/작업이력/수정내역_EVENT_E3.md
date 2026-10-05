# Event Engine E3 수정내역 — 수정본19

수정본18 전체를 독립 복사한 수정본19에서 E3를 완료했다. 기본 실행은 이벤트 엔진이며, 김매니저는 SIGNAL 출력만 맡는다. 이전 수정본은 수정하지 않았다. 속도·폴링 알림 일치는 합격 기준으로 쓰지 않았다.

## 남은 구조와 실행

`Part1/OZ_SYSTEM CONTROL.pyw`의 전체 시작 → `program/event_host.py` → 전용 pipe 수신 → 기존 STAFF 공개 publish API → Ingress → 단일 Event Engine → Processor/Consumer → SIGNAL → `manager_KIM.SignalOutput` → 호스트가 주입한 Telegram transport.

- `event_host`가 Telegram 명령 권한과 종목을 확인해 COMMAND로 넣는다. Gemini 요청은 Consumer가 EXTERNAL_REQUEST 신호를 내고, 호스트가 HTTP 응답을 검증해 EXTERNAL_REPLY로 돌려준다. 같은 요청의 답변은 한 번만 수용한다. 전략은 HTTP를 호출하지 않는다.
- 기존 Composer 판정·조합 메서드는 `event_composer_domain.py`로 옮겼다. 판단식은 바꾸지 않았다. 공유 COMPOSER의 연속 오류는 자동 중지하지 않고 기존 승인대로 DEGRADED 한 번만 낸다. SPECIAL별 분리는 하지 않았다.
- 출력은 수신자별 signal_id를 중복 방지하며, 등록 확인의 논리 ID를 실제 Telegram message_id에 연결한다. 원래 규칙대로 알림은 사용자의 원 명령에 답글을 달고, 취소는 봇의 등록 확인 답글을 찾아 해당 종목으로 전달한다. HTTP 결과가 불명확한 예외를 무작정 재전송하지 않는다. 영구 Outbox는 없다.
- 경제 캘린더는 기존 조건/문구를 유지한 `event_economy_host.py` 호스트 서비스로 이동했다. 외부 캘린더 조회/호스트 시각은 전략 폴링이 아니다. 알림도 Ingress SIGNAL과 같은 출력 경로를 통한다.
- 시작 설정·별칭·SPECIAL 선택·기존 상태를 읽되, 쓰기는 실행본 `Part1/event_state` 또는 명시한 별도 경로로 제한한다. 해당 경로의 상태가 기존 logs보다 우선한다. 심볼별 Composer 메모리, OZ 관측 상태, Watch/stream/SWEEP 상태, 출력 완료 영수증을 종료 시 저장한다. 중간 강제 종료에 대한 영구 Outbox/감사 재생은 구현하지 않았다.
- 단일 스레드 엔진과 I/O worker를 분리했다. 큐를 버리거나 합치지 않는다. 실제 운영 전환/외부 송신은 이번 시험에서 하지 않았다. 사용자의 수정본16 운영 경로는 그대로다.

## 제거와 보존

- 전략별 Thread/run 루프·파일 명령 worker·ZMQ Staff/Manager client·ComposerMaintenance·SnapshotClient·StaffCompat.request·Part2 가상 시계 실행 경로를 제거했다. 기존 판정 메서드는 Consumer가 부른다.
- 파일 삭제 최종 **120개**. 정확한 경로와 삭제 전 SHA256은 `검증결과/event_e3/deletions.json`, 함수/클래스 이동 전 목록은 `structural_removals.json`이다. 그 목록의 EconomyWorker는 소멸이 아니라 호스트 이동이다.
- 사용자 추가 지시에 따라 `generic_backtest/native_mt5.py`, `native_features.py`, `bar_seed.py`와 의존 **68개 파일**을 보존했다. 이미 삭제했던 **45개**를 수정본18에서 복구했다. `native_automation_preserved.json`에 의존 관계·복구 해시를 남겼다. 공유 provider/계산 모듈도 이 의존 관계 때문에 남는다. 옛 백테스트 실행 엔진과 UI 실행 연결은 제거했다.
- MT5 자동 추출과 DuckDB 창고는 남는다. 삭제 대상 재계산 엔진을 부르던 창고 보조 함수 두 개만 제거했다(`warehouse_compute_detach.json`). Part2 UI는 옛 엔진이 종료됐으며 이벤트 창고 연결은 후속 작업임을 표시한다. 연결을 임의로 새로 만들지 않았다.
- 삭제 자동 승인 검토에서 기존 DuckDB 의존성 미해결이 지적되어 해당 연결을 정리한 뒤 삭제했다. 최신 네이티브 보존 지시는 최종 삭제 목록과 계획에서 반영했다. 이전 계획은 이름에 superseded를 붙여 작업 이력으로만 보존한다.

## MT5와 네이티브 추출

- PRICE/RSI/STO/DI의 최초 계산에서 모든 지표 버퍼를 EMPTY_VALUE로 초기화했다. 과거 부족 구간을 임의 숫자로 노출하지 않는다. 내부 재귀 평활 seed는 계산에 필요한 기존 0 초기값을 사용하고 출력은 EMPTY_VALUE로 가린다. 계산식은 변경하지 않았다.
- 테스터 native export에 wonbi_upper/lower/sigma를 끝에 추가했다. 기존 42개 값 순서는 그대로고 값 열은 45개다. 원비는 EA CalcOpenBands4 결과다.
- native export 관측 간격은 STAFF_TIMER_MS, 기본 1000ms다. 1000 미만 입력도 손실 없이 저장하기 위해 native 파일 version=2의 observed_time 단위는 밀리초, bar_time은 초다. `TIMER_SNAPSHOT_V2`/observation_unit/observation_interval_ms를 manifest에 쓴다. 과거 version=1(초) 읽기도 유지한다.
- DuckDB는 기존 second-keyed native_snapshots를 유지하고 v2는 native_snapshots_v2에 저장한다. 자동화의 NativeSnapshotSource도 양쪽 형식과 as-of 시간을 읽는다. 이는 native 추출 파일 변경이며, **EA pipe Wire v2/schema·STAFF 소스는 변경하지 않았다.**
- EA와 지표 5개를 한 번에 컴파일: 오류 **0**, 경고 **0**. 증거 `compile_result.json`과 `compile/*.log`. MetaEditor 프로세스 반환값 1과 별개로 컴파일러 로그와 ex5 생성으로 판정했다. Strategy Tester 실행/신규 캡처/장기 시험은 하지 않았다. 초기 버퍼 수정은 컴파일 확인 범위이며 실제 캡처로 재측정하지 않았다.

## 시험 결과

E1·E2·PERF1·E3 관련 시험 **104개 통과**, 새 실패 0. 관련 묶음 한 번 후 실패 원인·새 시험만 재실행했다. Part1 전체 audit/루트 전체/Part2 일반 회귀 4묶음은 실행하지 않았다. 실제 Telegram과 네트워크는 차단했다.

- 명령 등록 → 비접촉(미발생) → 접촉(발생) → 중복 방지 → 원 명령 답글 → 등록 확인에 취소 답글.
- Gemini 외부 응답 중복 방지·종목 변경 거부, 무허가 명령 거부, BTC 취소 종목 연결.
- 이벤트 전용 상태 저장/재시작, 폴링 상태 무변경, 경제 호스트 상태 복사, native v1/v2·밀리초·원비·DuckDB 왕복.
- HELLO ACK·파이프 publication·잘린 프레임 거부·허용 종목, E1/E2 원자성/타이머/오류 격리/체크포인트와 PERF1 논리 시험 유지.
- 기존 테스트 수정: E1의 opt-in 고정 검사를 기본 이벤트 진입 검사로 교체했다. E2 직접 판정 함수 비교 fixture는 현재 독립 복사 모듈을 사용하며 SWEEP 초기화에 `_level_cache=None`을 명시했다. 이전 버전의 구현을 정답으로 강제하지 않고 기존 논리 검사 항목을 유지했다. 개발 중 실패 XML도 삭제하지 않았다.

| 입력 | 묶음 | 전체 신호 | 최종 알림 | LIVE↔재생 신호 / 출력 |
|---|---:|---:|---:|---|
| synthetic240 | 240 | 5794 | 19 | 동일 / 동일 |
| actualXAU | 239 | 5916 | 13 | 동일 / 동일 |

비교는 signal_id·source_time·방향·문구·수신자와 출력 답글/영수증을 포함한 전체 기록이다. LIVE는 실제 온라인 대기가 아니라 동일 캡처 바이트를 `PipeReceiver.receive → STAFF → 엔진`으로 넣은 오프라인 수신 경로다. 재생은 STAFF 공개 publish 후 같은 엔진을 즉시 처리한다. 이전 알림과의 비교는 하지 않았다.

## 참고 측정 1회

보존 XAU, 모든 실제 전략 포함, 재생 1회: **239묶음**, 평균 **454.957ms**, p99 **503.269ms**. 엔진의 묶음 처리와 파생 내부 이벤트를 포함하며 파일 읽기·STAFF 바이트 디코드·외부 송신은 제외한다. 다른 검증 작업이 끝난 뒤 측정했다. 합격 기준이나 S0 성능 게이트가 아니다.

## 무결성과 남은 사항

수정본18 **34828파일** 대조: 불일치 0, 추가 0. Part3·기존 검증 결과·사용자 config/별칭/SPECIAL 설정/성능정책 **32890파일** 보존. BTCUSD 설정 유지. `38-event-e3` 해시 체인 등록, 삭제는 after_sha256=null로 명시하며 무단 재생성을 검출한다. 무결성 검사기 변경은 이 삭제 표식 처리뿐이다. 새 무결성 진단 0, 과거 잔여 진단 10건은 기록 그대로 남았다. build/part1_immutable_sha256.json을 재생성했다.

다음 병목은 기존 OZ/SWEEP 판정 메서드 주변의 DataFrame 준비와 매 묶음 객체 복원이다. 이번 E3에서는 구조 전환만 했고 판정식을 새로 쓰지 않았다. 후속 OZ 재작성에서 전광판 NumPy 뷰와 장수 객체로 바꾸며, SWEEP/다른 Consumer/SPECIAL 분리는 별도 범위다. 내부 source_epoch·health·seq 누락은 기존 STAFF 의미를 사용한다. 대규모 입력 backlog와 호스트 강제 종료 복구는 Outbox 없는 현재 설계의 한계다.

## Part1 변경 파일

- `Part1/OZ_SYSTEM CONTROL.pyw`
- `Part1/program/MT5/DI_of_Moses.mq5`
- `Part1/program/MT5/PRICE_of_Moses.mq5`
- `Part1/program/MT5/RSI_of_Moses.mq5`
- `Part1/program/MT5/STO_of_Moses.mq5`
- `Part1/program/MT5/THE_STAFF_OF_MOSES.mq5`
- `Part1/program/command_interpreter.py`
- `Part1/program/event_application.py`
- `Part1/program/event_commands.py`
- `Part1/program/event_composer_domain.py`
- `Part1/program/event_composition.py`
- `Part1/program/event_economy_host.py`
- `Part1/program/event_engine/__init__.py`
- `Part1/program/event_engine/board.py`
- `Part1/program/event_engine/capture_io.py`
- `Part1/program/event_engine/composition_consumer.py`
- `Part1/program/event_engine/domain_support.py`
- `Part1/program/event_engine/engine.py`
- `Part1/program/event_engine/facts.py`
- `Part1/program/event_engine/frames.py`
- `Part1/program/event_engine/history.py`
- `Part1/program/event_engine/ingress.py`
- `Part1/program/event_engine/interfaces.py`
- `Part1/program/event_engine/levels.py`
- `Part1/program/event_engine/metrics.py`
- `Part1/program/event_engine/model.py`
- `Part1/program/event_engine/oz_processor.py`
- `Part1/program/event_engine/oz_state.py`
- `Part1/program/event_engine/preparation.py`
- `Part1/program/event_engine/replay.py`
- `Part1/program/event_engine/restoration.py`
- `Part1/program/event_engine/scheduler.py`
- `Part1/program/event_engine/staff_adapter.py`
- `Part1/program/event_engine/static_rules.py`
- `Part1/program/event_engine/sweep_state.py`
- `Part1/program/event_engine/watch_consumer.py`
- `Part1/program/event_host.py`
- `Part1/program/event_pipe_host.py`
- `Part1/program/event_startup.py`
- `Part1/program/manager_KIM.py`
- `Part1/program/monitor_OZ.py`
- `Part1/program/staff_compat.py`
- `Part1/program/staff_snapshot.py`
- `Part1/program/strategy_FVG.py`
- `Part1/program/strategy_INDICATOR.py`
- `Part1/program/strategy_SWEEP.py`
- `Part1/program/strategy_TREND.py` (삭제)

추가: `Part1/audit/source_integrity.py`, 관련 tests, build 검증 도구, Part2 native.py/native_features.py와 제거 대상 연결·런처. 상세 삭제 목록은 위 JSON이 최종 목록이다.
