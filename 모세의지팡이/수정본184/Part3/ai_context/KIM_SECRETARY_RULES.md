# 김비서 자연어 계약

Part1의 `command_aliases.json`, `command_interpreter.py`, `event_composer_domain.py`, `watch_orchestrator.py`가 현재 공식 해석 기준이다. LLM은 표현을 공식 의미로 옮길 뿐이며, 실제 등록·검증·실행은 프로그램이 한다. 이 Part3 AI는 Watch를 직접 등록하지 않고 신규 SPECIAL 의도만 반환한다.

단순 요청은 제공된 어휘로 처리한다. “김비서 Watch처럼”, “순서무관 latch”, “SPECIAL5 부모 교체” 같은 요청은 조회 도구로 관련 현재 코드를 좁게 읽는다. 도구 응답은 최종 답변이 아니며 원래 전략 생성 요청을 계속 처리한다.
