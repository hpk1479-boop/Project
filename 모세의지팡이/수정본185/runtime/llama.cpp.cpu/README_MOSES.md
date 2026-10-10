# MOSES GGUF 실행기

공식 llama.cpp Windows x64 CPU 빌드 b11323을 그대로 준비했다. `llama-server.exe`와 동봉 DLL을 함께 유지한다. 모델 파일은 AI 설정에서 선택한다. 수정본75부터 실행기는 프로젝트의 runtime/llama.cpp/llama-server.exe를 자동으로 사용한다. 다른 실행기로 바꿀 때만 접힌 상세 설정에서 프로젝트 상대 경로를 지정한다.

- 배포 파일: [llama-b11323-bin-win-cpu-x64.zip](https://github.com/ggml-org/llama.cpp/releases/download/b11323/llama-b11323-bin-win-cpu-x64.zip)
- ZIP SHA256 확인값: `bc984e5e0f0337f89c2364cbc24274c31a4ba83f9e8ceb497abb85b8d5046a95`
- 공식 안내: [llama.cpp server](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md)
- 동봉 실행기는 CPU용이며 GPU 사용은 별도 GPU 지원 빌드가 필요하다.

파일 다운로드와 해시 확인은 이번 수정본73에서 수행했다. 배포 경로와 머신 경로를 코드에 고정하지 않았다. 자세한 설정은 프로젝트의 `Part3/docs/LOCAL_GGUF.md`를 참고한다.
