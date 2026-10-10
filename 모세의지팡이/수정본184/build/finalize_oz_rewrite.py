"""Evidence consolidation; previous revisions are read-only throughout."""
from pathlib import Path
import sys,json,hashlib,ast,xml.etree.ElementTree as ET
from collections import Counter
R=Path(__file__).resolve().parents[1];OLD=R.parent/'수정본19';OUT=R/'검증결과/oz_rewrite'
def read(p):return json.loads(p.read_text('utf-8'))
def write(p,v):p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(v,ensure_ascii=False,indent=2),encoding='utf-8')
def sha(p):
    with p.open('rb') as h:return hashlib.file_digest(h,'sha256').hexdigest()

def register():
    sys.path.insert(0,str(R/'Part1/audit'))
    from source_integrity import verify_sources
    before=verify_sources()['final_sha256'];empty=hashlib.sha256(b'').hexdigest()
    unit=R/'Part1/audit/remediation/39-oz-rewrite'
    if unit.exists():raise FileExistsError(unit)
    names={p.relative_to(R/'Part1').as_posix() for p in (R/'Part1/program').rglob('*.py') if '__pycache__' not in p.parts}
    names.update(n for n in before if n.startswith('program/') and n.endswith('.py'))
    rows=[]
    for name in sorted(names):
        path=R/'Part1'/name;old=OLD/'Part1'/name
        actual=sha(path) if path.is_file() else None;previous=sha(old) if old.is_file() else None
        if actual!=previous:rows.append({'file':name,'before_sha256':before.get(name,empty),'after_sha256':actual,'revision19_sha256':previous})
    write(unit/'changes.json',rows)
    write(unit/'review.json',{'stage':'OZ rewrite','scope':'persistent OZ state and full NumPy Board views; same decision flow and output contract',
        'removed':'OZ-only preparation cache, history anchors and implicit-indicator BoardFrames branch',
        'protected':'other strategy/consumer decisions, Part2 native extraction, Part3, MT5 and config',
        'auto_review':'Initial broad shared-code rewrite rejected. Reference audit confirmed explicit indicator requests in remaining consumers; retained SelectionPort interface and non-OZ frame logic, removed only OZ-specific branch. Related tests pass.'})
    result=verify_sources();prior=read(OUT.parent/'event_e3/integrity_registration.json')['remaining_errors']
    added=sorted(set(result['integrity_errors'])-set(prior))
    write(OUT/'integrity_registration.json',{'chain_valid':not any('broken hash chain' in e for e in result['integrity_errors']),
        'remaining_errors':result['integrity_errors'],'added_diagnostics':added,'changed_files':rows})
    assert not added,added

def preserve():
    before=read(OUT/'revision19_before_manifest.json');changed=[];protected=[];count=0
    for item in before:
        name=item['path'];path=OLD/name
        if not path.is_file() or sha(path)!=item['sha256']:changed.append(name)
        if (name.startswith(('Part2/','Part3/','검증결과/')) or Path(name).suffix in ('.mq5','.mqh','.ex5')
            or Path(name).name in ('config.txt','command_aliases.json','special_settings.json','staff_performance_policy.json')):
            count+=1
            if not (R/name).is_file() or sha(R/name)!=item['sha256']:protected.append(name)
    names={r['path'] for r in before}
    added=[p.relative_to(OLD).as_posix() for p in OLD.rglob('*') if p.is_file() and p.relative_to(OLD).as_posix() not in names]
    result={'revision19_files':len(before),'revision19_mismatches':changed,'revision19_added':added,
        'protected_revision20_files':count,'protected_revision20_mismatches':protected}
    write(OUT/'source_preservation.json',result);assert not changed and not added and not protected,result

