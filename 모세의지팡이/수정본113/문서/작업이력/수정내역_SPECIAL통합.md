# 수정본4 — SPECIAL LIVE/백테스트 통합

기준 폴더: 수정본3 (Part1) + 수정본2 (Part2). 원본 폴더는 건드리지 않았습니다.

## 1. OZ 트리거 5번 롤백 (Part1)

- 5번 조건을 수정본3의 "HMA6 시가 기울기"에서 원래의 **HMA6 색 꺾임**으로 되돌렸습니다.
  - 판정 함수 `_hma6_turn_pullback_trigger`는 원본 파일과 AST 기준 동일합니다.
  - 색 기준은 2개봉 전 비교입니다: `hull > hull[2]`이면 상승색입니다.
  - 매수: 직전 마감봉 상승색 → 최근 마감봉 하락색. 매도: 반대.
- 누적 규칙은 그대로입니다: B0 터치는 즉시 통과, 그 외는 HMA17 / 원비 / 캔들 / HMA6 색 꺾임 중 서로 다른 2개 이상.
- 알림 표시: `HMA6 하방꺾임`, `HMA6 상방꺾임`.
- `수정내역_OZ트리거.md`도 같은 내용으로 고쳤습니다.

## 2. Part1 LIVE SPECIAL = 유일한 기준 원본

- Part1의 SPECIAL·manager_KIM·monitor_OZ·TREND/FVG/SWEEP 전략 로직은 바꾸지 않았습니다.
- Part2에 있던 SPECIAL 사본을 모두 지웠습니다.
  - `BACKTEST_SPECIAL/BACKTEST_SPECIAL1~7.py` (WATCH_UI_V1은 유지)
  - `generic_backtest/special_builtin.py`, `special_runtime.py`, `special_oz_runtime.py`
  - `live_replay/reference/`의 Part1 코드 사본 전부 (`__init__.py`, `settings.json`만 남음)
  - `build/rebuild_live_reference.py` (사본 생성기)
  - 사본 전용 테스트: `conditional_validation/test_restoration.py`, `test_intrabar.py`, `benchmark_original.py`, `test_worker_smoke.py`, `tests/live_parity/test_legacy_special1_corrections.py`
- LIVE_REPLAY(`live_replay`)도 이제 `Part1/program`의 현재 파일을 직접 불러옵니다(`part1_host/loader.py`).
  - 시계·저장소·네트워크만 재생용으로 바꿉니다.
  - Part1 폴더는 임시 폴더로 복사한 뒤 실행하므로 LIVE 로그/상태 파일에 쓰지 않습니다.

## 3. 같은 SPECIAL 함수 + 같은 트리거 입력

- 백테스트 엔진 `Part2/part1_host`가 Part1 프로그램을 수정 없이 실행합니다.
  - 실행 대상: THE STAFF OF MOSES → manager_KIM → strategy_TREND/FVG/SWEEP → monitor_OZ → SPECIAL.
  - 실제 `run()` 루프 본문을 가상 시계로 돌립니다. 주기는 Part1 코드의 값 그대로입니다: manager 0.25초, 엔진 0.5초/0.25초, 명령 작업자 0.2초.
  - 시작 전에 Part1 원본 파일의 SHA256을 확인합니다.
  - 텔레그램·Gemini 키는 지우고 네트워크는 막습니다. 텔레그램은 실제로 보내지 않고 기록만 합니다.
- 트리거 입력은 LIVE와 같은 `Part1/special_settings.json`(OZ_SYSTEM CONTROL [전략 설정] 슬롯)입니다. 슬롯이 비어 있으면 SPECIAL 코드 기본값을 씁니다.
- Part2의 일반 OZ 백테스트 엔진(`calculations/allzone.py`)도 Part1 LIVE OZ 메서드를 가져다 씁니다. 수정본3에서 추가된 프로필/누적 트리거 메서드와 상수를 가져오는 목록에 추가했습니다.

## 4. 입력 규격 통일 (OZ/FVG/TREND/SWEEP)

- LIVE: MT5 EA가 매초 feed마다 45열 STAFF 파이프 메시지를 보냅니다.
- 백테스트: 같은 EA가 Strategy Tester에서 **같은 함수(`BuildStaffPayload`)로 만든 같은 45열 메시지**를 파일로 기록합니다(`STAFF_PIPE_V1`).
  - 새 봉·첫 기록·열 상태 변화 때는 FULL(650봉 전체), 그 외는 형성 중인 봉 1줄(ROW)만 기록합니다.
  - 백테스트는 이를 LIVE와 바이트 단위로 같은 파이프 메시지로 다시 만들고, LIVE EA처럼 매초 모든 feed를 다시 보냅니다.
  - 그래서 OZ·FVG·TREND·SWEEP 모두 LIVE와 똑같은 STAFF 입력을 받습니다.
