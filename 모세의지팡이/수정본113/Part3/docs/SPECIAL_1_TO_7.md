> 현재 OZ 계약: 올존 · 무지성 올존 · 브레이커 올존 · 무지성 브레이커 올존(NORMAL/BLIND × OZ/BREAKER)입니다. SPECIAL5는 상위 NORMAL/BREAKER와 확정봉 EMA50/200을 사용하며, SPECIAL7은 15분 TREND → 1분 NORMAL/BREAKER입니다.

# SPECIAL 1~7 — 현재 원본 로직과 카드 대응

기준은 현재 공통 프로젝트 루트의 `Part1/program/SPECIAL/SPECIAL1.py`~`SPECIAL7.py`입니다. 이름에서 추정한 일반 기술적 지표 예제가 아닙니다. 생성기는 원본 본문을 보존하고 변경한 설정만 반영합니다. 아래 설명은 작성 시점의 예시이며 현재 불러온 Recipe와 실행 본문이 우선입니다.

## 공통 계약

모든 전략은 `register(manager)`로 등록합니다. 일반형은 `register_special_bundle(strategies=..., timed_chains=...)`, 복잡한 전략은 이 계약과 함께 `special_api`의 OZ/watch callback을 사용합니다. 원본 알림 게이트, 감시 payload, 재발행 및 취소 관계를 유지해야 합니다.

TREND는 SMA20 OPEN 기울기와 HMA50 기울기 방향의 일치입니다. WONBI는 기존 OPEN4 밴드 접촉입니다. ‘현재 EMA50 > EMA200’, ‘확정봉에서 EMA50/200 교차’, ‘현재값과 2봉 전 HMA 비교’는 서로 다른 조건입니다.

### 재현의 범위

기본 생성 파일과 원본은 등록 정의 및 원본 runtime 구조를 기준으로 비교했습니다. 바뀌는 것은 새로운 전략의 등록/감시/상태 namespace, 생성 메타데이터, 화면에서 읽어 고정한 최종 프로필입니다. 원본의 상태 파일을 그대로 이어받지 않습니다. 초기 상태가 비어 있는 원본과 신규 파일을 같은 데이터/프로필/시각 조건에서 비교해야 합니다.

## SPECIAL1 — 내부유동성

기본 종목 XAUUSD+, NAS100. 1h/2h/3h/4h에서 각각 같은 TF의 TREND와 WONBI가 함께 성립하면 방향을 계승하여 1m,2m,3m,4m,5m,6m,10m,12m,15m,20m,30m,1h의 브레이커 OZ를 감시합니다. 최종 기본 프로필은 NORMAL/BREAKER입니다.

현재 AI 조건 목록의 시간봉별 독립 branch로 표현합니다. 시간봉 모두를 동시에 충족하는 AND로 바꾸지 않습니다. 수동 카드 생성기는 제거했습니다.

핵심 원본: `_main_1_specs`, `_main_alert_template`, `register`.

## SPECIAL2 — 외부유동성 스윕

12 TF에서 각각 SWEEP를 감시하고, 발생한 그 TF의 OZ로 연결합니다. PDH/PDL/4H/8H/PWH/PWL/SESSION 레벨 목록과 ATR14 × 1.5 자격 조건을 사용합니다. 최종 NORMAL/BREAKER입니다.

화면에서는 첫 카드에 12 TF 묶음을 채웁니다. 이 묶음은 12개 동시 충족이 아니라 12개 독립 등록입니다. `same_tf_oz=true`가 TF 계승을 표현합니다. 레벨과 ATR은 상세 설정에서 편집합니다.

단순 SWEEP 조건만 복사하면 원본 알림의 위치·ATR 정보를 보완하는 callback이 없어집니다. `_main_2_specs`뿐 아니라 이벤트 handler 전체가 생성 파일에 남습니다.

핵심 원본: `_main_2_specs`, SWEEP 위치/이벤트 보완 함수, `register`.

## SPECIAL3 — 장초반 추세 눌림

