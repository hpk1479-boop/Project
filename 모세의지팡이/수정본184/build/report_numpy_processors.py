"""Render the final report only from completed diagnostics and measurements."""
from pathlib import Path
import json

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / '검증결과/numpy_processors'


def read(name):
    return json.loads((OUT/name).read_text('utf-8'))


def main():
    old, new = (read(f'revision{r}_day/result.json') for r in (21, 22))
    comparison = read('alert_comparison.json')
    assert {x['case'] for x in comparison} == {'day', 'week'}
    tests = read('test_summary.json')
    integrity = read('integrity_registration.json')
    inventory = read('source_inventory.json')
    parity = read('live_replay_comparison.json')
    diagnosis = read('calculation_diagnostics.json')
    rows = []
    for name in ('SWEEP_STATE', 'INDICATOR', 'FVG_STATE', 'WATCH_CONDITIONS',
                 'OZ_STATE', 'COMPOSER', 'FVG', 'SWEEP', 'OZ'):
        before = old['processor_timings'][name]['ms_per_bundle']
        after = new['processor_timings'][name]['ms_per_bundle']
        rows.append(f'| {name} | {before:.4f} | {after:.4f} | {(1-after/before)*100:.1f}% |')
    four = ('SWEEP_STATE', 'INDICATOR', 'FVG_STATE', 'WATCH_CONDITIONS')
    old_four = sum(old['processor_timings'][n]['ms_per_bundle'] for n in four)
    new_four = sum(new['processor_timings'][n]['ms_per_bundle'] for n in four)
    rows.append(f'| 변경한 4개 합계 | {old_four:.4f} | {new_four:.4f} | {(1-new_four/old_four)*100:.1f}% |')
    perf = '\n'.join(rows)
    totals = '\n'.join(f'| {label} | {old[key]:.4f} | {new[key]:.4f} |'
                       for key, label in (('input_inclusive_ms', '입력 읽기 포함 평균 ms/묶음'),
                           ('engine_mean_ms', '엔진 전용 평균 ms/묶음'),
                           ('engine_p99_ms', '엔진 전용 p99 ms/묶음'),
                           ('elapsed_seconds', '입력 읽기 포함 전체 초'),
                           ('cpu_seconds', '프로세스 CPU 초')))
    totals += f"\n| 최대 프로세스 메모리 MiB | {old['max_memory_bytes']/1048576:.2f} | {new['max_memory_bytes']/1048576:.2f} |"
    parity_rows = '\n'.join(f"| {p['case']} | {p['bundles']} | {p['signals']} | {p['notifications']} | 신호·출력 모두 동일, 오류 0 |" for p in parity)
    alerts_rows = '\n'.join(f"| {'2026-09-25 하루' if p['case']=='day' else '2025-09-01~07 첫 주'} | {p['before_measurement']['bundles']} | {p['before']} | {p['after']} | {p['removed']} | {p['added']} |" for p in comparison)
    differences = sum(p['removed']+p['added'] for p in comparison)
    # If there are differences, require a reviewed explanation, not an automatic
    # assumption that either old or new output is correct.
    if differences:
        diagnosis_text = (OUT/'alert_difference_analysis.md').read_text('utf-8')
    else:
        diagnosis_text = '두 구간 모두 추가·삭제·시각·방향·문구·수신자·signal_id 차이가 없었다. 이전 알림과의 일치는 진단 결과이며 합격 조건으로 사용하지 않았다.'
    changed = '\n'.join('- `' + p['path'] + '`' for p in inventory['changes'] if p['before_sha256'] is not None and p['after_sha256'] is not None)
    added = '\n'.join('- `' + p['path'] + '`' for p in inventory['changes'] if p['before_sha256'] is None)
    deleted = [p['path'] for p in inventory['changes'] if p['after_sha256'] is None]
    assert not deleted, deleted
    cases = diagnosis['cases']
    numeric_max = max((float(x.get('max_abs', 0) or 0) for x in cases), default=0)
    body = f'''# 수정내역 — SWEEP·INDICATOR·FVG·WATCH 판다스 제거

작성일: 2026-09-27. 산출: 수정본22. 수정본21 전체를 독립 복사한 뒤 작업했다.

## 결과와 범위

네 처리기의 묶음 판정 경로를 Snapshot NumPy 뷰와 상주 객체로 바꿨다. 관련 시험 {tests['unique_tests']}개가 통과했고, synthetic240·actualXAU239에서 LIVE 수신 경로와 재생의 신호·최종 출력이 모두 같다. 이번 측정에서 변경한 네 처리기의 합계는 {old_four:.3f} → {new_four:.3f}ms/묶음이다.

COMPOSER·SPECIAL, 사용자 config, Part2 공개 run 차단, 녹화 형식, DuckDB, EA·MT5·Wire는 변경하지 않았다. 실제 텔레그램 전송 및 외부 네트워크는 시험 드라이버에서 차단했다. Part3 제외 규칙을 작업 시작부터 적용했다. 이전 수정본에 실행·상태 기록을 남기지 않도록 수정본21 진단은 `검증결과/numpy_processors/host21/Part1/program` 전체 복사본에서 했다.

## 처리기 구조와 계산 시점

| 처리기 | 상주 객체·입력 | 봉 확정 또는 설정 변경 때 계산 | 매 묶음 처리 |
|---|---|---|---|
| SWEEP_STATE | SweepRuntime·LiquidityDetector·LevelStore / time·OHLC 직접 참조 | 1d·4h·8h·5m 확정봉 키, 주/세션 경계 또는 세션 설정 변경에 따른 PDH/PDL·직전 4h/8h·PWH/PWL·이전 세션 H/L | source_tf 새 확정봉에서만 터치 평가. 같은 봉 대표 선정, 레벨 소멸·해제, 상태/건강 전달 유지 |
| FVG_STATE | FVGRuntime·StructureStore / 전체 공급 이력 NumPy 뷰 | 자체 Wilder ATR과 생성·확정봉 채움·나이·후보 구조. 설정 변경도 무효화 | 진행봉 high/low/close로 터치, 현재 가격·시각, 기존 수명주기 이벤트 |
| INDICATOR | IndicatorRuntime·ArrayFactFrame / native HMA·EMA 등 직접 참조 | SMA·점수 Fact의 확정 구간 및 누적 상태. 요청한 점수·metric만 계산 | 진행봉 의존 Fact 마지막 행, 추세·점수 비교, 기존 snapshot/변화 이벤트. ATR14_GENERAL은 Board의 공용 NumPy Fact 한 개를 공유 |
| WATCH_CONDITIONS | WatchRuntime·WatchMonitor·WatchMAStore / 뷰와 상주 감시 등록부 | 가변 OPEN MA의 확정 이력, 새 기간 요청·봉 확정·OPEN 정정. native MA는 그대로 참조 | 진행봉 CLOSE 기반 EMA 마지막 값·터치/교차/퍼센타일/표현식 판정. 확정봉 조건은 새 확정봉에서만 평가 |

`event_engine/market.py`는 Snapshot의 읽기 전용 time/values/volume을 참조한다. 네 경로에서 BoardFrames·SelectionPort·RawSelectionPort·legacy_frame을 사용하지 않는다. 전체 공급 이력을 읽되 DataFrame 창을 만들거나 자르지 않는다. 가변 MA의 추가 필요 이력은 Watch Fact 저장소가 관리한다.

확정 상태의 키에는 source_epoch·이력 길이·시작 시각·마지막 확정봉 시각이 포함된다. 새 봉·재연결·기간 변경에 따른 재계산을 유지한다. 같은 묶음의 ATR14_GENERAL은 기존 공용 Fact를 공유하며 FVG의 산술평균 seed Wilder ATR과 섞지 않는다. pandas 참조 구현은 COMPOSER와 수치 진단을 위해 남겨 두었다.

상태 객체는 묶음마다 재생성하거나 직렬화하지 않는다. 기존 상태 JSON의 시작 복원은 유지했고, Watch 파일 내보내기는 명시적 `export_engine_state`/호스트 저장에서만 수행한다. 명시적 checkpoint의 deepcopy는 허용하며, 일상 시장 처리 경로에서는 사용하지 않는다.

## 검증 1 — 조건·경계·복원

신규 `tests/test_numpy_processors.py` 45개 통과. 관련 E1/E2/E3·OZ 시험을 합쳐 중복 제거 {tests['unique_tests']}개 통과, 미해결 실패 {len(tests['failed'])}개다. 전체 일반 회귀와 Part2 제외 묶음은 실행하지 않았다.

- SWEEP: 레벨 종류별 산출, 진행봉 배제, UTC 주 경계, KST 뉴욕 자정 통과, 최초 터치·같은 봉 대표·해제·복원, 종목별 캐시와 봉 경계.
- FVG: ATR 비율 양 끝과 범위 밖, 확정봉 채움, 최대 나이, 진행봉 터치, 초기 재방송 방지, 생성·채움·만료 전이, 일반 ATR과 seed 분리.
- INDICATOR: 상승·하락·중립·기울기 불일치·미준비, 진행봉 가격 반영, score Fact 전체 재계산 대조, DMI 엄격 경계와 가중치, 봉 안의 점수 변경.
- WATCH: 조건별 발생·비발생, 확정/진행봉 경계, 잘못된 교차 방향, MA 종류·기간 변경, native 열 권위, 표현식, 기존 JSON 복원과 checkpoint.
- DataFrame 생성 및 deepcopy를 금지한 실제 네 처리기 묶음 호출, Snapshot 배열 메모리 공유, ATR 객체 공유를 확인했다.

기존 시험의 내부 저장 구조 단정 3곳만 바꿨다. `test_event_e2_domains.py`의 FVG·INDICATOR checkpoint 비교는 런타임 객체 identity 대신 복원된 핵심 상태와 이어지는 신호를 확인한다. `test_event_e2_composition.py`의 같은 밀리초 복수 Watch는 private dict 경로 대신 호스트의 명시적 JSON 내보내기에서 등록 ID·개수와 재생 연속성을 확인한다. 검증 대상 동작은 줄이지 않았다.

초기 환경용 임시 폴더 권한 오류와 시험 fixture/내부 구조 단정 실패 기록은 지우지 않았다. 해당 원인을 고친 관련 시험의 최종 성공 결과로 판정했다. `test_summary.json`은 `related_final.xml`, `composition_storage.xml`, `logic_final.xml`의 최종 항목을 합친 결과다.

## 검증 2 — 기존 계산과 값 대조(진단)

같은 전체 650행 이력에서 {len(cases)}건을 비교했다. INDICATOR 전체 score/metric Fact·공용 ATR, 양방향 점수, Watch 가변 MA, FVG 구조, SWEEP 주·세션 레벨을 포함한다. 허용 절대 오차 1e-10 기준 차이 0건이며, 관측 최대 절대 오차는 {numeric_max:.6g}이다. 내부 비트 동일성을 합격 조건으로 사용하지 않았다.

결과: `검증결과/numpy_processors/calculation_diagnostics.json`. 650행은 동일 입력 진단에 사용한 길이이며 새 실행 경로가 고정 DataFrame 650행을 만드는 의미가 아니다.

## 검증 3 — LIVE 수신 = 재생

| 입력 | 묶음 | 신호 | 최종 알림 | 결과 |
|---|---:|---:|---:|---|
{parity_rows}

signal_id·시각·payload와 출력 경로의 방향·문구·수신자를 포함한 전체 기록을 비교했다. 합성 구간은 checkpoint 전후 연속성도 통과했다. 보존 캡처는 기존 파일을 읽었고 MT5를 실행하지 않았다. 상세: `검증결과/numpy_processors/live_replay_comparison.json`과 `*_final.json`.

## 검증 4 — 전체 감시 알림 진단

두 버전에 같은 11개 초기 명령, SPECIAL1~7 및 동일 트리거 설정을 메모리로 주입했다. 사용자 config 파일은 수정하지 않았다. 공개 run을 열지 않고 비공개 진단 드라이버로 실행했다.

| 구간(UTC) | 묶음 | 수정본21 알림 | 수정본22 알림 | 삭제 | 추가 |
|---|---:|---:|---:|---:|---:|
{alerts_rows}

{diagnosis_text}

전체 차이 목록: `검증결과/numpy_processors/alert_differences_day.json`, `alert_differences_week.json`. 원본 알림 CSV와 코드 해시는 각 `revision21_day`, `revision22_day`, `revision21_week`, `revision22_week` 폴더에 보존했다. run_id만 제외하고 알림 필드 전체를 비교했다.

하루 캡처는 REAL_TICKS이며 2025-09 첫 주가 속한 보존 월 조각은 MIXED_OR_GENERATED다. 첫 주 진단은 동일 보존 입력의 코드 차이 비교이며 실제 틱 재현을 입증하는 자료가 아니다.

## 참고 측정 — 2026-09-25 하루 BAR

동일 {new['bundles']}묶음·명령·설정, 일반 계측 각 1회다. 두 하루 측정은 각각 다른 무거운 시험이 없는 상태에서 실행했다. 주간 비교의 병렬 실행 시간은 아래 성능표에 사용하지 않았다. 성능 게이트와 cProfile은 실행하지 않았다.

**전체 감시 기준이며 대상 전략 선택 실행의 백테스트 시간이 아니다.**

| 처리기/Consumer | 수정본21 ms/묶음 | 수정본22 ms/묶음 | 감소율 |
|---|---:|---:|---:|
{perf}

| 전체 지표 | 수정본21 | 수정본22 |
|---|---:|---:|
{totals}

엔진 전용은 Ingress 투입부터 `engine.run()` 종료까지이며 내부 신호 처리도 포함한다. 입력 읽기 포함은 압축 캡처 읽기·디코드·BAR 선택부터 엔진 처리까지다. 각 처리기 시간은 실제 `on_event` 호출의 누적을 시장 묶음 수로 나눴다. 초기 명령 등록 비용은 처리기 표에서 제외했다. 최대 메모리는 전체 측정 프로세스의 관측 최고값이다.

엔진 평균 감소율은 {(1-new['engine_mean_ms']/old['engine_mean_ms'])*100:.1f}%, 입력 포함 평균 감소율은 {(1-new['input_inclusive_ms']/old['input_inclusive_ms'])*100:.1f}%다. 단일 표본이므로 작은 차이를 고정 성능으로 해석하지 않는다. COMPOSER의 {new['processor_timings']['COMPOSER']['ms_per_bundle']:.3f}ms/묶음은 남은 큰 비용이며 이번 작업에서는 수정하지 않았다.

COMPOSER 측정값은 증가했다. 기존에는 먼저 실행한 네 처리기가 Board의 공용 raw DataFrame 캐시를 준비했지만, 새 경로는 그 캐시를 만들지 않아 COMPOSER가 필요한 프레임을 처음 준비하는 비용을 부담한다. 이는 소스에서 확인한 비용 귀속 변화이며 증가분 전체를 별도 분해 계측한 것은 아니다. 따라서 네 처리기 감소율만 전체 개선율로 제시하지 않고 위 엔진 전체 수치를 함께 사용한다.

## 변경·삭제 목록과 무결성

기존 파일 수정:

{changed}

새 코드·시험·도구:

{added}

추가 문서: `AGENTS.md.txt` 최신 지시 부록, 본 보고서. 생성된 원시 결과는 `검증결과/numpy_processors/` 아래에 모았다. Part1 무결성 체인 `Part1/audit/remediation/41-numpy-processors/changes.json`·`review.json`을 추가하고 `build/part1_immutable_sha256.json`을 재생성했다. 체인 신규 진단 {len(integrity['added_diagnostics'])}개, 보호 대상 변경 {len(inventory['protected_differences'])}개, 수정본21 원본과 독립 실행 복사본의 소스 차이 {len(inventory['isolated_revision21_differences'])}개다. 기존 무결성 진단은 보존했다.

기존 파일 삭제는 없다. 네 처리기에서 프레임 준비·매 묶음 런타임 재생성 경로를 제거했으며, 옛 strategy_*.py와 pandas 참조 구현은 남겨 두었다. 개발 중 새로 만든 일회용 포팅 도구 `build/port_numpy_processors.py`만 작업 종료 전에 제거했다. `monitor_OZ.py`는 바이트 그대로 유지해 CRLF/LF 혼합도 바꾸지 않았다.

비교한 코드·시험·도구의 경로별 이전·이후 SHA256은 `검증결과/numpy_processors/source_inventory.json`, 최종 시험 합계는 `test_summary.json`, 체인 결과는 `integrity_registration.json`에 있다. 이후 단계는 시작하지 않는다.
'''
    (ROOT/'수정내역_판다스제거.md').write_bytes(body.replace('\r\n','\n').replace('\n','\r\n').encode('utf-8'))
    print('Report written: 수정내역_판다스제거.md')


if __name__ == '__main__':
    main()
