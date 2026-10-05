# 회귀 테스트 매트릭스

원칙: **현재 소스가 실제로 하는 일을 assertion으로 고정한다.** 정상 동작과 알려진 결함의 characterization을 구분한다. 결함도 현재 결과가 재현되면 PASS다. 이는 장애 안전성 승인이나 수정 완료를 의미하지 않는다. 자동 검증하지 않은 항목을 PASS로 표시하지 않는다.

실행: `python -B audit/run_tests.py`. 결과/환경/원본 해시 검사는 [test_report.json](audit/results/test_report.json). 모든 자동 사례는 [test_baseline.py](audit/test_baseline.py)의 unittest이고, 아래 테스트명은 `test_` 접두사를 포함한다. MT5/Telegram/실 네트워크 없이 실행한다.

## 1. 입력과 판정 기준

| fixture | 입력/조작 | oracle |
|---|---|---|
| replay.json | UTC 고정 epoch, 80행 상승 OHLCV, 45개 합성 MT5 slot, FVG gap, PDL, OUT/IN/NEXT_BAR 3단계 | FVG 116.2~118.6, TREND 86.6666667/16.6666667, BLIND OZ LONG S/B0 최종 event |
| pipe fault | 같은 sequence, 마지막8바이트 누락, optional column NaN | 이전 캐시/sequence 유지, 요청별 실패 |
| clock | wall+monotonic 30/30.001,5/5.001,60초 경계 | stale/metric/chain/latch의 현재 부등호 |
| lifecycle | 실제 state 파일을 유지하고 한 component 객체만 재생성 | 복원 필드, RAM 초기화, 재전송/누락 |
| transport fault | handler 전 timeout / handler 후 ACK 유실 / HTTP500 | REQ 재생성, 재시도 유무, 알림 수, watch 보존/소진 |
| command stream | 실제 JSONL 정상/partial append, worker 시작 offset | 등록/취소/유실, PING/refresh 복구 |

합성 OHLC를 기대 결과 함수에 다시 구현하지 않는다. 전략 함수는 실제 소스, 고정 수치/방향/이벤트 수/상태 key가 oracle이다. 길이/방향/갯수만 보지 않고 핵심 가격·점수·event 필드와 final recipient까지 검사한다. 운영 원본 SHA-256 변화도 별도 실패다.

## 2. 실행하는 자동 매트릭스

