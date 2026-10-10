"""Write the S1-style S2 closeout only after every required gate succeeds."""
from staff_s2_evidence import ROOT, OUT, read


def main():
    status = read(OUT/'status.json')
    assert status['s2_complete'], 'Do not publish an incomplete revision as complete'
    tests = read(OUT/'baseline_signature_compare.json')
    perf = read(OUT/'performance_compare.json')
    synthetic = read(OUT/'synthetic_compare.json')
    actual = read(OUT/'actual_compare.json')
    source = read(OUT/'source_delta.json')['changes']
    collection = read(OUT/'part2_collection_scope.json')
    timing = '\n'.join(f"| {name} | {metrics['cpu_s']['s0']:.6f} | {metrics['cpu_s']['s2']:.6f} | {metrics['cpu_s']['ratio']:.3f} |"
                       for name,metrics in perf['metrics'].items())
    changed = '\n'.join('- `'+r['file']+'`'+(' (신규)' if r['before_sha256'] is None else '') for r in source)
    rows = '\n'.join('| '+name+' | '+str(sum(group['s2_counts'].values()))+' | '+
        ', '.join(f'{key} {value}' for key,value in group['s2_counts'].items())+' | 새 실패 0 |'
        for name,group in tests['groups'].items())
    text = f'''# 수정본9 — S2 STAFF 내부 저장 구조 교체

수정본8(S1 완료) 전체를 독립 복사했다. 원본 7,423개 파일의 SHA256을 `검증결과/staff_s2/s1_frozen_manifest.json`에 동결하고 원본 변경 없음과 보호 대상 복사본 일치를 최종 검증했다. S0 골든·expected·S1 증거·성능 정책은 갱신하지 않았다. S2만 완료했으며 S3 이후는 시작하지 않았다.

## 구현 범위

- 기존 전용 Named Pipe 수신 스레드는 유지하면서 수신 작업에서 pandas DataFrame 생성을 제거했다. numpy 파싱·EMPTY_VALUE/비유한 값 NaN 정제·기존 유효 행/OHLC 검사 후 (심볼, TF)별 최신 `StaffSnapshot`을 원자적으로 교체한다.
- Snapshot의 time·volume·45열 values는 불변 bytes를 바탕으로 하는 읽기 전용 배열이다. `setflags(write=True)`도 거부한다. seq·source_epoch·indicator_validity·수신 시각을 함께 보관하고 validity도 수정할 수 없다.
- legacy 요청은 원래 DataFrame 처리 순서(시간 변환, 열 순서, dropna, 마지막 중복 유지, 정렬, tail(650), reset_index, dtype, attrs)를 그대로 사용한다. publication별 잠금 아래 한 번만 만들며 모든 반환은 복사본이다. 늦게 끝난 옛 요청이 새 Snapshot의 캐시를 덮어쓰지 않는다.
- DataFrame 생성 잠금과 수신 publication 잠금을 분리했다. 요청들이 공유하는 WATCH MA 이력은 요청 전용 잠금으로 직렬화한다. 수신 스레드는 이 잠금을 잡지 않으며 MA 계산 함수·이력 규칙 자체는 그대로다.
- 최초·30초 초과 공백·유효성 변화·재연결의 epoch 규칙, 중복/역행 seq 거부, 정확히 30초일 때 FRESH, 주말·허용 심볼·FEED_NOT_READY를 유지한다. 재연결은 기존처럼 seq 기록만 지우고 마지막 정상 데이터와 freshness를 유지하며 다음 seq 1을 수용한다.
- 공개 내부 주입점 `publish_frame`/`receive_one`, cache/시계/session/허용 대상 생성자 인자를 추가했다. Part2 두 runtime의 STAFF private 필드 재선언과 `_read_exact`/`_consume_one` 몽키패치를 제거했다. 실제 Win32 `_consume_one`은 같은 파서를 호출하는 호환 래퍼다.
- checkpoint에 남아 있던 STAFF private 저장 구조 의존도 `export_state`/`restore_state`/`health_session`으로 바꿨다. checkpoint는 원래부터 source/engine fingerprint가 다른 버전의 파일을 거부하므로 기존 checkpoint를 새 버전용으로 재해석하지 않는다. 같은 S2 실행의 중단/재개 의미는 유지한다.
- ZMQ 요청/응답 API, v1 45열 wire, EA, 원비·Fact 계산, 매니저·SPECIAL·전략 로직은 바꾸지 않았다. 내부 `snapshot()`은 ZMQ SNAPSHOT API가 아니다.

## 변경 파일

{changed}

검증/기록 도구는 `build/staff_s2_evidence.py`, `register_staff_s2.py`, `run_staff_s2_gates.py`, `finalize_staff_s2.py`, `write_staff_s2_report.py`, `inventory_part3_legacy.py`, `diagnose_staff_s2_storage.py`, `record_staff_s2_gui_diagnosis.py`다. 무결성 단위와 현재 소스 불변 목록도 등록·재생성했다.

## 후속 사용자 지시: Part3 레거시 고정

Part3는 이번 S2와 이후 단계의 동기화·수정 대상에서 제외한다. Part1/Part2의 직접 참조 5곳은 S1 검사 3곳과 과거 변경 기록 2곳이며 `Part3_레거시_참조목록.md`와 JSON에 기록했다. 정리는 S8 이후 별도 작업이다. 현재 명시적 Part3 매개변수 검사는 S1에서 PASSED이며, 기존 Part3 전용 실패 ID는 없다. 이후 확인되는 기존 Part3 실패는 기준선 결함으로 구분하며 새 Part1/Part2 실패를 면제하지 않는다. `AGENTS.md.txt`와 인계 문서에 이 지시를 남겼고 Part3 파일은 변경하지 않았다.

## 기존 테스트 fixture 변경과 BEFORE의 완전성

`test_oz_fvg_optimization.py`는 runtime import만 동결 BEFORE 전용 어댑터로 바꿨다. BEFORE는 수정본6의 Part1/program 전체 동결본이며 전체 manifest를 확인한다. 옛 STAFF/OZ/FVG 일부와 현재 모듈을 섞지 않는다. 옛 private 수신기를 요구하는 BEFORE에는 동결된 과거 host 자체를 사용하고, 현재 production host에는 private fallback을 두지 않는다.

전후 6개·LIVE↔BACKTEST 6개·시나리오 1개·성능 1개, 총 14개 검증을 그대로 유지했다. 모든 기존 test 함수 AST와 성능 시나리오 계약 해시가 S1과 동일하다. `tests/sparse_events/test_events.py`의 fixture에서 `_cache` 직접 읽기만 공개 `keys()`/`get()` 호출로 대체했으며 assertion은 바꾸지 않았다. 새 S2 검증은 {len(collection['added'])}개다.

개발 중 S2/S1 관련 검사와 checkpoint 영향 검사만 실행했다. 최초 새 테스트의 잘림 길이가 실제 프레임보다 길었던 문제와 EMA 검사 대상 열 선택 오류를 바로잡고 해당 실패만 재실행했다. 구현을 고정한 뒤 전체 게이트를 각 1회 실행했다. 진단 목적 재실행이 있으면 `diagnosed_reruns.json`과 원시 최초 결과를 모두 보존한다.

### 전체 G1에서 발견해 해결한 추가 저장 결합

최초 루트 통합 테스트에서 새 실패 53개가 발생했다. `allzone_events.py`와 `event_catalog.py`가 `getattr(cache, field)` 및 `CACHE_FIELDS` 문자열 목록으로 옛 STAFF 필드에 접근하는 경로를 처음 수정에서 빠뜨린 것이 원인이었다. 이것을 기존 기준선 결함으로 처리하지 않았다. 이벤트 저장·복원은 공개 `export_state`/`restore_state`로 바꾸고, future-state 비교는 공개 메타데이터와 legacy DataFrame 복사본을 사용한다. 계산·후보 조건·이벤트 판정은 바꾸지 않았다. 영향 받은 **53개만 재실행해 전부 통과**, 공개 API 검사 2개도 통과했다. 최초 실패 로그는 그대로 남겼다.

이 수정에서 STAFF의 변경 메서드는 `export_state` 하나뿐이며, 메타데이터만 필요한 조회가 배열 전체를 직렬화하지 않도록 옵션을 추가했다. 앞서 골든·3-way·성능 참고에 사용한 **파서·legacy 변환·요청 핸들러·계산 함수 AST는 모두 동일**하다. `private_storage_diagnosis.json`에 근거를 기록했고 전체 G2/G3나 성능 측정을 반복하지 않았다. S2 체인의 최초 기록도 보존한 뒤 최종 after-hash와 현재 불변 목록을 갱신했다.

전체 Part2 실행에서 달력 검사 1개가 일시적인 Tcl 초기화 오류로 SKIP됐다. 그 1개만 독립 실행하자 S1의 기존 `PermissionError: SPECIAL 디렉터리를 파일로 읽음` 오류가 동일하게 재현됐다. 테스트·GUI·expected를 수정하지 않았다. 원시 전체 결과(728 PASS / 6 FAIL / 20 ERROR / 20 SKIP)와 독립 재현을 모두 보존하며, 아래 signature는 이 진단을 반영한 728 PASS / 6 FAIL / 21 ERROR / 19 SKIP이다.

## 게이트 결과

| 게이트 | 결과 |
|---|---|
| G2 합성 | PASS — 33,996 사례 / 19,160 DataFrame, {synthetic['comparison_mode']} 정확 일치 |
| G2 실제 MT5 | PASS — 4,542 사례 / 4,302 DataFrame, {actual['comparison_mode']} 정확 일치 |
| 미준비 응답 | PASS — 기존 실제 캡처의 D1 EMA 미준비 239건까지 동일 |
| G3 | PASS — 240초 전 LIVE / 후 LIVE / 후 BACKTEST와 S0 일치, 비어 있지 않은 시나리오 |
| G1 | PASS — 기존 baseline signature 보존, 새 실패 0 |
| S2 파이프 견고성 | PASS — 잘림·손상 헤더·중복/역행·재연결 seq 1·느린 소비자·동시 요청 |
| S1/ATR/원비 | PASS — S1 신규 검사 지속 통과, ATR 분리 및 원비 불변 |
| 무결성 | PASS — 체인 정상, 기존 진단 25개 유지, 추가 진단 0 |
| 성능 | 참고 1회만 기록, 판정 없음 |

합성 SQLite SHA256: `{synthetic['right_sha256']}`

실제 MT5 SQLite SHA256: `{actual['right_sha256']}`

기록기의 응답 집합 해시도 기존 S0와 같다: 합성 `{read(OUT/'synthetic/summary.json')['sha256']}`, 실제 `{read(OUT/'actual/summary.json')['sha256']}`. 이는 위의 SQLite 파일 전체 해시와 구분한다.

MT5를 다시 실행하거나 S0 캡처를 다시 생성하지 않았다. 보존된 실제 `pipe_*.bin`/`manifest.tsv`/`complete.txt`를 현재 S2 코드에 공급했다. OZ의 명시적 set/frozenset 태그만 순서를 무시하는 S1 비교 규칙을 그대로 썼으며, 일반 목록·타입·구성원·중복은 계속 엄격히 비교한다.

## 전체 테스트 수집 범위

| 그룹 | 수 | 결과 | 비교 |
|---|---:|---|---|
{rows}
| Part1 audit | {tests['audit_s2']['run']} | failures {tests['audit_s2']['failures']}, errors {tests['audit_s2']['errors']} | 새 실패 0 |

Part2는 validation_suite·cadence_input_validation·conditional_validation·watch_ma_validation 전체를 실행했다. S1의 **745개 ID 누락 0**, 신규 {len(collection['added'])}개를 포함해 총 **{collection['s2_collected']}개**다. 'baseline signature 통과'는 기존 실패까지 사라졌다는 뜻이 아니며, 각 실패 원인과 상태는 `baseline_signature_compare.json`에 보존한다.

## 성능 참고 기록

S2 STAFF 측정은 1회만 실행했다. 아래는 당시 S0 중앙값과 이번 1회 CPU 시간(초) 비교로, 실행 세션이 다르므로 개선율 판정이나 허용치 변경의 근거로 쓰지 않는다. 전체 시나리오 timing은 기존 14개 검증 중 성능 항목과 G3 원시 결과에 남긴다. 성능 게이트는 S5·S8에만 적용한다.

| 작업 | S0 CPU 초 | S2 CPU 초 | S2/S0 참고 비율 |
|---|---:|---:|---:|
{timing}

`staff_performance_policy.json`, `staff_performance_policy.py`, `staff_performance_protocol.py`와 `성능규칙_S1_S8.md`는 S1 해시 그대로다. 재보정·허용치 완화·S0 성능 재측정은 하지 않았다.

참고 측정 이후 추가된 `export_state(metadata_only=True)`는 측정 대상 파서/legacy 요청 경로에서 호출되지 않는다. 측정 대상 메서드 AST 불변 근거와 당시 소스 해시를 함께 남겼으며, 최종 전체 소스를 다시 측정했다고 주장하지 않는다.

향후 S5/S8 실행 주의: 동결 CPU runner의 S0 worker는 현재 `part1_host.runtime`을 직접 import한다. S0 STAFF에는 새 공개 주입 API가 없으므로, 그때의 실행 어댑터에도 이번 BEFORE와 같은 동결 host 경계를 연결해야 한다. 측정 작업량·정책 파일·한도를 변경해서 해결하면 안 된다. S2에서는 금지된 S5/S8 성능 게이트를 실행하거나 이 후속 단계 작업을 시작하지 않았다.

## 불변 증거 및 무결성

`23-staff-s2-storage/changes.json`은 STAFF의 S1 해시→S2 해시만 연결한다. 기존 manifest와 과거 체인을 수정하지 않았다. S1의 기존 진단 25개는 그대로 남는다. `build/part1_immutable_sha256.json`은 현재 S2 소스 목록이며 골든/expected 변경이 아니다.

최초 복사 검증에서 로그·pycache 5개 차이를 찾아 원본 동결 해시와 일치하도록 복사본만 다시 복사했고 7,423개 전체 일치를 확인했다. 과거 pytest 임시 symlink가 일반 파일로 복사된 추가 항목은 이전 검증 임시 자료이며 원본을 참조하는 실행 의존성으로 쓰지 않는다. 근거는 `copy_verification.json`·`copy_repair.json`이다.

## 발견한 기존 결합 및 남는 기존 결함

- 지목된 두 runtime 외에 checkpoint도 `_cache` 등 STAFF private 필드를 직접 저장하고 있었다. 새 저장 구조에서 continuation이 깨지지 않도록 같은 S2 공개 경계로 옮겼다.
- 같은 결합이 두 이벤트 저장 모듈의 문자열 기반 필드 조회에도 있었다. 최초 전체 G1의 53개 새 실패를 유발한 S2 이전 누락이며, 공개 경계로 옮긴 후 전부 해결했다. 전략 조건 변경이나 기준선 실패 면제로 처리하지 않았다.
- 기존 Part2 주입기는 뒤에 불필요한 바이트가 붙은 프레임을 캐시에 반영한 뒤 오류를 냈다. 공개 단일 프레임 주입 API는 끝 위치까지 먼저 확인한 뒤 반영해 손상 입력이 마지막 정상 Snapshot을 바꾸지 않도록 했다. 기존 오류 문구와 정상 v1 형식은 유지한다.
- WATCH MA 요청 경로의 공유 이력 객체에는 동시 요청 보호가 없었다. 계산 함수는 유지하고 DataServer 요청 경계에만 잠금을 추가했다. 이전 ZMQ REP는 단일 요청 처리였으며 이번 동시성 검증으로 명시적으로 보호했다.
- 실제 MT5 캡처의 D1 EMA 미준비 239건, 기존 테스트의 SPECIAL 디렉터리 파일 읽기·누락 예제/매니저·설정 resources/fixture·Windows 불변 목록 경로 검사 등은 S1에 있던 상태 그대로다. 사용자 범위 밖의 기존 결함을 숨기거나 기대 결과로 새로 승인하지 않았다.

최종 근거: `검증결과/staff_s2/status.json`, `baseline_signature_compare.json`, `source_delta.json`, 골든 비교 파일, `parity_s0_compare.json`, `immutable_verify.json`. S3 이후 작업은 시작하지 않았다.
'''
    (ROOT/'수정내역_STAFF_S2.md').write_text(text,encoding='utf-8')
    (OUT/'결과.md').write_text(text,encoding='utf-8')
    (ROOT/'인계_STAFF_S2.md').write_text('''# 수정본9 S2 완료 인계

`수정내역_STAFF_S2.md`와 `검증결과/staff_s2/status.json`을 먼저 읽는다.
수정본8 원본, 수정본9 안의 S0/S1 증거, 골든·expected·성능 정책은 불변이다.
S2 Snapshot 저장/공개 주입점만 완료했다. S3는 시작하지 않았으며 사용자 지시 없이 다음 단계를 진행하지 않는다.
기존 실패는 baseline signature에 보존되어 있다. 새 실패 0이며 Part2 기존 745개 ID를 모두 유지한다.
성능은 S2 참고 1회만 기록했고 S5/S8 고정 규칙·허용치를 바꾸지 않았다.
Part3는 레거시다. 이후 동기화·수정 금지. `Part3_레거시_참조목록.md`를 유지하며 정리는 S8 이후 별도 작업으로 한다.
Part3 관련 기존 실패는 기준선 결함으로 구분한다. 새 Part1/Part2 회귀는 계속 차단한다.
''',encoding='utf-8')
    print('S2 report and handoff written')


if __name__=='__main__': main()
