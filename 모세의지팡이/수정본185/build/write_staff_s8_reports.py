from staff_s8_evidence import *
if (OUT / 'baseline_S8/manifest.json').exists():
    raise SystemExit('S8 is approved and frozen; historical finalizer must not overwrite it')
status=read(OUT/'status.json');behavior=read(OUT/'external_behavior_summary.json')
perf=read(OUT/'performance/comparison.json');tests=read(OUT/'baseline_signature_compare.json')
ea=read(OUT/'ea_output_equality.json');diag=read(OUT/'ea_diagnosis.json');live=read(OUT/'live_performance_reference.json')
build=read(OUT/'ea_build_identity.json')
verdict='완료' if status['s8_complete'] else '구현·검증 수행 완료, 필수 게이트 미통과로 S8 완료 승인 보류'
lines=[f'# 수정본15 — S8 파이프·EA 최적화', '',f'판정: **{verdict}**. 수정본14 전체 25,211개 파일을 해시 동결한 뒤 독립 복사했다. 이벤트 엔진은 시작하지 않았다.',
 '', '## 범위와 구현', '',
 '- EA: LIVE의 매 주기 전체 HMA/지표/FULL 직렬화를 없앴다. 새 봉, 이력 개수/시작점/지표 계산 개수 변화, 준비 상태 변화는 FULL이다. 진행봉은 CopyBuffer(0,1)의 마지막 값만 읽고 OPEN HMA·OPEN 밴드·원비는 이전 FULL에서 재사용한다. 마지막 값/거래량까지 같으면 HEARTBEAT만 만든다.',
 '- 미전송 묶음은 성공할 때까지 보존한다. 재연결 시 미전송 관측의 FULL 표현을 캐시에서 그때 생성한다. TF 묶음·seq·CRC·관측 시각 및 프레임을 버리지 않는 규칙은 유지했다.',
 '- 테스터는 기존 S7의 현재 봉 계산 경계를 유지하면서 ROW를 직렬화한 뒤 비교하던 중복 작업을 값 비교로 바꿨다.',
 '- STAFF_Identity_Status는 1초마다 기록하거나 계정/연결/활성/파이프 등 상태가 변하면 즉시 기록한다. 시각과 seq는 기록 시 최신 값을 사용한다. 단순 시각·seq 변화만으로 1초 제한을 우회하지 않는다.',
 '- STAFF: 요청 검증 후 동일 publication 조합의 multipart 응답을 캐시한다. 128개 요청 조합 슬롯으로 상한을 두고 각 슬롯의 최신 응답만 보관한다. 수신 프레임을 버리는 coalescing과 무관하다. 반환 목록은 새 객체이며 bytes는 불변이다.',
 '- 매 요청의 허용 심볼/TF, 주말, FEED_NOT_READY, stale, 기대 sigma, 지표 준비 검사는 계속 수행한다. seq 재시작/epoch/수신 시각 변경은 캐시를 무효화한다.',
 '- 수신 배열의 최종 소유자가 불변 bytes일 때만 저장소를 공유한다. 외부 mutable 배열은 복사·동결한다. 정렬된 시간 배열의 검증은 빠른 경로를 사용하고 중복/역행/NaT는 기존 보존 규칙을 적용한다. 응답 인코딩은 수신 lock 밖이다.',
 '', '## 변경 파일', '',
 '- Part1/program/THE STAFF OF MOSES.py',
 '- Part1/program/MT5/THE_STAFF_OF_MOSES.mq5',
 '- Part1/program/MT5/STAFF_Identity_Status.mqh',
 '- Part1/program/MT5/STAFF_Wire_Schema.mqh — 열/상수/schema는 불변, EA 빌드 해시만 갱신',
 '- 신규 Part2/validation_suite/test_staff_s8.py, S8 build 검증 도구, 정책/보고/인계 문서, 무결성 단위31, 현재 불변 목록',
 '', '전략·매니저·SPECIAL·클라이언트 계산식·Wire 구조/schema·Part3·사용자 config·S0–S7 기준선·expected·성능 정책은 변경하지 않았다. 기존 테스트는 수정하지 않았다. Part3 참조 5곳은 목록만 유지했다.',
 '', '## EA 출력 검증과 발견한 기존 결함', '',
 f"XAUUSD+ 2026-09-23 12:00–12:04 UTC, BTCUSD 2026-09-19 00:00–00:10 UTC를 S7/S8 EA로 각각 실제 캡처했다. 총 관측 {sum(x['observations'] for x in ea['cases']):,}개. 별도 실행 간 원시 Snapshot 전체 일치: **{ea['passed']}**.",
 '', '차이는 양 종목 일봉의 가장 오래된 2행, 21–44열(24개 지표 열)에만 있다. 같은 S7 EA를 재컴파일·재실행해도 동일 셀들이 변한다. 1e-311 수준 값 등 초기 지표 버퍼의 비결정적 값이 관측되며, S8에서 추가로 달라진 셀은 없다. 가격·거래량·원비 열은 이 결함의 차이 대상이 아니다. 지표 초기 버퍼의 미초기화가 의심되지만 계산/값 변경 금지 범위이므로 값을 치환하거나 지표를 고치지 않았다.',
 '', f"추가 진단 EA에서 같은 관측 시각의 최적화 LIVE 상태와 기존 전체 BuildStaffPayload를 직접 비교했다. XAU/BTC {diag['live_fast_vs_full_same_observation']['observations']:,}개 모두 시간·거래량·48열이 비트 동일했다. FULL/ROW/HB 구성: {diag['live_fast_vs_full_same_observation']['kinds']}. 진단 코드는 검증 폴더에만 있으며 생산 소스에는 들어 있지 않다.",
 '', '따라서 최적화 경로의 새 값 차이는 발견되지 않았지만, 사용자가 지정한 별도 실행 간 완전 동일 게이트의 실패를 자동 면제하지 않았다. 기존 S7 기준선을 그대로 유지한다. 원본 실패·재현 자료: ea_output_equality.json, ea_diagnosis.json, capture_difference_diagnosis.json, *_sigma3_v2 캡처.',
 '', '초기 캡처 도구는 source 선택 후 컴파일을 호출하지 않아 지정한 BEFORE EA가 실행됐다는 보장이 없었다. 해당 시도를 *_harness_attempt1로 보존했다. 도구에 명시적 컴파일과 실행 소스/EX5 해시 기록을 추가하고 필요한 전후·재현 비교만 다시 실행했다. 최종 판정은 executed_source.json이 있는 캡처만 사용한다.',
 '', '## 외부 동작', '', '| 시나리오 | 최종 알림 | 조건 충족 전달 | S7·LIVE↔BT |','|---|---:|---:|---|']
