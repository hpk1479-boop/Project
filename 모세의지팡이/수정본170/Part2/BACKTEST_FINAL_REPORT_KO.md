# BACKTEST 실행 복구 및 ①→②→③→④ 최적화 실측 보고서

검증일: 2026-09-22. 기준 입력은 이번에 첨부된 `BACKTEST_standalone(1).zip`이며 이전 보고서의 실측값을 재사용하지 않았다.

원본 ZIP SHA-256: `9b311c73230ea3d9969d2d28538f589b5d796a09b5411490729292b5f4d419e1`

## 0. 결론과 검증 범위

실행 진입점 복구와 네 단계 코드 변경을 순서대로 적용했다. 완료한 회귀시험은 **153 PASS**, 산출 JSON의 별도 exact 비교는 **92 PASS / 0 FAIL**이다. 네 native family 총67,200회 호출, OZ12,000개 관측, 합성 1일/1주 BAR WATCH 누적80개 실행을 검증했다. 확인된 불일치를 큰 허용오차로 덮지 않았다.

**실제 브로커 raw가 첨부되지 않았으므로 실제 1일·1주 시장 데이터 성능은 SKIP이다.** 합성 raw에는 미래 행을 섞지 않았으나 실제 거래소/브로커의 tick 밀도·공백·세션·변동성을 대표하지 않는다. OZ 양성 상태기계와 native 수치 검증은 별도 fixture이다. 이를 합성 BAR WATCH 전체 성능과 곱하여 전체 OZ 백테스트 개선 배수로 환산하지 않았다.

주 실측 환경은 Linux x86-64, Python 3.13.5, NumPy2.3.5, Pandas2.2.3, pytest9.0.2이다. 원본 Windows 요구 버전인 Pandas3.0.1은 그대로 pin했다. 별도 Linux3.0.1 환경 설치 시도는 DNS 실패로 완료하지 못했다. **Windows Python3.12/Pandas3.0.1 GUI·MT5 및 3.0.1 runtime 회귀는 미검증**이다. 해당 설치 실패 로그도 보존했다.

## 1. 가장 먼저 수정한 실행 치명 오류

원본의 두 `.venv-*`가 다른 PC의 `C:\Users\hpk14\.cache\codex-runtimes\...` Python을 참조했다. 기존 진입점은 파일이 있다는 이유로 이 고장 난 실행기를 호출했다. 사용자 계정 이름만 `pyvenv.cfg`에서 바꾸는 방식은 사용하지 않았다.

새 시작점은 **`START_BACKTEST.cmd`**이다. 동작 가능한 현재 PC의 Python3.12/3.13 64비트를 찾고 `bootstrap.py`가 독립 프로세스로 실행/import/bitness/prefix를 검사한다. 고장 난 환경은 백업 후 재생성하며 설치 실패 시 실패 환경과 원본 환경을 모두 보존한다. `BACKTEST_SETUP.log`에 설치·GUI 예외를 남긴다. 복사된 Python이나 `.pyw` 파일 연결을 먼저 실행하지 않는다.

5개 시작점 시험이 PASS했다. 실제 Windows 더블클릭 성공을 주장하지 않는다. 이 ZIP은 Python 본체를 포함한 오프라인 포터블 EXE가 아니며, 현재 PC의 정상 Python(pip/Tcl/Tk 포함)과 첫 설치 시 패키지 저장소 연결이 필요하다. `START_BACKTEST_KO.md`에 실행 절차를 기재했다.

## 2. ① 최하위 TF 봉마감 Gate

`evaluation_base_tf`는 WATCH의 `required_timeframes`에서 초 단위로 가장 짧은 TF를 선택한다. coordinator는 `TimeframeCloseGate(base_tf)`를 사용한다. 외부 설정의 `ONE_MINUTE_CLOSE`와 직접 호출 호환 클래스는 남겼지만 coordinator가 1m를 강제하지 않는다. WATCH 의미/요구 TF에 새로운 TF를 삽입하지 않는다.

최하위 TF는 방금 닫힌 완료 봉, 그 외 TF는 실제 관측 시점의 진행 봉이다. 다음 실제 base 봉의 첫 유효 가격 tick을 받은 시점에 마감을 확정한다. quote/token/진입·청산 시각을 명목 마감시각으로 소급하지 않는다. tick 없는 구간에 가짜 마감을 만들거나 EOF에서 강제로 평가하지 않는다. 모든 raw tick은 여전히 시장/리스크 경로에 들어간다. 기존 TICK 분기는 유지했다.

### Gate 자체 actual callback 검증

1시간,10초 간격 입력, 사전 warmup 없는 fixture에서 실제 callback을 직접 세었다. 최초 봉과 EOF 처리 때문에 정확히60/20/10/4가 아니라 아래 횟수이다.

