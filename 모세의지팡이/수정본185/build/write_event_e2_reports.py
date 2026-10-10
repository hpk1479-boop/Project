"""Publish E2 completion evidence only after all required scenarios exist."""
from event_e2_common import *
external=read(OUT/'external_comparison.json')
g1=read(OUT/'baseline_signature_compare.json');preserved=read(OUT/'preservation.json')
integrity=read(OUT/'integrity_registration.json');startup=read(OUT/'startup_inventory.json')
assert external['completed_scenarios_passed'] and not external['unclassified_difference_count']
assert (OUT/'user_stop_processes.json').exists()  # User narrowed the final evidence scope.
assert g1['passed'] and preserved['passed'] and integrity['chain_valid'] and not integrity['added_diagnostics']
assert startup['polling_inputs_unchanged'] and not startup['errors']
rows=external['scenarios']
table=['| 구간 | 초 | 폴링 알림 | 이벤트 LIVE / 재생 | 시장 조건 알림 (등록 확인 제외) | 정확 일치 | 승인 대상 차이 |',
       '|---|---:|---:|---:|---:|---|---:|']
for r in rows:table.append(f"| {r['case']} ({r['symbol']}) | {r['seconds']} | {r['polling_notifications']} | {r['event_live_notifications']} / {r['event_replay_notifications']} | {r['event_market_notifications']} | {r['live_replay_signals_exact']} | {len(r['differences'])} |")
measurement=['| 보존 캡처 | 묶음 수 | 평균 ms | p99 ms |', '|---|---:|---:|---:|']
for r in rows:
    if r['measurement']:
        m=r['measurement'];measurement.append(f"| {r['case']} | {m['bundles']} | {m['mean_ms']:.3f} | {m['p99_ms']:.3f} |")
changes='\n'.join('- `Part1/'+r['file']+'`' for r in integrity['changed_files'])
differences=['# 폴링 → 이벤트 알림 차이 목록 (E2)','','원시 시각·문구·수신자를 변경하지 않고 대조했다. 아래 차이는 자동 승인하지 않는다. 기존 기준선은 보존한다.','']
for r in rows:
    differences+=['## '+r['case'],'']
    for i,d in enumerate(r['differences'],1):
        differences += [f"### {i}. {d['strategy']} — {d['classification']}",'',
            f"- 폴링 시각: `{d.get('polling_time')}` / 이벤트 시각: `{d.get('event_time')}` (Unix seconds)",
            f"- 수신자: `{d['recipient']}`",f"- 근거: {d.get('reason')}",'', '```text', d['message'], '```','']
    if not r['differences']:differences+=['차이 없음.','']
