# GPT 작업 시작점 — PART 3 전략연구소

현재 실행 계약은 Part3와 같은 상위 폴더의 Part1·Part2입니다. `reference/`는 과거 복사본으로 최신 기준이 아닙니다.

## 먼저 읽을 순서

1. `AGENTS.md`: 수정 범위와 원본 보존 규칙
2. `docs/SPECIAL_1_TO_7.md`: 어떤 원본 구조를 기반으로 사용할지 결정
3. `docs/AI_INTENT_SCHEMA.md`, `docs/STRATEGY_SCHEMA.md`: AI 의도 및 내부 Recipe 계약
4. `lab/catalog.py`, `lab/ai_compiler.py`, `lab/special_templates.py`, `lab/compiler.py`: 현재 계약·AI 코드 생성·명시적 고유 수명 생성
5. 프로젝트 루트의 `Part1/program/SPECIAL/`(기본 스페셜 레시피의 유일한 원본)과 `Part1/program/strategy_recipe/`: 현재 프리셋 데이터와 공통 실행 계약 확인. `settings/strategy_registry.json`은 SPECIAL 폴더가 없는 옛 프로젝트용 읽기 전용 대체본이며 배포 때 SPECIAL 폴더에서 다시 만들어지므로 고치지 않는다
6. `docs/GPT_HANDOFF.md`, `docs/PART1_PART2_API.md`: 복잡한 확장 및 실행 연결

`../Part1/program/event_composer_domain.py`에 현재 `ConditionSpec`, `StrategySpec`, `ConfigTimedChainSpec`, `SpecialPluginAPI`, `ComposerManager`가 있습니다. `watch_orchestrator.py`, `event_composition.py`, `domain_clock.py`, `domain_memory.py`도 현재 Part1에서 읽으세요.

## GPT가 만들어야 하는 결과

공통 AI 설정에서 사용자가 선택한 모델은 신규 SPECIAL의 자연어를 구조화된 의도로만 바꿉니다. AI는 Python/Recipe 파일을 직접 작성하지 않습니다. 사용자가 해석 결과를 확인하여 전략 화면에 적용하면 Part3 검증·기존 compiler/storage 생성 흐름을 사용합니다. 수동 생성기와 Recipe v1 생성은 제거했습니다. Part3는 JSON 검증·코드 생성만 하며, 자연어는 재판정하지 않습니다.

항상 실제 실행 본문이 있는 `Test_SPECIALXXX.py`를 반환하세요. `pass`, 미구현 함수, 원본 SPECIAL의 import 후 호출만 하는 래퍼, PART3 설치가 있어야만 동작하는 전략 파일로 끝내지 마세요. 실제 PART1 엔진 의존성은 유지합니다.

설정 가능하다는 주장과 동일한 시세에서 동일 신호를 냈다는 검증은 구분하세요. 관측한 테스트 범위만 보고하세요. 정적 원본 읽기, 정의 비교, 함수 점검을 실제 전체 백테스트로 표현하지 마세요.

## 사용자 요청의 의미를 분해할 때

TF/방향/지표 계산식뿐 아니라 **진행봉·확정봉**, **사건 발생·상태 유지**, **A 이후 B·순서무관**, **시간 제한의 시작점**, **새 신호가 이전 신호를 교체하는지**, **취소와 최종 재검사 시점**을 확인하세요. 기존 전략 기반이라면 사용자에게 요구받지 않은 원본 규칙은 유지합니다.

전략 이름에 따른 추측으로 특별 전략을 RSI·MACD·이평 교차 예제로 대체하지 마세요. SPECIAL1~7의 실제 본문이 판단 기준입니다.
