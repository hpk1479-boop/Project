# Part2 봉마감 차단 수정 및 입력 공급 최소 최적화 보고서

작성 기준: 2026-09-23 (KST). 입력: 사용자가 제공한 `모세의지팡이 합본 - 지피티 (2).zip`의 **현재 Part2**. `구버전`은 작업 대상으로 사용하지 않았습니다.

**Part1 변경 파일 수 = 0. Part1 원본/실행 파일은 배포 ZIP에 포함하지 않습니다.** 실행 코드 수정은 Part2의 두 파일뿐입니다. 전략, 상태머신, scheduler, OHLC, commit 및 consumption 구현은 원본 그대로입니다.

봉마감 차단을 수정했고, 실제 입력에서 확인한 불필요한 행 bytes 변환과 중간 TickRecord 복제를 제거했습니다. 모든 검증을 무제한으로 완료했다고 주장하지 않습니다. 실제 대규모 SPECIAL 양성 신호 전수 검증, Windows 목표 환경 검증은 아래 한계에 따로 표시합니다.

## 1. 원인 코드와 수정

원인: `generic_backtest/conditional.py` 원본 38–42행의 `validate_cadence()`입니다. SPECIAL1~7 또는 OZ WATCH에 `ONE_MINUTE_CLOSE`가 설정되면 `E_INTRABAR_CADENCE_REQUIRED`를 발생시켰습니다. `generic_backtest/runner.py:87–88`에서 정상 setup에 앞서 이 검증을 호출하므로 실제 최하위 TF scheduler에 도달하지 못했습니다.

수정: 호출 지점과 호환 함수는 남기고 잘못된 거부만 제거했습니다. 함수 docstring에 다음 의미를 명시했습니다.

- `ONE_MINUTE_CLOSE`는 상위 TF의 완성을 기다리지 않습니다. 최하위 필요 TF의 마감 확인마다 평가하고, 상위 TF는 그 시점까지 관측된 진행봉을 사용합니다.
- 기존 첫 다음 봉 유효 tick에 의한 마감 확인 시각/순서를 유지합니다. 시각을 명목상 경계로 소급하지 않습니다.
- TICK, LIVE_PARITY의 cadence나 전략 조건, 상태 전이, consumption은 변경하지 않습니다.

유효하지 않은 모드를 거부하는 `validate_evaluation_mode()`는 그대로입니다. 봉마감 설정을 TICK/LIVE_PARITY로 자동 변환하는 코드는 추가하지 않았습니다. 현재 실행 소스에 `E_INTRABAR_CADENCE_REQUIRED`를 발생시키는 경로는 남아 있지 않습니다.

## 2. 최하위 TF 평가와 진행 중 상위 봉

`generic_backtest/evaluation.py`는 수정하지 않았습니다. 기존 `LOWEST_REQUIRED_TIMEFRAME_CLOSE_V2`, `evaluation_base_tf()`, `TimeframeCloseGate`, `close_evaluation_view()`를 사용합니다.

| 모드 | 유지한 평가 방식 |
|---|---|
| TICK | 기존 범위에서 raw tick마다 평가 |
| 봉마감 (`ONE_MINUTE_CLOSE`) | 전략이 실제 요구하는 최하위 TF 마감을 다음 유효 tick으로 확인할 때 평가 |
| LIVE_PARITY | 기존 RAW/PUBLISH/EVALUATE publication·poll 순서와 cadence 유지 |

1H+5m 회귀 테스트에서 09:05, 09:10, …, 09:55, 10:00 평가를 확인했습니다. 09:35에는 1H가 `FORMING`이며 09:32에 실제 관측된 low가 반영됩니다. 09:36의 큰 high는 09:35 view에 없고 이후 평가에만 나타납니다. 별도 fixture에서는 **실제 Part2 SPECIAL1의 WONBI/setup 함수**와 이미 참인 TREND 조건을 사용하여 setup이 09:35에 생성되고 10:00까지 지연되지 않는 것을 확인했습니다.

