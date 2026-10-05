# AI 자연어 전략 인터페이스

Part3는 아직 실전에서 전략을 만들어 검증한 정답 프로그램이 아니다. 과거 수동 화면이나 과거 생성 파일에 맞추기 위해 사용자 의미를 바꾸지 않는다.

## 흐름

자연어 → 로컬 Qwen3:8b → 구조화된 intent → 사용자 확인 → Recipe v2 → validator → compiler → storage.generate → 독립 Test_SPECIAL 파일.

AI는 데이터만 반환한다. 코드·경로·source_text·파일 쓰기·삭제·Shell 도구는 없다. 조회 도구는 현재 같은 프로젝트의 Part1/Part2/Part3 코드와 제한된 문서만 읽는다. reference 복사본은 사용하지 않는다. 시장 계산은 MT5와 현재 Python Fact/처리기가 한다.

수동 생성기·Recipe v1 생성·수동 Python 입력은 제거했다. 생성은 AI Recipe v2만 사용한다. 과거 생성 파일은 보기만 가능하다. 이번 단계에서는 학습 데이터 자동 저장과 모델 학습을 하지 않는다.

## 출력 예시

    {
      "supported": true,
      "intent": "CREATE_STRATEGY",
      "interpretation": {
        "direction": "LONG",
        "symbols": ["XAUUSD+"],
        "steps": [
          {"kind": "MA_CROSS", "tfs": ["1m"], "ma_left": "WMA17", "ma_right": "SMA20"},
          {"kind": "FVG_NEW", "tfs": ["5m"], "side": "BULL"}
        ],
        "order_mode": "SEQUENTIAL",
        "global_combine": "ALL",
        "within_sec": 600,
        "final_after": "FVG_NEW",
        "final": {"kind": "OZ", "tfs": ["1m"], "validation_mode": "BLIND", "trigger_mode": "BREAKER"}
      },
      "needs_clarification": false,
      "clarification_question": null,
      "message_ko": "상향 교차 후 10분 이내 신규 상승 FVG가 생기면 최종 OZ 감시"
    }

이 예시는 교육용 계약이다. 실제 감시 등록 명령이 아니다.

## 조건

공통 필드는 kind, tfs, direction, bar_state, tf_combine이다. Python 입력에서는 단일 tf도 받으며 검증 후 tfs로 정규화한다. steps/branches에는 길이 제한이나 슬롯 변환이 없다. 빈 steps는 선행 조건 없는 OZ 감시다.

| kind | 추가 필드 | 의미 |
|---|---|---|
| TREND | 없음 | 기존 SMA20/HMA50 기울기 추세 의미 |
| MA_STATE | ma_family, fast_period, slow_period, side | 현재 배열 |
| MA_PRICE_STATE | ma_family, slow_period, side | 가격과 MA 위치 |
| MA_SLOPE_STATE | ma_family, slow_period, side, lookback | 기울기 |
| MA_CROSS | ma_left, ma_right | 새 교차 사건, 서로 다른 계열도 가능 |
| FVG_STATE | side, state(EXISTS/AREA) | 존재 / 접촉 상태 |
| FVG_NEW | side | 새 영역 생성 |
| FVG_TOUCH | side | 영역 접촉 |
| WONBI_TOUCH | side | 원비 상단·하단 접촉 |
| EXTERNAL_LIQUIDITY_TOUCH | level, side, capture | 기존 SWEEP 외부유동성 접촉. 알림 위치에는 실제 터치한 레벨 이름·가격이 나온다 |
| SESSION_START | session | 현재 config 세션 시작 |
| OZ_ALERT | validation_mode, trigger_mode | 선행 OZ 사건 |
| REGIME_BAND | regime_families, relation, family_combine | MT5 레짐 열 비교 |
| PRICE_LEVEL | level, relation | 숫자 가격 또는 공식 레벨 |
| LIQUIDITY_LEVEL | level, relation | 기존 외부유동성 Fact 레벨 |

TF·종목·세션·OZ 프로필 목록은 현재 코드/config에서 읽는다. 문서의 예시를 고정 계약 목록으로 쓰지 않는다. MA는 SMA/WMA/EMA/HMA와 양의 정수 기간이며 현재 Python Fact를 사용한다.

