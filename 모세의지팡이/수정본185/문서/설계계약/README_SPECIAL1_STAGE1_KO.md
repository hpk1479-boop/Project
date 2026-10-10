# SPECIAL1 — Part1 소스 기준 1차 수정본

**실제 LIVE Alert parity 완료본이 아니다.** SPECIAL1 판단 코드와 관측 순서 재생을 검증하는 첫 단계다. raw tick → MT5 native 지표 계산·발행 재현은 아직 구현하지 않았다. SPECIAL2~7 및 일반 WATCH로 확대하지 않았다.

Part1 전체 82개 파일은 업로드 원본과 바이트 단위로 같다. Part2에 `live_replay`를 추가하고 기존 SPECIAL1 관련 파일 3개만 수정했다. DataManager는 수정하지 않았다. 기존 보고서·테스트·canonical 계산 계약은 이번 판단의 기준으로 사용하지 않았다. 새 증거는 `reports/live_parity/`에만 있다.

## 실행 입구

### 새 SPECIAL1 관측 재생 GUI

`Part2/LIVE_REPLAY.pyw`를 실행한다. 입력은 아래 CLI와 동일한 native observation JSONL이다. 사용자 선택은 **실시간 / 봉마감** 두 개다. 실제 GUI 창 실행은 이 Linux 환경에서 검증하지 않았고 import만 확인했다.

**기존 `BACKTEST CONTROL.pyw`는 새 경로로 자동 전환되지 않는다.** 기존 GUI에서 TICK/LIVE_PARITY를 선택했다고 새 관측 재생을 사용하는 것은 아니다.

### CLI와 합성 예제

터미널에서 `Part2` 폴더로 이동한 뒤 실행한다. 예제는 시장 데이터가 아니라 테스트를 위해 만든 native-buffer 값이다.

```bash
python -B -m live_replay example --output synthetic.jsonl
python -B -m live_replay run synthetic.jsonl --mode 실시간 --output realtime.json
python -B -m live_replay run synthetic.jsonl --mode 봉마감 --output close.json
```

실시간은 Part1 `FINAL_ALERT.event_time`을 보존한다. 봉마감은 같은 신호의 표시시각만 다음 M1 경계로 옮긴다. 계산에 완료봉을 대입하거나 신호를 재평가하지 않는다. `delivery_time_ns`는 별도이며, 전달 재시도가 늦어진 경우 과거에 실제 표시된 것처럼 보고하지 않는다.

종료 시점보다 미래에 있는 봉마감은 자동 출력하지 않는다. `pending_close_count`를 확인한다. 실제 관측 시간이 진행되는 `CLOCK` 또는 후속 기록이 있어야 표시된다.

### 이어서 재생하기

```bash
python -B -m live_replay run first.jsonl --mode 실시간 --output first_result.json --checkpoint-out continuation.json
python -B -m live_replay run next.jsonl --mode 실시간 --output next_result.json --checkpoint-in continuation.json
```

후속 파일의 health_session·source fingerprint·모드가 같고 관측 키가 엄격히 증가해야 한다. 체크포인트는 **이 재생기의 연속 실행 상태**다. 실제 Part1 프로세스 재시작이나 LIVE 저장 상태를 가져오는 기능이 아니다. Python/pandas/numpy 및 엔진 소스 fingerprint가 다르면 복원을 거부한다.

### Alert 비교

```bash
python -B -m live_replay compare --live part1_live_ledger.json --replay realtime.json --mode 실시간 --output comparison.json
```

비교 ledger는 `origin=PART1_LIVE`, `timestamp_policy=PART1_EVENT_TIME`과 명시적 UTC 정수 ns 시각, direction, session, strategy, symbol, source_tf, source_spec_id, source_spec_ids가 필요하다. 같은 시각·방향의 다른 branch가 순서 비교에서 섞이지 않도록 branch 식별자도 검사한다. 형식은 `Part2/live_replay/TRACE_FORMAT_KO.md`를 본다. 원장 부재를 0건으로 취급하지 않는다. 양쪽 0건도 `INCONCLUSIVE_EMPTY`다.

`--duckdb ON`은 현재 명시적으로 거부한다. OFF로 몰래 전환하지 않는다. 실제 LIVE 기준 비교가 없는 상태에서 가속 성공을 주장하지 않기 위한 차단이다.

## 입력과 검증 범위

입력은 `STAFF_PUBLISH` native pipe payload, 개별 `TREND_POLL` / `COMPOSER_POLL` / `OZ_POLL`, 비동기 `COMMAND_DRAIN`, 필요 시 원본 `TREND_FACT`, 시간 진행 `CLOCK`이다. native payload에는 각 timeframe의 forming bar를 포함한 OHLCV·HMA·PRICE/RSI/STO/DI 버퍼가 이미 있어야 한다.

이 패키지에 **실제 LIVE 관측 수집기, raw tick을 이 형식으로 만드는 변환기, MT5 내 custom indicator 실행기**는 포함하지 않았다. 실제 Part1 코드는 수정하지 않았다. 수집되지 않은 과거의 buffer 관측 순서·폴링 위상·네트워크 결과는 추정으로 채우지 않는다.

봉마감 정책은 신호의 `event_time`이 속하는 UTC M1 경계다. M1에서는 정수 분 경계와 KST 분 경계가 일치한다. broker bar의 naive 시각을 UTC로 임의 해석하지 않는다.

## 테스트와 보고서

루트 폴더에서:

```bash
python -B build/verify_part1_immutable.py
python -B build/run_live_tests.py
```

테스트 실행마다 `reports/live_parity/reruns/`에 새 로그를 만들어 이전 실패를 덮어쓰지 않는다. 제공된 최종 결과는 네 개의 서로 겹치지 않는 pytest 그룹으로 **50 passed / 3 skipped / 0 failed**다. 실제 LIVE 검증, DuckDB OFF/ON, 실제 GUI 실행은 통과로 계산하지 않았다.

Part1에서 판단 소스를 다시 추출하는 명령은 `python -B build/rebuild_live_reference.py`다. 생성물만 변경하고 Part1은 읽기만 한다. 추출 정의·원본 경로·행 번호·SHA256은 `Part2/live_replay/reference/source_map.json`에 있다. 배포된 Part2는 Part1/DataManager 없이 자체 import 및 합성 CLI 재생이 가능하다.

전체 분석과 미완료 항목은 `reports/live_parity/FINAL_REPORT_KO.md`, 기계 판독 결과는 `final_test_summary.json`, 실제 LIVE 미검증 기록은 `actual_live_comparison.json`을 확인한다. 합성 2건을 실제 LIVE 2건으로 해석하면 안 된다.

## 환경

검증 환경은 Python 3.13.5 / numpy 2.3.5 / pandas 2.2.3 / pyzmq 27.1.0 / pytest 9.0.2다. 업로드 요구사항의 pandas 3.0.1은 이 환경에 없었다. 설치 시도가 DNS 오류로 실패하여 해당 버전 검증은 미실행이다. 기존 requirements를 호환 확인 없이 변경하지 않았다. DuckDB와 MetaTrader5도 설치되어 있지 않다.
