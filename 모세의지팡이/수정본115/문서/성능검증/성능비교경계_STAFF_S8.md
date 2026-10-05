# S8 성능 측정 경계 — 측정 전 고정

`build/staff_performance_policy.json` 및 측정·판정 원본 Python 파일은 수정하지 않는다. 같은 논리 CPU(상속 마스크의 최하위), 정상 우선순위의 독립 worker에서 S0→S8 순서로 5쌍 실행한다. 모든 표본을 보존하며 재선별/허용치 변경을 하지 않는다.

parser_650_rows는 동결 정책의 650행 v1 45열 FULL 1,000회 수신이다. 두 worker 모두 S0에 보존된 합성 입력 생성기를 그대로 불러온다. S8 48열 합성 생성 비용이나 v2 CRC를 S0에만 추가하지 않는다. 이것은 성능 입력이며 이전 Python 원비를 S7 결과의 정답으로 쓰는 비교가 아니다. 현재 v2 48열 수신은 별도의 LIVE 참고 기록에 포함한다.

전체 경로는 동결된 test_oz_fvg_optimization.run의 240초·8개 Watch를 그대로 호출한다. 이 함수는 성능 시나리오만 호출하며 제외된 Part2 pytest 회귀 묶음을 수집/실행하지 않는다. 함수 AST 계약 해시를 확인한다. S0는 S2 인계의 전체 동결 BEFORE host 어댑터를 사용한다. S8은 현재 공개 주입 host를 쓴다.

request_0~3은 S5에서 정한 최종 클라이언트 DataFrame 비용 경계를 유지한다(S0 legacy 서버, S8 SNAPSHOT 전달·디코드·staff_compat 포함). 각 250회·준비 호출 수·GC 정책을 그대로 쓴다. 사용자 지시대로 이 네 항목은 환경 범위와 비율을 참고로만 기록한다.

판정은 parser_650_rows(1.05), oz_fvg_live_seconds(1.06) 두 항목의 CPU 중앙값 비율 및 동결 S0 자체 범위, Python/numpy/pandas/platform/affinity 일치다. request_0~3을 판정에서 제외하는 사용자 지시 외에 산식·표본·허용치를 바꾸지 않는다. 실패는 그대로 보고한다.
