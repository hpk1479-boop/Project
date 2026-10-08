# 구현·성능·결과 검증 보고서

## 1. 제출 상태 — 전체 LIVE 동일성 승인본은 아님

이 산출물은 **현재 제공본 Part2의 조건부 계산 및 PARTIAL 결과 보존 수정본**이다. Part1은 읽기 전용 기준으로만 사용했다. 수정 전 분석은 `01_ANALYSIS_KO.md`, 파일별 변경과 재현 명령은 `03_DIFF_AND_REPRODUCE_KO.md`, 원시 계측/시험 로그는 같은 디렉터리에 있다.

다음 네 가지는 구분해야 한다.

1. **구현 및 제한된 검증 완료:** native 호출 이전의 게이트, 실행 내 exact-prefix 복원, 이미 진행 중인 OZ 후보/소비 복원 시험, observation 단위 취소 commit, 기존 대시보드 PARTIAL 표시.
2. **부분적인 Part1 대조 완료:** 실제 Part1 OZ 메서드 30개를 읽기 전용 AST oracle로 실행한 양성/취소/만료/재시도 대조 및 실제 WONBI 코드의 09:32 접촉 대조. 전체 LIVE 모니터/터미널/스레드 실행을 대체하는 증거는 아니다.
3. **미확정:** 한 달 전체 SPECIAL1~7의 비어 있지 않은 이벤트·setup·WATCH·ARMED·cancel·expire·consume 타임라인을 원본 Part1 LIVE와 대조한 결과. 실제 터미널 native seed, 동일 관측 스케줄/호출 위상 캡처는 확인되지 않았다. `UNVERIFIED`를 `VERIFIED`로 바꾸지 않았다.
4. **미구현 세분화:** 하위 mapped group 내부를 6분→3분→1분으로 더 분할하는 단계별 최적화. 모든 전략/모든 TF에서 이 수준까지 최적화했다고 주장하지 않는다.

따라서 사용자 요청의 최종 승인 조건인 **“Part1 LIVE 전체 동일 + 최적화 전후 전체 이벤트/시각 동일 + 한 달 실측”을 모두 충족했다고 선언하지 않는다.** 아래 수치는 그 제한을 넘어서 해석하면 안 된다.

## 2. 구현 경계

기존 공통 경로는 전략 callback 전에 `OptionalFeatureRegistry.advance`가 Percentile kernel을 호출했다. 새 SPECIAL/OZ WATCH 경로는 이 위치에서 feature marker와 원시 관측 입력만 기록한다. 실제 feature/history 요청이 발생할 때 필요한 native stream을 생성하고, 그 stream이 마지막으로 처리한 정확한 checkpoint부터 현재까지 따라잡는다. 일반 전략과 primitive WATCH는 기존 eager 경로가 기본이다.

새 저장은 **현재 실행의 메모리 안 raw 입력·관측 ordinal·token·checkpoint**뿐이다. 다른 실행의 feature를 불러오거나 새 디스크 지표 캐시를 만들지 않는다. 같은 시각의 복수 tick도 ordinal로 구분한다. reconstruction 요청은 현재 callback binding과 이전 prefix 범위만 허용하며 만료된 binding·미래 cursor는 `E_FUTURE_READ`로 거부한다.

과거 replay 관측에서는 실제 lower engine의 조용한 상태 전이·소비를 재현한다. 현재 관측에만 현재 Watch 환경을 넣는다. 이것은 “과거에서 완성된 알림을 지금 다시 발생”시키는 방식이 아니다. 활성화 순간 이미 완성돼 Part1이 silent consume하는 후보는 되살리지 않고, 아직 진행 중인 후보는 보존한다. state reset이나 임의의 짧은 lookback으로 대체하지 않았다.

보존을 우선한 대가로 첫 gate가 늦으면 첫 준비 입력까지 계산할 수 있다. 같은 비용을 나중에 지불할 가능성이 있으며 raw 로그 메모리는 입력량에 비례한다. native/상태 replay 비용을 제외한 속도 향상 수치는 사용하지 않는다. `max_bytes`는 IPC 메시지 경계이지 전체 raw 로그 메모리 상한이 아니다.

SPECIAL1~7과 OZ_PERCENTILE_SOURCE_V1 WATCH는 진행봉 조건을 포함한다. **실제 실행 시작 시 ONE_MINUTE_CLOSE는 `E_INTRABAR_CADENCE_REQUIRED`로 거부**한다. 날짜/자료 취득 계획 작성 자체는 허용하지만 그 모드로 전략을 실행하지 않는다. 기존 저장 설정을 몰래 TICK으로 바꾸지 않았으며, 사용자가 명시적으로 TICK 또는 LIVE_PARITY를 선택해야 한다. 두 모드 중 어느 것도 실제 Part1 polling 캡처 없이 완전한 LIVE 위상 동일성을 보증하지 않는다.

## 3. 공통 OZ 복원·소비 의미

게이트 이전의 lower engine 호출 자체는 0이 될 수 있지만 과거 lower 상태가 불필요하다는 의미는 아니다. OUT/IN episode, cross, TRUE-B0, validation, 중복키, 취소/만료/소비까지 정확하게 재생한다. 정상 관측을 생략했던 기간 중 실제 입력 gap이 있었다면 원래 gap 순서에서 restart guard를 적용한다. 누락된 입력을 새로운 가격/봉으로 보간하거나 native unavailable 값을 0으로 채우지 않는다.

MAIN 세션 밖 최종 알림 억제는 전략별로 같지 않다. Part1 코드상 SPECIAL1/2/3/6/7은 child 유지, SPECIAL5는 형제 child까지 종료, SPECIAL4는 시간필터/불확정입력/명확한 취소 사유를 구분한다. 이 구분을 구현에 반영했다. 세션 시간·수식·가격 임계값을 최적화 명목으로 재설정하지 않았다.

## SPECIAL1

