# 공유 OZ 독립 판정 및 김비서 출력 정책 — 수정본32

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
| SPECIAL1 | 51 | 51 | 51 | 0 | 0 |
| SPECIAL2 | 25 | 24 | 23 | 2 | 1 |
| SPECIAL6 | 32 | 32 | 32 | 0 | 0 |

차이 전체 목록과 시장 사건별 감시 귀속은 `month_signal_comparison.json`에 있다. 단독 결과를 정답으로 맞추지 않았다. CSV 차이는 3행이지만 실제로는 아래 두 사건이다. 시각은 UTC다.

| 시각 | 차이 | 확인된 원인 |
|---|---|---|
| 9월 12일 13:53, 6m SHORT | 단독에만 1건 | 단독은 05:12 TRUE B0 3647.59로 PDH 3649.12를 2차 확인해 CONFIRMED가 됐다. ALL은 해당 확인 호출이 없었고 06:58 생존 거리 탈락(ATR_1P5_SURVIVAL)으로 INVALID가 됐다. 같은 OZ 사건에서 ALL의 적격 감시는 SPECIAL5뿐이었다. |
| 9월 24일 07:43, 6m SHORT | 양쪽 1건씩 있으나 문구·ID 차이 | 단독은 이전 child 완료 뒤 새 child를 써서 `4시간 고가 3646.50`, ALL은 남아 있던 child의 등록 문맥으로 `전일 고가 3454.02`를 표시했다. 시장 사건·시각·방향·현재 가격·TRUE B0는 같다. |

**남은 사항:** 공유 OZ 후보의 평가 이력이 활성 감시 구성에 영향을 받는 상위 상태 결합이 남아 있다. 현재 상태가 INVALID이면 `_matching`에서 제외하는 동작 자체는 조건에 맞지만, 위 결과를 “정당한 동일 결과”로 처리하지 않는다. 이번 요청에 따라 차이와 이유를 기록했으며 후보 상태 머신·판정식을 추가로 고치지는 않았다. 김매니저 출력 정책의 대표 선택이나 첫 SPECIAL의 응답 반환으로 생긴 차이는 아니다. 상세 근거는 `shared_difference_diagnosis.json`, `case6m_ALL.jsonl`, `case6m_SPECIAL2.jsonl`에 있다.

공유 정책을 고정한 최적화 비교에서는 알림 102건이 전후 동일하다. 마지막 두 공유 결함 수정(외부 확인 조기 반환, 명시적 watch ID 등록)을 적용한 최종 ALL은 123건이며, 증가한 21건은 SPECIAL2다. 최적화 효과와 정책 수정 효과를 혼합하지 않는다.

| 최종 한 달 실행 | 알림 | 엔진 ms/묶음 | COMPOSER ms/묶음 |
|---|---:|---:|---:|
| ALL | 123 | 45.336 | 16.035 |
| SPECIAL1 | 51 | 7.407 | 1.700 |
| SPECIAL2 | 25 | 14.348 | 4.589 |
| SPECIAL6 | 32 | 2.359 | 0.743 |

동시 검증·증거 수집을 포함한 참고값이며 성능 게이트는 아니다.

검증 증거는 `검증결과/shared_oz_composer_input/`에 새로 기록했다. 관련 로직 시험은 중복을 제외한 128개가 통과했다. 실제 텔레그램 전송은 차단했다. 합성 240초(5,790신호·18알림), 실제 TIMER 239초 구간(230묶음·5,668신호·13알림)의 LIVE 수신과 재생 신호 해시 및 모의 출력 영수증이 일치했다.

Part1 무결성 변경 단위는 `Part1/audit/remediation/62-shared-oz-composer-input`이다. `build/part1_immutable_sha256.json`을 갱신했다. 수정본31부터 있던 미등록 항목 6개는 그대로이며 이번 변경의 새 무결성 오류는 0개다. 기존 항목과 전후 대조는 `integrity_existing_comparison.json`에 기록했다. EA·지표·MQL 헤더·Wire 스키마·사용자 config 15개 보호 파일은 수정본31과 동일하다.

## 변경 파일

`composer_oz_dispatch.py`, `event_composer_domain.py`, `event_composition.py`, `manager_KIM.py`, `event_engine/model.py`, `event_engine/engine.py`, `event_engine/composition_consumer.py`, `oz_engine/controllers.py`(이상 Part1/program), `Part2/event_backtest/warehouse.py`.
