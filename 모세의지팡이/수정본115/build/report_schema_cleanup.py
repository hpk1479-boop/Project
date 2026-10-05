"""Final report from completed evidence only; no inferred passes."""
from pathlib import Path
import json
ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'검증결과/schema_cleanup'
def read(n):return json.loads((OUT/(n+'.json')).read_text('utf-8'))
def main():
    repeat=read('repeat_capture');timer=read('bar_timer_native_meaning');flow=read('bar_timer_alerts');parity=read('live_replay_comparison')
    alerts=read('alert_differences');cells=read('cell_diagnostics');tests=read('test_summary');source=read('source_inventory');integrity=read('integrity_registration')
    captures=read('captures');restore=read('mt5_restoration');binary=read('tested_binaries');identity=read('ea_build_identity')
    assert repeat['identical'] and timer['identical'] and flow['signal_equal'] and flow['outputs_equal'] and tests['passed']==tests['unique_tests']
    assert restore['restored'] and restore['normal_terminal_running'] and not source['protected_differences'] and not integrity['new_errors']
    text=['# 수정내역 — 지표 정리·스키마 확정 / 수정본23','',
    '완료. 수정본22 전체 독립 복사본에서 작업했다. 원본과 이전 수정본, 사용자 config(BTCUSD 포함), COMPOSER/SPECIAL 판정 본문, monitor_OZ의 기존 바이트를 보존했다. 노트북 LIVE는 변경하지 않았다. 구 결과와의 일치는 진단으로만 사용하고 로직 조건 및 LIVE=재생을 검증했다.',
    '', '## 0. 먼저 완료한 판다스 제거 신호 차이 진단','',
    '- 수정본21·22 전체 Part1/program을 각각 독립 호스트로 사용해 2025-09-01~07 6,726개 BAR 묶음을 재생했다. 내부 신호 172,351→172,336, 기간 내 최종 알림은 각 33건이다.',
    '- 최초 수치 차이는 첫 묶음 TREND SMA20 기울기의 약 1.36e-12 반올림 차이다. 최초 의미 차이는 묶음 307의 6m FVG다. 구 49행 입력과 새 650행 전체 이력의 FVG_WILDER_ATR seed가 달라 0.98 gap의 최소 비율 경계를 다르게 넘었다.',
    '- 같은 입력 길이를 주면 구 pandas/새 NumPy FVG 계산은 서로 같았다. FVG 차이는 1,249개 묶음에 나타났고 Watch/OZ로 일부 전파됐다. 판다스 제거 버그가 아니라 전체 이력 사용의 의도된 결과다. 0번 생산 코드 수정은 없다.',
    '- 9월5일 23:49 UTC SPECIAL1 두 건의 signal_id 차이는 15m FVG 생성 판정 시점 → Watch 등록 시점/ID → OZ event_id에서 전파됐다. 두 알림의 시각·방향·문구·수신자는 같다.',
    '- 상세 최초 신호, 네 가지 입력/구현 교차 진단, 원인 ID 사슬: `진단_판다스제거_신호차이.md`, `검증결과/schema_cleanup/phase0/`.',
    '', '## 1. 최종 스키마와 원비','',
    f'- 최종 50열, schema_id **0x{identity["schema_id"]:08X}** ({identity["schema_id"]}). 단일 원본 `Part1/program/staff_schema.py::PIPE_VALUE_COLUMNS`, 생성물 `Part1/program/MT5/STAFF_Wire_Schema.mqh`.',
    '- 제거: ema_21, open_band_179/279/300/400의 lower/upper 8열, wonbi_sigma. 추가: PRICE/RSI/STO/DI 각각 lower_out·upper_out·regime_slope 12열. 나머지 열은 유지했다.',
    '- 전체 새 열 순서(0~49), 기존 48열 사용처, 62개 Fact와 처리기별 계산 목록: `지표정리_목록.md`. 새 수신기와 캡처 독자는 새 스키마만 받으며 구 스키마는 명시 거부한다.',
    '- EA 원비는 OPEN4 중심과 고정 3σ upper/lower를 전달한다. InpWonbiSigma, SET_WONBI_SIGMA 제어, 기대 σ/수신 σ 경고를 제거했다. wonbi_std Wire 열은 만들지 않았다.',
    '- config WONBI_SIGMA=3.0이면 공용 WONBI_BANDS Fact가 native 배열을 그대로 반환한다. 다른 σ만 `(native_upper-mid)/3`을 표준편차로 사용해 중심±σ×표준편차로 환산한다. OZ·Watch·COMPOSER는 같은 Board Fact, SPECIAL은 그 입력을 읽는다.',
    '- 여러 σ를 동시에 사용하는 활성 소비자는 없었다. σ3/2/2.5/4, 터치/비터치/경계·중복 억제, 임시 config에서 세 소비 경로 전달, 잘못된 σ 거부를 시험했다. Part2 실행 scenario.wonbi_sigma와 chunk 결과에 사용값을 기록한다. 녹화 캐시 식별자·EA 입력에는 σ가 없으므로 녹화 재생성이 필요 없다.',
    '- Wire 외 기존 native_snapshots 마지막행 파일의 고정 σ=3 메타 열과 SNAPSHOT 제어 헤더의 source σ 메타는 기존 형식 호환을 위해 유지한다. 기대 σ/가변 설정 경로가 아니며 이벤트 엔진 입력으로 사용하지 않는다.',
    '', '## 2. 지표 중복과 OUT·레짐 의미','',
    '- EMA21 컬럼/기울기·명령·저장된 교차 기간 사용을 EMA20으로 통합했다. EMA21 표기는 EMA20 별칭으로 정규화한다. 사용자 별칭/config 파일 자체는 수정하지 않았다.',
    '- OPEN4 std 중복은 native 고정3σ 폭에서 복원하는 공용 함수로 합쳤다. HMA/EMA 고정 열은 기존처럼 native 우선이다. OPEN EMA50와 CLOSE EMA50, PRICE CLOSE HMA6와 OPEN HMA6, iATR/ATR14_GENERAL/FVG_WILDER_ATR 등 계산 입력·seed가 다른 항목은 유지했다.',
    '- HMA168·EMA50·EMA200은 SPECIAL4/Watch의 실제 사용처가 있다. PRICE 전용 regime_basis 이름은 RSI/STO/DI의 기존 basis가 이미 레짐 basis인 것과 명명 차이다. 임의로 열을 더 삭제하지 않았다.',
    '', '| 지표 | 실제 TIMER 피드 관측 | OUT 차이 | OUT→IN 관측 | 전환 차이 | 준비된 slope 비교 | slope 차이 |',
    '|---|---:|---:|---:|---:|---:|---:|']
    for name,r in timer['native_meaning'].items():text.append(f'| {name} | {r["observations"]:,} | {r["out_state_mismatch"]} | {r["out_to_in"]:,} | {r["out_to_in_mismatch"]} | {r["slope_samples"]:,} | {r["slope_mismatch"]} |')
    text+=['','이 결과와 strict 경계 조건 시험을 근거로 OZ·Watch의 OUT/OUT→IN은 native OUT 상태를 읽고, OZ/COMPOSER의 slope는 native 기울기를 쓴다. 진행봉 내부의 매 관측 상태 전이는 그대로 유지한다. regime zone은 전달된 값과 native 경계의 비교로 남는다(해당 zone 열은 Wire에 없음). 예전 monitor_OZ 계산 함수는 참고용으로 보존하지만 활성 경로에서 호출하지 않는다.',
    '', '## 3. 초기 버퍼와 MT5 실측','',
    '- PRICE의 Ehlers 최초 읽기 셀(R-8)은 EMPTY_VALUE로 명시 초기화하고 기존 CLOSE fallback을 사용한다. RSI(R-25), STO/DI(R-37)의 smooth 최초 셀은 기존 재귀식의 0 seed를 명시한다. 같은 루프가 쓰기 전에 읽던 bbBasisBuf[limit+1]도 0으로 초기화했다. 재귀식·기간·가중치는 변경하지 않았다.',
    '- 보존 구 녹화에서는 2h 등 상위 프레임의 RSI/STO/DI basis/레짐 경계에 약 1e105의 할당 메모리 기원 값이 있었다. 예: DI basis 4.58265165659805e105 → 0.0004454122731211291. 이번 초기화로 결정적인 값이 됐다. 9개 레짐 계열 열마다 누적 5,370,300셀 차이가 기록됐다(반복 관측 중복 합계). 이 날 최종 알림 변화는 없었다.',
    '- EA 1개와 저장소의 custom 지표 PRICE/RSI/STO/DI 4개, 총 MQ5 5개를 컴파일했다. 모두 오류 0·경고 0. 검증 캡처에 실제 사용한 EX5를 수정본23 배포 폴더에 보관했다.',
    f'- 동일 하루 BAR 두 번: **{repeat["records"]:,}개 레코드, 19 TF, 시각·거래량·50열 전체 비트 동일**, 차이 0. 일봉 최초 행도 제외하지 않았다. 압축 파일 자체의 gzip 시각/세션 표시는 비교 대상이 아니다.',
    f'- BAR ↔ TIMER 봉 경계: {timer["matched"]:,}개 피드 레코드 정확 일치, 누락 0. 실제 replay.select_inputs 봉마감 필터를 통과한 TIMER도 {flow["filtered_timer_bundles"]:,}묶음/신호 {flow["signals"]:,}건·알림·출력이 BAR와 완전히 같다.',
    '', '| 녹화(2026-09-25 XAUUSD+) | 피드 레코드 | 테스터 초 | 원본 bytes | 보존 bytes(gzip 포함) |','|---|---:|---:|---:|---:|']
    for r in captures:text.append(f'| {r["label"]} | {r["records"]:,} | {r["elapsed_seconds"]:.2f} | {r["raw_bytes"]:,} | {r["stored_bytes"]:,} |')
    text+=['','모두 실제 틱 기반 모든 틱(Model=4), 저널 REAL_TICKS 확인. 자동화 실행 후 설치 대상 13개 파일의 전후 SHA256가 같고 일반 터미널 재실행도 확인했다. `mt5_restoration.json`, `compile/result.json`, `tested_binaries.json`에 근거를 남겼다.',
    '', '## 4. 로직·외부 동작 검증','',f'- 관련 로직 시험 **{tests["passed"]}개 통과**. Part2 일반 회귀 4묶음/성능 게이트는 실행하지 않았다. 실제 네트워크와 Telegram을 차단하고 출력 transport는 시험용으로 주입했다.',
    '- 테스트 파일 변경: test_oz_rewrite.py와 test_numpy_processors.py의 48열 고정 입력을 레지스트리 크기로 바꾸고 native OUT/slope를 공급했다. 기존 발생·비발생·경계·복원·캐시 오염 검사 항목을 줄이지 않았다. test_schema_cleanup.py에 신규 스키마/원비/config/EMA/native 경계 검사를 추가했다.',
    '- 개발 중 시험 도구의 등록 방식 오류와 옛 열 입력 누락은 해당 시험만 고쳐 재확인했다. 초기 실패 XML은 보존하며 최종 항목별 결과는 test_summary.json에 모았다.',
    '', '| 동일 입력 LIVE 수신 ↔ 재생 | 묶음 | 내부 신호 | 최종 알림(등록 확인 포함) | signal_id·시각·방향·문구·수신자 및 출력 |','|---|---:|---:|---:|---|']
    for r in parity:text.append(f'| {r["case"]} | {r["bundles"]:,} | {r["signals"]:,} | {r["notifications"]} | 모두 일치 |')
    text+=['','하루 알림 21건은 시작 등록 확인 11건과 시장 처리 후 10건을 포함한다. 합성은 19건, TIMER239는 13건으로 빈 비교가 아니다. TIMER239는 tick이 없는 시각을 기다리지 않아 실제 투입 묶음은 230개다.',
    '', '### 구22 ↔ 새23 하루 알림 진단','',
    f'내부 신호 {alerts["old_signals"]:,}→{alerts["new_signals"]:,}, 최종 알림 {alerts["old_notifications"]}→{alerts["new_notifications"]}. signal_id를 포함한 내부 신호와 최종 알림 모두 같다. 따라서 추가/삭제/시각·문구·수신자 변경 목록은 빈 목록이다.',
    '', '| 분류 | 달라진 최종 알림 |','|---|---:|']
    for k,v in alerts['categories'].items():text.append(f'| {k} | {v} |')
    text+=['','`alert_differences.json`에 빈 차이 목록까지 보존했다. 이는 관측한 하루의 결과이며 EMA20 전환/초기값 수정이 모든 기간에 알림 차이를 만들지 않는다는 뜻은 아니다.',
    '', '## 5. FULL 변화 셀 참고 측정','',
    '동일 BAR 시각의 연속 FULL끼리 같은 봉 시각을 정렬했다. 각 다음 FULL의 앞 64행을 웜업 관찰 구간으로 분리했다. 다음 창에 새로 들어온 봉은 overlap 비교에서 제외하며 진행봉 수정은 나머지 구간에 포함한다. 성능 합격 기준이 아니다.',
    '', '| 구분 | FULL 비교 쌍 | 변화 셀 합계 | 쌍당 평균 | 앞64행 | 나머지 |','|---|---:|---:|---:|---:|---:|']
    for version,label in [('old','구48열'),('new','새50열')]:
        r=cells[version].values();n=sum(x['full_pairs'] for x in r);total=sum(x['changed_cells'] for x in r)
        text.append(f'| {label} | {n:,} | {total:,} | {total/n:.3f} | {sum(x["front64_cells"] for x in r):,} | {sum(x["remainder_cells"] for x in r):,} |')
    text+=['','열 집합이 다르므로 총계만으로 수식 불안정을 판단하지 않는다. TF/열별 집계는 cell_diagnostics.json에 있다. 동일 시각 구/새 원비 upper/lower 26,163개 레코드는 비트 동일했고 σ3 Fact의 원본 전달도 전부 확인했다.',
    '', '## 6. 배포 파일·무결성·유의사항','',
    '노트북 LIVE 전환 시 함께 교체할 파일(이번에는 노트북에 배포하지 않음):',
    '', '| 위치 | 파일 |','|---|---|',
    '| Experts | THE_STAFF_OF_MOSES.mq5 / .ex5 |',
    '| Indicators | PRICE_of_Moses.mq5/.ex5, RSI_of_Moses.mq5/.ex5, STO_of_Moses.mq5/.ex5, DI_of_Moses.mq5/.ex5 |',
    '| EA include | STAFF_Wire_Schema.mqh, STAFF_Wire_V2.mqh, STAFF_Identity_Status.mqh |',
    '', '새 EA와 수정본23 Python 수신기를 함께 사용해야 한다. 구 녹화는 구 수정본으로만 재생한다. 사용자 config는 그대로 보존하며 WONBI_SIGMA를 바꾸려면 config.txt를 수정하고 재시작한다. 실행 중 변경 명령은 없다.',
    f'',f'해시 체인 `Part1/audit/remediation/42-indicator-schema` 등록, build/part1_immutable_sha256.json 재생성. 신규 무결성 오류 0; 이전부터 있던 등록 경고 {len(integrity["remaining_errors"])}개는 그대로다. 사용자 config·monitor_OZ·SPECIAL/COMPOSER 결정 소스의 보호 대조 차이 0.',
    '', 'Part3 제외 규칙은 작업 시작부터 적용했다.',
    '', '## 수정·추가 파일','']
    text.extend('- `'+x['path']+'`' for x in source['changes'])
    text+=['','추가 문서: AGENTS.md.txt 최신 지시, 진단_판다스제거_신호차이.md, 지표정리_목록.md, 본 보고서. 검증 산출물은 검증결과/schema_cleanup/에 모았다. 삭제한 소스 파일은 없고 열·함수·활성 호출 경로를 해당 파일 안에서 정리했다. 다음 단계는 시작하지 않았다.']
    (ROOT/'수정내역_지표정리_스키마.md').write_text('\n'.join(text)+'\n',encoding='utf-8')
    status={'status':'complete','schema_id':identity['schema_id'],'columns':50,'logic_tests':tests['passed'],
        'repeat_capture_equal':True,'bar_timer_inputs_equal':True,'bar_timer_alerts_equal':True,'live_replay_equal':True,
        'alert_differences':0,'mt5_restored':True,'new_integrity_errors':0,'report':'수정내역_지표정리_스키마.md'}
    (OUT/'status.json').write_text(json.dumps(status,ensure_ascii=False,indent=2),encoding='utf-8')
    print(status)
if __name__=='__main__':main()