| 항목 | 구현 / 판정 |
| --- | --- |
| Part1 대응 | SPECIAL1 / Composer의 TREND + WONBI → NORMAL DIVERGENCE OZ |
| LIGHT / 최소 feature | 상위 1h·2h·3h·4h의 현재 OHLC와 OPEN 4개 WONBI를 먼저 확인한다. 접촉한 TF에서만 HMA50을 포함한 실제 TREND engine frame을 만든다. |
| 선행 게이트 / 정확한 시점 | 같은 상위 TF의 방향 TREND와 WONBI 접촉이 MAIN 세션의 현재 관측에서 모두 성립하여 child가 생성되는 시점. 완성될 상위 봉의 최종 high/low를 읽지 않는다. |
| 봉중 / 완료봉 | WONBI는 진행봉의 관측된 high/low, TREND는 현재 snapshot. 09:32 최초 접촉 검증에서 같은 09:32에 arm하며 10:00까지 기다리지 않았다. TREND 방향은 해당 테스트에서 명시적 입력으로 고정했다. |
| ARMED 전 생략 | 하위 Percentile 네 family, 하위 OPEN HMA6/17, mapped OZ 상세 frame 및 OZ observe. WONBI 미접촉 TF의 TREND용 HMA50도 생략한다. |
| 다음 단계 / 활성 TF·feature·engine | 현재 전략의 OZ base 1m~1h와 HistoricalOZEngine.required_timeframes가 산출한 NORMAL mapped union. Percentile PRICE/RSI/STO/DI + HMA6/17 + HistoricalOZEngine(NORMAL,DIVERGENCE). |
| 게이트 재구성 / 진행 중 후보 | 실행 내 마지막 native/OZ checkpoint부터 게이트의 실제 관측 token까지 순서대로 재현한다. 과거 관측은 environments={}로 조용히 처리하고, 현재 token에만 실제 child 환경을 부여한다. OUT episode, IN 복귀, HMA 극값·cross, TRUE-B0 후보, mid-validation, 완료·취소·만료, registration/alert key와 silent consumption을 복원한다. 최초 호출에는 그 실행의 준비 입력 이력도 필요할 수 있다. 임의의 7/10봉 제한은 두지 않았다. |
| 완료 / consumption | 유효 하위 OZ 완료는 원래 내부 소비 규칙을 따른다. MAIN 안에서 최종 전달되면 해당 공유 child를 종료한다. MAIN 밖 억제 시 OZ 후보는 소비될 수 있으나 Composer child는 유지한다. 과거 완료 이벤트를 재방송하지 않는다. |
| 취소 / 만료 | 하위 OZ의 반대 cross·one-way·10/7/7 규칙을 기존 엔진 그대로 사용한다. 상위 조건이 이후 false가 됐다는 이유로 sticky child를 새롭게 삭제하거나 TTL을 추가하지 않는다. |
| HEAVY OFF / 복귀 | 유효 child가 하나도 없으면 하위 엔진 호출 OFF. 상위 WONBI/TREND와 signature를 가진 LIGHT로 복귀한다. 엔진 상태를 무조건 초기화하지 않는다. |
| Part1 / 기존 Part2 / 시각 비교 | 아래의 Part1 비교는 선택한 실제 메서드·합성 양성 상태기계 대조 수준이다. 원본 LIVE 프로그램의 스레드·터미널 native seed·동시 polling 전체와 같은 시장 데이터로 실행한 통합 비교가 아니다. 전체 SPECIAL의 이벤트 개수·방향·시각·순서·취소·만료·소비 동일성은 미인증이다. 원본 Part2는 Generic worker 시작부터 __future__ import 거부로 실패했다. 그러므로 원본 대비 이벤트가 모두 같다는 판정은 하지 않았다. 수정 공통 코드의 eager/conditional 짧은 입력은 모두 0건으로 동일했다. |

## SPECIAL2

| 항목 | 구현 / 판정 |
| --- | --- |
| Part1 대응 | SPECIAL2 + SWEEP detector / external-liquidity TRUE-B0 qualification |
| LIGHT / 최소 feature / TF | 현재 SWEEP source TF 1m~1h의 완료봉/현재 OHLC, 외부 레벨 자료(4h·8h·1d·세션 포함), ATR14와 외부 생존 자격을 유지한다. 이 상태는 나중에 하위 OZ를 켜기 위해 필요하므로 임의로 끄지 않는다. |
| 선행 게이트 / 정확한 시점 | 해당 TF에서 실제 새 SWEEP fact가 성립하고 기존 ATR14×1.5/외부 자격 검사를 거쳐 same-TF child가 활성화되는 현재 관측. |
| 봉중 / 완료봉 | SWEEP detector의 새 완료봉 처리와 현재 자료로 하는 ATR 자격 유지 검사를 구분한다. detector를 상위 봉의 미래 최종 고저로 당기지 않는다. 실제 LIVE external controller 전체 통합은 별도 미인증이다. |
| ARMED 전 생략 | 후속 Percentile 네 family, HMA6/17, OZ와 그 mapped frame 계산. 외부 레벨/SWEEP 검사는 선행 조건이므로 남긴다. |
| 다음 단계 / 활성 TF·feature·engine | same-TF NORMAL DIVERGENCE OZ와 mapped TF의 Percentile/HMA/TRUE-B0 qualification. 구현의 legacy bridge는 한 방향 child가 활성화되면 이 전략의 선언된 OZ dependency union을 복원한다. 개별 SWEEP TF만의 더 세밀한 native scope 분할은 하지 않았다. |
| 게이트 재구성 / 진행 중 후보 | 실행 내 마지막 native/OZ checkpoint부터 게이트의 실제 관측 token까지 순서대로 재현한다. 과거 관측은 environments={}로 조용히 처리하고, 현재 token에만 실제 child 환경을 부여한다. OUT episode, IN 복귀, HMA 극값·cross, TRUE-B0 후보, mid-validation, 완료·취소·만료, registration/alert key와 silent consumption을 복원한다. 최초 호출에는 그 실행의 준비 입력 이력도 필요할 수 있다. 임의의 7/10봉 제한은 두지 않았다. 외부 sweep·level 자격은 LIGHT에서 유지한 현재 상태를 최종 TRUE-B0 평가에 연결한다. |
| 완료 / consumption | 원래 external TRUE-B0 gate가 승인하는 최종 OZ와 같은 방향 child 소비. MAIN 밖 최종 알림 억제만으로 child를 종료하지 않는다. 이미 소비된 sweep/candidate를 새 사건으로 복원하지 않는다. |
| 취소 / 만료 | 외부 레벨 소멸·ATR 한계에 따른 자격 상실과 OZ 자체 취소를 구분한다. 기존 OZ 카운터를 사용하고 새로운 sweep TTL은 추가하지 않는다. |
| HEAVY OFF / 복귀 | 활성 child가 없을 때 하위 계산을 끄고 외부 레벨·SWEEP/signature LIGHT를 계속한다. |
| Part1 / 기존 Part2 / 시각 비교 | 아래의 Part1 비교는 선택한 실제 메서드·합성 양성 상태기계 대조 수준이다. 원본 LIVE 프로그램의 스레드·터미널 native seed·동시 polling 전체와 같은 시장 데이터로 실행한 통합 비교가 아니다. 전체 SPECIAL의 이벤트 개수·방향·시각·순서·취소·만료·소비 동일성은 미인증이다. 원본 Part2는 Generic worker 시작 실패. external controller 자체에 대한 완전한 Part1 통합 시각 비교는 수행하지 않았다. 짧은 수정 eager/conditional 대조는 0건 동일이다. |

