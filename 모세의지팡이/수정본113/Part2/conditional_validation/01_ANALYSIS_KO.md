# 수정 전 실행 경로·전략 분석

## 기준과 범위

입력 ZIP의 최상위 `모세의지팡이 Part1`, `모세의지팡이 Part2`를 기준으로 한다. ZIP 안의 `수정본/`은 사용하지 않는다. 원본은 별도 디렉터리에 보존하고 Part2 복사본에서만 작업한다. Part1의 파일 해시는 작업 전후 전체 비교한다.

이 문서는 코드 변경 전에 작성한 분석이다. 아래의 **가능한 게이트는 설계 후보**이며, 구현·검증의 완료 선언이 아니다. 실행 및 대조 상태는 최종 보고서에 별도로 기록한다.

## 공통 정답과 주의점

Part1 `monitor_OZ.OZMonitor._run_once`는 환경 Watch가 없을 때에도 OUT/IN, HMA cross, 후보 유지와 **silent completion consumption**을 처리한다. 환경이 처음 생기는 관측에서도 이미 완성된 조건을 먼저 조용히 소비한다. 따라서 새 Watch 생성 시 하위 엔진을 빈 상태로 시작하거나, 현재 OHLC의 마지막 몇 봉만 읽는 것은 같은 전략이 아니다.

후보의 10/7/7봉 제한은 OUT episode 시작 전 과거의 상한을 증명하지 않는다. 미종료 OUT 구간, cross 이전 극값, native 계산의 호출 이력, 이미 소비한 TRUE-B0 키가 남는다. 검증 없이 7봉/10봉 같은 고정 lookback을 사용하지 않는다. 안전한 재구성 기준은 **해당 실행의 마지막 정상 엔진 관측 이후부터 현재까지의 실제 관측 prefix**이다. 최초 활성화라면 해당 실행의 준비 구간도 포함한다. 과거 raw/관측 입력을 재생하는 것과 과거 실행의 지표를 캐시하여 재사용하는 것은 구분한다.

`generic_backtest.runner`는 전략 callback보다 먼저 `OptionalFeatureRegistry.advance`를 호출한다. `materialize=False`도 현재 코드는 native kernel을 호출한다. 전략 함수의 return만 앞당겨서는 이 계산이 줄지 않는다.

세션은 Part1 `program/config.txt`의 MAIN 08:00–12:00 / 14:00–18:00 / 21:00–24:00, OPENING 09:00–11:00 / 16:00–18:00 / 21:00–23:00(KST)을 대조했다. 경계의 포함 여부는 각 실제 코드의 비교식을 따른다.

## 전략별 분석 표

