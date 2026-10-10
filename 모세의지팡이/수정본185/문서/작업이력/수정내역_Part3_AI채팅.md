# 수정본41 — Part3 AI 채팅(자연어로 전략 만들기) 틀

수정본40을 복사했다(검증결과·캐시 제외). **Part1·Part2 파일은 하나도 바꾸지 않았다**(수정본40 대비 파일 해시 비교로 확인). Part3만 수정했다.

## 목표와 원칙
- 최종 목표
  - 사용자가 SPECIAL 번호를 말하지 않아도, 말로 설명한 전략을 AI가 SPECIAL1~7 구조나 일반 조건 조합으로 표현해 `Test_SPECIAL###.py`를 만든다.
  - "알려줘/감시" 같은 즉석 감시는 Part2 WATCH와 같은 자연어 명령으로 만든다.
- AI의 역할
  - AI는 **파이썬 코드를 직접 쓰지 않는다.** 기존 Part3 기능을 도구로만 조작한다(불러오기·슬롯 변경·검사·미리보기·생성 요청·백테스트 요청).
  - 설정을 바꿀 때마다 `catalog.validate`로 검사한다. 잘못된 변경은 되돌리고 이유를 AI에게 돌려줘 스스로 고치게 한다.
- 사용자 확인
  - **파일 생성과 백테스트는 사용자가 화면의 "확인 · 실행"을 눌러야만 실행된다.**
  - 모델은 `request_generate`/`request_backtest`로 요청만 할 수 있다. 실행 도구는 모델에게 주지 않는다.

## 구성 (Part3)
| 파일 | 역할 |
|---|---|
| `lab/ai/provider.py` | 모델 연결. OpenAI 호환 채팅+도구 호출 형식 하나로 **Gemini 무료 API**와 **Ollama(Qwen3-8B)**를 모두 지원한다. 외부 라이브러리는 쓰지 않는다. 시험용 `ScriptedProvider`가 있다. |
| `lab/ai/tools.py` | 모델이 쓰는 도구 14개와 작업 상태(`Workspace`). 기존 `catalog`·`storage`·`integration`을 감싸기만 한다. |
| `lab/ai/agent.py` | 채팅 루프. 시스템 지시에 `docs/SPECIAL_1_TO_7.md`와 `docs/STRATEGY_SCHEMA.md` 전문을 넣어 **모든 SPECIAL을 읽은 상태**로 시작한다. 한 번에 도구 호출은 최대 12번이다. |
| `watch_check.py` | WATCH 문장을 실제 엔진(WATCH만, 네트워크 차단, 시세 없음)에 한 번 등록해 인식되는지 확인한다. 별도 프로세스에서 Part1/Part2를 **읽기만** 한다. 기존 백테스트 연결과 같은 방식이다. |
| `lab/server.py` | API 추가: `POST /api/ai/chat`, `/api/ai/confirm`, `/api/ai/reset`, `/api/ai/settings`, `GET /api/ai/settings`. 기존 토큰·Origin 검사를 그대로 쓴다. |
| `lab/integration.py`, `backtest.py` | 백테스트에 WATCH 명령을 받는 경로를 추가했다. Part2 WATCH 모드와 같은 `commands=[{'strategy':'WATCH','text':…}]` 계약이다. Test 전략 경로는 그대로다. |
| `web/index.html`, `web/ai_chat.js`, `web/style.css` | "✦ AI 채팅" 탭: 대화창, 도구 실행 기록, 확인·실행 버튼, 연결 설정(제공처·모델·키). 모델 응답은 textContent로만 표시한다(HTML 주입 방지). |

## 도구 목록 (모델이 부를 수 있는 것)
- 알기: `list_specials`, `describe_special`, `vocabulary`
- SPECIAL 편집: `load_special`, `new_custom`, `get_recipe`, `update_recipe`, `set_slot`, `validate`, `preview`
- 실행 요청: `request_generate`, `request_backtest` (확인 대기)
- WATCH: `set_watch_command`, `check_watch_command`

## 연결 설정
- **Gemini (기본)**
  - 주소 `https://generativelanguage.googleapis.com/v1beta/openai/`, 모델 기본값 `gemini-2.5-flash`(화면에서 변경 가능).
  - 키는 환경변수 `GEMINI_API_KEY` 또는 화면에서 저장한다.
  - 화면에서 저장한 키는 이 컴퓨터의 `%LOCALAPPDATA%/THE_STAFF_OF_MOSES/part3_ai_keys.json`에만 둔다. 프로젝트·설정 파일에는 저장하지 않는다(경로 규칙).
- **Ollama / Qwen3-8B**
  - 주소 `http://localhost:11434/v1/`, 모델 `qwen3:8b`. 키는 없다.
  - 처음 한 번 `ollama pull qwen3:8b`(약 5GB)가 필요하다.
- 제공처·모델 선택만 `Part3/projects/ai_settings.json`에 저장한다(키 없음).

