# COMPOSER 조건 재평가 축소 — 수정본32

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

공유 정책을 동일하게 고정한 `policy_ALL`과 `optimized_ALL`을 비교했다. 전체 신호 해시와 알림 CSV가 모두 같다. 신호 해시: `926d05d5121f0b79e236d38fa2723d05d6569ffff682dab5ce64262f9e7193c5`. 공유 정책의 추가 수정(외부유동성·명시적 감시 소유자)은 1번 작업 변경으로 분리해 최종 단독/ALL 검증에 포함했다.

| 30,146묶음 | 최적화 전 | 최적화 후 |
|---|---:|---:|
| 엔진 ms/묶음 | 74.114 | 47.480 |
| COMPOSER ms/묶음 | 45.714 | 16.943 |
| 재생 전체 경과 초 | 2563.18 | 1733.76 |

엔진 계측에는 검증용 신호 해시 수집 비용도 포함된다. 월별 검증 프로세스를 병행했으므로 수치는 참고 측정이며 성능 게이트가 아니다. 입력과 엔진 최적화를 함께 적용한 월간 총수치다. 입력 경계의 분리 측정은 `수정내역_입력경로_중복검사.md`에 있다.

검증 증거는 `검증결과/shared_oz_composer_input/`에 새로 기록했다. 관련 로직 시험은 중복을 제외한 128개가 통과했다. 실제 텔레그램 전송은 차단했다. 합성 240초(5,790신호·18알림), 실제 TIMER 239초 구간(230묶음·5,668신호·13알림)의 LIVE 수신과 재생 신호 해시 및 모의 출력 영수증이 일치했다.

Part1 무결성 변경 단위는 `Part1/audit/remediation/62-shared-oz-composer-input`이다. `build/part1_immutable_sha256.json`을 갱신했다. 수정본31부터 있던 미등록 항목 6개는 그대로이며 이번 변경의 새 무결성 오류는 0개다. 기존 항목과 전후 대조는 `integrity_existing_comparison.json`에 기록했다. EA·지표·MQL 헤더·Wire 스키마·사용자 config 15개 보호 파일은 수정본31과 동일하다.

## 변경 파일

`Part1/program/composer_fact_index.py`, `Part1/program/event_composer_domain.py`. 신규 시험은 `tests/test_composer_input32.py`에 있다.
