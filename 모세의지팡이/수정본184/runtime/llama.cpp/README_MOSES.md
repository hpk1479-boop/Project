# GGUF AI 실행기

llama.cpp b11323 공식 Windows Vulkan x64 배포본입니다. 원래 모델 파일과 프로젝트 상대 실행기 경로를 그대로 사용합니다.

- 원본: https://github.com/ggml-org/llama.cpp/releases/tag/b11323
- 패키지: llama-b11323-bin-win-vulkan-x64.zip
- ZIP SHA256: afe4d94a7c828b7e96bbfc4ba6c27a99752e98cf7754c9f8250aadfd144798bc
- 설정: `settings/ai_settings.json`의 `gguf_gpu_layers=-1`은 자동 GPU 배치, `0`은 CPU입니다.
- CPU backend도 함께 포함합니다. 별도의 `runtime/llama.cpp.cpu`는 개발 작업본의 보관 자료이며 새 설치 묶음에는 포함하지 않습니다.
- 본체 라이선스: `LICENSE` — [b11323 공식 원문](https://github.com/ggml-org/llama.cpp/blob/b11323/LICENSE). OpenMP 라이선스는 `LICENSE-LLVM-OpenMP`에 있습니다.

모델명과 경로는 설정값을 사용합니다. 실행기 교체가 모델의 전략 해석 정확도를 보장하지는 않습니다.
