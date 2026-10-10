# Remediation 실행 보고서

> 후속 상태: 아래 기록에서 미해결이었던 NEW-01은 후속 수정 단위 `13-new01`에서 해결했다. [NEW-01 수정 결과](<C:/Users/hpk14/Desktop/모세의지팡이 - 지피티/NEW01_REMEDIATION_REPORT.md>)를 참고한다. 아래 70개 전체 PASS는 당시 결과로 보존하며, 후속 작업은 사용자 지시에 따라 관련 테스트만 실행했다.

기준: 2026-09-19, 사용자가 확정한 ISSUE-01~13 계약. 최초 감사 문서·fixture·28개 파일의 원본 해시와 최초 49개 테스트 보고서는 보존했다. 수정은 12개의 되돌릴 수 있는 단위로 기록했다. 각 단위의 관련 테스트가 통과한 뒤 다음 단위로 진행했다.

**최종 전체 regression: 70개 PASS, failure 0, error 0, skip 0.** 모든 코드 변경 후 전체 suite를 한 번 실행했고 63.400초가 소요됐다. 최초 suite의 정상 동작 검증을 유지하면서 승인된 결함 동결 assertion을 수정 후 계약 assertion으로 바꾸고 경계·재시작 검증을 추가했다. Python 3.12.14 / NumPy 2.3.5 / pandas 3.0.1 환경이다.

원본 28개 중 9개 수정, 공통 모듈 2개 추가. 단계별 SHA-256 연결 및 최종 파일 해시 오류 0개다. `command_interpreter.py`, SPECIAL 구현, 설정 파일, 개별 지표 계산 소스는 수정하지 않았다.

**완료 범위의 주의점:** ISSUE-09의 기존 대상인 개인 `watch_orchestrator` SEQUENTIAL deadline을 수정했다. 공식 설정의 별도 `ConfigTimedChainSpec.max_gap_sec` 경로에도 유사한 처리 시각 의존성이 발견됐으며 아래 NEW-01로 남아 있다. 전체 시스템의 모든 시간창이 동일 계약으로 통합됐다는 뜻은 아니다. MT5 실행 검증과 외부 알림 결과 불명 상태의 처리도 아래 제한 사항을 확인해야 한다.

## 1. 실행 순서와 관련 테스트 결과

각 폴더의 `before.zip`, `changes.patch`, `changes.json`, `tests.txt`가 변경 전 소스, 패치, 전후 해시, 관련 테스트 실행 증거다. 횟수는 해당 단위 실행의 test method 수이며 다른 단위와 중복될 수 있다. 실패 재현 로그가 있는 단위는 이를 덮어쓰지 않고 별도로 보존했다.

| 순서 / 변경 단위 | ISSUE | 관련 결과 |
|---|---|---:|
| 01-issue10 | 10: JSONL 완성 줄 | 5/5 PASS |
| 02-issue06 | 06: watch 소유권 | 5/5 PASS |
| 03-delivery-identity | 03 및 02·12의 선행 처리 ID·수신 중복 방어 | 6/6 PASS |
| 04-issue02 | 02: TREND pending / ACK retry | 5/5 PASS |
| 05-issue12 | 12: chain 완료 알림 pending | 6/6 PASS |
| 06-fact-reconciliation | 01·04·05: 현재 fact·revision·무효화 | 14/14 PASS |
| 07-source-health | 08·11: source health와 선택 지표 결손 | 10/10 PASS |
| 08-issue07 | 07: OZ 관측 상태 복원 | 10/10 PASS |
| 09-issue13 | 13: 실제 지원 selector만 허용 | 7/7 PASS |
| 10-issue09 | 09: 최종 완료 시각 deadline | 9/9 PASS |
| 11-issue12-filter | 12: FILTER 완료 알림 경로 보완 | 5/5 PASS |
| 12-verification | 원본 해시 + 승인된 변경 이력 검증 | 1/1 PASS |

