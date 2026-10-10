# 폴링 LIVE ↔ 이벤트판 시작 경로 대조 (E2)

기준 소스는 수정본16 Part1 전체이고 구현은 수정본17이다. 아래 `program`은 선택한 읽기 원본의 `Part1/program`, `logs`는 그 하위 `logs`다. 원본 파일의 값은 문서에 복사하지 않는다. 사용자 config와 BTCUSD 설정은 그대로 읽는다.

## 시작 순서

SYSTEM CONTROL은 STAFF → 김매니저 → INDICATOR → FVG → SWEEP → OZ 순으로 프로세스를 시작한다. STAFF 뒤 2초, 김매니저 뒤 1초 지연이 있지만 프로세스 내부 초기화의 완료 순서까지 보장하지는 않는다. 각 프로세스의 초기화와 시작 PING이 의존 관계를 만든다.

이벤트판은 `event_startup.create_live_event_engine(program)`을 명시적으로 호출할 때만 시작 입력을 읽는다. config → SPECIAL 선택/트리거 → 별칭 → 저장 상태/완성된 SWEEP 이력 → 모듈 및 처리기 구성 순서다. 처리기 상태는 최초 입력 처리 시 canonical 복원 함수를 한 번 실행한다. SWEEP 복원 → FVG 복원 → OZ 외부 상태/수동 감시 복원 및 SWEEP 이력 적용 → Consumer 복원 순서다. Composer는 개인 감시 → 개인 시간연쇄 → FVG 생성 감시 및 구버전 이관 → 활성 OZ 자식 → SPECIAL 등록/공식 연쇄 및 SPECIAL4 복원 순서를 그대로 사용한다. 이후 기존 PING 처리 함수로 오래된 구독을 정리하고 현재 감시를 다시 전달한다. 시장 판단은 모든 TF를 Board에 commit한 뒤 수행한다.

종목별 Composer는 해당 종목의 저장 감시와 상태만 소유한다. 저장된 다른 종목 구독을 고아로 판단해 취소하거나 다른 종목 시계로 SPECIAL 런타임을 만료시키지 않는다. 공유 처리기의 구독은 전체 종목을 보관한다.

## 입력 파일 및 연결

| 폴링 입력 | 원래 읽는 위치/순서 | 이벤트 연결 |
|---|---|---|
| `program/config.txt` | 각 서비스 load_config, utf-8-sig KEY=VALUE | canonical manager load_config 결과를 모든 어댑터에 명시 전달 |
| `Part1/special_settings.json` | SYSTEM CONTROL: enabled, trigger. 누락/잘못된 형식은 전체 활성/코드 기본값 | 동일 fallback, enabled_specials/trigger_overrides 전달 |
| `program/SPECIAL/SPECIAL1.py`~`SPECIAL7.py` 및 `oz_profiles.py` | 트리거 기본값, 등록/판정 코드 | 같은 소스 로드. 전용 profile namespace로 폴링 전역 설정 변경 금지 |
| config `COMMAND_ALIASES_FILE` (기본 `program/command_aliases.json`) | Composer/CommandInterpreter 초기화. 상대 경로 program 기준 | 같은 parser/경로 규칙으로 로드. 실행 중 파일 감시는 없음(설계의 시작 CONFIG 고정) |
| `logs/notification_deliveries.json` | NotificationService Records, 송신 이력 확인 | 같은 NotificationService + 메모리 Records + 전송 대역. delivered/unknown 상태 의미 유지 |
| `logs/event_receipts.json` | Composer 입력 중복 확인 | canonical Records, 메모리 저장소 |
| `logs/fact_revisions.json` | Composer 이전 Fact revision 확인 | canonical Records, 메모리 저장소 |
| `logs/composer_signatures.json` | Composer 생성 중 마지막 판정 signature 복원 | canonical 복원, 메모리 저장소 |
| `logs/composer_private_watches.json` | 개인 전략 및 Telegram 답글/감시 연결 정보 | `_load_private_state` 재사용 |
| `logs/composer_timed_chains.json` | WatchOrchestrator 개인 연쇄 상태/단계/기간 | `load_state` 재사용 |
| `logs/composer_fvg_created_watches.json` | FVG 생성 감시 | `_load_fvg_created_watch_state` 재사용 |
| `logs/composer_active_oz_watches.json` | 활성 OZ 자식 및 소유권 | `_load_active_children_state` 재사용 |
| `logs/composer_config_timed_chain_state.json` | SPECIAL 등록 시 공식 연쇄 signature/state/active | `_load_config_chain_state_for_specs_locked` 재사용 |
| `logs/oz_generic_watch_state.json` | Composer의 구버전 단독 FVG_NEW 이관, OZ 일반 Watch | 기존 이관 조건 그대로. GenericWatchController `_load_state` 재사용 |
| `logs/trend_watch_state.json` | INDICATOR 구독 registry | canonical Registry 복원 |
| `logs/trend_stream.json` | TREND FactStream generation 증가 | 같은 FactStream 생성, sequence=0 |
| `logs/trend_pending_events.json` | INDICATOR 미확인 이벤트 | Records 전체 복원 후 MemoryRecords로 연결 |
| `logs/fvg_watch_state.json`, `logs/fvg_stream.json` | FVG registry, generation 증가 | canonical Registry/FactStream 복원 |
| `logs/sweep_watch_state.json`, `logs/sweep_stream.json` | SWEEP registry, generation 증가 | canonical Registry/FactStream 복원 |
| `logs/sweep_detector_state.json` | 터치 기억, fingerprint, pending sync | `_load_detector_state` 재사용 |
| `logs/oz_external_liquidity_state.json` | OZ 외부 유동성 state, SWEEP registry bootstrap | `_load_state` → `_bootstrap_sweep_registry` |
| `logs/oz_manual_watch_state.json` | OZ 수동 감시/세대 | OZWatchController `_load_state` |
| `logs/oz_outgoing_events.json` | OZ 송신 확인/중복 상태 | canonical TelegramSender Records + 논리 ACK 대역 |
| `logs/oz_observed_*.json` | 심볼/검증/트리거 profile별 후보와 관측 체크포인트 | 같은 파일명 identity, `restore_observed_checkpoint` 재사용 |
| `logs/special4_state.json`, `logs/special4_state_*.json` | SPECIAL4 심볼별 버전/active/마지막 30m 상태 | 같은 Special4Runtime `_load_state`, 버전 검사 유지 |
| `logs/sweep_event.jsonl` | SweepEventFileWorker는 offset 0부터 이력을 읽음 | LF로 끝난 완전한 JSON 행 중 SWEEP_EVENT만 순서대로 적용, 잘린 마지막 행 대기/미적용 |
| config `OZ_COMMAND_FILE` (기본 `logs/oz_watch_command.jsonl`) | 각 명령 worker는 시작 시 EOF로 이동 | 경로/존재만 기록. 옛 명령을 재실행하지 않음. 새 명령은 Ingress COMMAND |