| required_timeframes | 선택 Gate | 실제 callback | 진행봉/미래 spike 검증 |
|---|---|---|---|
| 1m,3m,15m | 1m | 59 | PASS |
| 3m,6m,15m | 3m | 19 | PASS |
| 6m,15m | 6m | 9 | PASS |
| 15m | 15m | 3 | PASS |

추가로 BID/LAST 유효 tick, 동일시간 순서, gap, NaN 가격, base 완료행·상위 진행행, 불변 snapshot, closed-base frame adapter, lookback0, 새/legacy 결과 검증을 포함해 Gate10개 시험이 PASS했다.

### 1일/1주 Gate 구간 수정 전후

다음은 시장집계+Gate+가벼운 checksum callback만의 벤치마크다. 완전 WATCH 실행 시간은 뒤의 누적 표와 구분한다. 30초 간격 합성 raw이며 사전 warmup이 없어 누적 실행의 callback 기회보다 하나 작다.

| 기간 | 최하위 TF | 필터 | 전 callback | 후 callback | 전(s) | 후(s) | 배수 |
|---|---|---|---|---|---|---|---|
| 1일 | 1m | OFF | 1439 | 1439 | 0.108 | 0.103 | 1.05× |
| 1일 | 1m | ON | 539 | 539 | 0.106 | 0.103 | 1.04× |
| 1일 | 3m | OFF | 1439 | 479 | 0.097 | 0.091 | 1.06× |
| 1일 | 3m | ON | 539 | 179 | 0.098 | 0.090 | 1.08× |
| 1일 | 6m | OFF | 1439 | 239 | 0.083 | 0.082 | 1.01× |
| 1일 | 6m | ON | 539 | 89 | 0.077 | 0.078 | 0.98× |
| 1일 | 15m | OFF | 1439 | 95 | 0.062 | 0.059 | 1.05× |
| 1일 | 15m | ON | 539 | 35 | 0.063 | 0.060 | 1.06× |
| 7일 | 1m | OFF | 10079 | 10079 | 0.757 | 0.735 | 1.03× |
| 7일 | 1m | ON | 3779 | 3779 | 0.735 | 0.718 | 1.02× |
| 7일 | 3m | OFF | 10079 | 3359 | 0.662 | 0.640 | 1.04× |
| 7일 | 3m | ON | 3779 | 1259 | 0.659 | 0.630 | 1.05× |
| 7일 | 6m | OFF | 10079 | 1679 | 0.552 | 0.523 | 1.05× |
| 7일 | 6m | ON | 3779 | 629 | 0.539 | 0.511 | 1.05× |
| 7일 | 15m | OFF | 10079 | 671 | 0.443 | 0.412 | 1.08× |
| 7일 | 15m | ON | 3779 | 251 | 0.440 | 0.406 | 1.09× |

연결 검증에서 기존 결과 verifier가 아직 M1 메타데이터를 강제하는 FAIL을 발견하여 수정했다. 새 결과는 실제 최하위 TF/봉 상태 규약을 검사하고, 과거 결과는 과거 규약 그대로 검증한다. lookback0 예제 전략은 CLOSE에서만 완료 base 행을 한 개 보존하며 기존 requirements/warmup/TICK은 바꾸지 않았다. 당시 실패 로그를 삭제하지 않았다.

## 3. ② 정확한 incremental Percentile Band

`RollingNativeWindow`는 deque/chronological key·cell map·정렬된 (값,순서,key) 구조를 유지한다. 같은 계산 실행의 새 봉에서는 빠지는 값 삭제/새 값 삽입, 같은 진행봉에서는 해당 값 교체를 수행한다. 일반 유한값 경로에서 매 평가마다 전체 window를 재정렬하지 않는다. 이 구현은 히스토그램 근사가 아니며 SQLite/디스크 캐시만 추가한 것도 아니다.

기본 grid100 규칙은 원래 101개 threshold 산식·비교·목표 개수를 그대로 유지한다. 고정밀 PRICE 보간의 `a*(1-w)+b*w`와 oscillator 보간의 `a+frac*(b-a)`를 서로 통일하지 않았다. newest-first 동점 순서, n=1 원본 cell, window 포함범위, NaN/taint/warmup/write event를 보존한다. NaN/무한대/특수 signed-zero처럼 정확한 shortcut을 증명하기 어려운 예외는 원래 계산으로 fallback한다. fallback을 근사 계산으로 바꾸지 않았다.