| 항목 | SPECIAL1 | SPECIAL2 | SPECIAL3 | SPECIAL4 |
|---|---|---|---|---|
| Part1 대응 | SPECIAL1 + Composer | SPECIAL2 + SWEEP + 외부유동성 controller | SPECIAL3 + unordered timed chain | SPECIAL4Runtime |
| 최초 LIGHT | 상위 OHLC/OPEN WONBI 접촉 | 외부 레벨·source TF OHLC | 1m/2m EMA cross와 5m/6m FVG 생성 | 30m 새 봉 경계 |
| 최초 선행 조건 | 같은 1h/2h/3h/4h의 TREND와 WONBI ALL | 같은 TF의 유효 SWEEP 사실 | 동방향 EMA50/200 cross와 FVG 생성의 무순서 쌍 | 직전 :15/:45 15m WONBI 접촉, 직전 30m 같은쪽 미접촉 |
| 선행 TF | 1h,2h,3h,4h | 1m~1h 12개 + 4h/8h/1d/세션 자료 | 1m,2m,5m,6m | 30m,15m,1m |
| 최소 feature | OPEN 4개·현 low/high, 접촉시 HMA50 포함 실제 TREND ensemble | 외부 레벨, 완료봉 H/L, ATR14 | 완료봉 EMA50/200, FVG 원래 ATR 계산 | 완료봉 WONBI, 30m close, 완료1m ATR14 |
| 봉중/완료 | WONBI 진행봉, TREND 실제 현재 snapshot | detector의 새 완료봉 처리; ATR 생존은 현재 자료도 사용 | EMA 완료봉 cross; FVG 생성/접촉은 각각 source contract | 30m 경계 완료봉 setup; 이후 excursion은 봉중 |
| 게이트 전 생략 후보 | 하위 Percentile/HMA6·17/OZ | 하위 Percentile/HMA6·17/OZ | 최종 OZ; pair 이전 FVG를 전부 끄면 안 됨 | 하위 Percentile/OZ; 최종 추세 filter |
| 다음 단계 | MAIN 세션에서 Composer child arm | same TF child + ATR14×1.5 자격, TRUE-B0 재검사 | pair 뒤 5/6/10/12/15m FVG touch + OPENING | 6분 active window, excursion < ATR×1 |
| ARMED 이후 TF | OZ 1m~1h와 NORMAL mapped union | SWEEP TF와 그 NORMAL mapped TF | 최종1m/2m와 NORMAL mapped TF | OZ1m/3m/6m; 최종 gate 1m/3m/15m |
| ARMED feature/engine | native4family, OPEN HMA6/17, HistoricalOZEngine NORMAL+DIVERGENCE | 동일 + external TRUE-B0 gate | NORMAL+OZ, FVG와 chain 유지 | NORMAL+OZ, 최종 HMA50/168·EMA50/200 |
| 복원 상태 | episode, cross극값, 후보, validation, silent consumption, alert keys | 동일 + 외부 qualification은 LIGHT 소유 유지 | 동일 + chain/FVG는 원래 이벤트 순서 유지 | 동일 + anchor/만료/취소는 LIGHT 유지 |
| 진행 상태 복원 | 현재 token 이전 실제 관측을 무환경으로 재생; 현재 활성화 probe 유지 | 완료/소비된 것을 되살리지 않고 exact replay | touch 전에 생긴 미완료 하위 후보 포함 | 경계 전 미완료 하위 후보 포함 |
| 완료 | NORMAL+DIVERGENCE source 완료, 첫 공유 child 소비 | 같은 TF 완료, 선택 sweep 자격 확인 | 유효 deadline의 최종 OZ | 유효6분·excursion·최종추세 gate를 통과한 OZ |
| 취소 | OZ 자체 반대cross/one-way 등; 단순 상위조건 false는 child 취소 아님 | 외부레벨 소멸/ATR 초과와 OZ 자체 취소는 별도 | 반대EMA 또는 반대5/6mFVG, 새pair 교체 | 새30m cycle, excursion >= ATR, 최종완료 처리 |
| 만료 | OZ 원래10/7/7 기준, Composer child에는 임의TTL 없음 | OZ 기준; 임의 sweep TTL 추가 금지 | gap1800초, final7200초와 실제 completion_deadline 결합 | anchor+360초 이상 |
| consumption | 무환경/활성화 silent consume + 성공 후 registration/child 처리 | TRUE-B0 검증·sweep 소비·registration/child 원래 순서 | pair 소비, 이벤트 중복방지, 최종 chain 처리 | 전달 억제되어도 해당 child 종료 |
| HEAVY OFF | 유효 child가 더 이상 없을 때 | 유효 child가 더 이상 없을 때; invalid external만으로 임의소비 금지 | 최종 child가 없거나 deadline 밖 | active 종료 |
| LIGHT 복귀 | WONBI/상위 TREND와 signature 유지 | external detector/signature 유지 | cross/FVG pair latch 유지 | 다음 실제30m 경계 |

| 항목 | SPECIAL5 | SPECIAL6 | SPECIAL7 | Part2 WATCH |
|---|---|---|---|---|
| Part1 대응 | SPECIAL5Runtime/handler | SPECIAL6 + Composer | SPECIAL7 + Composer | Composer/WatchOrchestrator/monitor_OZ의 지원되는 부분집합 |
| 최초 LIGHT | 부모 자체가 OZ이므로 부모 계산을 임의로 제거할 수 없음 | 같은15m/30m EMA 배열 | 실제15m TREND | 선언된 선행 TREND/FVG 조건 |
| 최초 선행 조건 | 5m~1h NORMAL DIVERGENCE 또는 DIVERGENCE_REGIME 완료 | EMA50/200 배열 + HMA168 현재 vs2봉전 + 같은 TF FVG touch | 15m TREND 방향 | ALL/ANY condition signature; OZ_DIRECT에는 상위 게이트 없음 |
| 선행 TF | 5,6,10,12,15,20,30m,1h와 mapped TF | 15m,30m | 15m | plan.conditions TF |
| 최소 feature | 부모4family/HMA/OZ; DIVERGENCE만 마지막완료봉 EMA 추가 | EMA close, HMA168 open, 원래 FVG | HMA50 등 실제 TREND ensemble | TREND HMA50 ensemble / FVG ATR |
| 봉중/완료 | 부모OZ 봉중/각 trigger 원래 기준; EMA는 완료봉 | MA snapshot 봉중, FVG source contract | 실제 현재 TREND snapshot | 조건별 source contract; HMA_CROSS/BAR는 완료봉 |
| 게이트 전 생략 후보 | 하위1/2/3m BLIND OZ/Percentile/HMA | 하위1~6m Percentile/HMA6·17/OZ | 하위1m/3m/6m Percentile/HMA/OZ | composite의 하위 Percentile/HMA/OZ |
| 다음 단계 | MAIN 세션에서 같은방향 최신setup으로1/2/3m child | MAIN에서 same-profile child arm | MAIN에서 같은방향 child arm | sticky child 생성, 같은 signature 재등록 금지 |
| ARMED TF | 1m,2m,3m; 부모 유지 TF | 1,2,3,4,5,6m + mapped union | 1m,3m,6m | OZ base와 mapped union |
| ARMED feature/engine | BLIND+OZ, child/parent HMA취소 | NORMAL+DIVERGENCE | NORMAL+DIVERGENCE_REGIME | plan의 validation/trigger |
| 복원 상태 | 기존 BLIND 후보·소비 상태 + 새 setup별 child 식별자 | 기존 NORMAL DIV 후보·소비 상태 | 기존 REGIME 후보·소비 상태 | OZ episode/cross/후보/mid_validated/alert_keys/environment identities |
| 진행 상태 복원 | setup 이전 살아있는 lower state, 이미완료된 것은 제외 | setup 이전 살아있는 lower state | TREND arm 이전 살아있는 lower state | gate까지 실제 prefix 재생, fresh activation probe |
| 완료 | 첫 유효 child OZ, 같은setup 형제 모두 종료 | 공유profile 최종OZ | 같은방향1m 최종OZ | 첫 same-direction OZ가 해당child 소비 |
| 취소 | 부모 반대cross 전체, 자식 반대cross 해당TF, 최신setup 교체 | OZ 자체 취소; 상위 false에 임의cancel 없음 | OZ 자체 취소; TREND반전만으로 기존child 임의삭제 금지 | 선행false는 stickychild 취소아님; gap에는 restart guard |
| 만료 | child마다 setup이후 완료봉 > MAX_BARS_AFTER_B0; 원래OZ 한계 | 원래OZ 한계, 임의 setupTTL 없음 | 원래OZ 한계, 임의TTL 없음 | 원래OZ 한계; timed chain 지원범위 확대하지 않음 |
| consumption | 부모 내부소비, 최종child+형제소비, 같은방향 최신setup | signature/child/TRUE-B0 중복방지 | signature/child/TRUE-B0 중복방지 | 무환경 silent consume와 fresh activation probe 유지 |
| HEAVY OFF | 유효lowerchild 없음. 부모 계산은 계속 | 유효child 없음 | 유효child 없음 | composite children 없음; direct는 끌 근거 없음 |
| LIGHT 복귀 | 부모 두profile 감시 | 같은TF MA/FVG + signatures 유지 | 실제TREND + signatures 유지 | 선행 detector/matcher/signatures 유지 |

