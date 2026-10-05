# 최적화 검증 재현

## 입력과 환경

이 ZIP은 수정된 Part2만 포함한다. Part1과 raw archive는 포함하지 않는다. 제공 원본 ZIP의 최신 Part2를 별도
`BASELINE` 디렉터리에 그대로 풀어 기준으로 사용한다. 구버전 코드를 기준으로 삼지 않는다. raw 경로만
`optimization_evidence/raw_origin.json`을 참고해 원본에서 별도로 확보한다.

실행 환경/라이브러리 차이는 `optimization_evidence/environment.json`을 확인한다. 이번 전후 검증의 pandas는
2.2.3이었으며 제공 requirements의 3.0.1과 다르다. requirements를 임의 수정하지 않았다.

아래 명령의 BASELINE, RAW_SOURCE, INPUTS, OUT은 사용자 경로로 바꾼다. 현재 디렉터리는 최종 Part2다.
기존 출력 디렉터리를 덮어쓰지 않도록 새 경로를 사용한다.

```sh
export PYTHONDONTWRITEBYTECODE=1
export OPENBLAS_NUM_THREADS=1
export OMP_NUM_THREADS=1
export PART1_REFERENCE="/path/to/user-owned/모세의지팡이 Part1"

python conditional_validation/optimization_prepare_inputs.py \
  --source "RAW_SOURCE" --output "INPUTS" --rows 5000 50000 200000
```

마지막 중복 millisecond 그룹을 포함하므로 다른 입력에서는 실제 row 수가 요청 수를 약간 넘을 수 있다.
이번 실측 입력은 정확히 5,000/50,000/200,000행이었다. 입력 준비기의 5k 재실행 manifest까지 원본 측정 입력과
완전히 같음을 `reproduction_smoke.json`으로 확인했다. 입력은 검증용 raw prefix이며 feature cache가 아니다.

## 전체 pytest

Linux GUI 검증에서는 이번 실행처럼 xvfb를 쓴다. Windows에서는 기존 화면 환경에서 `python -m pytest ...`를
실행한다. Part1을 제공하지 않으면 원래의 Part1 read-only oracle 테스트가 skip되므로 이번 441/4와 같지 않다.

```sh
xvfb-run -a python -m pytest validation_suite conditional_validation cadence_input_validation \
  -q -ra -p no:cacheprovider --junitxml="OUT/final_tests.xml" --durations=15
```

기존 누락 fixture로 4개 실패/exit 1이 남는 것이 이번 기준 상태다. 새 failure node가 생겼는지 원본 XML과
비교해야 하며 4개를 삭제/xfail 처리하여 통과시켜서는 안 된다.

## 실제 worker 동일성

동일 검증 스크립트로 기준 snapshot과 최종 snapshot을 각각 실행한다. 정상 및 취소, FULL/DELTA_V1,
TICK/봉마감/LIVE_PARITY를 포함한다. 스크립트는 임시 검증 전략을 finally에서 삭제한다. 같은 폴더에서
동시 실행하지 않는다.

```sh
python conditional_validation/optimization_regression.py --root "BASELINE" --output "OUT/before.json"
python conditional_validation/optimization_regression.py --root . --output "OUT/after.json"
python conditional_validation/optimization_regression.py \
  --compare "OUT/before.json" "OUT/after.json" --output "OUT/comparison.json"
```

## wall-clock, memory, profile를 분리

```sh
# 최종 보고서 W와 같은 실제 coordinator, RSS sampler 없는 wall-only 실행
python conditional_validation/optimization_wall_benchmark.py \
  --root . --archive "INPUTS/real_50000" --rows 50000 --output "OUT/wall.json"

# 원래 benchmark의 별도 memory-sampled 실행
python cadence_input_validation/benchmark.py \
  --root . --archive "INPUTS/real_50000" --plugin BACKTEST_SPECIAL7 --rows 50000 --output "OUT/rss.json"

# 별도 cProfile. 이 inflated wall을 위 wall과 혼합하지 않는다.
python cadence_input_validation/benchmark.py \
  --root . --archive "INPUTS/real_50000" --plugin BACKTEST_SPECIAL7 --rows 50000 --profile --output "OUT/profile.json"
```

각 arm을 최소 3회, 순서를 바꿔 실행하고 다른 benchmark/pytest를 동시에 실행하지 않는다. raw observation/sec는
raw tick 수/wall이며 50k 입력의 전략 평가 횟수 142와 다르다. snapshot 간 profile 절대 경로 차이는 정상이다.
개별 STEP snapshot은 최종 ZIP에 중복 포장하지 않는다. 최종본과 원본의 정확한 실행 코드 diff는
`optimization_source_changes.diff`에 있다. 원본 보존 복사본에서만 단계별 diff를 검토/재구성한다.

## 함수 전용 검증과 counters

```sh
python -m pytest conditional_validation/test_optimization_gap.py \
  conditional_validation/test_optimization_bars.py conditional_validation/test_optimization_hot_loops.py \
  -q -p no:cacheprovider

python conditional_validation/optimization_gap_benchmark.py \
  --archive "INPUTS/real_50000" --rounds 3 --profile --output "OUT/gap.json"
python conditional_validation/optimization_gap_benchmark.py \
  --archive "RAW_SOURCE" --rounds 2 --skip-baseline --output "OUT/full_gap_only.json"
python conditional_validation/optimization_bar_benchmark.py \
  --archive "INPUTS/real_50000" --rounds 3 --output "OUT/bar.json"
python conditional_validation/optimization_hot_loop_benchmark.py \
  --root . --archive "INPUTS/real_50000" --case all --rounds 5 --repeat 10 --output "OUT/hot.json"
python conditional_validation/optimization_tf_profile.py \
  --root . --archive "INPUTS/real_5000" --plugin BACKTEST_SPECIAL7 --rows 5000 --output "OUT/tf.json"
```

HMA 채택 실측은 `--case hma --rounds 7 --repeat 150`과 같은 workload였다.
SPECIAL1/2/5는 제공 실제 prefix로 warmup guard에서 거절될 수 있다. guard를 해제하거나 요구 warmup을 줄이지 않는다.
보고서의 합성 epoch-start 데이터는 현재 `cadence_input_validation.test_input.native_rows/archive_from_parts`를 사용해
`ms=(arange(N)//3)*500`, `bid=100+sin(arange(N)*0.01)*2`, ask=bid+0.1, last=bid, coverage=[0,(ms[-1]+1)*1e6]로
생성했다. N은 5,000/50,000이며 결과 manifest를 evidence/input_manifests에 보존했다. 실제 raw benchmark와 구분한다.

Numba를 적용하지 않았으므로 JIT compile/warm 측정 명령은 없다. 원래 reference ZIP이나 Part1을 운영 코드로
덮어쓰는 명령도 없다. 이 문서와 모든 validation 도구는 전략 입력/운영 코드와 분리되어 있다.
