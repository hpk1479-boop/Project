import datetime as dt
from staff_s7_evidence import *
status=read(OUT/'status.json');assert status['s7_complete']
behavior=read(OUT/'external_behavior_summary.json');tests=read(OUT/'baseline_signature_compare.json')
perf=read(OUT/'performance_reference.json');live=read(OUT/'live_BTC/result.json');mt5=read(OUT/'mt5_wonbi_verification.json')
delta=read(OUT/'source_delta.json');changed='\n'.join('- `'+x['file']+'`' for x in delta)
table=[]
for x in behavior['scenarios']:
    counts=x['counts']['after_live'];start=dt.datetime.fromtimestamp(x['start'],dt.timezone.utc).isoformat()
    table.append(f"| {x['name']} | {x['symbol']} | {start} / {x['seconds']}초 | {counts['final_alerts']} | {counts['condition_deliveries']} | 통과 |")
text=f'''# 수정본14 — S7 원비 MT5 원본 전환

수정본13 전체를 독립 복사하여 S7만 구현했다. 최종 판정: **완료**. S8은 시작하지 않았다. 원본 수정본13 22,223개 파일과 S0–S6 보존 결과·골든·expected·성능 정책·Part3의 해시 불변을 확인했다. 사용자 BTCUSD config 원문도 그대로다.

## 적용한 정책

원비는 MT5 계산값이 기준 원본이다. 이전 Python 원비 수치를 회귀 정답으로 사용하지 않았다. 외부 조건·상태 전이·알림 시각/방향/문구/수신자·source health/stale/reconnect/seq 의미·LIVE↔BACKTEST를 판정하며 내부 DataFrame/객체/set 순서는 진단이다.

Part2 기존 validation_suite, cadence_input_validation, conditional_validation, watch_ma_validation은 전체 실행과 게이트에서 제외하고 파일을 보존했다. part1_host 외부 시나리오·대표 입력·신규 test_staff_s7은 필수로 실행했다. Part1 audit와 루트 tests는 각 전체 1회 실행했다. 실패 원인 수정 후 해당 실패와 공통 입력 생성기 영향 범위 5개만 재확인했다. 정책은 AGENTS.md.txt와 검증정책_S6이후.md에 반영했다.

## 구현

- EA v1.70: CalcOpenBands4의 기존 OPEN 길이 4, 2-pass 평균·모표준편차에서 원비를 계산한다. InpWonbiSigma 기본 3.0, 양수/유한 입력만 허용하며 실행 중 변경 기능은 없다. 설계서의 런타임 sigma 파일 갱신안은 최신 사용자 지시로 적용하지 않았다.
- 기존 0–44열 불변, 45=wonbi_upper, 46=wonbi_lower, 47=wonbi_sigma. wonbi_mid는 기존 12열 open_band_4_mid의 레지스트리 별칭이다. 새 schema_id `0x36C28F68`, 구 45열 schema `0x4D2FA0E9`도 수용한다.
- Wire v2 40바이트 헤더, FULL/ROW/HEARTBEAT/HELLO/BUNDLE, CRC trailer, seq 누락 기록 구조는 유지했다. 실제 값 열 수만 등록 스키마에 따라 결정한다. HELLO ACK는 수신 schema를 그대로 돌려준다. MSP3는 48열을 기록하며 과거 45열 MSP3와 MSP2도 읽는다.
- STAFF는 수신·검증·불변 Snapshot·제어만 한다. 45열은 MT5 upper 슬롯18/lower 슬롯17/sigma3을 복사하여 48열로 정규화한다. Python 원비 계산을 하지 않는다. 45열 재생에 기대 sigma가 3.0이 아니면 LEGACY_WONBI_SIGMA 명시 오류다.
- SET_WONBI_SIGMA는 기대 sigma만 저장한다. EA sigma와 다르면 WONBI_SIGMA_MISMATCH 로그와 SOURCE_HEALTH의 warnings를 제공한다. 상태 FRESH/STALE 판정이나 MT5 밴드를 바꾸지 않는다.
- staff_compat의 add_wonbi_features와 호출, monitor_OZ 데이터 조합의 해당 호출, Part2 calculations/common의 중복 계산, event_catalog의 Wonbi 재계산을 제거했다. monitor_OZ 판정 AST는 데이터 조합 함수를 제외하면 S6와 같고, 매니저·SPECIAL·전략 파일은 바이트가 같다.
- Part2 OHLCV/틱만 가진 경로는 MT5 원비를 만들어낼 수 없으므로 Wonbi 요청을 MT5_WONBI_REQUIRED로 명시 거부한다. 네이티브 STAFF 캡처 경로를 사용해야 한다. 독립 BB 계산은 유지하며 Wonbi 이전 이벤트 캐시 재사용을 막는 정의 revision 2를 등록했다.
- 합성 전용 원비 생성은 EA 2-pass 포트다. OHLC를 바꾸는 SPECIAL 합성 fixture도 최종 OHLC에서 MT5 입력 열을 생성한다. 생산 경로에 Python 원비 fallback은 없다.

## 변경 파일

{changed}

추가 문서: AGENTS.md.txt, 검증정책_S6이후.md, Wire_v2_명세.md, Part3_레거시_참조목록.md, 본 보고서 및 인계_STAFF_S7.md. S7 build 도구·검증결과, 무결성 단위 30, 현재 불변 목록도 추가/갱신했다. 원본 소스 전후 해시는 source_delta.json에 있다.

## 실제 MT5 검증

EA와 PRICE/RSI/STO/DI 지표 5개 모두 **0 errors / 0 warnings**. 격리 설치 `검증결과/staff_s7/mt5_terminal`에서 컴파일하고 실행했다. 실행 중인 사용자 원본 터미널에 소스를 덮어쓰지 않았다.

- XAUUSD+ 2026-09-23 12:00–12:04 UTC: sigma3/2.5 각각 실제 Strategy Tester MSP3 캡처.
- 19개 TF의 114개 대표 샘플, 동일 시각 57쌍의 밴드 폭 확인. sigma3은 기존 MT5 3σ 열과 같고 sigma2.5의 폭은 2.5/3 비율이다. 상·하단 순서, wonbi_mid 별칭, 적용 sigma, 19개 클라이언트 전달을 확인했다.
- BTCUSD 2026-09-19 00:00–00:10 UTC 실제 주말 MSP3. 현재 토요일 미완료 일자 문제를 피하고 이미 존재하는 주말 구간을 사용했다.
- BTCUSD 실제 LIVE {live['wall_seconds']:.3f}초, {len(live['frames'])}개 파이프 프레임, 19 TF 원비 수신. 전략 서비스 및 외부 알림 전송은 실행하지 않았다. 고유 파이프로 기존 연결과 분리했다.
- S0 합성/실제 골든의 ATR14_GENERAL 대표 입력 2개와 FVG ATR 분리·Wonbi 독립성을 신규 테스트에서 확인했다. 골든 생성은 다시 하지 않았다.

빌드 SHA256: `{status['ea_build_hash']}`. 원본 컴파일 로그·캡처·실패 시도는 그대로 보존했다.

## 외부 동작 비교 및 새 기준선

BEFORE는 수정본13의 **Part1/program 전체**를 해시 동결한 복사본이다. 부품 혼합이 없다. 모든 시나리오에서 S7 LIVE와 BACKTEST의 finals/special/telegram/manager_events/source_health/oz_state/fvg_state/watch_state가 같고, 원비 외 외부 결과는 S6와 같다. 원비 전용 수신자 881010/881011 및 WONBI_TOUCH 감시는 S6 비원비 비교에서만 제외하며, S7 LIVE↔BACKTEST에서는 포함한다. SOURCE_HEALTH의 새 Wonbi 입력 validity만 전후 비원비 비교에서 분리했다.

| 시나리오 | 종목 | UTC 시작 / 구간 | 최종 알림 | 조건 충족 전달 | 결과 |
|---|---|---|---:|---:|---|
{chr(10).join(table)}

S7 LIVE를 시나리오당 한 번 합산하면 최종 알림 {behavior['final_alerts']}건, 등록 확인 제외 조건 충족 전달 {behavior['condition_deliveries']}건이다. 두 수는 서로 중복될 수 있다. 실제 구간의 최종 알림 0건도 그대로 보고하며 Watch 전달·상태 전이는 검증했다. 여러 날짜의 유한 구간이며 연속 여러 날 전체를 검증했다는 뜻은 아니다.

**원비 원본 MT5 전환에 따른 승인된 기준 변경**: 비교 구간에서 제거된 알림 {behavior['changed_notifications_removed']}건, 추가된 알림 {behavior['changed_notifications_added']}건. 원비 차이의 개별 원인이나 이전 Python 수치 일치 여부는 분석하지 않았다.

새 불변 기준선: `검증결과/staff_s7/baseline_S7/manifest.json`. 240초·확장·실제 결과 사본과 실제/합성 MSP3 파일의 해시를 동결했다. S0는 참고 기준으로 보존했다.

## 테스트·무결성 게이트

- Part1 audit 177개: 최종 유효 176 PASS, 기존 source integrity 실패 1. 신규 파이프 fixture의 48열/v1 헤더 혼용은 v2로 수정하고 실제 Windows 파이프 검사만 재실행해 통과했다.
- 루트 279개: 최종 유효 267 PASS, 기존 5 FAIL·1 ERROR·6 SKIP(3 xfail 포함). S6 대비 새 실패 0, 누락 수집 0. 기존 checkpoint 내부 바이트 진단은 이번 실행에서 통과했다.
- S7 신규 20개 모두 PASS. 합성 MT5 전달·sigma 경고·구 v1/v2 sigma 제한·v1 바이트·48열 FULL/ROW/HB·CRC/잘림/schema/seq·재연결·MSP3·ATR 대표 입력/격리를 확인했다.
- 전체 실행 원본: root 34실패/238통과, audit 2실패, S7 1실패를 보존했다. 신규 실패 29개와 S7 EOFError 계약 1개를 원인별 수정 후 30개만 재실행해 모두 통과했다. 공통 합성 fixture 수정의 다른 SPECIAL 대표 5개도 통과했다.
- 변경한 기존 테스트: audit 입력 포장과 원비 기대값을 MT5 열 기준으로 갱신했다. root test_event_catalog의 OHLCV 저장/증분/재개 검사는 독립 BB로 유지하고, MT5 없는 Wonbi 요청의 명시 거부·기존 저장 데이터 보존을 검증하도록 바꿨다. native Wonbi/BB 양성 6개는 Python 계산 호출 대신 입력 MT5 열을 기대값으로 쓴다. 테스트를 삭제하거나 skip/xfail로 바꾸지 않았다.
- 무결성 체인 `30-staff-s7-mt5-wonbi`에 변경 7개 등록. 기존 진단 22개 유지, 새 진단 0. 현재 Part1 불변 목록을 재생성하고 경로 정규화 후 파일 집합·해시 일치를 검증했다. 기존 Windows 구분자 manifest 실패는 기준선 결함으로 남겼다.
- Part3 전체 불변 및 Part1/Part2 참조 5곳 목록만 기록했다. 정리는 S8 이후 별도다.

## 성능 참고 1회

실제 BTC 관측·프레임 종류를 동일하게 두고 S6 45열 v2와 S7 48열 v2 수신/CRC/검증/Snapshot 저장을 각 한 번 측정했다. 인코딩·host 시작은 제외했다. 성능 판정·허용치 변경은 없다. 짧은 CPU 표본 및 동시 검증 부하 때문에 처리량 보장으로 해석하지 않는다.

| 항목 | S6 | S7 | 비율 |
|---|---:|---:|---:|
| 수신 CPU 초 | {perf['measurements']['S6']['receive_cpu_s']:.6f} | {perf['measurements']['S7']['receive_cpu_s']:.6f} | {perf['cpu_ratio']:.4f} |
| 파이프 바이트 | {perf['measurements']['S6']['bytes']} | {perf['measurements']['S7']['bytes']} | {perf['byte_ratio']:.4f} |

## 발견한 결함·제한

S7 이전 Part2 OHLCV/틱 경로가 Wonbi를 Python에서 재계산했고 native BB가 Python Wonbi std에 결합되어 있었다. 이제 Wonbi는 MT5 입력만 받고 BB는 별도 경로다. 원시 OHLCV만으로 Wonbi를 요청하면 오류가 의도된 계약이다. 합성 SPECIAL OHLC 변경 후 원비 입력을 갱신하지 않는 fixture와 audit의 구 헤더 가정도 수정했다.

기본 pytest 임시 폴더의 Windows 접근 권한 오류가 있어 S7 결과 폴더의 고유 basetemp를 사용했다. 기존 DataManager 누락/GUI, Windows manifest 경로 비교, 22개 무결성 진단은 기준선 결함이다. 원본 실패 기록은 삭제하지 않았다.
'''
(ROOT/'수정내역_STAFF_S7.md').write_text(text,encoding='utf-8')
handoff=f'''# 수정본14 S7 인계

S7 완료. S8은 시작하지 않았다. `수정내역_STAFF_S7.md`와 `검증결과/staff_s7/status.json`을 먼저 읽는다.

## 정책

최신 AGENTS.md.txt 및 검증정책_S6이후.md 우선. 원비는 MT5 값이 원본이며 이전 Python 결과와 회귀 비교하지 않는다. 원비 변경 알림은 승인된 기준 변경으로 건수만 기록한다. Part2 기존 일반 회귀 4개 묶음은 실행하지 않는다. Part1 audit·루트 tests·part1_host 외부 시나리오·대표 입력·해당 단계 신규 테스트는 유지한다. 전체 게이트는 단계 구현 완료 후 1회, 실패 원인 관련 검사만 재실행한다. Part3 동결, S0–S6 골든/expected 및 성능 정책 불변.

## 현재 계약

- EA v1.70 InpWonbiSigma=3.0. 시작 시 입력만 사용하며 실행 중 변경 기능은 없다.
- Wire v2 기존 구조에 45 upper, 46 lower, 47 sigma 추가. wonbi_mid=기존12열 별칭. 새 schema `0x36C28F68`, 구45열 `0x4D2FA0E9`도 수용한다.
- 생성기: `build/generate_staff_s7_schema.py`. 컴파일: `build/compile_staff_s7_mt5.py`. 과거 S6 증거를 쓰는 이전 생성기는 S7 갱신에 사용하지 않는다.
- STAFF는 numpy 수신/검증/48열 Snapshot 저장·전달/health/제어만 한다. Python 원비 계산 없음. v1·구v2 45열은 MT5 3σ 슬롯 복사, 기대 sigma!=3이면 LEGACY_WONBI_SIGMA 오류.
- SET_WONBI_SIGMA는 기대 σ다. mismatch 로그+SOURCE_HEALTH warnings만 추가하고 실제 MT5 값·health 상태를 바꾸지 않는다.
- Part2 OHLCV/틱만으로 Wonbi를 만들지 않는다. MT5_WONBI_REQUIRED면 STAFF MSP3/native 경로를 사용한다. 기존 Python Wonbi 이벤트 캐시는 revision2로 분리했다.
- MT5 계산 포트는 synthetic 생성기 및 audit fixture에서만 허용한다. OHLC transform 뒤에 합성 MT5 원비 열을 완성한다.

## 동결 기준선 및 근거

`검증결과/staff_s7/baseline_S7/manifest.json`을 다음 단계 기준으로 사용한다. 해시 `{status['baseline_manifest_sha256']}`. 240초/실제 XAU/BTC/확대 XAU/BTC LIVE↔BACKTEST 동일, S6 비원비 외부 결과 동일. 최종 알림 {behavior['final_alerts']}건·조건 충족 전달 {behavior['condition_deliveries']}건. 원비 승인 기준 변경 알림: 제거 {behavior['changed_notifications_removed']}, 추가 {behavior['changed_notifications_added']}.

실제 sigma3/2.5 캡처 114 대표 샘플·57 폭 비교·19 클라이언트 TF 확인, EA/지표5개 0오류0경고, BTC LIVE19TF. 상세 파일은 `mt5_wonbi_verification.json`, `live_BTC/result.json`, `external_behavior_summary.json`.

G1 새 실패0: audit176 PASS+기존1 FAIL, root267 PASS+기존5 FAIL/1 ERROR/6 SKIP, 신규S7 20 PASS. 기존 Part2 일반 묶음은 실행하지 않았다. 실패 수정 내역과 원본/재실행 결과는 보고서 및 `baseline_signature_compare.json`에서 확인한다.

무결성 단위30, 기존진단22개/신규0. 현재불변목록 재생성. 수정본13 원본22,223개 및 S0–S6/Part3/성능정책 해시 불변. 사용자config BTC설정 원문 보존.

성능 참고 각1회: 수신 CPU 비율 {perf['cpu_ratio']:.4f}, 바이트 비율 {perf['byte_ratio']:.4f}; 판정없음. S5/S8 성능정책을 바꾸지 않았다. 실행된 MT5는 수정본14의 격리 설치이며 배포용 소스는 Part1/program/MT5에 있다.
'''
(ROOT/'인계_STAFF_S7.md').write_text(handoff,encoding='utf-8')
print('S7 reports written')