증거: [단위별 변경 기록][units], [전체 실행 로그][full-log], [기계 판독용 결과·최종 해시][full-json], [최초 감사 결과][baseline-json].

## 2. ISSUE별 변경·파일·회귀 검증

모든 테스트는 [audit/test_baseline.py][tests]에 있다. 아래 이름은 대표 추가·수정 테스트다. 각 단위 `tests.txt`와 최종 JSON에는 실제 실행한 전체 이름이 있다.

### ISSUE-10 — 미완성 JSONL 소비

- 변경: binary tail에서 newline으로 끝난 record만 offset을 전진시킨다. UTF-8 문자가 분할돼도 완성 전에 해석하지 않는다. 완성된 malformed JSON은 건너뛰고 다음 정상 record를 처리한다.
- 수정 파일: [TREND][trend], [FVG][fvg], [SWEEP][sweep], [OZ][oz]의 총 5개 command/event worker.
- 테스트: `test_jsonl_partial_line_waits_for_completion`, `test_jsonl_all_workers_utf8_partial_and_malformed_records`.
- 결과: 01 단위 5 PASS. command startup EOF 및 SWEEP event startup replay 0 정책은 유지했다. 파일 회전·복수 writer의 바이트 교차 기록 문제까지 해결한 것은 아니다.

### ISSUE-06 — 재시작 PING에서 독립 watch 삭제

- 변경: Manual/Generic watch에 `watch_owner`를 저장·복원한다. KIM이 생성한 child/chain만 KIM 소유로 표시하며, 이전 상태는 chain/spec 근거가 있는 항목만 추론한다. ID prefix만으로 독립 watch를 삭제하지 않는다.
- 수정 파일: [KIM][kim] `_push`, `_persisted_owned_oz_ids`, `_refresh_oz_after_ping`; [OZ][oz] watch spec·registry·command 처리.
- 테스트: `test_unowned_oz_watch_survives_restart_ping`, `test_oz_ping_cancels_only_owned_stale_watches`, `test_watch_registration_restart_and_ping_reconcile`.
- 결과: 02 단위 5 PASS. 독립 watch 보존과 종료된 KIM 소유 watch 정리를 함께 검증했다.

### ISSUE-03 — 동일 최종 알림 중복 전달

- 변경: 안정적인 OZ alert ID, KIM 수신 receipt, recipient별 notification ledger를 도입했다. 동일 완료 ID는 KIM/OZ 재시작 및 ACK 유실 후에도 확인된 Telegram 전달을 반복하지 않는다. 다른 ID의 같은 문구는 정상 별도 알림이다.
- 수정 파일: 신규 [durable_protocol.py][protocol], [KIM][kim] `_handle_event`, `NotificationService.send`, `send_telegram`; [OZ][oz] `TelegramSender`, `try_fire`.
- 테스트: `test_fact_duplicate_and_final_alert_duplicate_are_different`, `test_lost_final_ack_does_not_duplicate_delivery`, `test_delivery_identity_per_recipient_and_unknown_outcome`.
- 결과: 03 단위 6 PASS. ID가 없는 legacy event에 임의로 동일성을 부여하지 않는다. 외부 전달 결과가 불명확한 상태의 보류 정책은 5절 참고.

### ISSUE-02 — TREND ACK 실패 후 동일 상태 미재전송

- 변경: 관측된 상태 전이를 안정 ID와 함께 durable pending에 먼저 넣는다. ACK 성공 전에는 완료로 소비하지 않고 동일 ID로 재전송한다. pending은 TREND 재시작 후에도 이어진다.
- 수정 파일: [TREND][trend] `TrendEngine.run_watch_once`, 초기화; 선행 [protocol][protocol]·[KIM][kim] 수신 계약 사용.
- 테스트: `test_failed_trend_ack_retries_unchanged_state`, `test_trend_pending_survives_restart_and_lost_ack`, 기존 score 검증 `test_trend_fixture_and_duplicate_suppression`.
- 결과: 04 단위 5 PASS. 순수 TREND 계산식·UP/DOWN 판정은 변경하지 않았다.