## SPECIAL3

| 항목 | 구현 / 판정 |
| --- | --- |
| Part1 대응 | SPECIAL3의 unordered timed chain: EMA CROSS + FVG 생성 → FVG TOUCH → NORMAL OZ |
| LIGHT / 최소 feature / TF | 1m·2m의 완료봉 EMA50/200 cross와 5m·6m FVG 생성을 유지한다. 이 둘은 무순서 쌍이므로 EMA가 먼저라는 가정을 넣어 과거 FVG 생성을 끄지 않는다. |
| 선행 게이트 / 다음 단계 | 동방향 cross/FVG 쌍의 유효 기간 → 5m·6m·10m·12m·15m FVG touch → OPENING 세션에서 최종 child 활성화. |
| 봉중 / 완료봉 / 정확한 시점 | EMA는 완료봉 간 cross만 인정한다. FVG는 기존 source engine의 생성/접촉 조건과 현재 token을 사용한다. 최종 touch가 실제로 관측된 시점에 lower gate를 연다. 진행봉 spike로 EMA 완료봉 cross가 먼저 생기지 않는 검증 통과. |
| ARMED 전 생략 | 최종 1m/2m OZ의 Percentile/HMA6/17/mapped 상세 frame. 무순서 선행 FVG heavy를 모두 끄는 변경은 하지 않았다. |
| ARMED 후 TF·feature·engine | OZ base 1m·2m와 NORMAL mapped union; Percentile 네 family/HMA6/17; NORMAL+OZ engine. cross/FVG latch와 deadline은 LIGHT 소유 상태로 유지한다. |
| 재구성 / 진행 중 후보 | 실행 내 마지막 native/OZ checkpoint부터 게이트의 실제 관측 token까지 순서대로 재현한다. 과거 관측은 environments={}로 조용히 처리하고, 현재 token에만 실제 child 환경을 부여한다. OUT episode, IN 복귀, HMA 극값·cross, TRUE-B0 후보, mid-validation, 완료·취소·만료, registration/alert key와 silent consumption을 복원한다. 최초 호출에는 그 실행의 준비 입력 이력도 필요할 수 있다. 임의의 7/10봉 제한은 두지 않았다. 과거 FVG/setup 이벤트를 새 이벤트로 재방송하지 않는다. |
| 완료 / consumption | 유효 chain의 최종 OZ. MAIN 안 최종 전달 성공 시 해당 child 처리, MAIN 밖 전달 억제 시 child 유지. 중복·pair 소비는 현재 chain의 순서를 따른다. |
| 취소 / 만료 | 반대 EMA 또는 반대 5m/6m FVG 및 새 pair 교체를 기존 순서대로 처리한다. gap 1800초, final 7200초 및 실제 completion_deadline을 그대로 사용한다. |
| HEAVY OFF / 복귀 | 유효 최종 child가 없거나 deadline 밖이면 lower OFF. EMA/FVG 쌍·서명 감시 LIGHT로 돌아간다. |
| Part1 / 기존 Part2 / 시각 비교 | 아래의 Part1 비교는 선택한 실제 메서드·합성 양성 상태기계 대조 수준이다. 원본 LIVE 프로그램의 스레드·터미널 native seed·동시 polling 전체와 같은 시장 데이터로 실행한 통합 비교가 아니다. 전체 SPECIAL의 이벤트 개수·방향·시각·순서·취소·만료·소비 동일성은 미인증이다. 원본 worker 실행 계약 오류 및 미정의 FINAL_FVG_TFS 참조를 현재 정의 FINAL_FVG_TOUCH_TFS에 맞췄다. 전체 Part1 FVG 생성/접촉 시각 동일성은 미인증. 수정 eager/conditional 단기 대조는 0건 동일이다. |

## SPECIAL4

| 항목 | 구현 / 판정 |
| --- | --- |
| Part1 대응 | SPECIAL4Runtime: 30m 경계 setup → 6분 active window → 최종 OZ/추세 검증 |
| LIGHT / 최소 feature / TF | 실제 30m 새 봉 경계, 직전 :15/:45 15m WONBI 접촉, 직전 30m 같은쪽 미접촉, anchor close와 완료 1m ATR14. |
| 선행 게이트 / 정확한 시점 | 경계 관측에서 완료된 위 조건이 성립하여 active setup이 만들어질 때. 진행 중인 30m 봉을 미리 완료된 것으로 취급하지 않는다. |
| 봉중 / 완료봉 | setup은 30m 경계의 완료봉 조건이다. 활성화 이후 가격 excursion과 최종 snapshot 조건은 실제 현재 관측에서 평가한다. |
| ARMED 전 생략 | 하위 Percentile/HMA6·17/OZ; 최종 gate 전 HMA50/168·EMA50/200 추세 상세 계산. |
| ARMED 후 TF·feature·engine | OZ base 1m, NORMAL mapped 3m·6m; Percentile 네 family/HMA6/17/NORMAL OZ. 최종 후보가 실제 생긴 경우에만 1m·3m·15m의 기존 final trend gate를 계산한다. |
| 재구성 / 진행 중 후보 | 실행 내 마지막 native/OZ checkpoint부터 게이트의 실제 관측 token까지 순서대로 재현한다. 과거 관측은 environments={}로 조용히 처리하고, 현재 token에만 실제 child 환경을 부여한다. OUT episode, IN 복귀, HMA 극값·cross, TRUE-B0 후보, mid-validation, 완료·취소·만료, registration/alert key와 silent consumption을 복원한다. 최초 호출에는 그 실행의 준비 입력 이력도 필요할 수 있다. 임의의 7/10봉 제한은 두지 않았다. anchor·만료·excursion은 현재 active setup이 소유한다. |
| 완료 / consumption | 실제 OZ 최종 후보에서 callable delivery gate를 호출한다. 데이터 미확정은 ACK false로 재시도 후보를 남긴다. 허용되면 내부 소비 후 세션/최종 전달 규칙을 따른다. 시간 필터만 억제한 경우 child 유지, expiry/excursion/명확한 추세 불허는 취소한다. |
| 취소 / 만료 | 새 30m cycle, excursion >= ATR 한계, 실제 final gate의 명확한 불허; now >= anchor+360초 만료. 임계값 변경 없음. |
| HEAVY OFF / 복귀 | active setup 종료 시 lower OFF, 다음 실제 30m 경계를 기다린다. |
| Part1 / 기존 Part2 / 시각 비교 | 아래의 Part1 비교는 선택한 실제 메서드·합성 양성 상태기계 대조 수준이다. 원본 LIVE 프로그램의 스레드·터미널 native seed·동시 polling 전체와 같은 시장 데이터로 실행한 통합 비교가 아니다. 전체 SPECIAL의 이벤트 개수·방향·시각·순서·취소·만료·소비 동일성은 미인증이다. 원본은 Generic worker 시작 실패. callable ACK와 기존 bool ACK의 실제 사건·상태 재시도 대조는 통과했지만 SPECIAL4Runtime 전체 동일 타임라인 대조는 수행하지 않았다. 단기 이벤트는 0건 동일이다. |

