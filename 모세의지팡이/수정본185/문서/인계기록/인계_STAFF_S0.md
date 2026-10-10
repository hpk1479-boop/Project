# STAFF S0 인계

수정본7은 수정본6 전체 복사본에 S0 기준선 도구와 증거를 추가한 독립 폴더다. 원본 수정본6을 수정하거나 실행하지 않았다. 운영 계산·전략 소스는 변경하지 않았으며 S1 이후 단계는 시작하지 않았다.

먼저 `검증결과/staff_s0/결과.md`, `status.json`, `수정내역_STAFF_S0.md`를 읽는다. 기존 `설계_STAFF_책임분리.md`의 단계·책임 분리 기준은 유지한다.

- 합성 골든: `golden_run1/2`, 33,996개 사례, 19,160개 정상 DataFrame 정확 일치.
- 실제 골든: `actual_run1/2`, 4,542개 사례, 4,302개 정상 DataFrame 정확 일치. 기준본 일봉 EMA 미준비 오류 응답 239개도 동일하게 보존.
- 실제 MT5 캡처: `actual_mt5/capture_1470f4cb1dc04acf8e00abe7f14c40f0`. 실제 소스 컴파일과 테스터 실행·파일 검증 완료. `compile.json`, `result.json`, `tester_agent.log` 참조.
- 전체 경로: `parity_240`, SPECIAL7 및 Watch 11개. 기준본 LIVE/현재 LIVE/현재 BACKTEST 일치 및 실제 조건 발생 확인.
- 단독 성능 기준: `benchmark.json`. 후속 비교는 동일 조건에서 다른 검증 프로세스를 종료한 뒤 측정한다.
- 원본 보존: `source6_manifest.json`, `copy_verification.json`, `original_preservation.json`.

기존 테스트 전체가 green인 것은 아니다. Part1 해시 체인, SPECIAL 디렉터리 파일 읽기, 누락된 GENERIC_EXAMPLE_V1/DataManager, Windows 경로 비교 실패를 기준본에서도 재현했다. `test_summary.json`에 항목별 증거가 있다. 해시 기록이나 기대값을 갱신해 실패를 숨기지 않는다. 일봉 EMA 미준비도 정상값으로 대체하지 않는다.

`actual` 명령은 미준비 응답이 있으면 기록 완료 후 종료 코드 1을 낸다. 비교 명령 결과와 실제 생성된 골든을 함께 확인한다. 합성 summary의 과거 실제 샘플 미확인 표시는 이후 생성된 별도 실제 골든을 부정하는 최종 상태가 아니다.

S1을 진행할 때는 설계대로 별도 수정본 폴더를 만들고 이 S0 골든을 고정 기대값으로 사용한다. S0 증거 파일은 덮어쓰지 않는다. 로컬 독립 MT5 터미널의 계정 설정은 외부 공유 자료에 포함하지 않는다.