### ISSUE-12 — chain 삭제 뒤 완료 알림 소실

- 변경: chain 완료 전이와 notification intent를 같은 상태 파일에 저장한다. 전송 실패 시 완료된 전략을 재실행하지 않고 pending 알림만 재시도한다. 전달 성공 기록 후 pending 삭제 직전 crash도 recipient ledger로 중복 방어한다. SEQUENTIAL·UNORDERED에 이어 FILTER의 NOTIFY 완료도 동일 경로로 연결했다.
- 수정 파일: [watch_orchestrator][orchestrator] `_queue_completion_locked`, `retry_notifications`, 완료 처리·상태 저장; 선행 [KIM][kim] notification ID 사용.
- 테스트: `test_chain_notify_failure_preserves_pending_completion`, `test_chain_delivery_commit_crash_does_not_resend`, `test_filter_notify_failure_preserves_pending_completion`.
- 결과: 05 단위 6 PASS, 11 단위 5 PASS. FILTER 추가 테스트는 수정 전 pending 0건으로 실패하는 것을 먼저 확인했다.

### ISSUE-01 — KIM 재시작 후 변하지 않은 TREND fact 부재

- 변경: producer가 현재 상태의 complete snapshot을 반복 제공한다. KIM의 비어 있는 메모리를 상태 전이 발생 없이 복구한다. producer generation + sequence revision으로 역순·이전 세대 delta를 방어한다. Composer signature도 저장해 재동기화만으로 같은 조합을 재알림하지 않는다.
- 수정 파일: [TREND][trend], [KIM][kim], [durable_protocol][protocol].
- 테스트: `test_manager_restart_recovers_unchanged_trend_fact`, `test_reconciliation_after_manager_restart_does_not_renotify`, `test_fact_snapshot_empty_partial_and_out_of_order`.
- 결과: 공통 06 단위 14 PASS. PING·주기 refresh의 기존 등록 회복 경로도 유지했다.

### ISSUE-04 — FVG 재시작 후 KIM의 이전 touch 잔류

- 변경: FVG 현재 추적 집합과 touch 상태를 권위 있는 snapshot으로 제공한다. 유효한 빈 집합은 해당 scope의 과거 fact를 제거하고, 불완전 snapshot은 전체 대체에 사용하지 않는다. 이미 있던 zone을 재시작 때문에 CREATED로 꾸미지 않는다. SWEEP도 현재 watch별 active 집합을 동일 계약으로 재동기화한다.
- 수정 파일: [FVG][fvg] `process_watch_result`, [SWEEP][sweep], [KIM][kim] fact snapshot 처리, [protocol][protocol].
- 테스트: `test_fvg_restart_before_fill_reconciles_kim_touch`, `test_manager_restart_recovers_unchanged_fvg_and_sweep`, `test_fvg_manager_restart_then_engine_restart_repairs_touch`, 기존 live/closed fill·ATR·30봉·latest-three 검증.
- 결과: 공통 06 단위 14 PASS. 기존 zone 검출·touch/fill 의미는 유지했다.

### ISSUE-05 — SWEEP 무효화 뒤 과거 replay로 부활

- 변경: OZ가 watch/direction/level별 invalidation 시점을 영속화하고 그 이전·동일 시각 TOUCH를 거절한다. 새로운 시점의 cycle은 허용한다. KIM용 현재 상태 snapshot을 OZ lifecycle JSONL로 재발행하지 않는다.
- 수정 파일: [OZ][oz] `ExternalLiquidityController`, [SWEEP][sweep], [KIM][kim], [protocol][protocol].
- 테스트: `test_external_invalidation_then_old_duplicate_stays_invalid`, `test_sweep_restart_before_and_after_invalidation`, `test_sweep_closed_touch_restart_restores_without_oz_republish`, `test_oz_restart_external_state_replay_keeps_touch_identity`.
- 결과: 공통 06 단위 14 PASS. tombstone을 임의 TTL로 삭제하지 않는다.