이는 사용자의 1H+5m 예시 검증입니다. 배포된 SPECIAL1~7의 실제 전체 required TF를 줄이지 않았습니다. 현재 SPECIAL1~7의 최하위 TF는 모두 1m이므로 실제 SPECIAL 봉마감 실행은 1분 단위입니다. 1H+5m WATCH의 최하위 TF는 5m입니다.

중요한 원래 경계 의미: 정확히 09:05:00에 tick이 없으면 다음 유효 tick의 **실제 시각**에 마감을 확인합니다. 시계만으로 관측되지 않은 봉을 만들거나 EOF에서 강제 마감하지 않습니다. 이 원래 규칙은 보존했습니다.

## 3. 수정 전 먼저 확인한 병목

실제 백테스트 raw 공급 경로는 DataFrame의 `iterrows`/`itertuples`가 아니었습니다. NumPy `.npy` 메모리맵의 정확한 **60바이트 native tick 레코드**였습니다.

최초 원본 프로파일의 실제 tick 50,000건에서 raw 검증/로딩은 2.165503초, 프로파일된 시장 처리 루프는 6.044631초였습니다. 원본 `GenericArchiveReader.__iter__` 누적 1.900초에는 많은 빈 chunk open 비용도 포함되며, 이를 전부 행 객체 비용이라고 계산하지 않았습니다. 별도의 원본 전체 coordinator 프로파일에서는 `TickRecord` 생성이 150,000회, `TickRecord.from_bytes`가 50,000회였습니다. 이 **프로파일러가 켜진 시간은 속도 개선 표에 사용하지 않습니다.**

증빙: `cadence_input_validation/evidence/initial_profile.txt`, `pre_edit_coordinator_profile.json`.

### 변경 전 공급 구조

```text
검증된 NumPy mmap
  → NumPy row scalar
  → row.tobytes()
  → RAW.unpack()
  → chunk-local frozen TickRecord
  → dataclasses.replace(global stream / ordinal)
  → 기존 runner의 role별 TickRecord 복제
  → 기존 시장/전략 상태머신
```

### 변경 후 공급 구조

```text
동일한 검증된 NumPy mmap / exact native buffer
  → RAW.iter_unpack(memoryview(...))로 현재 record만 decode
  → global stream / ordinal의 frozen TickRecord를 한 번 생성
  → 기존 runner의 role별 TickRecord 복제 (변경 없음)
  → 기존 시장/전략 상태머신 (변경 없음)
```

`generic_backtest/history/cache.py`의 `GenericArchiveReader.__iter__()`만 바꿨습니다. 이미 배열인 입력을 다시 DataFrame/Arrow/float 배열로 바꾸지 않았습니다. Arrow·DuckDB를 추가하지 않았고, 원시 tick에서 OHLC 배열을 미리 생성하지도 않았습니다. OHLC 생성은 기존 시장 상태머신에 그대로 맡깁니다.

이는 전체 Python 객체 제거가 아닙니다. C unpack의 현재 tuple, 최종 frozen `TickRecord`, role 복제 및 기존 state 객체는 남습니다. 줄인 것은 **NumPy 행 scalar/bytes 변환과 불필요한 chunk-local TickRecord 생성·재복제**입니다.

## 4. 의미 보존과 미래 입력 제한

float 필드는 원본 `RAW` 형식 그대로 uint64 비트로 전달합니다. float32 변환, 반올림, float Timestamp 경유를 하지 않습니다. timestamp는 Python 정수로 보존합니다. 원본 schema/hash/dtype/count/order/range 검증은 그대로이며, 행별 오류의 코드, 우선순위, 로컬 ordinal도 대조했습니다.

