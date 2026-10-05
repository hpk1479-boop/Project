# Part2 단계별 최적화 및 동일성 검증 보고서

작성일: 2026-09-23 / Asia·Seoul  
기준본: 제공 ZIP 최상위 `모세의지팡이 Part2`  
판단: **STEP 1·2·3 채택. 최종 pytest 441 passed / 4 BASELINE_FAILURE / 0 NEW_FAILURE.**

## 1. 범위와 최종 결론

Part1 원본 **1,331개 파일의 SHA-256을 전후 대조해 변경 0개**를 확인했다. Part1 애플리케이션을 실행하거나 수정하지 않았고, 기존 read-only AST numerical/state-rule oracle 테스트에만 사용했다. 최종 산출물에는 Part1 소스·폴더가 없다. Part2 실행 코드 변경은 **3개 파일**뿐이다. 함수·전략·폴더명을 바꾸지 않았고, 새로운 영구 feature cache, DuckDB, Numba, Rust, Cython, 의존성 변경은 없다.

정확성 검증 후 단계별 결정을 기록하고 다음 단계로 진행했다. STEP 3의 quote, role tick, HMA도 각각 기준 측정 → 변경 → 비트 동일성 테스트 → 성능 측정 → 채택 순서로 수행했다. 성능 때문에 전략 조건, 상태 전이, consumption, 관측 시각, 봉마감 cadence, 취소/commit 경로를 바꾸지 않았다.

**최종 동일 프로토콜 교차 측정(실제 50,000 tick, 각 3회 중앙값)**은 13.701 → 3.312초, 누적 **75.83% 단축**, 약 **4.14배 처리량**이다. 단계 단독 개선율은 STEP 1 **70.26%**, STEP 2 **14.47%**, STEP 3 **4.97%**다. 최초 실행값과 재측정값은 아래에서 구분했으며, 느린 sample도 삭제하지 않았다.

### 검증 범위의 중요한 한계

실제 5,990,923 tick 전체는 **gap predicate만** 측정했다. 전체 전략 backtest를 그 규모로 실행한 것은 아니다. 50k/200k SPECIAL7 성능 입력은 충분히 워밍업된 모든 지표/상태를 활성화하지 못했고, 전략 알림이 0개였다. 따라서 이 처리량은 완전히 워밍업된 SPECIAL1~7 장기 실행의 처리량이나 모든 양성 신호의 end-to-end 인증이 아니다.

별도 54개 actual worker 시나리오 중 **27개가 비어 있지 않은 알림**을 생성했다(일반 전략 24개와 WATCH_BAR 3개). SPECIAL1~7의 해당 짧은 시나리오는 startup/입력/상태 보존 검증이며, 충분히 워밍업된 양성 시장 신호를 생성한 검증으로 표현하지 않는다. 이를 BAR 전 필드 비트 동일성, 활성 HMA 수치 테스트, 기존 Percentile/OZ/Part1 상태 규칙 회귀와 함께 사용했다. 실제 MT5 LIVE 프로세스/전송/외부 SWEEP의 통합 검증은 수행하지 않았다.

## 2. 기준본·원시 데이터·환경

최신 코드만 `baseline`으로 보존했다. `구버전`의 코드를 복원하지 않았다. 최신 Part2 폴더에 raw archive가 없어, 제공 ZIP 안에 있던 v1.1 폴더의 **raw 데이터만** 검증 입력으로 읽었다. 경로와 `code_from_that_directory_used: false`는 `optimization_evidence/raw_origin.json`에 기록했다.

원본 raw SHA-256: `65badb07b40d0516b73a54bbcc3ee3966a09188ed049d87996b4ef1e2a01143d`  
원본 tick 수: **5,990,923**. gap 4,659개는 모두 `EMPTY_UNVERIFIED`였다.

실제 연속 prefix 5k/50k/200k를 만들 때 raw record의 비트와 중복 순서는 바꾸지 않았다. 빈 prehistory chunk만 통합하고, prefix cutoff 이후에 시작하는 gap은 제외했다. 이 시점 이후 gap은 prefix의 어떤 tick에도 원래 predicate를 만족시킬 수 없다. 숫자 OHLC나 warmup bar는 만들지 않았다. 마지막 동일 timestamp 그룹을 쪼개지 않도록 했고, 이번 입력의 실제 row 수는 요청 수와 정확히 같았다. 각 prefix의 gap은 3,730개다. 따라서 **186,550,000 generator 호출 재현은 이 prefix manifest 기준**이며, 원본 전체 manifest의 4,659 gap을 그대로 둔 다른 실행과 혼동하지 않는다.

