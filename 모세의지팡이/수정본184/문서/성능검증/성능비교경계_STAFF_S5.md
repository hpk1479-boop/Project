# S5 성능 비교 경계 사전 고정

측정 전에 아래 경계를 고정한다. build/staff_performance_policy.json, policy.py, protocol.py 및 성능규칙_S1_S8.md는 변경하지 않는다.

- parser_650_rows: 동일 650행 v1 바이트 프레임 1,000개 수신·검증·저장 비용.
- request_0~3: 동일 지표 조합 250회 요청에서 클라이언트가 최종 DataFrame을 받기까지의 전체 CPU 비용. S0는 동결 BEFORE host의 legacy 요청과 서버 계산, S5는 JSON SNAPSHOT 요청 생성·서버 검증/전달·클라이언트 디코드·정규화·staff_compat 계산·복사까지 포함한다. 기존 S0 측정은 실제 TCP 대신 host 직접 전송을 사용했으므로 S5도 동일한 host 내 직접 multipart 전송을 사용한다. 서버만 측정하지 않는다.
- oz_fvg_live_seconds: 동결 시나리오 run 함수의 동일 240초 전체 경로.

동결 protocol의 worker 본문을 새 S5 실행 어댑터에서 사용하며 S0 runtime만 전체 동결 BEFORE host로 선택한다. S5 요청 함수만 최종 DataFrame 반환 경계로 연결한다. 작업 횟수·입력·warmup·GC·CPU 고정·환경 기록·5회 S0→S5 교대·중앙값 산식·S0 자체 범위·작업별 허용치는 동결 정책 그대로다. 동결 측정기 및 정책 파일은 수정하지 않는다. 새로운 결과 디렉터리에 모든 표본을 보존하며 실패 후 표본 재선정이나 허용치 조정을 하지 않는다.