전체 mmap은 reader 내부에만 있습니다. 전략에는 현재 observation의 불변 레코드만 전달합니다. 테스트의 추적 unpacker로 `next()` 한 번에 현재 레코드만 decode되고, 중단 후 미래 레코드가 decode/전달되지 않는 것을 확인했습니다. 기존의 전체 archive 무결성 사전 검사는 변경하지 않았으며, 그 검사는 미래 값을 전략에 노출시키지 않습니다.

같은 timestamp의 tick, 중복 observation, 원래 source ordinal을 제거·재정렬하지 않았습니다. 전략 vectorization, batch 미래 참조, 상태 전이 재설계, 새 영구 feature cache는 없습니다.

## 5. 변경 파일과 Part1 감사

| 분류 | 파일 |
|---|---|
| 실행 코드 수정 1 | `generic_backtest/conditional.py` |
| 실행 코드 수정 2 | `generic_backtest/history/cache.py` |
| 관련 기존 테스트 수정 | `conditional_validation/test_conditional.py` |
| 추가 검증/측정/증빙 | `cadence_input_validation/` |
| 추가 최종 보고/감사 | `CADENCE_INPUT_FIX_REPORT_KO.md`, `CADENCE_INPUT_FIX_MANIFEST.json` |

`runner.py`, `evaluation.py`, `live_parity.py`, `partial.py`, `pit/models.py`, `pit/archive/reader.py`, SPECIAL1~7 소스 및 나머지 기존 실행 소스는 바이트 단위로 보존했습니다. 통합 ZIP 내 Part1 파일 1,331개는 원본 감사 대상으로만 사용했습니다. 필요한 Part1 oracle 세 파일의 읽기 전용 복사본도 배포에서 제외했습니다.

기존 파일별 원본/수정 SHA-256, 추가 파일 전체 목록, 제외한 실행 환경/데이터 폴더는 새 매니페스트에 있습니다. 관련 기존 세 파일의 diff는 `cadence_input_validation/evidence/source_changes.diff`에 있습니다. 원래 있던 다른 매니페스트나 과거 보고서는 불필요하게 수정하지 않았습니다.

## 6. 결과 동일성

### 실제 전체 raw 입력

| 항목 | 결과 |
|---|---|
| observation 개수 | **5,990,923 = 5,990,923** |
| 마지막 source ordinal | 5,990,923 |
| 연속 동일 밀리초 추가 observation | 163건, 개수/순서 유지 |
| 필드/타입/원시 bytes | 전체 lockstep 일치 |
| 최종 raw SHA-256 | `65badb07b40d0516b73a54bbcc3ee3966a09188ed049d87996b4ef1e2a01143d` |
| archive identity | `8a5ebbad46f06ebc900b62f0bc1068b3b8b635611ba2aed6e7a5e76e64304de0` |

이 측정은 전체 raw의 동일성 검사이며, 599만 tick 전체를 SPECIAL 상태머신까지 재실행했다는 뜻은 아닙니다.

### 이벤트·상태·commit

실제 coordinator와 격리 worker의 전후 run에서 `events.jsonl`, `alerts.jsonl`, 해당하는 `entries.jsonl`, `outcomes.jsonl` 등 결과 파일을 바이트 단위로 비교했습니다. callback payload의 canonical SHA, 요구 TF, scheduler, 실행 상태, 마지막 decision token/commit cursor, conditional diagnostics 및 dashboard도 비교했습니다. 제외한 값은 `_seconds`로 끝나는 비결정적 진단 시간뿐이며, 시장 event timestamp나 setup 시간은 제외하지 않았습니다.

비어 있지 않은 일반 알림·거래·중복 intent fixture는 LONG/SHORT, 알림 개수·방향·timestamp·순서, 결과 중복 정책을 확인했습니다. 실제 worker 중단 fixture에서는 40번째 입력 진입에서 취소하고 **마지막 commit 39, 결과 PARTIAL, 마지막 journal event CANCELLED**가 전후 동일했습니다. 세 cadence 및 ALERT_ONLY/TRADE 조합을 모두 비교했습니다.

