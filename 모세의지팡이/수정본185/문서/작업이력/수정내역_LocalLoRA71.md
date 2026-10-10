# 수정본71 — Part3 local_lora provider

## 동작

AI 설정에서 Ollama 또는 로컬 LoRA를 선택·저장한다. local_lora는 ai_settings.json의 base_model/adapter_path/load_in_4bit를 실제 로딩에 사용하며 모델명과 학습 폴더 기본값을 만들지 않는다. timeout과 선택 max_new_tokens/local_files_only도 적용한다. 기존 모델 선택은 복사 당시 Ollama 설정을 유지했다.

모델은 최초 요청 때 별도 Python 프로세스에 로드하고 같은 설정의 대화에서 재사용한다. 다른 대화도 같은 모델 프로세스를 공유한다. 실행 도중 PyTorch 등 선택 패키지는 웹 서버에 import하지 않는다.

모델 전환은 현재 요청 완료·기존 모델 종료를 확인한 뒤 실행한다. Ollama /api/ps로 적재 모델을 조회하고 /api/generate keep_alive=0 해제 후 다시 조회한다. LoRA worker는 terminate/kill 뒤 실제 process.wait 완료를 확인한다. 같은 서버 요청은 순차 처리하며 수정본71의 다른 서버 창도 OS 잠금으로 동시 적재를 막는다. 자식 추론 프로세스도 별도 잠금을 잡고 부모를 감시해 강제 종료 시 잔류를 막는다.

Ollama 요청 실패·시간초과는 종료 확인 대기 상태로 남긴다. 목록이 잠깐 비어 있어도 해당 모델의 명시 해제와 확인이 끝나기 전 다음 모델을 로드하지 않는다. 설정 변경은 진행 중 응답이 이전 설정의 해석 결과로 돌아오는 것을 차단한다.

파일/패키지 누락, 호환 실패, 4비트 GPU 부재, 메모리 부족, 시간초과는 오류를 반환하고 웹 서버를 유지한다. timeout은 대기·첫 로딩·추론에 적용하며 시간초과 worker는 종료한다. local_files_only 기본 true로 서버 요청 중 자동 다운로드를 피한다.

원래 출력 schema와 읽기 전용 도구 계약을 provider에 전달하고 JSON 출력 제한과 최종 JSONSchema 검증을 적용한다. 도구 응답은 기존 Agent가 받는 content/tool_calls 형태로 변환하며 Agent/tool loop를 수정하지 않는다. decoder 호환 복사본의 enum 표현만 바꾸고 원본 schema로 최종 검증한다. 조건 목록에 숨은 20개 제한을 두지 않는다.

## 변경 파일

- Part3/lab/ai/provider.py: provider 선택과 설정 필드 읽기. 기존 OpenAICompatible 추론 클래스는 유지.
- Part3/lab/ai/local_lora.py: 설정·상대 경로 검증과 지연 추론 프로세스 연결.
- Part3/lab/ai/lora_worker.py: 모델·PEFT LoRA 최초 로드, 재사용, JSON 출력, 부모 종료 감시.
- Part3/lab/ai/model_runtime.py: 배타 잠금, Ollama 해제 확인, worker 종료, 오래된 요청 차단.
- Part3/lab/server.py, unified_settings.py: 두 provider 설정 조회·저장과 대화 초기화.
- Part3/web/unified.js, ai_chat.js, style.css: provider/LoRA 설정과 표시.
- Part3/requirements-local-lora.txt: 선택 의존성. 기존 통합설치 필수 의존성은 유지.
- Part3/docs/LOCAL_LORA.md: 설치·설정·이관·병합 GGUF 배포 안내.
- verification/test_local_lora71.py: 새 회귀 60건. 기존 관련 4개 테스트는 /api/ps 및 해제 모의 응답을 확장하고 원래 chat assertion은 유지.
- verification/check_local_lora_decoder71.py, ui71_preview.cjs: 선택 JSON/UI 검사.
- verify_current_manifest.json, CURRENT_VERIFY.md: 새 공식 검사 등록과 결과.

Part1/Part2, Agent, tool loop, output schema, intent, Recipe v2, validator, execution_plan, compiler는 변경하지 않았다. 원본 수정본70과 이전 검증결과 폴더도 변경하지 않았다.

## 사용

프로그램의 설정 → 전략 연구 AI → AI 실행 방식을 로컬 LoRA로 선택한다. 학습한 기본 모델과 프로젝트 루트 기준 LoRA 폴더를 입력·저장하고 전략 연구 대화에서 사용한다. 실행 Python에 선택 의존성을 설치하고 모델 파일을 먼저 준비해야 한다. 입력 예와 설치 명령은 Part3/docs/LOCAL_LORA.md에 있다.

다른 컴퓨터에 완성 모델만 배포하면 기본 모델과 LoRA를 병합한 GGUF를 Ollama에 등록하고 기존 Ollama provider에서 등록 모델을 선택한다. 이번 작업에서는 실가중치가 없어 병합/GGUF 산출물을 생성하지 않았다. 외부 프로그램과 이전 수정본의 별도 모델 요청까지 계속 통제하는 기능은 아니다.

## 검증

공식 검증 2300 PASS, FAIL 0, 기존 SKIP 739, 종료코드 0. 새 local_lora 회귀 60건 포함. 별도 실제 JSON 제한/검증 라이브러리 65건 PASS, 모의 API Edge 설정·전환·420px UI PASS. 실모델/GPU 추론은 미검증이다.

검증 증거는 이번 수정본의 검증결과/current_verify/latest.json, local_lora_actual_decoder71.json, local_lora_ui71_mobile.png, 수정본71_작업범위.json이다. 상세 진단과 제외 이유는 CURRENT_VERIFY.md에 있다.