### ISSUE-08 — stale fact로 신규 Composer 조합 성립

- 변경: 전략 fact 값과 source/feed health를 분리한다. source가 STALE/CLOSED/UNAVAILABLE이거나 epoch 동기화가 안 되면 조건은 `None`(UNKNOWN/SUSPENDED)이며 새 조합에 사용할 수 없다. 기존 TRUE fact를 FALSE로 바꾸거나 지우지 않는다. feed gap·재연결·지표 validity 변경은 source epoch를 갱신하며, producer 현재 snapshot과 STAFF epoch가 다시 일치해야 READY가 된다.
- 수정 파일: [STAFF Python][staff], [KIM][kim] `StaffClientV2.health`, `_condition_source_usable`, 각 local producer binding; [TREND][trend], [FVG][fvg], [SWEEP][sweep], [protocol][protocol].
- 테스트: `test_stale_staff_does_not_expire_trend_fact`, `test_health_gap_without_prior_stale_poll_requires_resync`, 기존 metric freshness 검증.
- 결과: 공통 07 단위 10 PASS. 공통 fact TTL을 추가하지 않았다. 원래 있던 metric·MA freshness 규칙은 유지했다.

### ISSUE-11 — optional CopyBuffer 실패로 전체 OHLC 공급 중단

- 변경: [MT5 PublishFeed][mql]에서 선택 지표 실패 때문에 전체 송신을 중단하지 않는다. 실패한 그룹 slot은 `EMPTY_VALUE`로 명시하고, short-circuit된 CopyBuffer 배열도 실패 그룹에서 접근하지 않는다. Python은 marker를 NaN 및 `indicator_validity=False`로 전달한다. 소비자는 실제 요청한 지표의 유효성을 검증하며 OHLC-only 요청은 정상 처리한다.
- 수정 파일: [THE_STAFF_OF_MOSES.mq5][mql], [STAFF Python][staff]; 08과 health 계약 공유.
- 테스트: `test_mt5_optional_missing_slots_are_request_scoped`(EMA/PRICE/RSI/STO/DI 각 그룹의 실제 SMOS binary slot), `test_mt5_producer_guards_every_optional_slot`(MQL 분기·slot 정적 확인), `test_optional_indicator_failure_is_request_scoped`.
- 결과: 공통 07 단위 10 PASS. 과거 지표 값을 재사용하거나 0 등 정상 값으로 결손을 위장하지 않는다. 실제 EA 컴파일·CopyBuffer 실행 실패 주입은 하지 못했다. 최초 감사의 함수명 `WritePipeSnapshot`보다 실제 수정 지점은 `PublishFeed`가 정확하다.

### ISSUE-07 — OZ setup 관측 상태 소실

- 변경: 실제 실행에서 등록된 candidate/percentile candidate/episode, 이전 관측 상태, HMA cross extremes, environment identity, alert key, completion 시각을 profile별 checkpoint로 저장·복원한다. 복원 직후 현재 snapshot으로 기존 cancellation/expiry를 적용한다. 중단 구간 OHLC를 다시 해석해 관측하지 않은 OUT→IN 또는 cross 후보를 만들지 않는다. 동일 완료·recipient ID를 유지해 재시작 alert 중복을 방어한다.
- 수정 파일: [OZ][oz] 관측 상태 encoder/decoder, checkpoint wrapper, `OZMonitor` 초기화·cycle·cross/out-in·완료 처리.
- 테스트: `test_oz_restart_restores_observed_candidate`, `test_oz_resume_does_not_invent_unobserved_out_in`, `test_oz_restored_candidate_cancels_on_opposite_cross`, `test_oz_candidate_restart_replay_finishes_once`, `test_observed_state_roundtrip_all_profiles_and_sides`, `test_restored_oz_timer_is_not_restarted`.
- 결과: 08 단위 10 PASS. 6개 profile × 양 방향의 상태 roundtrip, 재시작 후 완료 1회, 원래 타이머·반대 cross 취소를 검증했다. 관측 cycle/checkpoint 및 alert 직전 저장을 경계로 하며, CPU 명령어 사이의 임의 전원 차단까지 모든 메모리 관측을 보장하는 WAL은 아니다.

