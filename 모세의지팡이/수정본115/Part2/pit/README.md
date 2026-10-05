# PIT_REAL_TICK_CORE_SPIKE_V1

격리된 CPU-only 기술검증 구현이다. 기존 `program/`, `replay/`, `backtest/`, GUI와 승인 manifest를 수정하지 않는다. 전체 production engine이나 historical exporter가 아니다. Gate1은 BLOCKED 상태이며 저장된 native 결과만 reference로 읽는다.

기존 `.venv-replay` Python 3.12 환경을 재사용한다. 새 dependency 설치는 하지 않았다. `requirements-pit.txt`는 실제 검증 환경의 주요 기존 package 버전 기록이다.

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
$env:PYTHONHASHSEED='0'
$env:TZ='UTC'
$env:OPENBLAS_NUM_THREADS='1'
$env:OMP_NUM_THREADS='1'
$env:MKL_NUM_THREADS='1'
$env:NUMEXPR_NUM_THREADS='1'
.\.venv-replay\Scripts\python.exe -m pit spike `
  --input "gate01_spike/20260919_123556_isolated/frozen_real/ticks_20s.npy" `
  --descriptor "pit_spike/20260919_pit_real_tick_core_v1/input_172.json" `
  --output "pit_spike/manual_172_01"
```

`--output`은 아직 존재하지 않는 `pit_spike/` 아래 경로여야 한다. 기존 출력, 보호경로, symlink/junction을 통한 우회는 거부한다. 다른 컴퓨터로 옮긴 descriptor는 실제 파일 경로와 hash를 명시적으로 검토해야 한다. 실행 중 승인 manifest를 갱신하지 않는다.

`--test-control`은 별도 `TEST_CONTROL_ON_REAL_TICKS` 알림을 최초 tick 뒤에 주입한다. 실제 OZ 알림이 아니다. 정상 모드에서는 raw tick에서 OZ를 만들어내지 않는다. `--without-b`, `--chunk-size`, `--host-delay`는 private session/재현성 시험 옵션이다. host delay는 입력 시간에 영향을 주지 않는다.

## 구현 경계

- `.npy` 원본 record를 `<qdddQqId>`의 60-byte 구조와 uint64 float bits로 보존한다. ordinal과 prefix commitment를 사용하며 전체 미래 파일 hash를 기능 ID에 넣지 않는다. `0x80`도 보존한다.
- `PIT_TICK_V1`은 매 record를 별도로 소비한다. 같은 ms의 record도 서로 다른 cutoff를 만든다. ASK-only는 quote를 갱신하며 bar/tick_volume은 검증된 BID-bit/유효 Bid 규칙으로만 갱신한다.
- 19TF 경계는 fixed policy이며 실제 native XAUUSD+ 2026-09-18 정오 open alignment를 검증했다. 12분 확대는 같은 alignment의 rollover smoke이다. DST, session 전환, 8h/D1 rollover 인증으로 확대하지 않는다.
- 최초 interval 앞부분이 없으면 `PARTIAL_START`, 잘못된 eligible price는 `UNKNOWN_GAP`이다. 빈 시간 slot을 만들지 않고 EOF를 completed로 만들지 않는다. seed는 완료 M1만 사용하고 현재 interval을 final seed로 대체하지 않는다.
- HMA는 승인 EA `WMAAt/CalcHMA` 연산 순서의 단일 OPEN HMA6/17 port이다. half/root는 floor이고 최소 표본은 7/20개다. 다른 indicator 계산은 이식하지 않았다. Native 수치 parity는 1m/2m live row만 검증했다.
- FeatureSession은 owner별 state와 handle을 가진다. 읽기는 passive이다. strict 유한 window에 부분 seed/공백이 남아 있으면 값은 unavailable이다. 단절 seed를 사용한 native 수식 비교와 전략 사용 가능 여부는 다르다.
- `PitBacktestInput`은 기존 `MarketSnapshot`, `OZAlert`, `NBarBacktestBatch`를 호출한다. Entry/Stop/RR/outcome 계산을 복제하지 않는다. 새 feature를 기존 historical dataset fidelity enum으로 위장하지 않는다.
- 기존 EventJournal의 실제 관측 ID/sequence를 projection에 사용한다. Entry 시점에 `ENTRY`와 `STOP_RESOLUTIONS` 각 1개를 기록한다. N/RR을 바꾸어도 후속 관측 sequence가 밀리지 않는다. N별 Stop 내용이 다른 실행의 전체 hash-chain envelope는 달라질 수 있다. 같은 입력 prefix/N의 과거 전체 Entry/Stop record는 미래 suffix에 의존하지 않는다.
- `payload_hash`는 `PIT_FRAME_CANONICAL`이며 STAFF packet hash가 아니다. 전체 frame은 첫 oracle cursor/Entry 시점에만 보관하고, 전 tick 650×45 snapshot을 저장하지 않는다.
- `LegacyStaffViewAdapter`는 승인 원본 STAFF private loader와 `DataServer.handle`만 호출한다. OHLC input에 원본 ATR/WONBI를 적용한다. Pipe receiver/ZMQ server/전략 loop는 시작하지 않는다. 전체 6/17/50/168 HMA group은 미구현이므로 명시적으로 거부한다.
- 원본 materialized bundle의 RR-only rescore 시험은 기존 verifier/rescorer를 그대로 사용한다. compact PIT 결과를 기존 rescore bundle이라고 표시하지 않는다. 실 delivered-OZ reader, 전체 전략 host, checkpoint, 장기 archive는 이번 구현 범위 밖이다.

## 검증

```powershell
.\.venv-replay\Scripts\python.exe -m pytest -c pit_tests/pytest.ini pit_tests -q -p no:cacheprovider `
  --basetemp "pit_spike/manual_tests_01" --junitxml "pit_spike/manual_tests_01.xml"
```

테스트용 `--basetemp`도 매번 새 경로를 사용한다. `pit_tests/helpers.py`의 UNIT fixture와 TEST_CONTROL 결과는 실제 broker/OZ 결과와 구분한다. 실제 자료/산출물/한계/추가파일 hash는 `pit_spike/20260919_pit_real_tick_core_v1/VALIDATION_REPORT.md`와 같은 폴더의 receipt를 참조한다.