def compare():
    records=[];old_differences=[]
    # Only explicit host shutdown export changed after market-path validation.
    # Preserve actual source hashes; do not relabel prior evidence as new runs.
    export_before=OUT/'pre_export_fix_event_startup.py.txt';export_after=R/'Part1/program/event_startup.py'
    functions=lambda p:{n.name:ast.dump(n) for n in ast.parse(p.read_text('utf-8')).body if isinstance(n,(ast.FunctionDef,ast.ClassDef))}
    old_functions,new_functions=functions(export_before),functions(export_after)
    changed_functions=[n for n in old_functions.keys()|new_functions.keys() if old_functions.get(n)!=new_functions.get(n)]
    assert changed_functions==['export_engine_state'],changed_functions
    allowed_export_hashes={sha(export_before),sha(export_after)}
    current={p.relative_to(R/'Part1').as_posix():sha(p) for p in (R/'Part1/program').rglob('*.py') if '__pycache__' not in p.parts}
    for case in ('synthetic240','actualXAU'):
        suffix='_final' if case=='synthetic240' else ''
        a,b=[read(OUT/f'{case}_{mode}{suffix}.json') for mode in ('live','replay')]
        compatible=[]
        for run in (a,b):
            delta=[name for name in current.keys()|run['source_hashes'].keys() if current.get(name)!=run['source_hashes'].get(name)]
            assert set(delta)<={'program/event_startup.py'},delta
            if delta:assert run['source_hashes']['program/event_startup.py'] in allowed_export_hashes
            compatible.append(delta)
        result={'case':case,'bundles':a['bundles'],'signals':len(a['signals']),'notifications':len(a['telegram']),
            'signal_identity':a['signals']==b['signals'],'output_identity':a['output_deliveries']==b['output_deliveries'],
            'errors':a['errors']+b['errors'],'source_identity':a['source_hashes']==b['source_hashes'],
            'source_unchanged':a['source_unchanged_during_run'] and b['source_unchanged_during_run'],
            'non_market_path_changes_after_run':compatible,
            'source_compatibility':'Only export_engine_state ownership filter changed, outside this driver path; separately tested in export_tests.xml.'}
        if case=='synthetic240':result['checkpoint_equal']=b['checkpoint_equal'];assert result['checkpoint_equal']
        records.append(result)
        assert all(result[k] for k in ('signal_identity','output_identity','source_unchanged')) and not result['errors'],result
        # Old E3 notifications are a diagnostic list, never a gate.
        old=read(OUT.parent/'event_e3'/f'{case}_replay.json')
        old_counts=Counter(json.dumps(x,ensure_ascii=False,sort_keys=True) for x in old['telegram'])
        new_counts=Counter(json.dumps(x,ensure_ascii=False,sort_keys=True) for x in b['telegram'])
        removed=[json.loads(x) for x in (old_counts-new_counts).elements()]
        added=[json.loads(x) for x in (new_counts-old_counts).elements()]
        old_differences.append({'case':case,'removed':removed,'added':added,'old_count':len(old['telegram']),'new_count':len(b['telegram']),
            'gate':False,'judgment':'No changed final notifications in this observed interval.' if not removed and not added else 'Inspect listed changes against intended OZ logic; old output is not an oracle.'})
    write(OUT/'live_replay_comparison.json',records);write(OUT/'prior_alert_differences.json',old_differences)

def inventory():
    files={p.relative_to(R).as_posix():sha(p) for p in (R/'Part1').rglob('*') if p.is_file() and '__pycache__' not in p.parts and 'results' not in p.parts and p.name!='special_settings.json' and p.suffix!='.ex5'}
    write(R/'build/part1_immutable_sha256.json',dict(sorted(files.items())))

