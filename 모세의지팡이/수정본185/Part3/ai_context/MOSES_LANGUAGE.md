# 모세 전략 자연어 해석 수칙

- 현재 상태와 새 사건을 구분한다. “EMA50이 EMA200 위”는 `MA_STATE`; “상향 교차·골크”는 `MA_CROSS` 사건이다. “데크”는 하향 교차다.
- “FVG가 있다”는 `FVG_STATE`, “FVG를 접촉했다”는 `FVG_TOUCH`, “새 FVG가 생겼다”는 `FVG_NEW`다. 서로 대체하지 않는다.
- “무지성”은 최종 OZ 검증 `BLIND`, “브레이커”는 최종 트리거 `BREAKER`다. OZ 프로필은 `NORMAL/BLIND`와 `OZ/BREAKER`의 네 조합이다. 알 수 없는 수식어를 임의 프로필로 해석하지 않는다.
- “A 후 10분 안에 B”는 `order_mode=SEQUENTIAL`, `within_sec=600`이다. 동시에 유지되는 두 상태와 다르다.
- “A 후 1분봉 10개 안에 B”는 `order_mode=SEQUENTIAL`, `within={"bars":10,"tf":"1m"}`이다. 시간으로 말하면 `within={"seconds":…}`다(`within_sec`와 함께 쓰지 않는다). 봉 수는 A가 판정된 봉 다음 봉부터 실제 봉을 센다. 기다리는 중에 A가 다시 나오면 새 A부터 다시 잰다.
- “A가 나온 뒤 1시간봉 6개 동안 B를 취소”는 A 조건에 `recent={"bars":6,"tf":"1h"}`를 붙여 `cancel_conditions`에 넣는다. “최근 기간 안에 A가 없을 때만”은 같은 조건에 `negated=true`를 붙여 steps에 둔다. 올존은 올존이 완성된 순간(알림 발생)부터 세며 B0·인정기간과 무관하다.
- 감시 방향과 반대 방향의 사건은 단계 `direction=OPPOSITE`다(매수 감시면 매도 올존). 매수·매도를 한 전략(BOTH)으로 쓸 때 쓴다.
- 진행봉/확정봉, 취소, 유효시간, 방향 계승, 조건의 ALL/ANY/INDEPENDENT는 원문의 뜻을 유지한다. 현재 실행 Recipe에서 표현할 수 없다면 조용히 생략하지 않는다.
- 신규 의도는 조건/사건 수에 제한이 없는 Recipe v2로 전달한다. 과거 SPECIAL3에 맞추기 위해 원문에 없는 FVG 접촉을 추가하지 않는다.
- “기본더블비”는 지정 TF의 TREND와 같은 방향 WONBI_TOUCH를 모두 만족하는 조합이다. 매수는 상승추세와 하단 WONBI, 매도는 하락추세와 상단 WONBI다. 방향을 제한하지 않았다면 두 방향을 독립 branch로 표현하고, 최종 OZ TF나 감시 시간을 임의로 추가하지 않는다.
- 백테스트할 전략의 조건 자체만 요청하고 후속 OZ를 말하지 않았다면 최종 행동은 조건 자체의 NOTIFY다. OZ 요청이 있다면 그 TF·방향·프로필을 그대로 사용한다. 실행 기간은 전략 감시의 final_window_sec로 옮기지 않는다.
- 실제 TF·종목·지표 이름·레벨·OZ 프로필은 이 문서의 고정 목록이 아니라 현재 Part1/Part3 코드의 계약을 따른다.
