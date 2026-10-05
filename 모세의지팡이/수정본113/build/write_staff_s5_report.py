"""Write S5 handoff from actual evidence, including unsuccessful gates."""
from staff_s5_evidence import *

status=read(OUT/'status.json');tests=read(OUT/'baseline_signature_compare.json')
perf=read(OUT/'performance/comparison.json');delta=read(OUT/'source_delta.json')['changes']
changed_tests=read(OUT/'existing_test_changes.json')
legacy=read(OUT/'legacy_callers_review.json')
table='\n'.join(f"| {name} | {v['s0_median']:.6f} | {v['candidate_median']:.6f} | {v['ratio']:.4f} | {v['limit']:.2f} | {v['baseline_drift_ratio']:.4f} | {'통과' if v['pass'] and v['environment_stable'] else '실패'} |" for name,v in perf['metrics'].items())
gate_table='\n'.join(f"| {k} | {'통과' if v else '실패'} |" for k,v in status['gates'].items())
test_table='\n'.join(f"| {name} | {len(g['s5_signature'])} | {', '.join(str(k)+' '+str(v) for k,v in g['s5_counts'].items())} | {len(g['differences'])} |" for name,g in tests['groups'].items())
files='\n'.join('- `'+r['file']+'`' for r in delta)
testfiles='\n'.join('- `'+name+'`' for name in changed_tests)
failed=[name for name,v in status['gates'].items() if not v]
perf_fail=[name for name,v in perf['metrics'].items() if not v['pass']]
drift_fail=[name for name,v in perf['metrics'].items() if not v['environment_stable']]
spread='\n'.join(f"- {name}: S0 {min(v['s0_cpu_s']):.6f}~{max(v['s0_cpu_s']):.6f}초, S5 {min(v['candidate_cpu_s']):.6f}~{max(v['candidate_cpu_s']):.6f}초" for name,v in perf['metrics'].items())
result='모든 S5 게이트를 통과했다.' if status['s5_complete'] else 'S5 구현은 반영했지만 다음 게이트가 실패해 검증 완료로 판정하지 않았다: '+', '.join(failed)+'.'
frozen=read(OUT/'frozen_guard.json')
report=f'''# 수정본12 — S5 STAFF 계산 제거

수정본11(S4 완료) 전체를 독립 복사하고 원본 {frozen['original_files']:,}개 파일의 최초 복사 SHA256을 확인했다. 최종 원본 전체 불변 판정은 **{frozen['unchanged']}**이다. 작업 중 원본의 실행 로그·상태/pyc가 변경되어 전체 불변 검사는 실패로 보존했다. 소스 및 복사본 보호 파일의 불변 판정은 {frozen.get('original_source_and_protected_copy_unchanged',frozen['unchanged'])}이다. S0–S4 증거·골든·expected·성능 정책과 Part3는 그대로다. **S5만 진행했고 S6 이후는 시작하지 않았다.**

{result}

## 구현 범위 및 제거 목록

- STAFF의 `add_wonbi_features`, `apply_requested_features`, `validate_mt5_snapshot` DataFrame 검증 함수와 파생 계산 소유 모듈 import를 제거했다. ATR·EMA 파생·zone/regime/slope·SuperTrend·FVG·원비 계산은 기존 클라이언트 소유 함수 그대로다.
- StaffPipeCache의 `get`, `_legacy_frame`, publication별 DataFrame·frame_lock 캐시를 제거했다. 수신 스레드, numpy 파싱·검증, 불변 Snapshot, seq/epoch/validity/stale 및 공개 continuation 경계는 유지했다.
- DataServer의 legacy 데이터 준비·계산·Watch MA 이력·파생 열 검증 경로와 `_watch_ma_features`를 제거했다. legacy pickle 데이터 요청은 `SNAPSHOT_API_REQUIRED`와 **SNAPSHOT API 사용** 명시 오류를 반환한다. PING/SOURCE_HEALTH/SET_WONBI_SIGMA는 유지했다.
- STAFF는 sigma 상태만 보관하고 SNAPSHOT 헤더로 전달한다. staff_compat의 원비 계산식과 나머지 클라이언트 계산 코드는 바이트 단위로 불변이다.
- STAFF는 pandas를 import하지 않는다. staff_schema의 pandas import는 클라이언트 전용 legacy_frame 함수 안으로 옮겼다. 정규화 연산·열 순서·dtype·attrs는 동일하다. pandas와 파생 모듈 import를 차단한 독립 프로세스에서 STAFF 로드·v1 수신·SNAPSHOT 전달까지 검증한다.
- Part2 event_catalog의 STAFF 파생 함수 별칭을 기존 Fact 소유자에 직접 연결했다. continuation 검사용 DataFrame은 공개 Snapshot과 클라이언트 정규화 함수에서 만든다. 전략·매니저·SPECIAL·EA/wire v1 45열을 변경하지 않았다.
- S4의 SNAPSHOT 경로에는 feed 재수신 시 대기 로그 상태를 비우는 동작이 빠져 있었다. 원래 legacy 경로의 복구 로그·throttle 해제만 SNAPSHOT에 보존했다. 준비 상태·오류 응답·지표 판정·epoch 규칙은 변경하지 않았다.

## 변경 파일

{files}

Part1 audit 전송·테스트 helper와 아래 기존 테스트도 경계를 이전했다. S5 전용 build 도구와 증거만 추가했고 이전 단계 증거는 갱신하지 않았다.

## legacy 호출부 검색 및 정적 검사

제거 전 수정본11의 Part1/Part2 전체 Python 소스를 검색해 {len(legacy['before_hits'])}개 후보 행을 보존했다. Part3는 동결 대상이며 검색·이전 대상이 아니다. `legacy_callers_before.json`, `legacy_callers_review.json`에 파일·행·원문·분류·조치를 모두 기록했다.

- 실제 Part1 데이터 클라이언트는 S4에서 SNAPSHOT으로 이전되어 있었다. 남은 send_pyobj는 manager_KIM의 sigma/health 제어, 전략 이벤트 발송, 서버/manager 응답이다.
- Part2 record/actual/benchmark의 직접 legacy 데이터 검증 요청을 공개 `staff_data_request`로 이전했다. 이 함수는 SnapshotClient 디코드와 StaffCompat 계산까지 포함한다. health 요청 및 오프라인 제어 전송은 그대로다.
- Part2 event_catalog의 계산 별칭·cache.get 경로를 위 공개 경계로 이전했다.
- 사전 검색 목록에 포함됐던 pit/adapters/legacy_staff.py 후보의 구형 프로토타입 분류를 놓쳤고, 최종 전체 AST 검사가 남은 server.handle 호출을 검출했다. 이미 삭제된 replay 패키지에 의존해 import부터 불가능했고 현재 호출부도 없는 코드다. 기존 loader/서버 호출을 제거하고 생성/요청 시 E_SNAPSHOT_API_REQUIRED와 SNAPSHOT API 사용 명시 오류로 차단했다. 동작 중인 클라이언트 계산이나 PIT 계산식은 변경하지 않았다. 정적 검사에 예외를 추가하지 않았고 이 생성자 거부 assertion을 추가했다. 해당 정적 검사 1개만 재실행해 통과했으며 최초 전체 실패와 targeted overlay를 모두 보존했다.
- audit 및 S2–S4 테스트의 legacy 데이터 호출·캐시 조회는 새 클라이언트 경계로 옮겼다. immutable audit fixtures는 실행 중인 데이터 호출부가 아니며 그대로 보존했다.
- Part1/Part2 정적 AST 검사가 legacy 데이터 발송 부재, STAFF의 파생 모듈 import 부재, 기존 계산 소유자·EA·전략 파일 불변을 확인한다. 제어·이벤트 경로를 데이터 요청으로 오인해 삭제하지 않았다.

## 기존 테스트 변경 근거와 검사 보존

{testfiles}

- S1은 원비/apply_requested_features AST 및 소유자 객체 검사를 staff_compat로 옮겼다. 기존 S0 AST, ATR 두 정의 분리 및 실제/합성 ATR 골든 수치 검사는 유지했다.
- S2는 같은 publication의 1회 DataFrame 생성·복사 격리·느린 소비자·수신 중 동시 요청·실제 Windows pipe·재연결·seq 재시작 검사를 클라이언트 캐시에서 수행한다. 원본의 검사 개수와 test ID를 유지했다.
- S3/S4는 **수정본11 Part1/program 전체**를 별도 host가 복사·로드한 oracle로 비교한다. 옛 STAFF에 새 소유 모듈을 섞지 않는다. 600개 무작위 요청, 오류·준비 상태, MA 이력, 값·dtype·열 순서·attrs·복사 격리 검사를 유지한다. 현재 pickle 데이터 요청의 명시 오류와 실제 ZMQ SNAPSHOT 응답 비교를 함께 검사한다.
- S4의 STAFF 전체 파일 불변 검사는 S5에 허용된 제거/변경 노드만 제외한 메서드 AST 정확 비교와 snapshot 검증 연산 정확 비교로 바꿨다. 계산식·Watch·SPECIAL 불변 검사는 유지했다.
- audit/루트 테스트는 raw DataFrame 조회와 원비 기준 함수의 소유 위치만 바꿨다. 기존 수치·미준비 예외·throttle·회복·copy·epoch assertion을 유지했다.
- test_oz_fvg_optimization.py는 바이트 불변이며 **전후 6 + LIVE↔BACKTEST 6 + 시나리오 1 + 성능 1 = 14개** 검사를 그대로 수집했다. 성능 실패가 있으면 그 검사도 실패로 남긴다.

개발 중 관련 검사만 실행했다. 새 테스트의 pytest 예약 매개변수명 오류, Windows 전역 pytest 임시 폴더 접근 거부, 제거된 STAFF 함수를 참조하던 루트 테스트 6개를 각각 기록하고 해당 경계/경로만 수정했다. 최종 G1/G2/G3/성능은 각 1회 실행하며 진단용 재실행은 별도 원본 로그와 함께 기록한다.

## 게이트 결과

| 게이트 | 결과 |
|---|---|
{gate_table}

G2는 SNAPSHOT JSON/배열 → 클라이언트 디코드/정규화 → staff_compat 결과를 기록하고 동결 S0 SQLite 파일 SHA부터 비교했다. 합성 33,996 요청, 실제 4,542 요청 및 실제 미준비 응답 목록을 포함한다. 실제 OZ 전용 경로의 4,302개 고유 프레임 추가 비교도 유지했다. S0 골든을 생성하거나 expected를 갱신하지 않았다.

G3는 240초 전 LIVE / 후 LIVE / 후 BACKTEST를 한 번 실행하고 S0와 비교한다. BEFORE는 수정본6 전체 program 동결본이다. OZ set/frozenset 순서 규칙은 S1과 동일하다.

| G1 그룹 | 수집 | 결과 | 기존 signature 차이 |
|---|---:|---|---:|
{test_table}
| Part1 audit | {tests['audit_s5']['run']} | failures {tests['audit_s5']['failures']}, errors {tests['audit_s5']['errors']} | {len(tests['audit_differences'])} |

Part2 기존 816개 ID를 모두 유지했는지는 part2_collection_scope.json에 명시한다. 기존 기준선 실패와 새 차이는 baseline_signature_compare.json에 별도로 보존한다.

최종 Part2 수집은 **824개(기존 816 + S5 신규 8)**이며 빠진 ID는 없다. 신규 S5 8개는 모두 통과했다. 기존 signature와 남은 차이는 성능 fixture의 PASSED→ERROR, 그리고 test_mt5_plan_runs_strategy_tester_before_history_prepare의 기존 SPECIAL 경로 ERROR→Tk 환경 SKIPPED 두 건이다. 후자는 기존 fixture의 tk.TclError 처리 분기가 실행됐고, tk8.6/ttk/notebook.tcl을 찾지 못한 사유가 JUnit에 남아 있다. 이 테스트 소스와 assertion은 바꾸지 않았으며, 새 예외 허용이나 통과 판정을 추가하지 않았다. 따라서 G1 signature는 실패로 유지한다. 나머지 기존 테스트 상태·원인과 audit/root signature는 동일하다.

## 동결 성능 게이트

측정 전 `성능비교경계_STAFF_S5.md`에 request_0~3의 전체 클라이언트 비용 경계를 고정했다. S0는 legacy 요청+서버 계산, S5는 SNAPSHOT 생성/전달+디코드+정규화+staff_compat+최종 복사 비용이다. 양쪽 모두 원래 측정과 동일한 host 내 직접 전송을 사용한다. 서버 비용만 비교하지 않는다.

동결 protocol.py의 worker 루프·횟수·warmup·GC를 그대로 사용하고, 별도 어댑터가 S0에 전체 동결 BEFORE host를 선택하며 S5 요청 함수만 최종 DataFrame 경계로 연결한다. 정책·표본 수·산식·허용치는 불변이다. 동일 논리 CPU에 고정한 S0→S5 5쌍 전체 표본을 보존했다. 다른 테스트와 겹쳐 측정하지 않았다.

| 작업 | S0 CPU 중앙값(초) | S5 CPU 중앙값(초) | S5/S0 | 허용치 | S0/교정 중앙값 | 판정 |
|---|---:|---:|---:|---:|---:|---|
{table}

환경 필드 일치: {perf['environment_match']}. 최종 성능 판정: **{'PASS' if perf['pass'] else 'FAIL'}**.
후보 비율 초과: {perf_fail or '없음'}. S0 자체 범위 이탈: {drift_fail or '없음'}.
실패 시 허용치를 완화하거나 표본을 다시 골라 통과시키지 않았다. S0 자체 범위 이탈은 후보 코드 속도 비율과 별개의 환경 유효성 실패다. 새 성능 게이트 실패는 G1의 기존 성능 검사에도 그대로 반영되며 기준선 결함으로 숨기지 않는다.

같은 세션의 전체 5회 관측 범위(제외 표본 없음):

{spread}

### 실패 원인 분석

request_0의 공식 표본은 S0 중앙값 0.375초, S5 중앙값 0.4375초/250회였다. 추가 비용은 회당 약 0.25ms이며 1.15 허용치에 해당하는 0.43125초를 0.00625초 초과했다. 이를 반올림하거나 허용치를 바꿔 통과시키지 않았다.

실패한 request_0만 진단용 cProfile로 각 250회 관찰했다. 새 경로는 매 요청 JSON/3개 배열 전달·decode·클라이언트 frame 복사를 수행한다. 프로파일에서 250회 SnapshotClient.request 약 0.07초, frame 약 0.04초의 경로 비용이 확인되었고, ATR/원비 계산은 여전히 요청마다 250회다. 서버 계산을 없애도 최종 DataFrame 전체 비용이 모두 없어지는 것은 아니다. 이 수치는 프로파일러 오버헤드가 있는 진단값이며 공식 성능 표본을 대체하지 않는다. 진단 실행의 총시간은 공식 비율을 재현하지 않았으므로 전체 16.67% 증가를 특정 함수 하나의 회귀로 단정하지 않는다. 동결 S0의 세션 내 변동과 전송/디코드 고정비를 분리해 확정하려면 별도 측정 판단이 필요하다.

request_2는 S0 중앙값 3.09375초가 동결 허용 구간 3.139740566~3.5278125초 밖에 있어 환경 유효성에서 실패했다. 프로그램 해시·버전·CPU affinity는 일치했지만 교정 당시 CPU 시간 분포와 같다고 볼 수 없었다. CPU 주파수/스케줄러 등 구체적 외부 원인은 이번 증거로 확정하지 못했다. 성능 정책 변경, 공식 표본 재선정, 추가 5쌍 게이트 재실행은 하지 않았다. 근거는 performance_failure_analysis.json과 request0_failure_profile.json이다.

## 무결성·기존 결함·인계

`27-staff-s5-server-calculation-removal`에 STAFF와 staff_schema 변경을 등록했다. 기존 무결성 진단 23개는 유지하고 새 진단과 broken chain 여부를 별도로 검사했다. 현재 Part1 불변 목록을 재생성했다. 이전 체인·S0 골든·expected·성능 정책 파일은 변경하지 않았다.

전체 복사 시 pytest current 디렉터리 링크 {frozen['materialized_alias_files']:,}개 파일이 독립 파일로 실체화됐다. 각 대상이 수정본11 내부 동결 manifest 항목으로 해석되고 대상/복사 파일 SHA가 동일함을 검증했다. 임의 추가 파일을 허용한 것이 아니며 materialized_frozen_aliases.json에 목록을 남겼다.

Part3 전체 해시는 그대로이며 참조 목록만 유지했다. 새로 확인한 이전 경계의 결함은 SNAPSHOT 경로의 대기 로그 해제 누락과, 제거된 STAFF 내부 함수에 결합된 검증 호출들이다. 계산식이나 전략 결정을 바꾸는 방식으로 해결하지 않았다.

추가로 Part2 PIT의 사용되지 않는 legacy 어댑터가 이미 없는 replay 패키지를 import하면서 옛 STAFF 데이터 요청을 남기고 있었다. 전체 정적 검사에서 확인한 뒤 명시적 폐기 오류 경계로 차단했다. baseline_signature_compare.json은 최초 전체 결과에 static_diagnosed.xml의 해당 1개 진단 결과만 합친다. 그 외 기존 실패와 성능 실패는 유지한다.

### 원본 실행 상태 변동

G2 최초 진입 전 불변 검사에서 수정본11의 Part1/logs, Part1/program/logs, pyc 경로 변경을 발견했다. 원본 로그에는 2026-09-26 17:55:52~17:56:15의 서비스 기동/상태 갱신이 남아 있다. 그 실행의 주체는 확인하지 못했고 현재 해당 경로의 Python 프로세스도 조회되지 않았다. 이 작업의 host는 임시 복사본에서 실행하며 공식 worker는 -B 및 PYTHONDONTWRITEBYTECODE를 사용한다. 원본 실행 상태를 임의로 복원·삭제하지 않았다.

최초 G2는 사전 검사에서 종료되어 골든 계산을 실행하지 않았다. 원본 전체 불변 실패를 유지하면서, 소스/골든/보호 파일이 일치하는 경우에 한해 독립 복사본의 결과 검사를 계속하도록 분리했다. 이후 G2 계산은 처음으로 1회 실행했다. 초기 실패 로그와 original_runtime_drift_detected.json을 보존했다. 이 분리는 원본 불변 게이트의 통과나 동결 manifest 재생성을 뜻하지 않는다. final status의 해당 게이트는 실패다.

근거: `검증결과/staff_s5/status.json`, `baseline_signature_compare.json`, `performance/comparison.json`, `frozen_guard.json`.
'''
(ROOT/'수정내역_STAFF_S5.md').write_text(report,encoding='utf-8')
(ROOT/'인계_STAFF_S5.md').write_text(f'''# 수정본12 S5 인계

수정내역_STAFF_S5.md와 검증결과/staff_s5/status.json을 먼저 읽는다.

{result}

S6 이후는 시작하지 않았다. 실패한 게이트가 있으면 해결/사용자 지시 없이 다음 단계로 진행하지 않는다.
수정본11의 소스와 S0–S4 증거, 골든·expected·동결 성능 정책은 유지했다. 원본 실행 로그·상태/pyc는 작업 중 변동하여 전체 불변 게이트가 실패했다. original_runtime_drift_detected.json을 확인하고 원본 상태를 임의로 복원하지 않는다. 원비 계산은 S7까지 staff_compat의 기존 함수 그대로다. Part3는 레거시 동결이며 동기화하지 않는다.

STAFF는 numpy Snapshot 수신·검증·저장·SNAPSHOT 전달·제어만 한다. legacy pickle 데이터는 SNAPSHOT API 사용 오류를 반환한다. 모든 계산/Watch 이력/정규화는 클라이언트가 소유한다.
request_0~3 전체 클라이언트 비용과 동결 BEFORE host를 사용하는 측정 경계는 성능비교경계_STAFF_S5.md와 build/run_staff_s5_performance.py에 고정되어 있다. 정책 파일을 변경하거나 실패 표본을 덮어쓰지 않는다.

성능: {'PASS' if perf['pass'] else 'FAIL'}; 후보 비율 초과 {perf_fail or '없음'}; S0 자체 범위 이탈 {drift_fail or '없음'}.
Part2 기존 816개 ID 보존 및 새 실패 여부는 baseline_signature_compare.json의 실제 결과를 따른다. 새 실패를 기존 Part3 결함으로 처리하지 않는다.
''',encoding='utf-8')
print(result)