## 검증
- `Part3/tests/test_ai_agent.py` 8건(가짜 모델 사용, 네트워크 차단)과 기존 Part3 시험: **22 PASS**
  - 시스템 지시에 SPECIAL1~7과 규칙이 모두 들어간다.
  - SPECIAL 흐름
    - 사용자가 번호를 말하지 않은 문장에 대해 `list_specials`로 기반을 고르고 SPECIAL3을 불러온다.
    - 지원하지 않는 TF(7m)는 **거절되고 이유가 모델에게 돌아간다.**
    - 모델이 5m·15m로 고친다.
    - 생성은 요청 상태로만 남는다(파일 없음). 사용자 확인 후에만 `Test_SPECIAL` 파일이 생긴다. 파일은 문법 검사를 통과하고 새 TF가 들어 있다.
  - 모델은 `confirm`/알 수 없는 도구를 쓸 수 없다. 잘못된 변경은 원래 설정으로 되돌려진다.
  - WATCH: 실제 엔진으로 확인했다.
    - `골드 1분 상단 원비 터치 계속 알려줘`는 인식된다(WONBI_TOUCH, 1m).
    - 의미 없는 문장은 인식되지 않는다.
    - 백테스트 요청은 확인 대기 상태가 된다.
  - OpenAI 호환 요청 형식(주소·인증·도구)과 도구 호출 해석을 확인했다.
  - 모델이 없을 때(404) `ollama pull` 안내 오류가 나온다.
  - 키는 컴퓨터별 위치에 저장된다.
- 브라우저 확인(로컬 서버): AI 채팅 탭 열기 → 메시지 전송까지 정상이다.
  - Gemini 키가 없으면 "키 없음" 안내가 나온다.
  - Ollama로 바꾸면 "qwen3:8b 없음 · ollama pull" 안내가 나온다.
  - 시험으로 생긴 설정 파일과 초안은 지웠다.

## 아직 안 한 것 (다음 단계)
- **실제 모델 대화 품질 시험.** 이 PC에는 Gemini 키가 없고 Ollama에 받아 둔 모델이 없다. 키 입력 또는 `ollama pull qwen3:8b` 후 진행한다.
- 프롬프트 조정. 특히 Qwen3-8B는 도구 호출이 흔들릴 수 있다. 실제 대화로 실패 유형을 보고 도구 설명·예시를 다듬는다.
- SPECIAL3/4/5 같은 연결형의 `options`(연결 순서·시간 제한 등)를 말로 바꾸는 사례 시험
- 화면 연동
  - 생성한 결과를 기존 "전략 설정" 탭 화면에 바로 불러오기
  - 백테스트 진행 상태를 채팅 창에 표시하기(현재는 시작 메시지만 표시)
- 대화 기록은 서버 메모리에만 있다(서버를 끄면 사라짐).

## 추가 — 실행 창 정리 (사용자 요청)
- **Part3를 명령 프롬프트 없이 시작**
  - `START_PART3.pyw`(신규, 더블클릭)는 pythonw로 서버를 켜고 브라우저를 연다.
  - `START_PART3.bat`도 이 방식으로 바꿨다. bat 창은 잠깐 떴다 바로 닫힌다.
  - 콘솔이 없어 Ctrl+C로 끌 수 없다. 대신 페이지가 20초마다 `/api/ping`을 보내고, 탭을 닫으면 약 3분 뒤 서버가 스스로 종료된다(`run.py --idle-exit`).
  - 시작 오류는 오류 창으로 알리고 `logs/start_error.txt`에 남긴다.
  - 기존 `python run.py`(콘솔) 방식도 그대로 쓸 수 있다.
- **버튼 이름**: `PART 1 데이터 준비` → `PART 1 라이브 감시`. 아래쪽 버튼 설명도 `라이브 감시 화면`으로 바꿨다.
- **PART1/PART2 버튼**
  - 두 버튼 모두 .pyw를 더블클릭한 것처럼 pythonw로 콘솔 없이 연다(`CREATE_NO_WINDOW`).
- **PART2 버튼 화면이 안 뜨던 원인**
  - 버튼이 백테스트 화면이 아니라 예전 가상환경 복구 스크립트 `Part2/bootstrap.py`를 실행하고 있었다.
  - 실제 화면인 `Part2/BACKTEST CONTROL.pyw`로 고쳤다.
  - 이 PC에서 실제로 눌러 "SPECIAL / WATCH 백테스트" 창이 pythonw로 뜨는 것을 확인하고 닫았다.
