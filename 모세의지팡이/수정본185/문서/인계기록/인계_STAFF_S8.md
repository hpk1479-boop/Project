# 수정본15 S8 인계

구현·검증 수행 완료, 필수 게이트 미통과로 S8 완료 승인 보류. 먼저 수정내역_STAFF_S8.md와 검증결과/staff_s8/status.json을 읽는다. **이벤트 엔진은 시작하지 않았다.**

## 현재 상태

- 생산 변경은 STAFF, EA, Identity Status, 생성 mqh 빌드 해시 4개뿐이다. schema=0x36C28F68, 48열/Wire 구조 불변.
- 진행봉은 마지막 버퍼만 갱신하고 OPEN HMA/원비를 재사용한다. 동일 값은 HB. FULL은 새 봉·이력/준비 변화·재연결이며 미전송 관측을 버리지 않는다.
- STAFF는 같은 publication 응답을 캐시하고 수신 bytes 저장소를 안전하게 공유한다. stale/seq/reconnect/health 의미를 바꾸지 않았다.
- 외부 5개 시나리오 결과=True, G1 새 실패0=True, 정식 성능=True.
- 미통과: ['EA_separate_capture_values_identical']. 허용치를 바꾸거나 자동 승인하지 않았다.

## 다음 판단에 필요한 근거

별도 MT5 실행 간 일봉 최초2행/지표24열의 비결정성은 같은 S7 재실행에서도 재현됐다. S8 신규 차이 셀은 없고, 동일 시각의 LIVE 증분/FULL 대조 10,469개는 일치한다. 실제 원시 출력 게이트는 실패 상태로 보존했다. 초기 지표 버퍼 수정은 값 변경이므로 이번 S8 범위에서 임의로 하지 않았다.

성능 표본10개와 판정은 검증결과/staff_s8/performance/에 있다. S0 자체 범위 또는 후보 비율 실패가 있으면 기록을 그대로 보고 다음 지시를 받는다. request_0~3은 사용자 지시로 참고만이다.

## 기준선·도구

- 현재 기준선: 검증결과/staff_s7/baseline_S7/manifest.json. S0–S7 보존, S8 새 기준선 없음.
- 최신 생성기/컴파일: build/generate_staff_s8_schema.py, compile_staff_s8_mt5.py. 컴파일 후 격리 터미널도 최종 생산 EA로 복원했다.
- EA 증거: ea_output_equality.json, ea_diagnosis.json, 각 캡처 executed_source.json. harness_attempt1은 실패한 도구 시도이며 정답 증거가 아니다.
- Part1 audit/루트 전체는 이미 각1회 실행했다. Part2 일반 회귀4묶음은 계속 미실행. 불필요하게 전체 게이트나 S0 생성 작업을 반복하지 않는다.
- Part3 동결, 참조5곳은 Part3_레거시_참조목록.md. 사용자 config BTC 설정 불변.


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
