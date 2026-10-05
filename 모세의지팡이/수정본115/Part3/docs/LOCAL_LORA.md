# Local LoRA 사용 및 다른 컴퓨터로 배포

`local_lora`는 설정한 기본 모델에 학습된 PEFT LoRA를 붙여 AI 대화를 실행하는 선택 기능입니다. 수정본75에서는 공통 AI 서비스가 WATCH 보조 해석·전략 연구·백테스트 자연어 명령에 같은 모델을 제공합니다. 기존 `ollama`도 계속 사용할 수 있습니다. 모델 이름이나 LoRA 경로에 기본값은 없습니다. 아래 이름은 설명용 예시이며 실제로 준비한 모델과 어댑터를 지정해야 합니다.

## 준비와 선택 설치

일반 통합 설치에는 LoRA 추론 패키지를 추가하지 않았습니다. Ollama만 사용하거나 병합된 GGUF를 Ollama로 실행할 때는 이 선택 설치가 필요하지 않습니다.

1. 학습에 사용한 기본 모델, 토크나이저, 학습된 LoRA를 준비합니다. 어댑터 폴더에는 `adapter_config.json`과 `adapter_model.safetensors` 또는 `adapter_model.bin`이 필요합니다.
2. 프로그램을 실행하는 Python 환경에 하드웨어에 맞는 PyTorch를 설치합니다. GPU 빌드는 [PyTorch 공식 설치 선택기](https://pytorch.org/get-started/locally/)를 사용합니다.
3. 프로젝트 루트에서 같은 Python 환경으로 아래 명령을 실행합니다.

```powershell
python -m pip install -r Part3/requirements-local-lora.txt
```

통합 설치가 만든 환경을 사용하는 경우 3번의 `python` 대신 `통합설치/.venv-moses/Scripts/python.exe`를 사용합니다. 임의의 다른 Python에 설치하면 프로그램이 패키지를 찾지 못할 수 있습니다.

이 패키지 목록은 선택 설치 목록입니다. 실제 GPU·드라이버·기본 모델·어댑터 조합에서 로딩이 검증됐다는 뜻은 아닙니다. 이 provider의 `load_in_4bit=true` 경로에는 CUDA GPU와 호환되는 bitsandbytes 실행 환경이 필요합니다. CPU로 사용하려면 4비트를 끄고 기본 모델을 담을 메모리를 준비합니다. 패키지의 하드웨어 지원 범위는 [공식 양자화 안내](https://huggingface.co/docs/transformers/en/quantization/bitsandbytes)를 확인합니다.

## AI 설정

설정 위치는 프로젝트 루트 기준 `settings/ai_settings.json`입니다. 공통 AI 설정 화면에서 접혀 있는 **상세 설정 · 로컬 LoRA**를 펼치고 **로컬 LoRA 사용**을 켭니다. 기본 모델과 어댑터 경로, 4비트 사용 여부, 제한 시간을 입력·저장합니다. 기본 선택 목록에는 Ollama·로컬 GGUF·Gemini만 표시합니다. LoRA 사용을 끄면 기본 목록을 다시 선택할 수 있으며 기존 LoRA 저장값은 유지합니다. 상세 설정은 화면을 다시 열거나 저장한 뒤에는 접힙니다. 아래 예시는 값을 설명하기 위한 것이며 배포된 모델 파일을 의미하지 않습니다.

```json
{
  "provider": "local_lora",
  "base_model": "Qwen/Qwen3.5-4B",
  "adapter_path": "models/MOSES_Qwen35_4B_LoRA",
  "load_in_4bit": true,
  "local_files_only": true,
  "timeout": 90
}
```

- `base_model`: Hugging Face 모델 ID 또는 프로젝트 안 기본 모델 폴더의 상대 경로입니다. 로컬 폴더는 `./models/base_model`처럼 명시합니다. 어댑터를 학습한 모델 구조·버전과 일치해야 합니다.
- `adapter_path`: 프로젝트 루트 기준 상대 경로입니다. 드라이브 문자나 프로젝트 밖으로 나가는 `../` 경로를 저장하지 않습니다.
- `load_in_4bit`: `true`면 4비트 양자화로 기본 모델을 읽습니다. `false`면 4비트 패키지에 의존하지 않는 로딩 경로를 사용합니다.
- `local_files_only`: `true`면 모델 ID도 사전에 준비한 로컬 캐시에서만 읽습니다. 생략 시에도 `true`이며 요청 중 자동 다운로드하지 않습니다. 다운로드를 허용하려면 사용자가 명시적으로 `false`를 설정해야 합니다.
- `timeout`: 요청의 제한 시간(초)입니다. 첫 로딩은 이후 요청보다 오래 걸릴 수 있습니다.
- `max_new_tokens`: 선택 항목입니다. 지정하면 이번 응답의 최대 생성 토큰 수로 사용합니다. 값을 지우면 실행 제한값인 4096을 사용합니다. 긴 응답이 도중에 잘리면 이 제한과 메모리 여유를 함께 확인합니다.

프로젝트 전체를 다른 위치로 옮길 때는 위 상대 경로의 모델 폴더도 함께 옮깁니다. Hub ID만 지정하면 다른 컴퓨터의 캐시까지 함께 옮겨지는 것은 아닙니다. 이관할 때는 프로젝트 안의 기본 모델 폴더를 준비하고 상대 경로로 지정하는 방법이 편리합니다.

같은 설정으로 이어서 대화하면 처음 로드한 모델을 재사용합니다. 모델이나 제공자가 바뀌면 이전 모델을 해제한 뒤 다음 모델을 사용합니다. 수정본71의 여러 MOSES 창에서도 AI 모델의 배타 잠금을 사용하므로 다른 창이 모델을 사용 중이면 중복 사용 오류를 표시합니다. 같은 창의 요청은 제한 시간 안에서 순서대로 처리합니다. 다른 창을 강제로 종료해도 별도 모델 실행 프로세스는 부모 종료를 감지해 종료합니다. 이 정책은 이 프로그램이 관리하는 요청에 적용됩니다. 외부 프로그램이 별도로 새 모델을 올리는 것까지 영구적으로 통제하지는 못합니다.

LoRA 파일 누락, 필수 패키지 누락, 모델 구조 불일치, 메모리 부족, 제한 시간 초과는 오류로 표시됩니다. 자동으로 다른 모델에 대화를 보내거나 전략을 적용하지 않습니다. 기존 해석 확인·schema 검증·Recipe 적용 절차를 그대로 거칩니다.

## 모델 호환성

일반 텍스트 생성 모델과 비전 구조가 포함된 모델은 같은 로딩 클래스로 취급하지 않습니다. 모델 설정의 구조를 보고 지원되는 Transformers 로딩 경로를 사용합니다. 예시인 [Qwen3.5-4B 공식 모델](https://huggingface.co/Qwen/Qwen3.5-4B)은 비전 인코더가 포함되어 있으므로, 어댑터의 모듈 이름과 로드한 기본 모델의 구조가 맞아야 합니다.

출력은 기존 Part3 응답 schema를 전달해 JSON 형식을 제한하고, 반환 후 다시 검증합니다. 도구 호출은 provider 안에서 기존 Agent가 받는 형식으로 변환합니다. 형식 검증 통과가 자연어 의미의 정확성까지 보장하지는 않으므로 처음 사용하는 LoRA는 학습에 없던 전략 문장으로 확인해야 합니다. [Transformers 도구 호출 계약](https://huggingface.co/docs/transformers/main/en/chat_extras), [JSON 출력 제한 라이브러리](https://github.com/noamgat/lm-format-enforcer)

## GGUF로 다른 컴퓨터에 배포

완성된 모델을 다른 컴퓨터에서 사용하기만 한다면 기본 모델과 LoRA를 병합한 GGUF를 기존 Ollama에 등록하는 방식이 가능합니다. 이때 배포 컴퓨터의 제공자는 `ollama`입니다. `local_lora`용 Python 추론 패키지는 필요하지 않습니다.

1. **학습한 기본 모델 + LoRA를 병합**하여 완전한 모델 가중치를 저장합니다. PEFT의 `merge_and_unload()`가 병합 API입니다. 모델에 따라 병합 가능한 로딩 정밀도와 필요한 메모리를 먼저 확인합니다. [PEFT 병합 안내](https://huggingface.co/docs/peft/main/en/conceptual_guides/lora)
2. 모델 구조를 지원하는 llama.cpp 변환기로 병합 모델을 GGUF로 변환합니다. 필요한 경우 GGUF 양자화 도구로 용량을 줄입니다. 토크나이저와 대화 템플릿도 해당 모델에 맞게 보존합니다. [llama.cpp 공식 프로젝트](https://github.com/ggml-org/llama.cpp)
3. 배포 폴더에 GGUF와 `Modelfile`을 함께 둡니다. 예시 구조:

```text
models/배포모델/
  model.gguf
  Modelfile
```

`Modelfile`의 상대 경로 예시:

```text
FROM ./model.gguf
```

4. 다른 컴퓨터에 Ollama를 설치하고, 해당 배포 폴더에서 모델을 등록합니다. 아래 이름은 사용자가 정하는 예시 등록명입니다.

```powershell
ollama create moses-lora -f Modelfile
```

5. Part3의 AI 설정에서 Ollama를 선택하고 등록한 모델을 선택·저장합니다. JSON 예시는 다음과 같습니다.

```json
{
  "provider": "ollama",
  "model": "moses-lora",
  "timeout": 90
}
```

Ollama는 GGUF를 가져올 때 양자화를 수행하지 않습니다. 여러 조각의 GGUF라면 모든 조각을 복사하고 공식 안내에 맞게 등록합니다. [Ollama GGUF 가져오기](https://docs.ollama.com/import)

병합 전·병합 후·양자화 후에 같은 전략 문장을 넣어 해석 결과와 도구 호출을 확인합니다. 다른 컴퓨터에서도 해석 수정, 사용자 재확인, 적용 흐름을 확인한 뒤 사용합니다. 이 문서는 준비 절차이며, 실제 LoRA 학습·가중치 병합·GGUF 생성 또는 실모델 성능 검증을 완료했다는 보고가 아닙니다.
