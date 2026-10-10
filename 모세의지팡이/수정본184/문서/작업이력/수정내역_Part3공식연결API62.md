# 수정본62 Part3 공식 연결 API 정리

수정본61을 복사해 수정본62에서 작업했다. 이전 검증결과와 캐시는 복사하지 않았다. 생성 전략의 조건·계산식·순서·취소·봉 기준·Recipe v2 및 schema는 변경하지 않았다. Part2와 기존 테스트 파일도 변경하지 않았다.

Part1에는 범용 연결 API만 추가했다. Part3가 실행 중 Part1 소스파일을 패치/덮어쓰거나 Part1 객체의 private 메서드를 교체하는 경로를 추가하지 않았다. 현재 생성 템플릿과 백테스트 진입점의 기존 교체 경로를 제거했다. 저장돼 있던 과거 생성 Python은 덮어쓰지 않았다.

## 제거한 의존

| 기존 private 접근/교체 | 수정본62의 공식 경계 |
|---|---|
| `manager._desired_subscriptions_locked` 저장/교체와 `_subscription_dirty` 직접 설정 | `special_api.register_subscription_provider(owner, provider)` |
| `manager._handle_fact_event` 저장/교체 | `special_api.register_fact_observer(owner, observer)` |
| `manager._special_oz_event_handlers` 직접 조회·전후 key 비교 | `special_api.capture_oz_handlers()`와 `register_oz_handler(spec_id, handler)` |
| `manager._push(payload)` 직접 호출 | `special_api.request_watch(payload)` → 기존 명령 경계 |
| `manager._delivery_context` 직접 설정/복구 | `special_api.notify(text, event=..., event_id=...)` → 기존 알림/중복 정책 |
| `manager._part3_intent_facts` private 동적 속성 생성 | `special_api.shared_resource(name, factory)` → 동일 manager의 기존 MA/레벨 Fact 캐시 공유 |
| `board._frame_cache`의 identity 읽기 | 읽기 전용 `board.publication_token` |
| manager의 event_services/kernel 구조를 직접 추적 | `special_api.market_context()` → 현재 Board·종목·사건 시각 |
| `event_application._part3_original_loader` 생성 및 `load_strategy_inputs` 실행 중 교체 | Part1 `register_strategy_loader(name, loader, dependencies=...)` |

Part3 자체의 `_board`, `_observe` 등 내부 메서드는 Part1 private 의존이 아니다. 기존 SPECIAL4/5 계산 코드는 신뢰된 템플릿으로 생성하는 방식과 자기 클래스의 내부 메서드를 유지한다. 이번 범위는 Part1 객체와의 연결 경계다.

## API 계약

- `register_subscription_provider(owner, provider)`: provider는 `{family: {watch_id: payload}}`를 반환한다. 지원 family는 현재 TREND/FVG/SWEEP이다. 같은 owner의 재등록은 교체이며 여러 owner의 선언은 기존 구독과 함께 합쳐진다. 변경/취소는 기존 구독 reconciliation과 명령 전송을 사용한다.
- `register_fact_observer(owner, observer)`: 기존 Part1 Fact 검증/반영을 마친 뒤 `ok=True`, `stale`이 아닌 Fact만 observer(event)에 전달한다. 각 observer는 소유된 사본을 받는다. 반환값이 기존 ACK를 바꾸지 않으며, 같은 owner 재등록은 교체다. observer 오류는 기존 엔진 오류 경계로 전달된다.
- `register_oz_handler(spec_id, handler)`: 기존 공식 `register_oz_event_handler`의 별칭이다. 기존 이름도 유지한다. `handle_oz_event(event)`와 김매니저의 알림 lifecycle/공유 OZ 판정 경로는 같다.
- `capture_oz_handlers()`: 등록 범위 안에 새로 들어온 owner만 반환한다. 이미 등록된 owner는 포함하지 않는다. 상속 전략의 최종 조건 wrapper는 이 공개 등록 결과를 통해 연결한다. 예외가 나도 등록 범위는 닫힌다.
- `shared_resource(name, factory)`: manager마다 한 번 생성해 공유한다. 가변 MA·전일/세션 레벨 계산은 기존 Fact 계층을 그대로 사용한다. 체크포인트에서는 기존 manager deepcopy 계약을 따른다.
- `market_context()`와 `publication_token`: 기존 사건 시각과 게시 identity를 공개한다. 실시간 시계나 신규 지표 계산을 추가하지 않는다.
- `notify(...)`: 기존 send_telegram/Output 정책에 사건 메타데이터를 전달하고 호출 뒤 문맥을 복구한다. 알림의 종목·방향·시간·전략 ID를 유지한다.
- `register_strategy_loader(...)`: 독립 프로세스의 생성 전략 loader와 의존성을 등록한다. 명시적으로 선택한 전략만 읽으며 기본 SPECIAL1~7의 소스/로딩 동작을 유지한다. `unregister_strategy_loader(name)`로 외부 등록만 해제할 수 있다. Part3 백테스트 spawn 초기화에서도 같은 공개 등록을 사용한다.

