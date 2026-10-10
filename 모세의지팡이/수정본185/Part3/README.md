> 수정본48: OZ는 올존 / 무지성 올존 / 브레이커 올존 / 무지성 브레이커 올존입니다. 아래 기존 생성물은 자동으로 전략을 바꾸지 않고 보존합니다. 특히 Test_SPECIAL012·014의 제거된 프로필은 재열기/적용 시 오류로 차단하며, 현재 SPECIAL5·7을 명시적으로 불러와 새 번호로 생성해야 합니다. OZ 트리거 명칭과 내부값은 BREAKER로 통일하며 구명칭의 별칭 변환은 지원하지 않습니다.

# PART 3 전략연구소

수정본95의 Part3 실행 코드입니다. 현재 설치·이관 안내는 프로젝트 루트의 `배포안내95.txt`, 통합 화면 안내는 `README_MOSES_DESKTOP.md`를 먼저 확인하세요. 전략 설정을 Python 코드로 만들고 기존 PART2에서 검토하기 위한 로컬 프로그램입니다. 기존 PART1/PART2와 SPECIAL1~7 파일을 덮어쓰지 않습니다.

## 바로 실행

Windows에서는 프로젝트 루트의 **`START_MOSES.pyw`**를 실행하세요. 라이브 감시·백테스트·전략 생성이 같은 웹 화면에 표시됩니다. 기본 브라우저에서 열려면 `START_PART3.bat` 또는 `START_PART3.pyw`를 사용합니다. 포트가 사용 중이면 다른 빈 포트를 사용합니다.

명령행에서도 실행할 수 있습니다.

```console
python run.py --idle-exit 180
```

필수 환경은 프로젝트 루트의 `통합설치.bat`로 준비합니다. 설치 도구의 대상은 Windows x64와 Python 3.12·3.13이며, 전용 창과 JSON 검증에는 추가 패키지가 필요합니다. AI는 Gemini / Ollama / GGUF / 사용 안 함을 선택합니다. 기본 Gemini에는 모델과 키를 직접 설정해야 합니다. AI 설정 없이도 기존 프리셋 조회·불러오기·표 편집은 가능하며, 자연어 해석을 실행할 때 선택한 AI의 필수 설정을 검사합니다.

전용 창은 라이브 엔진 종료를 확인한 뒤 닫힙니다. 브라우저 실행은 마지막 탭을 닫은 뒤 약 25초, 브라우저 강제 종료는 마지막 접속부터 3분 뒤 라이브 엔진과 서버를 정리합니다. 명령행에서 `--idle-exit`를 생략하거나 0으로 지정하면 **Ctrl+C**로 종료합니다. `web/index.html`을 직접 더블클릭하지 말고 실행 파일을 사용하세요. Part1·Part2 개별 Tk GUI는 제거했으며 엔진 독립 실행 방법은 상위 `README_MOSES_DESKTOP.md`에 있습니다.

## 기존 프로젝트와 배치

```text
Trading/
├─ Part1/                  ← 기존 파일 유지
├─ Part2/                  ← 기존 파일 유지
└─ Part3/                  ← 이 압축파일에서 꺼낸 폴더
   ├─ START_PART3.bat
   └─ run.py
```

이 배치에서는 상위 폴더 `..`를 자동 인식합니다. 다른 위치에 두었으면 화면의 **연결 설정**에서 Part1과 Part2가 함께 들어 있는 폴더를 지정하세요. Part1 폴더 자체가 아니라 그 위의 공통 폴더입니다. 기존 실행용 Python 경로도 지정할 수 있습니다.

현재 Part3는 공통 상위 폴더의 실제 Part1을 기준으로 전략을 생성합니다. Part1이 없으면 과거 참조본으로 대체하지 않습니다. PART2 실제 백테스트에는 사용자의 데이터 환경이 필요합니다. 포함된 `reference/`는 과거 자료이며 최신 계약이 아닙니다.

## 화면 사용 순서

자연어 아이디어 입력 → AI 구조화 해석 확인 → 적용 → Recipe v2 검증·코드 미리보기 → 사용자 확인 후 전략 파일 생성.

수동 카드·슬롯 생성기와 수동 Python 입력은 제거했습니다. Part3는 JSON 검증과 코드 생성만 담당하며 원문을 다시 해석하지 않습니다. 조건/분기는 길이 제한 없는 목록입니다. 기존 생성 파일은 보기만 유지합니다. 사용법은 docs/USER_GUIDE.md, 현재 AI 계약은 docs/AI_INTENT_SCHEMA.md를 확인하세요.

