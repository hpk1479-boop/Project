# Event Engine E2 성능 측정 — XAU 결과

작성: 2026-09-27T02:28:35+09:00

**XAU 이벤트 재생의 묶음 처리 평균은 1131.900ms, p99는 1359.144ms다. OZ_STATE와 SWEEP_STATE가 합계 76.21%를 차지했다.** 최적화나 소스 수정은 하지 않았다.

## 사용자 판정과 이번 종료 범위

- E2 및 등록 확인 메시지 시점 차이 36건은 사용자 승인이다. 이 문서에 승인 사실을 기록하며 기존 소스·status·기준선·수정내역 파일은 변경하지 않았다.
- 최신 지시에 따라 **BTC cProfile을 중단하고 XAU의 완료된 자료만 보고한다.** BTC의 중간 프로파일과 일반 측정 수치를 이 보고서에 혼합하지 않는다.
- 수정본16 폴링판 CPU 측정은 아직 시작하지 않았다. 추가 실행 없이 종료하라는 지시에 따라 **미측정**으로 남긴다. 이벤트판과 폴링판의 속도 비율을 추정하지 않는다.
- E3, 최적화, 추가 회귀 테스트, MT5 실행은 하지 않았다. 산출물은 이 문서 하나다.

## 측정 조건과 범위

| 항목 | 값 |
|---|---|
| 후보 소스 | 수정본17 Part1, 파일 수정 없이 실행 |
| 입력 | 보존 MSP3 `검증결과/staff_s7/final_mt5_sigma3_v2` |
| 심볼 | XAUUSD+ |
| 시장 구간 | Unix 1790164800 이상 1790165040 미만, 240초 |
| 처리한 MARKET_BUNDLE | 239개 (시장 초 수와 동일한 개념이 아님) |
| 경로 | capture_bundles → StaffIngressAdapter.publish → STAFF → 동일 EventEngine |
| 전략 설정 | 승인된 E2 비교와 동일: SPECIAL1~7 등록, SPECIAL7 시험 트리거 `무지성 올존`, 기존 11개 Watch 명령 |
| 시작 상태 | E2 재생 시나리오의 신규 메모리 상태. 실제 폴링 저장 상태를 덮어쓰거나 임의로 섞지 않음 |
| 일반 계측 | 1회, cProfile 미적용; 경량 on_event/분류 경계 계측 포함, 계측 비용 보정 없음 |
| cProfile | 같은 캡처 별도 1회, 표준 cProfile 기본 wall timer |
| CPU 환경 | 논리 CPU 20개, 고정 affinity 없음, 분석/검증 작업 병렬 실행 없음 |
| Python / pandas / numpy | 3.13.15 / 3.0.1 / 2.3.5 |
| 네트워크 | socket/requests 전송 차단. Telegram은 논리 수집만 함 |

시작 전 Python 비교 프로세스가 없는 것을 확인했다. 일반 계측 직전 1초 간격 5회 전체 CPU 사용률은 **0.46~2.34%**, 프로파일 직전은 **1.29~2.77%**였다. 측정끼리 순차 실행했다. OS·Codex 등의 기본 백그라운드 서비스까지 정지한 환경은 아니다.

묶음 지연은 엔진의 Board commit·처리기·Consumer·내부 SIGNAL·기한 도래 타이머 처리를 포함한다. 캡처 파일 읽기와 STAFF decode, 초기 import/COMMAND 등록은 묶음 지연에서 제외한다. 아래 전체 재생 CPU/wall은 캡처 읽기·STAFF 전달까지 포함하고 초기화는 제외한다. 가상 시장 시각을 기다리는 sleep은 없다. p99는 239개 실제 표본의 NumPy percentile(선형 보간)이다.

## 1. XAU 묶음 처리 및 전체 비용

| 지표 | 일반 계측 결과 |
|---|---:|
| 묶음 평균 | 1131.900 ms |
| 묶음 p99 | 1359.144 ms |
| 엔진 묶음 처리 합계 | 270.524 s |
| 전체 재생 wall | 272.794 s |
| 전체 재생 process CPU | 239.687500 s |
| 시장 1초당 이벤트판 process CPU | 0.998698 CPU s / 시장 s |
| 논리 알림 | 13건 (등록 확인 11건 + 시장 조건 알림 2건) |
| 엔진 오류 | 0 |

