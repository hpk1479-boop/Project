# Part3 개발 인계

사용자 = 자연어 전략 아이디어 / AI = 자연어 해석 / Part3 = JSON 검증 + 코드 생성.

## 현재 경로

Agent.send → validate_intent → 사용자 적용 → Recipe v2 → validate_recipe/execution_plan → compile_recipe/compile_ai → 사용자 생성 → storage.generate.

수동 카드·슬롯 생성기, Recipe v1 생성, 수동 Python 입력과 원문 정규식 재판정은 제거했다. AI가 준 종목·TF·조건·프로필을 원문에서 다시 추출해 바꾸지 않는다. 조건 수·분기 수의 카드 제한이 없다.

schema.py/intent.py와 일반 intent_runtime.py/intent_port.py의 JSON 계약·계산/실행 의미를 유지한다. 새 기능은 문장별 예외가 아니라 공통 조건/관계 필드로 표현한다. 현재 Part1/Part2 계약은 같은 프로젝트의 실제 폴더에서 읽는다. reference 사본은 실행·AI 조회 기준이 아니다.

## 생성기

- catalog.py: 현재 코드 계약과 원본 SPECIAL의 읽기 전용 정보, Recipe v2 검증 경계.
- ai/schema.py: canonical JSON 필드·지원값·관계 검증과 Recipe v2 변환.
- ai/agent.py: 읽기 전용 조회 → JSON 파싱/검증 → 확인 대기. 의미를 다시 판정하지 않는다.
- ai_compiler.py: 공통 실행 계획과 독립 Test_SPECIAL 생성.
- special_templates.py: 명시적으로 요청된 SPECIAL4/5 고유 수명 규칙만 이름 붙은 인자로 현재 Part1 소스에서 생성. 슬롯/Recipe v1을 경유하지 않는다.
- storage.py: 번호 보호, 미리보기, 사용자 확인 후 저장. 과거 생성 파일은 보기만 제공한다.

AI 도구는 vocabulary/list_specials/describe_special/read_doc/search_project_code/read_project_code뿐이다. 코드·경로·source_text·파일 쓰기/삭제·Shell/임의 실행 권한을 주지 않는다.

## 확인

    python -B -m unittest discover -s tests -p test_ai*.py
    python -B -m unittest discover -s tests -p test_confirmed_50.py
    python -B -m unittest discover -s tests -p test_part3_http_flow.py

확정 원문 50개는 test_ai_guard_50.py에서 ScriptedProvider로 검증한다. 실제 Qwen 호출이나 프롬프트 성능 시험은 별도 사용자 지시가 있어야 한다. Part1/Part2 변경과 실제 텔레그램 전송은 이 작업에서 하지 않는다.
