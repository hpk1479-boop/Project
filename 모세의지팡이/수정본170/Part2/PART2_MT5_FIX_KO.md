# Part2 MT5 실행 수정 — 2026-09-25

## 확인한 원인

- Part2 MT5 선행 검사가 선택 기간 대신 Python 전략 준비 구간을 사용해 약 6일 선택에도 약 2년을 중복 재생했다.
- PRICE의 13/14/15, RSI/STO/DI의 9/10/11 보조 버퍼가 plot 없이 INDICATOR_DATA로 등록되어 실제 테스터 CopyBuffer가 4806을 반환했다. 모든 feed의 출력이 0건이었다.
- 0건 export도 성공 처리했고, started.txt 생성 후 터미널이 종료되면 완료 파일을 무기한 기다릴 수 있었다. 최초 초기화 알림 후 진행 이벤트도 억제됐다.

## 수정

- MT5 검사는 선택한 기간을 사용한다. Python 전략 계산의 seed_plan과 과거 데이터 준비 구간은 유지한다.
- 보조 버퍼를 INDICATOR_CALCULATIONS로 등록한다. 버퍼 번호와 계산식은 유지한다. Part2 배포 함수가 사용하는 Part1/program/MT5의 공용 원본 4개를 수정하고 실제 MT5에 컴파일했다.
- 빈 시간봉 출력은 실패 처리하고, 테스터 조기 종료를 감지한다. 5초마다 경과 시간과 생성 용량을 표시한다.
- 취소/실패 후 일반 MT5 복원 메시지가 성공 완료로 잘못 표시되지 않게 수정했다.

## 검증

- 수정 전 실제 실행: 19개 feed, 0 rows. 진단 로그에 보조 버퍼 CopyBuffer 4806 확인.
- 수정 후 실제 실행: XAUUSD+, 사용자 선택 구간, 19개 feed, 235,889 rows, 41.57초, PASS.
- 증거: generic_runs/part2_native_verification/result.json.
- pytest: test_native_data_build.py, test_execution_modes.py, test_korean_diagnostics.py — 33 passed.
- 검증 범위는 Part2의 실제 MT5 선행 실행 및 데이터 생성이다. 이후 Python 전략 전체 실행과 최종 Dashboard 결과는 이 검증에서 실행하지 않았다.