OZ component fixture는 LONG/SHORT 각각 COMPLETE, OPPOSITE, EXPIRE, GAP, DELIVERY_RETRY를 실행하고 매 observation의 전체 상태 snapshot과 event tree를 비교했습니다. WATCH/ARMED/완료/취소/만료 및 consumption 관련 상태가 비교 범위에 들어갑니다. 완료와 delivery retry에서는 최종 양성 OZ alert가 정확히 1개인지도 확인했습니다. 이 fixture의 지표 프레임은 의도적으로 제어된 것으로, 실제 SPECIAL 양성 시장 신호 전수 검증과는 구분합니다.

성능 측정 run도 해당하는 result hash/의미적 metadata 또는 공급자 종료값을 재비교했으며, **23쌍 모두 일치**했습니다. 서로 다른 cadence 사이의 결과가 같다고 주장하는 것은 아닙니다. 각 cadence 안에서 최적화 전후가 같다는 검증입니다.

## 7. SPECIAL1~7 및 WATCH 결과

| 경로 | TICK / 봉마감 / LIVE_PARITY 실행 및 전후 동일성 | 실제 required 최하위 TF | 이 작업의 실제 50k 성능 측정 |
|---|---|---|---|
| SPECIAL1 | 모두 통과 | 1m | 미측정: 선택한 실제 prefix의 warmup coverage 부족 |
| SPECIAL2 | 모두 통과 | 1m | 대표 성능 경로로 선택하지 않음 |
| SPECIAL3 | 모두 통과 | 1m | 측정 |
| SPECIAL4 | 모두 통과 | 1m | 측정 |
| SPECIAL5 | 모두 통과 | 1m | 대표 성능 경로로 선택하지 않음; 동일 prefix는 요구 warmup coverage 부족 |
| SPECIAL6 | 모두 통과 | 1m | 대표 성능 경로로 선택하지 않음 |
| SPECIAL7 | 모두 통과 | 1m | 측정 |
| WATCH: 1H TREND + 5m OZ | 모두 통과 | 5m | 측정 (`WATCH_OZ`) |
| WATCH: 15m TREND + 1m OZ | 모두 통과 | 1m | 원본 사전 전체 프로파일, 통합 비교 |
| WATCH: 5m 봉마감 | 모두 통과 | 5m | 50k 및 200k 측정 (`WATCH_BAR`) |

주의: SPECIAL1~7 세 모드 테스트는 69개 합성 tick의 짧은 실제 worker 실행입니다. full warmup 없이 시작하는 fixture임을 명시하며 최종 SPECIAL alert는 비어 있습니다. 실제 50k SPECIAL3/4/7도 LIGHT 경로이고 최종 alert가 0건입니다. 따라서 각 SPECIAL의 실제 시장 양성 신호 전체를 확인했다고 표현하지 않습니다. 원래 전략 소스 보존, 전체 입력 비트 동일성, 각 callback/결과 동일성 및 별도 양성 상태 fixture가 이 변경의 근거입니다.

WATCH BAR의 실제 50k run은 평가 기회 28회, 최종 알림 **27건**입니다. 기존 primitive의 bootstrap 동작 때문에 첫 평가와 알림 개수가 1:1이 아니며 이 동작도 바꾸지 않았습니다. 실제 200k 결과는 알림 **85건**이 동일했습니다.

또 다른 기존 제한: `1시간 원비 하단 터치이고 5분 올존 알려줘`라는 자연어 WATCH 조합은 compiler에서 별도의 `COMPOSITE_CONDITION_SCOPE_UNSUPPORTED`로 막힙니다. 이번 봉마감 거부와 다른 문제이며, compiler 지원 범위를 임의로 넓히지 않았습니다. 1H+5m cadence는 generic gate, 실제 SPECIAL1 WONBI/setup 함수, 지원되는 TREND+OZ WATCH로 검증했습니다.

