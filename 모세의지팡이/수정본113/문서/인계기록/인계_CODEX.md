# Codex 인계 메모 (수정본6 기준 → STAFF 책임분리 구현)

## 1. 먼저 읽을 것
1. `AGENTS.md.txt`: 작업 규칙
   - 원본과 이전 수정본은 수정하지 않습니다.
   - Part1에서 Part2/Part3를 호출하지 않습니다.
   - 요청하지 않은 로직은 바꾸지 않습니다.
   - 기존 테스트는 통과시키고, 새 동작에는 회귀 테스트를 추가합니다.
2. `설계_STAFF_책임분리.md`: 구현할 설계입니다. 단계 S0–S8과 각 단계의 게이트가 적혀 있습니다.
3. `수정내역_*.md`: 수정본3–6에서 바뀐 내용입니다.

## 2. 확정된 결정
- 원비는 **MT5 계산값을 기준 원본**으로 씁니다. Python 원비 계산은 삭제합니다. σ는 STAFF가 파일로 MT5에 전달하고, MT5가 `wonbi_sigma` 열에 적용한 값을 담아 보냅니다.
- ATR은 둘로 나눕니다.
  - `ATR14_GENERAL`: `indicator_facts` Fact 등록부의 공용 Fact입니다. 식은 `rma(tr,14)`이며 기존 STAFF `atr_14`와 비트 단위로 같습니다. OZ 외부유동성과 SPECIAL4가 씁니다.
  - `FVG_WILDER_ATR`: `strategy_FVG` 내부 Fact입니다. 공용 등록부에는 등록하지 않고, `_wilder_atr` 별칭을 유지합니다.
- 결과 불변 단계(S1–S6)를 먼저 끝냅니다. 원비 전환(S7)은 별도 단계로 진행합니다.
- 수정본은 패치가 아니라 **완성본 폴더**로 만듭니다(예: `수정본7`).

## 3. 알아두면 시간을 아끼는 것
- `monitor_OZ.py`와 `manager_KIM.py`는 **CRLF와 LF가 섞인 파일**입니다. 줄바꿈을 통째로 정규화하면 무결성 해시와 diff가 모두 깨지므로, 바꾸는 줄만 원래 줄바꿈을 유지하며 고칩니다.
- Part1 소스를 바꾸면 세 가지를 갱신해야 합니다.
  1. `Part1/audit/source_integrity.py`의 해시 체인: `audit/remediation/NN-*/changes.json`에 단위를 등록하지 않으면 `test_source_hashes…`가 실패합니다.
  2. `build/part1_immutable_sha256.json`: 재생성합니다.
  3. `Part2/generic_backtest/watch/engines/source_contract.json`: Part2가 Part1 함수를 포트한 경우 source_sha256을 갱신합니다.
- Part2 `calculations/allzone.py`의 `_CORE_METHODS`는 monitor_OZ 메서드를 복사해 씁니다. monitor_OZ에 메서드를 추가하면 여기에도 추가해야 Part2 test_oz가 통과합니다.
- STAFF `source_health`에는 프로세스마다 새로 만드는 무작위 세션 UUID가 들어 있습니다. 실행 간 비교를 할 때는 `':'` 앞부분을 제외합니다(`Part2/validation_suite/test_oz_fvg_optimization.py` 참고).
- 최적화 전 코드와 비교할 기준 원본은 `Part1/audit/fixtures/legacy_*.py`에 있습니다.
- 전후 비교와 LIVE↔백테스트 3-way 비교의 틀은 `Part2/validation_suite/test_oz_fvg_optimization.py`(240초 합성, SPECIAL7, Watch 8개)입니다. S0 골든과 G3 게이트는 이것을 일반화해서 만들면 됩니다.
- 루트 `tests/sparse_events`의 자정 체크포인트 테스트는 **기존부터 간헐적으로 실패**합니다(수정본5와 6 모두 전체 실행 2번 중 1번). 단독 실행에서는 통과합니다.
- `Part1/program/config.txt`에는 텔레그램과 Gemini 키가 있습니다. 테스트에서 **실제 텔레그램 전송은 절대 하지 않습니다.** Part2 part1_host는 네트워크를 차단한 상태로 실행합니다.
- EA(mq5)를 바꾸는 S6–S8은 MetaEditor 컴파일과 Strategy Tester 실측이 필요합니다.

## 4. 수정본6 기준 회귀 결과 (비교 기준선)
- Part1 audit 178개: 원본 무결성 해시 1건만 기존 실패
- watch_ma_validation 187개 통과
- Part2 validation_suite 660개 통과. tkinter 등 환경 문제로 인한 실패 18건과 오류 21건은 기준선과 같음
- 루트 tests 263–264개 통과
- 성능: 합성 240초 전체 경로 111.2초(수정본5는 221.5초)
