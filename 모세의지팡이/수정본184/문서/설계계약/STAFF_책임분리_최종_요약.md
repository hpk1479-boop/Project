# STAFF 책임분리 최종 요약 — S0~S8

## 단계별 결과

| 단계 | 결과 |
|---|---|
| S0 | 수정본6 전체 기준선·합성/실제 MT5 캡처·성능 기준 동결 |
| S1 | 순수 Fact를 indicator_facts/FVG 등 주인 모듈로 이전 |
| S2 | numpy Snapshot 저장·수신 스레드·공개 입력 주입점 |
| S3 | pickle 없는 SNAPSHOT API·클라이언트 publication 캐시 |
| S4 | 전략/Watch/OZ/manager 클라이언트 이전 |
| S5 | STAFF 파생 계산·legacy 데이터 경로 제거. 사용자 승인 예외 기록 보존 |
| S6 | Wire v2 FULL/ROW/HB/HELLO/BUNDLE, schema·CRC·seq 누락 기록, MSP3 |
| S7 | MT5 원비 원본 전환. Python 원비 제거, 48열, 새 S7 기준선 |
| S8 | 파이프/EA 최적화 구현·검증 수행. 현재 판정: 구현·검증 수행 완료, 필수 게이트 미통과로 S8 완료 승인 보류 |

## 최종 구조

MT5 EA가 OHLC/MT5 지표/원비를 계산한다 → Wire v2 TF 묶음 → STAFF 전용 수신 스레드가 검증·seq 진단·불변 numpy Snapshot을 저장한다 → SNAPSHOT API가 publication 응답을 캐시해 전달한다 → 클라이언트 staff_snapshot/staff_compat와 각 Fact 모듈이 필요한 계산을 수행한다 → 기존 매니저·SPECIAL·Watch·OZ/FVG 조건과 상태 전이가 알림을 결정한다.

STAFF에는 pandas DataFrame 생성이나 파생 지표 계산이 없다. PING/SOURCE_HEALTH/기대 sigma 제어를 유지한다. 원비 sigma는 EA 시작 시 고정 입력이며 SET_WONBI_SIGMA는 기대값만 보낸다. 45열 과거 입력은 MT5 3σ 열을 매핑하고 sigma!=3은 명시 거부한다. 원비 없는 OHLCV/틱 경로에 Python fallback을 만들지 않는다.

## 명세와 기준선

- Wire_v2_명세.md 및 Part1/program/staff_schema.py가 현재 명세/레지스트리다. 기존45열 + wonbi_upper/lower/sigma, wonbi_mid는12열 별칭, schema0x36C28F68.
- S0 참고: 검증결과/staff_s0/. 실제 캡처와 expected를 포함한 이전 단계 기록을 보존한다.
- 현재 불변 기준: 검증결과/staff_s7/baseline_S7/manifest.json, SHA256 19933bf3b67773c7943f4ba8e25a668d097acc7925bcf77fbaffdddddcc0f17c.
- S8 검증 결과: 검증결과/staff_s8/. 미통과 게이트 ['EA_separate_capture_values_identical']. S8 기준선을 새로 고정하거나 S7을 덮어쓰지 않았다.
- 최신 성능 정책은 build/staff_performance_policy.json으로 동결되어 있다. S8 측정 경계는 성능비교경계_STAFF_S8.md.

## 남은 기존 결함과 제한

- S7 EA에서도 일봉 최초2행 지표24열의 비결정적 초기값이 반복 측정으로 확인됐다. S8 동일 시각 증분/FULL은 일치하지만 별도 실행 원시 출력 전체 일치는 실패다. 지표 초기화 수정 여부는 별도 판단이 필요하다.
- 기존 root DataManager 누락/GUI·Windows 경로 불변 목록 실패·collection error 및 audit 무결성22개 진단을 기준선 결함으로 보존했다. Part3는 레거시 동결이고 참조 정리는 별도 작업이다.
- 외부 검증은 합성240초·확대XAU/BTC 각1200초·실제XAU240초/BTC600초이다. 최종 알림14건/조건 전달29건이며 연속 수일 전체를 검증했다는 의미는 아니다.
- LIVE 참고 측정은 짧은 공통 구간1회이며 처리량 보장이 아니다. 상세 성능 실패가 있다면 원본 표본과 허용치를 그대로 유지한다.

## 이벤트 엔진 착수 전에

이벤트 엔진은 구현하지 않았다. S8 미통과 게이트 처리를 먼저 결정한다. 이후 폴링→이벤트 전환에 따른 알림 시점 변화는 별도 승인 변경으로 기록하고 새 기준선을 정해야 한다. source_epoch/stale/reconnect/seq 누락·TF 묶음 의미를 보존하며 프레임을 버리는 coalescing을 넣지 않는다. ATR14_GENERAL과 FVG_WILDER_ATR을 합치지 않는다. Part2 일반 회귀4묶음은 제외 상태를 유지하되 외부 동작 도구·대표 입력·신규 테스트·Part1 audit·루트 tests는 유지한다.


## S8 최종 사용자 승인 — 이전 보류 판정을 대체

S8은 **완료**다. `EA_separate_capture_values_identical`의 원시 실패는 그대로 보존하고, S7에서도 재현된 일봉 최초 2행·지표 24열 초기 버퍼 결함에 대해 **사용자 승인 예외**를 적용한다. 외부 동작 및 성능 게이트 통과를 완료 근거로 한다. 위 본문의 승인 보류/기준선 미동결 내용은 승인 전 이력이다.

현재 기준선: `검증결과/staff_s8/baseline_S8/manifest.json`. S7 기준선은 변경 없이 보존한다. 승인 전 보고서와 status는 `검증결과/staff_s8/approval_history/`, 원시 실패와 표본은 기존 위치에 보존한다.

E1은 이 지표 결함을 수정하지 않는다. MT5 별도 실행 간 비교에서만 `build/mt5_known_defect_exclusions.json`의 일봉 최초 이력 2개 시각 × 값 열 21~44(0-based)를 제외한다. 최초 FULL의 이력 시각에 고정하며 이후 매 창의 첫 두 행이나 ROW를 다시 제외하지 않는다. OHLC·거래량·원비·알림·health·seq 의미는 제외하지 않는다.
