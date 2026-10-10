> 과거 개발 단계의 보존 문서입니다. 아래 프로필/검증 수치는 수정본48의 현행 계약이나 검증 결과가 아닙니다. 현행 네 프로필과 SPECIAL5·7 변경은 `수정내역48.md`, 현재 전략 설명은 `Part3/docs/SPECIAL_1_TO_7.md`를 참조하세요.

# SPECIAL1~7 수정 상태 요약

## 적용 범위

기준본은 이번에 업로드된 `SPECIAL2_7_partial_working_project.zip` 하나입니다.
SPECIAL2 → 3 → 4 → 5 → 6 → 7 순서로 현재 `Part2/live_replay`에 필요한 경로를 연결하고 누적 실행 검증했습니다. SPECIAL1의 전략 정의는 유지했습니다.

**이번 결과는 native 관측기록 재생 경로의 SYNTHETIC_PASS입니다. 실제 LIVE_PARITY_PASS가 아닙니다.**
Part1 82개 파일과 DataManager 81개 파일은 기준 ZIP과 전체 바이트가 동일하며, 추가·삭제도 없습니다. DuckDB 구현·최적화는 하지 않았습니다.

사용 입구는 `Part2/LIVE_REPLAY.pyw` 또는 Part2 폴더에서 `python -B -m live_replay`입니다.
기존 `BACKTEST CONTROL.pyw`의 일반 백테스트 경로를 이 재생 경로로 자동 전환하지는 않았습니다.

## 전략별 실제 dependency와 상태

아래에서 A = 1m·2m·3m·4m·5m·6m·10m·12m·15m·20m·30m·1h입니다.
`NORMAL`, `BLIND`는 validation_mode이고, `OZ`, `DIVERGENCE`, `DIVERGENCE_REGIME`은 trigger_mode입니다.

| 전략 | runtime에서 유지·판단하는 고유 조건 | ALLZONE 종류 | ALLZONE TF | 상태 |
|---|---|---|---|---|
| SPECIAL1 | 같은 1h/2h/3h/4h branch의 TREND + WONBI. 서로 다른 TF를 섞지 않음 | NORMAL / DIVERGENCE | A | 기존 전략 유지, LONG/SHORT 회귀 통과 |
| SPECIAL2 | source TF SWEEP, 외부 ATR 자격 상태·identity·소비·무효화. SWEEP은 source TF 외 1d/4h/8h/5m 관측도 사용 | NORMAL / DIVERGENCE | 해당 SWEEP과 같은 TF, A 중 하나 | LONG/SHORT 및 실제 2m triggering 회귀 통과 |
| SPECIAL3 | 확정 1m 또는 2m EMA50/200 교차 + 신규 5m/6m FVG의 순서무관 결합, 이후 5m/6m/10m/12m/15m FVG touch. 반대 교차·반대 신규 FVG 취소, OPENING/MAIN 필터 | NORMAL / OZ | 1m 또는 2m | 양방향·두 교차 TF·두 입력 순서 통과 |
| SPECIAL4 | 새 30m 경계에서 확정 15m WONBI touch와 확정 30m 같은 방향 WONBI 미접촉. 6분 cycle, 1m ATR14 이동량, 최종 1m/3m/15m EMA/HMA 조건 재검증 | NORMAL / OZ | 1m | 양방향, ATR·추세 차단, 만료·재시도 통과 |
| SPECIAL5 | 상위 두 종류 ALLZONE을 내부 setup으로 소비. DIVERGENCE 부모만 같은 TF 확정봉 EMA50/200 확인. 부모/자식 반대 HMA6/17 교차, 자식별 봉수 만료, 첫 성공 후 형제 소비 | 부모 NORMAL / DIVERGENCE 및 DIVERGENCE_REGIME → 자식 BLIND / OZ | 부모 5m·6m·10m·12m·15m·20m·30m·1h → 자식 1m·2m·3m | 양방향·두 부모 종류·세 하위 TF 통과 |
| SPECIAL6 | 같은 15m 또는 30m 안의 현재 EMA50/200 배열 + 현재 HMA168 대 2봉 전 기울기 + 같은 방향 FVG touch. stale MA는 신규 setup을 막지만 기존 watch를 임의 취소하지 않음 | NORMAL / DIVERGENCE | 1m·2m·3m·4m·5m·6m | 양방향·15m/30m branch 및 교차 TF 혼합 차단 통과 |
| SPECIAL7 | 15m TREND 방향. 추가 WONBI/FVG/SWEEP setup 없음. 파일 주석과 달리 실제 등록·최종 코드의 MAIN 세션 필터 적용 | NORMAL / DIVERGENCE_REGIME | 1m | 양방향, 반대 추세·레짐·세션 차단 통과 |

