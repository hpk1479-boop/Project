# 수정본12 S5 인계

수정내역_STAFF_S5.md와 검증결과/staff_s5/status.json을 먼저 읽는다.

S5 구현은 반영했지만 다음 게이트가 실패해 검증 완료로 판정하지 않았다: original_and_goldens_frozen, G1_baseline_signature, existing_14_comparisons, performance_S5.

S6 이후는 시작하지 않았다. 실패한 게이트가 있으면 해결/사용자 지시 없이 다음 단계로 진행하지 않는다.
수정본11의 소스와 S0–S4 증거, 골든·expected·동결 성능 정책은 유지했다. 원본 실행 로그·상태/pyc는 작업 중 변동하여 전체 불변 게이트가 실패했다. original_runtime_drift_detected.json을 확인하고 원본 상태를 임의로 복원하지 않는다. 원비 계산은 S7까지 staff_compat의 기존 함수 그대로다. Part3는 레거시 동결이며 동기화하지 않는다.

STAFF는 numpy Snapshot 수신·검증·저장·SNAPSHOT 전달·제어만 한다. legacy pickle 데이터는 SNAPSHOT API 사용 오류를 반환한다. 모든 계산/Watch 이력/정규화는 클라이언트가 소유한다.
request_0~3 전체 클라이언트 비용과 동결 BEFORE host를 사용하는 측정 경계는 성능비교경계_STAFF_S5.md와 build/run_staff_s5_performance.py에 고정되어 있다. 정책 파일을 변경하거나 실패 표본을 덮어쓰지 않는다.

성능: FAIL; 후보 비율 초과 ['request_0']; S0 자체 범위 이탈 ['request_2'].
Part2 기존 816개 ID 보존 및 새 실패 여부는 baseline_signature_compare.json의 실제 결과를 따른다. 새 실패를 기존 Part3 결함으로 처리하지 않는다.