- `THE_STAFF_OF_MOSES.mq5`를 고쳤습니다. 이 환경에서는 MQL5 컴파일을 할 수 없어 컴파일 검증은 하지 못했습니다.
  - BACKTEST CONTROL(MT5 모드)은 기존처럼 Part1의 .mq5를 MT5 폴더로 복사한 뒤 MetaEditor로 자동 컴파일합니다. 컴파일 오류가 나면 그 단계에서 멈춥니다.
  - LIVE용 EA는 전송 내용이 바뀌지 않았습니다(같은 함수를 둘로 나눴을 뿐). 그래도 같은 소스를 쓰도록 MetaEditor에서 한 번 다시 컴파일하는 것을 권장합니다.
- 기록 용량은 하루 약 1.6GB(추정)입니다.

## 5. BACKTEST CONTROL 화면

- 전략 목록 맨 위에 다음 항목이 추가됩니다.
  - `Part1 SPECIAL 전체 · 전략 설정 기준`: 전략 설정에서 켜진 SPECIAL 전부.
  - `SPECIALn · Part1 LIVE`: SPECIAL 하나씩.
- Part1 SPECIAL은 MT5 모드에서만 실행됩니다. 흐름은 다음과 같습니다.
  1. MT5 Strategy Tester가 45열 기록을 만듭니다.
  2. `special-run`이 Part1 코드로 재생합니다.
  3. 결과 창에 알림 시각(KST), 전략, 방향, 주기, 검증/트리거가 표시됩니다.
- 로그에 SPECIAL별 트리거 슬롯 상태가 표시됩니다(`SPECIAL7=무지성 올존 (전략 설정)` / `코드 기본값`).
- 결과 파일: `special_result.json`, `special_alerts.csv`.

## 6. LIVE ↔ 백테스트 parity 테스트

- 파일: `Part2/validation_suite/test_part1_host_parity.py`
- 조건: 합성 시장 하루(2026-09-22 09:00 KST 시작), SPECIAL7, 트리거 슬롯 `무지성 올존`.
- LIVE 경로는 매초 45열 메시지를 바로 보냅니다. 백테스트 경로는 EA 기록 파일을 거쳐 BACKTEST CONTROL과 같은 엔진으로 실행합니다.
- 결과: 알림 **1건 = 1건**, 시각(09:03:05 KST)·방향(SHORT)·주기(1m)·spec 모두 일치합니다.
- 함께 확인하는 항목:
  - 기록에서 다시 만든 메시지가 LIVE 메시지와 바이트 단위로 같음.
  - Part1 원본 해시가 같음.
  - 서비스 주기가 Part1 코드 값과 같음.
- 기본 창은 4분입니다. `PART1_HOST_PARITY_FULL=1`로 설정하면 12분 창으로 실행합니다.

## 7. 알려진 제약

- 속도: Part1 루프를 LIVE 주기 그대로 돌리므로 시장 1초당 약 1.2초가 걸립니다(하루 ≈ 28시간). TREND 점수 계산이 약 55%, OZ가 약 40%입니다. 최적화는 별도 지시 때 진행합니다.
- 합성 데이터 parity만 검증했습니다. 실제 날짜 검증은 컴파일된 EA로 Strategy Tester 기록을 만든 뒤 가능합니다.
- ALLZONE 이벤트 카탈로그의 식별자는 기존 결과와 호환되도록 그대로 두었습니다(해시만 Part1 monitor_OZ 기준).

## 8. 테스트

- Part1 audit: 수정본3 이전부터 있던 무결성 해시 1건 외에는 모두 통과합니다. MQL 계약 테스트는 새 `BuildStaffPayload` 기준으로 고쳤습니다.
- Part2 (validation_suite, watch_ma_validation, conditional_validation, cadence_input_validation): 수정본2 기준선과 실패 목록이 같습니다(tkinter 없음, IPC 워커, MT5 시작 화면 등 환경 문제). 새로 생긴 실패는 없습니다.
  - 수정본3 Part1과 함께 돌리면 깨지던 Part2 일반 OZ 테스트(`test_oz.py`, `cadence_input_validation` OZ 전이)는 이번에 통과하도록 고쳤습니다.
  - `test_oz.py`의 기대 트리거는 수정본3 규칙에 맞춰 `HMA17` → `HMA17, WONBI`(2개 누적)로 바꿨습니다.
- 루트 tests: 263 통과. 실패 3건과 수집 오류 1건은 모두 이 환경에 tkinter와 DataManager 폴더가 없어서 생긴 것입니다(기준선과 같음). 수정본2 기준선에서 실패하던 SPECIAL 재생 테스트는 Part1을 직접 실행하면서 통과합니다(사본 전용 테스트는 삭제).
- `build/part1_immutable_sha256.json`을 수정본4 Part1 기준으로 다시 만들었습니다. `.ex5`는 재컴파일 대상이라 비교에서 제외합니다.
