# 수정본15 — S8 파이프·EA 최적화

판정: **구현·검증 수행 완료, 필수 게이트 미통과로 S8 완료 승인 보류**. 수정본14 전체 25,211개 파일을 해시 동결한 뒤 독립 복사했다. 이벤트 엔진은 시작하지 않았다.

## 범위와 구현

- EA: LIVE의 매 주기 전체 HMA/지표/FULL 직렬화를 없앴다. 새 봉, 이력 개수/시작점/지표 계산 개수 변화, 준비 상태 변화는 FULL이다. 진행봉은 CopyBuffer(0,1)의 마지막 값만 읽고 OPEN HMA·OPEN 밴드·원비는 이전 FULL에서 재사용한다. 마지막 값/거래량까지 같으면 HEARTBEAT만 만든다.
- 미전송 묶음은 성공할 때까지 보존한다. 재연결 시 미전송 관측의 FULL 표현을 캐시에서 그때 생성한다. TF 묶음·seq·CRC·관측 시각 및 프레임을 버리지 않는 규칙은 유지했다.
- 테스터는 기존 S7의 현재 봉 계산 경계를 유지하면서 ROW를 직렬화한 뒤 비교하던 중복 작업을 값 비교로 바꿨다.
- STAFF_Identity_Status는 1초마다 기록하거나 계정/연결/활성/파이프 등 상태가 변하면 즉시 기록한다. 시각과 seq는 기록 시 최신 값을 사용한다. 단순 시각·seq 변화만으로 1초 제한을 우회하지 않는다.
- STAFF: 요청 검증 후 동일 publication 조합의 multipart 응답을 캐시한다. 128개 요청 조합 슬롯으로 상한을 두고 각 슬롯의 최신 응답만 보관한다. 수신 프레임을 버리는 coalescing과 무관하다. 반환 목록은 새 객체이며 bytes는 불변이다.
- 매 요청의 허용 심볼/TF, 주말, FEED_NOT_READY, stale, 기대 sigma, 지표 준비 검사는 계속 수행한다. seq 재시작/epoch/수신 시각 변경은 캐시를 무효화한다.
- 수신 배열의 최종 소유자가 불변 bytes일 때만 저장소를 공유한다. 외부 mutable 배열은 복사·동결한다. 정렬된 시간 배열의 검증은 빠른 경로를 사용하고 중복/역행/NaT는 기존 보존 규칙을 적용한다. 응답 인코딩은 수신 lock 밖이다.

## 변경 파일

- Part1/program/THE STAFF OF MOSES.py
- Part1/program/MT5/THE_STAFF_OF_MOSES.mq5
- Part1/program/MT5/STAFF_Identity_Status.mqh
- Part1/program/MT5/STAFF_Wire_Schema.mqh — 열/상수/schema는 불변, EA 빌드 해시만 갱신
- 신규 Part2/validation_suite/test_staff_s8.py, S8 build 검증 도구, 정책/보고/인계 문서, 무결성 단위31, 현재 불변 목록

전략·매니저·SPECIAL·클라이언트 계산식·Wire 구조/schema·Part3·사용자 config·S0–S7 기준선·expected·성능 정책은 변경하지 않았다. 기존 테스트는 수정하지 않았다. Part3 참조 5곳은 목록만 유지했다.

## EA 출력 검증과 발견한 기존 결함

XAUUSD+ 2026-09-23 12:00–12:04 UTC, BTCUSD 2026-09-19 00:00–00:10 UTC를 S7/S8 EA로 각각 실제 캡처했다. 총 관측 10,469개. 별도 실행 간 원시 Snapshot 전체 일치: **False**.

차이는 양 종목 일봉의 가장 오래된 2행, 21–44열(24개 지표 열)에만 있다. 같은 S7 EA를 재컴파일·재실행해도 동일 셀들이 변한다. 1e-311 수준 값 등 초기 지표 버퍼의 비결정적 값이 관측되며, S8에서 추가로 달라진 셀은 없다. 가격·거래량·원비 열은 이 결함의 차이 대상이 아니다. 지표 초기 버퍼의 미초기화가 의심되지만 계산/값 변경 금지 범위이므로 값을 치환하거나 지표를 고치지 않았다.