## 관계·수명

- direction: LONG/SHORT/BOTH. 단계/최종의 SAME_AS_PREVIOUS_DIRECTION은 직전 조건 방향을 계승한다.
- order_mode: SIMULTANEOUS / SEQUENTIAL / UNORDERED.
- global_combine: ALL / ANY / INDEPENDENT. 순차·순서무관은 ALL 연결이다.
- tf_combine: ALL / ANY / INDEPENDENT. 독립 TF는 별도 분기다.
- within_sec: **조건끼리 허용하는 시간**. 처음 조건부터 마지막 조건까지의 간격이며 순차/순서무관 복수 단계에 필요하다.
- final_window_sec: **최종 OZ를 기다리는 시간**. within_sec와 별개이며 임의의 7200초 등을 만들지 않는다.
- final_window_from: 최종 대기 시간을 세기 시작하는 때. `ALL_CONDITIONS`(기본, 조건이 모두 모인 때부터) 또는 `FIRST_CONDITION`(처음 조건이 발생한 때부터). `final_window_sec` 또는 `lifecycle.expires.seconds`와 함께만 쓴다. 처음 조건부터 세면 마지막 조건이 올 때 이미 대기 시간이 지났을 수 있으며, 그 경우 감시를 열지 않는다. 시작 사건의 시각은 그 사건이 발생한 시각이지 처음 본 시각이 아니다.
- cancel_conditions: 후보 취소 조건 목록.
- final_conditions: 최종 OZ 알림 순간 재검사 조건 목록.
- after_conditions: SEQUENTIAL/UNORDERED setup이 완료된 뒤 기다리는 조건. 최종 대기 시간은 `final_window_from` 기준으로 정해지며, 이후 FVG 터치 때 다시 시작하지 않는다. 접촉 조건을 통과하면 최종 OZ 감시가 열린다.
- lifecycle.precondition_check: 선행 **상태** 조건을 검사하는 방식. `WHILE_ACTIVE`(기본, 감시하는 동안 계속 검사하고 거짓이 되면 취소) 또는 `AT_START`(감시를 시작할 때만 검사하고 이후 거짓이 되어도 취소·차단하지 않음). 사건 조건·취소 조건·최종 조건은 이 선택과 무관하다.
- lifecycle.expires: `seconds` 또는 `bars`+`tf`. 봉 수 수명은 시간봉(`FINAL` 등) 자신의 확정봉을 센다. `bars_setting`(대문자 config 키, 예: MAX_BARS_AFTER_B0)을 함께 쓰면 그 설정이 양의 정수일 때 그 값을, 아니면 `bars`를 한도로 쓴다. 확정봉 수가 한도를 **넘으면**(`>`) 종료한다.
- 외부유동성 연결: `EXTERNAL_LIQUIDITY_TOUCH` 단계에 `capture` 이름을 주고, 최종 OZ에 `level_gate`를 둔다. `level_gate`는 `ref`(캡처 이름), `atr_period`(기본 14), `atr_mult`(기본 1.5)이며 최종 OZ 시간봉은 터치와 같은 `SOURCE`여야 한다. 터치한 레벨과 최종 OZ 저점·고점의 거리가 ATR×배수 이내일 때만 통과한다(OZ 엔진의 기존 외부유동성 Gate가 판정한다).
- branches: 독립 전략 분기 목록. 각 분기는 steps와 필요한 방향·최종 TF·시간 제한 등을 명시한다. 공통 설정을 상속하지만 수명과 판정 상태는 따로 갖는다. 공통 steps와 동시에 쓰지 않는다. 같은 source TF에 속하는 추세·원비·MA·FVG 조건을 한 branch로 묶어 서로 다른 TF의 조건이 섞이지 않게 한다. 분기 개수 제한은 없다.
- persistent: 반복 감시 여부.
- bar_state: UNSPECIFIED / CLOSED / FORMING. 미지정 MA_CROSS 기본은 확정봉이다. FVG_NEW·외부유동성 사건은 기존 처리기의 확정봉 의미를 사용한다.
- REGIME의 MATCHING_FAMILY: TF 사이에서 같은 PRICE/RSI/STO/DI 계열이 통과해야 한다.
- final: OZ(복수 tfs, NORMAL/BLIND, OZ/BREAKER) 또는 NOTIFY.

