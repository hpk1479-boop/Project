"""Render the S4 report from completed gate evidence, without changing expectations."""
from staff_s4_evidence import *

status=read(OUT/'status.json')
assert status['s4_complete'], status
tests=read(OUT/'baseline_signature_compare.json')
chain=read(OUT/'integrity_registration.json')
delta=read(OUT/'source_delta.json')['changes']
parity={s:read(OUT/f'parity_240_{s}/summary.json') for s in ('S4a','S4b','S4c','S4d')}
counts='\n'.join(f"| {g} | {len(v['s4_signature'])} | {', '.join(f'{k} {n}' for k,n in v['s4_counts'].items())} | 새 실패 0 |"
    for g,v in tests['groups'].items())
audit=tests['audit_s4']
counts+=f"\n| Part1 audit | {audit['run']} | failures {audit['failures']}, errors {audit['errors']} | 새 실패 0 |"
files='\n'.join('- `'+d['file']+'`' for d in delta)
g2={k:read(OUT/f'{k}_compare.json') for k in ('synthetic','actual')}
g2rows='\n'.join(f"| {k} | {v['left_cases']} | {v['comparison_mode']} | `{v['right_sha256']}` |" for k,v in g2.items())
g3rows='\n'.join(f"| {s} | 240초, 전/후 LIVE/후 BACKTEST 및 S0 정확 일치 | {p['metrics']['after_live']['oz_facts_computed']} | {p['metrics']['after_live']['oz_facts_reused']} |" for s,p in parity.items())
perf=read(OUT/'performance_compare.json')
perfrows='\n'.join(f"| {k} | {v['cpu_s']['s0']:.6f} | {v['cpu_s']['s4']:.6f} | {v['cpu_s']['ratio']:.3f} |" for k,v in perf['metrics'].items())
oz_counts={'synthetic':read(OUT/'synthetic/dual_path_comparison.json')['oz_direct_comparisons'],
           'actual':read(OUT/'actual_oz_supplement.json')['frames']}
