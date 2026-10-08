# FAILED_OPTIMIZATION_LOG — 실패·입력 제약·재측정·미적용 기록

기준은 제공 ZIP 최상위의 최신 Part2다. 최종 pytest는 441 passed / 기존 4 failed / NEW_FAILURE 0이다.
운영 최적화의 최종 채택 5개 단위(gap, BAR, quote, role tick, HMA), 확정 실패로 인한 운영 코드 rollback 0개.
기존 실패/입력 전제 실패/검증 도구 재시도를 최적화 성공이나 새 운영 코드 실패로 집계하지 않는다.

## F-001 — BASELINE_FAILURE: 원래 있던 4개 pytest 실패

| 필드 | 기록 |
| --- | --- |
| 단계 | BASELINE → 최종 동일 |
| 대상 함수/파일 | `validation_suite/test_ipc.py::test_isolated_workers_order_positive_outputs` |
| 시도한 최적화 | 없음. 원본부터 실패했음 |
| 실패한 테스트 | SIGNAL-DELTA_V1, SIGNAL-FULL, ENTRY-DELTA_V1, ENTRY-FULL |
| 예상 결과 | 기존 fixture 전략을 읽고 두 worker의 비어 있지 않은 출력을 비교 |
| 실제 결과 | `backtest_specials/GENERIC_EXAMPLE_V1.py` 누락으로 FileNotFoundError |
| 이벤트 차이 | 로드 전에 실패하여 비교까지 도달하지 않음; NEW_FAILURE 아님 |
| timestamp 차이 | 비교까지 도달하지 않음 |
| 성능 결과 | pytest 원본 387/4, 최종 441/4. 성능 wall-clock 비교로 쓰지 않음 |
| 롤백 여부 | 해당 없음. 누락 파일 복원/폴더명 변경/테스트 삭제 또는 skip 처리하지 않음 |
| 다음 단계 진행 | STEP 1→2→3 및 최종 회귀 모두 진행 |
| 증거 | baseline_tests.xml/log, final_tests.xml/log, integrity_checks.json |

## F-002 — BASELINE_INPUT_PRECONDITION: 실제 prefix의 요구 warmup 부족

| 필드 | 기록 |
| --- | --- |
| 단계 | STEP 2 전략별 측정; 원본에서도 직접 재확인 |
| 대상 함수/파일 | `generic_backtest/runner.py::GenericRunCoordinator.run`, SPECIAL1/2/5 |
| 시도한 최적화 | BAR 명시적 생성자 전후 전체 coordinator 측정 |
| 실패한 테스트/실행 | 실제 50k/5k prefix, STEP 1/2 총 12개 측정 요청; 원본 50k 3개 확인 |
| 예상 결과 | 동일 raw로 해당 SPECIAL 전체 실행 |
| 실제 결과 | 원본과 변경본 모두 `E_WARMUP_INSUFFICIENT: raw prefix missing` |
| 이벤트 차이 | 전략 처리 전 같은 입력 precondition에서 거절됨. 양성 이벤트 비교 아님 |
| timestamp 차이 | 이벤트 처리 전 거절됨 |
| 성능 결과 | 거절까지의 시간을 정상 전략 성능으로 쓰지 않음. 합성 50k/5k 별도 측정은 완료 |
| 롤백 여부 | 불필요. warmup 체크는 최적화 대상이 아니고 동일 원본도 거절. guard/전략 조건을 수정하지 않음 |
| 다음 단계 진행 | 다른 SPECIAL/WATCH, 54 worker 회귀, 합성 보조 입력, STEP 3 진행 |
| 증거 | baseline_input_preconditions.json, baseline_*_raw_precondition.log, step[12]_*_50000.log |

## F-003 — HARNESS_RETRY: 검증 도구 초기 재시도

| 필드 | 기록 |
| --- | --- |
| 단계 | 기준 검증 준비 |
| 대상 | 외부 실행 wrapper 및 신규 `optimization_regression.py`의 임시 fixture 준비 |
| 시도 | 첫 전체 pytest와 임시 archive manifest 작성 |
| 실패 | 첫 pytest가 실행 도구 제한으로 완료 전 종료; 초기 fixture manifest overwrite가 FileExistsError |
| 예상 / 실제 | 완료된 suite/새 manifest를 기대했지만 미완료 또는 exclusive writer 거절 |
| 이벤트 / timestamp 차이 | 유효한 비교 결과가 없어 판단하지 않음; 운영 코드 NEW_FAILURE 아님 |
| 성능 결과 | 미완료 측정은 집계 제외. 완료된 재실행의 XML/로그만 채택 |
| 롤백 여부 | 운영 코드 변경 없음. harness가 자신이 생성한 임시 manifest만 unlink 후 다시 작성하도록 수정 |
| 다음 단계 진행 | 성공한 baseline/STEP 1/2/3 54-case 결과를 확보하고 전체 절차 진행 |