완료 상태와 preview 적용을 분리하며 preview 수정은 원복한다. SAME/APPEND 상태는 재사용하고 재인덱싱/재시작/capture는 안전하게 재구성한다. PRICE/RSI/STO/DI source recurrence와 계산·쓰기 순서를 바꾸지 않았다. `incremental_percentiles=False`로 원본 경로가 보존되어 있다.

### 정확성 및 full native 호출 시간

정상/constant/NaN/warmup/sliding/다중 동점/반복 preview × 기본/고정밀 각각 비교했다. family당16,800회는 **7case×2mode×1,200회이며 case는5개의 독립240관측 sequence**다. 단일 객체16,800회 연속 실행이라고 표현하지 않는다. 원본 전체-buffer 비용으로 최초 무제한 시험이200초에 중단되어 이 구성으로 재검증했다. 별도 단위52개에는 restore/reindex/preview 추가 시험이 있다.

유한값 fixture는 명시적으로 `NATIVE_STATE_CONDITIONED`이며 실제 MT5 캡처가 아니다. source-only warmup에서는 `UNDEFINED_SOURCE_STATE`를 그대로 비교했다. 아래 불일치0은 NativeCell의 float비트·origin·taint·last_write_event, 모든 buffer, metadata, 쓰기 순서의 비교 결과다.

| family | 호출 수 | buffer cell 비교 수 | 불일치 | 원본(s) | 증분(s) | 배수 |
|---|---|---|---|---|---|---|
| PRICE | 16800 | 27774000 | 0 | 55.578 | 54.850 | 1.013× |
| RSI | 16800 | 22211790 | 0 | 18.225 | 16.098 | 1.132× |
| STO | 16800 | 34478860 | 0 | 25.764 | 24.086 | 1.070× |
| DI | 16800 | 34478860 | 0 | 26.266 | 25.097 | 1.047× |

Cold는 각 독립 sequence의 첫 native 호출 합계, warm은 동일 객체 후속 호출 합계다. 디스크 캐시는 OFF이다. 프로세스 기동/GUI/전체 raw 백테스트 cold/warm과는 다른 측정 범위다.

| family | cold 원본→증분(s) | warm 원본→증분(s) |
|---|---|---|
| PRICE | 1.444 → 1.366 | 54.134 → 53.484 |
| RSI | 1.216 → 0.988 | 17.009 → 15.110 |
| STO | 0.882 → 0.805 | 24.882 → 23.281 |
| DI | 0.894 → 0.807 | 25.371 → 24.290 |

완료된 current sample의 유한값/미정의 비교 개수도 기록했다(관측 수와 다른 buffer sample 수). PRICE52,770/11,960; RSI59,620/7,580; STO52,390/12,340; DI51,710/12,340이다. 모두 NaN만 비교한 PASS가 아니다.

### Band-only 수치 루프 (별도 마이크로벤치)

| family | 방식 | 행 수 | 원본(s) | 증분(s) | 배수 | 불일치 |
|---|---|---|---|---|---|---|
| PRICE | grid100 | 12000 | 1.297 | 0.157 | 8.28× | 0 |
| PRICE | exact | 12000 | 0.667 | 0.277 | 2.41× | 0 |
| RSI | grid100 | 12000 | 1.172 | 0.155 | 7.58× | 0 |
| RSI | exact | 12000 | 0.643 | 0.294 | 2.19× | 0 |
| STO | grid100 | 12000 | 1.156 | 0.152 | 7.58× | 0 |
| STO | exact | 12000 | 0.639 | 0.262 | 2.44× | 0 |
| DI | grid100 | 12000 | 1.162 | 0.154 | 7.55× | 0 |
| DI | exact | 12000 | 0.688 | 0.259 | 2.65× | 0 |

각 P20 연속12,000행 유한값 시험에서 full window build는1회, full sort는0회였다. 이 표의 약2.19–8.28배를 full native/전체 백테스트 개선 배수로 제시하지 않는다. PRICE full native 개선은1.013배에 그쳤다.

## 4. ③ OZ Pandas hot-loop

`oz_rules.py` 원문은 기준 ZIP과 바이트 단위로 같다. SHA-256:

`5cc327d9a4feda20fde885e4174cde4262c6f6f6b3af0faa416452ab1796d151`


OZ frame adapter에 관측 수명만 가지는 readonly NumPy column view/scalar row를 추가했다. 반복 `.iloc` Series 생성, vector timestamp 변환, bars_since 마스크 탐색을 scalar/index/binary search로 대체했다. candidate 생성·갱신·취소·만료, OUT→IN, HMA cross, TRUE B0, LONG/SHORT, DIVERGENCE/REGIME 순서는 원래 규칙 파일에서 그대로 실행한다. DataFrame 검증 경계와 최종 인터페이스는 남겼다.