모두 XAUUSD+와 NAS100을 원문대로 별도 처리합니다. XAUUSD+ LONG/SHORT 외에 NAS100의 SPECIAL1~7 LONG 사례도 원문 판단 코드와 비교했습니다.

### 공통 상태와 표시 정책

ALLZONE 엔진은 전략별로 복제하지 않았습니다. Part1의 종목별·프로필별 상태를 공유하고, 같은 종목의 여섯 LIVE 프로필이 하나의 STAFF 캐시를 공유하도록 수정했습니다. 캐시 TTL 내에서 다른 프로필이 새 publication을 먼저 보는 오류를 방지합니다. watch 생성 전의 base 상태 추적도 유지합니다.

Percentile PRICE/RSI/STO/DI와 HMA는 각 TF의 native bar-buffer 관측값을 소비합니다. forming 값을 확정봉 값으로 치환하지 않으며, tick을 Percentile 표본으로 사용하지 않습니다. native 지표를 raw tick에서 새로 산출하는 기능은 이번 결과에 없습니다.

실시간은 Part1 후보가 보유한 `event_time`을 사용합니다. 전달 재시도 시각으로 바꾸지 않습니다.
봉마감은 같은 신호의 실제 `source_tf` 봉마감으로 표시 시각만 이동합니다. 예를 들어 2m 신호가 10:42:30에 발생하면 10:44:00에 표시합니다. 조건이나 state를 재평가하지 않습니다.

서로 다른 TF는 봉마감 표시 순서가 원래 신호 순서와 달라질 수 있습니다. 비교기는 원래 신호 순서와 봉마감 표시 순서를 각각 검증합니다. 전달이 마감보다 늦으면 실제 전달 가능 시점까지 표시를 보류하고 원래 signal timestamp를 보존합니다.

### 다음 DuckDB 단계에 넘길 범위

위 표의 고유 setup, watch/candidate, 무효화·만료·소비, freshness, 전달·receipt는 runtime 상태입니다.
1m ALLZONE의 중간/상위 TF는 3m/6m, 2m은 6m/12m, 3m은 10m/20m입니다. 따라서 1m/2m/3m ALLZONE만을 이유로 그 TF의 Percentile/HMA만 따로 떼어 선계산 대상으로 확정할 수 없습니다. native forming 관측과 상태 전이를 보존하는지 먼저 비교해야 합니다. 이번 작업에서 schema나 선계산 방식을 정하지 않았습니다.

## 실행 검증

- 수정 전 이번 실행의 baseline: **58 통과 / 3 건너뜀**.
- SPECIAL2 단계: SPECIAL1 포함 **52 통과 / 2 건너뜀**, 추가 2m 양방향 **2 통과**.
- SPECIAL3 단계: SPECIAL1~3 **64 통과 / 2 건너뜀**, 반대 교차·재시도 추가 **2 통과**.
- SPECIAL4 단계: 누적 양성·SPECIAL4 회귀 **24 통과**.
- SPECIAL5 단계: 공유 캐시 수정 후 누적 양성·SPECIAL5 회귀 **32 통과**.
- SPECIAL6 단계: **36 통과 / 1 실패**. 실패는 stale MA에 대해 기존 watch 취소를 기대한 fixture 오류로, Part1은 watch를 유지합니다. 기대값 수정 후 해당 테스트 **통과**.
- SPECIAL7 단계: SPECIAL1~7 누적 양성·SPECIAL7·수정한 stale 테스트 **40 통과**.
- 동시 등록/종목 분리: **9 통과**. SPECIAL5·7 trace를 일곱 전략이 함께 등록된 원문 판단 코드와 비교했고, NAS100 일곱 전략도 비교했습니다.
- 전체 회귀 `tests/live_parity tests/special2_7`: **138 통과 / 3 건너뜀**.
- 그 실행 중 발견해 수정한 TF별 봉마감 비교기: 새 테스트 **6 통과**, 기존 비교기 테스트 **2개 재실행 통과**. 새 비교기 테스트를 제외한 위 전체 실행은 141개 수집본입니다. 중복을 제외한 전체 결과는 **144 통과 / 3 건너뜀**입니다.
- Part2 독립 복사본: 기존 SPECIAL1 CLI 회귀 통과. Part1·DataManager가 없는 별도 Part2 복사본에서 SPECIAL2~7 각각 SHORT 합성 trace를 생성하고 봉마감 재생해 **전략별 Alert 1건**을 확인했습니다.
- Part2 전체 Python 구문 검사: **278개 오류 없음**. DataManager 독립 module import 통과. GUI 실제 창·Windows MT5 실행 성공으로 해석하지 않습니다.

