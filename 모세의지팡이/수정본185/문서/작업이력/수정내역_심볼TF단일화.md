# 수정본35 — 심볼·TF 단일 원본화 1단계 (Python LIVE)

수정본34를 복사했다(검증결과·캐시 제외). 설계서: `STAFF_OF_MOSES_symbol_tf_design.md`.
전제(사용자 결정): 논리 심볼 이름은 그대로(XAUUSD+, NAS100, BTCUSD). 지표·전략·알림 판정·Wire schema는 바꾸지 않았다.
실제 Telegram/MT5 실행은 하지 않았다.

## 변경
- `Part1/program/event_pipe_host.py`
  - 파이프 수신에서 config 심볼 allowlist를 쓰지 않는다.
    대신 형식 규칙(1~64자, 공백·쉼표·제어문자 없음)을 통과한 심볼은 모두 받는다.
    빈 심볼과 UTF-8 오류는 원래대로 Wire 디코더가 거부한다. HELLO/schema/CRC 검증도 그대로다.
  - 수신 레지스트리 `receiver.symbols` = config 힌트(있으면 먼저) + 검증을 통과해 캐시에 들어온 심볼(처음 받은 순서).
    Health·RECONNECT·UNAVAILABLE 보고가 이 목록을 쓴다.
  - 재접속해도 이미 받은 심볼은 목록에 남긴다. EA가 그 심볼을 더 보내지 않으면 Health가 STALE로 알린다.
- `Part1/program/event_host.py`
  - `HostInputs.symbols` = config 힌트 + EA 수신 심볼.
    텔레그램 심볼 해석과, 심볼 없는 명령의 기본 심볼(첫 번째)에 쓴다.
  - 하드코딩 기본값 `XAUUSD+,NAS100,BTCUSD`는 제거했다.
- `Part1/program/event_application.py`
  - `symbols`를 주지 않으면 engine 구독은 wildcard(`()`)다. LIVE가 여기에 해당한다.
  - Part2 재생처럼 심볼을 명시하면 기존과 같다.
- `Part1/program/config.txt`
  - 값은 바꾸지 않았다(사용자 값 유지). "공급 목록은 EA 소유, LIVE 입력 제한 안 함" 주석 2줄만 넣었다.

## 바꾸지 않은 것 (이유)
- `event_composer_domain._allowed_symbols()`: config + SPECIAL + 수동 Watch 심볼을 그대로 쓴다.
  도메인 코드에 호스트 레지스트리를 넣으면 재생 결과가 LIVE 수신 이력에 따라 달라지므로 넣지 않았다.
  명령 심볼은 호스트가 먼저 해석해 COMMAND payload에 싣고, 도메인은 command_aliases.json도 함께 쓴다.
- `THE STAFF OF MOSES.py`의 `DataServer`(폴링판 STAFF 서버)는 이벤트 LIVE 경로가 아니라서 두었다.
  원래도 목록이 비어 있으면 제한하지 않는다.
- `command_interpreter.MT5_TIMEFRAMES`(명령 문법)는 유지했다.
- TF: 이벤트 LIVE 경로에는 원래부터 Python TF 공급 목록이 없다. 캐시에는 EA가 보낸 TF만 있다(시험으로 확인).

## 검증
- `tests/test_symbol_registry.py`(신규) + 관련 기존 시험: **43 PASS** (`검증결과/symbol_registry/tests.xml`).
  - config 세 키가 없어도 동작한다: 파이프 등록, 캐시, engine 수신(임의 심볼 `US30.cash` 포함), 명령 심볼 해석, 기본 심볼, Health 순서.
  - EA가 BTCUSD 1m/5m/1h만 보내면 캐시에는 그 3개만 있다.
  - 형식 위반 심볼(`XAU USD`, `A,B`, 탭, 제어문자)은 거부되고, 캐시·ingress·레지스트리에 남지 않는다.
  - 심볼을 명시한 재생 engine의 구독은 그대로다.
- 기존 시험 1건 수정(의도한 동작 변경): `test_event_e3.py::test_pipe_host_handshake_validation_and_publication`.
  "config에 없는 XAUUSD+ 거부"를 "형식 위반 `XAU USD` 거부"로 바꿨다.
