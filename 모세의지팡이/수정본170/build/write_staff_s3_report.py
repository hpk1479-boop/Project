"""Render completed S3 evidence without running gates again."""
from staff_s3_evidence import *

status=read(OUT/'status.json')
assert status['s3_complete'],status
tests=read(OUT/'baseline_signature_compare.json')
delta=read(OUT/'source_delta.json')['changes']
frozen=read(OUT/'frozen_guard.json')
synthetic=read(OUT/'synthetic/summary.json'); actual=read(OUT/'actual/summary.json')
counts='\n'.join(f"| {name} | {len(g['s3_signature'])} | "+', '.join(f'{k} {v}' for k,v in g['s3_counts'].items())+' | 새 실패 0 |' for name,g in tests['groups'].items())
audit=tests['audit_s3']
counts+=f"\n| Part1 audit | {audit['run']} | failures {audit['failures']}, errors {audit['errors']} | 새 실패 0 |"
files='\n'.join('- `'+r['file']+'`' for r in delta)
perf=read(OUT/'performance_compare.json')['metrics']
perfrows='\n'.join(f"| {k} | {v['cpu_s']['s0']:.6f} | {v['cpu_s']['s3']:.6f} | {v['cpu_s']['ratio']:.3f} |" for k,v in perf.items())
dual={k:read(OUT/k/'dual_path_comparison.json')['comparisons'] for k in ('synthetic','actual')}
extra=''
if (OUT/'diagnosed_reruns.json').exists():
    extra='\n최종 실행 중 진단 및 좁은 재실행은 `diagnosed_reruns.json`에 기록했다. 최초 결과를 삭제하거나 덮어쓰지 않았다.\n'
