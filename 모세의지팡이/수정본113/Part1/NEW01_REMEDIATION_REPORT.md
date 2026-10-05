# NEW-01 수정 결과

공식 ConfigTimedChain의 deadline 판정을 완료 시각 기준으로 수정했다. 중단 후 작업 폴더와 종료된 테스트 프로세스를 확인했으며, 이미 적용된 코드를 중복 수정하지 않았다. 이후 직접 관련된 기존 회귀테스트와 변경 해시 확인만 추가 수행했다.

**결과: NEW-01 테스트 13개 PASS + 직접 관련된 기존 회귀테스트 5개 PASS.** 마지막 사용자 지시에 따라 전체 regression suite는 실행하지 않았다. 기존 70개 전체 PASS 기록은 이전 수정 단계의 결과이며 이번 변경의 전체 검증 결과로 사용하지 않는다.

## 수정 파일

| 파일 | 변경 |
|---|---|
| [manager_KIM.py][kim] | 공식 chain callback·maintenance·상태 저장/복원·최종 OZ deadline filtering |
| [test_config_deadline.py][tests] | 기존 의미 characterization 및 NEW-01 회귀테스트 13개 |
| [run_tests.py][runner] | 향후 전체 suite 실행 시 NEW-01 테스트도 포함하도록 등록 |

운영 소스 수정은 `manager_KIM.py` 한 파일에 한정된다. MT5, FVG/SWEEP/TREND 계산, monitor_OZ, watch_orchestrator, SPECIAL 파일, 설정 파일은 이번 단위에서 수정하지 않았다.

## 적용 계약

- 유효한 `completion_time`을 우선하고, 없으면 `event_time`, `trigger_time`을 사용한다. 처리 시각 또는 bar 시작 시각으로 완료 시각을 대신하지 않는다. 완료 시각이 없는 timed trigger는 거절한다.
- 완료 시각 **≤ deadline은 유효**, **> deadline은 만료**다. A→B callback에 처리 시각을 넘기던 경로를 수정했다.
- 중간 조건 완료로 앞서 열린 최종 시간창을 연장하지 않는다. 순차형의 전체 deadline은 `A 완료 시각 + max_gap_sec`; 순서무관형은 해당 pair의 첫 완료 시각에 같은 제한을 더한다. `max_gap_sec=0`인 bar-only 설정에는 초 단위 전체 deadline을 새로 만들지 않는다.
- 다음 단계 window는 pair의 실제 완료 시각에 `final_window_sec`를 더해 계산한다. 최종 OZ에는 이 window와 상속된 전체 deadline 중 더 이른 경계를 적용한다. FVG TOUCH는 남은 window만 넘겨받으며 TOUCH 처리 시각부터 다시 시작하지 않는다.
- maintenance는 wall time 경과만으로 WAIT_B·post-touch·최종 child의 판정 증거를 삭제하지 않는다. 지연 도착한 event는 원래 deadline으로 판정한다. 늦은 B나 TOUCH를 거절해도 그 뒤 도착하는 유효한 지연 event의 근거를 지우지 않는다.
- 공식 최종 watch의 ID·deadline을 상태 파일 version 4의 `active`에 영속화한다. 재시작 및 PING 이후에도 같은 경계를 사용한다. SPECIAL 모듈이 순차 등록되는 동안 먼저 등록한 모듈의 저장 때문에 뒤 모듈의 미복원 기록이 사라지지 않도록 보존한다.
- 공식 FINAL_ALERT는 SPECIAL hook/Telegram 전송 전에 해당 watch의 deadline을 검사한다. 혼합 알림에서 만료된 공식 watch만 제외하고 독립 watch는 유지한다. 기존 suppressed/expired 응답의 `delivered=True`는 소비 ACK이며 실제 Telegram 전송 성공을 뜻하지 않는다.

## 보존한 전략 의미

- **MAX_GAP_BARS:** 기존 실제 확정봉·anchor 기준, N개 허용/N+1 거절, 확인 불가능할 때의 거절을 유지한다. source snapshot과 봉 수 gate 자체를 event-time 방식으로 다시 설계하지 않았다.
- **FVG/post-touch:** 지정 시간봉, 동방향 TOUCH, zone identity, pair 자격의 남은 window, TOUCH 소비 후 OZ 연결을 유지한다. 순차형 B 완료의 state 초기화가 방금 등록한 `post_touch_armed`까지 삭제하던 경로는 연속 진행·재시작 보장에 필요하여 함께 수정했다.
- **SPECIAL:** 기존 TIME_FILTER/FINAL_TIME_FILTER와 hook 결과를 유지한다. deadline이 유효하더라도 별도 봉 수·방향·시간필터가 거절하면 전략은 성립하지 않는다. 따라서 독립성이 보장되는 대상은 **deadline 판정**이며 기존 전략 gate의 기준을 바꾼 것은 아니다.
- 반대 방향 cancellation, SEQUENTIAL 순서, UNORDERED의 서로 다른 CROSS/FVG 조건·방향별 조합을 유지한다.