## SPECIAL5

| 항목 | 구현 / 판정 |
| --- | --- |
| Part1 대응 | SPECIAL5Runtime: 상위 NORMAL DIV 또는 DIV_REGIME 완료 → 1m/2m/3m BLIND OZ |
| LIGHT / 최소 feature / TF | 부모 자체가 heavy OZ이다. 부모 base 5m·6m·10m·12m·15m·20m·30m·1h와 mapped TF의 native four-family/HMA/OZ 두 프로필을 계속 유지한다. 이를 임의의 싼 조건으로 대체하지 않는다. |
| 선행 게이트 / 정확한 시점 | 상위 parent 완료가 현재 관측에서 발생하고 MAIN 필터를 통과할 때. DIVERGENCE 부모에만 마지막 완료봉 EMA50/200 방향 검사; REGIME 부모에 없는 EMA 조건을 새로 추가하지 않는다. |
| 봉중 / 완료봉 | OZ는 원래 현재 source 관측 기준, 부모 EMA 방향 보조 검사는 완료봉 기준. 상위 완료 시각에 same-direction 최신 setup을 만든다. |
| ARMED 전 생략 | 하위 1m·2m·3m Percentile/HMA6·17/BLIND OZ. 부모 native/OZ 계산은 필수이므로 OFF 하지 않는다. |
| ARMED 후 TF·feature·engine | 1m·2m·3m의 BLIND OZ + 네 Percentile family/HMA6·17; 부모 계산 계속. 같은 전략의 현재 부모 projection을 두 프로필이 사용하는 것이며, 다른 전략/다른 실행 공용 캐시는 만들지 않았다. |
| 재구성 / 진행 중 후보 | 실행 내 마지막 native/OZ checkpoint부터 게이트의 실제 관측 token까지 순서대로 재현한다. 과거 관측은 environments={}로 조용히 처리하고, 현재 token에만 실제 child 환경을 부여한다. OUT episode, IN 복귀, HMA 극값·cross, TRUE-B0 후보, mid-validation, 완료·취소·만료, registration/alert key와 silent consumption을 복원한다. 최초 호출에는 그 실행의 준비 입력 이력도 필요할 수 있다. 임의의 7/10봉 제한은 두지 않았다. setup마다 빈 하위 OZProfile을 만드는 대신 실행 내 지속 lower engine을 사용한다. 이미 진행 중인 lower 후보를 포함하되 새 setup의 child ID/age는 별도로 관리한다. |
| 완료 / consumption | 첫 유효 child 완료로 그 setup의 형제 child 모두 종료. SPECIAL1/2/3/6/7과 달리 MAIN 밖 알림이 억제돼도 형제까지 취소한다. 부모 내부의 완료 소비도 유지한다. |
| 취소 / 만료 | 같은 방향 최신 부모로 setup 교체; 부모 TF 반대 HMA6/17 cross는 전체 취소; child TF 반대 cross는 해당 child 취소; setup 이후 해당 child 완료봉 수 > 기존 MAX_BARS_AFTER_B0(10)일 때 해당 child 종료. |
| HEAVY OFF / 복귀 | 유효 child가 하나도 없으면 lower BLIND OFF, 부모 두 프로필만 계속한다. 모든 heavy가 꺼지는 전략이라고 보고하지 않는다. |
| Part1 / 기존 Part2 / 시각 비교 | 아래의 Part1 비교는 선택한 실제 메서드·합성 양성 상태기계 대조 수준이다. 원본 LIVE 프로그램의 스레드·터미널 native seed·동시 polling 전체와 같은 시장 데이터로 실행한 통합 비교가 아니다. 전체 SPECIAL의 이벤트 개수·방향·시각·순서·취소·만료·소비 동일성은 미인증이다. 원본 5는 실행 가능하나 새 setup마다 빈 lower state를 만드는 차이가 있어 원본 전체 이벤트 동일성을 주장하지 않는다. 원본/수정 단기 flat 입력 0건 비교와 수정 eager/conditional 0건 비교만 완료했다. |

## SPECIAL6

