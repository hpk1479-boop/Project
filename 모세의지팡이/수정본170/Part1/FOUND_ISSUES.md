# 발견된 문제 및 현재 제한

운영 코드는 수정하지 않았다. 아래 **실제 결과**를 characterization test의 PASS 조건으로 고정했다. **기대 결과**는 현재 소스가 보장한다는 뜻이 아니라 후속 수정 검토를 위한 후보 계약이다. 전략 변경 여부를 승인받기 전에는 기대 결과를 운영 코드에 적용하지 않는다.

## ISSUE-01 — KIM 단독 재시작 후 변하지 않은 fact가 복원되지 않음

- 파일/함수: `manager_KIM.py:ComposerManager.__init__, _refresh_engine_family_after_ping, _sync_engine_subscriptions`; TREND `_send_watch_state_if_changed`, FVG `process_watch_result`, SWEEP `_sync_restored_facts`.
- 재현 조건: TREND UP/FVG TOUCH/SWEEP TOUCH 전달 후 KIM만 재생성. 엔진 객체는 유지. 같은 snapshot 재평가 또는 구독 재전달.
- 실제 결과: KIM fact dict가 비어 있고 동일 상태는 재전송되지 않는다. SWEEP restored 동기화는 SWEEP 자체 재시작에서만 활성화된다. PRIVATE spec 저장 복원과 별개다.
- 기대 결과: 현재 살아있는 구독에 대한 fact를 별도 동기화하여 재시작 전 조합 가능 상태를 복구.
- 영향 범위: 해당 fact를 기다리는 Composer 조합이 다음 변화까지 중단. 대기 시간 상한 없음.
- 수정 후보: 세션/epoch를 가진 fact snapshot 요청·응답, PING/manager boot 세대 동기화. 과거 CREATED를 새 이벤트로 재생하지 않도록 lifecycle event와 구분.
- 기존 로직 변경 위험도: 높음. 과거 이벤트 재실행·일회성 watch 소진·중복 setup을 만들 수 있음.
- 재현 테스트: `test_manager_restart_loses_unchanged_trend_fact`, `test_manager_restart_loses_unchanged_fvg_and_sweep`, `test_fvg_manager_restart_then_engine_restart_repairs_touch`.

## ISSUE-02 — TREND 상태 송신 실패 후 동일 상태 재시도 없음

- 파일/함수: `strategy_TREND.py:TrendEngine._send_watch_state_if_changed`, `ManagerClient.send`.
- 재현 조건: 최초 UP 전달에서 KIM 수신 전 timeout, 다음 동일 UP snapshot.
- 실제 결과: `_last_watch_state`가 send 전에 갱신되어 다음 이벤트가 억제된다. KIM은 UP fact를 받지 못한다.
- 기대 결과: 성공 ACK 또는 명시적 재동기화까지 전달 필요 상태 보존.
- 영향 범위: KIM 시작 지연/일시 정지 시 TREND 기반 조합. FVG/SWEEP 일반 이벤트도 ACK 결과를 사용하지 않는 유사 패턴이 있으나 이 테스트의 직접 입증 범위는 TREND이다.
- 수정 후보: ACK 이후 watermark commit 또는 durable outbox+idempotent consumer.
- 기존 로직 변경 위험도: 중~높음. 재시도는 ISSUE-03 중복 소비 대책과 함께 검토해야 함.
- 재현 테스트: `test_failed_trend_ack_is_not_retried_when_state_unchanged`.

## ISSUE-03 — 최종 알림에 전역 dedup/전송 완료 기억이 없음

- 파일/함수: `manager_KIM.py:ComposerManager._handle_oz_event_core`, `NotificationService.send`; `monitor_OZ.py:TelegramSender`.
- 재현 조건: 같은 FINAL_ALERT를 두 번 수신하거나, Telegram 200 처리 후 KIM ACK만 유실되어 재전달.
- 실제 결과: 동일 message가 두 번 전송된다. Composer fact signature suppression과 최종 notification dedup는 별개다.
- 기대 결과: 동일 logical alert의 재전달을 식별하고 기존 처리 ACK를 반환.
- 영향 범위: 최종/일반 알림 중복, 재시작/ACK timeout 경계. watch 취소가 이미 되었어도 입력 자체가 차단되지는 않음.
- 수정 후보: producer event_id + 영속 consumer 결과 ledger, Telegram 전송/ACK 불확실성 정책 명시.
- 기존 로직 변경 위험도: 높음. 같은 문구의 정상 별도 알림을 잘못 병합하면 안 됨.
- 재현 테스트: `test_fact_duplicate_and_final_alert_duplicate_are_different`, `test_lost_final_ack_can_duplicate_delivery`.

