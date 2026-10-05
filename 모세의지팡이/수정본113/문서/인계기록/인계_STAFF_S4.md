# S4 완료 인계

완성본: 수정본11. 원본 수정본10과 S0–S3 증거는 동결되어 있다.
S4a–d 결과는 수정내역_STAFF_S4.md 및 검증결과/staff_s4/status.json을 확인한다.

S5는 시작하지 않았다. 다음 단계는 별도 독립본에서 진행한다.
S5에서 STAFF legacy 파생 계산을 제거할 때 G2 검증 대상은 staff_compat로 전환한다.
현재 S4 검증 도구의 dual 비교는 아직 살아 있는 legacy 경로를 참조하므로 그대로 S5에 사용할 수 없다.
원비는 S7까지 변경하지 않는다. EA/wire 변경은 S6부터다. Part3는 계속 레거시 동결이다.

성능 게이트는 S5와 S8에서만 build/staff_performance_policy.json의 동결 규칙으로 적용한다.
S0와 새 코드를 번갈아 각 5회, 고정 CPU/환경, CPU 중앙값 비율과 환경 안정성 규칙을 그대로 쓴다.
S2/S3 인계에 있는 BEFORE 전체 프로그램/host 주의사항도 유지한다.

실행 중 클라이언트: 세 전략은 raw Snapshot, OZ는 OZSnapshotFeatures+OZFactMemo,
일반 Watch와 manager_KIM/SPECIAL은 StaffCompat다. SOURCE_HEALTH와 sigma 제어는 기존 API다.
SPECIAL 파일과 결정 로직은 불변이며, MA 계산과 이력 함수도 이전 구현 그대로다.
