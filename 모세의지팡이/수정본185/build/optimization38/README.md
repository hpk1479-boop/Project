# 수정본38 검증 도구

수정본37을 변경 없이 보관한 별도 폴더와 수정본38을 비교합니다. 운영 코드의 전략 조건을 변경하거나 녹화를 합성하지 않습니다.

## 최종 적용 범위

- 전체 구독 내용을 키로 하는 불변 Subscriptions 재사용.
- 현재 설정에서 결정되는 Composer SWEEP watch_id와 fact_scope 문자열 캐시. 최신 binding·health·epoch는 캐시하지 않습니다.
- FactPort/Collector에서 이미 변환한 Fact의 두 번째 plain()만 생략. 최초 변환과 Signal.freeze는 유지합니다.

Delta 재조립 후보는 결과는 같았으나 시간 개선이 일관되지 않아 제외했습니다. 최종 Delta는 수정본37 파일과 동일합니다. BoardView 공유·가변 버퍼 공유·워커 분할은 적용하지 않았습니다.

## 실행 도구

| 파일 | 용도 |
|---|---|
| `run_regressions.py` | 선택한 프로젝트의 현재 엔진 회귀검사 또는 신규 54개 검사. 환경상 실행하지 못한 SQL·Windows 검사는 실패로 기록합니다. |
| `replay_probe.py` | 제공된 XAUUSD+ BAR 2026-09-01 녹화의 실제 워커 재생. `--trace`는 전체 발행 출력 내용·순서 해시, `--transport live`는 메모리 PipeReceiver 경로입니다. 기간이 고정된 이번 작업 전용 검사이며 일반 기간 실행기는 아닙니다. |
| `measure_steps.py` | 원본 함수와 후보 함수를 같은 프로세스에서 번갈아 반복 측정합니다. 운영 전체 시간과 별개의 하위 작업 측정입니다. Delta 항목은 최종본에서는 변경 없는 대조군입니다. |
| `verify_wire.py` | 녹화 전체를 CRC 검사 포함 복원하고 번들별 바이트 해시를 기록합니다. |
| `audit_scope.py` | 수정된 운영 함수와 보존한 코드 범위를 확인합니다. |
| `summarize_results.py` | 최종 18회 실행의 CSV 바이트·내부 출력 해시·시간·회귀검사 결과를 확인하고 요약을 생성합니다. |
| `seal_integrity.py` | 이번 변경 등록 시 사용한 도구입니다. **이미 등록한 최종본에서 재실행하지 않습니다.** 기존 등록이 있으면 중단하도록 되어 있습니다. |

새 회귀검사 파일은 `tests/test_part2_optimization38.py`입니다. 테스트 입력에는 선택 변경, 심볼 변경, 바인딩 교체, health·epoch 변경, 원본 데이터 사후 변이, FULL/ROW/HEARTBEAT와 잘못된 CRC 등이 포함됩니다.

## 상대 경로 실행 예시

프로젝트 루트에서 실행합니다. `BASE37`은 보관한 수정본37, `WAREHOUSE`는 녹화 창고, `CAPTURE`는 창고 기준 캡처 상대 경로를 실행 환경에서 지정합니다. CPU 고정은 선택 사항이며 아래 예시에서는 사용하지 않습니다.

```sh
python -B build/optimization38/run_regressions.py . 검증결과/재검사38 --new
python -B build/optimization38/audit_scope.py "$BASE37" .
python -B build/optimization38/measure_steps.py "$BASE37" . 검증결과/재측정38
python -B build/optimization38/replay_probe.py . "$WAREHOUSE" "$CAPTURE" "$WAREHOUSE/check38/special1" --strategy SPECIAL1 --trace
python -B build/optimization38/replay_probe.py . "$WAREHOUSE" "$CAPTURE" "$WAREHOUSE/check38/all_live" --strategy ALL --transport live --trace
python -B build/optimization38/summarize_results.py .
```

PowerShell에서는 환경 변수 문법을 해당 셸에 맞게 사용합니다. `replay_probe.py`의 출력 폴더는 운영 경로 규칙과 같게 **창고 안에** 있어야 합니다. 예시의 창고·원본 경로는 사용자가 실행 시 정하는 값이며 코드에 저장하지 않습니다.

## 1주 실제 입력 검증

이번 첨부에는 실제 하루 녹화만 있었습니다. 1주와 필요한 앞선 워밍업 입력을 갖춘 창고, 정상 DuckDB 환경에서는 보존된 `build/optimization37/verify_week.py`를 사용할 수 있습니다. 이 스크립트의 과거 도움말은 수정하지 않았으며 `--before`에 **수정본37 폴더**를 지정하면 됩니다. 출력은 이번 버전의 별도 폴더를 지정합니다.

```sh
python -B build/optimization37/verify_week.py --before "$BASE37" --after . --warehouse "$WAREHOUSE" --start "$START" --end "$END" --output 검증결과/실제1주38
```

`END`는 `START`보다 정확히 7일 뒤의 제외 경계입니다. 이 검사는 실제 부모 runner·SQL 병합까지 실행하고, 실행마다 새로 생기는 run_id만 제외한 알림 내용을 비교합니다. 이번 작업에서는 실제 1주 입력과 DuckDB를 확보하지 못해 실행하지 않았습니다.

## 증거 읽는 순서

최종 판정은 `검증결과/part2_optimization38/final_comparison.json`, `final_regression_comparison.json`, `measurements/retained_measurements.json`, `integrity.json`, `final_release_audit.json`에서 확인합니다.

`final_replay/`만 최종 구성의 비교 기록입니다. `candidate_replay/`, `wire/`, `measurements/measurements.json`의 Delta 항목은 제외된 중간 후보 검토 기록을 포함하므로 최종 성능으로 사용하지 않습니다. 실패한 최초 실행과 재시도는 삭제하지 않고 별도로 남겼습니다.

Linux 검증에서 DuckDB 설치가 두 번 실패했습니다. 워커 검증은 수정본37부터 존재한 검증용 로더만 사용했고, 운영 파일에 우회 코드를 넣지 않았습니다. 부모 SQL 경로는 실행하지 않았고, 메모리 LIVE 검사는 실제 Windows/MT5 송수신 검사가 아닙니다.