array를 다음 관측까지 붙잡지 않아 Copy-on-Write 변경의 stale view를 피한다. private column accessor는 dtype/shape를 확인하고 object/extension/timezone/subclass 등에는 원래 public 경로로 fallback한다. 무조건 Pandas0을 목표로 하지 않았다.

### 650행×3TF,6profile×2,000관측

원본 단계② 코드와 최적화 코드가 동일 관측 sequence에서 반환한 event ID/순서와 snapshot/guard/resume/candidate 전체 상태의 digest를 관측별 비교했다. 입력은 유한 OZ frame fixture이며 raw/native PB end-to-end가 아니다. 아래 시간에는 frame 생성과 비교 digest 비용을 넣지 않고 `observe` 구간만 넣었다.

| profile | 관측 | 원본(s) | 최적화(s) | 배수 | 상태/이벤트 불일치 | 양성 Alert |
|---|---|---|---|---|---|---|
| NORMAL / OZ | 2000 | 25.534 | 3.991 | 6.40× | 0 | 9 |
| BLIND / OZ | 2000 | 21.998 | 4.076 | 5.40× | 0 | 124 |
| NORMAL / DIVERGENCE | 2000 | 24.700 | 4.067 | 6.07× | 0 | 0 |
| BLIND / DIVERGENCE | 2000 | 25.839 | 4.071 | 6.35× | 0 | 18 |
| NORMAL / REGIME | 2000 | 25.344 | 3.925 | 6.46× | 0 | 7 |
| NORMAL / DIVERGENCE_REGIME | 2000 | 25.598 | 4.015 | 6.37× | 0 | 0 |

합계 **149.012316 → 24.145980초, 6.171310배**. 무작위 sequence에서 Alert가0인 NORMAL DIVERGENCE/DIVERGENCE_REGIME도 별도 유발 시험에서 LONG/SHORT 양성 Alert·TRUE B0를 검증했다. 50개 OZ 단위시험에 timer/opposite/one-way 취소, 만료, retry/future guard, NaN/동점/Copy-on-Write/fallback이 포함되어 있다.

| NORMAL/OZ 프로파일러150관측 | 원본 호출 | 최적화 호출 |
|---|---|---|
| Series.__init__ | 10377 | 450 |
| DataFrame._ixs | 16877 | 450 |
| vector to_datetime | 3199 | 0 |

위 Pandas 호출 수는 cProfile 사용 시험의 수치이고, wall-time 표는 profiler를 끈 독립 장기 replay다. 다른 trigger profile에는 필요한 scalar 시간 변환이 남을 수 있다.

## 5. ④ 격리된 worker IPC

기존에 이미 있던 worker 재사용/DELTA_V1/local LRU를 새 성과로 계산하지 않았다. 프로세스를 합치지 않았다. strategy callback/관측 순서를 batch하거나 미래 평가를 선계산하지 않았다.

새로 적용한 것은 최대128개의 **순수 수치 HMA memo get/put** 묶음과 scope/checksum 검증 유지, logical item별 기존10,000-op 예산 차감, ordered ACK, private decoded-bar template 캐시다. BarState는 매 callback마다 새 객체를 만들어 원래 객체 identity/소유권을 보존한다. 공개 SDK decoder와 미래 읽기 검증은 그대로이며 호환되지 않는 class 변화에는 안전한 fallback이 있다. maxsize 부족으로 memo eviction 순서가 달라질 수 있는 경우에는 기존 경로를 사용한다.

UI PROGRESS는 payload의 실제 변화가 없을 때만 억제한다. control/결과/CANCEL_ACK는 억제하지 않으며 PHASE 변화는 dedup 상태를 reset한다. 별도1,000회 반복 시험에서 PROGRESS100개, 중복900개 억제와 연속 sequence/취소 ACK 보존을 확인했다.

### 실제 subprocess,3회 반복 중앙값

120관측×682행×3TF의 동일 synthetic WATCH context를 사용했다. 이 fixture는 원래 `PRICE:UNDEFINED_SOURCE_STATE`를 유지하여 OZ Alert는0/관측 skip120이다. 따라서 이 표는 HMA memo/JSON/transport 비용이고 양성 OZ 전략 성능 인증이 아니다. 양성 SIGNAL/ENTRY 두 역할과 OZ 상태기계는 별도 시험으로 검사했다.

Cold는 memo 저장소가 빈 상태, warm은 이전 수치 memo를 재사용하되 새 worker를 시작한 상태다. 관측 실행 중에는 worker를 재사용한다. 중앙값들은 서로 다른 trial에서 나올 수 있으므로 구성요소를 더해 전체 wall-time으로 만들지 않는다.