## 8. 성능 측정 방법

순서: 원본 프로파일 → 병목 확인 → 두 실행 파일 최소 수정 → 동일성 통합 suite → 아래 성능 측정입니다.

원본의 잘못된 guard가 있는 상태에서는 봉마감 실행 자체가 불가능하므로, 속도 비교의 양쪽은 **동일한 수정된 봉마감 guard와 동일한 전략/상태머신**을 사용합니다. `legacy` arm은 정확한 원래 공급자, `current` arm은 새 공급자입니다. 이는 cadence 변경 효과를 성능 개선으로 섞지 않기 위함입니다.

실제 제공된 native tick의 연속 prefix를 값·timestamp·순서 그대로 사용했습니다. 빈 과거 chunk의 파일만 양쪽 동일하게 합쳤으며 원본 gap 목록은 유지했습니다. 실제 데이터나 warmup 봉을 합성하지 않았습니다. SPECIAL의 warmup 요구량을 낮추지 않았습니다.

전체 시간의 범위는 `GenericRunCoordinator.run()` 진입부터 결과 기록 후 반환까지이며, 요구사항 조회/worker 시작, archive 검증·로딩, 시장과 전략 처리, IPC, 최종 결과 기록을 포함합니다. GUI 기동·외부 플랜 작성·Python 최초 import는 범위 밖입니다. 별도 프로세스 전체 wall-clock도 `process_status.json`에 있습니다.

각 arm은 새 Python 프로세스에서 순차 실행했습니다. 50k headline은 경로당 전후 각 3회이며 AB/BA/AB 순서입니다. 다른 테스트/벤치 작업과 동시에 실행하지 않았습니다. profiler는 껐습니다. 기존 disk cache는 양쪽 모두 비활성화했고, OS 파일 캐시는 강제로 비우지 않은 재실행 측정입니다. 서버/OS 스케줄링 편차가 남으므로 아래 수치는 보편적인 보장값이 아닙니다.

환경: Linux x86_64, Python 3.13.5, NumPy 2.3.5, pandas 2.2.3, pyzmq 27.1.0, pytest 9.0.2, psutil 7.2.2. 제공된 목표 requirements의 pandas는 **3.0.1**이며 이 컨테이너에는 없었습니다. 설치 시도는 네트워크/DNS 문제로 실패했습니다. 목표 Windows/pandas 3.0.1 환경은 실측 인증하지 못했습니다. 실행 requirements는 변경하지 않았습니다.

## 9. 전체 백테스트 wall-clock: 각 50,000개 실제 tick

중앙값, 단위 초. observation/sec는 raw observation 기준이며 전략 callback/sec와 다릅니다. 양쪽 모든 run이 SUCCEEDED이며 관측 수 50,000입니다.

| 경로 | 최적화 전 | 최적화 후 | 속도 배수 | 시간 감소율 | observation/sec 전 → 후 | 알림 전 = 후 |
|---|---:|---:|---:|---:|---:|---:|
| SPECIAL3 | 18.990 | 18.215 | 1.043× | +4.08% | 2,633.0 → 2,745.0 | 0 = 0 |
| SPECIAL4 | 18.739 | 18.183 | 1.031× | +2.97% | 2,668.2 → 2,749.8 | 0 = 0 |
| SPECIAL7 | 15.130 | 15.281 | 0.990× | -1.00% | 3,304.8 → 3,271.9 | 0 = 0 |
| WATCH_BAR | 14.778 | 14.501 | 1.019× | +1.87% | 3,383.4 → 3,448.0 | 27 = 27 |
| WATCH_OZ | 15.690 | 14.895 | 1.053× | +5.06% | 3,186.8 → 3,356.8 | 0 = 0 |

