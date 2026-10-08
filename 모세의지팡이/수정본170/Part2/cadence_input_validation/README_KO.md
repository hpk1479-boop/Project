# 봉마감 / 입력 공급 최소 수정 검증 도구

실행 코드는 `generic_backtest/conditional.py`와 `generic_backtest/history/cache.py` 두 파일만 바뀌었습니다. 이 디렉터리는 테스트, 재측정 도구와 증빙입니다. Part1 코드는 포함하지 않으며 프로그램에서 자동으로 호출되지 않습니다. 새 영구 feature cache도 만들지 않습니다.

최종 수치, 한계, 변경 파일 해시는 Part2 루트의 `CADENCE_INPUT_FIX_REPORT_KO.md`, `CADENCE_INPUT_FIX_MANIFEST.json`을 확인하십시오. 원래 있던 다른 최종 보고서·매니페스트는 이전 작업의 기록이며 수정하지 않았습니다.

## 설치 위치

압축 안의 `모세의지팡이 Part2` 폴더 내용물을 현재 **`BACKTEST CONTROL.pyw`가 있는 실제 Part2 실행 루트**에 병합합니다. 원본의 바깥쪽 동명 폴더에 한 단계 잘못 넣지 않도록 주의하십시오. 기존 `.venv-generic`, `.venv-history`, `generic_cache`, `generic_runs`는 삭제하거나 교체하지 않습니다. Part1 폴더에는 어떤 파일도 복사하지 않습니다.

## 테스트

기존 Part2 Python 환경에서 다음을 실행합니다. 테스트용 의존성만 별도로 설치할 경우 `python -m pip install -r cadence_input_validation/requirements-validation.txt`를 사용합니다. 실행용 `requirements-backtest.txt`는 원본 그대로입니다.

```text
python -m pytest cadence_input_validation conditional_validation -q
python -m pytest validation_suite conditional_validation cadence_input_validation -q
```

전체 원본 suite에는 원래 제공되지 않은 `backtest_specials/GENERIC_EXAMPLE_V1.py`를 참조하는 IPC 테스트 4건이 있습니다. 그 경로를 바꾸거나 예제 플러그인을 새로 만들어 원본 오류를 숨기지 않았습니다. 새 일반 플러그인 테스트는 별도의 임시 fixture로 알림·거래·취소·중복을 검증하고 끝나면 fixture 파일을 삭제합니다.

Part1 대조 테스트는 환경변수 `PART1_REFERENCE`에 로컬 Part1 루트를 지정할 때만 읽기 전용으로 실행합니다. Part1 코드가 없는 Part2 단독 배포에서는 해당 대조 테스트가 skip될 수 있습니다. GUI 테스트는 Tk 디스플레이가 필요합니다. Linux 가상 디스플레이에서는 `xvfb-run -a python -m pytest validation_suite/test_ui_dates.py conditional_validation/test_dashboard.py -q`로 실행할 수 있습니다. 이는 Windows 실환경 테스트의 대체 인증이 아닙니다.

## 원시 데이터 동일성

첨부 프로젝트에서 사용한 원본 raw archive는 다음 경로입니다. 이 배포본에는 수백 MB의 원본 cache를 중복 포함하지 않습니다.

```text
generic_cache/raw/da11d1a4d418384f57bf9eb763f2fc83a77112bf255e16956a7be345da2a7002
```

`verify_full_archive.py --help`로 인자를 확인한 후 기존 raw archive를 지정합니다. 이 검증은 전체 원시 입력의 필드·순서·바이트 일치를 검사하며, 전체 SPECIAL 백테스트 성능 측정과는 다릅니다.

## 전후 성능 재측정

제공된 단일 원본 archive가 `generic_cache/raw`에 있는 상태에서 실행합니다.

```text
python cadence_input_validation/prepare_data.py .
python cadence_input_validation/run_benchmarks.py --output cadence_input_validation/local_measurements
```

`prepare_data.py`는 원본 연속 tick을 값·순서·timestamp 변경 없이 자른 **검증용 raw subset**을 만듭니다. 지표나 전략 상태를 캐싱하지 않습니다. 내용이 없는 과거 chunk 파일만 양쪽 공통으로 합치며, 원래 gap 목록은 유지합니다. warmup 봉을 합성하거나 전략 warmup 요구량을 바꾸지 않습니다.

`run_benchmarks.py`는 먼저 관련 동일성 테스트를 실행하고, 각 측정을 다른 Python 프로세스로 순차 실행합니다. legacy/current 순서는 AB/BA/AB로 바꿉니다. 일반 측정은 profiler 없이 실행하며, 구간 타이머와 cProfile은 별도 실행입니다. 기존 disk feature cache는 양쪽 모두 비활성화합니다. OS 파일 캐시를 강제로 비우지는 않습니다.

원본 봉마감 차단 검증을 그대로 둔 상태에서는 전후 wall-clock을 비교할 수 없습니다. 따라서 `legacy`와 `current` 두 arm은 **동일한 봉마감 수정·상태머신·전략**을 쓰고, 입력 공급자만 바꿉니다. `reference_reader.py`가 원래 입력 공급자 구현을 보관합니다. 테스트 도구 밖에서 legacy 공급자를 선택하는 새 실행 옵션은 추가하지 않았습니다.

```text
python cadence_input_validation/benchmark.py --plugin BACKTEST_SPECIAL3 --reader legacy --rows 50000 --output before.json
python cadence_input_validation/benchmark.py --plugin BACKTEST_SPECIAL3 --reader current --rows 50000 --output after.json
```

각 run의 `wall_seconds`는 실제 `GenericRunCoordinator.run()` 진입부터 결과 기록 후 반환까지입니다. GUI 실행, 플랜 작성, Python 모듈 최초 import는 포함하지 않습니다. 별도의 프로세스 wall-clock은 증빙의 `process_status.json`에도 남아 있습니다.

## 각 도구의 역할

| 파일 | 역할 |
|---|---|
| `test_input.py` | float 비트·정수 timestamp·중복·예외 순서·미래 index 비공개·1H+5m cadence·실제 SPECIAL1 WONBI setup |
| `test_integration.py` | 실제 coordinator/격리 worker의 SPECIAL1~7 및 WATCH 3모드, 일반 알림/거래, PARTIAL/CANCELLED, 비어 있지 않은 OZ 상태 비교 |
| `reference_reader.py` | 최적화 전 공급자만 테스트용으로 보존 |
| `prepare_data.py` | 실제 연속 tick subset 준비 |
| `benchmark.py` | 전체 coordinator wall-clock 및 별도 구간/cProfile 측정 |
| `benchmark_reader.py` | 전체 대용량 raw 공급자만 측정; 전체 전략 속도로 해석 금지 |
| `verify_full_archive.py` | 실제 전체 raw 입력 lockstep 동일성 |
| `aggregate_results.py` | 전후 결과 hash/상태 비교, 중앙값·속도·호출 수 집계 |
| `run_benchmarks.py` | 순차 재측정 실행 도구 |

## 해석 시 주의

짧은 SPECIAL 입력에서 최종 알림이 0건인 결과를 양성 신호 검증으로 표시하지 않았습니다. 합성 fixture의 양성 WATCH/일반 거래/OZ 결과와 실제 tick의 WATCH 봉마감 알림은 구분하여 기록합니다. 세 모드 **사이의** 이벤트가 같다고 요구하지 않습니다. 같은 모드에서 공급자 변경 전후의 결과가 같아야 합니다.

모든 실행 환경에서 동일한 속도 개선을 보장하지 않습니다. 개별 반복의 편차와 개선이 작거나 없는 경로도 보고서에 그대로 포함합니다.
