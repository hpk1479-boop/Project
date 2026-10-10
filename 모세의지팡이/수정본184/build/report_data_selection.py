from pathlib import Path
import csv,json,xml.etree.ElementTree as ET
ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'검증결과/data_selection'
if (OUT/'approved/complete.json').is_file():
    import runpy
    runpy.run_path(str(ROOT/'build/finalize_approved_selection.py'),run_name='__main__')
    raise SystemExit(0)
def read(name):return json.loads((OUT/name).read_text('utf-8'))
def write(name,value):(OUT/name).write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
trial=read('storage_trial/result.json');single=read('single_comparison.json');parity=read('live_replay.json')
public=read('public_day_run.json');day=read('day_conversion.json');inventory=read('source_inventory.json')
profile=read('json_profile_summary.json');plan=read('september_plan.json');imported=read('imported_day.json')
tests={}
for name in ('related.xml','selection_fixed.xml','reuse_final.xml'):
    for case in ET.parse(OUT/name).getroot().iter('testcase'):
        tests[case.get('classname')+'::'+case.get('name')]=not any(x.tag in ('failure','error') for x in case)
assert all(tests.values())
write('test_summary.json',{'unique_passed':len(tests),'failed':0,'part2_general_suites_run':False})
write('status.json',{'state':'IMPLEMENTED_AWAITING_RECORDING_CONFIRMATION','complete':False,
    'tests_passed':len(tests),'storage_month_exact':True,'day_raw_delta_alerts_equal':True,
    'all_vs_revision23_alerts_equal':True,'single_day_alerts_equal':all(x['exact'] for x in single),
    'live_replay_equal':True,'public_day_run_complete':public['run_id'],
    'pending':['XAUUSD+ 2025-09 actual recording approval','month auto-build then SPECIAL1 run (abort if history missing)',
               'current-schema month conversion measurement','SPECIAL1..7 one-week measurements'],
    'existing_september_evidence':'real ticks absent for all 22 trading days; generated ticks used',
    'part3_excluded':True})
table=[]
for item in single:
    m=item['metrics'];p=m['processor_timings']
    get=lambda name:f"{p[name]['ms_per_bundle']:.3f}" if name in p else '미생성'
    table.append('| '+ ' | '.join([item['strategy'],str(item['single_alerts']),f"{m['mean_ms']:.3f}",f"{m['elapsed_seconds']*1000/m['bundles']:.3f}",
        f"{m['p99_ms_upper_bound']:.1f}",get('OZ_STATE'),get('SWEEP_STATE'),get('FVG_STATE'),get('INDICATOR'),get('WATCH_CONDITIONS'),get('COMPOSER')])+' |')