## 변경 범위

- Part1: `program/event_composer_domain.py`(공식 plugin 연결), `program/event_engine/board.py`(게시 token 공개), `program/event_application.py`(외부 전략 loader 등록).
- Part3: `lab/intent_port.py`, `lab/ai_compiler.py`, `backtest.py`.
- 검증: `verification/test_part3_connection62.py` 추가, `verify_current_manifest.json`에 공식 대상으로 추가, `CURRENT_VERIFY.md` 갱신.
- Part2 변경 0개. 기존 Part1/Part2/Part3 테스트 변경 0개. `_handle_fact_event`, `_reconcile_facts`, `_apply_fact_event`, `_evaluate_symbol_locked`, `_condition_source_usable`, `_handle_oz_event`, `_handle_oz_event_core`의 AST는 모두 그대로다. 나머지 계산 파일도 그대로다.

## 검증

```powershell
python verify_current.py
```

| 검사 | PASS | FAIL |
|---|---:|---:|
| Part1 | 935 | 0 |
| Part2 | 379 | 0 |
| Part3 | 180 | 0 |
| Python | 270 | 0 |
| JS | 4 | 0 |
| Verifier | 10 | 0 |

TOTAL PASS **1778**, FAIL **0**, SKIP **737**, 종료코드 **0**.

기존 수정본61의 공식 대상과 제외 이유를 모두 유지했다. 제외에는 폐기 모듈/계약·GUI·선택적 실제 Windows 프로세스 시험 등이 포함되며 마지막 출력에 이유가 표시된다. 이를 실행한 것으로 보고하지 않는다.

새 13개 회귀 시험은 공개 capability만 제공한 일반/SPECIAL4/SPECIAL5 등록, 생성 코드의 Part1 private 접근 차단, 다중 구독/재등록/취소, 잘못된 Fact·동일 revision 차단과 observer 사본 분리, manager별 공유 캐시, OZ 등록 scope 해제, 두 생성 전략의 체크포인트 복원 후 LIVE/재생 알림 일치, 선택한 plugin만 로딩, 잘못된 API 등록 거부를 확인한다.

기존 24개 공통 Recipe 시험도 포함한다. #054/#059/#060과 신규 가변 MA, 확정봉/진행봉 경계, 실제 Part1 Wire 수신과 Part2 캡처 재생, 생성 파일 import/register/현재 Part3 백테스트 loader 경로가 계속 통과한다. 알림 결과의 조건·시각·방향을 바꾼 항목은 없다.

증거: `검증결과/current_verify/20261001_190945_716494/summary.json`, `검증결과/current_verify_console.log`, `검증결과/interface_change_scope.json`. 모든 증거는 수정본62에서 새로 만들었다. 실제 Ollama/MT5 역사 구간 실행이나 텔레그램 발송 결과를 뜻하지 않는다.
