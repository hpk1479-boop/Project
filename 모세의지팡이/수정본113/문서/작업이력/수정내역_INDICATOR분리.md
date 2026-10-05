# 수정본5 — strategy_TREND → strategy_INDICATOR 분리·최적화

기준: 수정본4 복사본. 원본 폴더와 수정본1~4는 건드리지 않았습니다.

**적용 방법**
- 이 폴더에는 이번에 바뀌거나 새로 생긴 파일만 들어 있습니다. 수정본4를 복사한 폴더 위에 같은 경로로 덮어쓰십시오.
- 수정본4에서 아래 3개 파일은 지워야 합니다(Part2의 사용되지 않던 추세점수 사본).
  - `Part2/generic_backtest/fast/trend_features.py`
  - `Part2/generic_backtest/fast/trend_kernels.py`
  - `Part2/generic_backtest/watch/engines/trend_incremental.py`
- `검증결과/`에는 성능 측정과 회귀 비교 결과 JSON을 넣었습니다.

## 1. 파일과 책임

| 파일 | 책임 |
|---|---|
| `Part1/program/strategy_INDICATOR.py` | 정식 실행 프로그램. STAFF·김매니저 통신, Watch 구독 registry, 명령 worker, `IndicatorEngine`(추세 상태, Watch metric, 추세점수 조회) |
| `Part1/program/indicator_facts.py` | 지표 계산식(기존 식 그대로). 지표마다 독립 Fact 계산기. 같은 입력 재사용 캐시(`FactStore`) |
| `Part1/program/indicator_score.py` | 전체 지표 추세점수(기존 30점 앙상블, 조건·가중치·기준 40점 그대로). 요청이 있을 때만 호출 |
| `Part1/program/strategy_TREND.py` | 임시 호환 래퍼. 예전 이름(`TrendEngine` 등)을 새 모듈로 연결만 합니다. 실행하면 INDICATOR가 실행됩니다 |

- OZ_SYSTEM CONTROL은 `strategy_INDICATOR.py`를 실행합니다(표시 이름 INDICATOR).
- 예전 `strategy_TREND.py` 프로세스가 떠 있으면 전체 시작·종료 때 함께 종료합니다. 두 프로그램이 같은 명령을 두 번 처리하는 것을 막기 위해서입니다.
- 김매니저와의 통신 이름은 그대로 둡니다: `TREND_WATCH`, `TREND_STATE`, `TREND_METRIC_STATE`, `TREND_QUERY`, strategy=`TREND`.
- 상태 파일 이름도 그대로입니다(`trend_watch_state.json`, `trend_pending_events.json`, `trend_stream.json`). 재시작하면 기존 Watch를 그대로 이어받습니다. 로그만 `indicator_monitor.log`로 바뀝니다.

## 2. 전략 `추세` (변경된 유일한 전략 조건)

- 상승(UP/LONG): SMA20(시가) 기울기 > 0 **그리고** HMA50 기울기 > 0
- 하락(DOWN/SHORT): 두 기울기가 모두 < 0
- 중립: 두 방향이 다르거나 어느 한쪽이 0
- SMA20(시가) 기울기 = 진행봉 SMA20 − 1봉 전 SMA20 (선택하신 기준)
- HMA50 기울기 = 진행봉 HMA50 − 2봉 전 HMA50 (기존 `hma50_slope`, Hull 색 기준과 같음)
- 이 추세는 SPECIAL1(`TREND@1h~4h`), SPECIAL7(`TREND@15m`), `기본더블비`, Watch의 `상승추세/하락추세일때`에 모두 적용됩니다.
- `추세 알려줘` 응답: `📈 … 추세 · UP` 다음 줄에 `SMA20(시가) 기울기 +x / HMA50 기울기 +y`. 예전의 LONG/SHORT 점수는 표시하지 않습니다.

## 3. 전체 지표 추세점수 — 요청할 때만 계산