초기 도구 중단/fixture 준비 오류를 최적화 실패로 위장하거나, 운영용 exclusive `write_json`을 바꾸지 않았다.

## F-004 — PERFORMANCE_RECHECK: STEP 3 최초 sample의 혼재

| 필드 | 기록 |
| --- | --- |
| 단계 | STEP 3 최종 성능 재검증 |
| 대상 | quote/role/HMA 결합본 전체 coordinator |
| 시도 | 최초 RSS-sampled 50k 3회 및 200k 1회 후 교차 측정 |
| 실패한 동일성 테스트 | 없음. 파일 hash/commit 및 54 worker 동일 |
| 예상 결과 | 작은 추가 속도 개선 |
| 실제 결과 | 최초 50k는 3.547→3.555초, 최초 200k는 10.596→10.845초로 방향이 불리했음 |
| 이벤트 차이 / timestamp 차이 | 없음 |
| 성능 재확인 | 200k 순서 반전 2회씩: 11.004→10.334초. sampler 없는 50k 균형 교차 3회씩: 3.485→3.312초. 모든 sample 보존 |
| 롤백 여부 | 실제 악화로 확정하지 않음. 상반된 단기 sample을 재확인했고 함수 전용 및 교차 전체 실행에서 개선 재현. 최종 채택 |
| 다음 단계 진행 | 최종 전체 suite 및 무변경/hash audit 완료 |
| 증거 | performance/step3_*_r*.json, paired_*_200000_r*.json, wall_confirmation_*_50000_r*.json |

프로토콜 변경을 숨기거나 가장 빠른 sample만 선택하지 않았다. RSS sampler가 차이의 유일한 원인이라고 확정하지 않는다.

## F-005 — DEFERRED_RISK: 강제 TF stream 공유 / gate-closed TF 생략

| 필드 | 기록 |
| --- | --- |
| 단계 | STEP 2 분석 |
| 대상 | conditional stream / PITFrameCache / owner별 전략 입력 |
| 시도한 최적화 | 설계 검토만; 운영 코드에 적용하지 않음 |
| 실패한 테스트 | 없음(미적용) |
| 예상 결과 | frame/stream 중복 계산 감소 |
| 실제 판단 | owner/native invocation/복구 시점 및 mutable frame이 달라 의미 동일성 근거가 부족함 |
| 이벤트 차이 / timestamp 차이 | 실행한 변경이 없으므로 해당 없음 |
| 성능 결과 | 미측정. 추정 개선 수치를 제시하지 않음 |
| 롤백 여부 | 미적용이므로 rollback 대상 없음 |
| 다음 단계 진행 | 안전한 BAR 생성 비용 최적화를 채택하고 STEP 3 진행 |

## F-006 — NOT_ATTEMPTED: Numba 및 미활성 수치 kernel 대공사

| 필드 | 기록 |
| --- | --- |
| 단계 | STEP 3 |
| 대상 | Numba/Cython/Rust, Percentile/OZ/FVG/EMA/TRUE-B0 |
| 시도한 최적화 | 실제 profile 검토. JIT/언어 교체 코드는 작성하지 않음 |
| 실패한 테스트 | 없음(미적용) |
| 예상 / 실제 판단 | 충분히 활성화된 실전 CPU 근거가 없는 kernel은 이번 변경에서 제외. 선택한 반복 변환/복사와 활성 HMA의 작은 Python 개선만 적용 |
| 이벤트 / timestamp 차이 | 코드 변경 없음 |
| 성능 결과 | JIT cold/warm 해당 없음. 미측정 향상을 주장하지 않음 |
| 롤백 여부 | 미적용. 실패한 JIT 구현이나 새 dependency는 최종본에 없음 |
| 다음 단계 진행 | 최종 전체 회귀·보고·패키징 완료 |

## 최종 잔존 실패 확인

최종 실패 node 집합은 원본의 4개와 정확히 같다. 기존 pass 387개 유지, 신규 테스트 54개 통과.
실험용 임시 전략 파일과 runtime 출력은 최종 ZIP에서 제외한다. baseline oracle는 동결된 테스트 reference이며
이전 버전 운영 코드 복원이나 실패한 최적화 구현이 아니다.