프로파일 실행은 CPU 476.031250초, 전체 wall 540.535초였다. 호출 추적 비용이 있으므로 이 실행의 평균/p99를 일반 성능 수치로 사용하지 않는다. 일반 계측과 프로파일의 묶음 수·알림 수는 각각 239개·13건으로 같았다. 이번 작업은 성능 측정이며 새 결과 동일성 게이트를 실행한 것은 아니다.

## 2. 처리기·Consumer별 비용

| 처리기 / Consumer | on_event 호출 수 | 총 wall 초 | 묶음당 ms | 엔진 시간 비중 |
|---|---:|---:|---:|---:|
| OZ_STATE | 6,038 | 106.856 | 447.096 | 39.50% |
| SWEEP_STATE | 6,038 | 99.304 | 415.499 | 36.71% |
| FVG_STATE | 6,038 | 7.145 | 29.893 | 2.64% |
| INDICATOR | 6,038 | 9.956 | 41.658 | 3.68% |
| WATCH_CONDITIONS | 6,038 | 3.974 | 16.629 | 1.47% |
| COMPOSER | 6,007 | 24.155 | 101.065 | 8.93% |
| OZ | 6,038 | 0.018 | 0.076 | 0.01% |
| FVG | 6,038 | 0.212 | 0.885 | 0.08% |
| SWEEP | 6,038 | 0.315 | 1.319 | 0.12% |
| 엔진/Board/스케줄러/계측 등 나머지 | — | 18.589 | 77.780 | 6.87% |

총 wall은 각 on_event 진입부터 반환까지 누적한 값이다. 하위 DataFrame·지표·판정 함수 비용이 포함된다. 묶음당 비용은 **총 비용 / 239**이며, 개별 호출 평균과 다르다. 시장 묶음 239개 외 내부 SIGNAL도 전달되므로 대부분 처리기는 6,038번 호출됐다(시장 239 + SIGNAL 5,799). 모든 호출이 전체 판정을 수행했다는 뜻은 아니며 빠른 반환도 포함한다. COMPOSER는 필터 후 6,007번이다. 아래 기능별 분류는 이 표의 하위 비용이므로 두 표를 합산하면 안 된다.

## 3. DataFrame·지표·판정 비용 비율

| 배타적 계측 분류 | wall 초 | 분류 총시간 대비 |
|---|---:|---:|
| DataFrame 생성·복사 | 26.081 | 9.64% |
| Python 파생 지표 / Fact 계산 | 88.918 | 32.87% |
| 기존 판정·상태 처리 경계 | 114.335 | 42.26% |
| 그 밖의 처리 | 41.194 | 15.23% |

분모는 일반 계측의 engine.run 구간 합계 270.528초다. 중첩된 계측 경계에서는 현재 가장 안쪽 분류에만 시간을 배정하여 네 분류의 합이 100%가 된다. cProfile 누적 시간을 더해 계산한 비율이 아니다.

- **DataFrame 생성·복사:** pandas DataFrame.__init__, _constructor_from_mgr, DataFrame에 대한 NDFrame.copy 하위 실행. 모든 pandas 열 연산·인덱싱 비용이라는 뜻은 아니다.
- **지표 / Fact:** indicator_facts 함수 및 FactFrame.get/FactCache.frame, staff_compat.apply_requested_features, monitor_OZ.add_* 파생 함수, FVG 특징, Watch MA 특징의 계측 경계. 이는 Python 파생 계산/캐시 접근을 포함하며 MT5 원비 공식을 Python으로 다시 계산했다는 뜻이 아니다. 이 안에서 생성·복사한 DataFrame 비용은 앞 분류로 분리된다.
- **판정·상태 처리:** 기존 run_once/run_watch_once/run_spec_once/run_query/evaluate/evaluate_once/process/process_watch_result/maintenance_tick/_handle_event/_evaluate_symbol_locked/_evaluate_spec_locked/poll/handle_oz_event 중 기존 OZ/FVG/SWEEP/INDICATOR/Composer/Watch/SPECIAL4·5 클래스에 존재하는 경계. 단순 비교식만의 시간은 아니며 하위 상태·입력 처리 중 다른 분류에 잡히지 않은 비용을 포함한다.
- **기타:** Board, SIGNAL 처리, 분류 밖 직렬화·변환·조회 등. 경계에 들어가지 않는 pandas 작업도 여기에 포함될 수 있다. 이 정의를 넘어서 모든 pandas 비용이나 순수 비교 연산 비용으로 해석하지 않는다.