- LIVE = 재생 (`build/parity_symbols35.py`, `검증결과/symbol_registry/parity.json`)
  - LIVE: config 목록 없음, engine wildcard, 수신 레지스트리 빈 상태에서 시작.
  - 재생: 기존 방식.
  - 합성 240초에서 240묶음·5,790신호·18알림이 같고, 신호 해시·전달 결과도 같다.
  - 해시 `d98753…`은 수정본32의 같은 시험(공유 OZ 입력) 결과와 같다. 따라서 **기존 알림 변화 0건**이다.
  - Part2 재생 경로는 심볼을 명시하므로 이번 변경으로 동작이 바뀌지 않는다. 그래서 SPECIAL 1주 재생은 생략했다.

## 무결성
- `Part1/audit/remediation/65-ea-symbol-registry` 등록.
- `build/part1_immutable_sha256.json` 재생성(수정본34와 같은 제외 규칙: logs·event_state·results·__pycache__).
- 변경 4파일 + 등록 2파일. 새 오류 0건, 기존 오류 6건 유지.

## 남은 단계
- 2단계 MT5 EA: 논리/브로커 심볼 분리, `ResolveBrokerSymbol`, 시장 조회를 broker_symbol로. 컴파일은 사용자 PC에서.
- 3단계 Part2: Tester Symbol=브로커 심볼, 결과는 논리 심볼. 고정 목록 제거 대상:
  - `settings.py`, `gui.py`
  - `bridge.py`의 PipeReceiver 시드 튜플(동작 영향 없음)

---

# 2단계 — MT5 EA 브로커 심볼 자동 매핑

## 변경
- `Part1/program/MT5/STAFF_Symbol_Map.mqh` (신규)
  - `StaffPickBrokerSymbol()`: 브로커 심볼 목록만으로 판정하는 순수 규칙. MT5 호출이 없다.
  - `ResolveBrokerSymbol()`: 논리 심볼마다 한 번만 판정하고 결과를 캐시한다.
  - 판정 순서:
    1. 정확히 같은 이름
    2. 별칭 이름: XAUUSD/GOLD, NAS100/US100/USTEC, BTCUSD/XBTUSD
    3. 별칭 + 접두·접미
       - 접두: 구분자로 끝나는 5자 이하 (예: `m.XAUUSD`)
       - 접미: 구분자로 시작하는 6자 이하 (예: `XAUUSD+`, `.pro`) 또는 소문자 1~4자 (예: `XAUUSDm`)
       - 제외 예: `XAUUSDT`, `US1000`, `GOLDEUR`
  - 결과 처리:
    - 후보가 정확히 하나면 그 심볼을 쓴다.
    - 여러 개면 `[SYMBOL MAP AMBIGUOUS] logical=… candidates=…`를 남기고 그 심볼의 feed만 뺀다.
    - 없으면 `[SYMBOL MAP MISSING]`을 남기고 그 심볼의 feed만 뺀다.
    - 성공하면 `[SYMBOL MAP] XAUUSD+ -> XAUUSD+ (exact)`를 남긴다.
- `THE_STAFF_OF_MOSES.mq5`
  - `FeedContext`
    - `symbol`: 논리 심볼. Wire·Python identity이며 의미는 기존과 같다.
    - `broker_symbol`: 신규. MT5 시세 조회에 쓴다.
  - LIVE `BuildFeed`
    - 브로커 심볼을 해석한 뒤 SymbolSelect/iMA/iCustom/CopyRates/SeriesInfoInteger에 브로커 심볼을 쓴다.
    - 논리 심볼 두 개가 같은 브로커 심볼로 연결되면 뒤쪽 논리 심볼의 feed를 빼고 `[SYMBOL MAP CONFLICT]`를 남긴다.
  - Strategy Tester
    - 새 입력 `InpTesterLogicalSymbol`: 비어 있으면 `_Symbol`이며 기존과 같다.
    - Wire·네이티브 export의 symbol과 데이터 구축 요청 대조에는 논리 심볼을 쓴다.
    - 시세 조회는 `_Symbol`을 쓴다.
  - `int OnInit()`부터 파일 끝(LIVE 타이머·송신 루프)은 수정본34와 바이트 동일하다(시험으로 고정).
  - 브로커 심볼이 논리 심볼과 같으면(현재 브로커) 조회하는 대상이 전과 같으므로 Wire 값도 같다.
