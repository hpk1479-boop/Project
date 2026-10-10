# Event Engine E2 인계

수정본16 전체를 독립 복사한 수정본17이다. 이전 폴더는 읽기 전용이다. E3는 시작하지 않았다. 최종 게이트 결과는 `수정내역_EVENT_E2.md` 및 `검증결과/event_e2/status.json`에 기록한다.

사용자 지시로 남은 확대 실행을 중단하고 완료된 결과만으로 종료했다. 합성240/BTC1200/실제XAU/실제BTC 네 쌍은 완료·동일하며, XAU1200 최종 비교는 미완료다. 등록 확인 시점 차이 36건은 별도 승인 대기이고 이 중단 지시를 차이 승인으로 취급하지 않는다. 미완료 구간을 다시 실행하거나 E3를 시작하지 않는다.

## 명시적 실행

```python
from event_startup import create_live_event_engine
from event_engine.staff_adapter import StaffIngressAdapter

# 선택한 polling program은 상태 읽기만 한다.
engine = create_live_event_engine(read_only_polling_program)
# 별도 STAFF cache를 사용한다. gap_journal도 event_state 아래에 지정한다.
adapter = StaffIngressAdapter(event_staff_cache, engine.ingress)
adapter.receive_one(read_exact, source_time=observation_ms)
engine.run()                     # 처리 스레드 하나
signals = engine.signals         # 전송은 연결하지 않음
checkpoint = engine.checkpoint()
```

합성/재생은 `event_application.create_event_engine(config, aliases=..., trigger_overrides=..., enabled_specials=..., state_files=...)`을 사용한다. 재생 기본값에는 LIVE 저장 상태를 섞지 않는다. 보존 MSP3는 Part1 `event_engine.capture_io.capture_bundles` → StaffIngressAdapter.publish → 동일 engine.run 경로다. 기다리는 가상 시계 루프가 없다.

`state_files`는 `{JSON basename: 원래 파일 텍스트}`이며 canonical 복원 함수에 전달한다. 실행 중 상태 변경은 ContextVar로 분리된 메모리 저장소와 engine.processor_state/strategy_state 안에 남는다. 전체 checkpoint는 메모리 저장·복원 계약이며 임의의 pickle 디스크 포맷을 추가하지 않았다. `event_startup.write_event_state_files`는 필요한 JSON 메모리 저장소를 이벤트 전용 경로에 내보내는 명시적 호스트 API다. polling program 트리 내부/상위 경로는 거부한다. 상세 입력·초기화 대조는 `시작경로_대조_EVENT_E2.md`.

**출력 경로 후속 항목:** 다른 수정본의 program을 읽으면 `engine.event_state_directory` 안내 속성도 그 program의 부모에 붙는다. 이 속성은 자동 저장에 사용되지 않는다. 이전 수정본을 읽을 때 export의 필수 `output_directory`에는 `C:/Users/hpk14/Desktop/개피곤/수정본17/Part1/event_state`를 명시해야 한다. 안내 속성 기준을 실행 중인 이벤트판 폴더로 바꾸는 보완은 미적용이다. 사용자 종료 지시에 따라 추가 소스 변경 없이 기록만 남긴다.

## 소유권과 호출 순서

- `SWEEP_STATE`: canonical detector/fingerprint/복원 동기화 및 외부 유동성 이벤트.
- `FVG_STATE`: canonical 활성/채움/접촉/이전 봉 lifecycle.
- `OZ_STATE`: 모든 profile의 후보/B0/관측/트리거 상태와 외부 gate, 수동 Watch.
- `INDICATOR`: canonical 추세/metric 계산, pending 이벤트 및 FactStream 세대/순번.
- `WATCH_CONDITIONS`: canonical GenericConditionMonitor/Watch MA 기억.
- `FVG`, `SWEEP`, `OZ`: 처리기 projection을 DOMAIN_FACT 신호로 전달하는 Consumer.
- `COMPOSER`: 같은 canonical Composer/WatchOrchestrator/SPECIAL1~7 등록·판정 함수를 호출한다. 종목별 CompositionKernel이 clock/state를 소유한다. 판단 공식은 새로 구현하지 않았다.

시장 묶음 전체 Board commit → 처리기 SWEEP/FVG/OZ → Consumer 등록 순서 → 내부 SIGNAL을 동일 Ingress 순번으로 처리한다. 공유 처리기 상태는 `__board__` projection만 Consumer에 읽기 전용으로 공개한다. 원본 dict를 직접 공유하지 않는다.

COMMAND는 `{symbol, text, chat_id, message_id?, reply_to_message_id?}`. 출력은 `NOTIFICATION`(내용/수신자/방향/event_time/답글 정보) 또는 내부 `DOMAIN_FACT`/`WATCH_COMMAND`다. Signal id는 E1의 전략명·심볼·source_time·조건 키로 생성한다. 무작위 UUID가 필요한 원래 등록 경로는 event_scope 안에서 source_time/owner/체크포인트 순번으로 결정적 ID를 만든다. polling scope 밖 동작은 기존 clock/uuid와 같다.

## 오류와 상태 파일

사용자 승인에 따라 COMPOSER는 연속 오류가 나도 계속 호출한다. 실패한 이벤트만 건너뛰고 5번째 연속 오류에서 DEGRADED를 한 번 낸다. 다른 Consumer의 5회 자동 중지는 유지한다. DEGRADED와 연속 오류 수는 checkpoint에 포함된다. 테스트 출력에는 실제 Telegram 전송이 없다.

SPECIAL 개별 분리는 아직 하지 않았다. E3에서 분리한 뒤 개별 자동 중지를 적용해야 한다. 기존 폴링 entrypoint는 event 모듈을 import하지 않는다. 초기 파일 읽기/로그 같은 호스트 기능과 source-time/메모리 판단 경계를 섞지 말아야 한다.

## E3 착수 전

1. 이 단계의 사용자 승인 대상인 폴링→이벤트 알림 시점 차이 목록을 검토한다. 승인 전 새 기준선으로 덮어쓰지 않는다.
2. 기본 폴링을 제거하거나 기본 이벤트 실행으로 바꾸지 않는다. E3 승인이 별도로 필요하다.
3. 김매니저의 전략 조합/출력 분리 시 전달·중복·답글 ledger 의미를 유지한다. SPECIAL4/5의 심볼별 런타임과 복원 상태를 다른 종목 시계로 진행시키지 않는다.
4. 경제 캘린더·Telegram 입력 서비스·네트워크 송신은 E2 엔진에서 시작하지 않는다. 외부 응답을 Ingress 입력으로 연결하는 호스트 경계를 명시해야 한다.
5. S0~S8 및 E1 기준선, 사용자 BTCUSD config, Part3를 보존한다. Part2 일반 회귀 4묶음·신규 MT5 캡처·S0 성능 게이트는 E2에서 실행하지 않는다.