| 상태 | 측정 구간 | 원본(s) | 최적화(s) |
|---|---|---|---|
| cold | startup wall | 0.427617 | 0.443186 |
| cold | 120 observe wall | 2.053564 | 1.379013 |
| cold | parent JSON encode | 0.010439 | 0.002782 |
| cold | parent JSON decode | 0.014896 | 0.005704 |
| cold | parent transport prepare | 0.663750 | 0.646296 |
| cold | parent pipe write | 0.024699 | 0.007247 |
| cold | parent reader CPU | 0.015250 | 0.006069 |
| cold | parent queue wait (startup 포함) | 1.698960 | 1.118797 |
| cold | worker JSON encode | 0.002525 | 0.002374 |
| cold | worker JSON decode | 0.021910 | 0.022570 |
| cold | worker context decode | 0.911366 | 0.395536 |
| cold | worker pipe read CPU | 0.023925 | 0.011119 |
| cold | worker pipe read wall (idle 포함) | 3.192819 | 3.040125 |
| cold | worker pipe write | 0.019576 | 0.005219 |
| cold | worker callback wall (cache 왕복 포함) | 0.401985 | 0.251439 |
| cold | worker cache RPC roundtrip wall | 0.228411 | 0.085211 |
| warm | startup wall | 0.444912 | 0.447464 |
| warm | 120 observe wall | 1.930499 | 1.350234 |
| warm | parent JSON encode | 0.008569 | 0.003408 |
| warm | parent JSON decode | 0.008999 | 0.003274 |
| warm | parent transport prepare | 0.658053 | 0.664425 |
| warm | parent pipe write | 0.017933 | 0.007625 |
| warm | parent reader CPU | 0.012223 | 0.004074 |
| warm | parent queue wait (startup 포함) | 1.650819 | 1.097814 |
| warm | worker JSON encode | 0.002509 | 0.002333 |
| warm | worker JSON decode | 0.020851 | 0.021889 |
| warm | worker context decode | 0.921806 | 0.398879 |
| warm | worker pipe read CPU | 0.016259 | 0.009137 |
| warm | worker pipe read wall (idle 포함) | 3.089237 | 2.981510 |
| warm | worker pipe write | 0.013789 | 0.004362 |
| warm | worker callback wall (cache 왕복 포함) | 0.293036 | 0.198908 |
| warm | worker cache RPC roundtrip wall | 0.146668 | 0.049945 |

관측 wall-time 개선 배수: cold 1.489×, warm 1.430×. Startup은 개선되지 않았다. 부모 queue wait/pipe read wall에는 worker 계산·idle·입력 생성 시간이 겹쳐 들어 있으므로 독립 CPU 사용시간이 아니다. `worker callback - cache RPC wall`도 순수 CPU가 아니라 중첩을 뺀 wall 잔차일 뿐이다.

| 상태 | 통신량 | 원본 | 최적화 | 감소율 |
|---|---|---|---|---|
| cold | cache RPC 왕복 수 | 2794 | 140 | 94.99% |
| cold | parent→worker 메시지 수 | 2916 | 262 | 91.02% |
| cold | worker→parent 메시지 수 | 2916 | 262 | 91.02% |
| cold | parent→worker bytes | 2799869 | 2707285 | 3.31% |
| cold | worker→parent bytes | 601405 | 546514 | 9.13% |
| warm | cache RPC 왕복 수 | 1397 | 70 | 94.99% |
| warm | parent→worker 메시지 수 | 1519 | 192 | 87.36% |
| warm | worker→parent 메시지 수 | 1519 | 192 | 87.36% |
| warm | parent→worker bytes | 2776617 | 2730338 | 1.67% |
| warm | worker→parent bytes | 278907 | 251461 | 9.84% |

3회 모두 input SHA, 순서 있는 output SHA, finish diagnostics가 같다. worker 프로파일 응답은 부모의 private 진단으로 제거되어 strategy 결과/ID에 섞이지 않는다. 마지막 프로파일 응답 자기 자신의 write와 첫 bootstrap request의 일부 decode가 child 계측에서 제외되는 경계도 있다. `stage4_*_repeat*.json`에 원본 값을 모두 남겼다.

## 6. 전체 5단계 누적 wall-time — 합성 BAR WATCH

동일 raw/동일 WATCH plan,1일 및7일,1m/3m/6m/15m,거래시간 OFF/ON,5stage=**80개 독립 결과 실행**이다. 입력은2025-01-13 00:00 UTC부터30초 간격 deterministic native-format tick이며1일은 측정2,880tick+45분prefix90tick,7일은20,160tick+prefix90tick이다. 생성 자료에 SYNTHETIC 표시를 했고 브로커 완전성 인증을 만들지 않았다.