- `STAFF_Wire_Schema.mqh`: `STAFF_EA_BUILD_HASH`만 새 소스 기준으로 재생성했다(59줄 중 1줄).
- `STAFF_Symbol_Map_Test.mq5` (신규): MT5 스크립트.
  - EA와 같은 mqh로 13가지 경우를 시험한다: exact, suffix(+/./소문자), prefix, 별칭, 별칭 우선, 모호 2종, 없음 2종.
  - 주문·파일·파이프는 쓰지 않는다.
- Part2 Tester 배포 목록에 `STAFF_Symbol_Map.mqh`를 추가했다: `native_mt5.py`, `recording.py`.
  이것이 없으면 Tester 컴파일이 include를 찾지 못한다.

## 검증
- MetaEditor64 컴파일(임시 사본): EA와 시험 스크립트 모두 **0 errors, 0 warnings** (`검증결과/symbol_registry/compile_*.txt`).
- `tests/test_symbol_registry.py` 13건 포함 관련 시험 **53 PASS**.
  - 시세 조회 호출은 모두 broker_symbol/broker만 쓴다.
  - Wire 프레임은 모두 논리 symbol을 쓴다.
  - OnInit~끝은 수정본34와 같다.
  - mqh가 Tester 배포 목록에 있다.
  - `test_ea_hello_build_hash_matches_changed_source`가 통과한다.
- 기존 실패 4건은 이번 작업과 무관하다. 수정본34에서도 같은 이유로 실패한다.
  - `test_event_e1::test_bar_close_fact_ignores_forming_updates_until_new_bar`
  - `Part1/audit/test_baseline.py`의 mt5 3건: harness 설정 단계의 파일 복사 오류(SameFileError)
  - 대신 BuildFeed 계약(지표 handle이 없어도 OHLC feed 유지)은 새 소스에 직접 대조해 확인했다.
- MT5 터미널 실행은 하지 않았다. 매핑 시험 스크립트 실행은 아래 "사용자가 할 일"에 있다.

## 컴파일·설치 (2026-09-29, 사용자 승인 후)
- 수정본35 `Part1/program/MT5/THE_STAFF_OF_MOSES.ex5`를 MetaEditor64로 새로 컴파일했다(0 errors, 0 warnings). 수정본34 빌드를 대체한다.
- 설치 시점에 MT5 터미널과 파이썬 LIVE는 실행 중이 아니었다. 그래서 실행 중인 EA가 재시작되지 않았다.
- 설치 전 백업: MT5 데이터 폴더의 `STAFF_backup_before_수정본35_20260929_141806`
  - 이전 `THE_STAFF_OF_MOSES.mq5/.ex5`, `STAFF_Identity_Status.mqh`, `STAFF_Wire_Schema.mqh`
- `MQL5/Experts`에 설치: `THE_STAFF_OF_MOSES.mq5/.ex5`, `STAFF_Identity_Status.mqh`, `STAFF_Wire_Schema.mqh`, `STAFF_Wire_V2.mqh`, `STAFF_Symbol_Map.mqh`
  - 6개 모두 수정본35와 해시가 같다. `.ex5`는 수정본35 빌드를 그대로 복사했으므로 Part2 빌드 identity가 LIVE 설치본과 같다.
  - 이전 설치본에는 `STAFF_Wire_V2.mqh`가 없었고 `STAFF_Identity_Status.mqh`는 09-25 판이었다. 둘 다 이번에 수정본35 판으로 맞췄다.
- `MQL5/Scripts`에 `STAFF_Symbol_Map_Test.mq5`와 `STAFF_Symbol_Map.mqh`를 설치하고 컴파일했다(0 errors, 0 warnings).