for s in behavior['scenarios']:
    c=s['counts']['after_live'];lines.append(f"| {s['name']} | {c['final_alerts']} | {c['condition_deliveries']} | {'통과' if s['passed'] else '실패'} |")
lines+=['',f"총 최종 알림 {behavior['final_alerts']}건, 조건 충족 전달 {behavior['condition_deliveries']}건이다. 시나리오당 S8 LIVE 한 번만 합산하며 두 항목은 중복될 수 있다. finals/SPECIAL/문구·시각·수신자/manager 이벤트/OZ/FVG/Watch/source health 8개 외부 필드를 원비 포함 비교했다. S7의 비원비 전용 제외 규칙은 S8 판정에 적용하지 않았다.",
 '', '## 전체 테스트·실측·보존', '',
 f"- Part1 audit: {tests['audit_counts']}. 루트: {tests['root_counts']}. 새 실패 root={len(tests['root_new_failures'])}, audit={len(tests['audit_new_failures'])}. 각 전체 실행 1회.",
 f"- S8 신규12 + S7 필수20: {tests['new_tests_counts']}. 기존 Part2 일반 회귀 4묶음은 실행하지 않았다.",
 '- 기존 Part1 무결성 실패1, 루트 DataManager/GUI·Windows 불변 목록 경로 관련 실패·collection error는 보존된 기준선과 signature로 비교했다. 성공하지 않은 기존 테스트를 PASS로 바꾸지 않았다.',
 '- EA·PRICE/RSI/STO/DI 5개 컴파일 오류·경고0. 실제 BTCUSD LIVE v2 19TF 수신·원비 열 확인. 격리 터미널이며 거래/전략 서비스/외부 알림을 실행하지 않았다.',
 '- 무결성 단위31에 생산 변경4개 등록. 기존 진단22개, 신규0. 현재 Part1 불변 목록1222개 재생성·검증.',
 '- 원본 수정본14와 보호된 S0–S7·Part3·expected·성능 정책 해시 불변. BTCUSD config 원문 불변.',
 '', '## 성능 판정', '',
 '성능비교경계_STAFF_S8.md를 측정 전에 고정했다. 같은 논리 CPU, S0→S8 순 5쌍, CPU 중앙값 및 동결 S0 자체 범위 확인. S0는 전체 동결 BEFORE host다. parser는 원래 650행 v1/45열 1000회, 전체 경로는 원래 240초·8 Watch다. 제외된 Part2 회귀 테스트를 수집한 것이 아니라 동결 성능 함수만 호출했다.',
 '', '| 작업 | S0 CPU 중앙값 | S8 CPU 중앙값 | 비율 | 허용치 | S0 범위 | 판정 |','|---|---:|---:|---:|---:|---|---|']