1. 1m 또는 2m EMA50/200 교차 사건.
2. 5m/6m 신규 FVG 사건을 같은 방향으로 연결. 기본 UNORDERED이며 두 사건의 간격 제한 1800초.
3. 연결 성립 뒤 5m/6m/10m/12m/15m 동방향 FVG 접촉을 기다림.
4. 1m/2m NORMAL/OZ 최종 감시. 반대 교차/반대 FVG 취소를 보존.

선행 교차/FVG의 `time_filters`와 최종 연결의 `final_time_filters`는 모두 빈 배열입니다(장초반 OPENING 필터 없음). 최종 알림 시간은 송출 직전의 FINAL_ALERT_TIME_FILTERS(또는 거래시간 슬롯)로만 정합니다. SPECIAL3의 기본값은 config의 MAIN_* 시간이 아니라 전략 전용 시간 `{"MAIN_ASIA": "0900-1100", "MAIN_LONDON": "1600-1800", "MAIN_NEWYORK": "2100-2400"}`(KST, 시작·종료 포함)입니다.

카드1 cross, 카드2 fvg_new, 카드3 fvg_touch, 카드4 final_oz입니다. EMA ‘배열’이 아니라 실제 교차 사건을 기억합니다. setup FVG의 ‘생성’과 후속 FVG의 ‘접촉’도 별도입니다.

### 중요한 시간 계산

현재 원본은 final_window_sec=7200 설정 외에 UNORDERED 완료 기한을 함께 사용합니다. 기본값에서 최종 기한은 다음 두 시각 중 빠른 시각입니다.

```text
min(
    두 선행 사건이 모두 갖춰진 시각 + final_window_sec,
    두 선행 사건 중 먼저 발생한 시각 + max_gap_sec
)
```

기본 1800초 간격일 때 09:00과09:10 사건이면 최종 기한이09:30인 실행 경로가 있습니다. ‘무조건 연결 이후2시간’으로 바꾸는 것이 원본 재현은 아닙니다. 이 처리는 `event_composer_domain.py`의 UNORDERED event memory와 child deadline 경로가 소유합니다. 생성기는 엔진의 이 로직을 패치하지 않습니다.

상수와 `_main_3_timed_chain()` 정의를 변경하면 지원 범위의 새로운 연결을 만들 수 있습니다. 이벤트 수/종류/기한 계산식을 바꾸는 요구는 custom runtime으로 작성하고 원본 엔진을 몰래 바꾸지 않습니다.

## SPECIAL4 — 30분 찢들

### setup

새30분봉이 시작되는 경계에서 직전 확정30분봉과 직전 확정15분봉을 참조합니다.15분봉은 기본 :15/:45 시작 봉이어야 합니다. 같은 방향에서15분 원비 접촉과30분 원비 비접촉을 확인합니다. 기준 가격과1분 ATR14는 그 cycle 시작에 고정합니다.

### 감시

기본360초 동안1분 OZ를 감시합니다. 방향으로 선행 가격 이동이 고정ATR×ATR_MULT 이상이면 무효화합니다. 현재 종가 하나가 아니라 감시 구간의 high/low 극값을 사용합니다. `>=` 경계값까지 취소 대상입니다. 타임아웃과 이전 cycle 교체/정리 로직은 원본을 유지합니다.

### 최종 OZ 직전 재검사

- 15m HMA50: 현재 vs2봉 전 기울기와 현재 가격 위치.
- 3m EMA50/200 배열 + HMA168 현재 vs2봉 전 기울기.
- 1m EMA50/200 배열.

LONG/SHORT 비교 방향을 반대로 적용합니다. setup의 확정봉 참조와 최종 신호 순간의 현재값 참조를 혼합하지 않습니다. 최종 NORMAL/OZ입니다.

AI의 명시적 SPECIAL4 상속은 cycle_tf/touch_tf/middle_tf/execution_tf와 각각의 gate 인자를 사용합니다. 수동 카드나 슬롯을 경유하지 않습니다. 원본 `_current_trend_allowed`와 `_excursion_exceeded`의 의미는 그대로 사용합니다.

