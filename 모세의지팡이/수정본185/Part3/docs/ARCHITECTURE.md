# 구조와 데이터 흐름

## 생성 경로

```text
웹 UI / CLI
  → Recipe(JSON 호환 설정)
  → catalog.validate : 필수값·현재 입력 계약만 확인
  → compiler.compile_recipe
      1. 읽어 둔 원본 source_text 선택
      2. 설정된 상수/함수만 편집
      3. 신규 spec/watch/state namespace 부여
      4. PART3_RECIPE와 생성 메타데이터 삽입
      5. Python 문법 compile (실행 없음)
  → storage.generate
      번호 확인 → open('x') → Test_SPECIALXXX.py
      같은 번호의 .recipe.json
```

화면에 보이는 코드와 저장 코드에 서로 다른 템플릿을 사용하지 않습니다. `/api/preview`와 `/api/generate`는 동일한 컴파일러를 호출합니다. 저장 시 번호가 바뀌면 그 번호로 다시 생성합니다.

## 파일별 책임

| 파일 | 책임 |
|---|---|
| `run.py` | 로컬 서버, 포트, 브라우저 열기 |
| `lab/server.py` | 로컬 HTTP, 세션 토큰, API 라우팅, 문서 제공 |
| `lab/catalog.py` | 원본 선택, 상수 추출, 7개 전략의 슬롯 역할, 입력 스키마 |
| `lab/source_edit.py` | AST 위치를 이용한 최소 텍스트 편집, 안전한 단순 상수 읽기 |
| `lab/compiler.py` | Recipe → 실행 가능한 전략 본문, namespace 분리 |
| `lab/storage.py` | 번호 예약, 새 파일 저장, 최근 작업, 재열기, 연결 설정 |
| `lab/integration.py` | 사용자가 요청한 PART1/PART2 실행 및 로그 추적 |
| `backtest.py` | 별도 프로세스 안에서 Test 플러그인 로더를 기존 이벤트 엔진에 연결 |
| `cli.py` | UI 없는 GPT/개발자 작업 경로 |
| `web/app.js` | Recipe를 유일한 화면 상태로 관리, 대화상자/미리보기/실행 |
| `web/style.css` | 3열 화면, 2×2 진입 카드, 반응형 스크롤 |

## 원본 보존형 생성

원본을 `ast.unparse`로 통째로 다시 출력하지 않습니다. AST는 바꿀 상수나 함수의 위치를 찾는 데 쓰고, 해당 UTF-8 바이트 범위만 교체합니다. 한글이 포함된 줄에서 AST 열 번호가 문자 수가 아닌 바이트 수인 점도 처리합니다. 따라서 원본의 긴 설명 주석과 수정하지 않은 코드 흐름이 남습니다.

기본 SPECIAL1/2/6/7의 슬롯/연결 설정을 바꾸지 않으면 원본 등록 함수를 유지합니다. 편집하면 실제 `StrategySpec` 사전으로 해당 정의만 다시 만듭니다. SPECIAL3은 시간 연결 사전의 값과 상수를 변경하며 기존 연결 runtime을 그대로 사용합니다. SPECIAL4/5는 원래 callback/runtime을 포함한 전체 본문을 가지고, 바뀐 옵션에 해당하는 구간만 변경합니다.

이 방식은 알려진 현재 원본 구조를 기준으로 합니다. 나중에 원본의 함수 이름·스키마가 바뀌었는데 단순 상수 변경처럼 처리할 수 없다면 자동으로 낡은 구조를 복원하지 않고 오류/원본 코드를 확인해야 합니다. GPT가 대상 원본과 `compiler.py`의 해당 branch를 함께 수정하는 경로를 문서화했습니다.

## 기본값 비교와 설정 고정

Recipe의 `_loaded_slots`, `_loaded_options`, `_loaded_name`은 처음 읽은 설정의 JSON 정규화 복사본입니다. 이를 현재 입력과 비교해 수정하지 않은 경로를 보존합니다. 생성된 파일의 메타데이터에서는 내부 비교용 키를 제외합니다. sidecar에는 그대로 남아 최근 작업을 다시 편집할 수 있습니다.

원본 `special_final_profile()`의 외부 프로필 덮어쓰기는 생성 파일에서 제거합니다. 실제 원본의 현재 설정을 불러올 때 적용한 프로필을 **생성 시 화면 값으로 고정**하는 것입니다. 생성된 파일이 원본 SPECIAL1~7용 환경 설정 때문에 나중에 다른 프로필로 바뀌는 것을 막습니다.

## 독립 namespace

파일명만 새로 만들고 `PIPELINE_1`을 남겨 두면 원본과 동시 등록할 때 충돌합니다. `_namespace()`는 다음을 분리합니다.

```text
PIPELINE_4                 → PIPELINE_TEST_SPECIAL011
OZARM:SP4:                 → OZARM:TEST_SPECIAL011:
special4_state.json        → test_special011_state.json
Special4Runtime            → TestSPECIAL011Runtime
```

기존 내부 함수명 `_main_4...`가 남는 것은 모듈 내부 이름이며 등록 충돌과 다릅니다. 신호/감시/상태 식별자가 외부 공유 영역에서 독립이어야 합니다. Test_를 수동으로 뗀 파일명 때문에 내부 ID를 반드시 옛 원본 번호로 되돌릴 필요는 없습니다.

## AI 의미와 Python 생성

조건은 canonical intent의 steps/branches/after_conditions/final_conditions/cancel_conditions로 전달한다. Part3가 원문을 정규식으로 재판정하거나, 빈 슬롯을 채우거나, 정해진 카드 수로 조건을 접지 않는다.

catalog는 현재 계약과 원본 SPECIAL의 읽기 전용 정보만 제공한다. compiler는 Recipe v2만 받는다. SPECIAL4/5의 명시적 고유 수명 요청은 special_templates가 이름 붙은 인자로 현재 소스를 생성한다. 수동 생성기·Recipe v1 생성 경로와 수동 코드 입력은 없다. 과거 생성 파일은 읽기 전용으로 조회한다.

## 데이터, 실행, 보안 범위

GUI는 `127.0.0.1`에만 바인딩됩니다. 외부 CDN, GPT 호출, 원격 사용자 접속을 제공하지 않습니다. API는 매 실행 만든 토큰과 Origin을 확인합니다. 외부 Python 본문은 미리보기 시 실행하지 않습니다. 실제 백테스트를 시작하면 사용자가 선택한 플러그인이 Python으로 실행됩니다.

서버가 저장하는 위치는 Part3의 generated/projects/logs입니다. PART2 구축 실행은 사용자가 지정한 별도 창고에 실제 원본 workflow가 기록합니다. 실행 요청을 프로그램 시작/전략 생성과 분리했습니다.

소규모 `tests/`는 개발자가 요청할 때만 실행합니다. UI에 재현 확인 배지나 자동 합격 기준을 표시하지 않습니다.