report=f'''# 수정본11 — S4 클라이언트 이전

수정본10(S3 완료) 전체를 독립 복사했다. 원본 13,139개 파일과 복사본의 최초 SHA256 일치를 확인했고, 최종 검사에서도 원본과 S0–S3 증거·골든·expected·성능 정책 불변을 확인했다. **S4a–S4d만 완료했으며 S5는 시작하지 않았다.** Part3는 레거시로 유지했다.

## 구현 범위

- S4a: SWEEP/FVG/INDICATOR의 StaffClient가 SNAPSHOT multipart를 요청한다. 원시 정규화 프레임만 소비하며 서버 파생 계산을 호출하지 않는다. 전략 조건·계산식·상태 전이는 그대로다.
- S4b: 실제 MA 요청 경계인 monitor_OZ.StaffClient의 명시적 지표 요청을 staff_compat로 연결했다. 각 클라이언트가 WatchMAFeatures와 MAFeatureCache를 소유한다. 기존 history_rows 확장, 겹치는 봉 대체, 진행 중 봉 수정, source_epoch·재연결에 따른 초기화 규칙을 그대로 사용한다. watch_ma.py/watch_ma_features.py/watch_orchestrator.py의 코드는 변경하지 않았다.
- S4c: OZSnapshotFeatures가 S1에서 이전한 네 밴드/regime 함수를 직접 호출한다. 외부유동성의 atr_14는 indicator_facts의 ATR14_GENERAL Fact에서 받는다. 원비는 S7까지 기존 staff_compat의 Python 함수를 사용한다. 동일 publication의 순수 계산은 공유 OZFactMemo에서 재사용하며 응답은 복사한다. SharedOZStaffClient의 기존 TTL·요청 합집합·16개 프로필 공유 규칙은 유지했다.
- S4d: manager_KIM.StaffClientV2의 데이터 요청을 staff_compat로 연결했다. SpecialPluginAPI는 기존 lane 선택을 그대로 실행해 해당 클라이언트에 도달한다. maintenance/event 클라이언트와 캐시는 분리되어 있다. SOURCE_HEALTH와 SET_WONBI_SIGMA 제어 경로 및 SPECIAL1–7 파일은 불변이다.
- Part2 host와 Part1 audit의 오프라인 전송 계층이 실제 SNAPSHOT multipart를 전달한다. live_replay도 Part1의 실제 클라이언트와 공개 transport 생성자 인자를 사용한다. OZ 공유 클라이언트의 private 필드 재선언을 공개 생성자로 대체했다. 서버 오류의 기존 재생 audit 의미를 유지한다.
- STAFF 서버·EA·wire v1 45열·staff_compat 계산 함수·원비 계산·전략/매니저 결정 로직을 변경하지 않았다. 서버 legacy 계산 제거는 S5의 작업이다.

## 변경 파일

{files}
- `Part1/audit/harness.py` — 테스트 전송 계층의 multipart 지원과 새 공개 라이브러리 로드.
- `Part1/watch_ma_validation/test_live_integration.py` — 실제 소비자 응답에서 MA 결과를 검사하도록 관찰 위치 변경.
- `Part1/audit/test_baseline.py` — 원시 프레임과 호환 파생 응답의 검사 경계 분리.

S4 전용 증거 도구는 build/*staff_s4*.py, 증거는 검증결과/staff_s4에 있다. 이전 단계의 도구와 증거는 갱신하지 않았다.

## 기존 테스트와 BEFORE 보존

기존 테스트 변경은 네 파일이다.

- test_staff_s1.py: 순수 소유자가 STAFF 서버에 의존하지 않는 검사를 유지하면서 S4에서 허용된 staff_snapshot/staff_compat 공개 라이브러리 import만 허용했다. manager_KIM 전체 파일 고정은 StaffClientV2를 제외한 전체 AST 정확 비교로 바꿨다. 원비·ATR·MA 이력 계산 검사는 유지한다.
- test_staff_s3.py: S3 당시 모든 클라이언트 파일을 바이트 고정했던 검사를, S4에서 승인된 클라이언트 클래스 외 전체 AST 정확 비교로 바꿨다. 원비·apply_requested_features·검증 함수의 AST 동일성 검사는 그대로다.
- Part1 WATCH 통합 테스트: 전송 계층 응답이 원시 multipart가 되었으므로 실제 클라이언트 계산 응답을 관찰해 기존 SMA 값 비교를 수행한다. 기존 assertion을 제거하지 않았고, SNAPSHOT 사용과 서버 MA 캐시 미생성 확인을 추가했다.
- Part1 audit/test_baseline.py: 전체 실행에서 TREND의 원시 프레임에 wonbi_mid가 없다는 오류를 확인했다. 기존 원비 수치·ATR 양수 검사를 김매니저의 실제 staff_compat 응답에 유지하고, TREND의 원시 열 전용 계약·길이·복사 격리 및 compat 복사 격리를 함께 검사한다. 고정 수치 expected는 그대로다.

신규 test_staff_s4.py에는 전략 소스 범위, 원시 전송, 복사 격리, MA 이력/epoch, 서버 계산 미사용, OZ 정확 일치/재사용/sigma 갱신, ATR 출처, SPECIAL lane 격리와 불변 검사를 추가했다. 공유 TTL 요청 메서드와 SOURCE_HEALTH 메서드도 S3 AST와 비교한다.

**전후 6개 + LIVE↔BACKTEST 6개 + 시나리오 1개 + 성능 1개, 총 14개 비교가 있는 test_oz_fvg_optimization.py는 바이트 단위 불변이다.** BEFORE는 수정본6 전체 Part1/program 동결본이며 옛/새 모듈을 섞지 않았다.

개발 중 관련 검사만 실행했다. S4b 신규 테스트가 history requirement를 18로 잘못 예상해 1회 실패했다. 기존 feature_source_rows의 최소 650 규칙을 확인하고 신규 테스트의 기대만 650으로 수정했다. 생산 코드·기존 expected는 변경하지 않았다. 최초 실패와 해당 1개 재검증 로그를 보존했다. 이후 S1/S3/S4 관련 58개 및 통신·재생 관련 14개가 통과했다.

## 게이트 결과

G2는 새 실행 결과를 만들고 기존 동결 골든과 해시부터 비교했다. S0 골든 생성 작업을 재실행하지 않았다.

| 입력 | 요청 수 | 비교 방식 | 결과 SHA256 |
|---|---:|---|---|
{g2rows}

모든 요청에서 legacy와 SNAPSHOT+staff_compat를 정확 비교했다. OZ 기본 네 밴드 요청은 합성 {oz_counts['synthetic']}건을 직접 Fact 경로로 추가 비교했다. 실제 골든은 항상 전체 지표를 요청하므로 이 분기의 실제 비교 수가 0임을 확인했다. 이 검증 누락은 별도 실제 프레임 검사로 보완했다: 동결 실제 골든의 {oz_counts['actual']}개 고유 프레임에서 원시 열을 읽어 Snapshot JSON/배열 → 실제 OZ StaffClient 경로에 넣고, S0 응답의 고정 OZ 소비 열을 투영한 결과와 정확 비교했다. WATCH MA 전용 attrs만 제외하며 source_epoch/indicator_validity 및 값·dtype·열 순서·index·frame digest를 비교한다. 골든은 재생성하지 않았다. 미준비 응답 목록도 전체 G2에서 S0와 같았다.

| 하위 단계 | G3 | 후 LIVE Fact 계산 | 재사용 |
|---|---|---:|---:|
{g3rows}

각 하위 단계 구현 완료 후 240초 G3를 한 번씩 실행했다. 모든 시나리오가 비어 있지 않다. OZ의 명시적 set/frozenset 순서 처리만 S1 규칙대로 적용한다. S4c의 재사용 횟수가 계산 횟수보다 많으며, S4d에서 SPECIAL1–7 해시는 그대로다.

## 전체 테스트 수집 범위

| 그룹 | 수 | 결과 | S3 signature 비교 |
|---|---:|---|---|
{counts}

Part2 기존 **804개 ID를 전부 유지**했다. 기존 실패·오류는 수정본10의 signature와 구분했고 새 실패는 없다. 전체 그룹은 최종 1회만 실행했다. Part1 audit 최초 원시 결과의 failures 1/errors 1 중 새 오류는 위 테스트 경계 문제였다. 해당 1개만 재실행해 통과했고 audit_diagnosed_rerun.json에 남겼다. 위 표는 이 진단 결과를 합친 최종 signature이며 최초 원시 로그/보고서도 그대로 보존한다. 생산 코드 수정과 골든 재실행은 없었다. 기준선 결함을 통과로 바꾸기 위해 expected를 수정하지 않았다.

G1/G2 실행 시작 시 로드된 동결 검사기는 이 테스트 수정을 아직 허용 목록에 포함하지 않아 종료 검사에서 audit/test_baseline.py 변경을 보고했다. 해당 원본 진단은 frozen_guard_prior_test_migration.json에 보존했다. 문서화한 테스트 4개만 명시적으로 허용한 최종 동결 검사에서 원본·골든·이전 증거의 값과 파일 목록 불변을 다시 확인했다. G1/G2 계산을 재실행하거나 불일치를 숨기지 않았다.

루트 표의 SKIPPED 6은 실제 skip 3개와 xfail 3개를 기존 signature 방식대로 합친 것이다.

## 성능 참고

| 작업 | S0 CPU 초 | S4 CPU 초 | 참고 비율 |
|---|---:|---:|---:|
{perfrows}

STAFF benchmark는 1회 참고 측정이며 판정하지 않았다. 세션이 다른 S0 값과의 비율로 속도 개선을 확정하지 않는다. STAFF legacy handler는 그대로이므로 이 표는 새 클라이언트 전체 실행 성능을 뜻하지 않는다. 동결 성능 규칙과 허용치는 변경하지 않았고 성능 게이트는 S5/S8에서만 적용한다.

## 무결성 및 기존 결함 기록

S3에서 이미 미등록 상태였던 수정 파일을 다음 체인에 연결하기 위해, S3 동결 원본 SHA256을 근거로 `25-staff-s4-frozen-s3-provenance`에 선행 이력만 명시 등록했다. 대상은 {', '.join('`'+r['file']+'`' for r in chain['frozen_baseline_reconciliations'])}이다. 이것은 과거 검증 통과를 새로 주장하는 변경이 아니다. 현재 S4 변경은 `26-staff-s4-client-migration`에 별도로 등록했다. 이전 체인·원본 manifest는 수정하지 않았다.

기존 미등록 진단 중 {len(chain['removed_diagnostics'])}개는 이 이력 등록으로 해소되었고 {len(chain['remaining_errors'])}개가 남아 있다. 새 진단은 0개이고 broken hash chain은 없다. 기존 SPECIAL 디렉터리 읽기·예제/매니저 누락·Windows 경로 등의 기준선 실패는 별도로 남아 있다. `build/part1_immutable_sha256.json`을 현재 목록으로 재생성하고 최종 해시를 확인했다.

강화한 파일 목록 검사에서 pytest의 current 디렉터리 링크를 copytree가 독립적인 실제 폴더로 복사한 1,252개 파일이 원래 rglob 목록보다 더 보이는 것을 확인했다. 각 항목은 원본 수정본10 내부의 동결 manifest 대상 경로로 해석되며 원본 대상과 복사 파일의 SHA256이 모두 동결값과 같다. 임의의 추가 파일을 허용하지 않고 이 조건을 만족한 링크 복사만 명시적으로 검증했다. 근거는 materialized_frozen_aliases.json이며 최초 검사 실패 로그도 보존했다. 원본·골든 파일의 추가/삭제나 갱신은 없다.

Part3 전체 파일 해시가 S3와 동일하다. Part1/Part2의 직접 참조는 Part3_레거시_참조목록.md에만 기록했고 정리하지 않았다. S8 이후 별도 작업이라는 정책을 유지한다.
'''
(ROOT/'수정내역_STAFF_S4.md').write_text(report,'utf-8')
(ROOT/'인계_STAFF_S4.md').write_text('''# S4 완료 인계

완성본: 수정본11. 원본 수정본10과 S0–S3 증거는 동결되어 있다.
S4a–d 결과는 수정내역_STAFF_S4.md 및 검증결과/staff_s4/status.json을 확인한다.

S5는 시작하지 않았다. 다음 단계는 별도 독립본에서 진행한다.
S5에서 STAFF legacy 파생 계산을 제거할 때 G2 검증 대상은 staff_compat로 전환한다.
현재 S4 검증 도구의 dual 비교는 아직 살아 있는 legacy 경로를 참조하므로 그대로 S5에 사용할 수 없다.
원비는 S7까지 변경하지 않는다. EA/wire 변경은 S6부터다. Part3는 계속 레거시 동결이다.

성능 게이트는 S5와 S8에서만 build/staff_performance_policy.json의 동결 규칙으로 적용한다.
S0와 새 코드를 번갈아 각 5회, 고정 CPU/환경, CPU 중앙값 비율과 환경 안정성 규칙을 그대로 쓴다.
S2/S3 인계에 있는 BEFORE 전체 프로그램/host 주의사항도 유지한다.

실행 중 클라이언트: 세 전략은 raw Snapshot, OZ는 OZSnapshotFeatures+OZFactMemo,
일반 Watch와 manager_KIM/SPECIAL은 StaffCompat다. SOURCE_HEALTH와 sigma 제어는 기존 API다.
SPECIAL 파일과 결정 로직은 불변이며, MA 계산과 이력 함수도 이전 구현 그대로다.
''','utf-8')
print('S4 report and handoff written')