추가 진단 EA에서 같은 관측 시각의 최적화 LIVE 상태와 기존 전체 BuildStaffPayload를 직접 비교했다. XAU/BTC 10,469개 모두 시간·거래량·48열이 비트 동일했다. FULL/ROW/HB 구성: {'1': 612, '2': 9821, '3': 36}. 진단 코드는 검증 폴더에만 있으며 생산 소스에는 들어 있지 않다.

따라서 최적화 경로의 새 값 차이는 발견되지 않았지만, 사용자가 지정한 별도 실행 간 완전 동일 게이트의 실패를 자동 면제하지 않았다. 기존 S7 기준선을 그대로 유지한다. 원본 실패·재현 자료: ea_output_equality.json, ea_diagnosis.json, capture_difference_diagnosis.json, *_sigma3_v2 캡처.

초기 캡처 도구는 source 선택 후 컴파일을 호출하지 않아 지정한 BEFORE EA가 실행됐다는 보장이 없었다. 해당 시도를 *_harness_attempt1로 보존했다. 도구에 명시적 컴파일과 실행 소스/EX5 해시 기록을 추가하고 필요한 전후·재현 비교만 다시 실행했다. 최종 판정은 executed_source.json이 있는 캡처만 사용한다.

## 외부 동작

| 시나리오 | 최종 알림 | 조건 충족 전달 | S7·LIVE↔BT |
|---|---:|---:|---|
| parity_240 | 5 | 8 | 통과 |
| weekday_1200 | 5 | 9 | 통과 |
| weekend_1200 | 4 | 8 | 통과 |
| actual_actual | 0 | 2 | 통과 |
| actual_btc | 0 | 2 | 통과 |

총 최종 알림 14건, 조건 충족 전달 29건이다. 시나리오당 S8 LIVE 한 번만 합산하며 두 항목은 중복될 수 있다. finals/SPECIAL/문구·시각·수신자/manager 이벤트/OZ/FVG/Watch/source health 8개 외부 필드를 원비 포함 비교했다. S7의 비원비 전용 제외 규칙은 S8 판정에 적용하지 않았다.

## 전체 테스트·실측·보존

- Part1 audit: {'PASS': 176, 'FAIL': 1}. 루트: {'ERROR': 1, 'PASSED': 267, 'SKIPPED': 6, 'FAILED': 5}. 새 실패 root=0, audit=0. 각 전체 실행 1회.
- S8 신규12 + S7 필수20: {'PASSED': 32}. 기존 Part2 일반 회귀 4묶음은 실행하지 않았다.
- 기존 Part1 무결성 실패1, 루트 DataManager/GUI·Windows 불변 목록 경로 관련 실패·collection error는 보존된 기준선과 signature로 비교했다. 성공하지 않은 기존 테스트를 PASS로 바꾸지 않았다.
- EA·PRICE/RSI/STO/DI 5개 컴파일 오류·경고0. 실제 BTCUSD LIVE v2 19TF 수신·원비 열 확인. 격리 터미널이며 거래/전략 서비스/외부 알림을 실행하지 않았다.
- 무결성 단위31에 생산 변경4개 등록. 기존 진단22개, 신규0. 현재 Part1 불변 목록1222개 재생성·검증.
- 원본 수정본14와 보호된 S0–S7·Part3·expected·성능 정책 해시 불변. BTCUSD config 원문 불변.

## 성능 판정

성능비교경계_STAFF_S8.md를 측정 전에 고정했다. 같은 논리 CPU, S0→S8 순 5쌍, CPU 중앙값 및 동결 S0 자체 범위 확인. S0는 전체 동결 BEFORE host다. parser는 원래 650행 v1/45열 1000회, 전체 경로는 원래 240초·8 Watch다. 제외된 Part2 회귀 테스트를 수집한 것이 아니라 동결 성능 함수만 호출했다.

| 작업 | S0 CPU 중앙값 | S8 CPU 중앙값 | 비율 | 허용치 | S0 범위 | 판정 |
|---|---:|---:|---:|---:|---|---|
| parser_650_rows | 2.000000 | 0.109375 | 0.0547 | 1.05 | 안정 | 통과 |
| request_0 | 0.406250 | 0.250000 | 0.6154 | 1.15 | 안정 | 참고만 |
| request_1 | 2.640625 | 2.515625 | 0.9527 | 1.09 | 안정 | 참고만 |
| request_2 | 3.281250 | 3.093750 | 0.9429 | 1.06 | 안정 | 참고만 |
| request_3 | 3.796875 | 3.656250 | 0.9630 | 1.09 | 안정 | 참고만 |
| oz_fvg_live_seconds | 73.109375 | 59.859375 | 0.8188 | 1.06 | 안정 | 통과 |

