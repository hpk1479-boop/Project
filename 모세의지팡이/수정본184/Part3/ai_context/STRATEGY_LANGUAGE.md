# 전략 의도 출력 계약

모델은 `supported`, `intent`, `interpretation`, `needs_clarification`, `clarification_question`, `message_ko`로 이루어진 JSON 객체 하나만 반환한다. 소스 코드, 파일명·경로, Recipe `source_text`를 생성하지 않는다. 지원되지 않는 요청은 `supported=false`와 이유 코드를 사용한다.

`interpretation`은 `direction`(LONG/SHORT/BOTH), `symbols`, `steps`, `order_mode`(SIMULTANEOUS/SEQUENTIAL/UNORDERED), `global_combine`(ALL/ANY/INDEPENDENT), `within_sec` 또는 `within`, `final_window_sec`, `final`을 담는다. 각 단계는 `kind`, `tf` 또는 `tfs`, `direction`, 필요한 조건 매개변수만 담는다. 최종 OZ에는 `kind=OZ`, `tf` 또는 `tfs`, `validation_mode`(NORMAL/BLIND), `trigger_mode`(OZ/BREAKER)를 쓴다. 방향 계승은 단계의 `direction=SAME_AS_PREVIOUS_DIRECTION`으로, 감시 방향의 반대는 단계의 `direction=OPPOSITE`로 표현한다.

이동평균은 `MA_STATE`, `MA_PRICE_STATE`, `MA_SLOPE_STATE`, `MA_CROSS`, `MA_PRICE_CROSS`, `MA_PRICE_TOUCH`를 구분한다. `MA_CROSS`에는 `ma_left="WMA17"`, `ma_right="SMA20"`처럼 두 이동평균을 각각 명시한다.

가격-MA 교차는 `{"kind":"MA_PRICE_CROSS","tfs":["1d"],"ma_family":"SMA","slow_period":20,"relation":"BOTH"}`처럼 표현한다. relation은 `BREAK_UP`(이전 가격 ≤ 이전 MA, 현재 가격 > 현재 MA), `BREAK_DOWN`(반대), `BOTH`(양방향)다. 양방향 전략에서 양방향 교차는 해당 교차 방향으로 한 번 알린다. 가격-MA 터치는 `{"kind":"MA_PRICE_TOUCH","tfs":["1m"],"ma_family":"EMA","slow_period":200}`처럼 표현하며, 판정 봉의 저가 ≤ MA ≤ 고가를 터치로 본다. 두 사건 모두 SMA/WMA/EMA/HMA와 임의의 양의 정수 `slow_period`를 지원한다. 계산은 현재 Part1 MA Fact 계층을 공유한다. SMA/WMA/HMA의 시가 및 EMA의 종가 입력 계약을 변경하지 않는다.

MA 교차·터치 사건은 `bar_state` 생략/UNSPECIFIED/CLOSED이면 확정봉, 명시적인 FORMING이면 진행봉이다. 초기 관찰 시 이미 발생한 사건과 같은 확정봉의 중복 관찰은 발화하지 않는다. 진행봉은 거짓→참의 새 사건을 발화하며 터치는 새 봉에서도 다시 발생할 수 있다. MA 값이 아직 계산되지 않았으면 사건을 발생시키지 않는다.

`SEQUENTIAL`은 `within_sec` 생략 또는 null이면 대기 시간 제한 없는 순차 연쇄다. 양의 정수가 있으면 기존 사건 간 시간 제한을 유지한다. `UNORDERED`의 복수 사건에는 기존대로 시간 제한을 명시한다. 순차 사건은 직전 소비한 사건보다 뒤의 시각이어야 하며 같은 시각의 사건이나 과거 사건으로 다음 단계를 채우지 않는다. `within_sec`는 조건끼리 허용하는 시간이고 `final_window_sec`는 최종 OZ를 기다리는 시간으로, 둘은 별개다. 최종 대기 시간은 기본적으로 조건이 모두 모인 때부터 세며, `final_window_from=FIRST_CONDITION`이면 처음 조건이 발생한 때부터 센다. 선행 상태 조건은 기본적으로 감시하는 동안 계속 검사하고, `lifecycle.precondition_check=AT_START`이면 감시를 시작할 때만 검사한다. 봉 수 수명은 `lifecycle.expires`의 `bars`와 `tf`로 쓰고, `bars_setting`에 대문자 config 키를 주면 그 설정 값을 우선 쓴다. 후속 터치는 `steps`에 MA_PRICE_TOUCH를 추가한다. 번호나 원문에 따른 별도 경로는 없다.

사건 뒤 기간은 `{"seconds":S}` 또는 `{"bars":N,"tf":"1m"}`이다. 조건끼리의 기간을 봉 수로 정하면 `within`에 쓴다(“올존 후 1분봉 10개 안에 3분 아웃인”은 SEQUENTIAL과 `within={"bars":10,"tf":"1m"}`). 봉 수는 사건이 판정된 봉 다음 봉부터 실제 봉을 세고, 시간은 사건 시각부터 잰다. “사건 뒤 일정 기간 동안 취소”는 그 사건 조건에 `recent`를 붙여 `cancel_conditions`에 넣는다(“1시간 매도 무지성 올존 후 1시간봉 6개 동안 하단 원비 터치 취소”는 매수·매도 BOTH 전략의 원비 터치 단계와, `direction=OPPOSITE`, `recent={"bars":6,"tf":"1h"}`인 1시간 BLIND 올존 취소 조건). 올존은 올존 엔진이 완성한 순간부터 세며 B0나 인정기간이 아니다.

`FVG_STATE`(존재), `FVG_NEW`(신규 생성), `FVG_TOUCH`(접촉)는 다른 의미다. 외부유동성 접촉은 `EXTERNAL_LIQUIDITY_TOUCH`와 실제 레벨 이름을 사용한다. 터치한 레벨과 최종 OZ 저점·고점의 ATR 거리 검사가 필요하면 터치 단계에 `capture` 이름을 주고 최종 OZ에 `level_gate={"ref":"<그 이름>","atr_period":14,"atr_mult":1.5}`를 쓴다(기간·배수는 생략하면 14, 1.5이며 최종 시간봉은 `SOURCE`). HIGH/LOW만 지정했다면 해당 방향의 공식 레벨 전체를 의미한다. 신규 FVG 뒤의 OZ 시작 시점은 `final_after=FVG_NEW`(직후) 또는 `FVG_TOUCH`(접촉 후)로 보존한다. 사용자가 이를 말하지 않았고 전략 의미가 달라지면 짧게 묻는다.

Part3가 검증한 뒤 Recipe v2의 strategy_intent로 전달한다. steps의 최대 개수는 없다. cancel_conditions, final_conditions, bar_state, persistent도 보존한다. 전략만 요청하면 사용자 적용 후 미리보기를 만들고 별도 생성 버튼으로 Test_SPECIAL 파일을 만든다. 새 전략과 백테스트를 함께 요청하면 전체 전략 및 실행 계획을 사용자가 확인한 후에만 같은 생성기로 파일을 만들고 기존 백테스트 작업에 전달한다. 변환할 수 없는 의미는 다른 조건으로 바꾸지 않는다. 초안이나 과거 생성 파일은 정답 사례가 아니다.