Python 3.13.5, NumPy 2.3.5, pandas 2.2.3, pytest 9.0.2, Linux/xvfb 환경에서 실행했다. 제공 요구사항의 pandas pin은 **3.0.1**이나 현재 환경은 **2.2.3**다. 모든 전후 측정은 같은 환경을 사용했고 의존성 파일을 수정하지 않았다. pandas 3.0.1 및 Windows/실제 MT5에서 이 결과를 재현했다고 주장하지 않는다. 원본 ZIP 해시, thread 설정, 환경 상세는 `optimization_evidence/environment.json`에 있다.

## 3. 성능 측정 방법과 최종 표

**W**: 기존 실제 `GenericRunCoordinator` + 격리된 worker를 실행한다. `BACKTEST_SPECIAL7`, `ONE_MINUTE_CLOSE`, 실제 최하위 TF `1m`, session filter OFF, 기존 disk cache OFF, 50,000 raw tick/142 전략 평가다. 전체 배열 미래 OHLC를 공급하지 않는다. coordinator 시작부터 완료까지를 재고, CLI의 Python import 시간은 포함하지 않는다. 최종 교차 비교에서는 선택적 RSS sampler만 검증 wrapper에서 끄고, 메모리는 별도 실행으로 측정했다. 실행 순서는 결과를 보기 전에 정했고 3개 sample 전부의 중앙값을 사용했다.

**R**: 기존 benchmark의 20ms RSS sampling 별도 실행. parent+child RSS 합의 최대값이며 공유 페이지가 중복 집계될 수 있다. 물리 메모리 고유 사용량(USS)이 아니다.

**P**: 동일 50k 입력의 별도 cProfile 실행. inclusive 시간은 서로 중첩되고 profiling overhead가 포함된다. W에 더하거나 wall-clock 개선율의 분모로 쓰지 않았다. state 시간은 전략 `on_observation` 자체 self time이며 하위 feature/IPC 시간을 제외한다. feature 입력 시간은 worker `PITFrameCache.frame` + `frame_signature`의 inclusive 합이다. Python hotspot 집합은 parent의 `quote_values`, 내부 `number`, `dataclasses.replace/_replace` self time 합이다. 전체 Python 실행 시간이라고 확대하지 않는다.

**M**: 별도 함수 전용 microbenchmark. raw 읽기·worker·전략 실행 시간이 아니다. gap 함수는 STEP 1 이후 변경하지 않았으므로 M 값은 같은 구현의 독립 측정값을 공통 표시했다. 각 단계별 실제 profile 원본은 따로 보존했다. 0(미활성)은 빠르게 계산을 완료했다는 뜻이 아니라 해당 수치 kernel이 이 prefix에서 실행되지 않았다는 뜻이다.

| 항목 / 측정 범위 | BASELINE | STEP 1 이후 | STEP 2 이후 | STEP 3 이후 | 최종 |
| --- | --- | --- | --- | --- | --- |
| 전체 wall-clock, s (W) | 13.701 | 4.074 | 3.485 | 3.312 | 3.312 |
| raw observation/sec (W) | 3,649.4 | 12,272.0 | 14,347.6 | 15,098.7 | 15,098.7 |
| 전략 평가 횟수 / raw tick 수 | 142 / 50,000 | 142 / 50,000 | 142 / 50,000 | 142 / 50,000 | 142 / 50,000 |
| gap 판정 총 시간, s (M) | 8.489295 | 0.004624 | 0.004624 | 0.004624 | 0.004624 |
| tick loop 전체 gap 항목 검사 | 186,500,000 | 0 | 0 | 0 | 0 |
| gap tick-loop generator 호출 | 186,550,000 | 0 | 0 | 0 | 0 |
| tick당 평균 gap 항목 검사 | 3,730.00 | 0 | 0 | 0 | 0 |
| tick당 평균 해당 generator 호출 | 3,731.00 | 0 | 0 | 0 | 0 |
| 사전 status 검사 / relevant 검사 / cursor 이동 | 해당 없음 / 0 / 해당 없음 | 3,730 / 0 / 0 | 3,730 / 0 / 0 | 3,730 / 0 / 0 | 3,730 / 0 / 0 |
| BAR 생성 _apply_tick, s (P, inclusive) | 2.405797 | 2.094934 | 1.036956 | 0.987398 | 0.987398 |
| resample, s / 호출 (P) | 0 / 0 | 0 / 0 | 0 / 0 | 0 / 0 | 0 / 0 |
| feature 입력 frame+signature, s (P, inclusive) | 0.454071 | 0.414533 | 0.416876 | 0.453232 | 0.453232 |
| feature 수치 kernel 계산, s (P) | 0 (미활성) | 0 (미활성) | 0 (미활성) | 0 (미활성) | 0 (미활성) |
| state callback 자체, s (P, self) | 0.001428 | 0.001254 | 0.001308 | 0.001484 | 0.001484 |
| Percentile 수치 kernel, s (P) | 0 (미활성) | 0 (미활성) | 0 (미활성) | 0 (미활성) | 0 (미활성) |
| HMA wma_at/calculate, s (P) | 0 (호출 0) | 0 (호출 0) | 0 (호출 0) | 0 (호출 0) | 0 (호출 0) |
| OZ 계산, s (P) | 0 (호출 0) | 0 (호출 0) | 0 (호출 0) | 0 (호출 0) | 0 (호출 0) |
| FVG scan, s (P) | 0 (미활성) | 0 (미활성) | 0 (미활성) | 0 (미활성) | 0 (미활성) |
| 선정 Python hotspot 집합, s (P, self) | 1.414363 | 1.201364 | 0.423086 | 0.072976 | 0.072976 |
| peak process-tree RSS, MiB (R) | 200.73 | 200.80 | 200.77 | 200.77 | 200.77 |
| 전략 알림 / journal record (동일 50k 입력) | 0 / 1 | 0 / 1 | 0 / 1 | 0 / 1 | 0 / 1 |
| 마지막 정상 raw commit cursor | 50,000 | 50,000 | 50,000 | 50,000 | 50,000 |
| pytest 통과 / 실패 (범위 별도 명시) | 387 / 4 전체 | 20 / 0 gap 전용 | 54 / 0 추가 단위 | 54 / 0 후보 검증¹ | 441 / 4 전체 |
| 별도 worker 시나리오 동일성 | 54개 기준 확보 | 54 / 54 동일 | 54 / 54 동일 | 54 / 54 동일 | 54 / 54 동일 |