(ROOT/'차이목록_EVENT_E2.md').write_text('\n'.join(differences),encoding='utf-8')
report=f'''# Event Engine E2 수정내역

수정본16 전체를 독립 복사한 **수정본17**에서 전략 이전을 구현했다. 기본 폴링 LIVE는 그대로이며 이벤트판은 opt-in이다. **사용자 지시로 남은 실행을 중단하고 완료된 결과만으로 E2 작업을 종료한다. E3 미착수.** 완료된 비교 구간과 회귀의 새 실패는 0이며, 원래 계획한 모든 게이트가 완료됐다는 의미는 아니다. 폴링→이벤트의 정당한 시점 차이 {external['approval_pending_difference_count']}건은 사용자 승인 대상으로 남긴다. 중단 지시를 시점 차이의 승인으로 해석하지 않으며 새 기준선을 임의로 확정하지 않는다.

## 이전 결과

| 대상 | 이벤트 소유자 | 재사용한 기존 판정 경로 |
|---|---|---|
| OZ 후보/B0/관측/프로필 | OZ_STATE | OZMonitor.run_once 및 기존 후보·trigger 함수, external/manual controller |
| FVG 활성/채움/접촉/만료 | FVG_STATE | FVGEngine.evaluate/process_watch_result |
| SWEEP 상태/터치 기억 | SWEEP_STATE | SweepEngine.run_spec_once/cleanup 및 ExternalLiquidityDetector |
| INDICATOR | INDICATOR Consumer | IndicatorEngine.run_watch_once/run_query |
| 일반 Watch/MA | WATCH_CONDITIONS | GenericConditionMonitor.evaluate_once (기존 루프 본문 분리), 기존 controller |
| OZ/FVG/SWEEP 알림 | OZ/FVG/SWEEP Consumer → COMPOSER | 기존 Fact 내용/세대/순번을 동일 조합 경로로 전달 |
| SPECIAL1~7/복합·시간연쇄 | COMPOSER / 종목별 CompositionKernel | canonical SPECIAL register/handler, ComposerManager, WatchOrchestrator |

OZ OUT→IN/HMA/B0 상태 하나를 먼저 이전하여 수정본16과 자정·체크포인트 포함 비교한 뒤 나머지를 진행했다. `decision_source_review.json`에서 기존 함수 752개의 AST가 동일함을 확인했다. 변경된 기존 함수는 생성자 주입, 상태/경로 복원, 전송 대역, source_time completion 및 기존 루프의 1회 실행 경계다. 원비/ATR/FVG 계산식을 다시 구현하지 않았다.

Composer 묶음은 사용자 후속 승인대로 연속 오류에도 계속 처리한다. 5번째 연속 오류에서 DEGRADED 신호를 한 번 내며 11회 실패·체크포인트·다른 Consumer 계속 동작을 시험했다. SPECIAL별 분리 전 자동 중지는 적용하지 않는다.

## 시작 입력과 쓰기 경계

`시작경로_대조_EVENT_E2.md`에 전체 파일과 초기화 순서를 대조했다. config, 사용자 BTCUSD, config 지정 별칭, SPECIAL 설정, 개인/공식 연쇄, 활성 자식, 전송/중복 ledger, TREND/FVG/SWEEP registry·generation·pending, OZ observed, SPECIAL4, SWEEP 이력 복원을 연결했다. 명령 JSONL은 폴링과 같이 기존 내용을 재실행하지 않는다.

실제 수정본16 입력 상태 파일 {startup['state_file_count']}개와 SWEEP 이력 {startup['sweep_history_events']}건을 복원했으며 오류 0, 읽은 파일 SHA256 변동 0이다. 처리는 메모리 상태에만 쓰고 명시적인 파일 출력은 polling program 트리 밖 event_state 경로로 제한한다. 이 테스트에서 Telegram은 전송하지 않았다.

## 외부 동작 검증

{chr(10).join(table)}

**XAU 1,200초 비교는 미완료다.** 최종 소스의 LIVE는 마지막 출력 660초, 재생은 780초 지점에서 사용자 요청으로 중단했다. 완료된 최종 결과 파일이 없으므로 부분 진행을 통과 결과로 사용하지 않는다. 앞선 다른 소스의 중간 결과도 대신 사용하지 않았다. BTC 1,200초는 중단 전에 양쪽 모두 끝났으므로 포함했다. 네 구간 전체 알림은 64건이며 등록 확인 44건과 시장 조건 알림 20건을 구분했다. 중단 PID/명령은 `user_stop_processes.json`에 보존한다.

비교 대상은 알림 signal_id·시각·방향·문구·수신자 전체다. LIVE는 파이프 reader 경계 → STAFF → 엔진, 재생은 MSP3/합성 bytes → STAFF → 같은 엔진이다. 검증은 네트워크를 차단하고 논리 전송 확인만 반환했다. 실제 OS named pipe 경계는 유지한 E1 테스트가 함께 검사한다.

전후 비교는 수정본16 전체 Part1 program을 시험 전용 폴더에 복사하여 실행했다. 구 모듈과 신 모듈을 섞지 않았다. 폴링과 이벤트 사이 차이 원문은 `차이목록_EVENT_E2.md`, 구조화 결과는 `검증결과/event_e2/external_comparison.json`이다. 버그로 남긴 차이 0건이다. SPECIAL1~7은 모두 등록했으나 모든 전략이 각 구간에서 알림을 냈다는 의미는 아니다. 승인 대상 등록 확인 메시지의 발생 시점/순서 차이를 전략 신호와 구별해 기록했다.

합성 240초 전체 전략 checkpoint 이후 SIGNAL이 완전히 일치했다. 실제 Watch의 자정 예약 시작/다른 종목의 시계 격리/재개 연속성도 통과했다. 만료 identity를 보존하는 연쇄가 이미 소비한 같은 기한을 매 묶음 재예약하는 경계를 발견해 수정했고 신규 시험으로 반복 발화를 차단했다.

## 회귀와 검사 파일 변경

- 전체 실행: Part1 audit 1회, 루트 tests 1회. Part2 일반 회귀 4묶음 미실행.
- 원인별 재검증 반영: audit 176 PASS / 기존 1 FAIL. 루트 {g1['root_effective_counts']['PASSED']} PASS / 기존 5 FAIL / 6 SKIP·XFAIL / 기존 1 수집 ERROR. 새 실패 0, 기존 검사 누락 0.
- `Part1/audit/harness.py`: 기존 manifest 기반 임시 복사/모듈 로딩에 domain_clock/domain_memory를 추가했다. `test_state_io.py` 및 `test_portable_paths.py`: 별도 프로세스/이동 경로 시험에도 새 의존 모듈을 로드하도록 보완했다. 검사·assertion은 줄이지 않았다.
- 최초 audit 로더 누락 오류, 후속 state I/O 8개 오류, setUpClass에서 실행되지 못한 OZ/FVG 7개는 원시 로그를 보존하고 영향 항목만 재실행했다. 중간 `unittest.loader._FailedTest.setUpClass` 선택은 유효 검사로 계산하지 않았다. 7개 실제 메서드의 결과로 클래스 setup 진단을 해소했다.
- 루트 E2 테스트 7개는 앞선 Part2 live_replay 테스트가 sys.modules에 남긴 상태 저장 대역 때문에 실패했다. E2에만 적용되는 `tests/conftest.py` import 격리로 해결하고 기존 검사를 모두 유지했다. Part2 코드를 수정하지 않았다.
- `test_event_e1.py`: 명시적 opt-in 호스트 경계만 허용 목록에 추가하고 기존 폴링이 이를 import하지 않는 검사는 강화했다. 정적 위반 음성/양성 대조, 실제 named pipe, ATR 분리 검사는 유지된다.
- 타이머/ID 검토 이전 비교 파일은 `final/`, `final_review/`, 중단 기록은 `interrupted_pre_*_review.json`에 보존했다. 최종 소스 비교는 `final_ready/`다. S0 골든 생성과 MT5 신규 캡처는 실행하지 않았다.
- 같은 밀리초·같은 사용자 명령에서 time_ns 기반 감시 ID가 겹치는 경계를 수정했다. 판정 시간은 바꾸지 않고 ID용 논리 순번만 engine clock state에 보관한다. 감시 2개 유지, 같은 시각의 세 번째 등록, 재생·checkpoint 연속성 검사를 통과했다. 다른 종목 SWEEP 정리를 현재 종목 묶음으로 진행하지 않도록 제한하고 Fact 자체의 symbol을 보존하는 검사도 추가했다.

## 참고 처리 시간 (판정 없음)

{chr(10).join(measurement)}

각 실제 캡처를 실제 전략 전체가 등록된 상태에서 참고 1회 측정했다. 경계는 EventEngine의 묶음 commit·처리기·Consumer·내부 신호 처리이며 wire 파일 읽기/STAFF decode는 제외한다. CPU 고정/S0 5쌍 성능 게이트는 실행하지 않았다. LIVE/재생 비교 작업이 함께 실행된 환경의 wall 처리 시간이며 전용 CPU 성능 수치로 해석하지 않는다.

## 무결성과 남은 기존 결함

수정본16 29,903개 파일 대조: 변경·누락·추가 0. 수정본17 내 사용자 config/Part3/S0~S8/E1 보호 자료 불일치 0. E2 해시 체인 `33-event-e2-strategies` 등록, 추가 무결성 오류 0, `build/part1_immutable_sha256.json` 재생성. 원시 기존 실패를 삭제하거나 승인 범위를 넓히지 않았다.

남은 기준선 결함은 Part3 동기화/단독 import, DataManager 관련 기존 실패·수집 오류 및 이전 체인의 미등록 원본 차이 진단이다. S8에서 승인된 일봉 초기 2행·24지표 버퍼 결함도 변경하지 않았다. 새로 관찰한 기존 시험 문제는 Part2 replay의 프로세스 전역 모듈 대역 잔존이며 이번에는 시험 격리로만 대응했다.

## 설계 해석과 다음 단계 제한

SPECIAL 개별 Consumer 분리 전까지 canonical Composer 공동 조합을 종목별 kernel로 보존한다(사용자 승인 오류 정책). 도메인 시계/ID와 JSON 저장 호출은 ContextVar 주입점으로 기존 함수에서 사용하고, polling scope 밖 기본 동작은 유지한다. 경제 캘린더·Telegram 입력/송신 서비스는 이번 전략 이전 대상이 아니며 이벤트판에서 시작하지 않는다. 파일 감시로 config를 런타임 변경하지 않는다. 상세 경계·E3 주의점은 인계 문서를 따른다.

종료 직전 코드 검토에서 발견한 출력 안내 경로 주의점: `create_live_event_engine(program)`의 `event_state_directory` 속성은 읽기 원본 program의 부모를 기준으로 만든다. 따라서 이전 수정본을 읽는 경우 이 안내 속성을 출력 위치로 사용하면 안 된다. 자동 파일 쓰기는 없으며 실제 export는 필수 `output_directory` 인자를 별도로 받는다. 이전 수정본을 읽을 때는 **수정본17/Part1/event_state를 명시**한다. 기본 안내 속성의 기준 경로 보완은 아직 적용하지 않았고, 사용자 종료 지시에 따라 후속 항목으로 남긴다. 시험은 명시적인 별도 출력 경로만 사용했으며 이전 수정본 파일을 쓰지 않았다.

## 변경 소스 전체

{changes}
'''
(ROOT/'수정내역_EVENT_E2.md').write_text(report,encoding='utf-8')
handoff=ROOT/'인계_EVENT_E2.md'
text=handoff.read_text('utf-8').replace('# Event Engine E2 인계 (검증 진행 중)','# Event Engine E2 인계')
handoff.write_text(text,encoding='utf-8')
write(OUT/'status.json',{'stage':'E2','implementation_complete':True,'technical_gates_passed':None,
    'execution_status':'CLOSED_AT_USER_REQUEST','completed_gates_passed':True,
    'all_original_gates_completed':external['complete'],'not_completed':['xau1200 final LIVE/replay comparison'],
    'known_followups':['Explicit revision17 event_state output required when reading previous revision; default path metadata followup'],
    'approval_status':'AWAITING_USER_TIMING_DIFFERENCE_APPROVAL',
    'approval_pending_notification_differences':external['approval_pending_difference_count'],
    'next_stage_started':False,'polling_default_unchanged':True,'polling_state_read_only':True,
    'event_state_output_separate':True,'baseline_replaced':False,
    'evidence':['external_comparison.json','baseline_signature_compare.json','startup_inventory.json',
                'preservation.json','integrity_registration.json']})
print('E2 reports written; pending timing approval',external['approval_pending_difference_count'],flush=True)