### ISSUE-13 — 미지원 selector가 ALL로 확장

- 변경: 신규 [sweep_selectors.py][selectors]를 canonical 목록으로 사용한다. VAH/VAL 기본값·alias·지원 목록을 제거했다. 미지원값이 지원값/ALL과 섞여도 전체 입력을 명시적으로 거절한다. legacy 저장값 중 잘못된 watch/spec은 로그와 함께 제외하고 다른 정상 항목은 복원한다.
- 지원: PDH, PDL, PWH, PWL; 4H·8H의 PREV high/low; SESSION의 현재/이전 high/low. 그룹명 및 개별 코드만 허용한다. 생략은 해당 계층의 기존 지원 기본값, 명시적 ALL은 지원 전체, 명시적 빈 목록은 오류다.
- 수정 파일: [selectors][selectors], [SWEEP][sweep], [KIM][kim], [command_aliases.json][aliases]. 미지원 입력 거절·회귀 테스트의 VAH/VAL 문자열은 남는다.
- 테스트: `test_unknown_sweep_selector_is_rejected`, `test_sweep_legacy_invalid_selector_does_not_discard_valid_watch`, 기존 outermost 선택·consumed·SPECIAL load·E2E 검증.
- 결과: 09 단위 7 PASS. VAH/VAL 계산을 복원하지 않았다.

### ISSUE-09 — 최종 완료 시각과 deadline

- 변경: 개인 SEQUENTIAL chain의 event/completion timestamp가 deadline 이하이면 허용하고 초과하면 만료한다. 중간 단계가 완료돼도 앞서 열린 최종 deadline을 지우거나 연장하지 않는다. 최종 OZ도 chain deadline과 최종 window 중 더 이른 경계를 적용한다. 시각이 없는 최종 timed event는 정상 수신 시각으로 위장하지 않는다.
- maintenance: wall time이 지났다는 사실을 표시하되 지연 수신 event를 판정할 chain ID/deadline을 보존한다. 재시작 복원·PING refresh도 해당 판정 상태를 유지한다. 무한 지연 가능성에 대한 임의 보존 TTL은 도입하지 않았다.
- 수정 파일: [watch_orchestrator][orchestrator] callback·advance·maintenance·final filtering, [KIM][kim] OZ 최종 event filtering·refresh, [OZ][oz] 실제 관측 event timestamp.
- 테스트: `test_timed_chain_deadline_equality_is_order_independent`, `test_chain_deadline_uses_event_time_after_maintenance_and_restart`, `test_intermediate_stage_does_not_extend_final_deadline`, `test_final_oz_completion_must_be_within_chain_deadline`, `test_deadline_boundary_matrix`(직전/동일/직후 × maintenance 순서 × 재시작 유무 12개 조합).
- 결과: 10 단위 9 PASS. FILTER 겹침창 및 UNORDERED latch의 기존 expiry 의미는 유지했다. 공식 설정의 별도 시간연쇄 경로는 NEW-01 참고.

## 3. 적용된 공통 전달·상태 계약

