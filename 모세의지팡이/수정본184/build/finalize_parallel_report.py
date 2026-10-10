"""Publish measured evidence, retaining explicit unmet comparison conditions."""
from pathlib import Path
import argparse,collections,json
ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'검증결과/parallel_oz'
REPORT=ROOT/'수정내역_병렬_OZ최적화.md'

def read(name):return json.loads((OUT/name).read_text('utf-8'))
def table(headers,rows):
    return '\n'.join(['| '+' | '.join(headers)+' |','| '+' | '.join('---' for _ in headers)+' |',
        *('| '+' | '.join(str(v) for v in row)+' |' for row in rows)])+'\n'
def main():
    p=argparse.ArgumentParser();p.add_argument('--warehouse',required=True);a=p.parse_args();runs=Path(a.warehouse)/'runs'
    comp=read('comparisons.json');weekly=read('weekly_measurements.json');year=read('year_analysis.json');chosen=read('measured_default.json')
    converted=read('keyframe_conversion.json');ids=read('signal_id_changes.json');source=read('source_inventory.json')
    if len(comp['oz_month_pairs'])!=7 or len(comp.get('positive_june_vs_all',{}))!=7 or sum(r['revision']==26 for r in weekly)!=7 or len(year['measurements'])!=2:
        raise ValueError('required evidence incomplete')
    intro=REPORT.read_text('utf-8').split('## 검증·측정 진행 상황')[0]
    intro=intro.replace('작성 중. 월별 비교·13개 조각 변환·연간 실측이 끝나기 전에는 완료 보고가 아니다.',
        '구현과 측정 결과를 아래에 기록한다. A1의 실제 발생 사례/단독 대 전체 선택 조건은 별도로 판정하며, 0건 비교나 기존 공유 선정 차이를 무조건 통과로 처리하지 않는다.')
    intro=intro.replace('의존 누락과 공유 선정 차이를 분리해 기록 중이다.','의존 누락과 공유 선정 차이를 분리해 아래에 기록했다.')
    intro=intro.replace('A 수정 직후 3종 비교는 일치했으며 최종 코드 재확인이 진행 중이다. 바뀐 ID 전문은 완료된 비교 자료에서 별도로 정리한다.',
        '최종 코드도 세 해시 시드에서 172,167개 SIGNAL 전체와 알림 14건이 ID까지 일치했다. 전후 ID 변경 전문은 `검증결과/parallel_oz/changed_signal_ids.csv`와 `signal_id_payload_differences.json`에 기록했다.')
    intro=intro.replace('월 작업 6/12/14 workers, 가장 빠른 worker 수의 2주 작업, 같은 후보의 beginning 비교를 순차로 실측한 뒤 기본값을 결정한다. 아직 측정 전이므로 기본 worker 수를 변경하지 않았다.',
        f"사용자 지시로 월 작업 6/12 workers의 완료 결과만 사용한다. 14 workers는 진행 중 중단, 2주 작업과 기존 시작 방식 비교는 실행하지 않고 모두 ‘생략’으로 기록한다. 기본값은 {chosen['best_work_size']} / 최대 {chosen['best_workers']} workers이며, 더 작은 컴퓨터에서는 물리 코어 수를 넘지 않는다. 사용자가 명시한 cores/work_size가 우선한다.")
    text=[intro,'## 검증 결과\n']
    text.append(table(['항목','결과'],[
        ['관련 시험','기존 관련 묶음 최초 206 PASS + 파이프 fixture 종료 경합 1 FAIL. fixture 동기화 후 해당 시험 PASS. 신규 24 PASS + 추가 epoch 경계 시험 PASS(관련 ATR 시험도 재확인). 상세 XML 보존.'],
        ['LIVE 수신 = 재생','synthetic240: 240묶음 / 5,794신호 / 19알림. TIMER239: 230묶음 / 5,668신호 / 13알림. 신호·출력·오류 모두 일치.'],
        ['해시 시드','0 / 1 / random 모두 SIGNAL SHA256 3223c6fbf8b8847c14ab5d8c2d24a34fe462c49cad62e68c216ecf4155b04f15'],
        ['키프레임','13조각 / 382,501묶음 / 279일. 원본 전체 해시 및 각 날짜 독립 복원 해시 일치.'],
        ['EWM','진행봉·봉 추가·이력 이동·정정·재연결·NaN/-0.0에서 전체 계산과 비트 일치.'],
        ['금지 범위','EA/schema/config 동일. SPECIAL AST는 집합 정렬 외 동일. monitor_OZ 혼합 줄바꿈 유지. 테스트 네트워크 차단.']]))
    text.append('### A1: 실제 월별 단독 대 전체 선택\n')
    text.append('실제 SPECIAL4가 발생한 2025-06을 추가로 전체 선택 및 SPECIAL1~7 단독 실행으로 비교했다.\n')
    text.append(table(['전략','전체 선택 속 해당 전략','단독','ID 포함 일치'],[
        [name,r['before'],r['after'],r['identical']] for name,r in comp['positive_june_vs_all'].items()]))
    text.append('2025-09의 보조 비교:\n'+table(['전략','전체 선택','단독','ID 포함 일치'],[
        [name,r['before'],r['after'],r['identical']] for name,r in comp['standalone_vs_all'].items()]))
    searches=read('special4_actual_search.json')
    text.append('SPECIAL4 단독 실제 발생 검색: '+', '.join(f"{r['start'][:7]}={r['notifications']}건" for r in searches)+'. 2025-09는 월별 비교에 포함했다.\n')
    text.append('SPECIAL4 첫 주 관측 진단에서는 setup 26건이 실제 생성되고 모두 종료되었다. 6분 만료와 ATR 선행이동 취소 등이 관측됐으며 poll 예외는 없었다. 진단은 `runs/parallel26_special4_flow_week_recorded/special4_flow_diagnostic.json`(창고 기준)에 있다. 첫 주 0건만으로 완료 처리하지 않고 위 6월 실제 발생 월까지 확인했다.\n')
    text.append('단독 대 전체 선택의 남은 차이는 기존 공유 감시 병합/출력 선정과 분리해 보아야 한다. `event_composer_domain._arm_oz_locked`는 동일 profile을 공유 child로 병합하고, 최종 출력과 child 소모는 공유 상태를 사용한다. 그 결과 단독 SPECIAL1의 같은 OZ를 전체 선택에서는 SPECIAL6이 대표 출력하는 사례가 있다. 이 규칙은 수정본25에도 존재하며 이번에 바꾸지 않았다. 모든 전략의 독립 알림을 전체 선택에서 각각 출력하도록 강제하면 기존 결정·출력 정책 변경이 된다. 차이 전문은 `comparisons.json`의 `standalone_vs_all`에 보존했다.\n')
    text.append('### A2: ID 변경 목록\n')
    text.append(f"종류별 SIGNAL 수는 전후 동일하다. 고유 ID {ids['removed_unique_ids']}개가 바뀌었고, 같은 ID의 payload에서 파생 watch_id가 바뀐 항목은 {ids['common_id_payload_changes']}개다. 최종 알림은 14건 모두 시각·방향·문구·수신자가 같고 ID 1건만 바뀌었다. 기존 내부 OZ 신호의 동일 ID 반복 120회는 전후 동일하며, 진단에서는 발생 순번까지 사용해 누락 없이 비교했다.\n")
    details=read('signal_id_payload_differences.json')
    text.append(table(['변경 전 ID','변경 후 ID'],[(r['before_id'],r['after_id']) for r in details if r['before_id']!=r['after_id']]))
    text.append('### OZ 전체 평가 대 선택 평가 (2025-09, 각 30,146묶음)\n')
    text.append(table(['전략','전체 평가 알림','선택 평가 알림','ID·순서 포함 일치'],[[k,v['before'],v['after'],v['ordered_identical']] for k,v in comp['oz_month_pairs'].items()]))
    text.append('발생이 0건인 SPECIAL3/4/7 구간은 양성 알림의 증거로 해석하지 않는다. 선언/동적 등록/선언 밖 오류는 별도 로직 시험으로 확인했다. 실제 적용 TF·프로필 전체는 `dependency_declarations.json`에 있다.\n')
    text.append('### 첫 주 전후 알림 차이 분류\n')
    text.append(table(['전략','25 알림','26 알림','ID 포함 일치','ID 외 동일'],[[k,v['before'],v['after'],v['identical'],v['same_except_id']] for k,v in comp['week_before_after'].items()]))
    text.append('첫 주 비교는 같은 월 시작에서 시작하므로 B/C 경계 변경이 없다. D는 위 월간 전체/선택 비교로 분리 확인했고, E는 판정 입력 비트 일치 시험으로 확인했다. 순수 ID 차이는 A2, SPECIAL4/5 cycle·maintenance 복구에 따른 차이는 A1로 분류한다. 상세 추가·삭제 행은 `comparisons.json`에 있다. 작업 크기 차이는 아래 C 비교에 따로 남긴다.\n')
    text.append('## 키프레임 변환 실측\n')
    text.append(table(['조각','묶음','키프레임','기존 MB','MSD2 MB','복원'],[[r['start'][:7],r['bundles'],r['keyframes'],f"{r['old_bytes']/1e6:.2f}",f"{r['new_bytes']/1e6:.2f}",'일치'] for r in converted]))
    text.append(f"합계: {sum(r['old_bytes'] for r in converted)/1e9:.3f}GB → {sum(r['new_bytes'] for r in converted)/1e9:.3f}GB. 이전 녹화·차분 파일은 보존했다. 변환 상세와 각 원본 SHA256은 `keyframe_conversion.json`.\n")
    text.append('## 1주 단독 측정 (2025-09-01~07)\n')
    text.append('후보 측정은 다른 재생 작업 종료 후 한 번씩 순차 실행했다. 전 수치는 수정본25의 같은 입력/방법 실측을 사용했다. 시간은 ms/묶음, 메모리는 MiB. 입력 포함은 worker의 스트리밍 구간이며, 초기 생성/결과 병합을 포함하는 연간 총 경과와 구분한다.\n')
    text.append(table(['판','전략','입력 포함','엔진','입력/호스트','최대 RSS'],[[r['revision'],r['strategy'],f"{r['input_included_ms']:.3f}",f"{r['engine_ms']:.3f}",f"{r['input_and_host_ms']:.3f}",f"{r['max_rss_mib']:.1f}"] for r in weekly]))
    names=sorted({k for r in weekly for k in r['processor_ms']})
    text.append(table(['판','전략',*names,'엔진 기타'],[[r['revision'],r['strategy'],*(f"{r['processor_ms'].get(k,0):.3f}" for k in names),f"{r['other_engine_ms']:.3f}"] for r in weekly]))
    text.append('SPECIAL4/5의 기존 낮은 비용에는 빠진 CHAINS 작업이 있었으므로 속도만 비교해 정확성 복구 비용을 회귀로 판단하지 않는다. 전체 값은 `weekly_measurements.csv`.\n')
    text.append('## 1년 SPECIAL1 병렬 실측\n')
    text.append('XAUUSD+ 2024-10-01~2025-10-01, 겹침 3거래일. i5-14500: 물리 14 / 논리 20. 각 조합은 순차 측정했다. 총 경과에는 runner의 검증·호스트 초기화·pool·결과 병합을 포함한다. CLI의 별도 plan/녹화 확인 단계는 제외한다. 생성 틱은 경고로 기록하고 실행했다.\n')
    text.append('수정본25 기록: 14 workers 설정/12 월 작업, runner 2,528.21초, CLI 전체 2,532.39초, 첫 진행률 590.85초. 근거는 `검증결과/engine_optimization/year_special1.json`·`year_special1.jsonl`·`year_special1_wall.json`. 당시 첫 진행률은 pool 시작 기준이며 새 parent 시간은 검증/초기화까지 포함한다. 시작 방식의 직접 비교는 이번 keyframe/beginning 동일 코드·작업 크기 결과를 사용한다.\n')
    text.append(table(['작업','설정 workers','실제 workers','시작','총 초','첫 진행률 초','묶음','워밍업 묶음','워밍업 비율'],[[r['work_size'],r['workers'],r['actual_workers'],r['capture_start'],f"{r['elapsed_seconds']:.2f}",f"{r['first_progress_seconds']:.2f}",r['bundles'],r['warmup_bundles'],f"{r['warmup_share']*100:.2f}%"] for r in year['measurements']]))
    text.append(table(['비교','알림 수','ID 포함 일치'],[[k,f"{r['before']} / {r['after']}",r['identical']] for k,r in year['comparisons'].items()]))
    text.append('**생략(사용자 지시): 14 workers, 2주 작업, 기존 시작 방식 비교.** 14 workers의 부분 진행 기록은 보존하지만 완료 측정값으로 사용하지 않는다. 따라서 MONTH/FORTNIGHT 알림 차이와 연간 keyframe/beginning 알림 동일성은 미검증이다. 키프레임 복원 해시와 관련 연속성 로직 시험 결과는 위에 별도로 기록했다.\n')
    text.append(f"사용자가 확정한 기본값: **{chosen['best_work_size']} / {chosen['best_workers']} workers**. 완료된 6/12 비교에서 12가 더 빨랐다. 전체 후보 중 최적이라고 주장하지 않는다. 결정 근거는 `measured_default.json`. 성능 허용치/게이트는 없다.\n")
    text.append('각 worker가 맡은 기간과 묶음 수는 `year_*.json`의 `worker_distribution`, 논리 코어별·worker별·작업별 묶음 수는 `year_*_cpu_distribution.csv`에 있다. 묶음 종료 시 CPU 샘플이므로 OS의 코어 이동을 포함한다. 코어 고정 측정은 아니다.\n')
    text.append('## 사용법\n')
    text.append('기존 GUI/CLI 선택 실행은 새 기본값을 사용한다. CLI `run`의 `--work-size MONTH|FORTNIGHT`, `--cores N`, `--capture-start keyframe|beginning`, `--oz-evaluation selected|all`로 비교/재설정한다. 설정 파일 → 시나리오 → 명시 CLI 값 순으로 덮어쓴다. 사용자 config 파일은 바꾸지 않았다.\n')
    text.append('## 시험 수정 및 무결성\n')
    text.append('test_data_selection의 저장 확인은 MSD2 공통 reader/verifier로 옮기고 실패 rollback/이동 검사를 유지했다. test_numpy_processors의 진행봉 fixture가 전체 이력을 덮어쓰던 부분을 마지막 행 변경으로 고치고 과거 정정 시험을 별도로 추가했다. test_oz_rewrite의 checkpoint mock은 공개 export_memory API를 따른다. test_event_e1의 Windows 파이프 writer는 reader가 끝날 때까지 연결을 유지하도록 fixture만 동기화했다. 최종 신규 시험의 Windows 기본 임시 폴더 접근 오류는 수정본 내부 basetemp로 해결했다. 원 실패 기록도 남긴다.\n')
    integrity=read('integrity_registration.json');preserve=read('previous_preservation.json')
    text.append(f"무결성 체인: `{integrity['unit']}`. 새 오류 {len(integrity['new_errors'])}건; 이전 오류 {len(integrity['remaining_errors'])}건은 유지. `build/part1_immutable_sha256.json` 재생성. 수정본25 보존 확인: {preserve['checked']}파일 / 변경 {len(preserve['changed'])}건.\n")
    text.append('## 수정 파일 목록\n')
    text.append('\n'.join('- `'+r['path']+'`' for r in source['changes'])+'\n- `수정내역_병렬_OZ최적화.md`\n- `검증결과/parallel_oz/` (시험·변환·비교·측정 증거)\n')
    REPORT.write_text('\n'.join(text),encoding='utf-8')
    print(REPORT.name)

if __name__=='__main__':main()