¹ STEP 3 quote/role 각각 54개 단위 검증, HMA 변경 후 관련 19개 검증을 실행했다. 전체 test suite를 매 단계마다 실행했다고 주장하지 않는다. STEP 1의 전체 경로 회귀는 별도 54 worker 시나리오이고, 최종 전체 suite에 기존 테스트와 신규 54개를 함께 실행했다. `최종` 성능 열은 STEP 3 이후 실행 코드가 그대로여서 같은 최종 측정값을 표시한 것이다. 추가 성능 변경을 적용한 것처럼 계산하지 않았다.

### 최종 교차 측정의 모든 sample

| 스냅샷 | 1회 s | 2회 s | 3회 s | 중앙값 s |
| --- | --- | --- | --- | --- |
| baseline | 13.824009 | 13.700771 | 13.442457 | 13.700771 |
| step1 | 3.981548 | 4.074328 | 4.101387 | 4.074328 |
| step2 | 3.469880 | 3.549274 | 3.484910 | 3.484910 |
| step3 | 3.311545 | 3.295055 | 3.456834 | 3.311545 |

### 최초 RSS-sampled 실행과 200k 결과 — 별도 데이터셋

| 규모 / 프로토콜 | BASELINE | STEP 1 | STEP 2 | STEP 3 / 최종 코드 |
| --- | --- | --- | --- | --- |
| 50k 초기 RSS-sampled, 3회 중앙값 s | 15.088 | 3.997 | 3.547 | 3.555 |
| 200k 초기 RSS-sampled, 각 1회 s | 56.369 | 12.507 | 10.596 | 10.845 |

STEP 3의 최초 50k 중앙값은 STEP 2보다 약 0.24% 느렸고, 최초 200k 단일 sample도 약 2.35% 느렸다. 이를 숨기지 않고 재측정 대상으로 기록했다. 같은 시기에 순서를 뒤집어 실행한 **200k 교차 비교**는 STEP 2 11.004 → STEP 3 10.334초(각 2회 중앙값), **6.09% 단축**이었다. STEP 2 sample은 11.425324, 10.582435, STEP 3 sample은 10.343510, 10.323875초다. 이후 W 프로토콜의 50k 균형 교차 측정에서도 STEP 3 개선이 재현됐다. 서로 다른 프로토콜/시점의 가장 좋은 값만 이어 붙이지 않았다.

## 4. STEP 1 — GAP 순회

### 실제 변경

`generic_backtest/runner.py`에 run-private `_GapCursor`를 추가했다. 시작할 때 `KNOWN_MISSING` / `ACQUISITION_ERROR`만 `(start_ns,end_ns)`로 추출하고 정렬한다. tick마다 `end_ns <= previous_ns`인 **앞쪽 구간만** 건너뛰고, 첫 남은 구간의 `start_ns <= now`를 판정한다. 원본 gap dict/순서와 tick 순서는 변경하지 않는다.

