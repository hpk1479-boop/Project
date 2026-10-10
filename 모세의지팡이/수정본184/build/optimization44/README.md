# 수정본44 — 가상진입 최적화 검증 도구

운영 변경은 `Part2/event_backtest/virtual_entry.py` 한 파일입니다. 합성 입력 검사, 실제 파일 형식의 합성 녹화 검사, 구간 성능 측정, 수정 범위 확인을 제공합니다. 사용자 시장 녹화·Windows MT5·실제 DuckDB 통합 실행을 대신하지 않습니다.

## 실행

프로젝트 루트에서 실행합니다. Python 의존성은 기존 `Part2/requirements-backtest.txt`를 그대로 사용합니다. 비교 경로는 사용자가 별도 보관한 수정본43 폴더 또는 ZIP을 지정합니다.

```bash
python build/optimization44/run_checks.py
python build/optimization44/compare_input_failures.py --original "수정본43_폴더"
python build/optimization44/probe_recorded.py --original "수정본43_폴더"
python build/optimization44/measure_paths.py --original "수정본43_폴더" --samples 7 --batch 5
python build/optimization44/measure_hash.py --samples 7
python build/optimization44/verify_scope.py --input-zip "수정본43.zip"
```

`run_checks.py`는 실패 항목을 한 번 재시도합니다. 그룹 이름을 주어 개별 실행할 수 있습니다. 이전 실행 결과를 유지하려면 증거 폴더를 별도 보관한 뒤 재실행하십시오. 이번 자료는 새로 실행한 증거만 포함하며 이전 수정본의 검증결과 폴더는 복사하지 않았습니다.

## 각 도구의 범위

- `support44.py`: 가상진입용 합성 알림·배열, FULL/ROW/HEARTBEAT 7개 녹화, 파일 저장 입력을 생성합니다. 비교 소스는 별도 모듈 이름으로 읽으며 운영 모듈에 설치하거나 원래 전략을 되돌리지 않습니다.
- `run_checks.py`: 신규 70개와 기존 관련 검사 묶음을 별도 프로세스에서 실행합니다. 실패·수집 오류를 통과로 바꾸지 않습니다.
- `compare_input_failures.py`: 남은 실패가 수정본43에서도 재현되는지 진단합니다. Part1 기존 소스 무결성 불일치는 임의 승인하지 않습니다.
- `probe_recorded.py`: MSD1, 검증 표시가 없는 MSD2, 검증 완료 MSD2의 실제 읽기·CSV 저장 경로를 비교합니다. 정상 종료와 진입 직후 중단의 6개 사례입니다. 예상되는 승패와 부분 결과도 별도 확인합니다.
- `measure_paths.py`: 프로파일러 없이 AB/BA 순서로 7회 측정합니다. 최종 측정은 표본당 5회 실행하고 1회당 시간으로 환산합니다. 목표가격·입력 딕셔너리 연산 횟수는 시간 측정 밖에서 셉니다. C 구간은 요약뿐 아니라 상세 행 생성도 포함하며, calculate 구간은 인메모리 합성 입력과 CSV 저장을 포함하고 녹화 디스크 읽기는 제외합니다.
- `measure_hash.py`: 기존 4 MiB read와 4 MiB/256 KiB readinto 후보를 비교합니다. 후보 코드는 검증 도구 안에만 있으며 운영 settings.py는 변경하지 않습니다. 실험 파일은 임시 폴더에 생성 후 삭제합니다.
- `verify_scope.py`: 수정본43 ZIP과 파일 단위 비교를 수행하고, 허용한 최적화 구문만 메모리에서 제거한 AST가 입력 소스와 같은지 확인합니다. Part3 코드는 실행하지 않습니다.

## 미완료·제외

⑦ 해시 버퍼 변경은 두 후보 모두 일관된 성능 개선이 확인되지 않아 배포하지 않았습니다. 작은 파일에서는 명확한 회귀가 있었습니다. DuckDB 설치는 두 번 실패했고, 연동 검사 1개와 테스트 수집 오류 1건은 한 번씩 재시도한 뒤 수정본43에서도 재현됨을 확인했습니다. 운영 DB 경로·의존성 파일을 바꾸지 않았습니다.

검증 환경은 Linux / Python 3.13.5 / NumPy 2.3.5 / pandas 2.2.3입니다. 프로젝트 지정 pandas 3.0.1 환경과 다릅니다. 가상진입 신규 검사는 DuckDB 없이도 실제 파일 읽기와 CSV 쓰기를 그대로 실행합니다. 기존 37·39·43 일부 검사에 포함된 검증용 SQL import 우회는 실제 SQL 검증을 의미하지 않습니다.

## 성능 자료 구분

최종 채택 구현의 공식 측정은 `measurements.json`입니다. `measurements_initial.json`은 청산 완료 캐시 해제를 추가하기 전의 초기 측정, `measurements_single_call_final.json`은 최종 구현을 표본당 한 번만 측정한 예비 자료입니다. 짧은 구간의 변동을 줄이기 위해 최종 자료는 표본당 5회 실행했습니다. 어느 측정도 전체 백테스트 개선율로 해석하지 않습니다.