for k,v in perf['metrics'].items():
    gating=k in perf['gating_workloads'];ok=v['pass'] and v['environment_stable']
    lines.append(f"| {k} | {v['s0_median']:.6f} | {v['candidate_median']:.6f} | {v['ratio']:.4f} | {v['limit']:.2f} | {'안정' if v['environment_stable'] else '이탈'} | {('통과' if ok else '실패') if gating else '참고만'} |")
lines+=['',f"정식 성능 게이트: **{perf['pass']}**, 환경 일치: {perf['environment_match']}. 허용치/표본/산식은 변경하지 않았다. 원본 10개 worker 표본과 실패 수치는 performance/에 보존한다.",
 '', '## 동시 LIVE 참고 측정', '', '| 항목 | S7 | S8 |','|---|---:|---:|']
for k in ('seconds','frames_per_second','feed_frames','pipe_bytes','receive_cpu_s','status_file_observed_writes'):
    lines.append(f"| {k} | {live['S7'][k]} | {live['S8'][k]} |")
lines+=['','동일한 실제 BTC 구간이 겹치도록 두 격리 EA를 동시에 실행했다. 시작 후15초를 제외한 공통 구간이다. 프레임 수는 묶음 기준이며 feed_frames도 별도 기재했다. CPU는 수신 스레드의 read/파싱/검증/저장 CPU이고 캡처 파일 기록은 제외했다. 상태 파일은 20ms mtime 관측 횟수이므로 OS 스케줄링에 따른 누락 가능성이 있다. 참고1회이며 성능 게이트가 아니다.',
 '', '## 최종 상태', '',f"미통과 게이트: {status['failed_gates']}. 사용자 승인 예외는 없다. S8 결과로 S7 기준선을 덮어쓰지 않았다. 이벤트 엔진은 미착수다.",
 '',f"Wire schema=0x{build['schema_id']:08X}, EA build={build['build_sha256']}. 상세 판정은 검증결과/staff_s8/status.json을 따른다."]