**union 병합은 하지 않았다.** 첫 남은 구간은 `end > previous_ns`이며 남은 구간 중 start가 최소다. 이 구간의 start가 now 이하이면 원래 any의 증인이 된다. start가 now보다 크면 뒤의 모든 start도 미래이므로 any는 false다. 이미 버린 구간은 end가 이전 previous 이하이고 tick이 단조 전진하므로 다시 true가 되지 않는다. 따라서 중첩/겹침/인접 구간에도 유효하다. 정렬된 end가 단조라고 가정하지 않는다. 0길이/역방향 구간도 임의의 union 해석으로 바꾸지 않고 원래 predicate 자체를 유지했다.

복잡도는 입력 gap status 1회 검사 O(G), relevant 정렬 O(R log R), tick 순회와 cursor 이동 O(T+R)이다. 새로운 디스크 cache가 아니다.

### 동일성

원래 any 코드를 테스트 reference로 유지했다. gap 없음, ignored-only, 각 대상 status 단독/다수/혼합, 겹침, 완전 중첩, 인접, start에 정확히 도달, previous=end, 전체 gap을 뛰어넘는 tick, 중복 timestamp, 긴 gap, 다량 ignored+소수 relevant, 전부 과거/전부 미래를 모두 포함했다. 같은 start·0길이·역방향 구간도 추가했다. **20개 pytest**, 1,000개 무작위 case × 100 tick = **100,000 tick-by-tick 대조**를 통과했다. 54 worker 시나리오와 실제 50k/200k 결과 파일·commit도 같았다.

### 측정

50k에서 원래 tick-loop gap generator는 cProfile 실측 **186,550,000회**였다. 항목 status 검사는 186,500,000회이고 나머지 50,000회는 generator 종료 resume다. 최적화 후 해당 tick-loop generator는 **0회**, relevant 검사와 cursor 이동도 이 실제 입력에서는 모두 **0회**다. 사전 status 검사 3,730회는 남아 있다. 사전 filter generator 자체의 존재를 숨기거나 프로그램 전체 generator가 0이라고 주장하지 않는다.

50k predicate-only: 8.489295 → 0.004624초. 200k predicate-only는 33.946066 → 0.017249초였다(각 2회 중앙값, `performance/gap_200000.json`). 실제 **5,990,923 tick 전체 predicate-only**는 최적화 구현 0.552004초(2회 중앙값)였고 전부 false였다. 모든 gap status가 비대상이므로 literal predicate와 동일하다는 독립 확인도 했다. **전체 6M backtest wall-clock이 아니다.** 전체 baseline 6M 시간을 추정해 실측처럼 제시하지 않았다.

판정: **ACCEPTED**. 관련 실행 파일 외 변경 없음. 롤백 필요 없음.

## 5. STEP 2 — multi-TF BAR / resample

### 현재 구조를 먼저 확인한 결과

현재 Part2는 이미 raw tick → `GenericMarketCore.step` → `GenericCandleBook.apply_tick/_apply_tick` 경로의 incremental BAR 생성이다. 요구 lookback의 TF만 book에 존재하며, forming bar의 interval 재사용, completed deque/tuple 재사용도 이미 있다. 핵심 BAR 경로에 반복 pandas resample은 없었다. `PITFrameCache.frame`은 completed/open signature가 달라질 때 재구축하고 그렇지 않으면 현재 forming 행의 high/low/close/volume만 수정한다. 숫자 계산용 groupby를 BAR resample과 혼동하지 않았다.

Conditional stream은 run-private raw log/checkpoint를 사용하지만 owner와 native invocation history가 다르다. 전략별/방향별 mutable DataFrame이나 재생 상태를 강제로 공유하면 catch-up·게이트 개방 시점의 의미를 바꿀 수 있어 공유/TF 생략을 추가하지 않았다. 이미 관측한 BAR를 미래의 완성 OHLC로 대체하지 않았고, 게이트가 닫힌 동안 필요한 상태 축적도 생략하지 않았다. 원래 cadence의 최하위 TF 봉마감 평가를 유지했다.

### profile에 근거한 적용

STEP 1 후 parent `dataclasses.replace` 217,064회, BAR `_apply_tick` 50,000회가 관측됐다. 그중 tick 내 BAR 갱신의 일반적 dataclass field 탐색/kwargs 구성 비용을 제거했다. `BarState`의 **19개 필드를 명시적으로 복사**하면서 기존 high/low 비교 순서와 덧셈 순서, state, quality, gap_before, complete_at_order, seed_quality를 모두 보존한다. 객체는 여전히 immutable이고 과거 snapshot을 수정하지 않는다. 완성 봉 처리·calendar·cache·DataFrame 생성 전략은 바꾸지 않았다.