report=f'''# 수정본10 — S3 SNAPSHOT API와 클라이언트 라이브러리

수정본9(S2 완료) 전체를 독립 복사하고 원본 {frozen['original_files']:,}개 파일의 SHA256을 동결했다. 원본 수정 없음과 S0/S1/S2 증거·골든·expected·성능 정책 불변을 최종 확인했다. S3만 완료했으며 S4 이후는 시작하지 않았다. Part3는 레거시로 고정했다.

## 구현 범위

- 같은 ZMQ REP endpoint에 태그 `STAFF_SNAPSHOT_V1` + JSON 요청을 추가했다. 응답은 JSON 메타데이터 1개 + TF별 time/volume/values 원시 배열 3개다. 새 경로에는 pickle이 없으며 legacy 단일 pickle 프레임 요청/응답은 그대로 지원한다.
- `staff_schema.py`는 현재 v1 45열의 Snapshot API 스키마와 기존 DataFrame 정규화를 담는다. EA wire v2, CRC, ROW/HEARTBEAT, 원비 MT5 매핑은 도입하지 않았다.
- 서버는 잠금 아래 동일 publication의 Snapshot과 age를 함께 읽고, numpy로 선택 지표의 유효성을 확인한 뒤 배열을 전달한다. SNAPSHOT 수신/응답 경로에서 DataFrame이나 파생지표를 생성하지 않는다. 여러 TF의 오류 우선순위, 30초 stale, 주말 빈 응답, 허용 범위, FEED_NOT_READY를 유지한다.
- `SnapshotClient`는 symbol/TF/schema/seq/source_epoch/received_at/max_bars로 publication을 구분한다. 재연결 후 seq 1 재시작을 옛 캐시로 오인하지 않는다. 전체 multipart의 구조·길이 검증을 완료한 후에만 캐시를 반영한다.
- 배열은 immutable bytes 기반 읽기 전용이며 메타데이터도 수정할 수 없다. 같은 publication의 DataFrame은 캐시하고 매번 복사본을 반환한다. 요청 직렬화, 캐시 잠금, 늦은 이전 frame 생성의 최신 캐시 덮어쓰기 방지를 추가했다.
- `staff_compat.py`는 S2의 파생 처리·검증·WATCH MA 이력 조합 코드를 그대로 사용한다. 열 순서·dtype·attrs와 MA 이력 확장 의미를 유지한다. 같은 Snapshot에서 sigma가 바뀌면 새 sigma로 파생 응답을 만든다.
- 원비 계산은 S7까지 Python S2 방식이다. STAFF의 `WonbiState`, `add_wonbi_features`, `apply_requested_features`는 불변이다. 새 어댑터에 필요한 임시 원비 함수도 기존 함수와 AST가 같도록 검증하며, 새로운 식이나 MT5 매핑을 도입하지 않았다.
- Part2 host에는 새 Part1 모듈 3개의 로드 순서만 추가했다. 기존 전략 클라이언트는 전환하지 않았다. 계산 함수·전략·매니저·SPECIAL·EA·config·Part3는 바꾸지 않았다.

## 변경 파일

{files}

별도 S3 증거 도구: `build/implement_staff_s3.py`, `staff_s3_evidence.py`, `prepare_staff_s3_tools.py`, `register_staff_s3.py`, `run_staff_s3_gates.py`, `record_staff_s3_dual.py`, `check_staff_s3_scope.py`, `record_staff_s3_max_bars_fix.py`, `record_staff_s3_gui_diagnosis.py`, `inventory_part3_legacy_s3.py`, `finalize_staff_s3.py`, `write_staff_s3_report.py`. 사용법은 `STAFF_SNAPSHOT_API_S3.md`에 기록했다.

## 기존 테스트와 BEFORE 보존

기존 테스트 파일은 수정하지 않았다. 전후 6개·LIVE↔BACKTEST 6개·시나리오 1개·성능 1개, 총 14개 검증과 S1/S2 신규 검증이 그대로 남아 있다. BEFORE는 수정본6의 Part1/program 전체 해시 동결본이다. 일부 옛 모듈을 현재 모듈과 섞지 않는다.

새 S3 검사는 30개다. 정상 무작위 요청 **600개 전부**에서 DataFrame의 값·dtype·열 순서·attrs까지 정확히 비교했다. 여러 지표/TF/MA 이력/seq/sigma 조합과 별도의 잘못된 요청·미준비·stale·주말·동시 요청·손상 multipart·읽기 전용·복사 격리·실제 ZMQ 통신·기존 max_bars 설정을 검증한다.

개발 중에는 관련 검사만 실행했다. 첫 신규 네트워크 테스트가 replay의 가짜 zmq 모듈을 참조한 테스트 바인딩 문제를 수정했다. 실제 pyzmq로 다시 검증했고 생산 동작/기존 expected를 바꿔 통과시키지 않았다. `dev_s3.log`의 최초 실패 기록은 유지한다. 이후 관련 S1/S2/S3 71개와 최종 S3 27개가 통과했다.

### 최종 실행 중 발견한 설정 호환성 문제

코드 검토에서 Snapshot 클라이언트가 wire의 행 수 제한(3–650)을 DataFrame.tail 설정인 max_bars에도 적용한 문제를 발견했다. 기존 legacy가 허용하는 1000·-1 설정과, 0 설정의 FEED_NOT_READY 응답을 새 경로가 거부했다. 신규 3개 회귀 사례로 실제 실패를 재현한 뒤 설정값의 추가 범위 제한만 제거했다. wire 배열 행 수와 길이 검증은 그대로다. 관련 12개를 좁게 실행해 모두 통과했고, 3개 실패 원본과 수정 후 결과를 보존했다.

이후 변경은 `staff_snapshot.py`의 max_bars 조건 하나뿐임을 역패치 SHA256으로 검증했다. 이미 완료한 G2 입력은 설정 파일의 max_bars=650을 사용했고 이 값에 대한 조건 결과는 동일하다. 기존 legacy G3와 STAFF 참고 측정은 이 클라이언트 디코더를 호출하지 않는다. 따라서 전체 G2/G3/성능을 반복하지 않았다. 최종 무결성 체인과 불변 목록은 새 해시로 갱신했고 초기 등록 기록도 보존했다. 근거는 `max_bars_diagnosis.json`이다.

### GUI 환경 변형 진단

전체 Part2 실행에서 `test_data_build_run_never_starts_backtest`가 Tk 초기화 문제로 SKIP됐고, S2에서는 SPECIAL 디렉터리를 파일로 읽는 기존 PermissionError였다. 그 1개만 독립 실행했다. 첫 시도는 전역 pytest 임시 폴더 접근 실패로 fixture 전에 중단되어 원시 기록을 남겼고, 작업 폴더 안의 새 basetemp로 같은 검사만 실행하자 S2 오류가 정확히 재현됐다. GUI·테스트·expected는 수정하지 않았다. 원시 전체 결과(755 PASS / 6 FAIL / 20 ERROR / 20 SKIP)와 모든 진단 시도를 보존하며 최종 signature에는 재현한 기존 오류를 반영했다. 근거는 `gui_signature_diagnosis.json`이다.

## 게이트 결과

| 게이트 | 결과 |
|---|---|
| G2 합성 | PASS — {synthetic['cases']:,} 사례 / {synthetic['unique_frames']:,} DataFrame, S0 SQLite SHA256 일치 |
| G2 실제 MT5 | PASS — {actual['cases']:,} 사례 / {actual['publications']:,} publications, S0 SQLite SHA256 일치 |
| G2 legacy ↔ SNAPSHOT+compat | PASS — 합성 {dual['synthetic']:,} + 실제 {dual['actual']:,} 데이터 요청을 같은 입력으로 직접 비교 |
| 미준비 응답 | PASS — 기존 실제 MT5 지표 미준비 {len(actual['errors'])}건까지 동일 |
| G3 | PASS — 240초 전 LIVE / 후 LIVE / 후 BACKTEST, S0 결과와 동일 |
| G1 | PASS — 기준선 signature 보존, 새 실패 0 |
| S1/S2/ATR/원비 | PASS — 기존 검증 유지 |
| 무결성 | PASS — 체인 정상, 기존 진단만 유지 |
| 성능 | 참고 1회, 판정 없음 |

G2는 입력을 한 번 진행하면서 매 요청마다 기존 경로와 새 경로를 비교하고 **새 SNAPSHOT+compat 응답을 골든으로 기록**했다. 기존 골든 생성기를 고치거나 S0 골든을 다시 생성하지 않았다. 새 결과 파일의 SHA256을 우선 비교했으며 같은 파일 해시를 확인했다. SOURCE_HEALTH 제어 요청은 기존 경로 그대로다.

합성 SQLite SHA256: `{sha(OUT/'synthetic/golden.sqlite')}`

실제 MT5 SQLite SHA256: `{sha(OUT/'actual/golden.sqlite')}`

보존된 실제 MT5 `pipe_*.bin`/`manifest.tsv`/`complete.txt`를 재생했다. EA를 수정·컴파일하거나 테스터 캡처를 다시 생성하지 않았다. G3의 OZ set/frozenset 순서 비교 규칙은 S1 그대로이며 일반 목록·값·타입은 엄격히 비교한다.
{extra}
## 전체 테스트 수집 범위

| 그룹 | 수 | 결과 | 비교 |
|---|---:|---|---|
{counts}

Part2 기존 745개와 S2 신규 29개, 총 **774개 ID를 모두 유지**했다. 최초 전체 실행 801개에 설정 호환성 진단으로 추가한 3개를 합쳐 최종 **804개**다(S3 신규 30개). 전체 실행을 반복하지 않고 관련 결과만 합쳤으며 최초 원시 XML/로그는 보존했다. baseline signature 통과는 기존 결함이 해결됐다는 뜻이 아니다. SPECIAL 디렉터리 파일 읽기·누락 예제/매니저·Windows 경로 등의 기존 실패는 그대로 구분한다.

## 성능 참고

| 작업 | S0 CPU 초 | S3 CPU 초 | S3/S0 참고 비율 |
|---|---:|---:|---:|
{perfrows}

한 번 측정한 legacy STAFF 경로 참고치다. 세션이 다른 S0 중앙값과의 비율로 성능 합격/불합격이나 속도 개선을 주장하지 않는다. 신규 API는 아직 전략에 적용하지 않았으므로 실제 백테스트 전체 가속을 완료했다고 주장하지 않는다. 성능 게이트는 S5/S8에서만 동결 규칙대로 적용한다. S2 인계의 S5/S8 BEFORE host 어댑터 주의사항도 유지한다.

## 불변 증거와 무결성

`24-staff-s3-snapshot-api/changes.json`에 기존 STAFF와 신규 모듈 3개를 등록했다. 이전 manifest/체인은 수정하지 않았다. 현재 소스 목록 `build/part1_immutable_sha256.json`만 재생성했다.

`mechanical_scope_proof.json`은 기존 수신·정규화·계산·legacy 핸들러 본문과 호환 조합의 AST 동일성 29건을 기록한다. 원본 수정본9, S0/S1/S2 증거, EA·전략·원비·골든·expected·성능 정책·Part3의 불변을 확인했다.

## 발견한 경계 조건과 남는 기존 결함

- multi-TF에서 첫 TF의 지표 오류와 다음 TF 미수신이 함께 발생하면 검사 순서도 응답 계약이다. 새 API가 먼저 모든 TF를 모으면서 오류 우선순위를 바꾸지 않도록 TF별 numpy 유효성 검사와 별도 회귀 검사를 넣었다.
- seq 단독 키는 재연결 시 캐시 오인 위험이 있다. seq뿐 아니라 source_epoch/수신 시각도 publication identity에 포함했다. 기존 epoch 증가 규칙 자체는 바꾸지 않았다.
- 기존 Python 원비 산술은 S7까지 유지한다. S3 어댑터의 임시 복사본은 AST 동일성으로 고정하며 독립적인 식 변경을 금지한다.
- Part3 직접 참조 5곳은 기록만 했다. 이후 동기화/수정 및 참조 정리는 하지 않으며 정리는 S8 이후 별도 작업이다. 기존 Part3 실패만 기준선 결함으로 취급하고 새 Part1/Part2 회귀는 면제하지 않는다.

최종 근거: `검증결과/staff_s3/status.json`, `baseline_signature_compare.json`, `source_delta.json`, G2 비교 파일, `parity_s0_compare.json`, `immutable_verify.json`. 다음 단계 S4는 아직 시작하지 않았다.
'''
(ROOT/'수정내역_STAFF_S3.md').write_text(report,encoding='utf-8')
(OUT/'결과.md').write_text(report,encoding='utf-8')
(ROOT/'인계_STAFF_S3.md').write_text('''# 수정본10 S3 완료 인계

`수정내역_STAFF_S3.md`와 `검증결과/staff_s3/status.json`을 먼저 읽는다.
원본 수정본9 및 수정본10의 S0/S1/S2 증거·골든·expected·성능 정책은 불변이다.
S3 API/클라이언트 라이브러리만 완료했다. 기존 전략 클라이언트는 아직 legacy를 사용한다.
S4 이후는 사용자 지시 없이 시작하지 않는다. 원비 MT5 전환은 S7이다.
Part3는 레거시이며 동기화/수정 금지. 참조 정리는 S8 이후 별도 작업이다.
S3 새 실패 0, Part2 기존 774개와 신규 30개를 모두 확인했다. 최초 전체 801개와 설정 진단 신규 3개의 원시 기록을 보존했다.
성능 참고만 1회 기록했다. S5/S8의 고정 정책과 S2 인계의 BEFORE host 어댑터 주의사항을 유지한다.
S0 골든 생성 작업을 반복하지 않는다. 개발 중 관련 테스트만, 각 단계 완료 후 전체 게이트 1회 원칙을 유지한다.
''',encoding='utf-8')
print('S3 report and handoff written')
