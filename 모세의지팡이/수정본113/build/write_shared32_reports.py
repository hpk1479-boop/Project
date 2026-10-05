from pathlib import Path
import json
ROOT=Path(__file__).resolve().parents[1];O=ROOT/'검증결과/shared_oz_composer_input'
read=lambda p:json.loads((O/p).read_text('utf8'))
comparison=read('month_signal_comparison.json');times=read('month_timings.json')
indexed={r['run']:r for r in times};before=indexed['policy_ALL'];after=indexed['optimized_ALL']
tests=read('test_summary.json');restored=read('month_restore_verification.json')
input_before=read('policy_input_measurement.json')['normal'];input_after=read('final_input_measurement.json')['normal']
changed=read('changed_files.json')
table='\n'.join(f"| {r['strategy']} | {r['solo']} | {r['ALL']} | {r['exact']} | {r['solo_only']} | {r['ALL_only']} |" for r in comparison['strategies'])
common=f'''검증 증거는 `검증결과/shared_oz_composer_input/`에 새로 기록했다. 관련 로직 시험은 중복을 제외한 {tests['unique_tests']}개가 통과했다. 실제 텔레그램 전송은 차단했다. 합성 240초(5,790신호·18알림), 실제 TIMER 239초 구간(230묶음·5,668신호·13알림)의 LIVE 수신과 재생 신호 해시 및 모의 출력 영수증이 일치했다.

Part1 무결성 변경 단위는 `Part1/audit/remediation/62-shared-oz-composer-input`이다. `build/part1_immutable_sha256.json`을 갱신했다. 수정본31부터 있던 미등록 항목 6개는 그대로이며 이번 변경의 새 무결성 오류는 0개다. 기존 항목과 전후 대조는 `integrity_existing_comparison.json`에 기록했다. EA·지표·MQL 헤더·Wire 스키마·사용자 config 15개 보호 파일은 수정본31과 동일하다.
'''
report=f'''# 공유 OZ 독립 판정 및 김비서 출력 정책 — 수정본32

## 변경

같은 OZ 사건에 붙은 source spec들을 결정적인 순서로 모두 판정한다. 앞 SPECIAL의 거래시간 차단, 내부 단계 소비, 예외가 뒤 SPECIAL을 건너뛰게 하지 않는다. 공유 child 취소·교체·연쇄 완료는 모든 참여자의 판정이 끝난 뒤 처리한다. 실패한 참여자가 있으면 공유 입력은 유지하고 이미 성공한 조건의 알림 영수증으로 재전송을 막는다.

SPECIAL5 내부 단계와 다른 SPECIAL의 최종 알림은 각자 자신의 watch ID 범위만 처리한다. 원래 코드는 첫 handler의 응답을 반환했고, 다른 감시가 있으면 외부유동성 TRUE B0 확인도 조기에 반환했다. 외부 확인은 이제 일반 감시가 있어도 각 외부유동성 gate를 끝까지 검사한다. 또한 서로 다른 명시적 watch ID를 같은 프로필이라는 이유로 등록 단계에서 버리지 않는다. 조건식·ATR 한계·시간 필터·SPECIAL 템플릿은 바꾸지 않았다.

OZ는 감시 소유자와 무관한 시장 사건 ID를 전달하고, COMPOSER는 source spec별 조건 ID와 실제 SPECIAL 이름을 가진 SIGNAL을 낸다. 같은 SPECIAL의 여러 조건이 통과해도 각각 기록·전송한다(사용자 추가 답변 반영). 시장 사건 키는 종목·TF·방향·검증/트리거 프로필·TRUE B0 정체성을 사용한다.

## 출력 설정

`Part1/program/config.txt`에서 `OZ_OUTPUT_POLICY=ALL_STRATEGIES`가 기본이다. 설정을 생략해도 같다. 조건별 신호를 모두 전송하고 같은 신호의 재수신만 막는다. `OZ_OUTPUT_POLICY=MARKET_REPRESENTATIVE`이면 같은 시장 사건·수신자에 첫 성공 알림만 전송한다. 첫 전송 실패는 다음 전략을 막지 않는다. 대표 선택은 결정적인 신호 처리 순서다. 기존 config 값은 직접 바꾸지 않았다.

이 정책은 `manager_KIM.SignalOutput`에만 있다. Part2 `ResultWriter`는 출력 정책 전의 NOTIFICATION SIGNAL을 저장한다. 가상 진입은 그 CSV와 B0 정보를 읽으므로 대표 전송 여부와 관계없이 조건별 신호를 집계한다.

## 2025-09 XAU 한 달

각 실행은 동일한 보존 BAR 30,146묶음, 9월 1일~10월 1일(종료 제외), 같은 시작 상태, 별도 겹침 없음이다. 각 조건별 메시지·시각·방향·수신자·signal_id까지 비교했다.

| 전략 | 단독 | ALL의 해당 전략 | 완전 일치 | 단독에만 | ALL에만 |
|---|---:|---:|---:|---:|---:|
{table}

차이 전체 목록과 시장 사건별 감시 귀속은 `month_signal_comparison.json`에 있다. 단독 결과를 정답으로 맞추지 않았다. CSV 차이는 3행이지만 실제로는 아래 두 사건이다. 시각은 UTC다.

| 시각 | 차이 | 확인된 원인 |
|---|---|---|
| 9월 12일 13:53, 6m SHORT | 단독에만 1건 | 단독은 05:12 TRUE B0 3647.59로 PDH 3649.12를 2차 확인해 CONFIRMED가 됐다. ALL은 해당 확인 호출이 없었고 06:58 생존 거리 탈락(ATR_1P5_SURVIVAL)으로 INVALID가 됐다. 같은 OZ 사건에서 ALL의 적격 감시는 SPECIAL5뿐이었다. |
| 9월 24일 07:43, 6m SHORT | 양쪽 1건씩 있으나 문구·ID 차이 | 단독은 이전 child 완료 뒤 새 child를 써서 `4시간 고가 3646.50`, ALL은 남아 있던 child의 등록 문맥으로 `전일 고가 3454.02`를 표시했다. 시장 사건·시각·방향·현재 가격·TRUE B0는 같다. |

**남은 사항:** 공유 OZ 후보의 평가 이력이 활성 감시 구성에 영향을 받는 상위 상태 결합이 남아 있다. 현재 상태가 INVALID이면 `_matching`에서 제외하는 동작 자체는 조건에 맞지만, 위 결과를 “정당한 동일 결과”로 처리하지 않는다. 이번 요청에 따라 차이와 이유를 기록했으며 후보 상태 머신·판정식을 추가로 고치지는 않았다. 김매니저 출력 정책의 대표 선택이나 첫 SPECIAL의 응답 반환으로 생긴 차이는 아니다. 상세 근거는 `shared_difference_diagnosis.json`, `case6m_ALL.jsonl`, `case6m_SPECIAL2.jsonl`에 있다.

공유 정책을 고정한 최적화 비교에서는 알림 102건이 전후 동일하다. 마지막 두 공유 결함 수정(외부 확인 조기 반환, 명시적 watch ID 등록)을 적용한 최종 ALL은 123건이며, 증가한 21건은 SPECIAL2다. 최적화 효과와 정책 수정 효과를 혼합하지 않는다.

| 최종 한 달 실행 | 알림 | 엔진 ms/묶음 | COMPOSER ms/묶음 |
|---|---:|---:|---:|
| ALL | 123 | {indexed['final_ALL']['engine_ms']:.3f} | {indexed['final_ALL']['composer_ms']:.3f} |
| SPECIAL1 | 51 | {indexed['final_SPECIAL1']['engine_ms']:.3f} | {indexed['final_SPECIAL1']['composer_ms']:.3f} |
| SPECIAL2 | 25 | {indexed['final_SPECIAL2']['engine_ms']:.3f} | {indexed['final_SPECIAL2']['composer_ms']:.3f} |
| SPECIAL6 | 32 | {indexed['final_SPECIAL6']['engine_ms']:.3f} | {indexed['final_SPECIAL6']['composer_ms']:.3f} |

동시 검증·증거 수집을 포함한 참고값이며 성능 게이트는 아니다.

{common}
## 변경 파일

`composer_oz_dispatch.py`, `event_composer_domain.py`, `event_composition.py`, `manager_KIM.py`, `event_engine/model.py`, `event_engine/engine.py`, `event_engine/composition_consumer.py`, `oz_engine/controllers.py`(이상 Part1/program), `Part2/event_backtest/warehouse.py`.
'''
(ROOT/'수정내역_공유OZ_독립판정.md').write_text(report,encoding='utf8')
report=f'''# COMPOSER 조건 재평가 축소 — 수정본32

## 변경

공식·개인 spec 저장소를 사전 인터페이스의 `SpecRegistry`로 감쌌다. 등록·교체·삭제·복원 시 (종목, TF, 사실 종류) 대응표를 갱신한다. 현재 ALL 시작 시 공식 spec 21개의 대응은 `fact_dependency_table.json`에 있다. 변경된 FACT_SNAPSHOT을 쓰는 spec과 의존성을 확정하지 못한 spec만 평가한다. spec 등록 순서와 각 방향 판정식은 그대로다.

| 조건 | 사실 종류 |
|---|---|
| TREND, TREND_METRIC | TREND |
| FVG | FVG |
| SWEEP | SWEEP |
| WONBI / PERCENTILE | 각각 같은 이름 |
| MA_STATE / MA_PRICE_STATE / MA_SLOPE_STATE | MA |
| 알 수 없는 종류·TF·빈 조건 | 같은 종목의 모든 snapshot에서 기존처럼 평가 |

시장 묶음·연쇄·명령 처리의 기존 평가 경로는 유지했다. SPECIAL2 하루 프로파일의 방향 조건 평가 호출은 이전 진단 381,384회에서 60,888회로 줄었다. 서로 다른 시점 프로파일의 절대 CPU 배수로 해석하지 않는다.

## 한 달 동일성 및 측정

공유 정책을 동일하게 고정한 `policy_ALL`과 `optimized_ALL`을 비교했다. 전체 신호 해시와 알림 CSV가 모두 같다. 신호 해시: `{comparison['optimization_digest']['sha256']}`. 공유 정책의 추가 수정(외부유동성·명시적 감시 소유자)은 1번 작업 변경으로 분리해 최종 단독/ALL 검증에 포함했다.

| 30,146묶음 | 최적화 전 | 최적화 후 |
|---|---:|---:|
| 엔진 ms/묶음 | {before['engine_ms']:.3f} | {after['engine_ms']:.3f} |
| COMPOSER ms/묶음 | {before['composer_ms']:.3f} | {after['composer_ms']:.3f} |
| 재생 전체 경과 초 | {before['elapsed_seconds']:.2f} | {after['elapsed_seconds']:.2f} |

엔진 계측에는 검증용 신호 해시 수집 비용도 포함된다. 월별 검증 프로세스를 병행했으므로 수치는 참고 측정이며 성능 게이트가 아니다. 입력과 엔진 최적화를 함께 적용한 월간 총수치다. 입력 경계의 분리 측정은 `수정내역_입력경로_중복검사.md`에 있다.

{common}
## 변경 파일

`Part1/program/composer_fact_index.py`, `Part1/program/event_composer_domain.py`. 신규 시험은 `tests/test_composer_input32.py`에 있다.
'''
(ROOT/'수정내역_COMPOSER_조건재평가.md').write_text(report,encoding='utf8')
report=f'''# 입력 경로 중복 검사 축소 — 수정본32

## 변경

재생 시 복원기의 반복 CRC 검사를 STAFF에 맡긴다. 길이·행 매핑·일별 복원 SHA256 검사는 그대로다. 구축/독립 검증 API의 기본값은 CRC 검사 유지다. `verify_indexed`의 한 달 전체 복원 검사도 유지했다.

연속 조각/seq 판단에 필요한 디코드는 STAFF의 공개 `inspect_publication`에서 한다. 동일 스레드의 **동일한 불변 bytes 객체**만 바로 다음 `receive_publication`에서 디코드 결과를 한 번 재사용한다. bytearray, 다른 bytes 객체, 재매핑한 바이트는 다시 검사한다. seq·schema·허용 심볼·건강 상태·원자 publication은 LIVE와 재생 모두 기존 STAFF 경로에서 처리한다. LIVE 파이프 경로를 우회하지 않는다.

DeltaCodec FULL 복원은 이미 소유권을 가진 시간/값 배열을 다시 복사하지 않는다. 행 시각 정렬과 변경 셀 반영은 기존 NumPy 방식이다. TF마다 다른 길이와 이전 상태에 의존하므로 피드 순회 자체는 유지했다. 녹화 형식·압축·키프레임·Wire는 변경하지 않았다.

## 검증

- 한 달 {restored['bundles']:,}묶음 전체 복원 SHA256: `{restored['bundle_sha256']}`.
- 기본 CRC 검증 경로와 STAFF에 CRC를 맡기는 경로 모두 같은 저장된 원본 묶음 해시와 일치.
- NaN payload·EMPTY·음의 0·무한대 비트를 포함한 FULL/ROW 복원 시험 통과.
- CRC 손상, 입력 변경, 중복 seq의 기존 STAFF 처리 유지. 구축 검증 기본값 유지.
- 동일 정책의 한 달 전체 신호와 알림이 최적화 전후 일치.

| 첫 800묶음 입력 경계 | 전 | 후 |
|---|---:|---:|
| 증거용 배열 해시 제외 ms/묶음 | {input_before['bridge_ms_per_bundle']:.3f} | {input_after['bridge_ms_per_bundle']:.3f} |

두 경로의 Snapshot 해시도 일치했다. 일반 계측과 cProfile은 별개이며 위 표는 일반 계측이다. 다른 월간 검증이 병행된 참고값이다.

{common}
## 변경 파일

`Part1/program/THE STAFF OF MOSES.py`, `Part2/event_backtest/bridge.py`, `delta.py`, `keyframes.py`, `storage.py`.
'''
(ROOT/'수정내역_입력경로_중복검사.md').write_text(report,encoding='utf8')
report='''# 남은 병목 — 수정본32 (보고만)

SPECIAL2 실제 첫날 1,215묶음 cProfile 및 입력 800묶음 프로파일 기준이다. 아래 비용은 프로파일 누적시간/묶음이며 중첩되어 더할 수 없다. 계측 오버헤드와 검증용 신호 해시는 실제 운영 비용과 구분한다. 이번에 아래 항목을 추가로 고치지 않았다.

| 위치 | 관측 비용 ms/묶음 | 후속 후보·예상 효과 | 위험 |
|---|---:|---|---|
| `event_engine/domain_support.py:plain` | SPECIAL2 프로파일 약 11.65 (검증용 복제 포함) | Consumer와 결과 저장 경계의 같은 payload 재변환 축소. 운영 몫은 재계측 필요 | 중간: 가변 자료 오염·필드 누락 |
| `THE STAFF OF MOSES.py:_publish_arrays` | SPECIAL2 프로파일 약 7.03 | indicator validity 계산의 반복 열 탐색/650행 스캔 축소. 입력 전용 프로파일에서도 큰 비중 | 높음: 미준비·정정·건강 상태 의미 |
| `event_engine/model.py:freeze` | SPECIAL2 프로파일 약 5.20 | 이미 봉인한 사실 payload의 중복 동결 줄이기 | 중간: 불변성·스레드 안전 |
| `event_composer_domain.py:_condition_source_usable` | SPECIAL2 프로파일 약 3.31 | 동일 사건에서 반복되는 source binding/건강 상태 조회 공유 | 높음: stale·epoch 전환 누락 |
| `event_engine/board.py:view` | SPECIAL2 프로파일 약 2.79 | 같은 publication의 읽기 전용 view 생성 재사용 | 중간: consumer 접근 권한/이전 묶음 재사용 |
| `event_backtest/delta.py:decode` | 입력 전용 프로파일 약 4.26 (3.409초/800묶음) | 서로 다른 TF의 배열·bytes 조립 비용 축소. 추가 변경은 비트 복원 검증 필요 | 중간: NaN/음의 0/행 정정 손실 |

gzip 자체는 현재 프로파일에서 위 항목보다 작다. 측정 없이 압축 방식 교체가 가장 큰 효과라고 단정하지 않는다. 입력 프로파일의 `hashlib.update` 상당 부분은 이번 검증용 Snapshot 해시이며 운영 병목으로 계산하지 않는다. 월간 엔진 내부의 검증용 `validate_shared32.accept`도 최적화 대상으로 삼지 않는다.

예상 절감량의 상한은 각 행의 현재 관측 비용이지만, 필수 계산과 프로파일 오버헤드가 포함돼 실제 절감은 그보다 작다. 특히 plain/freeze와 STAFF 내부 비용은 서로 중첩된다. 운영 입력만의 분리 측정 없이 절감 ms를 더하거나 확정 효과로 제시하지 않는다.

증거: `검증결과/shared_oz_composer_input/final_SPECIAL2_profile_short/profile.txt`, `final_input_profile.txt`, `month_timings.json`.
'''
(ROOT/'추가병목_수정본32.md').write_text(report,encoding='utf8')
print('four reports written; review monthly difference reasons before final completion')