BAR 전용 8TF 50k 측정은 1.625212 → 0.712716초, **56.15% 단축**이었다. 생성 횟수와 처리 rows는 동일하다. 줄인 것은 reflection 비용이지 의미상 필요한 BAR 수가 아니다.

| TF | forming 처리 row | completed 생성 | 총 BarState 생성 |
| --- | --- | --- | --- |
| 1m | 41731 | 142 | 41873 |
| 2m | 41731 | 71 | 41802 |
| 3m | 41731 | 47 | 41778 |
| 5m | 41731 | 28 | 41759 |
| 6m | 41731 | 23 | 41754 |
| 15m | 41731 | 9 | 41740 |
| 30m | 41731 | 4 | 41735 |
| 1h | 41731 | 2 | 41733 |

이 표의 41,731 forming rows/TF는 실제 입력의 유효한 BID 가격 갱신 수다. 나머지 raw tick을 삭제한 것이 아니다. 양쪽 구현 모두 같은 flags/유효성 조건을 적용했다.

### 전략별 전체 coordinator 측정

| 전략 | 입력 / 반복 | STEP 1 s | STEP 2 s | 개선 | 결과 |
| --- | --- | --- | --- | --- | --- |
| BACKTEST_SPECIAL1 | 합성 50k, 각 1회 | 7.664 | 4.936 | 35.60% | 동일 |
| BACKTEST_SPECIAL2 | 합성 50k, 각 1회 | 8.993 | 6.467 | 28.09% | 동일 |
| BACKTEST_SPECIAL3 | 실제 50k, 각 1회 | 6.906 | 6.156 | 10.86% | 동일 |
| BACKTEST_SPECIAL4 | 실제 50k, 각 1회 | 7.008 | 5.818 | 16.98% | 동일 |
| BACKTEST_SPECIAL5 | 합성 50k, 각 1회 | 14.777 | 12.599 | 14.74% | 동일 |
| BACKTEST_SPECIAL6 | 실제 50k, 각 1회 | 5.421 | 4.414 | 18.59% | 동일 |
| BACKTEST_SPECIAL7 | 실제 50k, 각 3회 중앙값 | 3.997 | 3.547 | 11.26% | 동일 |
| WATCH_OZ | 실제 50k, 각 1회 | 4.540 | 3.991 | 12.09% | 동일 |

SPECIAL1·2·5는 실제 prefix에서 원본/STEP 1/STEP 2 모두 `E_WARMUP_INSUFFICIENT: raw prefix missing`으로 중단됐다. 원본에서도 직접 재확인했다. 요구 warmup을 줄이거나 guard를 해제하지 않고, 별도의 epoch-start 합성 입력에서 같은 코드끼리 비교했다. 합성 값은 실제 broker 데이터 성능으로 표시하지 않는다. 각 단일 측정의 개선율은 통계적 확정값이 아니라 보조값이다.

### TF 생성·DataFrame counters (별도 합성 5,000 tick)

아래는 parent BAR 생성과 worker 내부 PIT frame을 실제 격리 프로세스에서 계측한 값이다. STEP 1/2의 모든 count와 결과 파일이 동일했다. raw 입력의 유효 가격 갱신이 5,000회라서 각 TF forming allocation도 5,000회다. input rows는 반복 frame 요청에 전달된 rows 합으로, 고유 row 수가 아니다. 실패한 not-ready frame 요청은 호출/input에는 포함되지만 반환 frame/rebuild에는 포함되지 않는다.

| 전략 | TF | BAR forming rows 합 | frame 호출 | frame 입력 rows | 재구축 | forming patch | DF copy | resample |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| BACKTEST_SPECIAL1 | 10m, 12m, 15m, 1h, 1m, 20m, 2h, 2m, 30m, 3h, 3m, 4h, 4m, 5m, 6h, 6m | 80000 | 26 | 26 | 0 | 0 | 0 | 0 |
| BACKTEST_SPECIAL2 | 10m, 12m, 15m, 1d, 1h, 1m, 20m, 2h, 2m, 30m, 3h, 3m, 4h, 4m, 5m, 6h, 6m, 8h | 90000 | 162 | 548 | 62 | 74 | 62 | 0 |
| BACKTEST_SPECIAL3 | 10m, 12m, 15m, 1m, 2m, 3m, 5m, 6m | 40000 | 120 | 418 | 48 | 46 | 48 | 0 |
| BACKTEST_SPECIAL4 | 15m, 1m, 30m, 3m, 6m | 25000 | 50 | 206 | 24 | 0 | 24 | 0 |
| BACKTEST_SPECIAL5 | 10m, 12m, 15m, 1h, 1m, 20m, 2h, 2m, 30m, 3h, 3m, 4h, 5m, 6h, 6m | 75000 | 26 | 34 | 2 | 6 | 2 | 0 |
| BACKTEST_SPECIAL6 | 10m, 12m, 15m, 1m, 20m, 2m, 30m, 3m, 4m, 5m, 6m | 55000 | 26 | 26 | 0 | 0 | 0 | 0 |
| BACKTEST_SPECIAL7 | 15m, 1m, 3m, 6m | 20000 | 13 | 13 | 0 | 0 | 0 | 0 |
| WATCH_OZ | 15m, 1h, 30m, 5m | 20000 | 2 | 2 | 0 | 0 | 0 | 0 |

