# E1 설계 해석과 범위

E1은 기본 실행에 연결하지 않는 Part1 추가 모듈이다. 기존 전략, 매니저, SPECIAL, 폴링 스레드, Part2/Part3 실행 경로를 바꾸지 않는다.

| 설계 항목 | 구현 |
|---|---|
| 1~2장 입력/불변 이벤트 | event_engine/model.py, staff_adapter.py |
| 3장 단일 관문·무손실 순서 | ingress.py, engine.py |
| 4장 원자 Board·Fact·상태 | board.py, facts.py, indicator_facts.FactSpec, interfaces.py |
| 5장 Consumer·Signal | Subscriptions, Strategy, Signal, signal_id |
| 6장 종목별 source_time Scheduler | scheduler.py; 과거 기한→묶음→동일 기한 |
| 7장 MSP3·합성·3해상도 | capture_io.py, replay.py; 입력은 같은 Input/Ingress 계약 |
| 11.3 오류 격리 | STRATEGY_ERROR 기록, 연속 5회 중지, DEGRADED SIGNAL |
| 정적 제약 | static_rules.py, tests/test_event_e1.py의 의도적 위반 입력 |

## 모호한 부분의 선택

1. **수신 시 순번과 과거 기한 타이머 삽입의 충돌**: 뒤의 시장 입력이 이미 순번을 받은 상태에서 앞 전략이 만든 타이머를 그보다 먼저 실행하면 engine_seq 처리 순서가 깨진다. Ingress 내부에 불변 원시 입력 도착 FIFO와 순번 부여 후 ready FIFO를 구분했다. 엔진이 앞 입력의 전략 처리를 마친 뒤, 다음 원시 입력을 확장하며 Ingress만 engine_seq를 부여한다. 부여된 순번은 재정렬하지 않는다. 원시 도착 입력의 source_seq·source_time·계측 수신 시각은 그대로 보존한다. 이는 설계 3장의 '수신 즉시 순번 부여'를 'Ingress가 처리 순서 확정 시 부여'로 구체화한 차이다. 미리 여러 묶음을 큐에 넣는 시험으로 확인한다.
2. **시각 단위**: 엔진 source_time과 TimerRequest.due_time은 정수 Unix 밀리초다. 기존 MSP3의 관측 초는 입력 어댑터에서 1000배한다. engine_time은 지연 계측용 단조 시계 ns이며 전략 시간으로 쓰지 않는다.
3. **같은 시각의 재관측**: 해당 종목 source_time이 증가하지 않으면 새로 타이머를 발화하지 않는다. 기한 동일 타이머가 다른 타이머 콜백에서 만들어지면 무한 즉시 호출을 피하기 위해 다음 해당 종목 시각 전진까지 대기한다. 요청 이벤트보다 과거 기한은 오류다.
4. **해상도 선언**: 필요한 최소 해상도와 경계를 선언한다. 선택 모드가 더 낮으면 보수적으로 근사치 표시한다. 조건 모드에서 경계가 없는 전략은 그 피드 입력을 모두 보존한다. 표시만 정확하다고 하면서 미선언 조건을 임의로 거르지 않는다. 봉 확정 여부는 마지막 봉 시각 변화로 판단하며 각 전략의 확정 봉 읽기 위치는 전략이 정한다.
5. **MSP3 묶음 재구성**: 보존 캡처는 피드별 파일이므로 같은 관측 시각을 manifest feed 순서로 모아 묶는다. 다른 종목 입력을 심볼 이름으로 정렬하지 않는다. 이 재구성 묶음의 source_seq와 원래 각 피드 seq를 구분한다. 후자는 Snapshot에 남는다.
6. **체크포인트**: 엔진 소유 메모리 상태를 반환/복원하는 API만 제공한다. 큐를 다 비운 이벤트 경계에서만 생성한다. 파일 포맷·자동 저장·Journal·Outbox·병렬 실행은 추가하지 않았다.
7. **지연/오류 기록**: 지연 계측만 metrics.py에서 perf_counter_ns를 사용한다. 임계 초과는 FEED_HEALTH의 ENGINE_BACKLOG 경고다. STRATEGY_ERROR는 error_log와 주입 log sink에 남긴다. 파일 로그 sink 연결은 호스트 책임이며 엔진이 직접 파일을 열지 않는다. DEGRADED는 SIGNAL일 뿐 텔레그램 전송을 호출하지 않는다.
8. **EA 관측**: 설계의 향후 매 틱 OnTick 전환은 이번 범위가 아니다. 요청대로 STAFF_TIMER_MS만 input(기본 1000)으로 바꾸고 STAFF_DISPATCH_MS와 테스터 추출 경로는 보존했다. 빌드 식별자만 재생성하고 Wire 구조/schema는 그대로다.
9. **검증 변경**: 사용자 후속 지시에 따라 MT5 실행 간 새 비교 캡처, S0 성능 5쌍, 일일 이상 신규 캡처를 만들지 않는다. 보존된 XAU/BTC MSP3 구간을 사용한다. 별도 실행 일봉 초기 버퍼 결함은 수정하지 않는다.

## E2/E3로 남긴 부분

실제 전략 이전, 김매니저 출력 분리, 시작 서비스 연결, 섀도 모드, 폴링 제거, Part2 창고, 영구 저장은 미구현이다. StaffIngressAdapter는 전용 입력 경로가 단독으로 쓰는 STAFF cache를 받는다. 기존 cache.start() 수신기와 이 어댑터를 같은 캐시의 경쟁 writer로 동시에 연결하지 않는다. health()는 기존 STAFF 호스트가 판정한 FRESH/STALE/UNAVAILABLE 전이를 주입하는 경계이며, 엔진에 별도 벽시계 stale 판정을 만들지 않는다.