## 테스트와 증거

운영 소스 수정 전에 테스트를 추가·실행했다. 기존 의미 characterization 4개는 PASS, 신규 경계/복원 검증은 실패하여 문제를 재현했다. 최초 실행은 subtest 포함 failure 22, error 3이었다. error는 기존 만료/복원 결과에 기대한 상태·응답 필드가 없어서 발생한 것이며 실패 로그를 보존했다.

| 검증 | 결과 |
|---|---|
| A→B deadline 직전/동일/직후 × maintenance 순서 × KIM 재시작 | PASS |
| 최종 OZ deadline 직전/동일/직후 × maintenance 순서 × KIM 재시작 | PASS |
| 최종 window를 실제 pair 완료에서 시작, post-touch 후 deadline 미연장 | PASS |
| post-touch 경계 및 만료 event 후 유효한 지연 event | PASS |
| UNORDERED 양 순서·경계·지연 수신·재시작 | PASS |
| completion timestamp 우선순위, 시각 누락 거절 | PASS |
| 확정 전달 후 재시작·동일 ID 재수신의 알림 중복 방어 | PASS |
| MAX_GAP_BARS, FVG 방향/시간봉, SPECIAL 시간필터·hook | PASS |
| SPECIAL load·개인 OZ deadline·전체 fact→Composer→OZ 알림·FILTER 기존 경로 | PASS |
| 승인된 변경 이력·최종 소스 해시 | PASS |

- [수정 전 재현 로그][red]
- [NEW-01 13개 테스트 로그 — 11.106초][green]
- [직접 관련된 기존 테스트 5개 로그 — 5.177초][related]
- [파일별 변경 전후 SHA-256][hashes]
- [수정 단위 패치][patch]

## 한계와 되돌리기

실제 KIM 소스 복사본·SPECIAL 등록·상태 파일 저장/복원을 offline fixture에서 실행했다. 실제 MT5/Telegram/다중 프로세스 동시 실행은 이번에 검증하지 않았다.

임의 지연 상한이나 TTL을 도입하지 않았다. deadline 경과 후 판정 상태와 watch는 이후 기존 setup 교체/cancellation 경로가 처리할 때까지 보존될 수 있다. 따라서 영속 상태 보존 비용은 남는다.

기존 version 3에 저장된 WAIT_B/post-touch 상태는 존재하는 deadline을 사용해 읽을 수 있다. **이전 코드가 이미 삭제했거나 저장하지 않은 최종 child 상태는 추측해서 복원하지 않는다.** 새 형식 적용 후 생성·저장된 공식 child부터 재시작 복원을 보장한다.

변경 단위는 [13-new01][unit]이다. 이전 코드 [before.zip][before]과 전후 해시·패치를 보존했다. 이전 전체 검사 결과도 이 폴더의 `previous_full_report.json`/`previous_full_suite.txt`로 별도 보관했다. 되돌릴 때는 해당 단위의 세 코드/테스트 파일만 복구하고 신규 테스트 파일은 제거한다. 운영 적용 후에는 version 4 상태 파일도 함께 백업하고 이전 binary의 상태 호환성을 확인해야 한다.

NEW-01은 위 계약과 검증 범위에서 해결했다. 이번 마무리에서 추가 미해결 기능 문제는 발견하지 않았다.

[kim]: <C:/Users/hpk14/Desktop/모세의지팡이 - 지피티/program/manager_KIM.py>
[tests]: <C:/Users/hpk14/Desktop/모세의지팡이 - 지피티/audit/test_config_deadline.py>
[runner]: <C:/Users/hpk14/Desktop/모세의지팡이 - 지피티/audit/run_tests.py>
[unit]: <C:/Users/hpk14/Desktop/모세의지팡이 - 지피티/audit/remediation/13-new01>
[red]: <C:/Users/hpk14/Desktop/모세의지팡이 - 지피티/audit/remediation/13-new01/before_tests.txt>
[green]: <C:/Users/hpk14/Desktop/모세의지팡이 - 지피티/audit/remediation/13-new01/tests.txt>
[related]: <C:/Users/hpk14/Desktop/모세의지팡이 - 지피티/audit/remediation/13-new01/related_regression.txt>
[hashes]: <C:/Users/hpk14/Desktop/모세의지팡이 - 지피티/audit/remediation/13-new01/changes.json>
[patch]: <C:/Users/hpk14/Desktop/모세의지팡이 - 지피티/audit/remediation/13-new01/changes.patch>
[before]: <C:/Users/hpk14/Desktop/모세의지팡이 - 지피티/audit/remediation/13-new01/before.zip>