서로 다른 단계의 테스트 수는 중복되므로 합산하지 않습니다. 원문 비교는 이번 ZIP의 Part1 판단 정의를 별도 실행하되 clock/STAFF/전달 adapter는 공유한 합성 비교입니다. 실제 프로세스 전체를 독립 실행한 LIVE 검증은 아닙니다.

### 비어 있지 않은 기본 합성 사례

2026-08-03 KST 기준입니다. 각 행의 기본 LONG 사례와 기본 SHORT 사례에서 **각각 Part1 원문 판단 1건 = Part2 1건**이며, direction·session·strategy/source branch·event_time·event order를 검사했습니다. 다른 TF/순서/차단 사례는 위 회귀에 별도로 포함됩니다.

| 전략 | LONG event_time | SHORT event_time | 세션 | 판정 |
|---|---|---|---|---|
| SPECIAL1 | 10:41:30 | 22:17:30 | MAIN_ASIA / MAIN_NEWYORK | SYNTHETIC_PASS |
| SPECIAL2 | 10:41:30 | 22:17:30 | MAIN_ASIA / MAIN_NEWYORK | SYNTHETIC_PASS |
| SPECIAL3 | 10:40:12.100 | 22:40:12.100 | MAIN_ASIA / MAIN_NEWYORK | SYNTHETIC_PASS |
| SPECIAL4 | 10:30:06.100 | 22:30:06.100 | MAIN_ASIA / MAIN_NEWYORK | SYNTHETIC_PASS |
| SPECIAL5 | 10:45:32.100 | 22:45:32.100 | MAIN_ASIA / MAIN_NEWYORK | SYNTHETIC_PASS |
| SPECIAL6 | 11:01:30 | 22:01:30 | MAIN_ASIA / MAIN_NEWYORK | SYNTHETIC_PASS |
| SPECIAL7 | 10:41:30 | 22:41:30 | MAIN_ASIA / MAIN_NEWYORK | SYNTHETIC_PASS |

실제 시장/LIVE 데이터의 Alert count와 timestamp는 **미측정**입니다. 0건 일치로 처리하지 않았습니다.

## 실패·미검증 항목

| 대상 | 발생 문제와 원인 | 수정·최종 결과 |
|---|---|---|
| 실행 환경 | 초기 긴 foreground 명령이 도구 실행 제한에 걸림 | 동일 테스트를 별도 프로세스로 실행하고 종료 결과까지 회수 |
| SPECIAL3 | 존재하지 않는 미사용 `parse_ma_expression` import | 불필요한 import 제거, 이후 회귀 통과 |
| SPECIAL3 | EMA 교차 fixture의 확정봉 경계를 잘못 맞춤 | native fixture 경계만 수정, 원문 전략 조건은 변경하지 않음 |
| SPECIAL3 | FVG chain이 요구하는 `_local_chain_epoch` adapter 정의 누락 | 현재 Part1의 해당 메서드 연결, 양방향 회귀 통과 |
| SPECIAL4 | 기존 `normalize_watch_contract` 연결 누락 | 원문 helper import 연결, 등록·회귀 통과 |
| SPECIAL4 | clock module과 clock.time 객체 binding 혼동 | 기존 replay clock.time binding으로 수정, 양방향·만료·재시도 통과 |
| SPECIAL6 | stale MA이면 이미 armed된 watch도 취소된다는 잘못된 테스트 기대 | Part1의 실제 유지 동작으로 기대값 수정. 제품 코드를 과거 기대값에 맞춰 되돌리지 않음 |
| 공통 봉마감 비교기 | TF별 마감 정책 확장 후 정상적인 표시 순서 변경을 신호 순서 오류로 판정 | 두 순서를 구분해 검증. 새로운 6개 및 기존 2개 테스트 통과 |