TF별 세부 counters와 별도 wall/profile 시간은 `performance/step[12]_*_tf_counters_synthetic_5000.json`에 있다. 이 계측 wrapper는 운영 파일을 수정하지 않으며 기존 optional profile metadata로만 counters를 반환한다. 계측 wall-clock을 성능 개선값으로 쓰지 않았다.

### 동일성

15개 BAR 테스트에서 SDK 전체 19개 TF, BID/LAST, lookback 0/1/8/681, duplicate tick, 5m/15m/30m/1h·날짜·대규모 공백 경계, invalid price/flags, PARTIAL_START, gap quality, completion/seed metadata, 과거 snapshot 불변, 무작위 2,000 tick을 원본 reference와 비교했다. **5m 봉마감 12회에서 진행 중 1h를 사용**하고, 후반에 나타나는 9,999 가격을 이전 시점의 1h high에 미리 넣지 않는 테스트도 통과했다. `BarState` 필드가 추후 추가되면 테스트가 실패해 명시적 생성자를 재검토하도록 했다.

하위 상태 선행·게이트 나중 개방·취소·완료·consumption·재생은 기존 conditional/restoration/partial/live-rule tests와 54 worker 회귀에서 확인했다. state-machine 구현 자체는 바꾸지 않았다.

판정: **ACCEPTED**. 반복 resample 제거/전략별 stream 통합을 새로 구현했다고 주장하지 않는다.

## 6. STEP 3 — Python hot loop

### profile와 우선순위

STEP 2 후 실제 50k parent profile에서 `quote_values` **91,873회**, 내부 `number` 367,492회, `dataclasses.replace` **50,365회**가 남았다. 이 반복 순수 변환/객체 생성부터 처리했다. 같은 실제 prefix에서 HMA 수치 kernel은 호출되지 않아 그것을 대형 실전 병목이라고 주장하지 않았다. HMA는 별도 활성 682-row HMA6/17 workload를 먼저 profile했다. 최초 활성 profile의 `wma_at`은 40,920회 호출됐고 kernel 자체 CPU의 대부분을 차지했다.

| 함수 / workload | 반복 | 기준 중앙값 s | 최적화 중앙값 s | 단독 개선 | 판정 |
| --- | --- | --- | --- | --- | --- |
| quote_values / 실제 raw 50k × 10회 | 5회 | 0.410253 | 0.179627 | 56.22% | 채택 |
| own_tick 복사 / 실제 raw 50k × 10회 | 5회 | 1.326814 | 0.555181 | 58.16% | 채택 |
| HMA OPEN / 682행 × HMA6·17 × 150회 | 7회 | 0.568553 | 0.526519 | 7.39% | 채택 |

각 비교는 현재 최종 함수와 제공 기준본의 동결된 test-only reference 간 대조다. role tick microbenchmark는 runner의 실제 `own_tick` 대입식을 AST로 추출해 측정했다. 운영 coordinator를 다른 구현으로 대체한 것이 아니다.

**quote_values**: `<Q`/`<d` pack-unpack 4회를 `<4Q`/`<4d` 1회로 묶었다. float64 숫자를 새 정밀도로 계산하지 않고 raw bit를 재해석한다. dict key 순서와 metadata를 유지한다. 11개 IEEE 경계값(±0, subnormal, ±∞, quiet/signaling NaN payload 포함) 및 무작위 uint64 10,000 tick을 byte 비교했다. 단독 함수 개선과 전체 backtest 개선을 구분한다. 이 후보만 적용한 최초 전체 50k 개선은 측정 편차 수준이었으나, 함수 전용 개선은 뚜렷하고 중첩 함수를 제거해 코드/할당 복잡성을 줄였다. 최종 결합 효과는 W/200k 교차 측정으로 재확인했다.

