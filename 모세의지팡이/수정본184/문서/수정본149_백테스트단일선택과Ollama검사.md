# 수정본149: 백테스트 전략 단일 선택과 Ollama 검사

수정본148을 별도 폴더로 복사해 작업했다. 이전 검증결과와 캐시는 복사하지 않았다.

## 사용자 요청과 동작

- Part2 백테스트 SPECIAL 전략 설정에서 다른 전략을 체크하면 기존 전략을 자동으로 해제하고 마지막에 체크한 하나만 남긴다. 알림 모드와 가상진입 모드에 같은 규칙을 적용한다.
- Part1 전략 설정의 복수 체크·전체선택·전체해제·저장·취소는 유지한다.
- Ollama 실행 모델 검사는 Ollama를 선택해 실행할 때만 한다. GGUF·Gemini 요청은 사용하지 않는 Ollama의 연결 상태나 실행 모델을 확인하지 않는다.
- 이전에 MOSES가 실제로 요청한 Ollama 모델은 실행 방식 전환·종료 때 그 모델만 정리한다. 다른 프로그램의 모델은 유지하며, 소유 모델 종료가 확인되지 않으면 소유 상태와 잠금을 유지해 다시 시도할 수 있다.

## 변경한 흐름

- `Part3/web/unified.js`: 공유 전략 팝업의 단일 선택 조건을 모든 백테스트에 적용한다. 적용된 Part2 카드의 변경·이전 다중 저장값 복원·요청 읽기에서도 하나만 유지한다. 예전 저장값은 체크 순서 기록이 없으므로 목록의 마지막 체크 하나를 남기며, 이후 클릭은 실제로 마지막에 체크한 전략을 우선한다.
- `common_ai/model_runtime.py`: GGUF·Gemini 요청의 전체 Ollama 검사·해제를 제거하고 기존 소유 모델 종료 함수를 재사용한다. 이전 요청 종료 확인도 소유한 모델만 대상으로 한다. 소유 모델이 없으면 이 함수는 Ollama 통신 없이 반환한다.
- `Part3/docs/LOCAL_GGUF.md`: 변경된 실행 방식별 검사와 소유 모델 종료 규칙을 반영한다.

Part1·Part2 계산 코드, 레시피 파일과 저장 설정은 변경하지 않았다. 전략 조건·시간봉·진입·손절 계산도 그대로다. 내부 백테스트 API의 기존 복수 전략 처리 계약은 변경하지 않았다.

## 검증

신규 회귀는 `tests/test_strategy_single149.py/.cjs`와 `tests/test_ollama_selected149.py`에 둔다. 기존 가상진입 화면의 복수 선택 기대와 공통 AI의 다른 프로그램 모델 해제 기대는 이번 사용자 요청에 맞게 바꾼다. 실제 시장 데이터·실제 AI 모델 추론을 시험한 결과를 뜻하지 않는다.

검증 결과:

- 신규 Ollama 회귀 24개와 기존 소유 모델 종료 회귀 15개: 39개 통과. GGUF·Gemini 실제 provider 진입점을 사용하고 추론·통신만 모의로 대체했다.
- 공통 AI 실행·설정·모델 선택·보안과 기존 소유 모델 종료: 107개 통과. 위의 종료 회귀 15개를 포함한다.
- 기존 모델 전환·설정 변경 관련 현행 시험: 10개 통과.
- 실제 Edge: 신규 단일 선택·LIVE 유지·Ollama 목록 조회 6개, 기존 실제 HTTP 전략 관리 5개, 가상진입 화면 13개 검증 통과. 해당 pytest 3개 통과, 화면 오류와 외부 통신 0개.
- 변경 Python 4개와 JavaScript 3개 문법 검사 통과.
- Part1·Part2·settings의 코드·설정 파일 1814개를 비교해 변경 0개를 확인했다.

초기 시험은 임시 폴더의 부모 경로 준비 누락으로 일부 실행하지 못해 준비를 고친 뒤 재실행했다. 별도 과거 진단의 삭제된 `local_lora` provider 기대는 현재 선택 목록과 맞지 않아 실패했다. 이를 복원하지 않고 현재 지원 provider의 관련 시험을 다시 실행해 실패 0개를 확인했다. 초기 기록과 최종 성공 기록을 구분해 보존했다.

최종 증거:

- `검증결과/revision149/tests/provider_checks.txt`
- `검증결과/revision149/ollama_runtime149_regression.json`, `ollama_runtime149_selected.log`
- `검증결과/revision149/strategy_single/strategy_single149.json`
- `검증결과/revision149/strategy101/http_ui/http_ui_checks.json`
- `검증결과/revision149/cases/revision137/ui/frontend.json`
- `검증결과/revision149/tests/changed_files.json`, `syntax.json`