(ROOT/'수정내역_STAFF_S8.md').write_text('\n'.join(lines)+'\n','utf-8')
handoff=f'''# 수정본15 S8 인계

{verdict}. 먼저 수정내역_STAFF_S8.md와 검증결과/staff_s8/status.json을 읽는다. **이벤트 엔진은 시작하지 않았다.**

## 현재 상태

- 생산 변경은 STAFF, EA, Identity Status, 생성 mqh 빌드 해시 4개뿐이다. schema=0x{build['schema_id']:08X}, 48열/Wire 구조 불변.
- 진행봉은 마지막 버퍼만 갱신하고 OPEN HMA/원비를 재사용한다. 동일 값은 HB. FULL은 새 봉·이력/준비 변화·재연결이며 미전송 관측을 버리지 않는다.
- STAFF는 같은 publication 응답을 캐시하고 수신 bytes 저장소를 안전하게 공유한다. stale/seq/reconnect/health 의미를 바꾸지 않았다.
- 외부 5개 시나리오 결과={behavior['passed']}, G1 새 실패0={tests['pass']}, 정식 성능={perf['pass']}.
- 미통과: {status['failed_gates']}. 허용치를 바꾸거나 자동 승인하지 않았다.

## 다음 판단에 필요한 근거

별도 MT5 실행 간 일봉 최초2행/지표24열의 비결정성은 같은 S7 재실행에서도 재현됐다. S8 신규 차이 셀은 없고, 동일 시각의 LIVE 증분/FULL 대조 10,469개는 일치한다. 실제 원시 출력 게이트는 실패 상태로 보존했다. 초기 지표 버퍼 수정은 값 변경이므로 이번 S8 범위에서 임의로 하지 않았다.

성능 표본10개와 판정은 검증결과/staff_s8/performance/에 있다. S0 자체 범위 또는 후보 비율 실패가 있으면 기록을 그대로 보고 다음 지시를 받는다. request_0~3은 사용자 지시로 참고만이다.

## 기준선·도구

- 현재 기준선: 검증결과/staff_s7/baseline_S7/manifest.json. S0–S7 보존, S8 새 기준선 없음.
- 최신 생성기/컴파일: build/generate_staff_s8_schema.py, compile_staff_s8_mt5.py. 컴파일 후 격리 터미널도 최종 생산 EA로 복원했다.
- EA 증거: ea_output_equality.json, ea_diagnosis.json, 각 캡처 executed_source.json. harness_attempt1은 실패한 도구 시도이며 정답 증거가 아니다.
- Part1 audit/루트 전체는 이미 각1회 실행했다. Part2 일반 회귀4묶음은 계속 미실행. 불필요하게 전체 게이트나 S0 생성 작업을 반복하지 않는다.
- Part3 동결, 참조5곳은 Part3_레거시_참조목록.md. 사용자 config BTC 설정 불변.
'''
(ROOT/'인계_STAFF_S8.md').write_text(handoff,'utf-8')
summary=f'''# STAFF 책임분리 최종 요약 — S0~S8

## 단계별 결과

| 단계 | 결과 |
|---|---|
| S0 | 수정본6 전체 기준선·합성/실제 MT5 캡처·성능 기준 동결 |
| S1 | 순수 Fact를 indicator_facts/FVG 등 주인 모듈로 이전 |
| S2 | numpy Snapshot 저장·수신 스레드·공개 입력 주입점 |
| S3 | pickle 없는 SNAPSHOT API·클라이언트 publication 캐시 |
| S4 | 전략/Watch/OZ/manager 클라이언트 이전 |
| S5 | STAFF 파생 계산·legacy 데이터 경로 제거. 사용자 승인 예외 기록 보존 |
| S6 | Wire v2 FULL/ROW/HB/HELLO/BUNDLE, schema·CRC·seq 누락 기록, MSP3 |
| S7 | MT5 원비 원본 전환. Python 원비 제거, 48열, 새 S7 기준선 |
| S8 | 파이프/EA 최적화 구현·검증 수행. 현재 판정: {verdict} |

## 최종 구조

MT5 EA가 OHLC/MT5 지표/원비를 계산한다 → Wire v2 TF 묶음 → STAFF 전용 수신 스레드가 검증·seq 진단·불변 numpy Snapshot을 저장한다 → SNAPSHOT API가 publication 응답을 캐시해 전달한다 → 클라이언트 staff_snapshot/staff_compat와 각 Fact 모듈이 필요한 계산을 수행한다 → 기존 매니저·SPECIAL·Watch·OZ/FVG 조건과 상태 전이가 알림을 결정한다.

STAFF에는 pandas DataFrame 생성이나 파생 지표 계산이 없다. PING/SOURCE_HEALTH/기대 sigma 제어를 유지한다. 원비 sigma는 EA 시작 시 고정 입력이며 SET_WONBI_SIGMA는 기대값만 보낸다. 45열 과거 입력은 MT5 3σ 열을 매핑하고 sigma!=3은 명시 거부한다. 원비 없는 OHLCV/틱 경로에 Python fallback을 만들지 않는다.

## 명세와 기준선

- Wire_v2_명세.md 및 Part1/program/staff_schema.py가 현재 명세/레지스트리다. 기존45열 + wonbi_upper/lower/sigma, wonbi_mid는12열 별칭, schema0x36C28F68.
- S0 참고: 검증결과/staff_s0/. 실제 캡처와 expected를 포함한 이전 단계 기록을 보존한다.
- 현재 불변 기준: 검증결과/staff_s7/baseline_S7/manifest.json, SHA256 19933bf3b67773c7943f4ba8e25a668d097acc7925bcf77fbaffdddddcc0f17c.
- S8 검증 결과: 검증결과/staff_s8/. 미통과 게이트 {status['failed_gates']}. S8 기준선을 새로 고정하거나 S7을 덮어쓰지 않았다.
- 최신 성능 정책은 build/staff_performance_policy.json으로 동결되어 있다. S8 측정 경계는 성능비교경계_STAFF_S8.md.

## 남은 기존 결함과 제한

- S7 EA에서도 일봉 최초2행 지표24열의 비결정적 초기값이 반복 측정으로 확인됐다. S8 동일 시각 증분/FULL은 일치하지만 별도 실행 원시 출력 전체 일치는 실패다. 지표 초기화 수정 여부는 별도 판단이 필요하다.
- 기존 root DataManager 누락/GUI·Windows 경로 불변 목록 실패·collection error 및 audit 무결성22개 진단을 기준선 결함으로 보존했다. Part3는 레거시 동결이고 참조 정리는 별도 작업이다.
- 외부 검증은 합성240초·확대XAU/BTC 각1200초·실제XAU240초/BTC600초이다. 최종 알림14건/조건 전달29건이며 연속 수일 전체를 검증했다는 의미는 아니다.
- LIVE 참고 측정은 짧은 공통 구간1회이며 처리량 보장이 아니다. 상세 성능 실패가 있다면 원본 표본과 허용치를 그대로 유지한다.

## 이벤트 엔진 착수 전에

이벤트 엔진은 구현하지 않았다. S8 미통과 게이트 처리를 먼저 결정한다. 이후 폴링→이벤트 전환에 따른 알림 시점 변화는 별도 승인 변경으로 기록하고 새 기준선을 정해야 한다. source_epoch/stale/reconnect/seq 누락·TF 묶음 의미를 보존하며 프레임을 버리는 coalescing을 넣지 않는다. ATR14_GENERAL과 FVG_WILDER_ATR을 합치지 않는다. Part2 일반 회귀4묶음은 제외 상태를 유지하되 외부 동작 도구·대표 입력·신규 테스트·Part1 audit·루트 tests는 유지한다.
'''
(ROOT/'STAFF_책임분리_최종_요약.md').write_text(summary,'utf-8')
print('S8 reports written; failed gates retained:',status['failed_gates'])