거래시간 ON은 원래 설정의 KST09–12/15–18/21–24이다. OFF/ON에서 raw 입력은 같고 필요한 시장 집계/리스크 tick을 버리지 않는다. 디스크 캐시는 OFF이며 OS 파일 캐시를 강제로 비우지는 않았다.

각 cell은1회 측정값으로 오차범위/통계적 우월성 보장이 아니다. coordinator preflight, worker기동, raw처리, 결과저장/검증을 포함하고 fixture 생성·WATCH compile·GUI/바깥 job 프로세스 기동은 제외한다. **BAR WATCH는 percentile/OZ를 호출하지 않으므로 +②/+③의 성능 기여를 이 표로 판정할 수 없다.** 이 표의 수치를 좋게 보이도록 순서를 재배치하거나 느린 값을 버리지 않았다.

| 기간 | base | 필터 | 기준(s) | +①(s) | +②(s) | +③(s) | +④(s) | 기준/최종 |
|---|---|---|---|---|---|---|---|---|
| 1일 | 1m | OFF | 2.866 | 2.835 | 3.095 | 2.860 | 2.820 | 1.02× |
| 1일 | 1m | ON | 2.238 | 2.166 | 2.336 | 2.263 | 2.221 | 1.01× |
| 1일 | 3m | OFF | 2.397 | 2.277 | 2.219 | 2.280 | 2.153 | 1.11× |
| 1일 | 3m | ON | 2.063 | 1.986 | 1.988 | 2.022 | 2.013 | 1.02× |
| 1일 | 6m | OFF | 2.445 | 2.062 | 2.034 | 2.107 | 2.057 | 1.19× |
| 1일 | 6m | ON | 2.129 | 1.971 | 1.990 | 1.988 | 1.942 | 1.10× |
| 1일 | 15m | OFF | 2.365 | 1.906 | 2.050 | 1.978 | 2.488 | 0.95× |
| 1일 | 15m | ON | 2.075 | 1.900 | 2.060 | 1.888 | 2.190 | 0.95× |
| 7일 | 1m | OFF | 9.289 | 8.894 | 9.287 | 8.543 | 9.373 | 0.99× |
| 7일 | 1m | ON | 5.144 | 4.906 | 5.001 | 4.922 | 4.978 | 1.03× |
| 7일 | 3m | OFF | 6.619 | 4.638 | 4.641 | 4.726 | 4.592 | 1.44× |
| 7일 | 3m | ON | 4.211 | 3.185 | 3.182 | 3.252 | 3.282 | 1.28× |
| 7일 | 6m | OFF | 6.024 | 3.445 | 3.448 | 3.517 | 3.574 | 1.69× |
| 7일 | 6m | ON | 3.888 | 2.921 | 3.004 | 2.844 | 2.993 | 1.30× |
| 7일 | 15m | OFF | 5.528 | 2.858 | 2.760 | 7.236 | 2.879 | 1.92× |
| 7일 | 15m | ON | 3.534 | 2.793 | 2.591 | 2.629 | 2.548 | 1.39× |

+③ 7일/15m/OFF의7.236초도 실제 관측값으로 남겼다. 이 단일 outlier의 원인을 별도 계측 없이 확정하지 않는다. 최종1m/OFF는 기준보다 약0.9% 느렸으며 모든 사례가 빨라졌다고 하지 않는다. 이번 입력에서는 최하위 TF를 올릴 때 Gate 감소 효과가 가장 분명했다.

### 실제 실행에서 기록한 평가 기회/필터 영향

원본은 모든 base 사례에서1분마다 feature 평가했고, 최종본은 아래 callback 기회를 기록했다. 사전 warmup이 있으므로 앞 Gate-only 표와 달리 측정 시작점에도 마감을 확정한다. BAR engine의 원래 startup suppression으로 Alert는 기회 수보다1작다. 이 startup 판정은 바꾸지 않았다.

| 기간 | base | OFF 평가 기회 | ON 평가 기회 | OFF Alert | ON Alert | 최종 OFF(s) | 최종 ON(s) | 시간 감소 |
|---|---|---|---|---|---|---|---|---|
| 1일 | 1m | 1440 | 540 | 1439 | 539 | 2.820 | 2.221 | 21.2% |
| 1일 | 3m | 480 | 180 | 479 | 179 | 2.153 | 2.013 | 6.5% |
| 1일 | 6m | 240 | 90 | 239 | 89 | 2.057 | 1.942 | 5.6% |
| 1일 | 15m | 96 | 36 | 95 | 35 | 2.488 | 2.190 | 12.0% |
| 7일 | 1m | 10080 | 3780 | 10079 | 3779 | 9.373 | 4.978 | 46.9% |
| 7일 | 3m | 3360 | 1260 | 3359 | 1259 | 4.592 | 3.282 | 28.5% |
| 7일 | 6m | 1680 | 630 | 1679 | 629 | 3.574 | 2.993 | 16.3% |
| 7일 | 15m | 672 | 252 | 671 | 251 | 2.879 | 2.548 | 11.5% |