| 항목 | 구현 / 판정 |
| --- | --- |
| Part1 대응 | SPECIAL6 + Composer: 같은 15m/30m MA·HMA slope·FVG touch → NORMAL DIVERGENCE OZ |
| LIGHT / 최소 feature / TF | 15m·30m 현재 EMA50/200 배열과 OPEN HMA168 현재/2봉전 slope. 이 조건을 만족할 때만 그 TF FVG engine을 이미 관측한 OHLC prefix로 따라잡는다. |
| 선행 게이트 / 정확한 시점 | 현재 같은 TF FVG touch + MA/slope fact가 MAIN에서 모두 성립하여 child 생성. 과거 FVG 상태는 복원하지만 과거 setup 알림은 다시 만들지 않는다. |
| 봉중 / 완료봉 | MA/slope는 현재 snapshot, FVG 생성·접촉은 기존 source contract. 게이트 시점 이후 raw나 완성된 미래 상위 봉을 쓰지 않는다. |
| ARMED 전 생략 | MA 불충족일 때 FVG heavy. child 생성 전 하위 Percentile/HMA6·17/OZ. OHLC-only FVG 복원에는 Percentile을 요구하지 않는다. |
| ARMED 후 TF·feature·engine | base 1m·2m·3m·4m·5m·6m와 NORMAL mapped union; native 네 family/HMA6·17/HistoricalOZEngine(NORMAL,DIVERGENCE). 선행 FVG/condition matcher는 별도 유지한다. |
| 재구성 / 진행 중 후보 | 실행 내 마지막 native/OZ checkpoint부터 게이트의 실제 관측 token까지 순서대로 재현한다. 과거 관측은 environments={}로 조용히 처리하고, 현재 token에만 실제 child 환경을 부여한다. OUT episode, IN 복귀, HMA 극값·cross, TRUE-B0 후보, mid-validation, 완료·취소·만료, registration/alert key와 silent consumption을 복원한다. 최초 호출에는 그 실행의 준비 입력 이력도 필요할 수 있다. 임의의 7/10봉 제한은 두지 않았다. upper FVG도 마지막 처리 checkpoint부터 OHLC-only 실제 prefix를 복원하고 현재 유효 touch만 setup에 사용한다. |
| 완료 / consumption | 공유 child의 최종 OZ. MAIN 안 최종 전달 시 child 소비; MAIN 밖 알림 억제는 child 유지. signature·OZ 내부 소비를 따로 보존한다. |
| 취소 / 만료 | OZ 자체 cross/validation/카운터 규칙. 상위 MA가 나중에 false라고 기존 sticky child를 임의로 삭제하거나 setup TTL을 추가하지 않는다. |
| HEAVY OFF / 복귀 | child가 없으면 lower OFF; 15m/30m MA·slope LIGHT와 필요 시 FVG 현재 상태 복원으로 복귀한다. |
| Part1 / 기존 Part2 / 시각 비교 | 아래의 Part1 비교는 선택한 실제 메서드·합성 양성 상태기계 대조 수준이다. 원본 LIVE 프로그램의 스레드·터미널 native seed·동시 polling 전체와 같은 시장 데이터로 실행한 통합 비교가 아니다. 전체 SPECIAL의 이벤트 개수·방향·시각·순서·취소·만료·소비 동일성은 미인증이다. 원본 6의 setup별 빈 lower 상태를 지속 engine으로 보정했다. 원본 full-strategy 결과 동일은 미인증이며 원본/수정 flat 단기 0건 비교만 완료했다. FVG 실제 LIVE 생성/접촉 시각 전체 대조는 미수행이다. |

## SPECIAL7

| 항목 | 구현 / 판정 |
| --- | --- |
| Part1 대응 | SPECIAL7 + Composer: 실제 15m TREND → 1m NORMAL DIVERGENCE_REGIME OZ |
| LIGHT / 최소 feature / TF | 15m 실제 HistoricalTrendEngine의 현재 ensemble/HMA50 snapshot. 기존 Part2의 완료15m EMA50/200 단순 비교를 TREND의 대체물로 계속 사용하지 않는다. 현재 Part2 native frame 구조에서 681행 요구를 선언한다. |
| 선행 게이트 / 정확한 시점 | 실제 15m TREND 방향 fact가 MAIN에서 유효한 현재 관측에 child 활성화. signature로 같은 사건 재등록을 억제한다. |
| 봉중 / 완료봉 | TREND는 현재 snapshot, 완료15m까지 일괄 지연시키지 않는다. 하위 native/OZ 각 조건은 원래 source 시각을 사용한다. |
| ARMED 전 생략 | 1m·3m·6m Percentile/HMA6·17/OZ. 15m 선행 TREND는 유지한다. |
| ARMED 후 TF·feature·engine | 1m base + 3m/6m mapped state; 네 family/HMA6·17/HistoricalOZEngine(NORMAL,DIVERGENCE_REGIME). |
| 재구성 / 진행 중 후보 | 실행 내 마지막 native/OZ checkpoint부터 게이트의 실제 관측 token까지 순서대로 재현한다. 과거 관측은 environments={}로 조용히 처리하고, 현재 token에만 실제 child 환경을 부여한다. OUT episode, IN 복귀, HMA 극값·cross, TRUE-B0 후보, mid-validation, 완료·취소·만료, registration/alert key와 silent consumption을 복원한다. 최초 호출에는 그 실행의 준비 입력 이력도 필요할 수 있다. 임의의 7/10봉 제한은 두지 않았다. |
| 단계 세분화의 범위 | 부모 TREND → 하위 mapped group의 조건부 계산은 구현했다. 6m IN → 3m OUT → 1m 엔진을 각각 따로 정지/복원하는 더 세밀한 단계 분할은 구현하지 않았다. cross/validation/소비 상태를 훼손하지 않는 보수적 경계를 선택했다. |
| 완료 / consumption | 같은 방향 1m 최종 OZ; MAIN 안 전달 성공이면 child 종료, MAIN 밖 알림 억제면 child 유지. signature와 TRUE-B0 소비 상태 보존. |
| 취소 / 만료 | 원래 OZ 반대 cross/validation/카운터 규칙; TREND 반전만으로 기존 sticky child를 새롭게 취소하지 않으며 새로운 TTL을 넣지 않는다. |
| HEAVY OFF / 복귀 | 유효 child 없음 → lower OFF → 실제 15m TREND/signature LIGHT. |
| Part1 / 기존 Part2 / 시각 비교 | 아래의 Part1 비교는 선택한 실제 메서드·합성 양성 상태기계 대조 수준이다. 원본 LIVE 프로그램의 스레드·터미널 native seed·동시 polling 전체와 같은 시장 데이터로 실행한 통합 비교가 아니다. 전체 SPECIAL의 이벤트 개수·방향·시각·순서·취소·만료·소비 동일성은 미인증이다. 기존 Part2 TREND 의미 차이를 수정했으므로 원본 전체 이벤트가 같다고 주장할 수 없다. dict-only 접근 대신 읽기 전용 Mapping을 수용하는 current bundle adapter를 사용한다. 원본/수정 flat 단기 0건 비교만 완료했다. |

## Part2 generic_backtest/watch

