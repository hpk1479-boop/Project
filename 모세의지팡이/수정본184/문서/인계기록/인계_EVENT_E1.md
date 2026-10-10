# Event Engine E1 인계

수정본16에 추가한 opt-in 뼈대다. **기본 폴링 LIVE는 그대로이며 E2는 시작하지 않았다.** S8 승인·동결 기준선은 `검증결과/staff_s8/baseline_S8/manifest.json`에 있다. 수정본15는 수정 대상이 아니다.

## 인터페이스 사용

`Part1/program`을 Python 경로로 두고 `event_engine`을 import한다. 엔진은 네트워크·파일·전략 서비스를 시작하지 않는다.

```python
from event_engine import (
    EventEngine, IngressSequencer, Kind, Subscriptions, Resolution,
    Boundary, Signal, TimerRequest,
)

class Example:
    name = 'example'

    def subscriptions(self):
        return Subscriptions(
            symbols=('XAUUSD+',), timeframes=('1m', '5m'),
            facts=('ATR14_GENERAL',),
            kinds=(Kind.MARKET_BUNDLE, Kind.TIMER),
            resolution=Resolution.CONDITION,
            boundaries=(Boundary('1m', 'close', 'wonbi_upper'),),
        )

    def on_event(self, event, board, state, emit):
        # 실제 조건식 이전은 E2에서 한다. 이 예시는 연결 계약뿐이다.
        symbol = event.payload['symbol']
        values = board.fact('ATR14_GENERAL', symbol, '1m')
        state['last_source_time'] = event.source_time
        if event.kind == Kind.MARKET_BUNDLE and not state.get('timer_requested'):
            state['timer_requested'] = True
            emit(TimerRequest(symbol, event.source_time + 60_000, 'recheck'))
        if event.kind == Kind.TIMER:
            emit(Signal(symbol, 'example-recheck', {
                'direction': 'LONG', 'text': '시험 메시지', 'recipients': (0,),
            }))

ingress = IngressSequencer()
engine = EventEngine(ingress, strategies=[Example()])
# 외부 입력은 ingress.post(...). 처리 스레드 하나에서 engine.run().
# SIGNAL 결과는 engine.signals. E1에는 실제 전송 연결이 없다.
```

- Wire 입력: `StaffIngressAdapter(cache, ingress).receive_one(read_exact)` 또는 `.publish(raw)`를 사용한다. 전용 writer만 연결하며 기존 폴링 캐시에 추가 writer를 연결하지 않는다.
- 순수 합성 입력: `Input`의 MARKET_BUNDLE payload에 `symbol`, TF→`FeedSnapshot` 매핑을 넣는다. `replay(engine, inputs, resolution)` 또는 `ingress.post`로 같은 엔진에 넣는다.
- MSP3: I/O 경계 `capture_io.capture_bundles(path)`가 `(source_time_ms, wire_bundle)`을 공급한다. STAFF 어댑터를 통해 불변 Snapshot으로 변환한다. Part2를 import하지 않는다.
- Board는 snapshot/declared fact/declared processor state만 읽는다. 배열·상태 뷰는 불변이다. 상태 변경은 전달받은 전략 전용 `state`에만 한다.
- `Signal`의 조건 키는 안정적인 식별자로 고정한다. signal_id는 전략 이름·심볼·source_time·조건 키의 SHA256이며 무작위 ID나 벽시계를 사용하지 않는다.
- `engine.checkpoint()`는 큐를 비운 경계에서 메모리 체크포인트를 만든다. 같은 전략/처리기 이름으로 생성한 새 엔진의 `restore()`로 복원한다. 자동 파일 저장은 없다.
- 연속 오류 5회면 자동 중지된다. `COMMAND` payload `{'command':'ENABLE_STRATEGY','strategy':'이름'}`으로 명시적으로 재활성화할 수 있다. 한 번 성공하면 오류 횟수는 0이 된다.
- 세부 순번/타이머 해석과 차이 목록은 `설계대응_모호성_EVENT_E1.md`를 먼저 읽는다.

## E2 OZ 상태 하나의 이전 절차 초안

1. S8의 OZ 후보 상태/B0 갱신 경로 하나를 선정하고 읽는 TF·MT5 열·ATR14_GENERAL·원비 경계를 목록화한다. 기존 판정 함수 본문은 변경하지 않는다.
2. `OZCandidateProcessor`의 name과 subscriptions를 정의한다. 기존 후보 상태를 엔진이 주는 processor `state`로 옮긴다. 시간 계산은 event.source_time만 사용한다.
3. 모든 TF가 commit된 Board에서 입력을 읽어 기존 후보/B0 전이 함수를 실행한다. 이 단계에서 알림을 보내지 않는다.
4. OZ Consumer는 subscriptions.processor_states에 처리기 이름을 선언하고 `board.processor(name)`으로 후보 상태를 읽는다. 기존 알림 조건이 참일 때만 Signal을 낸다.
5. 진행 봉 변화에 민감한 조건은 TICK 또는 모든 관련 경계가 선언된 CONDITION을 택한다. 확정 봉 조건은 이전 봉을 명시해서 읽는다. ATR14_GENERAL을 FVG_WILDER_ATR로 바꾸거나 공유하지 않는다.
6. 동일 합성·보존 XAU/BTC 입력에서 LIVE↔재생 SIGNAL의 ID·시각·방향·문구·수신자를 비교한다. S8 폴링 대비 시점 변화는 버그와 승인 대상 의미 변경을 구분해 사용자에게 보고한다.
7. 상태가 자정을 넘는 시험과 체크포인트 전후 연속성 시험을 통과한 뒤 다른 OZ 상태를 옮긴다. E1에서는 이 절차를 실행하지 않았다.

## 보존 사항

S0~S8 및 성능 정책, 사용자 BTCUSD config, Part3는 보존한다. `build/mt5_known_defect_exclusions.json`의 일봉 초기 버퍼 결함은 E1에서 고치지 않았다. 신규 MT5 비교 캡처와 S0 성능 게이트는 실행하지 않았다. 테스트는 외부 네트워크를 차단한다.
