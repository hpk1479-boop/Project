# Part3 관련 시험

실제 모델·MT5·텔레그램을 호출하지 않는 로직/계약 시험이다.

- test_ai_agent.py: AI 읽기 전용 도구, JSON 검증, 원문 재판정/자동 보정 부재, 사용자 확인 경계.
- test_ai_intent_v2.py: canonical 필드와 Recipe 변환, 구조 검증과 저장.
- test_ai_execution.py: 조건/사건/봉 기준·수명·체크포인트 및 같은 입력 LIVE=재생.
- test_confirmed_50.py: 사용자 확정 50개 구조의 schema→Recipe→validator→compiler→문법.
- test_ai_guard_50.py: 실제 교육 원문 50개→ScriptedProvider 확정 intent→Agent→apply→Recipe→validator→compiler→문법. 현재 이름은 기존 검사 위치를 유지한 것이며 자연어 guard는 없다.
- test_ai_only_generation.py: 수동 생성기 부재, 번호/기존 파일 보호, 초안/코드 입력 차단, 이름 붙은 SPECIAL4/5 고유 인자.
- test_part3_http_flow.py: localhost 실제 웹 API의 AI 적용→미리보기→생성 및 수동 생성 API 차단.

수동 생성기를 고정하던 test_smoke.py 14개는 해당 기능 제거에 따라 제거하고 test_ai_only_generation.py 15개로 대체했다. 구조 시험 50개와 관련 49개는 유지하며 자연어 재판정을 기대하던 시험만 이번 역할 분리에 맞게 변경했다.