## ISSUE-04 — FVG 단독 재시작을 사이에 둔 fill/expiry의 KIM 잔여 touch

- 파일/함수: `strategy_FVG.py:FVGEngine.__init__, process_watch_result`; `manager_KIM.py:_handle_fact_event`.
- 재현 조건: touch가 KIM에 존재할 때 FVG 재시작, 그 후 첫 snapshot에서는 영역이 확정봉 fill되어 이미 없음.
- 실제 결과: FVG 이전 active 기억이 없어 FILLED를 보내지 않고 KIM touch가 남는다. 이미 fill된 snapshot으로 다시 FVG 재시작해도 남는다. uninterrupted fill 경로는 정상 삭제됨.
- 기대 결과: 새 snapshot의 현재 active set과 KIM zone/touch를 reconcile하되 과거 생성 이벤트를 재생하지 않음.
- 영향 범위: FVG 조건이 잘못 계속 참일 수 있음. age expiry/추적수 탈락도 같은 메모리 경계를 검토해야 함.
- 수정 후보: FVG active-set snapshot protocol 또는 detector state 복원+reconcile.
- 기존 로직 변경 위험도: 높음. 최신 3개 밖 후보와 fill/expiry/추적제외의 구분을 유지해야 함.
- 재현 테스트: `test_fvg_restart_before_fill_leaves_kim_touch`, 대조 `test_fvg_live_fill_is_not_closed_invalidation`.

## ISSUE-05 — invalidation 뒤 과거 SWEEP TOUCH 재수신 시 부활

- 파일/함수: `monitor_OZ.py:ExternalLiquidityController.apply_event`.
- 재현 조건: 현재 registry가 살아있는 watch에서 TOUCH→더 늦은 INVALIDATED→예전 TOUCH 중복.
- 실제 결과: invalidation이 state를 삭제하여 timestamp 비교 대상도 없어짐. 이전 TOUCH가 PENDING_ATR로 다시 생성됨.
- 기대 결과: 삭제 후에도 마지막 무효화 watermark를 유지해 늦게 도착한 과거 TOUCH 거절.
- 영향 범위: 중복/역순 replay가 외부 유동성 자격을 잘못 부활시킬 수 있음. 정상 전체 JSONL 순서 replay의 최종 상태는 별도 대조 테스트에서 유지됨.
- 수정 후보: watch/direction/level별 tombstone 또는 sequence 포함 lifecycle.
- 기존 로직 변경 위험도: 중~높음. 같은 level의 정당한 새 cycle과 과거 event 구분 필요.
- 재현 테스트: `test_external_invalidation_then_old_duplicate_resurrects`, 대조 `test_oz_restart_external_state_replay_keeps_touch_identity`.

## ISSUE-06 — OZ 재시작 PING이 독립 Generic 감시를 지울 수 있음

- 파일/함수: `manager_KIM.py:ComposerManager._refresh_oz_after_ping`; `monitor_OZ.py:GenericWatchController._load_state`.
- 재현 조건: 개인 독립 Generic watch가 OZ에 저장됨. KIM의 timed chain/active child 소유 목록에는 없음. OZ 재시작 후 PING.
- 실제 결과: OZ가 watch를 복원한 뒤 KIM의 persisted−desired 비교가 CANCEL_GENERIC를 보내 제거함.
- 기대 결과: KIM 소유 child와 OZ 자체 저장이 유일한 원본인 독립 watch를 구분해 복원 유지.
- 영향 범위: 단독 Generic watch. 직접 Manual/FVG-created 후속 감시도 소유권 저장 경로별 추가 점검 필요.
- 수정 후보: 소유권 태그/소유 원장 범위로 reconciliation 제한 또는 독립 명령의 KIM 저장.
- 기존 로직 변경 위험도: 높음. 이미 끝난 watch를 살려내거나 다른 사용자의 watch를 지우지 않아야 함.
- 재현 테스트: `test_unowned_oz_watch_cancelled_by_restart_ping`.

## ISSUE-07 — OZ 후보는 watch 저장과 함께 복원되지 않음 (현재 복구 한계)