- 시험 추가: 두 버튼의 대상·pythonw·콘솔 없음, 페이지가 멈추면 서버 종료. Part3 시험 **25 PASS**.
- 참고: Part1 시스템 컨트롤이 켜는 LIVE 엔진 등의 창은 Part1 자체 동작이라 바꾸지 않았다(Part1 수정 금지).
- **오른쪽 아래 버튼 정리**: 상단과 같은 기능인 `PART 1 호출` 버튼을 뺐다. 아래 PART2 버튼은 상단(백테스트 화면 열기)과 기능이 달라 남기되, 이름을 `이 Test 전략 백테스트`(생성된 Test 전략을 PART2 엔진으로 실행)로 바꿨다. 화면에서 확인했다.
- **탭 정리**: 순서를 전략 설정 → AI 채팅 → 코드 편집 → 연결 설정으로 바꿨다. GPT 작업 문서 탭은 화면에서 뺐다(MD 파일은 그대로 둠).
- **AI가 작업 문서를 읽음**: 도구 `list_docs`/`read_doc`을 추가했다. Part3 최상위와 `docs/`의 MD는 자동으로 잡힌다(새 예시 문서를 넣으면 코드 수정 없이 AI가 찾는다). 긴 문서는 이어 읽는다. 폴더 밖 경로는 거절한다. 지시문 7번에 '어려운 구현·예시는 문서를 찾아 읽고 따를 것'을 추가했다. Part3 시험 **26 PASS**, 화면에서 네 탭 전환을 확인했다.
- **빈 화면(슬롯이 안 보임) 안내**: 이전 실행의 탭을 새로고침하거나 즐겨찾기로 열면 접속 토큰이 달라 모든 API가 거절(403)되고, 화면이 비어 슬롯이 1개로 보였다(슬롯이 줄어든 것이 아님). 페이지를 열 때 `/api/ping`으로 확인해 거절되면 상단에 '이 탭을 닫고 START_PART3를 다시 실행하세요' 빨간 안내를 띄운다. 브라우저에서 잘못된 토큰(안내 표시, 슬롯 0)과 올바른 토큰(안내 없음, 슬롯 4·SPECIAL 7)을 확인했다.
- **버그 수정(창 없는 실행 후 화면이 연결 안 됨)**: 콘솔 창이 없어 실행 중인지 보이지 않자 `START_PART3.pyw`가 여러 번 실행됐다(서버 5개). Windows에서 파이썬 웹서버가 이미 사용 중인 포트(8763)도 함께 잡을 수 있어서, 새 탭이 옛 서버에 연결되고 모든 API가 403으로 거절됐다. `LabServer`가 포트를 배타적으로 잡도록 고쳤다(`SO_EXCLUSIVEADDRUSE`, 주소 재사용 끔). 이제 두 번째 실행은 자동으로 빈 포트로 옮겨 각자 정상 동작한다(실측: 8763, 54802 모두 200). 남아 있던 Part3 서버 5개는 종료했다. 시험 추가, Part3 **27 PASS**.
- **탭을 닫으면 서버 종료**: 탭이 닫힐 때(pagehide) 페이지가 `/api/bye`를 보내고, 창 없는 실행(`START_PART3.pyw`)의 서버는 25초 안에 새 요청이 없으면 종료한다. 새로고침은 바로 다시 접속하므로 유지된다. 브라우저가 강제 종료되어 신호를 못 보내면 기존처럼 3분 뒤 종료한다. 콘솔 실행(`python run.py`)은 영향 없음. 시험: 닫힘 신호 후 종료, 신호 뒤 재접속(새로고침) 시 유지 — Part3 **29 PASS**. 실측: 닫힘 신호를 보내면 서버가 약 25초 안에 종료됐다. 단, Claude 앱 내장 브라우저 창은 탭을 없앨 때 이 신호를 보내지 않아 그 경로는 3분 예비 종료에 의존한다. 일반 Chrome/Edge 탭 닫기는 사용자 확인 필요.
- **Part1 config의 Gemini 키·모델 사용**: Part3 AI는 키를 환경변수 → 이 PC에 저장한 키 → Part1 `config.txt`의 `GEMINI_API_KEY` 순서로 찾는다. 모델은 Part3에서 따로 정하지 않았으면 Part1 `GEMINI_MODEL`(현재 `gemini-3.5-flash-lite`)을 쓴다. Part1 config는 읽기만 하며 키 값을 화면·로그·API 응답에 내보내지 않는다(어디서 온 키인지만 표시). Ollama에는 키를 보내지 않는다. 수정본41의 Part1 config는 키 칸이 비어 있다.
- **연결 설정 기본값**: 공통 상위 폴더는 고정 경로 없이 `..`(Part3 옆에 Part1/Part2)로 잡는다. 창고는 Part2가 쓰는 설정(Part2의 `event_backtest.json` 또는 Part2 기본 규칙)을 Part2 코드에서 읽어 그대로 쓴다(규칙 복사 없음). 기본값 그대로 저장하면 빈 값/상대 경로로 저장되어 폴더를 옮겨도 따라간다. 실측: `..` → 수정본41, 창고 → 개피곤_warehouse. 시험 추가, Part3 **31 PASS**.