| 계약 | 구현 및 의미 |
|---|---|
| event identity | producer가 ID를 부여하고 재시도에서 재사용한다. 수신 receipt는 family/kind/event ID/recipient/watch/chain/stage를 구별한다. FACT_SNAPSHOT은 receipt 완료 때문에 복원이 막히지 않도록 revision으로 처리한다. |
| ACK | `ok`/`accepted`는 이벤트 처리 또는 durable 완료 intent 수락을 뜻할 수 있다. 최종 알림 `delivered`는 일반 전달 경로에서 recipient ledger의 성공을 뜻한다. 기존 internal/suppressed/expired OZ 응답의 `delivered=True`는 watch 소비 ACK이므로 Telegram 성공으로 해석하면 안 된다. |
| notification state | `pending`은 확정 실패 후 재시도 대상, `sending`은 호출 직전 저장, `delivered`는 HTTP 성공 후 저장이다. 재시작 시 `sending`/불명확 결과는 자동 재전송하지 않는다. |
| fact revision | `[generation, sequence]`를 producer 상태 파일에서 이어가며 KIM이 scope별 watermark를 저장한다. complete snapshot의 scope는 family/symbol/tf, SWEEP은 watch ID까지 포함한다. 유효한 empty snapshot과 공급 실패를 구별한다. |
| source health | STAFF의 `SOURCE_HEALTH` 응답과 DataFrame attrs에 source epoch 및 indicator validity를 둔다. consumer가 producer snapshot binding과 현재 epoch를 대조한다. 기존 source/fact의 전략 상태와 별도다. |
| persistence | protocol records는 schema 1, temporary file + flush/fsync + replace. OZ external 상태는 version 5에 invalidation 기록 추가, timed chain은 version 2에 pending 추가, observed checkpoint는 schema 1이다. |

추가/변경되는 런타임 상태 파일은 운영 `program/logs` 아래의 `event_receipts.json`, `notification_deliveries.json`, `oz_outgoing_events.json`, `trend_pending_events.json`, `trend_stream.json`, `fvg_stream.json`, `sweep_stream.json`, `fact_revisions.json`, `composer_signatures.json`, `oz_observed_<profile identity>.json`, 기존 `oz_external_liquidity_state.json`, `composer_timed_chains.json`, OZ watch 상태 파일이다. 테스트는 임시 복사본의 logs를 사용했으며 실제 운영 상태를 생성·변경하지 않았다.

새 protocol record가 손상되거나 지원하지 않는 schema이면 정상 빈 기록으로 덮어쓰지 않고 실패시킨다. 운영자는 해당 원장을 보존한 채 복구해야 한다. producer generation 파일만 초기화하고 KIM revision 파일을 유지하면 오래된 세대로 거절될 수 있으므로 이 파일들의 독립 삭제를 복구 절차로 사용하면 안 된다.

## 4. 새로 발견된 문제 / 미해결 항목

### NEW-01 — 공식 ConfigTimedChain의 별도 처리 시각 기반 만료

- 파일/함수: [manager_KIM.py][kim] `_config_chain_expiry_status`, `_handle_config_chain_trigger`, `_maintain_config_timed_chains`, `_load_config_chain_state_for_specs_locked`.
- 재현 조건: 공식 SEQUENTIAL 설정에서 A를 저장한 뒤 B의 event timestamp는 deadline 이하이나 callback 처리 또는 maintenance 시각은 deadline 이상. 또는 그 시각에 KIM 재시작.
- 실제 결과: 정적 경로 확인상 `now >= deadline`에서 WAIT_B가 제거·거절되며 restore도 wall time에 따라 제외한다. 따라서 event가 제시간에 완성됐어도 거절될 수 있다. 이번 suite는 이 공식 분기를 실행 재현한 테스트를 포함하지 않는다.
- 기대 결과: 사용자 확정 최종 completion 계약을 공식 경로에도 적용한다면 event time과 durable 판정 상태로 결정해야 한다.
- 영향 범위: 별도 ConfigTimedChain, post-touch/FVG 단계, 공식 OZ final window. 개인 watch_orchestrator 수정 결과와 구별해야 한다.
- 수정 후보: 공식 chain에 완료 deadline·원래 event identity를 영속화하고 유지·복원·최종 alert filtering을 함께 연결한다. `MAX_GAP_BARS`, post-touch 및 SPECIAL 정책과 교차하는 경로의 특성 테스트를 먼저 추가한다.
- 기존 로직 변경 위험도: 높음. 원래 ISSUE-09의 파일/함수 범위를 넘어 공식 A/B/bar/post-touch 상태 전이를 건드린다. 이번 수정에 임의로 포함하지 않았다.