| 항목 | 구현 / 판정 |
| --- | --- |
| Part1 대응 | Part1 Composer / WatchOrchestrator / monitor_OZ의 현재 Part2 compiler가 지원하는 부분집합. Part1 WATCH는 수정하지 않았다. |
| LIGHT / 최소 feature / TF | plan.conditions의 TREND/FVG 등 선행 detector와 matcher. TREND TF는 HMA50 ensemble, FVG TF는 원래 ATR/FVG 계산을 유지한다. |
| 선행 게이트 / 정확한 시점 | ALL/ANY matcher가 현재 유효 fact에서 실제 child를 생성하는 관측. 이미 생성된 sticky child는 단순 선행 false로 새롭게 취소하지 않는다. OZ_DIRECT에는 상위 선행 조건이 없으므로 임의로 게이트를 추가하지 않는다. |
| 봉중 / 완료봉 | OZ composite는 source별 현재 관측 조건을 따른다. primitive HMA_CROSS/BAR_CLOSE 등 완료봉 WATCH의 기존 경로는 조건부 엔진으로 바꾸지 않았다. |
| ARMED 전 생략 | OZ_PERCENTILE_SOURCE_V1의 모든 lower Percentile/HMA6·17/OZ 상세 frame/observe. observe_setup과 observe_armed_oz를 분리해 native 호출 전 게이트를 적용한다. |
| ARMED 후 TF·feature·engine | plan의 base 및 validation/trigger에 필요한 mapped union. 기존 HistoricalWatchSession과 HistoricalOZEngine, 동일 native 네 family/HMA. |
| 재구성 / 진행 중 후보 | 실행 내 마지막 native/OZ checkpoint부터 게이트의 실제 관측 token까지 순서대로 재현한다. 과거 관측은 environments={}로 조용히 처리하고, 현재 token에만 실제 child 환경을 부여한다. OUT episode, IN 복귀, HMA 극값·cross, TRUE-B0 후보, mid-validation, 완료·취소·만료, registration/alert key와 silent consumption을 복원한다. 최초 호출에는 그 실행의 준비 입력 이력도 필요할 수 있다. 임의의 7/10봉 제한은 두지 않았다. 과거 lower를 재현하는 동안 Watch setup matcher 자체를 재실행하여 과거 child를 만들지 않는다. 현재 child만 lower 최종 환경에 연결한다. |
| 완료 / consumption | 현재 plan의 첫 same-direction 최종 OZ가 해당 child를 소비한다. silent consumption/fresh activation probe를 유지하고 과거 alert를 지금 새로 발생시키지 않는다. |
| 취소 / 만료 | 현 OZ 카운터·restart guard·등록 규칙. 지원되지 않는 timed-chain 문법을 새로 추가하지 않았고 임의 TTL도 만들지 않았다. |
| HEAVY OFF / 복귀 | composite children 없음 → lower OFF. 선행 detector/matcher/signature만 계속한다. 직접 OZ/필수 부모 OZ는 끌 근거가 없다. |
| Part1 / 기존 Part2 / 시각 비교 | 아래의 Part1 비교는 선택한 실제 메서드·합성 양성 상태기계 대조 수준이다. 원본 LIVE 프로그램의 스레드·터미널 native seed·동시 polling 전체와 같은 시장 데이터로 실행한 통합 비교가 아니다. 전체 SPECIAL의 이벤트 개수·방향·시각·순서·취소·만료·소비 동일성은 미인증이다. 현재 UI가 요구하지만 배포본에서 누락됐던 BACKTEST_SPECIAL/WATCH_UI_V1.py만 얇은 SDK 어댑터로 추가했다. primitive 3분 BAR WATCH는 TICK/봉마감/LIVE_PARITY 실제 worker·coordinator 경로에서 9개의 동일 경계 이벤트가 확인됐다. composite 단기 비교는 0건이며 full LIVE 통합은 미인증이다. |

## 12. 성능 측정 방법과 실제 자료 범위

비교를 세 종류로 분리했다.

**A. 수정된 동일 전략의 eager 대 conditional:** `resources.conditional_specials`만 False/True로 바꿨다. 수정 전 Part2 자체와 비교한 숫자가 아니다. 실제 isolated StrategyWorker 시작·core 처리·feature 계산·전략 callback·finish를 포함한 wall time을 사용했다. unprofiled와 별개인 profiled run에서 호출을 셌다. 디스크 feature cache는 사용하지 않았다.

**B. 원본 Part2 실행/단기 성능:** 원본 SPECIAL1~4는 `E_CAPABILITY_DENIED: import __future__`로 시작 실패. 5~7만 원본 상태로 짧은 입력을 실행했다. 원본 WATCH UI adapter는 누락돼 있었다. 실행 실패를 빠른 성능이나 이벤트 0건으로 세지 않았다.

**C. 입력 종류:** flat 합성 96회(1분마다 한 tick; 2회 시간 측정의 중앙값)와 실제 브로커 96 tick prefix(1회 시간 측정)를 각각 사용했다. 둘 다 충분한 다중 TF 준비 이력이 없고 모든 전략 이벤트가 0건이다. 아래 Percentile 절감은 실제 dormant 호출 생략의 증거이나, 양성 최종 OZ 처리 비용·신호 많은 기간·한 달 성능의 증거가 아니다. 모든 실행의 이벤트 hash는 원시 JSON에서 확인할 수 있다.

제공된 `generic_cache/raw`에는 **5,990,923개 BROKER_REAL_TICKS**가 있다. 선언 coverage는 2026-03-03 15:00 UTC~2026-09-22 15:00 UTC이며 hourly chunk 4,872개 중 213개가 nonempty, 4,659개가 `EMPTY_UNVERIFIED`이다. 연속 한 달의 완전한 검증 자료라고 간주하지 않았다. 입력 파일 자체는 변경하지 않았다. 실측 prefix는 `chunks/00003730.npy`의 첫 96 tick, **2026-08-06 01:00:06.135~01:00:26.388 UTC (20.253초)** 이며 이전 시장 이력을 주입하지 않았다. 이 결과를 “실제 한 달 백테스트 개선율”로 표시하지 않는다.

### 합성 dormant 96관측: 수정 동일 전략 eager → conditional

| 경로 | wall 초 전→후 | 감소율 | Percentile bundle 전→후 | FVG heavy 전→후 | frame 요청 전→후 |
| --- | --- | --- | --- | --- | --- |
| BACKTEST_SPECIAL1 | 7.271 → 1.101 | 84.9% | 1536 → 0 | 0 → 0 | 266 → 266 |
| BACKTEST_SPECIAL2 | 6.894 → 1.529 | 77.8% | 1536 → 0 | 0 → 0 | 2182 → 2182 |
| BACKTEST_SPECIAL3 | 4.476 → 2.499 | 44.2% | 480 → 0 | 96 → 96 | 1446 → 1282 |
| BACKTEST_SPECIAL4 | 2.795 → 1.159 | 58.5% | 288 → 0 | 0 → 0 | 680 → 546 |
| BACKTEST_SPECIAL5 | 6.334 → 5.729 | 9.6% | 1440 → 1152 | 0 → 0 | 288 → 192 |
| BACKTEST_SPECIAL6 | 5.185 → 1.023 | 80.3% | 1056 → 0 | 5 → 0 | 480 → 192 |
| BACKTEST_SPECIAL7 | 2.432 → 1.069 | 56.0% | 288 → 0 | 0 → 0 | 192 → 96 |
| WATCH_UI_V1 | 2.169 → 1.072 | 50.6% | 288 → 0 | 0 → 0 | 96 → 96 |