| ID | 테스트명 | 재현 조건 → 현재 기대 결과 | 분류 |
|---|---|---|---|
| B01 | test_source_hashes_unchanged | 원본28개 SHA-256 동일 | 불변성 |
| B02 | test_actual_special_plugins_load | 원본 SPECIAL6개 register, 공식 spec/chain/child 존재 | startup smoke |
| S01 | test_pipe_roundtrip_features_and_copy | SMOS decode→STAFF→실 client,80행/원비/ATR/복사 격리 | 정상 |
| S02 | test_pipe_duplicate_sequence_and_partial_do_not_replace | 동 ID/절단 입력→기존 snapshot 유지 | 정상 |
| S03 | test_staff_stale_boundary_and_client_error | 정확히30초 허용,30.001 실패,None; PING pong 유지 | 경계 |
| S04 | test_optional_indicator_failure_is_request_scoped | RSI NaN,OHLC 요청 성공/RSI 요청 실패 | 정상 |
| S05 | test_sigma_sync_uses_staff_and_rejects_invalid | sigma2 STAFF/KIM 일치,0 거부 | 정상 |
| S06 | test_weekend_empty_response_and_crypto_exemption | 토요일 XAUUSD={},BTCUSD는 데이터 없으면 오류 | 현재 계약 |
| S07 | test_client_timeout_recreates_req_socket | STAFF timeout→이전 socket close/새 REQ | 통신 경계 |
| T01 | test_trend_fixture_and_duplicate_suppression | UP 점수 고정,동일 방향 두 번째 fact 없음 | 전략/중복 |
| T02 | test_trend_restart_reemits_state_and_duplicate_notification | TREND만 재생성→fact 재전송,같은 KIM signature 알림 억제 | 단독 재시작 |
| T03 | test_failed_trend_ack_is_not_retried_when_state_unchanged | 최초 ACK 실패 후 같은 UP 누락 유지 | ISSUE-02 |
| T04 | test_metric_freshness_independent_from_trend_state | 허용 finite metric만 저장,5초 경계 | 정상 |
| F01 | test_fvg_atr_bounds_are_inclusive | gap=.5/3.5 허용,바깥값 거부(ATR2) | 전략 경계 |
| F02 | test_fvg_thirty_closed_bars_and_latest_three | eligible30개,추적3개,age0/1/2 | 전략 경계 |
| F03 | test_fvg_first_snapshot_suppresses_created_but_emits_touch | 초기 snapshot→TOUCH만,반복은 event0 | 현재 계약 |
| F04 | test_fvg_live_fill_is_not_closed_invalidation | live bot 도달은 생존,마감 후 FILLED/KIM 제거 | 전략/대조 |
| F05 | test_fvg_restart_before_fill_leaves_kim_touch | fill 직전/직후 FVG 재시작→KIM 잔여 touch | ISSUE-04 |
| F06 | test_fvg_manager_restart_then_engine_restart_repairs_touch | KIM 재시작 시 빈 store,FVG도 재시작하면 touch 회복 | ISSUE-01 대조 |
| W01 | test_sweep_closed_touch_restart_restores_without_oz_republish | SWEEP 재시작→restored fact,KIM만 재전달,JSONL 증가0 | 단독 재시작 |
| W02 | test_sweep_restart_before_and_after_invalidation | level 교체 전/후 SWEEP 재시작→이전 fact 제거 유지 | 장애 경계 |
| W03 | test_sweep_selects_outermost_and_consumes_other_levels | 동봉 여러 LONG 레벨→최저1개 event,나머지 consumed | 전략 |
| W04 | test_unknown_sweep_selector_falls_back_to_all | VAH 단독→ALL,PDL+VAH→PDL | ISSUE-13 |
| O01 | test_oz_restart_restores_watches_but_not_candidate | 후보 존재 중 OZ 재시작→watch복원/후보소실 | ISSUE-07 |
| O02 | test_oz_restart_external_state_replay_keeps_touch_identity | external 저장+처음부터 JSONL replay→동일 key/time/status | 정상 복구 |
| O03 | test_external_invalidation_then_old_duplicate_resurrects | TOUCH→INVALIDATED→과거 TOUCH→PENDING 재생성 | ISSUE-05 |
| O04 | test_oz_out_in_equal_band_is_in_and_strict_divergence | band equality=IN,BO equality불허/엄격 돌파 허용 | 전략 경계 |
| O05 | test_oz_silent_completion_without_environment | watch 없는 완성→silent consume,FINAL0 | 전략 |
| O06 | test_oz_normal_and_regime_validation_from_same_family | middle OUT/upper IN→S,NORMAL/REGIME slope 부호 | 전략 |
| O07 | test_oz_atr_gate_boundary_and_confirmed_survival | ATR 거리 equality 허용,CONFIRMED 후 ACTIVE 생존검사 제외 | 현재 계약 |
| O08 | test_telegram_failure_keeps_watch | HTTP500 final→watch 남음 | 정상 실패 처리 |
| K01 | test_manager_restart_loses_unchanged_trend_fact | private spec복원,동일 TREND fact 미복원 | ISSUE-01 |
| K02 | test_manager_restart_loses_unchanged_fvg_and_sweep | KIM만 재시작→동일 FVG/SWEEP fact 미복원 | ISSUE-01 |
| K03 | test_stale_staff_does_not_expire_trend_fact | STAFF stale300초,TREND gate는 계속 참 | ISSUE-08 |
| K04 | test_fact_duplicate_and_final_alert_duplicate_are_different | fact 재전달 알림0,같은 FINAL 재전달 알림2 | ISSUE-03 |
| K05 | test_lost_final_ack_can_duplicate_delivery | 수신처리 후 ACK 유실+retry→알림2 | ISSUE-03 |
| K06 | test_invalid_event_and_unknown_action_contract | non-dict/unknown kind 오류,unknown command 무시 | 계약 |
| R01 | test_watch_registration_restart_and_ping_reconcile | queue등록 직후 소비자재시작→skip,PING→복구,저장 후 재시작도 복구 | 필수 장애 |
| R02 | test_start_order_failed_ping_periodic_refresh_recovers_registration | 엔진 먼저/KIM unavailable PING실패→KIM refresh 등록 | 시작 순서 |
| R03 | test_unowned_oz_watch_cancelled_by_restart_ping | 독립 Generic복원 후 PING→취소 | ISSUE-06 |
| R04 | test_jsonl_partial_line_is_consumed_and_lost | partial 읽기 사이 append→등록 유실 | ISSUE-10 |
| C01 | test_timed_chain_middle_restart_and_stale_duplicate | stage1/deadline/watch ID복원,stage0 중복무시,완료 알림 | 필수 장애 |
| C02 | test_timed_chain_deadline_equality_depends_on_scheduler_order | deadline equality에서 callback/maintenance 순서 차이 | ISSUE-09 |
| C03 | test_unordered_distinct_conditions_and_expiry_boundary | 같은 조건 중복은1개,2종 충족,cutoff 경계 | 전략 |
| C04 | test_filter_overlap_survives_restart_and_expires | 첫 hit 저장→KIM 재시작→다른 hit→겹침50초→만료 | 복구/전략 |
| C05 | test_chain_notify_failure_removes_chain_before_delivery | 마지막 NOTIFY HTTP500→chain제거,재시작 후 없음 | ISSUE-12 |
| I01 | test_command_alias_macro_and_invalid_reload | 기본더블비 primitive/Tf치환,표현 alias,깨진 JSON→이전 사전 | 해석 경계 |
| E01 | test_end_to_end_all_facts_composer_oz_notification | 실제 binary→STAFF→세 엔진→ALL gate→OZ arm→external ATR→OUT/IN→LONG S B0→HTTP기록,child취소,중복0 | 전체 경로 |
| E02 | test_generic_bar_close_through_orchestrator_to_notification | binary→STAFF→Generic BAR/CLOSE→ZMQ callback→chain완료→HTTP기록 | orchestrator 경로 |