- 파일/함수: `monitor_OZ.py:OZMonitor.__init__, _process_out_in`; `OZWatchController._load_state`.
- 재현 조건: live OUT→IN 후보 등록 뒤 OZ만 재시작, 첫 snapshot은 IN.
- 실제 결과: WatchSpec은 복원되지만 후보/episode/cross/alert_keys는 초기화. 첫 IN으로 과거 OUT→IN을 만들지 않아 후보 없음.
- 기대 결과: 재시작 전 후보 연속성을 요구한다면 후보의 관측 이력도 복원해야 함. 이 기대는 별도 정책 결정 사항이며 현 코드의 과거 이벤트 억제는 그대로 고정함.
- 영향 범위: 감시가 남아 있어도 진행 중 setup 소실/다음 OUT→IN까지 대기. alert_keys 소실의 중복 영향도 추가 검증 대상.
- 수정 후보: observed episode/candidate checkpoint + snapshot 검증, 또는 복구 시 cold-start 상태를 명시.
- 기존 로직 변경 위험도: 매우 높음. intrabar 관측을 OHLC 과거봉으로 추측 복원하면 전략 의미가 달라짐.
- 재현 테스트: `test_oz_restart_restores_watches_but_not_candidate`.

## ISSUE-08 — STAFF stale과 Composer 기존 fact 유효성이 분리됨

- 파일/함수: `THE STAFF OF MOSES.py:DataServer.handle`; `manager_KIM.py:_condition_status_locked, _poll_wonbi, _poll_percentile`.
- 재현 조건: TREND UP을 KIM에 저장 후 feed 갱신 없이 300초 전진.
- 실제 결과: STAFF client는 None(error)을 받지만 KIM의 TREND 조건은 계속 참이다. MA/metric과 달리 TREND에 freshness 검사 없음.
- 기대 결과: 시스템 차원의 stale 차단 정책을 정한 경우, 오래된 fact가 새 조합을 성립시키지 않도록 health/freshness를 적용.
- 영향 범위: 오래된 fact와 나중에 온 다른 조건의 조합. 직접 재현은 TREND이며 FVG/SWEEP/WONBI/PERCENTILE도 별도 TTL 부재를 원문에서 확인.
- 수정 후보: feed health/received 시각을 fact 계약으로 전달. 임의 TTL 추가 금지.
- 기존 로직 변경 위험도: 높음. 현재 지속 fact의 수명을 바꾸므로 전략 조건 변경 가능.
- 재현 테스트: `test_stale_staff_does_not_expire_trend_fact`, 대조 `test_metric_freshness_independent_from_trend_state`.

## ISSUE-09 — chain deadline 정확한 경계가 처리 순서에 따라 달라짐

- 파일/함수: `watch_orchestrator.py:handle_generic_trigger, maintenance`.
- 재현 조건: now==stage_deadline에서 이벤트 callback과 maintenance 순서를 교환.
- 실제 결과: callback은 `>` 검사로 허용, maintenance는 `>=`로 먼저 삭제 가능.
- 기대 결과: 동일 시각에 단일 경계 정의를 적용해 실행 순서에 독립적인 결과.
- 영향 범위: 제한시간 정확한 경계의 시간연쇄 다음 단계.
- 수정 후보: 포함/제외 정책 확정 후 비교 기준 통일, event timestamp와 processing time 정책도 분리 검토.
- 기존 로직 변경 위험도: 중간. 경계 포함 여부 자체가 전략 시간 의미임.
- 재현 테스트: `test_timed_chain_deadline_equality_depends_on_scheduler_order`.

## ISSUE-10 — JSONL 미완성 줄을 소비한 뒤 복구하지 못함

- 파일/함수: `strategy_TREND/FVG/SWEEP.py:*CommandWorker.run`; `monitor_OZ.py:OZCommandFileWorker.run, SweepEventFileWorker.run`.
- 재현 조건: 명령 한 줄을 두 부분으로 나눠 append하고 그 사이 consumer 한 주기 실행.
- 실제 결과: 첫 partial line 읽은 즉시 offset 전진, JSON parse 실패. 나머지 부분도 독립 JSON으로 실패하여 command 유실. 직접 실행 증거는 FVG worker.
- 기대 결과: newline으로 끝나지 않은 record는 다음 append까지 대기하거나 직전 offset으로 복귀.
- 영향 범위: writer 중단/partial write, tail reader 재시작 경계. 구독은 refresh로 회복 가능하나 query/일회성 command는 같은 보장 없음.
- 수정 후보: 완전한 line commit 뒤 offset 전진, durable offset·queue 정책 별도 검토.
- 기존 로직 변경 위험도: 중간. 과거 명령 재생 범위를 바꾸지 않아야 함.
- 재현 테스트: `test_jsonl_partial_line_is_consumed_and_lost`.