핵심 원본: `Special4Runtime`, `_latest_row_before`, `_current_trend_allowed`, `_excursion_exceeded`, cycle 생성/갱신/완료 handler.

## SPECIAL5 — 프렉탈 MS 셋업

상위5m/6m/10m/12m/15m/20m/30m/1h에 NORMAL/BREAKER 부모 Watch를 TF·심볼당 하나 등록합니다. 선행은 브레이커 + source TF 마지막 확정봉 EMA50/200 방향입니다. 레짐밴드 대체 판정·지표군별 별도 TRUE B0·독립 소비 상태는 삭제했습니다. 삭제로 인한 전략 결과 변경을 보존하지 않습니다.

부모 신호의 방향을 계승해 하위1m/2m/3m BLIND/OZ를 감시합니다. 중요한 수명 규칙은 다음과 같습니다.

- 같은 방향에서 새 부모가 생기면 이전 하위 묶음을 교체합니다.
- 부모 TF 반대 HMA6/17 교차는 하위 묶음 전체를 취소합니다.
- 하위 TF 반대 교차는 해당 하위 감시만 취소합니다.
- 각 하위 만료는 자기 TF에서 b0 이후 확정봉 수로 계산합니다. 기본 원본 `bars > MAX_BARS_AFTER_B0` 비교를 그대로 유지하며 >=로 바꾸지 않습니다.
- 하나의 하위 최종 신호가 완료되면 그 부모 묶음의 나머지 자식도 종료합니다.

기본 MAX_BARS_AFTER_B0는 원본 설정을 읽고, 화면에서 null이면 그 경로를 유지합니다. 값을 지정하면 새 전략의 해당 상한만 고정합니다.

카드1 parent에는8개 TF를 묶습니다. 카드2~4는각각1m/2m/3m child입니다. SPECIAL5 확정봉 EMA필터, 부모/자식 취소 선택이 상세 파라미터입니다. 부모·자식의 상태를 하나의 현재값 조건으로 치환하면 원본 의미가 사라집니다.

핵심 원본: `Special5Runtime`, `_source_ema_trend_allowed`, source watch 생성, `_opposite_hma_cross_after`, 자식 만료/취소/완료 처리.

## SPECIAL6 — 추세 FVG 눌림

15m 또는30m 각각에서 EMA50/200 배열 + HMA168 방향 + 동방향 FVG 접촉을 함께 확인합니다. 각 TF에 LONG/SHORT 등록이 따로 있습니다. 최종1m~6m NORMAL/BREAKER입니다.

기본 화면은15분/30분 두 branch 카드입니다. HMA 방향은 원본 기울기 facts 기준이며 단순HMA현재가상하 비교와 다릅니다. 조건을 수정하면 `_main_6_specs` 정의를 다시 만들지만 원본 알림 handler는 남습니다.

## SPECIAL7 — 15분 추세 · 1분 브레이커 올존

15분 TREND 방향을 기준으로 같은 방향1분 NORMAL/BREAKER OZ를 감시합니다. 화면은 첫 branch 카드가15분TREND이며 최종설정이1분 브레이커 올존입니다. 기존 레짐 필터 제거로 신호가 달라지는 것은 의도한 전략 변경입니다. 이름이 비슷하다는 이유로 임의 RSI/MACD 조건을 붙이지 않습니다.

## 직접 코드 비교 순서

대상 원본의 `register`에서 시작해 호출되는 함수/클래스를 읽습니다. 생성 파일의 `PART3_RECIPE`를 확인하고 같은 함수/클래스의 변경값과 namespace만 비교합니다. 엔진 의존 시간 규칙은 현재 `Part1/program/event_composer_domain.py`, 감시 계약은 현재 `watch_orchestrator.py`, 이벤트 시간/상태는 현재 `domain_clock.py`, `domain_memory.py`, `event_composition.py`를 읽습니다.

현재7개 전체의 코드 참조는 `SPECIAL_REVIEW_REFERENCE.html`에도 포함되어 있습니다. 그 보고서의 설계 제안과 현재 구현은 구분하고, 이 폴더의 코드/이 문서를 최신 구현 기준으로 사용하세요.