## LIVE EA 로드 확인 (2026-09-29 14:20, 사용자가 MT5 실행)
- 새 EA 로드: `[SYMBOL MAP] XAUUSD+ -> XAUUSD+ (exact)`, `[SYMBOL MAP] NAS100 -> NAS100 (exact)`.
- feeds=38로 이전과 같다(해당 차트의 InpSymbols=XAUUSD+,NAS100 × 19TF).
- 증거: `검증결과/symbol_registry/live_ea_symbol_map_log.txt`
- `Named Pipe open 실패 err=5022`는 파이썬 LIVE가 꺼져 있을 때 나오는 연결 대기 메시지다.
## MT5 매핑 규칙 시험 (2026-09-29 14:24, 사용자 허락 후 MT5 재시작)
- MT5를 정상 종료했다(CloseMainWindow). 시작 설정 `[StartUp] Script=STAFF_Symbol_Map_Test`로 다시 켜서 스크립트를 실행했다.
- 결과: 13개 경우 모두 PASS, `[SYMBOL MAP TEST] ALL PASS - fail=0`
  - exact, 접미 `+`/`.pro`/소문자, 접두, 별칭, 별칭 우선, 모호 2종, 없음 2종
  - 증거: `검증결과/symbol_registry/symbol_map_test_mt5.txt`
- 재시작 후 LIVE EA가 차트(XAUUSD+, M10)에 자동으로 다시 로드되었다(14:24:27 loaded successfully).
  파이썬 LIVE 파이프 연결을 기다리는 상태다.

## 되돌리기
위 백업 폴더의 파일을 `MQL5/Experts`에 다시 복사한다.

## 주의 — Part2 녹화 데이터 호환
- Part2는 EA 빌드(.ex5 해시)로 저장된 녹화 조각을 고른다. 새로 컴파일하면 해시가 바뀐다.
  `Part2/ea_build_compatibility.json`에 승인 그룹을 추가하기 전에는 기존 녹화 데이터를 백테스트 후보로 쓰지 않고 다시 녹화하자고 제안한다.
- 브로커 심볼이 논리 심볼과 같으면 Wire 값은 같아야 한다. 하지만 이 목록은 "증거로만 승인"하는 규칙이라 추측으로 추가하지 않았다.
- 3단계(또는 별도 작업)에서 할 일: 새 빌드로 하루를 녹화해 기존 녹화와 비교한다. 같으면 호환 그룹에 추가한다.

---

# 3단계 — Part2 (Tester는 브로커 심볼, 결과는 논리 심볼)

## 변경
- `Part2/event_backtest/settings.py`
  - 고정 목록(`XAUUSD+/NAS100/BTCUSD`) 검사를 없애고 LIVE 파이프와 같은 형식 규칙만 둔다(1~64자, 공백·쉼표·제어문자 없음).
  - 컴퓨터별 설정 `Part2/event_backtest.json`에 `broker_symbols`를 추가했다({논리: 이 PC 브로커 Tester 심볼}). 없으면 같은 이름이다.
    - 브로커가 바뀌면 여기에 한 줄 넣는다. 예: `"broker_symbols": {"XAUUSD+": "GOLD.pro"}`
    - Python은 MT5가 어느 서버 기준으로 Tester를 돌릴지 알 수 없다. 그래서 EA 규칙을 Python으로 복제하지 않았다.
  - `tester_symbol()`, `stored_symbols()`(창고 captures의 논리 심볼 목록, 읽기 전용)를 추가했다.
- `gui.py`: 종목 칸은 창고에 있는 심볼 목록을 보여 주고, 직접 입력도 된다. 고정 목록은 없앴다.
- `bridge.py`: PipeReceiver 고정 시드 튜플을 없앴다.
- `generic_backtest/native_mt5.py`: `run_native_tester(..., logical_symbol=)`
  - 테스터 설정의 `Symbol=`에는 브로커 심볼을 넣는다.
  - EA 데이터 구축 요청 파일에는 논리 심볼을 넣는다(EA는 `InpTesterLogicalSymbol`과 대조).