files='\n'.join('- `'+v['path']+'`' for v in inventory['changes'])
report=f'''# 데이터 구축·선택 실행 — 수정본24

작성일: 2026-09-27. 수정본23 전체를 독립 복사했다. **구현과 보존 데이터 검증은 완료했으나, 요청한 2025-09 신규 녹화와 1주 실측은 대기 중이다. 작업 전체 완료로 판정하지 않는다.**

녹화 전에 확인받으라는 사용자 규칙에 따라 실제 녹화 승인을 요청했으며 아직 응답을 받지 않았다. MT5를 실행하거나 설치 파일을 변경하지 않았다. 기존 2025-09 저널에는 실제 틱이 22거래일·30,146분봉 전 구간에서 없어서 생성 틱을 사용했다는 기록이 있다. 새 녹화에서도 누락되면 백테스트를 시작하지 않는다. 이미 확보된 일별 실제 틱 자료와 한 달 구 스키마 자료의 저장 시험은 독립적으로 완료했다.

## 사용법

`Part2/BACKTEST CONTROL.pyw`에서 종목·기간·BAR/TIMER·전략을 고르고 실행한다. BAR가 기본이다. 데이터 구축 체크박스 기본은 꺼짐이다. 켜면 전체 요청 조각을 다시 녹화한다. 필요한 녹화가 있을 때 달/일 목록·실측 기반 예상 시간·임시 공간·차분 예상 크기를 확인 창에 표시한다. 확인 전에는 MT5를 호출하지 않는다. 별도 녹화 메뉴는 없다.

CLI는 Part2 폴더에서 설치된 Python으로 실행한다. 종료일은 UTC 배타적 경계다.

```powershell
python -B -m event_backtest plan --symbol XAUUSD+ --start 2025-09-01 --end 2025-10-01 --strategies SPECIAL1 --overlap 0
python -B -m event_backtest run --symbol XAUUSD+ --start 2025-09-01 --end 2025-10-01 --strategies SPECIAL1 --overlap 0 --yes
python -B -m event_backtest run --scenario scenarios/xau_selected.json --yes
python -B -m event_backtest strategies
python -B -m event_backtest compare --left 실행ID1 --right 실행ID2 --output comparisons/alerts.csv
```

`--yes`는 녹화 승인이다. 없으면 계획을 표시하고 종료 코드 2로 멈춘다. GUI는 검토한 계획의 토큰을 전달하며, 계획이 바뀌면 재확인이 필요하다. 이력 누락은 종료 코드 3으로 멈춘다. `--rebuild`가 GUI 체크박스에 해당한다. 전략 전체 선택은 `--strategies ALL`을 명시한다. 기간 생략 시 전월 말까지 최근 12개월이다. 창고 루트는 화면이나 컴퓨터별 설정에서 지정하며 목록에는 루트 기준 상대 경로만 저장한다.

시나리오는 `strategies` 목록을 사용한다. 명령마다 `strategy`를 선언하고 같은 항목을 선택해야 한다. 예: `{{"strategies":["OZ"],"commands":[{{"strategy":"OZ","chat_id":"BACKTEST","text":"골드 1분 무지성 올존 계속 알려줘"}}]}}`. 전체 선택은 선언된 모든 명령을 허용한다. 결정적 해석이 안 되는 명령은 네트워크로 넘기지 않고 오류다.

공식 설정 전략은 `OFFICIAL_SPECS` / `OFFICIAL_CHAIN_SPECS`의 JSON 정의를 `OFFICIAL:spec_id` / `CHAIN:spec_id`로 선택한다. 기존 사용자 config는 변경하지 않았다. 현재 config에는 별도 공식 JSON 정의가 없으며 SPECIAL이 자체 등록하는 공식 specs/chain specs는 선택한 SPECIAL에서만 로드한다.

## 저장 방식과 구축 절차

- 완료된 달은 월, 진행 중인 달은 일 조각이다. EA ex5 SHA256와 schema ID, 녹화 모드·관측 간격이 일치하고 파일 무결성이 확인되어야 재사용한다.
- 같은 EA·스키마의 기존 MSP3/gzip은 재녹화하지 않고 차분 변환한다. 다른 EA·스키마인 기존 6개월은 빈 조각이다.
- 현재 스키마: 50열, `0x379D2170`. EA와 schema 파일을 변경하지 않았다.
- 파일 차분 `MSD1`: 원래 Wire 헤더·CRC와 각 피드의 봉 시각 매핑, 새 시각, 바뀐 uint64 셀을 저장한다. 제거된 행은 새 행 매핑에서 빠진 것으로 표현한다. 원래 값으로 부동소수점 계산을 하지 않아 NaN payload·EMPTY·음의 0을 구분한다.
- FULL/ROW/HEARTBEAT와 묶음 순서·seq·관측 시각을 그대로 복원한다. 한 시점 묶음과 피드당 최신 창만 보관하므로 스트리밍이다.
- 검증 해시는 `LE int64 관측시각 + LE uint32 바이트수 + 원래 묶음 바이트`를 순서대로 SHA256에 넣는다. 원본/복원 묶음 수와 전체 해시가 일치해야 게시한다.
- 새 세대는 고유 경로에 먼저 만든다. 복원 검증·저널 확인 뒤에만 목록을 교체한다. 실패 시 옛 목록과 파일을 보존한다. 과거 gzip과 옛 세대 파일은 자동 삭제하지 않는다. 이번 실행이 만든 임시 MSP3만 복원 검증 후 지운다.
- `captures.duckdb`는 녹화 목록, `captures/.../capture.delta.gz`는 시장 데이터, `results.duckdb`는 실행·알림·시간이다. 과거 `event_backtest.duckdb` 목록은 읽기 전용으로 가져온다. worker별 CSV/결과는 `runs/실행ID/chunk_*`, 합친 결과는 `runs/실행ID/alerts.csv`다. 코드·설정·EA 해시, schema, 전략 목록, WONBI_SIGMA를 실행 기록에 넣는다.

### 한 달 저장 시험

동일한 2025-09 XAU 구48열 보관본 30,146묶음을 **저장 코덱 시험에만** 사용했다. 구 스키마를 새 엔진으로 재생하지 않았다. 신규50열 한 달 실측을 한 것으로 표시하지 않는다.

| 방식 | 크기 | 읽기 ms/묶음 | 원본 복원 |
|---|---:|---:|---|
| 파일 차분 + gzip | {trial['size_bytes']['file']:,} bytes | {trial['read']['file']['ms_per_bundle']:.3f} | 정확 일치 |
| DuckDB 차분 (UBIGINT 원래 비트) | {trial['size_bytes']['duckdb']:,} bytes | {trial['read']['duckdb']['ms_per_bundle']:.3f} | 정확 일치 |

파일 방식이 더 작고 더 빨라 채택했다. 상대 방식의 2배 읽기 지연 예외가 필요하지 않았다. 시험용 DuckDB 구현과 생성 DB는 삭제했고 결과 수치·해시는 `검증결과/data_selection/storage_trial/result.json`에 보존했다. 전체 코덱 비교/복원은 {trial['total_seconds']:.1f}초였다. 공통 인코딩 {trial['write_seconds']['encode']:.2f}초, 파일 쓰기 {trial['write_seconds']['file_write']:.2f}초, DuckDB 쓰기 {trial['write_seconds']['duckdb_write']:.2f}초이며 공통 원본 읽기 시간은 별도다.

새50열 하루는 1,377묶음·차분 {imported['stored_bytes']:,} bytes, 변환 {day['conversion_seconds']:.2f}초 + 복원 검증 {day['verification_seconds']:.2f}초였다. 기존 압축 입력 794MB를 읽었으며 이것을 원시 MSP3 크기로 잘못 표기하지 않는다. 이전 작업에서 같은 하루 원시 MSP3는 1.320GB였다.

## 선언된 전략 의존관계

| 선택 | Consumer/처리기 | COMPOSER의 추가 입력 |
|---|---|---|
| SPECIAL1 | INDICATOR, OZ, OZ_STATE, SWEEP_STATE/SWEEP | WONBI |
| SPECIAL2 | SWEEP, SWEEP_STATE, OZ/OZ_STATE | SWEEP setup |
| SPECIAL3 | FVG/FVG_STATE, WATCH_CONDITIONS, OZ/OZ_STATE, SWEEP_STATE/SWEEP | 시간연쇄·MA cross |
| SPECIAL4 | OZ/OZ_STATE, SWEEP_STATE/SWEEP | WONBI 및 기존 SPECIAL4 상주 handler |
| SPECIAL5 | OZ/OZ_STATE, SWEEP_STATE/SWEEP | 기존 SPECIAL5 부모/자식 handler |
| SPECIAL6 | FVG/FVG_STATE, OZ/OZ_STATE, SWEEP_STATE/SWEEP | EMA/HMA MA 상태 |
| SPECIAL7 | INDICATOR, OZ/OZ_STATE, SWEEP_STATE/SWEEP | TREND |
| OZ 명령 | OZ/OZ_STATE, SWEEP_STATE/SWEEP | 외부유동성 동적 감시 포함 |
| INDICATOR 명령 | INDICATOR | TREND/metric |
| FVG / SWEEP 명령 | 각 Consumer와 해당 STATE | 해당 사실만 |
| Watch 명령 | 선언된 복합·시간연쇄가 후속으로 등록할 수 있는 전체 family | 가변 MA/복합 조건 |
| 공식 spec / chain | 조건 정의에서 의존관계 도출 | 선택된 정의만 등록 |

COMPOSER는 모든 선택에 존재한다. 선택되지 않은 SPECIAL 모듈은 로드하지 않고, 대상 밖의 매 묶음 특징 갱신과 family 사실 처리를 하지 않는다. 동적 명령이 선언 밖의 처리기를 요구하면 오류로 드러내므로 조용히 누락시키지 않는다. SPECIAL6의 실제 FVG 의존 누락이 이 검사에서 발견되어 선언을 보완하고 재검증했다. 기본 LIVE는 `selection=None`, 즉 ALL이며 기존 동작을 유지한다.

## 검증 결과

1. 저장: 구 스키마 한 달의 두 코덱 및 새 스키마 하루 복원 해시 정확 일치. 새 하루 MSP3 ↔ 차분의 최종 알림 12건, ID·시각·전략·프로필·방향·문구·수신자 전 필드 일치.
2. 선택: 수정본23 전체 program 격리 복사본과 수정본24 ALL의 같은 하루 알림 12건 일치. SPECIAL1~7 단독 vs ALL 내 해당 전략 알림 모두 일치, arbitration 차이 0건. 건수는 `[0,0,0,0,1,0,0]`이다. SPECIAL5는 발생 표본이며 나머지는 이 하루에서 비발생 표본이므로 모든 조건 발생을 입증했다고 주장하지 않는다.
3. 파일 격리: worker에서 쓰기 audit hook으로 run 밖 파일 열기/생성/변경/삭제 및 자식 실행을 차단했다. 실제 하루 ALL 및 7개 단독과 공개 CLI 실행이 통과했다. 초기 STAFF/OZ logger만 호스트가 지정한 run/logs로 연결했다. LIVE의 기본 로그 위치는 그대로다. 네트워크는 차단했고 실제 Telegram 전송은 없다.
4. 자동 구축: 빈 조각, EA/schema 불일치, 동일 스키마 gzip 변환 계획, 미승인 시 MT5 호출 0회, 재구축 실패 시 이전 목록 유지, 검증 후 교체, 복원 호출, 이력 누락 시 실행기 호출 0회, 창고 이동 후 목록/재생 동일, GUI 기본값과 확인→run 순서를 시험했다. 관련 시험 **{len(tests)}개 통과**. Part2 일반 회귀 4묶음은 실행하지 않았다.
5. **요청한 2025-09 실제 신규 구축→SPECIAL1 실행은 미실행(녹화 승인 대기)**. 대신 이미 검증된 현재 스키마 하루를 창고에 등록하여 공개 CLI의 조각 재사용→SPECIAL1 재생→worker 결과→결과 DB/CSV 저장을 끝까지 확인했다. 실행 ID `{public['run_id']}`, 결과 `{public['alerts_csv']}`. 이를 9월 한 달 실측 통과로 대체하지 않는다.

LIVE 수신=재생 추가 확인: 합성 240초 5,794신호·19알림, 실제 TIMER 239초 230묶음·5,668신호·13알림에서 전체 신호 해시·알림·출력 모두 같다. 증거 `live_replay.json`. 합성 LIVE 첫 검증 중 코드 주석 변경 때문에 source 해시 감시가 실패한 원시 결과는 남겼으며, 변경을 끝내고 해당 구간만 재실행하여 통과했다.

기존 테스트 수정은 `tests/test_part2_event_runner.py`의 48열 고정 입력을 레지스트리 열 수로 바꾸고, 재사용 표본을 검증된 차분으로 만들었으며, EA 불변 비교 대상을 작업 직전 수정본23으로 바꾼 것이다. 순서·중복·이음새·열·알림 검사 항목은 줄이지 않았다. 새 동작은 `tests/test_data_selection.py`에 추가했다.

## 참고 측정

**다음 표는 요청한 1주 실측이 아니라, 확보된 새 스키마 2026-09-25 하루 BAR(각 1,377묶음) 결과다.** 겹침 0일, 동일 입력, 각 단독 1회다. 개발 중 검증 프로세스가 일부 겹친 참고 수치이며 성능 게이트가 아니다. 입력 포함은 worker 전체 소요/묶음, 엔진은 투입·처리 루프 구간이다.

| 전략 | 알림 | 엔진 ms/묶음 | 입력 포함 ms/묶음 | 엔진 p99 ms | OZ_STATE | SWEEP_STATE | FVG_STATE | INDICATOR | WATCH | COMPOSER |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
{chr(10).join(table)}

공개 CLI 하루 SPECIAL1은 {public['elapsed_seconds']:.2f}초, worker 최대 {public['max_worker_memory_bytes']/1024**2:.1f}MiB, 동시 프로세스 합산 표본 최대 {public['sampled_peak_total_memory_bytes']/1024**2:.1f}MiB였다. BAR 입력은 일부 전략의 진행봉 해상도보다 낮으므로 근사치 표시를 유지한다.

- 한 달 녹화 시간: 이번 신규 실측 없음. 이전 동일 구간 203.50초(생성 틱)만 있고, 새50열 확인 계획은 약 {plan['estimate']['recording_seconds']/60:.2f}분·임시 {plan['estimate']['temporary_msp3_bytes']/1e9:.2f}GB·여유 {plan['estimate']['recommended_free_bytes']/1e9:.2f}GB다. 변환·검증 시간 별도.
- 5년 XAU+NAS 각 60개월 추정: 구48열 월 차분을 열 비율로 환산하면 약 16.4GB, 새50열 하루×월22거래일 단순 환산은 약 30GB다. 일별 첫 FULL 비용과 두 종목의 변화량 차이 때문에 범위 추정일 뿐이다. NAS 실측은 없고 동일 비용 가정이다. 녹화 약 8.14시간(244.2초×120조각) + 변환·검증이며 신규 한 달 확인 후 갱신해야 한다.
- 1년 SPECIAL1 추정: 하루 공개 실행×252거래일이면 약 {public['elapsed_seconds']*252/3600:.2f}시간/1코어. 이 컴퓨터는 물리14코어이고 월12개 청크라 동시12개가 상한이다. 완벽한 균등 분배와 3거래일 겹침(24/21)을 가정한 낙관적 하한은 약 {public['elapsed_seconds']*252/3600/12*24/21:.2f}시간이다. 디스크 경합·코어 성능 차이·상태 워밍업·월별 불균형을 포함한 실측이 아니므로 보장하지 않는다.
- **SPECIAL1~7의 1주 측정은 아직 하지 않았다.** 이력 누락 구간을 몰래 실행하거나 하루 수치를 1주 실측으로 표시하지 않는다.

JSON cProfile는 하루 ALL 1회다. json.dumps 누적 13.37초, loads 1.92초(하위 encode/decode와 중복 합산 금지). 메모리 state write 중 fact_revisions 32,437회 / runtime read 64,872회, receipts write 1,602회 / read 2,401회라 삭제 대상이 아니다. 생성자 복원만 사용하는 private watch/timed chain/active children/FVG-created/SPECIAL4 상태 5키만 백테스트 dispatch에서 직렬화를 생략한다. 실시간 읽기 시도가 생기면 오류로 감지한다. LIVE 저장과 계산식은 그대로이며 `json_profile_summary.json`, `runs/raw_all_profile/json_profile.json`에 근거를 남겼다.

## 수정 목록·보존

{files}

추가 문서: `AGENTS.md.txt` 최신 범위, 이 보고서, `검증결과/data_selection` 시험·측정 기록. 복사본의 오래된 보고서는 삭제하지 않았다.

소스 무결성 체인 `Part1/audit/remediation/43-data-selection`, 새 오류 0. `build/part1_immutable_sha256.json` 1,343파일 일치. 기존 무결성 진단은 보존했다. EA·Wire/schema·SPECIAL 본문·event_composer_domain 판정 본문·config/BTCUSD는 해시 동일하다. monitor_OZ는 logger 목적지 한 줄만 바뀌었고 혼합 줄바꿈을 보존했다. 이전 수정본은 실행·수정하지 않았으며 비교는 수정본24 내부 격리 복사본에서 수행했다.

Part3 제외 규칙은 작업 시작(2026-09-27)부터 적용했다. Part3 읽기·시험·무결성 대조는 하지 않았다.

## 이어서 할 일

녹화 승인 후 동일 CLI의 2025-09 SPECIAL1 명령을 실행한다. 저널의 실제 틱 누락이 재현되면 차분 검증 자료와 누락 구간을 보존하고 백테스트를 시작하지 않는다. 누락을 허용하는 예외를 임의로 추가하지 않는다. 정상 이력이 확보되어야 한 달 전체 실행과 1주 단독 실측을 완료할 수 있다.
'''
(ROOT/'수정내역_데이터구축_선택실행.md').write_text(report,encoding='utf-8')
print('report written; task remains incomplete pending recording confirmation')