5개 경로의 중앙값 합계는 **83.326초 → 81.076초**, **1.028×**, **2.70% 시간 감소**입니다. 이는 대표 경로별 중앙값을 합친 지표이지 전체 599만 tick 백테스트의 속도 배수가 아닙니다. 개별 경로의 악화/편차도 제외하지 않았습니다.

| 경로 | 전 3회 원시 시간 | 후 3회 원시 시간 |
|---|---|---|
| SPECIAL3 | 18.990 / 18.657 / 19.123 | 18.308 / 18.215 / 18.088 |
| SPECIAL4 | 18.494 / 18.739 / 19.130 | 17.593 / 18.183 / 18.784 |
| SPECIAL7 | 14.774 / 15.241 / 15.130 | 15.281 / 14.987 / 15.390 |
| WATCH_BAR | 14.532 / 14.805 / 14.778 | 14.501 / 14.101 / 14.513 |
| WATCH_OZ | 16.103 / 15.690 / 15.620 | 14.895 / 15.121 / 14.736 |

## 10. peak memory 전후

50k 세 반복의 peak RSS 중앙값, MiB. parent와 parent+worker process tree를 20ms 간격으로 sampling했습니다. process-tree 합산은 공유 페이지가 중복 계상될 수 있으며 USS가 아닙니다. 짧은 최고점을 놓칠 수 있는 sampling 값이고 완벽한 최대 메모리 측정이 아닙니다. parent `ru_maxrss`도 각 JSON에 별도로 저장했습니다.

| 경로 | parent peak 전 → 후 | process tree peak 전 → 후 |
|---|---:|---:|
| SPECIAL3 | 107.00 → 107.04 | 204.79 → 204.80 |
| SPECIAL4 | 108.60 → 108.61 | 206.25 → 206.24 |
| SPECIAL7 | 105.42 → 104.25 | 200.74 → 200.81 |
| WATCH_BAR | 102.60 → 102.61 | 194.51 → 194.52 |
| WATCH_OZ | 104.26 → 105.18 | 199.70 → 199.73 |

전체 array를 추가 복제하지 않았습니다. 메모리 절감 자체가 주목표는 아니며, 남겨 둔 상태/입력 로그 및 worker 메모리가 대부분이므로 큰 RSS 개선을 주장하지 않습니다.

## 11. 구간 시간과 Python 객체 생성 경로

아래는 profiler 없는 headline과 **별도의 구간 타이머 측정**입니다. 각 경로 실제 50k, 단위 초. 데이터 로딩은 사전 무결성 검사와 iteration chunk open의 합, 행 변환은 reader next 시간에서 chunk open을 뺀 값입니다.

| 경로 / 공급자 | 검증·데이터 로딩 | 행/object 공급 | 시장 상태머신 `step` | 전략 worker observe 왕복 |
|---|---:|---:|---:|---:|
| SPECIAL3 / legacy | 0.030 | 0.467 | 3.112 | 2.661 |
| SPECIAL3 / current | 0.026 | 0.136 | 3.194 | 2.601 |
| SPECIAL7 / legacy | 0.028 | 0.466 | 2.201 | 0.455 |
| SPECIAL7 / current | 0.028 | 0.125 | 2.189 | 0.458 |
| WATCH_OZ / legacy | 0.029 | 0.517 | 2.347 | 0.076 |
| WATCH_OZ / current | 0.033 | 0.132 | 2.168 | 0.087 |

시장 상태머신 시간은 `GenericMarketCore.step` 실제 wall-time입니다. 전략 열은 IPC와 conditional 요청을 포함한 `StrategyWorker.observe` 왕복 시간이지 순수 전략 CPU만의 시간은 아닙니다. 두 열은 복원 작업에서 중첩될 수 있으므로 임의로 합산하지 않습니다. worker 내부 callback/IPC의 별도 계측값은 5k cProfile 증빙의 `worker_ipc_metrics`에 있습니다. 그 프로파일된 시간은 속도 배수 산출에 쓰지 않습니다.

