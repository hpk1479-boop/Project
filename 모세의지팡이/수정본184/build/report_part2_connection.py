"""Assemble the requested report only from completed measurement evidence."""
from pathlib import Path
import csv,json,sys
R=Path(__file__).resolve().parents[1];E=R/'검증결과/part2_connection'
def read(name):return json.loads((E/name).read_text('utf-8'))
def main():
    captures=read('year_captures.json');year=read('year_parallel.json');quarter=read('quarter_sequential.json')
    comparison=read('quarter_comparison.json');day=read('day_replay_comparison.json')
    inputs=read('recorded_input_comparison.json');preserved=read('preserved_comparison.json')
    moved=read('project_and_runner_move.json');protection=read('source_preservation.json')
    testing=read('test_summary.json');integrity=read('integrity_registration.json')
    exclusion=read('part3_exclusion_applied.json')['applied_at']
    assert inputs['equal'] and preserved['equal'] and day['BAR_TIMER_alerts_equal']
    assert moved['project_relocated'] and moved['warehouse_relocated'] and moved['alerts']>0
    assert testing['new_failures']==0 and not integrity['added_diagnostics']
    annual=[c for c in captures if c['start']>='2025-09-01']
    total_bytes=sum(c['stored_bytes'] for c in annual);raw_bytes=sum(c['raw_bytes'] for c in annual)
    tester=sum(c['elapsed_seconds'] for c in annual)
    rows='\n'.join(f"| {c['start']} | {c['end']} | {c['elapsed_seconds']:.2f} | {c['stored_bytes']/1024**3:.3f} | {c['tick_evidence']['actual']} |" for c in captures)
    measurements=quarter['chunks'][0]['calendar_month_measurements']
    month=measurements['2026-06']
    month_average=month['elapsed_seconds']/month['bundles']
    total_bundles=sum(c.get('observations_by_day',{}).get(day,0) for c in annual for day in c.get('observed_days',[]))
    extrapolated=month_average*total_bundles
    bundles=sum(c['bundles'] for c in year['chunks']);names={n for c in year['chunks'] for n in c['processor_timings']}
    timings={n:sum(c['processor_timings'].get(n,{}).get('ns',0) for c in year['chunks'])/max(1,bundles)/1e6 for n in names}
    timing_rows='\n'.join(f'| {n} | {v:.3f} |' for n,v in sorted(timings.items(),key=lambda x:-x[1]))
    day_capture={k:read('day_'+k+'.json')[0] for k in ('BAR','TICK')}
    d=day['results'];qdiff=read('quarter_difference_analysis.json')
    report=f'''# Part2 연결 — 이벤트 엔진 백테스트 실행기 / 수정본21

수정본20을 독립 복사한 수정본21에서 MSP3 녹화 → STAFF 공개 수신 API → Part1 이벤트 엔진 → DuckDB/CSV 경로를 연결했다. 전략 판정·계산식은 변경하지 않았다. 이전 알림을 정답으로 삼는 기준선이나 성능 합격선을 만들지 않았다.

## 실행 방법

1. `Part2/START_BACKTEST.cmd` 또는 설치된 Python 환경의 `Part2/BACKTEST CONTROL.pyw`를 연다.
2. 이 컴퓨터의 창고 루트를 선택하고 종목·기간·BAR/EVENT/TICK·시나리오·코어 수를 지정한다. 날짜는 UTC이며 종료일은 포함하지 않는다. 기본 기간은 최근 완료된 12개월이다.
3. **녹화 준비**는 이미 검증된 동일 종목·기간·EA 빌드·설정 조각을 재사용하고 빈 조각만 녹화한다. 실행 중 선택한 MT5를 정상 종료하며, 완료/오류 뒤 원래 설치 파일과 터미널을 복원한다.
4. **실행**은 녹화를 스트리밍 재생한다. 결과 경로는 창고 루트 기준 `runs/<실행 ID>/alerts.csv`다. **누락 조각 확인**으로 필요한 녹화 구간을 볼 수 있다.

CLI는 Part2 폴더에서 `python -B -m event_backtest prepare`, `run`, `missing`, `compare`를 사용한다. `--scenario scenarios/xau_continuous.json`, `--symbol XAUUSD+`, `--start 2025-09-01 --end 2026-09-01`, `--mode BAR`, `--cores 4`를 지정할 수 있다. `--sequential`은 전체 기간에 하나의 엔진 상태를 계속 사용한다. `compare --left <ID> --right <ID> --output comparisons/result.csv`의 출력은 창고 기준 상대 경로다. 그 외 `--output`은 프로젝트 기준 상대 경로다. `--warehouse`는 컴퓨터별 루트 지정이며 목록에 저장하지 않는다.

설정은 `Part2/event_backtest.json`, 예시는 `Part2/scenarios/xau_continuous.json`이다. 시나리오에 SPECIAL 목록·트리거 매핑·chat_id를 가진 명령 문장을 둔다. 명령은 COMMAND ingress로 들어가며 Gemini/네트워크를 쓰지 않는다. 결정적으로 해석되지 않으면 실행 오류다. 테스트와 실행기 worker는 socket 연결을 차단한다. 텔레그램에 실제로 보내지 않는다.

## 입력·조각·상태

- 엔진 입력은 MSP3의 Wire v2 FULL/ROW/HEARTBEAT뿐이다. gzip은 바깥 저장 용기이며 압축 해제한 레코드는 원래 MSP3와 같다. `native_snapshots`와 DuckDB 시장 데이터 재계산 경로를 엔진에 연결하지 않았다. 기존 네이티브 추출 자동화와 창고 코드는 보존했다.
- EA에 테스터 전용 `InpRecordingMode`(TIMER=0/BAR=1), `InpPipeRecording`, `InpNativeExport`를 추가했다. BAR는 TIMER 관측 격자에서 M1 새 봉의 첫 관측이다. TIMER는 STAFF_TIMER_MS를 사용한다. LIVE OnInit 이후 블록, Wire/schema, 기존 지표 계산식은 그대로다.
- 완료된 달은 월 조각, 진행 중인 달은 일 조각이다. 이미 존재하는 일 조각이 있는 과거 달은 그것을 보존하고 빈 일 조각만 채운다. 월·일 목록이 겹치면 커버리지 계획이 중복 재생을 막는다.
- 테스터 Model=4(실제 틱 기반 모든 틱)를 요청한다. 조각마다 저널 근거로 REAL_TICKS/MIXED_OR_GENERATED/UNCONFIRMED를 기록한다. 실제 틱이 아니거나 불명인 조각과 서로 다른 EA 빌드 혼합은 실행 경고다.
- 이전 조각의 확정 OHLC·거래량과 다음 첫 FULL이 이어지면 재연결하지 않는다. 진행 중이던 봉의 정상 변화와 rolling FULL 앞부분 지표 seed 차이는 재연결 근거로 쓰지 않는다. 다음 FULL의 값은 모두 전달한다. 조각 시작 seq만 연속 번호로 옮기며 조각 안의 누락 범위는 보존한다. 연속이 아니면 명시적 reconnect를 낸다.
- 스트리밍은 피드별 한 프레임 heap merge와 최대 8개 입력의 numpy 사전 선택이다. 전체 입력 목록을 만들지 않는다. 이벤트 모드에서 경계를 선언하지 않은 전략은 임의로 걸러내지 않는다. 요구 해상도가 선택 모드보다 높으면 근사치와 해당 소비자 목록을 결과에 남긴다. BAR는 TICK 필요 전략에 근사치다.
- 월별 병렬 worker는 독립 엔진이며 기본 물리 코어 수를 쓴다. 각 시작 앞에 실제 녹화에서 관측된 거래일 3일을 붙여 상태를 데운다. 겹침 알림은 저장하지 않는다. 거래일이 부족하면 이전 조각을 요구한다. 1회성 명령 재등록은 경고하며 예시는 계속 감시다. 연속 실행과 독립 월 실행은 같은 의미라고 가정하지 않는다.

## 이동 가능한 창고

기본은 프로젝트들의 상위 폴더 옆 `개피곤_warehouse`이며 루트는 화면/컴퓨터별 설정으로 바꾼다. 창고 내부 데이터는 `captures`, `runs`, `builds` 아래에 있다. DuckDB에는 captures/runs/alerts/timings 네 테이블만 만들었다. 시장 본체는 녹화 파일이다. OZ 이벤트 테이블은 만들지 않았다.

모든 새 목록·메타데이터·결과 경로는 창고/프로젝트 루트 기준 상대 경로다. 드라이브·절대 경로·루트 밖 `..`를 검증에서 거부한다. MT5 실행에 필요한 설치 경로와 임시 ini는 컴퓨터의 실행 영역에서만 쓰며 창고에 등록하지 않는다. 개발 초기 절대 경로가 있던 목록은 새 DuckDB로 재작성해 삭제된 페이지에도 남기지 않았다.

창고 이동 후 목록과 실제 XAU 재생이 같았고, 프로젝트와 창고를 함께 이동한 별도 통합 시험도 **{moved['alerts']}알림 / {moved['timing_rows']}처리기 시간 행**을 DB와 CSV에 정상 저장했다. 동시 worker는 개별 CSV만 쓰고 부모가 DuckDB에 모은다. 서로 다른 GUI 프로세스에서 같은 창고에 동시에 쓰는 것은 지원하지 않는다.

## 검증

- EA·PRICE/RSI/STO/DI 컴파일: 5개 모두 오류 0, 경고 0.
- 실제 XAU 하루 BAR와 TIMER→BAR: **1,377묶음**의 관측 시각·피드·값·준비 상태 동일, 최종 알림 **{d['BAR']['notifications']}건** 동일.
- 보존 XAU: **239묶음**, PipeReceiver 수신 경로와 재생 경로의 signal_id·시각·방향·문구·수신자 **{preserved['notification_count']}건** 동일. 실제 네트워크 LIVE 대기 시험이 아니라 같은 보존 바이트의 오프라인 수신 시험이다.
- 입력 소비량 제한·청크 경계·실제 거래일·상대 경로·이음새 전환·seq 연속/재연결·저장/조회·압축 무손실·출력 메타데이터 불변·모호 명령 오류 시험을 통과했다. 관련 E1 31개는 먼저 한 번 실행해 통과했고 이후 연결 영향 시험만 실행했다. 합계 **{testing['passed']}개**가 통과했다. Part2 일반 회귀 네 묶음은 실행하지 않았다.
- 반복 문구의 알림 하나가 누락되어도 이후 정확히 같은 알림까지 시각 차이로 오분류하지 않도록 먼저 정확 일치를 제외하고 나머지를 비교한다. 추가/삭제/시각 차이/ID 차이를 CSV로 낸다.
- 개발 중 시험 fixture 누락과 비교 SQL의 예약어 별칭 오류를 발견해 해당 부분을 고치고 관련 시험만 다시 통과했다. 실제 시나리오 결과를 기대값에 맞춰 변경하지 않았다.

## 1년 녹화 실측

요청 기간은 2025-09-01~2026-09-01(끝 미포함)이다. 겹침용 2025년 8월도 별도 보관했다. 아래 테스터 시간은 MT5 실행~완료 기준이며 압축·해시 시간을 포함하지 않는다. 12개월 합계 **{tester:.2f}초**, 원래 MSP3 **{raw_bytes/1024**3:.3f}GiB**, gzip 보관 **{total_bytes/1024**3:.3f}GiB**다.

| 시작 | 종료(미포함) | 테스터 초 | 보관 GiB | 실제 틱 근거 |
|---|---|---:|---:|---|
{rows}

## 재생 실측

| 항목 | 결과 |
|---|---:|
| 1년 월별 병렬 코어 설정 | {year['cores']} |
| 1년 실행 전체 초(검증/저장 포함) | {year['elapsed_seconds']:.2f} |
| 병렬 재생·결과 저장 초 | {year['replay_seconds']:.2f} |
| 전체 처리 묶음(겹침 포함) | {bundles} |
| 1개 코어 2026년 6월 순차 재생 초 | {month['elapsed_seconds']:.2f} |
| 같은 월의 CPU 초 | {month['cpu_seconds']:.2f} |
| 해당 월 묶음 수 | {month['bundles']} |
| 묶음 수 비례 1년 단일 코어 환산 초 | {extrapolated:.2f} |
| 최대 worker working set MiB | {year['max_worker_memory_bytes']/1024**2:.2f} |
| 부모+worker 동시 working set 관측 최대 MiB(2초 표본) | {year['sampled_peak_total_memory_bytes']/1024**2:.2f} |
| worker별 OS peak 합의 보수적 상한 MiB | {year['concurrent_memory_upper_bound_bytes']/1024**2:.2f} |

단일 코어 월 수치는 분기 연속 실행의 6월 구간에서 얻었으며 전체 1년을 단일 코어로 다시 실행한 값이 아니다. 병렬 실행/녹화와 일부 겹친 실측이다. 환산은 참고치이고 합격 기준이 아니다. OS peak는 worker별 최대 working set이며 동시 전체 최고값은 2초 표본의 한계가 있다.

| 처리기·Consumer | 1년 누적 호출 시간 / 묶음 ms |
|---|---:|
{timing_rows}

분기 2026-06-01~2026-09-01의 한 엔진 순차 실행과 연간 병렬 중 같은 3개월 worker를 비교했다. 동일한 월별 병렬 작업을 한 번 더 실행하지 않았다. 차이 **{comparison['differences']}행**. 상세는 창고 `runs/quarter_comparison.csv`, 분석은 프로젝트 `검증결과/part2_connection/quarter_difference_analysis.json`이다. 판단: {qdiff['judgment']}

## 하루 TIMER/틱 실측

2026-09-25 하루 XAU TIMER 1초 녹화: 테스터 **{day_capture['TICK']['elapsed_seconds']:.2f}초**, 보관 **{day_capture['TICK']['stored_bytes']/1024**3:.3f}GiB**. 틱 모드 재생 **{d['TIMER_tick']['elapsed_seconds']:.2f}초**, **{d['TIMER_tick']['bundles']}묶음**, 최종 알림 **{d['TIMER_tick']['notifications']}건**, 평균 **{d['TIMER_tick']['mean_ms']:.3f}ms**, p99 상한 **{d['TIMER_tick']['p99_ms_upper_bound']:.3f}ms**다. 여기서 틱 모드는 모든 *녹화 관측*을 처리한다는 뜻이며 STAFF_TIMER_MS=1000이므로 거래소의 개별 틱마다 녹화한 것이 아니다.

BAR 재생은 **{d['BAR']['elapsed_seconds']:.2f}초**, TIMER 파일을 BAR로 고르는 경로는 **{d['TIMER_filtered']['elapsed_seconds']:.2f}초**다. 후자는 같은 최종 입력이어도 모든 TIMER 프레임을 읽어 FULL/ROW를 복원해야 한다. 묶음 평균/p99는 STAFF 읽기·압축 해제를 제외한 엔진 투입~내부 이벤트 처리 완료 구간이며, 전체 재생 시간에는 입력 처리가 포함된다.

하루 첫 틱 측정은 약 2,631초 뒤 Windows 진행률 JSON 교체 잠금 오류로 중단됐다. 진행률 기록을 best-effort로 바꾸고 잠금 시험을 통과한 뒤 틱 측정만 재실행했다. 완료된 BAR/필터 결과는 재사용했다. 위 틱 시간은 성공한 재실행의 시간이며 실패한 시도는 포함하지 않는다.

개발 중 포터블 경로·결과 메타데이터·월별 계측·비교 조회를 보완했다. 틱 재실행의 code_hash_before/after는 보존했다. 앞서 끝난 BAR 두 실행은 최초 프로세스 실패 전에 소스 해시가 파일로 기록되지 않았으므로 해시 미기록 사실을 남겼다. 이를 최종 소스 실행으로 표시하지 않았다. 전략/판정 경로는 변경하지 않았으며 메타데이터 비변조와 이동 후 전체 실행을 별도로 시험했다.

## 무결성·남은 범위

수정본20 원본의 완료된 보존 대조에서 변경·추가 0을 확인했다. 전략 소스·사용자 config/BTCUSD·Wire/schema 보호 대상 변경 0. Part1 변경은 EA 녹화 분기, capture_io, replay 3개이며 `40-part2-event-runner`에 등록했다. 새 무결성 진단 0, 기존 {len(integrity['remaining_errors'])}개 잔여 진단은 그대로다. 불변 목록을 갱신했다.

Part3 제외 규칙 적용 시점: {exclusion}.

Part2 변경: `event_backtest` 신규 모듈, `event_backtest.json`, 예시 시나리오, 최소 GUI/시작 배치, `generic_backtest/native_mt5.py`의 선택적 캡처 API. `native_features.py`, `bar_seed.py`, 기존 네이티브 추출 코드는 보존했다. 삭제한 전략/창고 파일은 없다.

다음 병목은 위 표의 SWEEP_STATE·COMPOSER 등 남은 판정 입력 경로와 MSP3 읽기/압축이다. 이번 작업에서 전략 최적화·OZ 이벤트 창고·라이브 전환·손익 평가는 하지 않았다. 요청한 연결과 측정 보고 후 대기한다.
'''
    (R/'수정내역_PART2_연결.md').write_text(report,encoding='utf-8')
    (E/'status.json').write_text(json.dumps({'stage':'PART2_CONNECT','status':'COMPLETE','report':'수정내역_PART2_연결.md',
        'quarter_differences':comparison['differences'],'next':'STOP; await user review.'},ensure_ascii=False,indent=2),encoding='utf-8')
    print('REPORT COMPLETE',flush=True)

if __name__=='__main__':main()