## SPECIAL 1~7 기본 생성 파일 포함

`generated/`에 실제 생성기로 만든 기본 설정 파일 7개를 함께 넣었습니다. 손익 합격 예제가 아니라 **원본 로직을 새 namespace로 복제한 검토용 시작점**입니다.

| 생성 파일 | 기반 원본 |
|---|---|
| Test_SPECIAL008.py | SPECIAL1 · 내부유동성 |
| Test_SPECIAL009.py | SPECIAL2 · 외부유동성 스윕 |
| Test_SPECIAL010.py | SPECIAL3 · 장초반 추세 눌림 (이전 원본: 최종 OPENING 필터) |
| Test_SPECIAL015.py | SPECIAL3 · 장초반 추세 눌림 (현재 원본: OPENING 제거, 최종 알림 09-11/16-18/21-24) |
| Test_SPECIAL011.py | SPECIAL4 · 30분 찢들 |
| Test_SPECIAL012.py | SPECIAL5 · 프렉탈 MS 셋업 |
| Test_SPECIAL013.py | SPECIAL6 · 추세 FVG 눌림 |
| Test_SPECIAL014.py | 구버전 SPECIAL7 저장 자료 · 로드 시 지원 여부 확인 |

다음 생성 번호는 현재 `generated/`와 연결된 프로젝트의 점유 번호를 읽어 결정합니다. 빈 번호를 찾아 재활용하거나 기존 파일을 덮지 않습니다.

## 저장과 독립성

- `generated/`: 새 Test Python 파일과 AI Recipe 스냅샷입니다.
- `projects/`: 설정 저장본, 마지막 편집 초안, Part1/2 연결 경로입니다.
- `logs/`: 사용자가 시작한 백테스트의 요청과 실행 로그입니다.
- `reference/`: 과거 SPECIAL·공용 엔진의 보존 사본입니다. 실행 기준으로 사용하지 않습니다.

**생성된 Python 파일은 PART3를 import하지 않습니다.** `register(manager)`와 전략 로직이 한 파일에 들어 있습니다. 지표·OZ·시세·감시 API는 원래 SPECIAL과 마찬가지로 기존 PART1 엔진에서 제공받습니다. 이 파일을 단독 실행하는 시세 프로그램이나 주문 실행기로 해석하지 마세요.

PART3는 Test 접두사를 제거하거나 실전 SPECIAL로 승격하는 기능을 제공하지 않습니다. 채택 여부와 파일명 변경은 사용자가 직접 결정합니다. PART2에서 생성 파일을 연결하는 경로는 PART3가 시작하는 별도 프로세스이며, PART1/PART2의 원본 Python 파일은 수정하지 않습니다.

## GPT에게 Part3만 전달

**`GPT_START_HERE.md`**가 첫 문서입니다. Part3 전체를 압축해 첨부하고 다음처럼 요청하세요.

> 이 Part3가 현재 기준본입니다. GPT_START_HERE.md와 docs/GPT_HANDOFF.md를 읽고 작업하세요. 기존 SPECIAL 원본과 PART1/PART2를 바꾸지 말고, 제가 설명하는 전략만 새로운 Test_SPECIALXXX.py로 만들어 주세요. 기준 전략은 SPECIAL○이고, 변경할 조건은 …입니다. 나머지 조건과 판정 시점은 유지해 주세요.

사용 이력이 담긴 `projects/connections.json`, `logs/`, 최근 코드/메모에는 로컬 경로 또는 사용자가 입력한 내용이 들어갈 수 있습니다. 외부에 전달하기 전에 확인하세요. 배포본에는 계좌 설정 파일, API 키, 시세 창고를 넣지 않았습니다.

## 문서 안내

- `docs/USER_GUIDE.md`: 버튼, TF, 저장, 직접 코드 편집, 오류 대처
- `docs/ARCHITECTURE.md`: 생성 과정과 각 파일의 책임
- `docs/STRATEGY_SCHEMA.md`: 설정 JSON 전체 계약
- `docs/SPECIAL_1_TO_7.md`: 실제 원본 조건과 시간/취소 동작
- `docs/GPT_HANDOFF.md`: 복잡한 신규 전략 작성 절차 및 요청 양식
- `docs/PART1_PART2_API.md`: 등록 인터페이스와 백테스트 연결
- `docs/TEST_REPORT.md`: 수행한 점검과 실제 시세 재생이 필요한 범위

생성 문법·기본 입력 계약을 확인하는 작은 점검만 수행합니다. 자동 수익성 평가, 합격 판정, 전략 승격, 별도 대형 검증 시스템은 포함하지 않습니다.
