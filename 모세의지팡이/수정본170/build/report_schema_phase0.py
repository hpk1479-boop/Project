"""Close prerequisite diagnosis using preserved traces, without rerunning."""
from pathlib import Path
import csv,json
ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'검증결과/schema_cleanup/phase0'
def main():
    a,b=[json.loads((OUT/f'revision{r}/result.json').read_text('utf-8')) for r in (21,22)]
    rows=['# 스키마 변경 전 내부 신호 진단 완료','',
          '수정본21·22의 Part1 program 전체 복사본을 사용해 2025-09-01~07 BAR 6,726묶음을 각각 재생했다. 실제 네트워크는 차단했다. 원본과 복사본 소스 해시 변화는 없다.',
          '', '| 종류 | 수정본21 | 수정본22 | 차이 |','|---|---:|---:|---:|']
    for key in sorted(a['counts'].keys()|b['counts'].keys()):
        x,y=a['counts'].get(key,0),b['counts'].get(key,0)
        rows.append(f'| {key} | {x} | {y} | {y-x:+d} |')
    rows.extend(['',f"내부 신호 합계 {a['signals']:,}→{b['signals']:,}. 기간 내 최종 알림은 각 {a['notifications']}건이다. 시작 명령 확인 알림 11건은 기간 밖으로 별도 기록했다.",
      '', '## 최초 차이와 원인', '',
      '- 최초 수치 차이는 1번째 묶음 TREND sma20_slope의 부동소수점 반올림(약 1.36e-12)이다. 방향/조건 차이는 없다.',
      '- 최초 의미 차이는 307번째 묶음, 6m FVG의 세 번째 선택 영역이다. 구 입력 창은 49행, 새 입력은 전체 650행이며 Wilder ATR 시드의 영향이 다르다.',
      '- gap=0.98인 후보의 최소 기준은 전체 이력에서 0.9821483304, 49행에서는 0.9779291650이다. 전체 이력에서는 탈락한다.',
      '- 구 pandas 계산과 새 NumPy 계산에 같은 전체 이력을 주면 모든 결과가 같다. 같은 49행을 주어도 같다. 구현 버그가 아니라 승인된 전체 이력 사용의 결과다.',
      '', '## 최종 알림 ID 2건', '',
      '- 2025-09-05 23:49 UTC의 5m/6m SPECIAL1 알림 두 건은 시각·방향·문구·수신자가 같다.',
      '- 부모 OZ FINAL_ALERT의 watch_ids가 다르다. 공통 PIPELINE_1@3h 감시는 같고, PIPELINE_6@15m@LONG 감시 ID가 `OZARM:9cf2a46832166504b7b0`→`OZARM:3d0379096c3e36ad2365`로 달라졌다.',
      '- 앞 감시는 묶음 5590(1757048400000ms), 뒤 감시는 5714(1757055840000ms)에 등록됐다. FVG 터치 조건의 발생 시점이 signature에 포함되어 감시 ID→OZ event_id→최종 signal_id로 전파됐다.',
      '- 묶음 5590의 15m FVG gap=6.05: 전체 이력 ATR 상한은 6.0150899834라 제외, 49행 상한은 6.2685438076이라 포함된다. 이 사례도 같은 입력에서 구·신 계산이 정확히 같다.',
      '- 판정/ID 식은 변경하지 않는다. 전체 이력을 사용한 새 FVG 결과가 현재 설계에 맞다. 0번 진단에 따른 제품 코드 수정은 없다.',
      '', '## 근거', '',
      '- `검증결과/schema_cleanup/phase0/revision21`, `revision22`: 원시 신호·부모 이벤트·최종 알림·종류별 건수·소스 해시.',
      '- `family_diagnostic.json`: 전체 6,726묶음 비교. 의미 차이는 FVG 1,249묶음, 그에 연결된 WATCH_COMMAND 3묶음, OZ 4묶음이다. SWEEP/TREND/WATCH/최종 알림 내용 차이는 없다.',
      '- `fvg_history_diagnostic_final.json`, `watch_registration_fvg_diagnostic.json`: 두 후보의 전체 이력/49행 × 구/신 4방향 계산.',
      '- `identity_diagnostic.json`, `watch_identity_origins.json`, `event_5590.json`: 두 알림의 완전한 인과 추적.',
      '', '진단 완료. 이후부터 수정본23의 스키마 작업을 시작한다.'])
    (ROOT/'진단_판다스제거_신호차이.md').write_text('\n'.join(rows)+'\n',encoding='utf-8')
    print(a['signals'],b['signals'],a['notifications'],b['notifications'])
if __name__=='__main__':main()