①은 사용자가 요청한 평가 시각/봉 상태의 의도적 변경이다. 따라서 원본 CLOSE 결과와 event ID가 모든 전략에서 동일하다고 요구하지 않고, ① 적용본을 ②–④의 동일성 기준으로 삼았다. 원본 TICK은 원본과 직접 비교했다.

## 7. 결과 동일성 — 정확히 무엇을 비교했는가

WATCH plan:24개 명령 결과 전체 JSON(13개 compile 성공,11개 동일 거부)을 원본/최종 비교했다. 잘못 작성된 HMA 문자열을 파서 변경으로 억지 허용하지 않았다. 별도 올바른 `HMA6/17` TICK 양성 전략도 추가 검증했다.

OHLC:1일2,880tick의4TF 전체 불변 bar/quote/token/변경 이벤트를 원본/최종 관측별 비트 digest로 비교하여0불일치였다. Gate의 base 완료/상위 진행 view는 별도 prefix 산식/미래 spike 검사로 검증했다.

TREND:650행×3TF,240관측/720평가에서 score/direction/모든 요청 metric/state/event ID를 비교하여0불일치였다. UP390/DOWN317/NEUTRAL13,방향변화 event36개로 비어 있는 결과만 비교한 것이 아니다. TREND 계산식은 이번 변경 대상이 아니다.

PRICE/RSI/STO/DI:앞의67,200 paired invocation 및 bit/provenance/write-order 비교다. OZ는12,000관측 full-state/event trace와 별도 양성 유발 시험이다.

최종 결과 파일:① 기준으로②/③/④ 각16case, 총48쌍에서 alerts/events/dashboard/seed_plan **192개 파일 해시 비교**가 모두 같았다. 각 run의 manifest 자체도 verifier를 통과했다. 생성시각·성능 metadata·stage source hash가 다른 manifest/완료표식까지 byte-identical하다고 하지 않는다.

TRADE:원래 GENERIC_EXAMPLE 전략의1일 raw, TICK/CLOSE,OFF/ON,5종 stop×3종 target15variant를 실행했다. 원본 TICK→최종2쌍20개 결과 파일,①→최종4쌍40개 결과 파일 SHA가 모두 같다. `INVALID_STOP`은 original에서도 나오는 해당 variant의 결과이며 검증 실패를 덮은 것이 아니다. Alert/LONG/SHORT/entry/exit/stop resolution/target policy/outcome 파일과 순서/ID까지 포함한다.

| mode | 필터 | Alert/entry | LONG | SHORT | outcome | WIN | LOSS | INVALID_STOP | OPEN_END |
|---|---|---|---|---|---|---|---|---|---|
| TICK | OFF | 411 | 206 | 205 | 6165 | 1753 | 3642 | 531 | 239 |
| TICK | ON | 154 | 77 | 77 | 2310 | 650 | 1374 | 201 | 85 |
| ONE_MINUTE_CLOSE | OFF | 205 | 103 | 102 | 3075 | 904 | 1742 | 267 | 162 |
| ONE_MINUTE_CLOSE | ON | 77 | 39 | 38 | 1155 | 327 | 680 | 102 | 46 |

HMA6/17 TICK은 LONG/OFF142, LONG/ON54, SHORT/OFF142, SHORT/ON54개의 양성 Alert가 발생했고 4case의 plan·입력·결과 파일이 원본과 같다. 이 결과는 OHLC/HMA TICK 검증이며 native percentile/OZ의 실제 terminal 검증을 대신하지 않는다.

## 8. PASS / FAIL / SKIP

| 범위 | 판정 | 증거 |
|---|---|---|
| startup5 + Gate10 + percentile52 + OZ50 + IPC36 | 153 PASS | all_tests.log |
| 관측/plan/native/OZ/IPC/결과 파일 exact 비교 | 92 PASS / 0 FAIL | exact_equality_summary.json |
| 5stage × 2기간 × 4base × OFF/ON | 80 PASS (합성 BAR) | cumulative_*.json |
| TRADE3stage × 4case | 12 PASS (합성) | trade_*.json |
| HMA TICK 원본/최종4case씩 | 8 PASS (양성 합성) | hma_tick_*.json |
| 초기 timeout2건/source drift/결과 verifier/fixture 오류 | FAIL 기록 유지 → 재검증 PASS | FAILURE_LEDGER_KO.md |
| Pandas3.0.1 환경 설치 | FAIL (네트워크) | pandas3_install.log |
| 실제 브로커1일/1주, Windows/MT5, Pandas3 runtime, 실 raw 전체OZ, LIVE native 인증 | SKIP | failure_ledger.json S01–S06 |