## 4. cProfile 누적 시간 상위 30개 — XAU

| 순위 | 함수 (파일:행) | 총 호출 / 원시 호출 | 누적 초 | 자체 초 |
|---:|---|---:|---:|---:|
| 1 | `event_engine/engine.py:150 run` | 239 / 239 | 538.233 | 0.006 |
| 2 | `event_engine/engine.py:139 _dispatch` | 239 / 239 | 538.221 | 0.038 |
| 3 | `event_engine/engine.py:76 _process` | 6,038 / 6,038 | 538.181 | 1.308 |
| 4 | `event_engine/frames.py:29 select` | 8,127 / 8,127 | 305.714 | 0.242 |
| 5 | `event_engine/frames.py:57 request` | 7,410 / 7,410 | 285.032 | 0.022 |
| 6 | `event_engine/oz_processor.py:27 on_event` | 6,038 / 6,038 | 229.213 | 0.689 |
| 7 | `monitor_OZ.py:4452 run_once` | 3,824 / 3,824 | 205.781 | 0.015 |
| 8 | `monitor_OZ.py:4461 _run_once` | 3,824 / 3,824 | 205.763 | 0.359 |
| 9 | `monitor_OZ.py:2948 compose` | 4,541 / 4,541 | 151.604 | 0.238 |
| 10 | `event_engine/sweep_state.py:20 on_event` | 6,038 / 6,038 | 147.141 | 0.096 |
| 11 | `strategy_SWEEP.py:1045 run_spec_once` | 2,868 / 2,868 | 146.892 | 0.121 |
| 12 | `strategy_SWEEP.py:1032 _load` | 2,868 / 2,868 | 145.112 | 0.141 |
| 13 | `staff_schema.py:44 legacy_frame` | 12,906 / 12,906 | 87.399 | 1.090 |
| 14 | `event_engine/frames.py:22 raw` | 85,563 / 85,563 | 76.562 | 0.176 |
| 15 | `strategy_SWEEP.py:229 build_external_levels` | 2,868 / 2,868 | 66.472 | 0.812 |
| 16 | `staff_compat.py:127 _compose` | 4,303 / 4,303 | 60.290 | 0.161 |
| 17 | `staff_compat.py:16 apply_requested_features` | 17,687 / 17,687 | 59.212 | 0.200 |
| 18 | `~:0 <built-in method builtins.isinstance>` | 214,602,130 / 213,386,906 | 57.869 | 37.251 |
| 19 | `pandas/core/frame.py:4337 __getitem__` | 335,703 / 335,703 | 53.204 | 1.833 |
| 20 | `event_engine/model.py:29 freeze` | 20,855,586 / 234,880 | 53.191 | 27.244 |
| 21 | `pandas/core/arrays/_mixins.py:80 method` | 24,354 / 24,354 | 53.152 | 0.062 |
| 22 | `pandas/core/series.py:4506 map` | 5,724 / 5,724 | 53.149 | 0.051 |
| 23 | `event_engine/composition_consumer.py:20 on_event` | 6,007 / 6,007 | 52.698 | 0.093 |
| 24 | `pandas/core/base.py:996 _map_values` | 5,724 / 5,724 | 52.392 | 0.013 |
| 25 | `event_engine/board.py:63 view` | 108,622 / 108,622 | 52.380 | 0.261 |
| 26 | `pandas/core/arrays/datetimelike.py:763 map` | 5,724 / 5,724 | 52.349 | 0.281 |
| 27 | `event_composition.py:163 step` | 6,007 / 6,007 | 51.663 | 0.386 |
| 28 | `indicator_facts.py:557 add_atr14_feature` | 17,687 / 17,687 | 51.536 | 0.557 |
| 29 | `event_engine/board.py:7 __init__` | 108,622 / 108,622 | 51.302 | 0.275 |
| 30 | `pandas/core/frame.py:4559 __setitem__` | 140,616 / 140,616 | 50.783 | 1.010 |