## 수정 전 확인된 차이·실행 차단

1. SPECIAL1~4에는 `BACKTEST_PLUGIN` 메타데이터가 있지만 `requirements`/`create_alert_strategy`가 없다. Generic loader는 이들 레거시 소스의 import를 허용하지 않고, legacy discovery는 Generic marker가 있는 파일을 제외한다. `pit.jobs.specials`는 제공되지 않은 `replay.operational_baseline`에도 의존한다. 현재 UI 경로의 정상 전후 성능값을 임의로 만들 수 없다.
2. SPECIAL7의 `trend_direction`은 Part1 TREND 대신 **완료15m close EMA50/200**을 쓴다. Part1은 현재 snapshot TREND fact이며 ensemble이다. 이는 최적화 이전부터 존재한 전략 의미 차이이다.
3. SPECIAL7의 `bundle_state`는 `dict`만 허용하지만 Generic SDK는 `MappingProxyType`을 전달한다. source-defined 미정의 값과 별개인 입력자료형 문제이다. SPECIAL5/6은 `.get` 기반 접근이므로 같은 오류로 묶지 않는다.
4. SPECIAL5/6은 setup마다 새 하위 `OZProfile`을 생성한다. Part1의 이미 진행 중인 하위 상태를 그대로 포함한다고 볼 수 없다.
5. `results.verify_result`는 SUCCEEDED만 허용한다. coordinator 예외 경로는 `incomplete.json`만 기록하고 누적 alerts/dashboard를 finalize하지 않는다. IPC client 또한 cancel ACK를 받으면 결과가 있어도 error로 처리한다.
6. 기존 회귀시험의 최초 실행 결과: 27 PASS 후 3 FAIL에서 중단. 실패는 배포본에 없는 `backtest_specials/GENERIC_EXAMPLE_V1.py` fixture이다. 이것을 이번 변경의 신규 회귀와 혼동하지 않는다.
7. ZIP에는 실제 한 달 브로커 raw 시장 입력이나 Part1의 동일 관측시점 캡처가 확인되지 않았다. 합성 입력의 양성 상태기계 검증과 실제 시장/실제 LIVE 통합 검증을 구분해야 한다.

## 후속 확인 / 정정 (최종 판정은 02 문서)

초기 자료 확인 시점의 마지막 항목을 정정한다. 원본 ZIP에는 BROKER_REAL_TICKS 5,990,923개가 있다. 다만 hourly chunk 4,872개 중 4,659개가 EMPTY_UNVERIFIED이며 동일한 LIVE polling/native seed 캡처는 확인되지 않았다. 실제 96 tick prefix 계측을 추가했고 한 달 검증으로 표시하지 않았다.

SPECIAL4 consumption 설계 후보의 “전달 억제되어도 child 종료”는 일괄 적용하지 않았다. 시간 필터만 억제한 경우 유지, unknown final input은 ACK false로 retry, 명확한 expiry/excursion/추세 불허는 취소로 구분했다. 실제 구현·검증은 02 문서를 따른다.
