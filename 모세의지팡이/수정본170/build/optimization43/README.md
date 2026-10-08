# 수정본43 검증 도구

모든 테스트 입력은 합성 데이터입니다. 실제 MT5·Windows named pipe·SQL·시장 녹화 전체 백테스트를 검증한 자료가 아닙니다.

## 재실행

프로젝트 루트에서 실행합니다. 별도로 보관한 수정본42 프로젝트 폴더를 비교 입력으로 지정합니다.

```bash
python build/optimization43/run_checks.py
python build/optimization43/compare_input_failures.py --original "수정본42_폴더"
python build/optimization43/probe_integration.py
python build/optimization43/probe_worker.py
python build/optimization43/measure_paths.py --original "수정본42_폴더"
```

`run_checks.py`는 실패한 테스트를 한 번 재시도하고 결과를 `검증결과/계측_유효성최적화43/`에 기록합니다. 이전 실행 증거를 보존하려면 같은 도구를 다시 실행하기 전에 증거 폴더를 별도로 보관하십시오. 비교 입력은 정답 데이터가 아니라 변경 범위·기존 오류·구간 성능을 확인하기 위한 자료입니다.

## 도구의 경계

- `probe43_support.py`: 현재 ResultWriter 코드는 그대로 실행합니다. DuckDB가 없으면 검증용 모듈에서 import 문만 제외하며, SQL을 호출하면 명시적인 오류가 납니다. 운영 모듈 파일·DB 동작은 변경하지 않습니다.
- `probe_integration.py`: 직접 ingress, 재생 엔진, 인메모리 LIVE PipeReceiver·재생 adapter를 거쳐 유효성 판정·신호·B0·수신자·행 순서를 검사합니다. 테스트 전용 조건을 사용하며 기존 전략 전체를 대체하는 검사가 아닙니다.
- `probe_worker.py`: 실제 `run_chunk`, 정식 WATCH 명령·원비 터치, CSV 출력, 같은 워커 재사용 및 쓰기 제한을 검사합니다. 영구적인 쓰기·네트워크 가드 때문에 자식 프로세스에서만 실행합니다. 실패하면 한 번 재시도합니다.
- `measure_paths.py`: 입력 수정본의 실제 계측 래퍼와 `market.select`·OZ `ready` 함수 본문을 읽어 구간 시간만 측정합니다. 전략을 되돌리거나 운영 코드에 이전 로직을 설치하지 않습니다. 준비·디스크 읽기·SQL·전체 백테스트 비용은 제외됩니다.
- `measurements_attempt1.json`: OHLC 단독 조회에 불필요한 튜플 생성 비용이 있었던 첫 시도입니다. 이 시도의 구현은 배포하지 않습니다. `measurements.json`이 수정 후 최종 구현의 측정입니다.

프로젝트 의존성 파일은 변경하지 않았습니다. 이번 검증 환경은 Linux / Python 3.13.5 / NumPy 2.3.5 / pandas 2.2.3이며 DuckDB는 없었습니다. 프로젝트에 지정된 pandas 3.0.1 환경은 이번 실행 환경과 다릅니다. SQL 및 네이티브 Windows 검사는 해당 환경에서 별도 확인이 필요합니다.
