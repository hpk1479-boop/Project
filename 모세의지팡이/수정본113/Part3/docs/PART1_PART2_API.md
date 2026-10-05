# PART1/PART2 실행 연결과 전략 API

## 의존 방향

```text
PART3 생성기 → (원본 읽기) PART1 SPECIAL
PART3 실행 버튼 → PART1 제어 화면
PART3 실행 버튼 → PART2 기본 화면
PART3 backtest.py 별도 프로세스 → 기존 PART2 workflow → 기존 PART1 event engine

PART1 → PART3 호출 없음
PART2 → PART3 호출 없음
```

정확히는 PART3의 연결 프로세스가 PART2 모듈을 import하고 그 프로세스의 로더 함수만 주입합니다. 원본 파일을 패치하거나 PART2 소스에 PART3 import를 추가하지 않습니다. 이미 실행 중인 별도 실전 PART1 프로세스에도 영향이 없습니다.

## 플러그인 계약

```python
def register(manager):
    manager.register_special_bundle(strategies=definitions, timed_chains=())
    api=manager.special_api
    # 필요한 callback/runtime을 api에 등록
```

실제 계약의 기준은 현재 공통 프로젝트 루트의 `Part1/program/event_composer_domain.py`입니다. `reference/`는 과거 복사본입니다.

| 객체/기능 | 읽을 위치 |
|---|---|
| ConditionSpec | 같은 파일의 조건 파서, __post_init__, _condition_status_locked |
| StrategySpec | 일반 조건과 최종 OZ 정의 |
| ConfigTimedChainSpec | 교차/FVG 시간 연결 및 값 검사 |
| register_special_bundle | 정의 등록, 중복 ID 검사 |
| SpecialPluginAPI | 시간/STAFF/watch/OZ callback 전달 |
| 이벤트 시각 | domain_clock.py |
| 상태 메모리/파일 포트 | domain_memory.py |
| 실행 엔진 구성 | event_application.py, event_composition.py |
| 감시 종류·MA 기간 | watch_orchestrator.py |

SPECIAL4/5 원본은 custom runtime의 실사용 예제입니다. 객체 초기화 때 어떤 api를 받아 어떤 handler를 등록하는지 원본 `register`부터 읽으세요. 호출 인자는 기억으로 추측하지 말고 `SpecialPluginAPI`와 사용처를 확인합니다.

## PART2에서 Test 파일이 필요한 이유

현재 원본 로더 `event_application.load_strategy_inputs`와 `event_selection.SPECIAL_DEPENDENCIES`는 기존 SPECIAL1~7을 대상으로 합니다. 생성 파일을 Part2 폴더에 복사하는 것만으로 새로운 전략이 자동 선택되는 구조가 아닙니다.

`backtest.py.initialize(request)`는 이 별도 프로세스에서:

1. 사용자의 실제 Part1/program과 Part2를 모듈 경로로 추가합니다.
2. 선택한 Test 파일명에 공용 계산 capability 의존성을 선언합니다.
3. 원본 로더를 감싸 해당 파일의 실제 `register(manager)`를 가진 모듈만 로드합니다.
4. 원본 SPECIAL1~7을 선택하지 않은 새 시나리오를 만들어 실행합니다.

원본의 OZ 선택 최적화 선언 테이블은 새 파일의 custom callback 범위를 모르므로 이 경로는 **oz_evaluation='all'**을 사용합니다. 정확한 필요조건이 누락되어 계산이 잘리는 것을 피하기 위한 설정입니다. 선택 전략은 Test 하나이며, 모든 원본 SPECIAL을 추가로 실행한다는 뜻이 아닙니다.

## Windows multiprocessing

기존 runner는 프로세스 풀을 사용합니다. Windows spawn에서는 자식이 진입 스크립트를 다시 로드하므로, `PART3_BT_REQUEST` 환경변수로 로그 폴더의 요청 JSON을 전달합니다. `backtest.py`의 모듈 초기화에서 같은 로더/선택 의존성을 구성한 뒤 runner를 실행합니다. 이 순서를 main 안으로만 옮기면 자식 프로세스에서 새 Test 전략을 모를 수 있습니다.

코드 결과 해시에는 기존 엔진 해시와 생성 Test 파일의 SHA256을 함께 포함합니다. 설정, 입력 시세, 원본 엔진 버전이 달라지면 실행 결과도 달라질 수 있으므로 Test 파일명만으로 결과 동일성을 판단하지 않습니다.

## 실제 실행

GUI에서 연결 경로를 저장하거나 CLI로 설정합니다.

```console
python cli.py connect "D:\Trading" --python "D:\Trading\.venv\Scripts\python.exe" --warehouse "D:\Trading_warehouse"
python cli.py backtest Test_SPECIAL008.py --start 2025-01-01 --end 2025-02-01 --symbol XAUUSD+ --warehouse "D:\Trading_warehouse" --mode TICK --action plan
python cli.py backtest Test_SPECIAL008.py --start 2025-01-01 --end 2025-02-01 --symbol XAUUSD+ --warehouse "D:\Trading_warehouse" --mode TICK --action run --cores 1
```

날짜는 예시이며 종료일 미포함입니다. `plan`은 원본 PART2 데이터 구축 계획, `run`은 원본 `workflow.execute`입니다. run은 사용자가 명시한 실행으로 처리됩니다. GUI에서는 확인 창도 표시합니다.

실행 의존성은 기존 PART2가 사용하는 환경입니다. 특히 데이터 창고/캡처/MT5 경로와 duckdb 등은 기존 프로젝트의 요구사항 파일을 따릅니다. PART3의 기본 GUI 실행을 위해 이를 먼저 설치할 필요는 없습니다.

`logs/<작업ID>/request.json`, `process.log`, 성공 시 `result.json`에 진행/결과가 남습니다. 시세/리포트의 실제 창고 결과는 원본 PART2가 지정한 창고 안에 기록합니다. PART3에는 프로그램에서 작업을 취소하는 버튼을 새로 구현하지 않았습니다. 실행 중 프로세스의 강제 종료는 원본의 기록을 중단할 수 있으므로 완료 여부를 확인하세요.

## 기준 코드와 범위

reference에는 원본 Part1 공용 도메인 코드와 Part2 event_backtest/generic_backtest/data_warehouse/pit/part1_host 관련 Python을 넣었습니다. EA 바이너리, MT5 터미널, 원본 계정 설정, 데이터 창고는 포함하지 않았습니다. 참조본을 그대로 실제 Part1/Part2 위에 덮어쓰지 않습니다.

신규 전략을 실제로 실행할 때는 **연결한 현재 엔진**이 작동합니다. 생성 시 읽은 기준과 실제 실행 엔진이 달라졌으면 관련 API와 시간 동작을 비교해야 합니다. GPT에게 Part3만 전달할 때는 이 reference와 프로젝트 Recipe가 출발점입니다.