- `event_backtest/recording.py`
  - 녹화 시 `Symbol=`에는 브로커 심볼, `InpTesterLogicalSymbol`에는 논리 심볼을 넘긴다.
  - capture identity·창고 경로·결과는 논리 심볼을 쓴다.
  - 테스터 저널(누락 봉 확인)은 브로커 심볼로 찾는다. capture 메타데이터에 `tester_symbol`을 기록한다.

## 녹화 호환 승인 (수정본30과 같은 방법)
- 새 EA 빌드 `905946ca…`로 XAUUSD+ 2026-09-01 BAR 하루를 별도 창고 `검증결과/symbol_registry/day_probe/`에 녹화했다.
  - 앞의 `prepare()`를 그대로 썼다. Tester 약 21초. MT5는 자동으로 닫혔다가 다시 열렸다.
  - 설치된 EA는 새 빌드 그대로 복원되었다. 요청 파일은 남지 않았다.
- 결과: 묶음 **1,378개**, `bundle_sha256=d9c2868d…`
  - 기존 승인 빌드(`af30fe4b…`, `e434827f…`)의 같은 날 녹화와 **완전히 같다**. 복원 검증 통과, 누락 봉 0, `tester_symbol=XAUUSD+`.
  - 증거: `day_comparison.json`, `day_probe.log`, `day_probe_run.py`
- `Part2/ea_build_compatibility.json`의 기존 그룹에 `905946ca…`를 추가하고 증거를 적었다.
- 실제 창고 계획 확인(녹화 없이 계획만): 현 빌드 `905946ca` 기준 신규 녹화 0개.
  - 2026-09-01 하루: 기존 조각 1개 재사용
  - 2026-09-01~08: 기존 조각 7개 재사용
  - 증거: `plan_check.txt`
- `tests/test_ea_build_compatibility.py`의 승인 목록 고정 단언을 3개 해시로 갱신했다(의도한 변경).

## 검증 (3단계)
- `tests/test_symbol_registry.py` Part2 시험 5건 추가
  - 형식만 맞으면 어떤 논리 심볼이든 받는다
  - `broker_symbols` 매핑
  - Tester `Symbol=GOLD.pro` / 요청 파일은 `XAUUSD+` / `InpTesterLogicalSymbol=XAUUSD+`
  - recording과 bridge·gui에 고정 목록 없음
  - 창고 심볼 목록
- 관련 시험 **79 PASS** (`검증결과/symbol_registry/tests_stage3.xml`)
  - registry, 호환, Part2 실행기, 데이터 선택, E3 호스트, 재연결
- 기존 실패 1건(수정본34에서도 같은 위치에서 StopIteration):
  `test_ui_slots.py::test_gui_modes_dates_and_disabled_watch_time`
- `test_data_selection.py::test_build_replace_only_after_verification_and_restore`는 긴 임시 폴더에서 Windows 경로 길이 제한으로 실패했다. 짧은 임시 폴더에서는 35·34 모두 통과한다. 코드 문제가 아니다.

## 무결성 (2단계)
- `Part1/audit/remediation/66-ea-broker-symbol-map` 등록(EA, Wire_Schema, 신규 mqh·시험 스크립트).
- `build/part1_immutable_sha256.json` 재생성. 새 오류 0건, 기존 오류 6건 유지.

## 수정 파일
- 2단계
  - `Part1/program/MT5/THE_STAFF_OF_MOSES.mq5`, `STAFF_Wire_Schema.mqh`, `STAFF_Symbol_Map.mqh`(신규), `STAFF_Symbol_Map_Test.mq5`(신규)
  - `Part1/audit/remediation/66-ea-broker-symbol-map/`
  - `Part2/generic_backtest/native_mt5.py`, `Part2/event_backtest/recording.py`
  - `build/patch_ea_symbol_map.py`, `build/split_symbol_map_mqh.py`, `build/register_ea_symbol_map35.py`
- 1단계
  - `Part1/program/event_pipe_host.py`, `event_host.py`, `event_application.py`, `config.txt`
- `Part1/audit/remediation/65-ea-symbol-registry/`
- `build/part1_immutable_sha256.json`
- `build/patch_symbol_registry.py`, `build/parity_symbols35.py`, `build/register_symbol_registry35.py`
- `tests/test_symbol_registry.py`, `tests/test_event_e3.py`