정식 성능 게이트: **True**, 환경 일치: True. 허용치/표본/산식은 변경하지 않았다. 원본 10개 worker 표본과 실패 수치는 performance/에 보존한다.

## 동시 LIVE 참고 측정

| 항목 | S7 | S8 |
|---|---:|---:|
| seconds | 34.687 | 34.687 |
| frames_per_second | 1.009023553492663 | 0.9801943091071583 |
| feed_frames | 665 | 646 |
| pipe_bytes | 650020 | 129688 |
| receive_cpu_s | 0.125 | 0.0625 |
| status_file_observed_writes | 347 | 35 |

동일한 실제 BTC 구간이 겹치도록 두 격리 EA를 동시에 실행했다. 시작 후15초를 제외한 공통 구간이다. 프레임 수는 묶음 기준이며 feed_frames도 별도 기재했다. CPU는 수신 스레드의 read/파싱/검증/저장 CPU이고 캡처 파일 기록은 제외했다. 상태 파일은 20ms mtime 관측 횟수이므로 OS 스케줄링에 따른 누락 가능성이 있다. 참고1회이며 성능 게이트가 아니다.

## 최종 상태

미통과 게이트: ['EA_separate_capture_values_identical']. 사용자 승인 예외는 없다. S8 결과로 S7 기준선을 덮어쓰지 않았다. 이벤트 엔진은 미착수다.

Wire schema=0x36C28F68, EA build=be59815f1e42938e7f210eb4807eaa98c7df40350dab1677db1ab00cab1f7309. 상세 판정은 검증결과/staff_s8/status.json을 따른다.


## S8 최종 사용자 승인 — 이전 보류 판정을 대체

S8은 **완료**다. `EA_separate_capture_values_identical`의 원시 실패는 그대로 보존하고, S7에서도 재현된 일봉 최초 2행·지표 24열 초기 버퍼 결함에 대해 **사용자 승인 예외**를 적용한다. 외부 동작 및 성능 게이트 통과를 완료 근거로 한다. 위 본문의 승인 보류/기준선 미동결 내용은 승인 전 이력이다.

현재 기준선: `검증결과/staff_s8/baseline_S8/manifest.json`. S7 기준선은 변경 없이 보존한다. 승인 전 보고서와 status는 `검증결과/staff_s8/approval_history/`, 원시 실패와 표본은 기존 위치에 보존한다.

E1은 이 지표 결함을 수정하지 않는다. MT5 별도 실행 간 비교에서만 `build/mt5_known_defect_exclusions.json`의 일봉 최초 이력 2개 시각 × 값 열 21~44(0-based)를 제외한다. 최초 FULL의 이력 시각에 고정하며 이후 매 창의 첫 두 행이나 ROW를 다시 제외하지 않는다. OHLC·거래량·원비·알림·health·seq 의미는 제외하지 않는다.


## 원본 복원 확인 및 로그 차이 처리 — 사용자 최신 승인

승인 기록과 baseline_S8의 보관 위치는 수정본16이다. 수정본15 승인 메타데이터 변경 8개를 복원하고 새 파일 692개를 삭제했다. 8개 변경 전/복원 후 SHA256이 일치한다. 원본 전체 S8 완료 시점 해시 목록은 없으므로 전체 과거 동일성을 주장하지 않는다. S8 Part1 목록의 아래 12개 차이는 모두 로그·실행 상태다. 사용자 지시에 따라 추가 조사하지 않으며 E1을 진행한다.

- Part1/program/logs/OZ_monitor.log
- Part1/program/logs/composer_active_oz_watches.json
- Part1/program/logs/fvg_monitor.log
- Part1/program/logs/fvg_stream.json
- Part1/program/logs/indicator_monitor.log
- Part1/program/logs/kim_secretary.log
- Part1/program/logs/oz_manual_watch_state.json
- Part1/program/logs/oz_watch_command.jsonl
- Part1/program/logs/sweep_monitor.log
- Part1/program/logs/sweep_stream.json
- Part1/program/logs/the_staff_of_moses.log
- Part1/program/logs/trend_stream.json