단위는 초이며 cProfile 기본 timer 기준이다. 누적 시간은 하위 호출을 포함하고 자체 시간은 해당 함수에 직접 귀속된 시간이다. 재귀 원시 호출과 전체 호출을 구별했다. 같은 비용이 부모와 자식의 누적 열에 함께 나타나므로 **누적 열을 합산하거나 그 합으로 점유율을 만들지 않는다.** 계측기 호출 추적 부하가 있으므로 일반 wall/CPU 표와 직접 빼거나 합산하지 않는다.

관측된 주요 경로는 BoardFrames.select/request → 입력 프레임·특징 준비, OZ run_once, SWEEP run_spec_once/_load다. XAU 프로파일에서 SWEEP run_spec_once 누적 약 146.9초 중 _load 하위가 약 145.1초였다. 또한 일반 계측에서 OZ_STATE·SWEEP_STATE 합계가 약 76%였다. 따라서 이번 표본에서는 입력 준비를 포함한 이 두 경로의 비용이 크게 관측됐다. 특정 수정의 개선율이나 병목 제거 효과는 측정하지 않았다.

## 5. 수정본16 폴링 CPU 비교

| 요청 항목 | 결과 |
|---|---|
| 같은 XAU 캡처의 시장 1초당 수정본16 폴링 CPU | **미측정 — 최신 사용자 지시로 추가 측정 없이 종료** |
| 이벤트/폴링 CPU 비율 | 산출하지 않음 |

이전 E2 실행의 wall 시간, 동시 실행 수치, 알림 결과를 CPU 시간 대용으로 사용하지 않았다.

## 6. 기존 폴링 실행 간격과 봉 사용 — 수정본16 소스 기준

아래는 정적 코드 확인 결과다. `wait(0.5)`는 **계산 완료 후 0.5초 대기**이므로 실제 wall 진입 간격은 계산 시간이 더해진다. part1_host는 이 대기 상수를 가상 스케줄 주기로 재현한다. 실행 루프가 0.5초라는 것과 0.5초마다 새 확정봉 판정을 한다는 것은 다르다.

