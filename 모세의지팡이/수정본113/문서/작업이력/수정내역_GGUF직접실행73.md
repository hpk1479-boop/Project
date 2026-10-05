# 수정본73 — GGUF 직접 실행과 파일명 모델 선택

수정본72에서 새 폴더를 만들었다. 이전 검증결과·가상환경·Python 캐시는 복사하지 않았다. EXE 패키징과 라이선스 기능은 이번 작업에 포함하지 않았다.

## 추가 동작

- 기존 Ollama와 로컬 LoRA를 유지하면서 `local_gguf` 실행 방식을 추가했다.
- `models/gguf/`의 GGUF 파일을 자동 조회하고 파일명 그대로 선택 목록에 표시한다. 사용자의 마지막 지시에 따라 저사양/고사양과 모델 크기 분류를 제거했다.
- 사용자 다운로드 폴더의 `MOSES_Qwen35_4B_Q4_K_M.gguf`를 복사했다. 현재 Ollama 등록 모델과 파일 해시가 같으며 중복 복사본은 정리했다. 다른 모델 파일 검색은 사용자 요청에 따라 중단했다.
- 목록 조회와 저장은 가중치를 로드하지 않는다. 선택 모델 하나만 첫 AI 요청에 로드하고 같은 설정에서 재사용한다. 설정 변경은 기존 모델의 종료를 확인한 뒤 다음 요청에서 새 모델을 로드한다.
- MOSES가 공식 llama.cpp 실행기를 직접 관리한다. loopback 주소·실행마다 생성한 인증 키·준비 상태 확인·전체 요청 제한 시간·정확한 자식 프로세스 종료·Windows Job 보호를 사용한다.
- 기존 JSON 응답과 조회 도구 형식으로 반환한다. 잘못된 파일·로딩/추론 실패·시간 초과는 오류로 반환하고 재시도할 수 있다. 종료 실패 시 기존 모델 잠금은 유지하면서 요청 잠금은 풀어 종료 재시도를 가능하게 했다.

## 변경 파일

| 파일 | 역할 |
|---|---|
| `Part3/lab/ai/local_gguf.py` | 설정·상대 경로·파일 확인과 provider |
| `Part3/lab/ai/gguf_engine.py` | llama-server 준비·추론·JSON/도구 응답·소유 프로세스 종료 |
| `Part3/lab/ai/gguf_catalog.py` | GGUF 파일명 목록 조회. 가중치는 읽지 않고 헤더 24바이트만 확인 |
| `Part3/lab/ai/provider.py` | provider 선택과 설정 항목 확장 |
| `Part3/lab/ai/model_runtime.py` | 단일 모델 관리 경로에 GGUF 연결과 종료 실패 후 잠금 복구 |
| `Part3/lab/unified_settings.py` | 선택 모델·실행기·상세 설정 저장 |
| `Part3/lab/server.py` | 인증된 읽기 전용 `/api/ai/gguf-models` 추가 |
| `Part3/web/unified.js`, `ai_chat.js` | 파일명 선택·새로고침·상세 설정·GGUF 대기 안내 |
| `Part3/requirements-local-gguf.txt`, `통합설치/requirements-main.txt` | 가벼운 JSON 응답 검증 의존성 |

`runtime/llama.cpp/`에 공식 Windows x64 CPU 빌드와 라이선스를 준비했다. GPU 사용은 사용자가 GPU 지원 실행기를 지정하는 경우이며 동봉 CPU 빌드의 GPU 가속 검증을 주장하지 않는다.

## 변경 경계와 검증

Part1 313파일과 Part2 1723파일의 변경·추가·삭제는 없다. Agent/tool loop, canonical schema/intent, Recipe v2/validator/execution_plan/compiler, 기존 Ollama 추론 구현과 LoRA worker는 유지했다. 기존 선택 모델 설정도 자동 변경하지 않았다. 작업 범위 증거는 `검증결과/수정본73_작업범위.json`이다.

새 공식 대상은 `verification/test_gguf_engine73.py`, `test_local_gguf73.py`, `test_gguf_ui73.py`, `test_gguf_catalog73.py`이며 기존 공식 검사·제외 이유를 유지했다. 모델 전환 순서와 종료 실패 복구, 파일명 조회·저장·이동·빈 폴더·손상 파일·분할 파일·경로 탈출, 오류 후 UI 복귀를 검사한다.

실제 4B GGUF를 CPU로 실행하여 한국어 JSON 응답과 조회 도구 요청·결과 전달, 동일 프로세스 재사용과 종료를 확인했다. 실제 모델 결과: `검증결과/GGUF_provider_actual73.json`. 다른 모델 전환은 격리된 회귀로 검사했으며 실제 9B 추론을 했다는 뜻은 아니다. 자연어 전략 튜닝·실제 MT5 백테스트는 수행하지 않았다.

Edge 1440px/420px 설정 화면 7건도 확인했다. 모의 API를 사용한 UI 검사이며 실제 가중치 검사는 위 결과로 별도 기록했다. 화면 증거: `검증결과/GGUF_UI73/`.

최종 전체 검사와 제외 목록은 `CURRENT_VERIFY.md`와 `검증결과/current_verify/`를 확인한다. 사용 방법은 `Part3/docs/LOCAL_GGUF.md`, 파일을 추가할 위치는 `models/gguf/`이다.

최종 전체 검증: **2478 PASS / 0 FAIL / 739 SKIP**, 종료코드 0. 기존 제외 739건 유지, 추가 제외 없음. 결과: `검증결과/current_verify/20261002_042831_396667/summary.json`.