최종 실행에서 미해결 테스트 실패는 없습니다. 다음 범위는 통과로 표시하지 않습니다.

실제 LIVE Alert 원장, MT5 raw tick/native publication capture가 기준 ZIP에 없어 실제 LIVE parity를 측정하지 못했습니다. 현재 입력은 native snapshot과 명시적인 poll/command 순서 기록입니다. raw tick → native 지표/STAFF publication 생성은 미구현이며, 각 poll 내부의 프로세스 교차 실행과 실제 Telegram/network 전달은 미검증입니다.

SPECIAL2~7은 한 재생 실행 안의 상태를 유지합니다. SPECIAL2~7 전체의 재시작 checkpoint 가져오기는 아직 지원하지 않아 명시적으로 거부합니다. 기존 SPECIAL1 재생 continuation은 회귀 확인했습니다. 일반 manual WATCH 전체의 재현 완료본도 아닙니다.

Windows/MT5, 실제 GUI 창, DuckDB OFF/ON 비교의 세 gate는 건너뛰었습니다. DuckDB는 이번 범위에서 의도적으로 비활성 상태를 유지했습니다. 일반/기존 백테스터의 전체 테스트를 이번 LIVE 판단의 정답으로 삼거나 전부 실행한 것은 아닙니다.

## 실행 예

```text
cd Part2
python -B -m live_replay example --special 7 --direction LONG --output special7_synthetic.jsonl
python -B -m live_replay run special7_synthetic.jsonl --mode 실시간 --output special7_realtime.json
python -B -m live_replay run special7_synthetic.jsonl --mode 봉마감 --output special7_close.json
```

`example`은 합성 검증용이며 과거 시장 데이터를 내려받지 않습니다. SPECIAL2~7은 `--special 2`부터 `--special 7`까지 선택합니다. 여러 전략을 함께 재생할 때는 관측기록 HEADER의 `scope="SPECIALS"`, `specials=[1,2,3,4,5,6,7]`을 사용하고, 해당 전략의 실제 관측·poll 순서를 제공해야 합니다.

## 실제 수정 파일

전략별 ALLZONE 계산기를 새로 만들지 않았습니다. 기존 공통 reference/adapter에 필요한 원문 정의와 경로를 연결했습니다. `source_map.json`은 기존 무결성 해시를 갱신한 파일이며 새 호출 덤프가 아닙니다.

- 수정: `Part2/LIVE_REPLAY.pyw`
- 수정: `Part2/live_replay/__main__.py`
- 수정: `Part2/live_replay/checkpoint.py`
- 수정: `Part2/live_replay/compare.py`
- 수정: `Part2/live_replay/gui.py`
- 수정: `Part2/live_replay/reference/composer.py`
- 추가: `Part2/live_replay/reference/fvg.py`
- 수정: `Part2/live_replay/reference/oz.py`
- 수정: `Part2/live_replay/reference/settings.json`
- 수정: `Part2/live_replay/reference/source_map.json`
- 추가: `Part2/live_replay/reference/special3.py`
- 추가: `Part2/live_replay/reference/special4.py`
- 추가: `Part2/live_replay/reference/special5.py`
- 추가: `Part2/live_replay/reference/special6.py`
- 추가: `Part2/live_replay/reference/special7.py`
- 수정: `Part2/live_replay/reference/watch_primitives.py`
- 수정: `Part2/live_replay/runtime.py`
- 수정: `Part2/live_replay/synthetic_specials.py`
- 수정: `tests/special2_7/oracle.py`
- 추가: `tests/special2_7/test_close_ordering.py`
- 추가: `tests/special2_7/test_combined_replay.py`
- 추가: `tests/special2_7/test_reference_integrity.py`
- 수정: `tests/special2_7/test_special2.py`
- 추가: `tests/special2_7/test_special3.py`
- 추가: `tests/special2_7/test_special4.py`
- 추가: `tests/special2_7/test_special5.py`
- 추가: `tests/special2_7/test_special6.py`
- 추가: `tests/special2_7/test_special7.py`

이 요약 파일 `README_SPECIAL1_7_KO.md` 한 개를 추가했습니다. 기준 ZIP에 이미 있던 과거 보고서·덤프·build 파일은 이번 결과의 증거로 재사용하거나 다시 생성하지 않았으며, 원본 그대로 남겨 두었습니다.
