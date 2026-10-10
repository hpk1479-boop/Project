"""Persist observed causes, without treating solo output as a golden answer."""
from pathlib import Path
import json
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'검증결과/shared_oz_composer_input'
read=lambda name:json.loads((OUT/name).read_text('utf-8'))
traces={name:[json.loads(line) for line in (OUT/('case6m_'+name+'.jsonl')).read_text('utf-8').splitlines()]
        for name in ('ALL','SPECIAL2')}
at=1757685180000
states={name:[w for r in rows if r['kind']=='try_fire' and r['time_ms']==at
              for w in r['watches'] if w['watch']['source_spec_id']=='PIPELINE_2@6m' and w['watch']['direction']=='SHORT'][0]
        for name,rows in traces.items()}
assert states['ALL']['external_state']['status']=='INVALID'
assert states['SPECIAL2']['external_state']['status']=='CONFIRMED'
qualifications={name:[r for r in rows if r['kind']=='qualify' and r['time_ms']==1757653920000]
                for name,rows in traces.items()}
assert not qualifications['ALL'] and qualifications['SPECIAL2']
cases=[{
    'at_utc':'2025-09-12T13:53:00Z', 'strategy':'SPECIAL2','tf':'6m','direction':'SHORT',
    'classification':'pre_dispatch_external_qualification_state',
    'effect':'단독에만 1건',
    'first_qualification_utc':'2025-09-12T05:12:00Z',
    'qualification':qualifications,'state_at_alert':states,
    'findings':'단독은 TRUE B0 3647.59로 PDH 3649.12의 2차 ATR 확인을 수행해 CONFIRMED가 됐다. ALL은 같은 시각 그 확인 호출이 없고, 06:58 UTC ATR_1P5_SURVIVAL로 INVALID가 됐다. 13:53 같은 시장 OZ 사건에서 ALL에는 SPECIAL5만 적격 감시로 포함됐다.',
    'judgment':'현재 _matching은 INVALID 감시를 제외하므로 해당 시점의 출력 차단은 그 상태에 맞다. 다만 공유 OZ 후보의 평가 이력이 감시 구성에 의존하는 상위 상태 결합은 남아 있다. 어느 결과를 정답으로 맞추지 않았고, 판정식·후보 상태 머신을 이 작업에서 추가 변경하지 않았다.'
},{
    'at_utc':'2025-09-24T07:43:00Z','strategy':'SPECIAL2','tf':'6m','direction':'SHORT',
    'classification':'prior_completion_changed_child_lifetime',
    'effect':'양쪽 1건씩, 유동성 위치 문구·signal_id 차이',
    'solo_watch_id':'OZARM:9ab8919d81ff9f5834d6','ALL_watch_id':'OZARM:369c9553977691e57288',
    'solo_location':'4시간 고가 3646.50','ALL_location':'전일 고가 3454.02',
    'findings':'앞 사건에서 단독은 기존 child를 완료한 뒤 새 조건 child를 사용한다. ALL은 완료하지 못한 기존 child가 남아 같은 9월24일 시장 사건에 응답한다. 시장 사건 ID·시각·방향·현재 가격·TRUE B0는 같지만 등록 당시 조건 문맥과 이를 쓰는 SPECIAL2 ID가 다르다.',
    'judgment':'대표 출력 정책이나 첫 SPECIAL 반환 때문에 발생한 차이가 아니다. 조건별 child 수명과 등록 문맥의 차이이며 위 상위 상태 결합의 후속 영향으로 기록한다.'
}]
comparison=read('month_signal_comparison.json')
for row in comparison['differences']:
    index=0 if row['row']['time_ms']=='1757685180000' else 1
    row['reason']=cases[index]['classification']
    row['diagnosis_case']=index+1
(OUT/'month_signal_comparison.json').write_text(json.dumps(comparison,ensure_ascii=False,indent=2),encoding='utf-8')
(OUT/'shared_difference_diagnosis.json').write_text(json.dumps({'cases':cases,'remaining_issue':'shared OZ candidate evaluation depends on active watch environment'},ensure_ascii=False,indent=2),encoding='utf-8')
print('two incidents documented; three CSV difference rows')