### 외부 전송 결과 불명 — 보류 상태의 수동 해소 절차 필요

- 파일/함수: [KIM][kim] `NotificationService.send`; notification ledger.
- 재현 조건: Telegram 요청을 보냈으나 응답이 유실되거나, 성공 직후 ledger의 delivered 저장 전에 종료.
- 실제 결과: `sending` 기록과 pending intent가 남고 자동 재송신하지 않는다. 테스트에서 이 보류 동작을 검증했다.
- 기대 결과: 확인된 중복 발송을 방지하면서, 외부 결과를 확인할 수 있을 때만 완료/재시도를 결정해야 한다. 현재 Telegram API 호출에 application event ID의 서버 멱등 처리는 없다.
- 영향 범위: 해당 event/recipient 알림. 전략 조건을 다시 실행하지 않는다.
- 수정 후보: 운영자가 전달 증거를 확인하는 reconcile 절차와 감사 이력을 추가한다. 이번 단계에서 불명확 결과를 성공이나 실패로 추정하지 않았다.
- 기존 로직 변경 위험도: 중간. 무조건 자동 retry를 선택하면 중복 알림 위험이 돌아온다. 외부 exactly-once를 달성했다고 주장하지 않는다.

### 보존 기록 크기 및 만료 chain 정리 정책

- 파일/함수: [protocol][protocol] `Records`, [OZ][oz] invalidation·outgoing 기록, [watch_orchestrator][orchestrator] `maintenance`.
- 재현 조건: 장기간 많은 고유 event 또는 완료되지 않은 expired chain이 누적.
- 실제 결과: 현재 append 성격의 원장과 지연 event 판정 상태를 자동 GC하지 않는다. JSON 원장은 갱신 시 파일을 재기록한다. 장기 부하·디스크 용량 테스트는 하지 않았다.
- 기대 결과: replay/late delivery 허용 범위를 보존하는 안전한 정리 정책이 필요하다.
- 영향 범위: 상태 파일 크기, 저장 지연, expired watch의 보존 비용.
- 수정 후보: producer별 replay watermark와 전달 지연 상한을 계약으로 정한 뒤 원장 압축·안전한 watch 해제·tombstone GC를 별도로 적용한다.
- 기존 로직 변경 위험도: 높음. 임의 TTL로 제거하면 중복 방어와 지연 완료 판정이 다시 깨질 수 있어 이번에 추가하지 않았다.

## 5. 검증 범위와 운영 경계

- offline fixture는 실제 SMOS header/45-slot payload를 만들고 실제 Python source 복사본을 실행한다. 전송 경계는 pickle round trip과 실패/ACK 유실 주입, 시간은 고정 clock, Telegram은 응답을 제어하는 adapter다.
- 실제 MT5·Telegram·Windows named pipe·libzmq scheduling은 실행하지 않았다. MetaEditor/MT5 compiler가 없어 MQL compile 및 실제 CopyBuffer 실패 주입은 미검증이다. Python 경계·MQL 실패 분기와 모든 optional slot guard 검증을 MT5 실행 검증으로 표시하지 않는다.
- 새 process 간 계약은 함께 배포해야 한다. 변경 전 producer는 source epoch/snapshot을 제공하지 않으므로 새 KIM이 UNKNOWN/SUSPENDED로 볼 수 있다. mixed-version 무중단 rolling upgrade를 검증하지 않았다.
- 단일 서비스별 한 프로세스가 자기 원장을 쓰는 기존 운영 배치를 전제로 한다. 공통 Records의 lock은 프로세스 내부 lock이며, 같은 서비스 이중 실행의 원장 동시 쓰기를 해결하지 않는다.
- OZ는 실제 checkpoint에 저장된 관측을 복원한다. 중단 시간 동안 관측하지 않은 전략 후보를 복원한다고 주장하지 않는다.
- 전략적 개선, 삭제된 VAH/VAL 기능 복원, 기존 indicator 계산식 변경, FILTER/UNORDERED 전략 의미 재설계는 하지 않았다.