현재 표의 49개 unittest가 실행 대상이다. 재현 input 일부는 fixture recipe의 명시적 override로 만들며 소스 판정함수를 교체하지 않는다. 테스트 파일에 새 사례를 추가하면 표와 report의 수를 함께 검토한다.

## 3. 사용자 지정 장애 시나리오와 증거

| 요청 시나리오 | 직접 검증 사례 | 동결되는 결과 |
|---|---|---|
| manager_KIM 단독 재시작 | K01,K02,F06,C01,C04 | watch/chain 복원과 fact 유실을 별도로 확인 |
| TREND 단독 재시작 | T02,R01 | 등록복원,현재방향 재전송,같은 KIM signature중복 억제 |
| FVG 단독 재시작 | F05,F06,R01 | touch 재전송과 첫 snapshot invalidation 누락 차이 |
| SWEEP 단독 재시작 | W01,W02,R01 | detector disk복원/현재레벨 대조/restored fact만 KIM재전송 |
| monitor_OZ 단독 재시작 | O01,O02,R03 | watch/external복원,base후보소실,소유권 reconciliation 문제 |
| STAFF stale | S03,K03,T04 | STAFF응답 차단과 기존 fact의 상이한 만료정책 |
| 동일 event 중복수신 | T01,F03,K04,K05,O02,O03,C01 | dedup가 적용되는 층과 적용되지 않는 층 |
| 시작 순서 변경 | R01,R02 | worker EOF→PING 순서,엔진 먼저/KIM unavailable→refresh |
| watch 등록 직후 재시작 | R01 | consume 전/저장 후 crash cut의 차이 |
| timed chain 중간 재시작 | C01,C04 | 직렬 stage와 FILTER hit 각각 보존 |
| FVG invalidation 전/후 재시작 | F04,F05 | uninterrupted 정상제거 vs 재시작 첫 snapshot 잔여 touch |
| SWEEP invalidation 전/후 재시작 | W02,O03 | 현재 level reconcile 및 과거 touch의 역순 부활 |