| 대상 | 폴링 재실행 간격 / 호출 계기 | 확정봉 / 진행봉 사용 |
|---|---|---|
| OZ_STATE에 대응하는 OZMonitor | LOOP_SLEEP_SEC 기본 0.5초, 심볼·프로필별 루프 | 진행봉 percentile/레짐·터치와 확정봉 HMA cross·캔들·색전환 등 혼합. 전체가 봉 확정 전용은 아님 |
| FVG_STATE | 활성 Watch 0.5초, 없으면 0.25초 | 생성·채움·만료는 확정봉 구조 캐시, 터치는 현재 진행봉 high/low |
| SWEEP_STATE | 활성 spec 0.5초, 없으면 0.25초 | Detector.process는 -2 확정봉, last_closed_time 중복 배제. 데이터 로딩은 그 앞에서 실행 |
| INDICATOR / TREND | 활성 Watch 0.5초, 없으면 0.25초; 질의 도착 시 별도 처리 | 현재 -1 행 및 이전 행으로 지표/추세 계산. 모든 항목을 확정봉 전용으로 볼 수 없음 |
| WATCH_CONDITIONS / GenericConditionMonitor | LOOP_SLEEP_SEC 기본 0.5초 | BAR·기본 EMA/HMA cross는 확정봉; 원비·전일 가격 터치·percentile은 진행봉; MA 표현식은 evaluation_mode에 따름 |
| COMPOSER 유지관리 | maintenance_tick 후 0.25초 대기 | primitive 수집·조합은 COMPOSER_POLL_SEC=0.5초, Fact 입력은 도착 시 처리. 시간 연쇄·봉 만료도 별도 조건 검사 |
| OZ/FVG/SWEEP 알림 Consumer에 대응하는 경로 | 원래 처리기에서 조건 이벤트가 만들어지면 송신/Composer 처리. 별도 고정 알림 주기 없음 | 원본 Fact의 봉 의미를 유지 |
| SPECIAL1 | 독립 루프 없음. TREND/OZ 0.5초 및 Composer 원비·조합 0.5초 | 상위 TREND+원비 setup와 하위 OZ. 진행봉 조건과 OZ의 확정봉 조건 혼합 |
| SPECIAL2 | 독립 루프 없음. SWEEP/OZ 각 0.5초 + Fact 도착 시 조합 | SWEEP 확정봉 자격 → 같은 TF OZ. 최종 OZ는 트리거별 혼합 |
| SPECIAL3 | 독립 루프 없음. Generic cross/FVG/OZ 0.5초 + 연쇄 이벤트 처리 | EMA cross·FVG 생성은 확정봉, FVG touch는 진행봉, 이후 OZ |
| SPECIAL4 | 자체 poll 제한 0.5초, Composer 유지관리 hook | 새 30m 경계에서 확정 1m/15m/30m setup 검사. 감시 중 진행봉 1m/3m/15m 조건도 사용 |
| SPECIAL5 | 자체 poll 제한 1.0초; OZ 신호는 별도 0.5초 처리기에서 도착 | 부모/자식 OZ, 상위 EMA 필터·유지관리의 확정봉 수와 진행 조건 혼합 |
| SPECIAL6 | 독립 루프 없음. INDICATOR/FVG/OZ 0.5초 + Composer 조합 | 15m/30m 배열·방향과 진행봉 FVG touch, 이후 OZ |
| SPECIAL7 | 독립 루프 없음. TREND/OZ 0.5초 + Composer 조합 | 상위 15m 추세, 최하 1m OZ. 측정은 E2 시험 설정의 무지성 올존 사용 |
| SPECIAL 파일 재검색 | 기본 1.0초 (SPECIAL_SCAN_SEC) | 파일 변경 확인 주기이며 전략 판정 주기가 아님 |
| 명령/SWEEP 이력 worker | 0.20초 | 입력 전달 주기이며 봉 판정 주기가 아님 |

주요 근거: 수정본16 `monitor_OZ.py`의 GenericConditionMonitor.run/OZMonitor.run, `strategy_FVG.py` evaluate/main, `strategy_SWEEP.py` ExternalLiquidityDetector.process/main, `strategy_INDICATOR.py` main, `manager_KIM.py` maintenance_tick/ComposerMaintenance/_scan_special_strategies_if_needed 및 SPECIAL1~7. `LOOP_SLEEP_SEC` config 미지정 기본값 0.5초와 `COMPOSER_POLL_SEC=0.5`를 확인했다. SPECIAL4/5의 POLL_SEC는 각각 0.50/1.0이다.

## 보존 및 재현 정보

수정본16·17의 계측 대상 소스(Part1·루트 tests·build의 Python/MQL/스크립트)와 config·SPECIAL 설정·별칭 파일 SHA256 목록은 두 완료 실행 각각 전후 동일했다. 동일 목록의 합성 SHA256: `a54a5a3ca4eae0b3b6431ad8c974985421d638e0b586714c5737cbfdd06ffec6`. 기존 소스·config·성능 정책·기준선 파일은 수정하지 않았다. 실제 폴링 상태 파일에 쓰지 않았다.

임시 계측 도구·원시 자료는 수정본 밖 `C:\Users\hpk14\AppData\Local\Temp\event_e2_perf_20260927`에 있다. 완료 자료는 timedXAU.json과 profileXAU.json/.prof이며 BTC 중단 기록은 결과 계산에 사용하지 않았다. 수정본17에 새로 작성한 산출물은 **성능측정_EVENT_E2.md 하나**다. 측정 후 개선 작업 없이 대기한다.