합성 시험의 성공을 SKIP 항목의 PASS로 확장하지 않았다. 입력·기준 코드 손상 위험이 있는 수정은 하지 않았다. 기존 규칙 파일과 원본 ZIP을 보존했고, 회귀 원본 snapshot과 단계별 patch를 배포했다.

## 9. 남은 가장 큰 병목과 미해결 범위

실제 native 전체 호출에서는 원래 source recurrence, 전체 buffer materialization/clone/provenance, 호출별 입력 동기화가 여전히 크다. 그래서 band-only가 빨라도 PRICE 전체는1.013배다. 추가 최적화는 이 비용의 정확성 보존을 별도로 증명해야 한다.

IPC에서는 부모 transport/context 준비와 worker context decode가 JSON 자체보다 여전히 큰 부분을 차지한다. STARTUP 개선은 없고 작은 BAR 전략/짧은 실행에서는 시작·검증·파일 I/O가 비중을 차지한다. JSON을 임의 binary/pickle로 바꾸지 않았고 격리 경계를 제거하지 않았다.

OZ `observe`는6.17배 개선됐지만 frame 공급/native PB 준비/전송까지 포함한 실제 OZ1주 전체 성능은 이번 입력으로 확정할 수 없다. 실제 raw 및 검증된 native 초기 상태, Windows 배포 runtime 시험이 추가로 필요하다. `SOURCE_DEFINED_ONLY`의 미정의 값을 성능을 위해 유한 seed로 바꾸지 않은 것이 이 검증 한계의 일부다.

## 10. 배포 구성과 재현

최종 실행은 `START_BACKTEST.cmd`; 실행/복구 설명은 `START_BACKTEST_KO.md`이다. `validation_suite/`는 원본 경로를 남긴 테스트·벤치·exact comparer이고 `validation_outputs/`는 실측 JSON/초기 실패 로그를 포함한다. `validation_suite/references/`에는 source-only 기준/단계①②③/IPC계측전용 baseline이 있다. `patches/`와 manifest로 변경 파일을 추적한다.

① 이후 연결 검증에서 발견한 verifier/lookback0 수정은 누적 측정의 단계①②③ snapshot에 backport했다. 실제 예전 실패 기록은 그대로 남기고 `cumulative_reference_corrections.json`에 파일별 원본/보정 SHA를 기록했다. 실제 단계 commit 순서와 보정 snapshot을 혼동하지 않는다.

복사하면 다시 깨지는 `.venv-*`,`.git`,생성 bytecode/cache/run 파일은 배포하지 않는다. 기존 보고서/manifest는 `historical_reports/`에 원본으로 보존했고 이번 결과의 근거로 재사용하지 않았다.

재현 절차는 `validation_suite/REPRODUCE_KO.md`를 참조한다. 테스트용 pytest는 실행 의존성과 분리해 추가 설치한다. 실 브로커 접속이나 실제 주문은 이 검증에서 실행하지 않는다.

### 외부 확인 자료 (알고리즘 실측의 출처가 아님)

Python 공식 문서는 venv를 이동/복사 가능한 환경으로 보지 않고 새 위치에서 재생성하도록 설명한다: `https://docs.python.org/3/library/venv.html` (2026-09-22 확인).

원본 pin인 Pandas3.0.1 및 MetaTrader5 5.0.6180의 배포 파일 존재는 공식 PyPI에서 확인했다: `https://pypi.org/project/pandas/3.0.1/`, `https://pypi.org/project/MetaTrader5/5.0.6180/`. 컨테이너 pip 설치는 DNS 실패했고, 웹에서 파일 목록을 읽었다는 사실을 실제 설치/동작 PASS로 처리하지 않았다.

### 최종 ZIP 추출본 확인

새 폴더에 ZIP을 추출한 복사본에서 source manifest333개 파일의 SHA가 일치했고, `run_all.py` 기본 경로로153개 회귀시험을 다시 실행해153 PASS(51.37초)를 확인했다. compileall, source integrity,3m BAR 결과 저장/검증 및 lookback0 TRADE 실행도 통과했다. 이는 같은 Linux 환경의 포장 확인이며 Windows/Pandas3.0.1 검증을 대신하지 않는다. 로그는 `validation_outputs/package_checks.json`, `package_unit_tests.log`, `package_smoke.log`에 있다. 마지막 배포에는 이 포장 확인 기록만 추가하며 계산 소스는 검사본과 동일하다.