## ISSUE-11 — 선택 지표 결손 허용은 MT5 생산 단계까지 연결되지 않음 (정적 확인)

- 파일/함수: `MT5/THE_STAFF_OF_MOSES.mq5:WritePipeSnapshot`의 `ok_ema/ok_price/ok_rsi/ok_sto/ok_di` 분기; STAFF `validate_mt5_snapshot`.
- 재현 조건: OHLC는 정상이나 CopyBuffer 그룹 하나가 실패하는 MT5 상황.
- 실제 결과: 소스상 EA가 `return false`하여 해당 snapshot 자체를 보내지 않음. Python의 OHLC-only 요청 허용까지 데이터가 도달하지 않는다. **MT5 런타임 재현은 하지 않았다.**
- 기대 결과: 선택 지표 오류와 OHLC 공급을 분리하려는 의도라면 생산자도 결손 slot을 포함한 snapshot 전달. 현 동작 동결 기준은 송신 중단이다.
- 영향 범위: FVG/SWEEP 등 그 지표를 요청하지 않는 소비자까지 stale 가능.
- 수정 후보: EA optional buffer failure 정책과 Python request별 health 계약을 함께 검토.
- 기존 로직 변경 위험도: 높음. MT5 indicator warmup/불완전 값 노출 시점이 달라짐.
- 증거: EA buffer 분기 원문, `test_optional_indicator_failure_is_request_scoped`는 Python 소비 측 대조만 검증.

## ISSUE-12 — chain NOTIFY 실패에도 완료 상태가 제거됨

- 파일/함수: `watch_orchestrator.py:_advance_locked, handle_generic_trigger`.
- 재현 조건: 마지막 stage가 NOTIFY인 chain에서 Telegram HTTP 500.
- 실제 결과: chain은 callback 전 pop/save되고 callback False는 ACK ok에 반영되지 않는다. KIM 재시작 후에도 chain 없음.
- 기대 결과: 사용자 알림을 전달하지 못했다면 재시도 가능한 완료/전송 대기 상태 보존.
- 영향 범위: chain 조건 충족 알림 유실. 앞 단계 안내 실패와 최종 완료 실패를 구분할 필요.
- 수정 후보: pending-notification 상태/결과 ledger, callback 결과에 따른 완료 commit.
- 기존 로직 변경 위험도: 중~높음. 재시도로 조건 자체가 중복 실행되지 않도록 구분 필요.
- 재현 테스트: `test_chain_notify_failure_removes_chain_before_delivery`.

## ISSUE-13 — 지원하지 않는 SWEEP selector만 있으면 전체 레벨로 확장됨

- 파일/함수: `strategy_SWEEP.py:selector_codes, LEVEL_GROUPS`; `manager_KIM.py:DEFAULT_SWEEP_LEVELS`.
- 재현 조건: selector `("VAH",)` 또는 혼합 `("PDL","VAH")`.
- 실제 결과: VAH 단독은 ALL_LEVEL_CODES로 fallback, PDL+VAH는 PDL만 선택. 현재 detector는 VAH/VAL 레벨을 생성하지 않지만 KIM 기본 목록에는 남아 있다.
- 기대 결과: 지원하지 않는 selector를 분명하게 거절하거나 명시한 지원 목록만 허용. 과거 VAH/VAL 공식을 추정 복원하지 않음.
- 영향 범위: 개인/확장 SWEEP query/watch의 의도보다 넓은 레벨 감시 또는 일부 누락.
- 수정 후보: 요청 경계의 selector 검증/지원 목록 정합.
- 기존 로직 변경 위험도: 높음. 현재 ALL fallback을 이용하는 사용자의 감시 범위가 달라짐.
- 재현 테스트: `test_unknown_sweep_selector_falls_back_to_all`.

추가 동결 관찰: external 상태가 CONFIRMED이면 `update_market`의 ACTIVE 생존 검사에 재진입하지 않는다. `test_oz_atr_gate_boundary_and_confirmed_survival`로 확인했다. 이를 무조건 결함으로 단정하거나 재검사 조건을 추가하지 않았다.