**own_tick**: 10개 `TickRecord` 필드를 그대로 복사하고 기존 role ordinal만 넣는다. 별도의 상태 전이 함수를 만들지 않고 기존 대입 위치를 유지했다. 실제 대입식의 10,000 무작위 raw bit/ordinal 대조에서 raw bytes, tick_id, 원본 불변성을 확인했고, 모델 필드 guard도 추가했다.

**HMA**: `math.isfinite`, `sys.float_info.max`의 동일 값을 local로 잡고, 한 iteration 안에서 같은 정수 weight의 float 변환 결과를 재사용했다. 정수 weight 증가, numerator/denominator 누적, threshold 비교, NaN/EMPTY_VALUE 처리, window 시작, warmup, 기간 제한은 그대로다. vectorized dot/FMA/fastmath/float32를 쓰지 않았다. 전체 OPEN HMA6/17 series를 list/NumPy float64/object, None, NaN, ±∞, EMPTY_VALUE, ULP 인접값, overflow/underflow/±0에서 비트 비교했다. 7개 paired round 중 6개에서 빨랐고 한 개의 느린 sample도 보존했다.

**Numba 미적용**: 추측성 JIT를 도입하지 않았다. 따라서 cold compile / warm JIT 시간은 **해당 없음(미실행)**이며 0초 compile 성능으로 표시하지 않는다. Python/NumPy/struct 구조의 작은 개선만 유지했다. Percentile, OZ, FVG, EMA, TRUE-B0는 이 실제 prefix에서 충분히 활성화된 CPU 근거가 없어 수정하지 않았다. 미측정 native kernel의 속도 향상을 주장하지 않는다.

판정: **3개 후보 모두 ACCEPTED**. 최종 상태에서 실패한 최적화 구현은 없고, 실제로 되돌릴 필요가 확정된 운영 최적화도 없었다. 미적용 위험 후보와 모든 기존/입력 실패·재측정 과정은 실패 로그에 따로 남겼다.

## 7. 최종 동일성·상태·취소·commit

최종 suite: `validation_suite`, `conditional_validation`, `cadence_input_validation`. **441 passed / 4 failed**, pytest 자체 시간 **316.21초**. 기존 기준본은 **387 passed / 4 failed**, 301.70초였다. 기존 통과 387개가 모두 유지되고 신규 54개가 통과했다. 테스트 수가 달라 이 suite 시간 자체를 최적화 wall-clock으로 비교하지 않는다. 기존 실패와 다른 failure node는 **0개**, 기존 pass → fail/skip/missing도 **0개**다.

기존 4개는 `validation_suite/test_ipc.py::test_isolated_workers_order_positive_outputs`의 SIGNAL/ENTRY × DELTA_V1/FULL 조합이다. 원본에 `backtest_specials/GENERIC_EXAMPLE_V1.py`가 없어 전략 로드 전에 실패한다. 이를 복원하거나 테스트를 삭제/xfail/skip하여 숨기지 않았다. pytest exit code가 1인 이유는 이 4개다.

별도 actual worker matrix는 SPECIAL1~7, WATCH_OZ/WATCH_TREND_OZ/WATCH_BAR, 비어 있지 않은 일반 SIGNAL/TRADE, TICK/ONE_MINUTE_CLOSE/LIVE_PARITY, FULL/DELTA_V1 transport, 정상 완료/39 raw commit 후 취소를 포함한다. **54 case 전부**에서 전달 context payload SHA, 모든 JSONL 원문 SHA/행 수, dashboard, execution, evaluation schedule, last token, requirements, conditional 진단, worker finish 상태가 기준본과 같았다. 54개 case 합계 알림은 **561개**이며 중복 시나리오 간 재생까지 포함한 총합이다. 고유 시장 신호 수라고 해석하지 않는다.

비교에서 제거한 것은 진단 소요시간 `_seconds`뿐이며 시장 timestamp, ordinal, 이벤트 순서/중복, setup/WATCH/ARMED/cancel/expire/complete/consumption 상태를 임의 정규화하지 않았다. 바뀌어야 하는 source provenance hash나 output 경로 자체를 전략 결과 동일성으로 오인하지 않았다. 최종 전체 suite는 기존 Percentile 52개/OZ 50개/Part1 live-rule 16개/conditional 15개/restoration 7개/partial 11개 등의 기존 테스트를 그대로 포함한다. PARTIAL 파일의 마지막 CANCELLED journal, atomic observation rollback, OPEN_CANCELLED 결과도 기존 테스트에서 통과했다.

