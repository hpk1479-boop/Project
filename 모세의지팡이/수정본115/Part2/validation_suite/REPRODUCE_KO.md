# 검증 재현

이 디렉터리는 원래 전략을 바꾸지 않고 reference 경로와 최적화 경로를 비교하기 위한 시험이다. 실제 브로커 데이터 수집이나 주문을 실행하지 않는다. 제공된 수치 fixture와 합성 raw는 실 raw가 아니다.

## 환경과 기본 회귀시험

Windows에서는 먼저 최상위 `START_BACKTEST.cmd`로 현재 PC의 Python3.12/3.13 64비트 환경을 준비한다. 테스트에만 필요한 pytest를 추가 설치한 뒤 다음 명령을 최상위 BACKTEST 폴더에서 실행한다.

```bat
.venv-generic\Scripts\python.exe -m pip install pytest==9.0.2
.venv-generic\Scripts\python.exe -m pytest validation_suite -q
```

Linux 주 측정은 Python3.13.5 / NumPy2.3.5 / Pandas2.2.3 / pytest9.0.2였다. Windows 원본 pin Pandas3.0.1 실행 검증과 MT5 연결은 이 배포에서 SKIP했다. 컨테이너에서3.0.1 별도 설치를 시도한 DNS 실패 로그를 보존했다. 서로 다른 Pandas 버전의 수치가 항상 동일하다고 가정하지 않는다.

## 순차 전체 재현

출력 폴더가 이미 있으면 덮어쓰지 않고 중단한다. 새 이름을 사용한다.

```bat
.venv-generic\Scripts\python.exe validation_suite\run_all.py --out validation_replay_01 --full
```

Linux에서는 동일 인자를 정상 Python에 전달한다.

```sh
python validation_suite/run_all.py --out validation_replay_01 --full
```

순서: 단위시험 → ①Gate 전후 → ②native4종/밴드 → ③OZ 전후 → ④IPC3반복 → 5단계1일/1주 누적 → TRADE/계약/HMA 추가시험 → exact 비교. 각 subprocess 실패/timeout은 기록하고 가능한 다음 명령으로 넘어간다. 예외가 포함된 JSON은 프로세스 종료코드0이어도 FAIL일 수 있으므로 comparer와 각 row status를 함께 본다. 환경/데이터 부재를 PASS로 간주하지 않는다.

원본 단계별 application source는 `references/`에서 새 출력 폴더 아래로 안전하게 추출한다. root에 현재 실행 코드를 덮어쓰지 않는다. baseline은 실행 복구만 적용한 전략 기준본이다. step1/2/3에는 후속 연결 검사에서 발견한 Gate verifier/lookback0 수정과 해당 local hash 갱신이 backport되어 있다. 정확한 목록은 `validation_outputs/cumulative_reference_corrections.json`이다. `ipc_before`는 단계③에 계측만 넣은 참고본으로 단계④ 성능 변경은 없다.

## 중요한 해석 규칙

`bench_cumulative.py`는 native-format 30초 간격 합성 raw로 BAR WATCH를 실행한다. 실제1주/OZ 전체 벤치마크가 아니다. 각 case의 결과 manifest를 검증한다. `bench_oz.py`는650행×3TF로6profile을 replay하고 관측마다 전체 상태/event digest를 남긴다. `validate_percentile_long.py`의 유한 seed는 NATIVE_STATE_CONDITIONED이며 source-only 미정의 상태를 임의로 초기화한 운영 결과가 아니다.

Cold/warm은 시험마다 뜻이 다르다. Native에서는 새 kernel sequence의 첫 호출/후속 호출이고, IPC에서는 수치 memo 저장소 비어 있음/재사용이다. native와IPC 표의 시간을 더하거나 배수를 곱해 전체 백테스트 성능으로 제시하지 않는다.

비교 기준은 TICK의 경우 원본이며, CLOSE의 경우 사용자가 요청한 Gate/봉 상태 변경이 적용된 step1이다. step1 이후 계산/판정/ID/파일이 변하면 FAIL이다. floating tolerance를 확대해 PASS 처리하지 않는다.

## 원본 기록

`validation_outputs/`에는 이번 작업에서 실행한 JSON/프로파일/실패 로그가 있다. 그 안의 `/mnt/data/...` 절대경로를 사용하는 driver들은 당시 컨테이너의 실행 이력이며 새 PC용 기본 실행기가 아니다. 재현은 이 문서와 경로 독립적인 `run_all.py`를 사용한다.