별도 5,000개 실제 tick cProfile의 호출 수:

| 경로 | TickRecord 생성 | TickRecord.from_bytes | NumPy scalar.tobytes | dataclasses.replace 전체 타입 |
|---|---:|---:|---:|---:|
| SPECIAL3 | 15,000 → 10,000 | 5,000 → 0 | 5,000 → 0 | 43,269 → 38,269 |
| SPECIAL7 | 15,000 → 10,000 | 5,000 → 0 | 5,000 → 0 | 26,645 → 21,645 |
| WATCH_OZ | 15,000 → 10,000 | 5,000 → 0 | 5,000 → 0 | 26,629 → 21,629 |

위 ALERT_ONLY/LIGHT 측정에서 TickRecord 전체 생성은 입력의 중간 복제를 제거하여 observation당 3회에서 2회로 줄어듭니다. 입력 공급자 자체는 2회에서 1회이며, 원래 runner role별 복제 1회는 남습니다. `dataclasses.replace` 열은 bar/context 등 다른 타입도 포함한 전체 호출 수입니다. archive hash 검증용 ndarray의 chunk `.tobytes()`는 원래대로 남아 있으므로 scalar 변환 제거와 혼동하지 않습니다.

## 12. 더 큰 실제 입력의 측정

### 200,000개 실제 tick, WATCH BAR 전체 coordinator (각 1회)

| 항목 | 전 | 후 |
|---|---:|---:|
| 전체 wall-clock (초) | 51.919 | 50.912 |
| observation/sec | 3,852.2 | 3,928.3 |
| parent peak RSS (MiB) | 103.89 | 105.29 |
| process tree peak RSS (MiB) | 195.83 | 195.82 |
| 최종 알림 수 | 85 | 85 |

전체 속도: **1.020×**. 단일 반복이라 통계적 유의성을 주장하지 않습니다. 결과파일·commit·scheduler가 정확히 일치합니다.

### 5,990,923개 실제 tick 전체 raw 공급자만 (각 1회)

| 항목 | 전 | 후 |
|---|---:|---:|
| 사전 검증/로딩 (초) | 2.156 | 2.128 |
| 공급 iteration (초) | 29.922 | 8.542 |
| 검증+공급 전체 (초) | 32.078 | 10.670 |
| observation/sec | 186,762.2 | 561,467.6 |
| parent peak RSS (MiB) | 109.36 | 109.41 |

공급자 검증+iteration 속도는 **3.006×**입니다. **이 값을 전체 전략 백테스트 속도로 제시하지 않습니다.** 이 run에는 시장/전략 상태머신이 없으며, 큰 데이터에서 입력 비용 감소를 별도로 확인한 보조 측정입니다.

## 13. 테스트 집계와 남은 검증 한계

원본 suite: 285 passed / 4 failed / 33 skipped (322개). Part1 oracle가 없는 첫 원본 실행에서는 oracle 관련 17건이 skip되었으며, 읽기 전용 oracle를 붙인 별도 원본 대조 18건은 통과했습니다.

GUI와 읽기 전용 oracle를 반영한 원본의 유효 고유 집계는 318 passed / 4 failed / 0 skipped (322개)입니다.

수정본 전체 suite: 371 passed / 4 failed / 16 skipped (391개). 네 실패는 원본과 같은 `validation_suite/test_ipc.py::test_isolated_workers_order_positive_outputs`의 SIGNAL/ENTRY × DELTA_V1/FULL 네 조합이며, 누락된 `backtest_specials/GENERIC_EXAMPLE_V1.py` 때문에 발생합니다. 관련 없는 예제 경로를 수정하지 않았습니다. 새로운 입력/통합 검증 64건과 관련 cadence 검증은 모두 통과했습니다.