추가 matrix의 LIVE_PARITY 시나리오는 producer=10,000ms, poll=5,000ms, phase=0/2,500ms인 명시적 검증 설정을 썼다. 실제 LIVE 기본값을 변경하지 않았다. 이 합성 phase 검증을 실제 운영 LIVE polling 전체 인증으로 바꾸어 표현하지 않는다.

### 실제 prefix 마지막 commit (모든 단계 동일)

| raw 수 | raw commit | raw commit ns | decision ordinal | decision now_ns | 결과 |
| --- | --- | --- | --- | --- | --- |
| 50000 | 50000 | 1785986578485000000 | 49394 | 1785986520048000000 | FULL / END_OF_DATA |
| 200000 | 200000 | 1786003893565000000 | 199705 | 1786003860048000000 | FULL / END_OF_DATA |

50k의 마지막 raw commit과 마지막 전략 decision ordinal 49,394가 다른 것은 원래의 봉마감 cadence 때문이다. EOF에서 강제 봉마감 평가를 추가하지 않았다. 이벤트 시각도 nominal bar end가 아니라 원래 관측 token/경계 정책을 그대로 보존했다.

## 8. 실패 처리와 diff

`FAILED_OPTIMIZATION_LOG.md`에 4개 기존 pytest 실패, 실제 raw warmup precondition, 초기 harness 재시도, STEP 3 성능 편차 재확인, 공유/JIT 미적용 사유를 기록했다. 환경/검증 precondition 실패를 NEW_FAILURE로 오분류하지 않았으며, 동일성이 깨진 코드를 남기지 않았다. 미적용 설계 후보는 운영 코드에 들어간 적이 없어 rollback 대상이 없다.

실행 코드 diff는 다음뿐이다.

| 파일 | 변경 이유 | STEP 1 | STEP 2 | STEP 3 | 무관 변경 |
| --- | --- | --- | --- | --- | --- |
| `generic_backtest/runner.py` | gap cursor + 정확한 role TickRecord 생성 | 예 | 아니오 | 예 | 없음 |
| `generic_backtest/market.py` | immutable BAR 생성 비용 + quote bit 변환 | 아니오 | 예 | 예 | 없음 |
| `pit/features/hma_open.py` | 같은 HMA 연산의 local/weight 변환 재사용 | 아니오 | 아니오 | 예 | 없음 |

**Part1 변경 0개 / Part2 기존 파일 수정 3개 / 삭제 0개.** 추가 검증·보고 파일은 297개이며, Part2 전체 변경·추가 경로 수는 300개다. 추가 항목에는 원시 evidence/log/profile이 포함되므로 실행 코드 변경 수와 구분한다. 전체 파일별 SHA/분류/단계/이유는 `optimization_diff_manifest.json`, 실행 코드 unified diff는 `optimization_source_changes.diff`에 있다. 기존 보고서/기존 reference ZIP은 제공 Part2의 기존 검증 자산으로 유지했으며, 이를 현재 실행 코드로 복원하지 않았다.

새 검증 코드는 `test_optimization_gap.py`, `test_optimization_bars.py`, `test_optimization_hot_loops.py`다. `optimization_reference.py`는 제공 최신본에서 동결한 테스트 전용 oracle이며 운영 코드가 import하지 않는다. `optimization_regression.py`는 임시 일반 검증 전략을 만들고 finally에서 삭제한다. benchmark/profile/input-preparation wrappers는 운영 코드와 분리되어 있다. 새 영구 feature cache가 아니다.

최종 ZIP은 최신 Part2의 기존 396개 파일을 모두 유지하고 위 변경·검증 자료만 추가한다. Part1, 작업 snapshot, 외부 raw archive, 신규 runtime `generic_runs`/`generic_cache`, 임시 전략, bytecode, 실패한 실험 구현은 넣지 않는다.

## 9. 재현 및 증거 위치

명령과 입력 준비 방법은 `OPTIMIZATION_REPRODUCTION.md`를 따른다. 원시 evidence는 `optimization_evidence/` 아래에 있다. 특히 `optimization_measurements.json`은 모든 표의 수치, `environment.json`은 환경, `measurement_protocol.json`은 측정 순서/범위, `integrity_checks.json`은 Part1 및 원본 무변경/신규 실패 0을 기록한다. 단계별 `stage*_decision.json`, `step3_*_decision.json`, pytest XML/log, `*_parity_comparison.json`, 전체 profile와 개별 sample JSON을 함께 보존했다.

이 산출물의 채택 기준은 **검증된 동작 동일성 + 재현된 개선**이다. 위에서 명시한 미실행 전체 6M 전략/완전 warmup/다른 pandas·Windows·실제 MT5 LIVE 범위를 완료했다고 주장하지 않는다.
