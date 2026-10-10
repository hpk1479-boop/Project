# Part3 AI 전략 생성 사용법

사용자 = 자연어로 전략 아이디어 / AI = 자연어 해석 / Part3 = JSON 검증 + 코드 생성.

새 설치와 기존 상태 이관은 프로젝트 루트의 `배포안내95.txt`를 먼저 확인하세요. AI 기본 방식은 Gemini이며 모델·키는 직접 지정합니다. AI 없이 시작하려면 **불러오기**에서 기존 프리셋을 선택해 표를 편집할 수 있습니다.

## 사용 순서

1. 통합 화면의 전략 생성에서 자연어 아이디어를 입력합니다.
2. AI의 구조화된 해석에서 조건, 방향, TF, 사건 순서, 유효시간, 최종 OZ를 확인합니다.
3. 해석이 맞으면 적용을 누릅니다. Part3가 Recipe v2를 검증하고 코드 미리보기를 만듭니다. 이 시점에는 파일을 생성하지 않습니다.
4. 전략 파일 생성을 누르면 기존 번호와 충돌하지 않는 generated/Test_SPECIALXXX.py 및 설정 JSON을 저장합니다.
5. 현재 해석은 표에서 직접 수정할 수 있습니다. 표의 **확인 후 전략 생성** 또는 **확인 후 백테스트 실행**은 전체 조건을 검증한 뒤 진행합니다. 이전 Recipe v1 생성물을 조용히 덮어쓰거나 재생성하지 않습니다.
6. **초기화**는 대화와 해석 초안을 비우며 실행 중 작업·생성 파일·모델 캐시는 유지합니다. **사용 안 함** 저장은 새 추론을 막고 해당 런타임이 소유한 모델을 해제합니다.

조건 수·분기 수의 슬롯 제한은 없습니다. 현재 지원하는 의미·필드는 docs/AI_INTENT_SCHEMA.md를 확인합니다. 지원되는 TF·종목·지표 계약은 현재 Part1/Part2 코드에서 읽습니다.

Part3는 자연어를 다시 판정하지 않습니다. AI가 반환한 JSON에 지원하지 않는 값이나 코드/경로 같은 금지 필드가 있으면 적용을 거부합니다. 의미가 맞는지는 해석 결과에서 확인합니다.

## CLI

검증 가능한 AI Recipe v2 JSON을 사용합니다.

    python cli.py preview --recipe projects/strategy.json
    python cli.py generate --recipe projects/strategy.json
    python cli.py list

수동 SPECIAL 로드와 --code 입력은 제거했습니다. CLI도 웹과 같은 validator/compiler/storage 경로를 사용합니다.

## 범위

Part3는 시장 지표를 계산하지 않습니다. 생성된 전략은 현재 Part1의 Fact·Processor·Consumer 계약을 사용합니다. AI에는 조회 도구만 제공하며 파일 생성은 신뢰된 Part3 storage.generate만 수행합니다. 실전 적용과 백테스트 실행은 별도 사용자 행동입니다.