Percentile bundle은 실제 `PercentileFeatureProvider.on_market` 호출 횟수이다. 이 TICK 계측의 bundle당 네 family가 실행되므로 family 호출 수는 bundle×4로 산출했으며 독립적으로 계측한 별도 수치가 아니다. frame 요청은 캐시 hit를 포함한 요청 횟수여서 실제 frame 재계산 횟수와 혼동하지 않는다. FVG heavy는 `add_fvg_local` 함수 호출이다.

### HMA / OZ / 단계 계측의 해석

| 경로 | HMA WMA helper 전→후 | OZ observe 전→후 | gate 후/복원 |
| --- | --- | --- | --- |
| BACKTEST_SPECIAL1 | 0 → 0 | 0 → 0 | 0 / 0 (미활성) |
| BACKTEST_SPECIAL2 | 0 → 0 | 0 → 0 | 0 / 0 (미활성) |
| BACKTEST_SPECIAL3 | 30 → 0 | 0 → 0 | 0 / 0 (미활성) |
| BACKTEST_SPECIAL4 | 2286 → 0 | 0 → 0 | 0 / 0 (미활성) |
| BACKTEST_SPECIAL5 | 1183 → 40 | 0 → 0 | 0 / 0 (미활성) |
| BACKTEST_SPECIAL6 | 20 → 0 | 0 → 0 | 0 / 0 (미활성) |
| BACKTEST_SPECIAL7 | 1143 → 0 | 0 → 0 | 0 / 0 (미활성) |
| WATCH_UI_V1 | 1143 → 0 | 0 → 0 | 0 / 0 (미활성) |

HMA 열은 실제 `wma_at` helper 호출이지 “HMA 전체 출력 개수”가 아니다. 원시 JSON의 `worker_diagnostics`에는 runtime별 HMA output/miss와 frame rebuild 집계도 있다. OZ observe가 0→0인 것은 양성 OZ 성능이 개선되었다는 증거가 아니라 지표 미준비/게이트 미성립이다. SPECIAL5의 게이트 전 1,152 native bundle은 필수 부모 계산이며, 이를 “생략 실패”라고 감추거나 반대로 “게이트 전 모든 heavy=0”이라고 표시하지 않는다. SPECIAL3 FVG 96→96도 무순서 선행 조건상 유지가 필요한 계산이다.

### 실제 브로커 20.253초, 96 tick prefix

| 경로 | wall 초 전→후 | 감소율 | Percentile bundle 전→후 |
| --- | --- | --- | --- |
| BACKTEST_SPECIAL1 | 4.659 → 0.750 | 83.9% | 1536 → 0 |
| BACKTEST_SPECIAL2 | 4.563 → 0.873 | 80.9% | 1536 → 0 |
| BACKTEST_SPECIAL3 | 1.861 → 0.841 | 54.8% | 480 → 0 |
| BACKTEST_SPECIAL4 | 1.304 → 0.753 | 42.3% | 288 → 0 |
| BACKTEST_SPECIAL5 | 4.241 → 3.898 | 8.1% | 1440 → 1152 |
| BACKTEST_SPECIAL6 | 3.531 → 0.909 | 74.3% | 1056 → 0 |
| BACKTEST_SPECIAL7 | 1.519 → 0.904 | 40.5% | 288 → 0 |
| WATCH_UI_V1 | 1.559 → 0.715 | 54.1% | 288 → 0 |

### 원본 Part2 단기 실행 결과

| 원본 경로 | 원본 wall 중앙값 | 판정 |
| --- | --- | --- |
| SPECIAL1~4 | 정상 시간값 없음 | worker 시작 실패; 실행 성공으로 집계하지 않음 |
| BACKTEST_SPECIAL5 | 6.295초 | 96개 flat 관측, 0건; 전체 이벤트 동일성 증거 아님 |
| BACKTEST_SPECIAL6 | 4.660초 | 96개 flat 관측, 0건; 전체 이벤트 동일성 증거 아님 |
| BACKTEST_SPECIAL7 | 2.203초 | 96개 flat 관측, 0건; 전체 이벤트 동일성 증거 아님 |
| WATCH_UI_V1 | 정상 시간값 없음 | 원본 실행 adapter 파일 누락 |

### 복원 비용 별도 실측

`benchmark_reconstruction.py`는 합성 150관측에서 80·81·82·120·121·122번째 관측만 consumer가 활성화되도록 했다. 첫 gate 이전 native stream은 없었고, 재생한 **122개 context**의 feature가 eager prefix와 일치했다. 마지막 gate 이후 28회는 실제로 계산되지 않았다.

| 항목 | 값 |
|---|---:|
| eager native bundle | 150 |
| conditional 총 native bundle | 122 |
| 그중 과거 복원 bundle | 116 |
| 현재 활성 관측 bundle | 6 |
| 복원 시작 회수 | 2 |
| native 복원 시간 | 0.268220초 |
| 현재 native 계산 시간 | 0.041157초 |
| raw/marker 기록 시간 | 0.000961초 |
| history 전송·비교 포함 시간 | 0.695266초 |

이것은 독립적인 native 재생 microbenchmark이며 SPECIAL 전체 성능이 아니다. 80번째 gate에서 이전 79회를 결국 계산해야 한다는 비용을 숨기지 않는다. 늦은 활성화/빈번한 재활성화에서는 전체 eager보다 느려질 수도 있다.

## 13. 중간 취소 결과 보존

### 처리 방식과 마지막 commit 기준

`ObservationCommit`은 정상 완료한 관측의 population 길이, journal byte offset/sequence/hash, outcome transaction checkpoint, raw cursor와 decision token을 기억한다. 한 observation에서 SIGNAL/ENTRY/위험·결과 처리와 마지막 cancel check까지 모두 끝난 뒤에만 commit한다. tick 중간 또는 worker 응답 generator 중간에 취소되면 그 observation에서 늘어난 event/entry/outcome과 journal의 미확정 꼬리만 되돌린다. 이전 정상 commit 기록은 삭제하지 않는다.