## 호스트 서비스와 일시 상태

이 목록은 빠진 전략 입력과 의도적으로 시작하지 않는 호스트 서비스를 구별한다.

- STAFF: config의 허용 심볼/기대 σ/endpoint/stale/pipe 설정. Snapshot 및 seq/epoch는 연결 수신 상태이고 디스크에서 과거 Snapshot을 복원하지 않는다. E2는 별도 STAFF 캐시를 주입받으며 폴링 캐시에 writer를 추가하지 않는다. seq 누락 출력은 시험 결과 또는 이벤트 전용 경로다.
- 김매니저의 WonbiState/SET_WONBI_SIGMA는 기대 σ 전달용이며 원비를 계산하지 않는다. E2 판정은 Board의 MT5 원비와 전달된 config를 사용한다. 실제 네트워크 제어·Telegram getMe/getUpdates/김매니저 유지관리 스레드는 실행하지 않는다. 출력은 SIGNAL이며 E3의 전송 분리 작업을 시작하지 않는다.
- `ECONOMY_ALERTED_FILE`(기본 program/alerted_events.txt), `ECONOMY_BRIEFING_FILE`(기본 logs/last_briefing.txt), 구 program/last_briefing.txt: 경제 캘린더 호스트 서비스의 중복/브리핑 상태다. 경로 규칙을 동일하게 목록화하되 E2 전략 엔진에서는 경제 네트워크 서비스나 파일 이관을 실행하지 않는다. 경제 서비스는 이번 전략 이전 대상이 아니다.
- SYSTEM CONTROL의 환경 설정/OZ_SYSTEM_ROOT/OZ_SYSTEM_LOG_DIR 및 pycache/logging 위치, Telegram 입력 offset, ZMQ 연결, EA Identity Status는 서비스 관리 정보다. 전략 복원 파일이 아니다. 기본 폴링 프로세스 시작 코드는 변경하지 않는다.
- 기존 재시작에서 저장하지 않던 FVG 활성 계산 캐시, Generic Watch 현재 봉 임시 상태, Watch MA 이력은 초기화한다. 실행 도중 E2 체크포인트에는 엔진 소유 상태로 포함한다.

## 쓰기 경계

상태 읽기는 선택한 polling program에서 허용한다. 이벤트 처리 중 기존 `read_json/atomic_json/Records`는 ContextVar 메모리 저장소로만 접근한다. 자동 상태 파일 저장·Journal·Outbox는 추가하지 않는다.

명시적 호스트 출력 `write_event_state_files(files, output_directory=..., polling_program=...)`는 기본 안내 경로 `Part1/event_state` 같은 별도 위치만 허용한다. polling program 내부 또는 상위 디렉터리, basename이 아닌 이름, JSON 아닌 입력과 링크 탈출을 거부한다. 같은 이름의 임시 링크를 따라 쓰지 않도록 배타 생성 임시 파일 후 교체한다. 이 API는 선택한 메모리 JSON 저장소의 출력이며 전체 엔진 체크포인트 파일 형식을 새로 정의하지 않는다. 전체 연속성은 기존 `checkpoint()/restore()` 메모리 계약이다.

`test_event_e2_startup.py`는 비어 있지 않은 registry/세대 번호, 손상 상태 fallback, 두 종목 시작 취소 격리, 별도 출력, polling 입력 트리 전체 SHA256 불변을 검사한다. 실제 읽은 파일 경로/해시는 검증결과 event_e2/startup_inventory.json에 기록하며 비밀값은 기록하지 않는다.

종료 시 확인한 제한: `create_live_event_engine(program)`의 `event_state_directory` 안내 속성은 읽기 원본의 부모를 기준으로 만든다. 이전 수정본을 읽는 경우 이 속성 대신 export에 수정본17의 `Part1/event_state`를 명시한다. 자동 저장은 없고 export는 출력 경로 필수 인자를 받는다. 안내 속성 기준 변경은 사용자 종료 지시에 따라 후속 항목으로 남긴다.