## 4. 추가 확장용 매트릭스 — 아직 자동 검증하지 않은 범위

이 표는 하네스가 확장될 위치와 판정할 결과를 정의한다. 현재 PASS 결과에 합산하지 않는다.

| ID | 입력/주입 위치 | 검증할 현재 계약 | 실행 상태 |
|---|---|---|---|
| P01 | Windows 실제 EA→Named Pipe write/read | header/slot, broker time, 재접속 sequence, concurrent writer | 미실행,MT5 필요 |
| P02 | 실제 libzmq REQ/REP+subprocess kill | EFSM/RCVTIMEO/SNDTIMEO/HWM/메시지 큐/ACK loss | 미실행,pyzmq 필요 |
| P03 | state fsync/replace 앞뒤 OS강제종료 | disk에 남은 old/new 상태,여러 파일 commit 간극 | 객체 재시작만 검증; 전원손실 미실행 |
| P04 | 각 worker JSONL truncate/rotate/Windows sharing | offset 복구/명령복제/유실 | partial line만 실행 |
| P05 | 여섯 프로세스 모든 startup permutation | desired 구독 수렴과 stale삭제,timeout 복구 상한 | 대표 순서만 실행 |
| P06 | FVG BEAR mirror,age29→30 중 restart,추적3개 승격 | 만료와 fill/밀림 구분 | BULL/age/추적수 기초만 실행 |
| P07 | PWH/PWL,4H/8H,세션 자정/DST,동봉 LONG+SHORT | 원문 level산출·대표선정과 ID | PDL/동방향 대표만 실행 |
| P08 | 모든 OZ 6 profile LONG/SHORT S/A/B/C | family 독립탈락,모든 trigger 우선순위,각 timer N/N+1 | BLIND E2E,NORMAL/REGIME,DIVERGENCE 경계만 실행 |
| P09 | OZ environment 교체/다중 소유자/수신자 일부실패 | silent probe,profile병합,일부 성공 후 watch소진 | no-environment/final실패 기본만 실행 |
| P10 | scheduled start,CANCEL_ON,UNORDERED disk복원 | 예약·음수 stage·K-of-N live-child 재시작 | sequential/FILTER복원과 latch만 실행 |
| P11 | 공식 SPECIAL1~6의 독립 market replay | 현재 전략 spec/hook/timefilter/각 고유 state | 모두 register smoke,공식별 E2E 미실행 |
| P12 | 모든 자연어 예시/Gemini fixture 응답 | intent우선순위,TF/방향 부가·손실 거부 | alias/macro/실패 reload만 실행 |
| P13 | corrupt state/복수 item 중1개 malformed | 각 loader의 partial/all복원 범위 | 미실행 |
| P14 | 시계 역행/monotonic 재설정/out-of-order candle | wall vs monotonic/fact 역순 덮어쓰기 | 명시적 deadline/역순SWEEP만 실행 |
| P15 | SHORT/NEUTRAL tie,TREND 전 metric/query | score/threshold equality,bar_mode=CLOSED/query field | UP score/metric cache만 실행 |
| P16 | STAFF 전체 NaN OHLC/whitelist/unknown indicator/여러 TF 중1개 오류 | 현 weak validation와 request 원자성 | optional RSI/stale/partial 기본만 실행 |

## 5. 실행 결과 해석과 향후 수정 절차

1. 이 단계에서 운영 소스를 바꾸지 않는다. report의 `source_files_changed=[]`를 확인한다.
2. 문제를 수정할 후속 작업에서는 먼저 해당 재현 테스트를 읽고 현재 PASS assertion이 어떤 기존 동작인지 확인한다.
3. 승인된 기대 계약만 별도 regression assertion으로 추가/변경한다. 기존 전략 score/threshold/시각/side 의미를 편의상 바꾸지 않는다.
4. 재시작/재시도 수정은 중복·유실·과거이벤트 생성의 대조 사례를 함께 실행한다. 단순히 테스트를 skip하거나 hash manifest를 다시 생성해 녹색으로 만들지 않는다.
5. 위 미검증 wire/MT5/OS crash 항목을 이 offline PASS로 대체하지 않는다.