def report():
    outcomes={}
    for name in ('related_tests.xml','scope_tests_final.xml','readiness_tests.xml','export_tests.xml'):
        for case in ET.parse(OUT/name).getroot().iter('testcase'):
            outcomes[(case.get('classname'),case.get('name'))]=not any(c.tag in ('failure','error') for c in case)
    assert all(outcomes.values())
    tests={'unique_tests':len(outcomes),'passed':sum(outcomes.values()),'new_failures':0,
        'method':'Related collection once, then only added scope/readiness tests and affected startup paths. Earlier scope fixture failure retained.'}
    write(OUT/'related_test_summary.json',tests)
    compare_results=read(OUT/'live_replay_comparison.json');actual=read(OUT/'actualXAU_replay.json');measurement=actual['measurement']
    diagnostic=read(OUT/'old_oz_diagnostic.json');prior=read(OUT/'prior_alert_differences.json')
    assert not any(x['added'] or x['removed'] for x in prior),'Changes require per-alert judgment before report.'
    integrity=read(OUT/'integrity_registration.json');preserved=read(OUT/'source_preservation.json')
    changes='\n'.join('- `Part1/'+x['file']+'`'+(' — 삭제' if x['after_sha256'] is None else '') for x in integrity['changed_files'])
    cases='\n'.join(f"| {x['case']} | {x['bundles']} | {x['signals']} | {x['notifications']} | 동일 |" for x in compare_results)
    timing='\n'.join(f"| {name} | {v['calls']} | {v['ms_per_bundle']:.3f} |" for name,v in sorted(actual['processor_timings'].items(),key=lambda item:-item[1]['ms_per_bundle']))
    retired=read(OUT/'retired_tests.json')['retired_functions']
    retired_text='\n'.join('- `'+name+'`' for name in retired)
    report=f'''# OZ 재작성 — 수정본20

수정본19(E3 완료본) 전체를 독립 복사한 수정본20에서 OZ 재작성을 완료했다. 입력 준비와 상태 수명만 바꾸었고, 16개 프로필의 판정식·설정·출력 규칙은 유지했다. 이전 알림을 정답으로 삼거나 새 기준선을 만들지 않았다.

## 최종 구조와 변경

- `event_engine/oz_processor.py` → `oz_engine.runtime.OZRuntime` → TF별 `OZMarketView` → 상주 `OZProfile` 16개. Watch와 외부유동성 컨트롤러도 한 번 생성해 계속 사용한다. SYMBOL별 프로필 상태는 독립이다.
- 시장 뷰는 Snapshot의 읽기 전용 time/values 배열을 그대로 참조한다. 묶음당 준비된 TF마다 한 번 만들고 16개 프로필이 공유한다. DataFrame/Series를 만들지 않고 입력 창을 자르지 않는다. 전광판의 전체 이력(최대 650행)과 정수 epoch 초를 사용한다.
- percentile·HMA 교차·교차 전 극값·경과 봉·캔들·색전환·레짐·SUPER·터치를 이름 있는 OZ 전용 Fact로 계산한다. 같은 뷰에서 공유하며, 원래 봉 위치·엄격/포함 비교·동률 최근 봉 규칙을 유지한다.
- ATR14_GENERAL은 공용 `indicator_facts` 등록부의 NumPy 실행 경로다. 기존 TR[0] seed와 Wilder EWM(adjust=False, ignore_na=False, min_periods=14) 식을 그대로 사용한다. 전체 이력/NaN 표본 값이 기존 전체 계산과 정확히 같음을 확인했다. 다른 소비자의 기존 pandas 계산 경로는 유지한다.
- 평가 대상은 자기 피드, NORMAL 중위·상위, REGIME 상위, 연결된 외부유동성 source TF, 해당 심볼·프로필·TF의 Watch/외부 상태 변경이다. seq만 전진하고 값이 같은 HEARTBEAT는 평가하지 않는다. source_epoch 변경은 재평가한다. 모든 필요 TF가 뒤늦게 준비되면 전체 base TF를 한 번 초기 평가한다.
- 프로필 상태 인코딩은 명시적 엔진 checkpoint/호스트 상태 저장 때만 한다. 재생 중 매 묶음 export/restore는 없다. 기존 oz_observed JSON timestamp 형식을 정수 시각으로 읽으며, 실제 재시작 때 관측하지 못한 OUT→IN/교차를 추정하지 않는 규칙을 유지한다.
- `event_startup.export_engine_state`는 런타임의 명시적 export를 호출한다. 쓰기는 계속 이벤트 전용 경로다. 기존 Watch 상태 JSON이 손상되면 기존 빈 상태 복원 정책과 오류 로그를 유지한다.
- `preparation.OZDerivedCache`, `history.oz_anchors`와 해당 anchor/window 계산, BoardFrames의 OZ 전용 무지표 요청 분기를 제거했다. 다른 소비자의 명시적 지표 요청·격리 복사·행 수 계산과 SelectionPort 인터페이스는 유지했다.
- Watch.try_fire의 매칭·수신자 그룹·one-shot/persistent 처리, payload·문구·event_id 규칙은 그대로다. 정수 B0를 event_id에 넣을 때는 기존 Timestamp 문자열로 명시 포맷해 ID 규칙을 유지한다.

## 변경하지 않은 범위

WATCH_CONDITIONS, SWEEP, FVG, INDICATOR, COMPOSER 및 SPECIAL 판정 소스, MT5 EA/지표/Wire, Part2와 Part3, 사용자 config를 변경하지 않았다. `native_mt5.py`, `native_features.py`, `bar_seed.py`와 네이티브 추출 의존 파일은 수정본19 보존본 그대로다. MT5 실행·신규 캡처·BTC 측정·실시간 대기 비교·성능 게이트·Part2 일반 회귀는 하지 않았다.

## 로직 시험

관련 시험 **{tests['passed']}개 통과**, 새 실패 0. 전체 관련 묶음은 한 번 실행했고 이후에는 추가/영향 시험만 재실행했다. 네트워크 guard와 주입된 가짜 출력 transport로 실제 Telegram 송신을 차단했다.

- OUT→IN 발생/비발생, GC/DC와 교차 전 극값/최근 동률, TRUE B0 선택, 반대 교차 취소.
- 한 방향 확정 3봉, 진행봉 제외, B0 10봉·교차 7봉·등록 7봉의 = / +1 경계.
- B0/HMA17/원비/캔들/HMA6 색전환, 포함 터치·엄격 돌파, 누적 2개 및 B0 단독, B0 당일 최소 경과 봉.
- 16개 프로필의 완성, NORMAL 영구 탈락, REGIME·SUPER 일시 실패, BREAKER strict break.
- 환경 없음/신규/교체의 조용한 소비, 환경 유지 시 발송, 외부 ATR×1.5 1차·생존·2차의 경계/초과, 등록 전 봉 터치 금지.
- 같은 입력의 변경 TF 평가와 전체 TF 평가를 240관측으로 비교. 후보 생성과 조용한 소비가 실제 발생했고 상태/결과가 같았다. 중위/상위/외부 source/Watch 변경의 평가 범위도 별도로 확인했다.
- 전체 Snapshot 무복사/쓰기 차단, pandas 생성 없는 ATR, 상주 객체 identity 유지, checkpoint에서만 프로필 직렬화, 재시작 공백 미추정, 늦게 준비된 TF의 최초 평가.

개발 중 scope 시험 입력이 매 새 봉에 반대 교차를 일으켜 후보를 취소했으므로 소비가 발생하지 않았다. 이는 구현 실패가 아닌 발생 사례 구성 오류였고, 다음 봉까지 교차 방향을 유지하도록 입력을 고쳐 실제 소비를 검사했다. 최초 실패 XML도 보존했다.

## LIVE 수신 경로와 재생

| 입력 | 묶음 | 전체 SIGNAL | 최종 알림 | 신호·출력 비교 |
|---|---:|---:|---:|---|
{cases}

signal_id·source_time·방향·문구·수신자 및 답글/출력 영수증이 동일했다. 합성 240초 중간 체크포인트 이후 연속성도 통과했다. LIVE 검증은 온라인 대기가 아니라 같은 캡처 바이트를 PipeReceiver.receive → STAFF → 엔진으로 처리한 오프라인 수신 경로다. 재생은 STAFF 공개 API → 같은 엔진이며 가상 대기를 하지 않는다. 최종 결과는 `synthetic240_*_final.json`, `actualXAU_*.json`이다. TF 준비 경계 수정 전 합성 결과도 진단 이력으로 보존했다.

시장 경로 검증 후 명시적 호스트 종료 저장 함수(export_engine_state)의 OZ 파일 소유권 필터를 보완했다. 이 함수는 시나리오 드라이버에서 호출되지 않는다. AST로 변경 함수가 이 함수 하나뿐임을 확인했고 저장/복원 영향 시험 7개를 별도로 통과했다. 따라서 시장 시나리오를 또 반복하지 않았다. 전후 실제 소스 해시와 이 차이는 비교 JSON에 그대로 남겼으며, 과거 실행을 최종 소스로 실행한 것처럼 바꾸지 않았다.

## 진단 차이와 판단

기존 OZMonitor와 새 OZ에 같은 전체 이력을 넣은 완성·NORMAL 탈락·타이머 만료·조용한 소비 **{diagnostic['cases']}사례**, 의미 차이 **{len(diagnostic['differences'])}건**. 시각 표현을 epoch 초로 정규화해 비교했으며 이 비교는 합격 기준이 아니다. 별도의 발생/비발생 시험이 로직 정상 작동의 근거다.

수정본19 E3 기록 대비 최종 알림의 시각·수신자·문구 변경: 합성 240초 **0건**, 보존 XAU **0건**. 차이 목록은 `prior_alert_differences.json`에 보존했다. 이 관측 구간 밖의 모든 시장에서 차이가 없다고 일반화하지 않는다. 이전의 잘린 창과 달리 ATR/B0는 전체 가용 이력을 사용한다.

## 참고 측정 1회

모든 실제 전략을 넣은 보존 XAU 재생 **{measurement['bundles']}묶음**. 평균 **{measurement['mean_ms']:.3f}ms**, p99 **{measurement['p99_ms']:.3f}ms**. 파일 읽기·STAFF 디코드·외부 전송을 제외하고 엔진의 묶음과 내부 SIGNAL 처리까지 잰다. 처리기별 값은 각 on_event의 누적 시간을 전체 묶음 수로 나눈 것이다. 공용 Fact 준비와 엔진 제어 비용은 개별 행 밖이며 전체 시간에는 들어간다. 다른 시험 실행을 끝낸 뒤 한 번 측정했고, 성능 합격 기준으로 쓰지 않았다.

| 처리기·Consumer | 호출 수 | ms/묶음 |
|---|---:|---:|
{timing}

이전 E3 참고 평균 454.957ms/p99 503.269ms는 재측정하지 않은 역사 자료다. CPU 고정 5쌍 게이트나 같은 환경의 대조 실험으로 해석하지 않는다. 다음 병목은 위 표의 SWEEP·COMPOSER 등 남은 DataFrame 입력 경로이며 이번 범위에서는 고치지 않았다.

## 설계 해석·유지한 연결부

1. 시장 봉/B0/교차 상태는 정수 epoch 초다. 외부 명령 등록시각과 완료 payload의 초 미만 정밀도는 보존했다. 등록 시각을 정수로 내리면 등록 전 시작된 봉을 터치로 인정할 수 있어 판정 의미를 바꾸기 때문이다.
2. Composer는 기존 external_memory JSON 투영을 읽으므로, 외부유동성 상태가 실제 바뀔 때만 그 작은 투영을 직렬화한다. 16개 프로필의 매 묶음 직렬화는 제거했다. Composer 변경 금지와 기존 출력 계약을 함께 지키기 위한 연결부다.
3. 모듈 monitor_OZ의 옛 OZMonitor/순수 함수는 진단 및 같은 파일의 GenericConditionMonitor 의존을 위해 남겼다. 새 OZ 처리기는 그 클래스를 만들지 않는다. 기존 명령 해석과 출력 envelope 함수는 그대로 재사용한다.
4. 최초 공용 코드 정리안은 자동 승인 검토에서 다른 소비자 영향 우려로 거부되었다. 실제 호출부를 확인하고 OZ 전용 분기만 제거하는 좁은 변경으로 진행했다. 다른 소비자의 격리 복사/명시 지표 요청 시험을 유지했다. 미해결 승인 차단은 없다.

## 기존 시험 처리

삭제된 OZ DataFrame 창/anchor/cache 구조를 고정하던 아래 7개 함수는 활성 PERF1 시험에서 제외했다. 원문은 `retired_perf1_source.py.txt`, 목록/근거는 `retired_tests.json`에 보존했다. 대응 논리는 신규 NumPy/전체 이력 시험으로 검사하며, 다른 소비자 관련 검증을 줄이지 않았다. 기존 REGIME/SUPER 진단의 잘린 창 입력은 전체 프레임으로 바꾸었다.

{retired_text}

## 무결성과 파일

수정본19 **{preserved['revision19_files']}파일** 대조: 변경 0, 추가 0. 수정본20 안의 Part2/Part3·과거 검증 결과·MT5·사용자 설정 등 보호 대상 **{preserved['protected_revision20_files']}파일**도 불일치 0. `39-oz-rewrite` 해시 체인을 등록했고 새 무결성 진단 0, 기존 잔여 진단 {len(integrity['remaining_errors'])}건은 그대로다. `build/part1_immutable_sha256.json`을 재생성했다. 세부 증거는 `검증결과/oz_rewrite`에 있다.

{changes}

추가 변경: `AGENTS.md.txt`, `tests/test_oz_rewrite.py`, PERF1 시험의 위 진단 이동, build 검증·보고 도구. OZ 재작성을 마쳤으며 후속 SWEEP/전략 재작성은 시작하지 않는다.
'''
    (R/'수정내역_OZ_재작성.md').write_text(report,encoding='utf-8')
    write(OUT/'status.json',{'stage':'OZ_REWRITE','status':'COMPLETE','tests':tests,'live_replay':compare_results,
        'measurement':measurement,'old_oz_diagnostic_differences':len(diagnostic['differences']),
        'report':'수정내역_OZ_재작성.md','next':'STOP; await user review.'})

if __name__=='__main__':
    for action in sys.argv[1:]:globals()[action]();print(action,'PASS',flush=True)