- 평상시 LIVE/SPECIAL 추세 상태에서는 추세점수를 계산하지 않습니다. `TREND_STATE`에 score/long_score/short_score가 더 이상 없습니다.
- 계산하는 경우는 두 가지뿐입니다.
  - 조회: `골드 15분 추세점수 몇 점?`, `추세점수 알려줘/조회`
    - 응답: `📊 … 추세점수 · 66.7점 / LONG 16.7 / SHORT 66.7 · 전체 지표 기준`
  - Watch 조건: 추세점수·롱점수·숏점수 조건 (예: `15분 추세점수 40 이상일때 …`)
- 점수 값은 분리 전과 비트 단위로 같습니다.

## 4. on-demand + 증분 계산

- **Fact 단위 계산**: 지표마다 필요한 Fact만 계산합니다. `ADX` Watch는 DMI 하나만 계산하고, Supertrend·VWAP 같은 다른 지표는 계산하지 않습니다.
- **공유**:
  - 한 입력 안에서 각 Fact는 1회만 계산합니다. LONG/SHORT 점수, 여러 metric 필드, 추세 상태가 같은 값을 씁니다.
  - True Range 하나를 DMI·Supertrend·ATR상태·Vortex·Chop이 함께 씁니다.
- **재사용(같은 심볼·TF)**: 이전 입력과 원시 컬럼(time/open/high/low/close/volume/hma_50)을 비교합니다. 바뀐 컬럼에 의존하지 않는 Fact는 그대로 재사용합니다.
  - 시가·HMA50 기반 Fact(추세, SMA20, EMA10/50 등): 새 봉 때만 다시 계산합니다.
  - 종가·고가·저가·거래량 기반 Fact: 해당 값이 바뀔 때만 다시 계산합니다.
- **계산 루프**: MSS와 ATR 추세상태 계산을 pandas 행 접근 대신 NumPy 루프로 바꿨습니다. 결과는 분리 전과 같음을 테스트로 확인했습니다.

## 5. 정식 경로 전환 목록

- Part1:
  - OZ_SYSTEM CONTROL(실행 목록, 구버전 프로세스 정리)
  - watch_ma_features(`indicator_facts`에서 이평 함수 import)
  - manager_KIM(추세점수 조회 명령, 결과 문구, 주석)
  - command_interpreter(`추세점수 몇 점?` 판별)
  - audit harness·테스트, watch_ma_validation, 문서(ARCHITECTURE_BASELINE, INTERPROCESS_CONTRACTS)
- Part2:
  - part1_host(백테스트 SPECIAL 실행기)
  - LIVE_REPLAY reference/runtime
  - WATCH 백테스트 `trend_math.py`/`trend.py`: 추세 상태는 LIVE와 같은 Fact, 점수는 요청 시에만
  - 계약 파일(source/local_contract)
  - 검증 테스트
- 루트 tests: live_parity, special2_7 oracle
- build: `part1_immutable_sha256.json` 재생성, `run_live_tests.py`
- 삭제: Part2에 남아 있던 추세점수 사본과 보조 파일(`trend_incremental.py`, `fast/trend_features.py`, `fast/trend_kernels.py`). 어디에서도 호출되지 않던 중복 계산 코드입니다.
- 그대로 둔 것:
  - `Part1/audit/fixtures`의 원래 기준 manifest·inventory(불변 증거).
  - `AGENTS.md`/`CLAUDE.md`(사용자 규칙 문서).
  - `build/uploaded_source_sha256.json`(원본 업로드 기록).

## 6. 회귀 테스트

- `Part1/audit/test_indicator_split.py` (11개):
  - 정식 경로, 호환 래퍼
  - 분리 전 코드(`audit/fixtures/legacy_strategy_TREND.py`)와의 비교: 12개 무작위 시장(NaN·동률 포함, 1m/15m/1h/4h). 추세점수·판정·전체 metric·지표 함수가 모두 동일합니다.
  - 추세 규칙 진리표
  - 재사용·재계산·공유 횟수
  - 평상시 경로에서 점수 미계산
  - `추세점수 몇 점?` end-to-end
  - 성능