메타데이터는 `status=PARTIAL`, `termination=CANCELLED`, `requested_interval`, `computed_interval`, `last_committed_at_ns`, `last_committed_raw_cursor`, `last_committed_decision_token`, `last_committed_event_kind`, `progress_percent`, `commit_policy=ATOMIC_COMPLETE_OBSERVATION_V1`이다. 정상 완료는 FULL/END_OF_DATA로 구분한다. 실제 평가 구간은 half-open `[요청 시작, 마지막 정상 관측 ns+1)`이며 요청 끝을 넘지 않는다. 아직 확정 관측이 없으면 빈 prefix/0%로 저장한다.

결과 저장 직전 취소 경합도 IPC lock으로 결정한다. 취소가 먼저 이겼다면 이미 계산 완료한 observation까지는 유지하고 END_OF_DATA 종료 처리만 되돌려 CANCELLED footer/결과로 commit한다. 저장이 먼저 끝났다면 늦은 취소는 TOO_LATE이다. cancel ACK를 받았다는 이유만으로 유효 PARTIAL 결과를 client가 폐기하지 않도록 바꿨다.

### PARTIAL 저장과 대시보드

기존 `alerts.jsonl`, `entries.jsonl`, `outcomes.jsonl`, `events.jsonl`, `dashboard.json`, `generic_manifest.json`의 결과 묶음을 사용한다. 별도 대시보드를 만들지 않았다. verifier는 PARTIAL 상태와 잘린 실제 구간, alert/entry/exit token 범위, 통계를 검증한다. 이번 run의 미완성 수치 tape는 abort하며 다른 run의 기존 결과/기록을 초기화하지 않는다.

기존 화면 제목과 본문에 PARTIAL / CANCELLED, 요청 기간, 마지막 정상 commit, 진행률을 표시한다. 이벤트 수와 LONG/SHORT, 시간별/월별 집계는 저장된 확정 prefix만 사용한다. 약 50% 취소 시 49.4%, 마지막 commit 09:29:40, 이벤트 90개로 나타난 실제 Tk/Xvfb 화면은 `dashboard_partial.png`이다. 이 예시는 명확히 SYNTHETIC_TEST_ONLY이며 실거래 성과가 아니다.

### 통계와 미완성 자료 제외

취소 이전에 exit가 확정된 WIN/LOSS만 승률·실현 R·MDD 등 확정 손익의 근거로 쓴다. 취소 당시 열린 entry는 `OPEN_CANCELLED`로 남기되 미래 가격으로 강제 청산하거나 확정 WIN/LOSS로 바꾸지 않는다. 마지막 commit 이후 alert/entry/exit를 포함하지 않는다. archive의 전체 기간 validated_session_hours는 partial 분모로 재사용하지 않고 NOT_RECOMPUTED_FOR_PARTIAL_PREFIX로 표시한다. 전체 archive coverage 메타데이터와 실제 계산 구간은 별개이다.

10/30/50/80%의 ALERT_ONLY/TRADE 8개 테스트에서 FULL 결과의 확정 prefix와 일치함을 비교했고, 다음 새로운 FULL run이 동일한 결과를 만드는 것도 검사했다. 여기에 observation 중간 generator 취소, 최초 빈 prefix, 최종 commit 경합 3개와 기존 GUI 4개를 더해 취소 저장/표시를 확인했다. 강제 프로세스 kill·전원 중단 후 재개 기능을 새로 구현한 것은 아니다.

## 14. 검증 증거와 남은 차이

`test_live_rules.py`: Part1 실제 `monitor_OZ.py`의 30개 메서드 본문을 변경 없이 AST로 읽는다. durable I/O decorator만 테스트 환경에서 no-op이며 외부 eligibility는 명시적 입력이다. 6개 프로필×양방향 12개 양성 사례와 반대 cross/만료/gap/delivery retry 4개 사례를 비교했다. 장기간 LIVE native buffer나 controller I/O 전체 검증으로 확대 해석하지 않는다.

`test_intrabar.py`: Part1 실제 WONBI poll 및 STAFF 계산 메서드와 현재 SPECIAL1 gate를 비교했다. 09:32 최초 touch와 미래 09:50 spike를 분리했다. 동시에 실제 TREND 엔진과 전체 WONBI poll 위상까지 함께 대조한 테스트는 아니다. SPECIAL3 EMA 테스트는 진행봉과 완료봉의 시점 분리를 검증한다.

`test_restoration.py`: 양방향에서 gate가 후보 시작 뒤/이미완료 시점/소비 뒤에 열리는 경우를 검사했다. 미완료 후보는 eager와 같은 시점에 양성 완료되고, 이미 완성/소비된 후보는 늦은 gate 때문에 되살아나지 않았다. input frame은 명시적 fixture이며 실제 native feature replay는 별도 `test_conditional.py`에서 검사했다.

`test_worker_smoke.py`는 1~7 실제 worker의 시작/observe/finish를 검증하는 테스트이지 양성 신호 대조가 아니다. `test_watch_path.py`는 기존 일반 BAR WATCH의 실제 worker/coordinator/검증기 경로를 확인한다. 최종 pytest 개수와 기존 원본의 실패는 `verification_summary.json`, `final_test_log.txt`, `original_test_log.txt`를 따른다. 없는 GENERIC_EXAMPLE_V1 fixture를 이번 범위 밖에서 새로 만들어 시험을 통과시키지 않았다.

### 아직 전체 승인할 수 없는 항목

각 SPECIAL 전체에 대한 동일 시장·동일 관측 스케줄의 비어 있지 않은 alert/setup/WATCH/ARMED/cancel/expire/consume 전수 대조, 한 달 warmed 실제 시장 실행시간, 실제 LIVE terminal native seed/copy-buffer/call 위상, SWEEP external controller 전체, 모든 세부 TF 단계 게이트, 최악 복원 시간·장기간 raw 로그 메모리 한계는 확정하지 못했다. 이는 통과한 것으로 기록하지 않았다.

원본 Part2에는 실행 계약, SPECIAL7 TREND 대체식, SPECIAL5/6의 빈 lower 상태 생성 등 Part1과 다른 부분이 있었다. 이들을 현재 Part2 구조 안에서 보정했으므로 **“원본 Part2와 항상 byte-for-byte 같은 이벤트”**와 **“기존 차이를 Part1 기준으로 보정”**을 동시에 사실인 것처럼 쓰지 않는다. 최종 전체 LIVE 동일성 인증 없이 이 산출물을 실전 정답 검증 완료본으로 취급해서는 안 된다.
