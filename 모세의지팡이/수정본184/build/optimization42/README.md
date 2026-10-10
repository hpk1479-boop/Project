# Fact 최적화42 검증 도구

프로젝트 루트의 `수정내역_Fact최적화42.md`에 변경 범위·결과·재실행 명령이 있습니다.

- `run_checks.py`: 선택된 관련 검사 실행과 실패 항목 1회 재시도.
- `compare_original_failures.py`: 지정한 수정본41에서 실패를 대조. 먼저 `run_checks.py`를 실행합니다.
- `measure_fact_paths.py`: 원본 Fact 파일과 현재 Fact 경로의 합성 구간 측정. 전체 백테스트 측정이 아닙니다.

운영 파일은 이 도구들이 변경하지 않습니다. 결과는 프로젝트 루트 기준 `검증결과/Fact최적화42/`에 기록합니다. Python, NumPy, pandas, pytest 및 각 기존 검사가 요구하는 의존 패키지가 필요합니다.
