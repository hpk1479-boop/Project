# 실패·복구·SKIP 기록

초기 실패를 덮어쓰지 않았다. 아래 PASS는 해당 범위의 재검증만 뜻하며 Windows/실 raw 인증이 아니다.

## F00 — 0 / 원래 Windows 실행

관련 파일: `BACKTEST CONTROL.pyw / .venv-*/pyvenv.cfg`

기대값: GUI 시작

실제값: No Python at C:\Users\hpk14\.cache\codex-runtimes\...

예상 원인: 다른 PC의 base Python을 참조하는 복사된 venv + 실행 파일 존재만 확인

조치: CMD + 건강검사 + 백업/재생성/복원 + 진단 로그

결과: **코드 수정·단위 PASS / Windows 실기동 SKIP**

증거: `validation_outputs/stage0_tests.txt`

## F01 — ② / 최초 무제한 장기 native 검증

관련 파일: `validation_suite/validate_percentile_long.py`

기대값: 모든 family/case 완료

실제값: 200초 제한 도달, PRICE의 먼저 완료된 3 case는 불일치 0

예상 원인: 원본 호출 전체 버퍼 materialization/copy 누적 비용

조치: 원본 로그 보존; 240관측 독립 블록 ×5, case당1200으로 구성하여 전체67,200회 완료

결과: **재검증 PASS; 동일 단일 객체 수천관측 연속native 검증으로 과장하지 않음**

증거: `validation_outputs/stage2_PRICE_initial_timeout_partial.json / .log`

## F02 — ③ / 연결 실행 OZ profile

관련 파일: `validation_suite/bench_oz.py`

기대값: 6 profile 완료

실제값: 60초 셸 시간제한, 단위50 PASS 후 profile3개 완료

예상 원인: 프로파일러를 포함한 복합 명령 실행시간 초과

조치: 부분 결과 보존; 독립 순차실행으로 profile6개와 12,000관측 재완료

결과: **재검증 PASS**

증거: `validation_outputs/stage3_after_profile_timeout_partial.json / .log`

## F03 — ③ / WATCH compiler source integrity

관련 파일: `generic_backtest/watch/engines/local_contract.json`

기대값: 검증된 새 adapter로 compile

실제값: E_WATCH_SOURCE_DRIFT: oz.py

예상 원인: packaged-local adapter SHA가 기존 파일값

조치: 테스트 완료된 local 파일SHA만 갱신, 출처source hash와 oz_rules.py 원본 보존, 검증 비활성화 안 함

결과: **재검증 PASS**

증거: `validation_outputs/stage3_compile_initial_failure.json / stage4_tests_initial.txt`

## F04 — ① 연결검증 / 3m 결과 저장/검증

관련 파일: `generic_backtest/results.py`

기대값: LOWEST_REQUIRED_TIMEFRAME_CLOSE_V2 검증

실제값: E_RESULT_INTEGRITY: minute schedule metadata

예상 원인: 최종 verifier가 여전히 M1 메타데이터 고정

조치: 실제 required TF 최솟값/뷰 검증 추가, legacy 결과는 legacy 규약 별도 검증

결과: **재검증 PASS**

증거: `validation_outputs/stage1_integration_initial_failure.log / stage1_integration_rerun.log`

## F05 — ① 연결검증 / lookback=0 및 TRADE fixture

관련 파일: `generic_backtest/evaluation.py / validation_suite/integration_fixtures.py`

기대값: 닫힌 base 행 보존, 유효한 TRADE 설정

실제값: base 완료행 미보존 위험; fixture BID basis는 E_TARGET_CONFIG

예상 원인: 0-lookback book에는 completed slot 없음; BID는 지원 outcome basis가 아님

조치: CLOSE에만 private base slot 최소1; fixture basis만 CHART_PRICE로 수정

결과: **재검증 PASS**

증거: `validation_outputs/stage1_integration_rerun.log / stage1_trade_rerun.log / trade_*.json`

## F06 — 배포환경 / Pandas3.0.1 별도환경 설치

관련 파일: `requirements-backtest.txt`

기대값: NumPy2.3.5/Pandas3.0.1/pyzmq27.1.0 환경 확보

실제값: pip DNS Temporary failure in name resolution; direct wheel download도 실패

예상 원인: 컨테이너 패키지 저장소 네트워크 접근 실패. 패키지 미출시/미존재 때문이 아님

조치: 독립 venv만 생성; 주 측정 환경은 변경하지 않음; downstream 3.0.1 실행 검증 SKIP

결과: **환경 설치 FAIL / Pandas3.0.1 실행 검증 SKIP**

증거: `validation_outputs/pandas3_install.log / post_pipeline_status.json`

## S01 — 전체 / 실제 브로커 1일 raw 전체 성능

관련 파일: `입력/실행 환경`

기대값: 요청된 실제 환경 검증

실제값: 실행하지 않음

예상 원인: 첨부 ZIP에 raw archive 없음

조치: 합성/조건부 seed fixture 결과와 명확히 분리

결과: **SKIP**

증거: `BACKTEST_FINAL_REPORT_KO.md`의 검증 범위/미해결 범위 절

## S02 — 전체 / 실제 브로커 1주 raw 전체 성능

관련 파일: `입력/실행 환경`

기대값: 요청된 실제 환경 검증

실제값: 실행하지 않음

예상 원인: 첨부 ZIP에 연속 raw archive 없음

조치: 합성/조건부 seed fixture 결과와 명확히 분리

결과: **SKIP**

증거: `BACKTEST_FINAL_REPORT_KO.md`의 검증 범위/미해결 범위 절

## S03 — 전체 / Windows GUI/MT5 터미널 실기동

관련 파일: `입력/실행 환경`

기대값: 요청된 실제 환경 검증

실제값: 실행하지 않음

예상 원인: 실행 호스트가 Linux이며 Windows/MT5 프로세스 없음

조치: 합성/조건부 seed fixture 결과와 명확히 분리

결과: **SKIP**

증거: `BACKTEST_FINAL_REPORT_KO.md`의 검증 범위/미해결 범위 절

## S04 — 전체 / 원래 Windows Python3.12/Pandas3.0.1 전체 회귀

관련 파일: `입력/실행 환경`

기대값: 요청된 실제 환경 검증

실제값: 실행하지 않음

예상 원인: Windows 실행 불가 + Linux3.0.1 설치 네트워크 실패

조치: 합성/조건부 seed fixture 결과와 명확히 분리

결과: **SKIP**

증거: `BACKTEST_FINAL_REPORT_KO.md`의 검증 범위/미해결 범위 절

## S05 — 전체 / 실 raw 기반 양성 OZ/percentile 전체 5단계 누적 성능

관련 파일: `입력/실행 환경`

기대값: 요청된 실제 환경 검증

실제값: 실행하지 않음

예상 원인: 실 raw/검증된 초기 native 상태 없음; SOURCE_DEFINED_ONLY의 UNDEFINED를 임의로 덮을 수 없음

조치: 합성/조건부 seed fixture 결과와 명확히 분리

결과: **SKIP**

증거: `BACKTEST_FINAL_REPORT_KO.md`의 검증 범위/미해결 범위 절

## S06 — 전체 / LIVE/MT5 native terminal 1:1 인증

관련 파일: `입력/실행 환경`

기대값: 요청된 실제 환경 검증

실제값: 실행하지 않음

예상 원인: 원본 소스기반 oracle는 보존했으나 실제 terminal 캡처가 없음

조치: 합성/조건부 seed fixture 결과와 명확히 분리

결과: **SKIP**

증거: `BACKTEST_FINAL_REPORT_KO.md`의 검증 범위/미해결 범위 절