## 6. 되돌리기와 재실행

이 작업 공간은 Git 저장소가 아니므로 commit 대신 [단위별 archive와 patch][units]를 남겼다. 의존 변경은 뒤 단위부터 역순으로 되돌린다. 해당 단위의 `changes.json`에 기록된 파일만 `before.zip`의 이전 내용으로 복구하고, 그 단위에서 신규 생성한 파일은 제거한다. archive 전체를 무조건 덮어쓰지 않는다. 공유 protocol 도입 단위만 단독 제거하면 후속 consumer가 깨질 수 있다.

운영 적용 이후 되돌릴 때는 프로세스를 정지하고 소스와 `program/logs` 상태를 함께 백업해야 한다. 이번 archive는 코드와 audit 대상 파일의 변경 전 자료이며 실제 운영 중 이후 생긴 상태 원장을 포함하지 않는다. 이전 binary가 새 상태를 저장하면서 필드를 버릴 수 있으므로 상태 호환 검토 없이 binary만 교체하지 않는다.

전체 suite 재실행 진입점: [audit/run_tests.py][runner]. 관련 단위는 [audit/test_baseline.py][tests]의 개별 test method를 지정할 수 있다. 최신 결과는 `remediation_test_report.json`에 기록되며 최초 `test_report.json`을 덮어쓰지 않는다. 이번 작업의 전체 실행 횟수는 1회다.

[kim]: <C:/Users/hpk14/Desktop/모세의지팡이 - 지피티/program/manager_KIM.py>
[oz]: <C:/Users/hpk14/Desktop/모세의지팡이 - 지피티/program/monitor_OZ.py>
[trend]: <C:/Users/hpk14/Desktop/모세의지팡이 - 지피티/program/strategy_TREND.py>
[fvg]: <C:/Users/hpk14/Desktop/모세의지팡이 - 지피티/program/strategy_FVG.py>
[sweep]: <C:/Users/hpk14/Desktop/모세의지팡이 - 지피티/program/strategy_SWEEP.py>
[staff]: <C:/Users/hpk14/Desktop/모세의지팡이 - 지피티/program/THE STAFF OF MOSES.py>
[mql]: <C:/Users/hpk14/Desktop/모세의지팡이 - 지피티/program/MT5/THE_STAFF_OF_MOSES.mq5>
[orchestrator]: <C:/Users/hpk14/Desktop/모세의지팡이 - 지피티/program/watch_orchestrator.py>
[protocol]: <C:/Users/hpk14/Desktop/모세의지팡이 - 지피티/program/durable_protocol.py>
[selectors]: <C:/Users/hpk14/Desktop/모세의지팡이 - 지피티/program/sweep_selectors.py>
[aliases]: <C:/Users/hpk14/Desktop/모세의지팡이 - 지피티/program/command_aliases.json>
[tests]: <C:/Users/hpk14/Desktop/모세의지팡이 - 지피티/audit/test_baseline.py>
[runner]: <C:/Users/hpk14/Desktop/모세의지팡이 - 지피티/audit/run_tests.py>
[units]: <C:/Users/hpk14/Desktop/모세의지팡이 - 지피티/audit/remediation>
[full-log]: <C:/Users/hpk14/Desktop/모세의지팡이 - 지피티/audit/results/remediation_full_suite.txt>
[full-json]: <C:/Users/hpk14/Desktop/모세의지팡이 - 지피티/audit/results/remediation_test_report.json>
[baseline-json]: <C:/Users/hpk14/Desktop/모세의지팡이 - 지피티/audit/results/test_report.json>