GUI 후속 확인을 반영한 유효 고유 testcase 집계: **387 passed / 4 failed / 0 skipped / 391 total**. 자세한 실행별 결과는 `final_test_summary.json`, `final_all_tests.xml`, `gui_followup.xml`에 있습니다. 동일 testcase의 재실행은 중복해서 합산하지 않았습니다.

남은 한계는 숨기지 않습니다. 모든 SPECIAL의 충분히 warmup된 실제 시장 양성 이벤트를 전체 599만 tick으로 전수 대조하지 않았습니다. 전체 실제 raw는 입력 동일성/공급 성능까지 확인했고, 전체 coordinator 실측은 대표 50k 및 WATCH 200k입니다. 테스트 환경은 목표 pandas 3.0.1/Windows와 다릅니다. 지원되지 않는 WONBI+OZ 자연어 WATCH 조합과 원본 누락 fixture 오류는 요청 외 문제여서 그대로입니다.

## 14. 남아 있는 주요 병목과 변경 유지 판단

가장 큰 별도 병목은 `generic_backtest/runner.py:197–198`의 매 tick gap 목록 선형 순회입니다. 원본 50k 전체 프로파일에서 generator 호출 **186,550,000회**, self 16.826791초, `any()` 누적 29.475934초였습니다. 프로파일 overhead가 있으므로 이 숫자를 무계측 실행시간 비중으로 환산하지 않습니다. 입력의 긴 EMPTY_UNVERIFIED gap 목록이 반복 검사되며, 이번 최소 입력 공급 최적화의 범위를 벗어나므로 변경하지 않았습니다.

그 밖에 시장 상태/봉 객체 갱신, token·identity canonical encoding/hash, role별 TickRecord 복제, worker IPC 및 상태가 활성화될 때의 지표/프레임 생성이 남습니다. 전략 계산의 vectorization이나 상태머신 재작성으로 이 비용을 줄이려 하지 않았습니다.

행 변환 호출이 실제로 감소하고, 큰 실제 raw 공급자 및 대표 경로 전체 wall-clock 합계에서도 개선이 측정되어 이 한 가지 입력 공급 변경을 유지했습니다. 개별 SPECIAL7처럼 중앙값이 악화되거나 편차가 큰 경우도 표에서 숨기지 않습니다. 모든 전략/환경의 일률적인 속도 향상을 보장하지 않습니다.

## 15. 적용과 재현

산출물은 Part2 실행 소스와 관련 테스트·증빙만 포함합니다. `.venv-generic`, `.venv-history`, `generic_cache`, `generic_runs`, `__pycache__` 및 Part1은 넣지 않았습니다. 기존 환경/데이터를 지우지 말고 **`BACKTEST CONTROL.pyw`가 있는 실제 Part2 루트**에 병합하십시오.

테스트/성능 재현 명령은 `cadence_input_validation/README_KO.md`에 있습니다. `reference_reader.py`는 테스트 전용 원본 공급자이며 실행 앱에는 모드/옵션을 추가하지 않았습니다. `aggregate_results.py`는 결과 hash와 의미적 metadata를 먼저 대조한 뒤 중앙값과 호출 수를 집계합니다. 새 영구 전략 feature cache는 없습니다.

## 16. 요청 항목 대응

| 요청 항목 | 이 보고서의 위치 |
|---|---|
| 1 원인 코드 위치 / 2 수정 방식 | §1 |
| 3 최하위 TF 평가 | §2 |
| 4 변경 전 row 구조 / 5 변경 후 입력 구조 | §3–4 |
| 6 변경 파일 / 7 Part1 변경 0 | §5 및 새 매니페스트 |
| 8 이벤트 동일성 | §6 |
| 9 SPECIAL1~7 / 10 WATCH | §7 |
| 11 전 시간 / 12 후 시간 / 13 속도 배수 / 14 observation/sec | §9, §12 |
| 15 메모리 | §10, §12 |
| 16 남은 병목 | §14 |
