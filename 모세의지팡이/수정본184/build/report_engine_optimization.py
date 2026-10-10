"""Render the report from completed evidence, with explicit pending sections."""
from pathlib import Path
import argparse,json
ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'검증결과/engine_optimization'
def read(path):return json.loads(path.read_text('utf-8'))
def table(headers,rows):
    return '\n'.join(['| '+' | '.join(headers)+' |','| '+' | '.join(['---']*len(headers))+' |']+
        ['| '+' | '.join(str(v).replace('|','\\|').replace('\n',' ') for v in row)+' |' for row in rows])
def main():
    p=argparse.ArgumentParser();p.add_argument('--warehouse',required=True);a=p.parse_args();warehouse=Path(a.warehouse)
    complete=(OUT/'measurements_complete.json').exists() and (OUT/'integrity_registration.json').exists()
    sections=['# 엔진 최적화 — 수정본25',
        ('완료. COMPOSER A·엔진 기본 비용·공통 STAFF 디코드만 변경했다. COMPOSER B와 전략생성기는 시작하지 않았다.' if complete else
         '진행 중. 아래 확정된 검증 결과와 진행 상태를 기록하며, 단독/1년 측정과 무결성 등록이 끝나기 전에는 완료 판정이 아니다.'),
        '## 범위와 보존',
        '- 수정본24를 전체 복사하고 누락 파일도 보충했다. 실행 비교는 `검증결과/engine_optimization/before_runtime`의 독립된 전체 Part1/program에서 수행했다. 이전 수정본은 실행·수정하지 않았다.\n'
        '- 수정본25의 Part3만 삭제했다. 루트 AGENTS.md·CLAUDE.md와 수정본25 AGENTS 두 파일에 전략생성기 예약 자리 규칙을 반영했다. 원본 Part3는 그대로다.\n'
        '- Part1·Part2 실행 코드의 Part3 호출은 없었다. `Part2/validation_suite/test_staff_s1.py`의 폴더 검사 두 곳만 Part2 대상으로 바꿨다. Part1에서 Part2/Part3 import를 금지하는 검사는 보존했다.\n'
        '- EA·지표·Wire 열과 schema·차분 저장 형식·SPECIAL 판정 코드·사용자 config·monitor_OZ의 줄바꿈은 변경하지 않았다. 실제 텔레그램 및 전략 네트워크는 시험/재생에서 차단했다.',
        '## 1. 먼저 수행한 프로파일',
        'XAUUSD+ 2025-09-01~08(UTC, 종료일 제외), 6,726묶음, SPECIAL1과 SPECIAL4를 각각 단독 cProfile했다. 아래는 누적 시간 상위 20개다. 누적 시간은 자식 호출을 포함하므로 서로 더하면 안 되며, cProfile 시간은 일반 실측 시간과 구분한다.']
    for name in ('special1','special4'):
        rows=read(warehouse/f'runs/engineopt_before_profile_{name}/profile_functions.json')
        sections.extend([f'### {name.upper()}',table(['함수','호출 수','자체 초','누적 초'],
            [(r['function'],f"{r['calls']:,}",f"{r['self_seconds']:.3f}",f"{r['cumulative_seconds']:.3f}") for r in rows[:20]])])
    sections.extend(['SPECIAL4의 사용하지 않는 Fact 선행 계산은 292.648초였다. SPECIAL1의 원비 갱신은 371.660초, Fact 선행 계산은 346.298초였다. 이 근거로 불필요한 Fact 선행 계산, 원비 DataFrame 준비, 반복 Wire 디코드 순으로 정리했다.',
        '## 2. 처리별 변경',
        table(['영역','변경','유지한 의미'],[
            ('엔진','Fact는 등록/권한 검증 후 실제 board.fact 호출에서만 지연 계산한다. 선언된 모든 Fact를 모든 TF/내부 SIGNAL마다 선행 계산하던 루프를 제거했다.','commit → 처리기 → Consumer 순서, 타이머, engine_seq·signal_id 산식 유지'),
            ('STAFF','불변 bytes 위의 memoryview로 자식 프레임을 디코드한다. receive_publication이 한 번 검증한 묶음을 원자적으로 저장하고 같은 publication의 Snapshot/누락 기록을 반환한다.','CRC·schema·seq·재연결·epoch·잘린 프레임 거부와 전체 rollback 유지'),
            ('재생 입력','이음새 seq 변경 시 배열을 다시 pack하지 않고 검증된 헤더만 수정한다. 바깥 CRC를 재작성하며 결과는 STAFF에서 다시 검증한다. 변경이 없으면 원본 bytes 재사용.','STAFF 우회 없음, 프레임 삭제/합치기 없음, 저장 형식 동일'),
            ('Records','event_receipts/fact_revisions/composer_signatures를 상주 dict로 두고 단일 값만 복사한다. JSON은 명시적 checkpoint/호스트 저장에서만 만든다.','기존 JSON 복원 및 중복 응답 의미 유지'),
            ('보존','receipts와 출력 논리 중복 기록은 해당 심볼의 source_time 기준 3일 보존. 정확히 3일인 항목은 유지하고 이를 넘으면 정리한다.','전략 활성 상태·signature와 실제 답글 참조는 삭제하지 않음; 논리 메시지 sequence는 계속 증가'),
            ('관심 사실','현재 spec·연쇄·감시의 TREND/FVG/SWEEP 종목·TF와 SWEEP watch_id를 확인하고 불필요한 FACT_SNAPSHOT을 plain/deepcopy 전에 거른다.','상태 변화·조회·명령 이벤트는 원래 경로로 처리'),
            ('시장 입력','원비는 WONBI_BANDS, OUT은 native 열, MA는 WatchMAStore의 기존 Fact를 읽는 NumPy 뷰로 공급한다. 동일 Snapshot 하트비트는 계산을 생략하고 MA 유효시각은 갱신한다.','확정/진행봉 위치와 비교식, 문구·ID 산식 유지'),
            ('Watch','spec·연쇄·SPECIAL handler가 없는 Composer는 시장 입력 갱신을 호출하지 않는다. 등록·출력·복원·타이머는 유지한다.','관심 대상 외 TF로 조건 평가하지 않음')]),
        'SPECIAL4/5 플러그인이 직접 요구하는 기존 chart/maintenance DataFrame API는 유지했다. COMPOSER 자체의 원비·OUT·MA·복합조건 봉 시각 입력은 NumPy로 바꿨지만, SPECIAL 판정 본문과 구조는 이번 범위에서 바꾸지 않았다. 이 플러그인 입력 준비는 전체 선택의 남은 비용이다.',
        '## 3. 검증'])
    if (OUT/'live_replay.json').exists():
        lr=read(OUT/'live_replay.json')
        sections.extend(['관련 로직 시험 105개와 추가 원자적 rollback/기존 Records 파일 I/O 시험 8개가 모두 통과했다. 변경과 무관한 Part2 일반 회귀 4묶음은 실행하지 않았다.',
            table(['입력','묶음','전체 SIGNAL','최종 알림','LIVE = 재생'],[(k,v['bundles'],v['signals'],v['notifications'],all(v['fields_equal'].values())) for k,v in lr.items()]),
            '신호 전체 SHA256, 알림 목록과 시각·방향·문구·수신자·signal_id, mock 출력, 오류 목록을 비교했다. 오류 0건이며 실제 네트워크 전송은 없다. 증거: `검증결과/engine_optimization/live_replay.json`, `related_tests.xml`, `records_io_tests.xml`.',
            '시험에는 3일 보존 경계·중복·체크포인트/호스트 복원, 미사용 Fact, 가변 MA의 열 이름/값/하트비트 유효성, 동적 관심 사실, Watch 무등록 계산 생략, CRC·잘림·schema·seq·재연결 및 묶음 rollback이 포함된다.',
            '기존 시험 수정: test_event_e1.py와 test_event_e2_domains.py의 오래된 48열 합성 fixture를 현재 50열/이름 기반 인덱스로 고쳤다. E1의 모든 Fact 선행 호출을 고정하던 검사는 실제 Fact 요청 시의 값 공유·처리기→Consumer 순서 검사로 대체했다. test_event_e2_domains.py 전체 실행은 추가하지 않았다. Part3 폴더 검사는 제거하되 Part1 독립성 검사는 유지했다.'])
    if (OUT/'alert_comparisons.json').exists():
        comparisons=read(OUT/'alert_comparisons.json')
        sections.extend(['### 첫 주 알림 비교',table(['선택','24 알림','25 알림','전체 필드·순서 동일','추가','삭제'],
            [(k,v['before_alerts'],v['after_alerts'],v['exact_order_equal'],sum(x['count'] for x in v['added']),sum(x['count'] for x in v['removed'])) for k,v in comparisons.items()]),
            '차이 전문(시각·문구·수신자·signal_id 포함): `검증결과/engine_optimization/alert_comparisons.json`. 원인 분류는 `alert_difference_analysis.json`을 함께 본다.'])
        if (OUT/'alert_difference_analysis.json').exists():
            analysis=read(OUT/'alert_difference_analysis.json')
            sections.append(table(['선택','분류','ID만 다른 알림','내용 변경 삭제/추가'],[
                (k,v['classification'],sum(p['count'] for p in v['id_only_pairs']),
                 f"{v['unpaired_removed']}/{v['unpaired_added']}") for k,v in analysis['comparisons'].items()]))
            sections.append('기존 원비 tf_set 순회에서 전역 touch sequence가 부여되어 프로세스 해시 시드가 다르면 하위 ID가 달라질 수 있다. 별도 seed=0 첫 주 전체 재생은 수정 전후 14건 모두 ID·순서까지 일치했다. 작은 동일 입력에서 seed=0/1로 바꾸면 양쪽 수정본 모두 같은 방식으로 touch_id가 달라지는 것도 재현했다. 판정 순서·ID 산식은 수정하지 않았다. 근거: controlled_seed_comparison.json, wonbi_order_probe.json.')
    else:sections.append('첫 주 전체 선택과 SPECIAL1~7 단독 최종 비교는 측정 순서에 따라 진행 중이다.')
    if (OUT/'market_memory/result.json').exists():
        mem=read(OUT/'market_memory/result.json');samples=mem['samples'];last=samples[-1]
        sections.extend(['### LIVE Composer 메모리',
            '분당 상태 전환 17,280건/12일의 고정 범위 시험에서 receipts는 4일차 이후 4,321건으로 고정됐다. JSON 크기는 약 1.37MB, RSS는 약 85MB였다.',
            f"추가로 14일 합성 시장을 15분 간격 {mem['bundles']:,}묶음으로 실제 PipeReceiver → STAFF → 전체 전략에 넣었다. {last['signals']:,} SIGNAL과 {last['notifications']} 알림이 발생했고 오류 0건이다.",
            table(['일','receipts','fact scopes','signatures','출력 중복 기록','활성 자식','기록부 KB','프로세스 RSS MiB'],
                [(s['day'],s['receipts'],s['fact_scopes'],s['signatures'],s['notification_dedup'],s['active_children'],f"{s['records_bytes']/1000:.1f}",f"{s['rss_bytes']/2**20:.1f}") for s in samples]),
            '최근 3일 이벤트 밀도에 따라 기록 수는 변동하지만 누적 전체 이력을 쌓지 않는다. signatures는 마지막 상태를 보관하며 38개에서 안정됐다. RSS는 NumPy·다른 처리기·합성 원천도 포함하므로 Composer 전용 사용량은 아니다. 14일 표본 검증이며 무기한 상한을 실측한 것은 아니다. 증거: `memory/result.json`, `market_memory/result.json`.'])
    sections.extend(['## 4. 전후 단독 실측',
        '2025-09-01~08, BAR, 6,726묶음, 워밍업 0일, 각 1회 단독. 전 수치는 수정본24의 완료 실측, 후 수치는 녹화와 다른 재생이 종료된 뒤 직렬로 측정했다. cProfile을 켜지 않았다. 주간 표의 전체 시간은 run_chunk의 재생 루프: 입력 복원·STAFF·엔진·알림 쓰기를 포함하고 호스트 초기화와 최종 DB 병합은 제외한다. 엔진 시간은 묶음 투입부터 내부 이벤트 처리 완료까지다. 1년 공개 CLI 실측은 별도 표에 전체 경과도 기록한다.'])
    perf=[];details=[]
    for i in range(1,8):
        path=warehouse/f'runs/engineopt_after_week_special{i}/result.json'
        if not path.exists():continue
        old=read(ROOT/f'검증결과/data_selection/approved/week_special{i}.json')['chunks'][0];new=read(path)
        for label,r in [('24',old),('25',new)]:
            proc=r['processor_timings'];cost=lambda name:proc.get(name,{}).get('ms_per_bundle',0.)
            total=r['elapsed_seconds']/r['bundles']*1000
            perf.append((f'SPECIAL{i}',label,f"{r['elapsed_seconds']:.2f}",f'{total:.3f}',f"{r['mean_ms']:.3f}",
                f"{total-r['mean_ms']:.3f}",f"{r['mean_ms']-sum(v['ms_per_bundle'] for v in proc.values()):.3f}",r['notifications'],f"{r['max_memory_bytes']/2**20:.1f}"))
            details.append((f'SPECIAL{i}',label,*[f'{cost(name):.3f}' for name in ('OZ_STATE','SWEEP_STATE','FVG_STATE','INDICATOR','WATCH_CONDITIONS','COMPOSER')]))
    if perf:
        sections.extend([table(['선택','수정본','전체 초','입력 포함 ms/묶음','엔진 ms/묶음','엔진 밖 ms/묶음','엔진 내부 기타 ms/묶음','알림','최대 RSS MiB'],perf),
            table(['선택','수정본','OZ_STATE','SWEEP_STATE','FVG_STATE','INDICATOR','WATCH','COMPOSER'],details),
            '엔진 밖 시간은 순수 차분 읽기만의 시간이 아니라 STAFF 디코드·호스트/출력 비용을 포함한 차이다. 처리기별 값의 단위는 ms/묶음이다. 실제 필요한 Fact가 소비자의 board.fact 호출에서 계산되므로 그 시간은 이제 INDICATOR 등 호출한 처리기 안에 포함된다. 이전의 엔진 기타 비용과 처리기 비용 간 계측 위치가 달라졌으므로 전체 엔진 시간도 함께 본다. 측정 원문은 창고 `runs/engineopt_after_week_special1`~`special7`과 프로젝트 `검증결과/data_selection/approved/week_special*.json`이다.',
            '속도 개선이 RSS 감소를 뜻하지는 않는다. 예를 들어 SPECIAL1의 최대 RSS는 약 278→383MiB로 증가했다. 상주 기록과 불변 버퍼 공유를 포함한 전체 프로세스 실측값이며, Composer 기록부의 장기 보존 상한 시험과 구분한다.'])
    else:sections.append('단독 성능 측정 대기 중. 녹화와 CPU 경쟁 중인 진단 재생 시간은 이 표에 사용하지 않는다.')
    sections.append('## 5. 1년 자동 구축·월별 병렬 실측')
    if (OUT/'year_build/complete.json').exists():
        built=read(OUT/'year_build/complete.json');plan=read(OUT/'year_build/plan.json');new_starts={c['start'] for c in plan['record']}
        caps=sorted(built['captures'],key=lambda c:c['start']);fresh=[c for c in caps if c['start'] in new_starts]
        sections.extend([f"XAUUSD+ 2024-10-01~2025-10-01 BAR. 기존 2025-09 조각을 재사용했고, 첫 월의 워밍업 3거래일 때문에 2024-09도 구축했다. 새 조각 {len(fresh)}개, 전체 구축 실제 경과 {built['elapsed_seconds']:.2f}초.",
            table(['조각','용도','녹화 초','변환 초','복원 검증 초','저장 MB','복원 해시','틱 기록'],[
                (c['start'][:7],'워밍업' if c['start']<'2024-10-01' else '재사용' if c['start'] not in new_starts else '본 기간',
                 f"{c.get('elapsed_seconds',0):.2f}",f"{c.get('conversion_seconds',0):.2f}",f"{c.get('verification_seconds',0):.2f}",
                 f"{c.get('stored_bytes',0)/1e6:.2f}",c.get('reconstruction_verified',False),c.get('tick_evidence',{}).get('actual','UNKNOWN')) for c in caps]),
            f"새 조각 합계: MT5 녹화 {sum(c.get('elapsed_seconds',0) for c in fresh):.2f}초, 변환 {sum(c.get('conversion_seconds',0) for c in fresh):.2f}초, 복원 검증 {sum(c.get('verification_seconds',0) for c in fresh):.2f}초. 전체 구축 경과에는 터미널 종료/복원, 파일 이동과 자동화 준비도 포함된다.",
            '생성 틱은 경고만 남겼고 M1 이력 누락 검사 후 실행한다. 새 조각은 원 MSP3 묶음 해시와 무손실 복원 해시를 확인한 뒤 임시 원본만 삭제했다. 기존 gzip 보관본은 삭제하지 않았다. MT5 설치 파일 복원 해시 결과: `year_build/mt5_restoration.json`.'])
    else:sections.append('승인된 1년 구축 진행 중. `검증결과/engine_optimization/year_build/progress.jsonl`에 진행과 완료 조각을 기록한다.')
    if (OUT/'year_special1.json').exists():
        year=read(OUT/'year_special1.json');chunks=year['chunks']
        first_progress=None
        with (OUT/'year_special1.jsonl').open(encoding='utf-8') as log:
            for line in log:
                try:event=json.loads(line)
                except ValueError:continue
                if event.get('event')=='RUN_PROGRESS':
                    first_progress=event['elapsed_seconds'];break
        sections.extend([f"SPECIAL1 1년 실제 실행 ID: `{year['run_id']}`. 물리 코어 기준 최대 worker 설정 {year['cores']}, 월 작업 {len(chunks)}개, 기본 겹침 3거래일.",
            table(['항목','실측'],[('공개 CLI 전체 경과 초',f"{read(OUT/'year_special1_wall.json')['seconds']:.2f}"),
                ('runner.run 전체 초',f"{year['elapsed_seconds']:.2f}"),('병렬 재생·결과 병합 초',f"{year['replay_seconds']:.2f}"),
                ('검증·준비 초',f"{year['verification_and_setup_seconds']:.2f}"),
                ('병렬 재생 시작→첫 진행률 초',f'{first_progress:.2f}' if first_progress is not None else '없음'),
                ('워밍업 포함 처리 묶음',sum(c['bundles'] for c in chunks)),
                ('최종 알림',sum(c['notifications'] for c in chunks)),('샘플링한 전체 최대 RSS MiB',f"{year.get('sampled_peak_total_memory_bytes',0)/2**20:.1f}"),
                ('근사치 표시',year['approximate'])]),
            f"결과 CSV: 창고 `{year['alerts_csv']}`. 실행·알림·타이밍은 창고 DuckDB에도 저장했다. 본 수치는 선택한 SPECIAL1 백테스트 시간이며 녹화·변환 시간과 구분한다.",
            'BAR는 진행봉을 요구하는 전략에 대해 기존 근사치 표시를 유지한다. 생성 틱 경고와 조각 목록은 `year_special1.json` 및 해당 run에 남긴다.',
            '첫 진행률까지의 시간에는 프로세스 시작과 앞선 월 조각의 초기 복원이 포함된다. 현재 CaptureInputs는 워밍업 시작 전 자료도 조각 첫 부분부터 복원·STAFF 검증하며 엔진 투입만 보류한다. 월별 작업마다 이 앞부분 읽기가 반복되어 1년 실측에서 큰 비용으로 나타났다. 이번에는 저장 형식과 조각 시작 방식은 유지했다.'])
    else:sections.append('1년 병렬 재생은 구축·검증·직렬 실측 완료 후 실행한다. 아직 추정치를 실측값으로 쓰지 않는다.')
    sections.extend(['## 6. 남은 기존 제약과 다음 병목',
        '- SPECIAL4/5 단독 선택의 기존 의존성 선언에는 CHAINS가 없다. 따라서 `_update_local_chain_events`의 플러그인 poll이 단독 선택에서 호출되지 않는다. SPECIAL4의 30분 cycle과 SPECIAL5의 maintenance에 영향을 주는 기존 제약이다. 이번에는 선택 규칙·전략 동작을 바꾸지 않았으며, 해당 단독 시간과 알림 건수를 기능 완전성의 증거로 해석하지 않는다.\n'
        '- SPECIAL2는 OZ·SWEEP 의존이며 원비·MA 시장 입력을 요구하지 않는다. 따라서 이번 NumPy 원비·MA 입력 전환의 직접 이득이 적고, COMPOSER 8.782ms/묶음이 남았다. 단독 실행 전체는 350.61→193.37초로 줄었다. 상세 판정·조정 경로의 추가 최적화는 이번에 하지 않았다.\n'
        '- OZ 판정과 실제 사용하는 ATR Fact 계산도 남은 비용이다. 전체 선택에는 SPECIAL4/5의 기존 DataFrame 유지보수 입력이 남아 있다. COMPOSER B·SPECIAL 재작성·의존성 교정은 후속 범위다.\n'
        '- 기존 집합 순회가 원비 touch sequence와 파생 ID에 영향을 주는 것을 해시 시드 통제 재생으로 확인했다. 판정 순서나 ID 산식을 임의로 정렬/변경하지 않았다. 해시 시드가 다른 프로세스 사이의 ID 동일성까지 보장한다는 의미는 아니다.\n'
        '- 원래 있던 무결성 미등록 항목은 기존 증거를 보존하며 이번 변경으로 추가하지 않는다.',
        '## 7. 수정 파일·무결성'])
    if (OUT/'source_inventory.json').exists():
        inv=read(OUT/'source_inventory.json');sections.append('\n'.join('- `'+x['path']+'`' for x in inv['changes']))
        integrity=read(OUT/'integrity_registration.json')
        sections.append(f"Part1 체인 `{integrity['unit']}` 등록, 새 무결성 오류 {len(integrity['new_errors'])}건. `build/part1_immutable_sha256.json`을 재생성했다. 상세 전후 해시: `source_inventory.json`; 이전 수정본 보존: `before_source_preservation.json`.")
    else:sections.append('최종 소스 확정 후 체인 44-engine-optimization과 불변 목록을 등록한다.')
    if (OUT/'result_storage_verified.json').exists():
        verified=read(OUT/'result_storage_verified.json')
        sections.append(f"최종 저장 확인: DuckDB 실행 상태 COMPLETE, 알림 {verified['alerts']}건의 모든 필드가 결과 CSV와 일치했다. 13개 녹화 조각 등록과 처리기별 403,159묶음 타이밍도 확인했다. 증거: `검증결과/engine_optimization/result_storage_verified.json`.")
    sections.extend(['## 사용',
        'GUI·CLI 사용법은 수정본24와 같다. Part2에서 `python -m event_backtest run --symbol XAUUSD+ --start 2024-10-01 --end 2025-10-01 --mode BAR --strategies SPECIAL1 --cores 14 --yes`로 실행한다. 창고 위치는 컴퓨터의 설정 또는 `--warehouse`로 지정한다. 이미 검증된 같은 EA/schema 조각은 재사용한다.',
        '보고용 보조 스크립트와 모든 새 증거는 build 및 `검증결과/engine_optimization`에 있다. 별도 성능 합격 기준은 적용하지 않았다.'])
    (ROOT/'수정내역_엔진최적화.md').write_text('\n\n'.join(sections)+'\n',encoding='utf-8')
    print('report updated; complete=',complete)
if __name__=='__main__':main()