## SPECIAL 고유 구조

base_special은 SPECIAL1~7 참고 출처다. 일반 전략은 사용자 의미를 steps로 보존한다. SPECIAL4/5 고유 cycle·부모/자식 교체·봉 수 만료까지 그대로 요청한 경우만 inherit_base_rules=true와 special_parameters를 사용한다. 허용 인자는 vocabulary에서 제공하며 현재 원본에서 신뢰된 compiler가 읽는다.

기존 구조가 요청을 표현하지 못하면 과거 형태에 억지로 맞추지 않는다. 모호한 의미는 질문하고 실행할 수 없는 의미는 적용 오류로 알린다. 알 수 없는 필드·TF·조건은 validator가 거부한다.

## 실행 연결

생성 파일에 신뢰된 Part3의 IntentMachine/IntentPort가 포함된다. lab/Part3를 import하지 않는다. register(manager)가 현재 Part1 입력·Fact·OZ·출력 API에 연결한다.

현재 공개 플러그인 API에는 자유로운 FVG/SWEEP 구독 선언이 없다. Part3 어댑터가 manager의 구독 조회와 Fact 수신을 인스턴스에서 확장한다. 기존 메서드를 그대로 호출한 뒤 검증된 Fact를 관찰한다. Part1/Part2 소스 수정, 처리기 계산 복제, 전략의 네트워크 직접 전송은 하지 않는다.

Ollama 구조화 출력으로 모델 JSON 형식을 제한하고 의미·권한은 Part3 validator가 별도로 검사한다. 잘못된 의도는 사용자 적용 전에 차단한다. 모든 자연어 표현의 정확성을 보장하는 문서는 아니다.

## 확정 정답 50개 기능 확인

tests/confirmed_answers.py는 사용자 확정 #001~#050의 **검증용 의미 fixture**다. 자연어를 해석하는 운영 코드나 파인튜닝 데이터가 아니다. tests/test_confirmed_50.py가 schema, intent→Recipe, validator, compiler, 생성 코드 문법/모듈 로딩을 확인한다. 실제 모델은 호출하지 않는다.

옛 정답지의 XAUUSD 표기는 현재 config의 실제 골드 종목 XAUUSD+로 연결한다. 레벨 PREV_DAY_LOW는 현재 Python 외부유동성 계약의 PDL에 해당한다. 모델은 계산식을 만들지 않으며 현재 Fact/처리기를 사용하는 생성 어댑터가 이 계약을 실행한다.

Part3의 기능 완성이 우선이다. 구조화 JSON 출력 강제와 validator 차단은 유지한다. Qwen 실모델 프롬프트 튜닝·해석 성능 추가 검증·학습은 별도 지시 전까지 하지 않는다. 50/50 기능 통과는 모델의 자연어 해석 정확도 100%를 의미하지 않는다.

## 해석과 검증의 역할

사용자는 자연어 아이디어를 입력하고 AI가 의미를 해석한다. Part3는 원문을 정규식으로 다시 해석하지 않는다. 종목, TF, MA 상태/사건, OZ 프로필을 원문에서 재추출하여 AI 결과를 수정하거나 거부하지 않는다.

Part3는 JSON 형식, 현재 허용된 조건·TF·종목·기간·프로필, 관계 필드와 컴파일 가능한 실행 계약을 검증한다. 잘못된 JSON/지원하지 않는 값은 차단하며 코드·경로·파일 쓰기·삭제·Shell 도구 권한은 AI에 제공하지 않는다. 의미 해석의 정확성은 AI와 사용자 확인 단계에서 검토한다.

SPECIAL4/5 고유 수명 규칙은 기존 schema의 명시적 inherit_base_rules/special_parameters로 요청할 때만 현재 Part1 소스에서 생성한다. 이 생성기는 슬롯이나 Recipe v1을 경유하지 않는다.
