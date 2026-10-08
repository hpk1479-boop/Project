"""Complete the report from actual public month/week runs, preserving prior evidence."""
from pathlib import Path
import json,sys,xml.etree.ElementTree as ET

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'검증결과/data_selection';APP=OUT/'approved'
def read(p):return json.loads(p.read_text('utf-8'))
def save(p,value):p.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')

def main():
    assert read(APP/'complete.json')['month_complete']
    assert read(APP/'result_consistency.json')['csv_worker_db_equal']
    assert len(read(APP/'export_repair.json')['runs'])==9
    restore=read(APP/'mt5_restoration.json');assert restore['equal']
    month=read(APP/'month_special1.json')
    weeks=[read(APP/f'week_special{i}.json') for i in range(1,8)]
    events=[json.loads(line) for line in (APP/'month_special1.jsonl').read_text('utf-8').splitlines() if line.startswith('{')]
    capture=next(e for e in events if e.get('event')=='CAPTURE_COMPLETE')
    assert capture['reconstruction_verified'] and not capture['history_missing']
    assert any('생성 틱' in warning for warning in month['warnings'])
    for result in [month,*weeks]:
        assert all(c['bundles']>0 for c in result['chunks'])
        assert result['schema_ids']==[capture['schema_id']]
        assert result['ea_builds']==[capture['ea_build_hash']]
    tests={}
    for name in ('related.xml','selection_fixed.xml','reuse_final.xml','history_policy.xml','journal_policy.xml','export_policy.xml','export_runner.xml'):
        for c in ET.parse(OUT/name).getroot().iter('testcase'):
            tests[c.get('classname')+'::'+c.get('name')]=not any(x.tag in ('failure','error') for x in c)
    assert all(tests.values())
    save(OUT/'test_summary.json',{'unique_passed':len(tests),'failed':0,'part2_general_suites_run':False})
    sys.path.insert(0,str(ROOT/'build'))
    from seal_data_selection import inventory
    old={};new={}
    for folder in ('Part1/program','Part2/event_backtest','Part2/scenarios','tests','build'):
        old.update(inventory(ROOT.parent/'수정본23',folder));new.update(inventory(ROOT,folder))
    inv=read(OUT/'source_inventory.json')
    inv['changes']=[{'path':n,'before_sha256':old.get(n),'after_sha256':new.get(n)} for n in sorted(old.keys()|new.keys()) if old.get(n)!=new.get(n)]
    save(OUT/'source_inventory.json',inv)
    files='\n'.join('- `'+row['path']+'`' for row in inv['changes'])
    m=month['chunks'][0];n=m['bundles'];wall=read(APP/'month_special1_wall.json')['public_workflow_seconds']
    rows=[]
    for i,result in enumerate(weeks,1):
        c=result['chunks'][0];p=c['processor_timings']
        def value(name):return f"{p[name]['ms_per_bundle']:.3f}" if name in p else '미생성'
        rows.append('| '+' | '.join([f'SPECIAL{i}',str(c['bundles']),str(c['notifications']),f"{result['elapsed_seconds']:.2f}",
            f"{c['mean_ms']:.3f}",f"{c['elapsed_seconds']*1000/c['bundles']:.3f}",f"{c['p99_ms_upper_bound']:.1f}",
            value('OZ_STATE'),value('SWEEP_STATE'),value('FVG_STATE'),value('INDICATOR'),value('WATCH_CONDITIONS'),value('COMPOSER')])+' |')
    measurement=f'''## 승인 후 실제 한 달 실행 및 1주 실측

사용자가 녹화를 승인하고 이력 기준을 정정했다. **실제 틱 부재·생성 틱은 1분봉 이력 누락이 아니다.** `history_check`는 생성 틱과 다른 TF의 누락을 M1 누락으로 취급하지 않는다. 선택 구간 M1 이력 자체의 누락만 중단하며 생성 틱/미확인 틱은 실행 metadata·DuckDB·결과 JSON의 경고로 남긴다. 과거 정책으로 실제 틱 부재를 중단 사유로 판단했던 내용은 이 정정으로 대체했다. 승인 전 보고서는 `검증결과/data_selection/approved/report_before_approval.md`에 보존했다.

### XAUUSD+ 2025-09 BAR → SPECIAL1

기간은 2025-09-01 00:00 UTC부터 2025-10-01 00:00 UTC 직전까지다. `--yes --overlap 0 --sequential --strategies SPECIAL1`로 공개 CLI를 실행했다. 기존 구스키마 조각은 재사용하지 않았으며 녹화→차분 변환→원본 복원 해시→일반 MT5 복원→선택 재생→DuckDB/CSV 저장이 모두 끝났다.

| 항목 | 실측 |
|---|---:|
| EA 테스터 녹화 | {capture['elapsed_seconds']:.2f}초 |
| 원시 MSP3 | {capture['raw_bytes']:,} bytes |
| 차분 조각(부속 목록 포함) | {capture['stored_bytes']:,} bytes |
| 차분 변환 | {capture['conversion_seconds']:.2f}초 |
| 복원 해시 검증 | {capture['verification_seconds']:.2f}초 |
| 차분 읽기·복원·해시 검증 평균 | {capture['verification_seconds']*1000/n:.3f} ms/묶음 |
| 묶음 수 | {n:,} |
| SPECIAL1 재생·DB 저장(통합 CSV 정정 별도) | {month['elapsed_seconds']:.2f}초 |
| 녹화부터 계산·DB 저장까지(통합 CSV 정정 별도) | {wall:.2f}초 |
| 엔진 평균 / p99 상한 | {m['mean_ms']:.3f} / {m['p99_ms_upper_bound']:.1f} ms/묶음 |
| 입력 포함 worker 평균 | {m['elapsed_seconds']*1000/n:.3f} ms/묶음 |
| 알림 | {m['notifications']}건 |
| worker 최대 메모리 | {month['max_worker_memory_bytes']/1024**2:.1f} MiB |
| 합산 메모리 표본 최대 | {month['sampled_peak_total_memory_bytes']/1024**2:.1f} MiB |

- 복원 SHA256: `{capture['bundle_sha256']}`. 원본/복원 묶음 수와 바이트 해시가 정확히 같았다. 이번 임시 MSP3는 검증 뒤 삭제했고 옛 gzip은 보존했다.
- 저널 판정: `{capture['tick_evidence']['actual']}`, 1분봉 누락 0건. 실행 경고: {'; '.join(month['warnings'])}.
- 설치 EA·지표·헤더 {len(restore['before'])}개 파일의 전후 SHA256/존재 상태가 같고 일반 터미널 재실행 로그를 남겼다. 근거: `approved/mt5_restoration.json` 및 `approved/month_special1.jsonl`.
- 실행 ID `{month['run_id']}`, 결과 `{month['alerts_csv']}`. 전략 목록·코드/config 해시·EA 빌드·schema ID·WONBI_SIGMA={month['wonbi_sigma']}를 결과와 DB에 기록했다.
- 근사치: `{month['approximate']}`. BAR 입력보다 높은 관측 해상도를 선언한 Consumer 때문에 표시하며, 틱 부재 경고와 별개다.

### SPECIAL1~7: 2025-09-01~07 단독 실행

종료 경계는 2025-09-08 00:00 UTC다. 같은 새 월 조각을 사용하고 겹침 0일·각 전략 단독·1 worker로 **순서대로 각 1회** 측정했다. 기간 안 주말/휴장 시간에는 관측 묶음이 없다. 아래 시간은 선택 실행 실측이며 전체 감시 수치가 아니다. 입력 포함 평균은 worker의 스트리밍 읽기와 엔진 처리, 엔진 평균은 투입/처리만이다. 프로세스 시작·최종 DB 저장은 실행 총시간에 포함한다.

| 전략 | 묶음 | 알림 | 실행 총초 | 엔진 ms/묶음 | 입력 포함 ms/묶음 | 엔진 p99 ms | OZ_STATE | SWEEP_STATE | FVG_STATE | INDICATOR | WATCH | COMPOSER |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
{chr(10).join(rows)}

측정 원문은 `검증결과/data_selection/approved/week_special1.json`~`week_special7.json`이다. 모든 측정에서 Telegram/전략 네트워크와 run 폴더 밖 쓰기 차단을 유지했다. 성능 합격 기준은 적용하지 않았다.

### 새 실측을 사용한 용량·시간 추정

- XAU 5년(60개월): 월 {capture['stored_bytes']/1e6:.2f}MB를 선형 환산하면 **{capture['stored_bytes']*60/1e9:.2f}GB**, 녹화 {capture['elapsed_seconds']*60/3600:.2f}시간 + 변환/검증 {(capture['conversion_seconds']+capture['verification_seconds'])*60/3600:.2f}시간이다.
- XAU+NAS 각 5년(120조각): NAS도 같은 비용이라고 가정할 때 **{capture['stored_bytes']*120/1e9:.2f}GB**, 녹화 {capture['elapsed_seconds']*120/3600:.2f}시간 + 변환/검증 {(capture['conversion_seconds']+capture['verification_seconds'])*120/3600:.2f}시간이다. NAS는 실측하지 않았으며 변화량·실제 틱 다운로드·초기화 차이가 있으므로 추정이다.
- SPECIAL1 1년: 이번 한 달×12 = **{month['elapsed_seconds']*12/3600:.2f}시간/1 worker**. 12개월을 12 worker로 완벽히 나누면 겹침 없는 산술 하한은 {month['elapsed_seconds']/60:.2f}분, 기본 3거래일 겹침을 월22거래일에 비례해 더하면 약 {month['elapsed_seconds']/60*25/22:.2f}분이다. 병렬 실측이 아니며 디스크·CPU 경합/월별 편차로 더 길어진다. 녹화 비용은 별도다.

이력 정정 관련 시험은 `history_policy.xml` 4개와 `journal_policy.xml` 1개가 통과했고, 기존 실행과 중복을 제거한 관련 시험은 총 **{len(tests)}개 통과**다. 생성 틱 허용, M1 누락 중단, 다른 TF/기간 외 누락 제외, 공개 흐름의 생성 틱 실행과 검증 후 교체/복원을 확인했다.

### 최종 대조에서 발견한 통합 CSV 저장 결함과 수정

계산 완료 후 CSV·DB를 대조하면서 `COPY (SELECT ... WHERE run_id=?) TO ?`의 DuckDB 매개변수 결합 순서 결함을 발견했다. 파일명 매개변수가 SELECT보다 먼저 결합되어 코드 폴더 `Part2/실행ID`에 헤더뿐인 CSV가 생성됐다. 따라서 앞선 worker 쓰기 차단 시험만으로 부모 프로세스의 결과 저장까지 검증됐다고 볼 수 없었다.

최종 저장을 `Warehouse.export_results`의 SELECT + `fetchmany(1024)` + CSV 쓰기로 바꿨다. 실제 경로는 SQL 매개변수로 전달하지 않는다. 지정 경로, 알림 내용/정렬, 0건 헤더, 실제 runner 병합 경로, 코드 폴더 추가 파일 0건을 검사하는 시험 2개(`export_policy.xml`, `export_runner.xml`)가 통과했다.

worker CSV와 DB의 알림은 온전했으므로 이번 8개 실행과 앞선 하루 실행의 통합 CSV 9개를 DB에서 재생성했다. 잘못 생성된 94-byte 빈 파일 9개는 승인된 작업 폴더 안의 정확한 실행 ID/내용을 확인하고 증거를 보존한 뒤 제거했다. `approved/export_repair.json`, `approved/exports_before_repair`, `approved/result_consistency.json`에 근거가 있다. 계산 재실행 없이 알림 건수·내용을 복구했다. 기존 계산 코드 해시와 시간은 바꾸지 않고 `result_export_code_hash`로 저장 수정 버전을 별도 기록했다. 위 실측 시간에는 수동 CSV 정정 작업 시간이 포함되지 않는다.
'''
    report=(APP/'report_before_approval.md').read_text('utf-8')
    start=report.index('## 사용법')
    report='# 데이터 구축·선택 실행 — 수정본24\n\n사용자 승인 후 2025-09 실제 녹화→SPECIAL1 실행과 SPECIAL1~7의 9월 1~7일 단독 실측까지 완료했다. 이전 수정본은 수정하지 않았다.\n\n'+report[start:]
    lines=report.splitlines()
    lines=[('5. **2025-09 실제 신규 구축→SPECIAL1 실행 완료.** 아래 승인 후 실측의 실행 ID, 녹화·복원·재생 수치와 결과 파일을 확인한다. 이전에 완료한 하루 공개 실행 증거도 보존한다.' if line.startswith('5. **요청한 2025-09 실제 신규') else line) for line in lines]
    lines=[('3. 파일 격리: worker의 run 밖 쓰기·네트워크 차단은 유지한다. 이번 최종 대조에서 부모 프로세스의 통합 CSV가 코드 폴더에 생성되는 결함을 찾아 수정했고, 저장 경로/실제 runner 병합 시험을 추가했다. 잘못 생성된 빈 파일은 증거 보존 후 정리했다. 아래 CSV 결함 항목에 원인과 조치를 기록했다. Part1 1,343파일은 추가·삭제·변경 0건이다.' if line.startswith('3. 파일 격리:') else line) for line in lines]
    report='\n'.join(lines)+'\n'
    report=report.replace('**76개 통과**',f'**{len(tests)}개 통과**')
    a=report.index('## 참고 측정');b=report.index('JSON cProfile는',a)
    report=report[:a]+measurement+'\n'+report[b:]
    a=report.index('## 수정 목록·보존');b=report.index('추가 문서:',a)
    report=report[:a]+'## 수정 목록·보존\n\n'+files+'\n\n'+report[b:]
    report=report[:report.index('## 이어서 할 일')]+'''## 완료 및 사용 시 주의

승인된 한 달 실제 실행과 7개 전략의 1주 측정을 마쳤다. 생성 틱은 경고만 남긴다. 실제 1분봉 누락이 있으면 해당 구간을 보고하고 실행을 중단한다. BAR의 근사치 표시는 전략이 요구하는 관측 해상도에 따른 기존 규칙이다. 추가 녹화는 선택 기간의 현재 EA/schema 조각이 없을 때만 확인 후 진행한다.
'''
    (ROOT/'수정내역_데이터구축_선택실행.md').write_text(report,encoding='utf-8')
    status=read(OUT/'status.json')
    status.update(state='COMPLETE',complete=True,pending=[],recording_approved=True,tests_passed=len(tests),
        history_policy='M1_ONLY_GENERATED_TICKS_WARN',month_run_id=month['run_id'],
        week_run_ids={f'SPECIAL{i}':r['run_id'] for i,r in enumerate(weeks,1)},mt5_files_restored=True,
        result_export_bug_fixed=True,result_exports_verified=True,
        current_schema_month_restore_sha256=capture['bundle_sha256'])
    save(OUT/'status.json',status)
    save(APP/'measurement_summary.json',{'capture':capture,'month':month,'weeks':weeks,'tests':len(tests)})
    print('report completed:',month['run_id'],'week measurements',len(weeks),'tests',len(tests))

if __name__=='__main__':main()