- `Part2/validation_suite/test_indicator_regression.py`: 같은 합성일·같은 STAFF 입력·SPECIAL7·Watch 3개·조회 3개로 두 번 실행합니다.
  - BEFORE: 분리 전 엔진 코드. 추세 판정만 새 규칙의 독립 구현으로 바꾸고, 전체 점수는 예전처럼 매 루프 계산합니다.
  - AFTER: 새 INDICATOR.
  - 비교 항목: SPECIAL 알림, 모든 최종 알림, 모든 텔레그램 문구, 김매니저가 받은 추세·metric 값. 모두 동일합니다.
- `test_part1_host_parity.py`: 새 추세 기준에서는 기존 합성 시드(7)의 15분 추세가 중립이라 SPECIAL7 알림이 없습니다. 그래서 시드를 0으로 바꿨습니다. LIVE와 백테스트 모두 1건(09:03:17 KST, SHORT, 1m)으로 일치합니다.

## 7. 성능 측정

| 측정 | 분리 전 | 수정본5 |
|---|---|---|
| 평상시 추세 루프 12회(3개 봉 × 4틱, 650봉) | 3.56초, 전체 점수 24회 | 0.011초, Fact 12회(시가·SMA20·HMA50·추세 × 3봉) — 약 330배 |
| 추세점수 단발 계산(요청 시) | 0.30초 | 0.052초(약 5.7배), 중복 계산 0회 |
| Part1 전체 파이프라인 합성 240초(SPECIAL7 + Watch 3개 + 조회 3개) | 394~395초, 전체 점수 486회 | 220~225초, 전체 점수 1회(추세점수 Watch 요청분) |

- 평상시 추세 상태는 봉마다 1회만 계산하고, 같은 봉 안의 틱에서는 재사용합니다.
- 자료: `Part1/audit/results/indicator_perf.json`, `Part2/generic_runs/indicator_regression/performance.json`
- 이전 보고의 "시장 1초당 약 1.2초"(TREND 점수가 약 55%) 병목이 이번 작업으로 약 0.9초/초까지 줄었습니다. 남은 시간의 대부분은 monitor_OZ입니다.

## 8. 최종 전체 회귀테스트 (1회)

- Part1 audit 171개: 수정본3 이전부터 실패하던 원본 무결성 해시 1건 외에는 모두 통과했습니다.
- Part1 watch_ma_validation: 187개 통과.
- Part2 전체 suite: 646개 통과. 합성일 parity와 이번 회귀·성능 테스트도 포함입니다.
  - 실패 18건과 오류 21건은 수정본2 기준선과 같은 목록입니다(tkinter 없음, IPC 워커, MT5 시작 화면 등 이 환경 문제).
  - 기준선의 `test_trend_uses_live_function_objects` 실패 1건은 이번에 해결됐습니다.
- 루트 tests: 264개 통과. 실패 3건과 수집 오류 1건은 이 환경에 tkinter와 DataManager 폴더가 없어서 생긴 것이며 기준선과 같습니다.
  - 중간 실행에서 `test_midnight_checkpoint…`가 한 번 실패했습니다. 수정본4와 수정본5를 같은 조합으로 다시 돌렸을 때는 모두 통과했고 최종 실행에서도 통과했습니다. 재현되지 않는 일회성 실패이며 OZ 체크포인트 쪽 테스트라 추세 변경과는 관계없습니다.

## 9. 알려진 사항

- 새 추세 정의 때문에 SPECIAL1·SPECIAL7·추세 Watch의 알림 시점과 횟수는 이전 버전과 달라집니다. 요청하신 정의 변경에 따른 것입니다.
- Watch 문장 `추세점수 40 이상일때`는 기존 파서 동작대로 `TREND(방향일치)` 조건도 함께 붙습니다. 변경하지 않았습니다.
- audit의 원본 무결성 해시 테스트 1건은 수정본3 이전부터 실패하던 항목이며 이번 작업과 무관합니다.
