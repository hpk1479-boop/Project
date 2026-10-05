# 전체 diff / 재현 절차

## 변경 범위 결론

- Part1: 전체 **1,331개 파일 SHA-256 동일**, 수정/추가/삭제 **0개**.
- 작업 기준 원본 Part2: 별도 보관본 전체 파일이 최초 manifest와 동일. 과거 `수정본/`은 사용하지 않음.
- 산출물 Part2: **기존 파일 21개 수정 + 신규 32개 추가 = 변경 53개**, 삭제 0개.
- 그중 운영 코드/계약 파일: **28개**(기존21개+신규7개). 추가 시험/계측/보고/화면/해시 자료: **25개**.
- 모든 변경은 아래 사유와 연결된다. 관련 없는 일반 전략, 원시 시장 입력, 기존 결과, 설정, native 지표 수식 파일, Part1은 변경하지 않았다.

최종 전체 테스트는 **318 PASS / 4 FAIL / 0 SKIP**이다. 추가 테스트60개는 모두 통과했다. 원본 전체 회귀는257 PASS / 5 FAIL이었으며 원본의 SPECIAL1 SDK 실행 문제로 실패했던 날짜 planner 테스트가 현재는 통과한다. 남은4개는 원본에도 없는 `backtest_specials/GENERIC_EXAMPLE_V1.py` fixture를 요구한다. 이 실패를 숨기거나 통과로 집계하지 않았다.

**FULL LIVE parity 승인 여부:** 미인증. 모든 전략의 비어 있지 않은 전체 시장 이벤트와 한 달 성능, 더 세밀한6m→3m→1m 게이트까지 완료했다고 보지 않는다. 상세 한계는 `02_IMPLEMENTATION_AND_VALIDATION_KO.md`를 먼저 읽는다.

## 운영 파일별 변경 이유

| 파일 | 변경 | 요청과 직접 관련된 이유 |
|---|---|---|
| `BACKTEST_SPECIAL/BACKTEST_SPECIAL1.py` | 수정 | WONBI-first LIGHT, 접촉 TF의 TREND/HMA 후계산, OZ 지연/복원 callback, 세션별 child 소비. |
| `BACKTEST_SPECIAL/BACKTEST_SPECIAL2.py` | 수정 | 외부 SWEEP LIGHT와 후속 OZ 분리, 기존 TRUE-B0 자격 연결, 무환경 과거 복원. |
| `BACKTEST_SPECIAL/BACKTEST_SPECIAL3.py` | 수정 | unordered EMA/FVG chain은 유지하고 최종 OZ 지연, 기존 미정의 TF 이름 연결, 세션 소비. |
| `BACKTEST_SPECIAL/BACKTEST_SPECIAL4.py` | 수정 | 30m boundary LIGHT, active 때 OZ 복원, 실제 final candidate에서 추세/ACK 계산. |
| `BACKTEST_SPECIAL/BACKTEST_SPECIAL5.py` | 수정 | 필수 부모 유지/하위 BLIND gating, setup 이전 지속 lower state 복원, 형제 취소 의미 연결. |
| `BACKTEST_SPECIAL/BACKTEST_SPECIAL6.py` | 수정 | MA/slope → OHLC-only FVG 복원 → 하위 OZ, 지속 상태·child/signature 소비. |
| `BACKTEST_SPECIAL/BACKTEST_SPECIAL7.py` | 수정 | EMA 대체 TREND를 현 Part2 TREND 엔진으로 연결, REGIME 하위 gating·지속 상태 복원. |
| `BACKTEST_SPECIAL/WATCH_UI_V1.py` | 추가 | 현재 UI/loader가 참조하지만 원본에서 누락된 WATCH SDK 진입 adapter 추가. |
| `generic_backtest/analytics.py` | 수정 | 빈 committed prefix 집계 및 부분 구간 statistics 지원. |
| `generic_backtest/cli.py` | 수정 | coordinator에 partial commit callback 연결. |
| `generic_backtest/conditional.py` | 추가 | 실행 내 raw prefix·현재 binding·선택 native stream·정확한 과거 replay와 계측. |
| `generic_backtest/conditional_client.py` | 추가 | 제한된 feature/history RPC 및 replay context 전송 수신. |
| `generic_backtest/context.py` | 수정 | 현재 scoped deferred feature marker를 worker의 명시적 feature 접근 시 해소. |
| `generic_backtest/gui.py` | 수정 | 기존 dashboard의 FULL/PARTIAL·요청/완료/진행률 표시 및 취소 결과 열기. |
| `generic_backtest/ipc.py` | 수정 | 취소 ACK 이후에도 확정 PARTIAL 결과 수신; 취소/commit lock 경합 처리. |
| `generic_backtest/journal.py` | 수정 | commit byte offset/sequence/hash checkpoint와 미확정 꼬리 rollback. |
| `generic_backtest/outcome.py` | 수정 | 확정 outcome transaction 보존, 취소 시 열린 거래 OPEN_CANCELLED 구분. |
| `generic_backtest/partial.py` | 추가 | 마지막 atomic observation checkpoint, rollback, 실제 구간/진행률 metadata. |
| `generic_backtest/results.py` | 수정 | PARTIAL finalize/verification, 실제 완료 구간과 이벤트·거래·통계 무결성 검사. |
| `generic_backtest/runner.py` | 수정 | SPECIAL/OZ WATCH native 호출 이전 conditional registry, 실행 주기 guard, 관측 commit·partial finalize. |
| `generic_backtest/special_builtin.py` | 추가 | 검토된 SPECIAL1~7 source SHA allowlist. 일반 plugin import/capability 제한은 유지. |
| `generic_backtest/special_oz_runtime.py` | 추가 | 현재 Part2 OZ 엔진의 전략별 지속 상태와 gated observation/replay 실행. |
| `generic_backtest/special_runtime.py` | 추가 | 현재 SPECIAL1~4 callback을 Generic SDK에 한정 연결; 양방향 LIGHT 후 lower 복원. |
| `generic_backtest/watch/engines/composite.py` | 수정 | 기존 observe를 setup/armed-OZ 단계로 분리하고 eager observe 계약 유지. |
| `generic_backtest/watch/engines/local_contract.json` | 수정 | 검토·수정한 composite.py/oz.py 두 local hash만 갱신; Part1 source 계약 hash는 유지. |
| `generic_backtest/watch/engines/oz.py` | 수정 | SPECIAL4 final gate를 위한 callable ACK 지원; 기존 bool ACK 동작 보존. |
| `generic_backtest/watch/percentile_runtime.py` | 수정 | setup 관측과 lower native/OZ 관측 분리, 실제 prefix silent 복원. |
| `generic_backtest/worker.py` | 수정 | 검토된 SPECIAL bridge 사전 로딩 및 scoped conditional RPC; 일반 callback 경로 보존. |

## 시험 / 보고 파일별 이유

| 파일 | 변경 | 요청과 직접 관련된 이유 |
|---|---|---|
| `conditional_validation/01_ANALYSIS_KO.md` | 추가 | 수정 전 전략별 분석과 후속 자료·소비 의미 정정. |
| `conditional_validation/02_IMPLEMENTATION_AND_VALIDATION_KO.md` | 추가 | 전략별 요청 항목, 성능 원시 수치 해석, PARTIAL 및 미인증 범위 보고. |
| `conditional_validation/03_DIFF_AND_REPRODUCE_KO.md` | 추가 | 전체 변경 파일/사유/개수 및 재현 절차. |
| `conditional_validation/benchmark.py` | 추가 | 수정 eager/conditional 짧은 합성/실제 prefix 벤치마크 재현 driver. |
| `conditional_validation/benchmark_original.py` | 추가 | 별도 원본 Part2 경로를 명시해 원본 SPECIAL5~7 실행 계측 재현. |
| `conditional_validation/benchmark_reconstruction.py` | 추가 | native exact-prefix 복원 회수/시간/실제 호출 대조 재현. |
| `conditional_validation/benchmark_results.json` | 추가 | 합성 dormant 96관측 실측 결과; 2회 시간 측정+별도 profile. |
| `conditional_validation/broker_prefix_benchmark.json` | 추가 | 실제 broker 96 tick의 한정된 prefix 실측과 입력 hash. |
| `conditional_validation/dashboard_partial.png` | 추가 | 기존 Tk dashboard의 약50% 취소 합성 결과 표시 화면. |
| `conditional_validation/final_test_log.txt` | 추가 | 최종 전체 회귀 + 추가 테스트의 원문 로그. |
| `conditional_validation/original_part2_benchmark.json` | 추가 | 수정하지 않은 원본 SPECIAL5~7의 단기 실측. |
| `conditional_validation/original_part2_execution.json` | 추가 | 원본 SPECIAL1~4 실행 실패와 5~7 선언 확인 기록. |
| `conditional_validation/original_test_log.txt` | 추가 | 원본 Part2 전체 validation_suite 회귀 로그. |
| `conditional_validation/part2_changes.diff` | 추가 | 기존 Part2 파일 21개의 전체 unified diff. 신규 코드는 산출물 각 파일. |
| `conditional_validation/reconstruction_benchmark.json` | 추가 | replay 122 context의 동일성 및 복원 native 실제 비용. |
| `conditional_validation/source_audit.json` | 추가 | Part1/원본 Part2 무변경 검사, Part2 diff 목록·해시·계수. |
| `conditional_validation/test_conditional.py` | 추가 | native prefix 복원, gap/동시 tick, 미래 접근 차단, 실행 주기 guard. |
| `conditional_validation/test_dashboard.py` | 추가 | 기존 GUI에서10/30/50/80% PARTIAL 결과 정상 표시. |
| `conditional_validation/test_intrabar.py` | 추가 | 실제 Part1 WONBI oracle의09:32 및 완료봉 EMA 시점 구분. |
| `conditional_validation/test_live_rules.py` | 추가 | Part1 실제 OZ 30메서드의 양방향/프로필/취소·만료·재시도 대조. |
| `conditional_validation/test_partial.py` | 추가 | 실제 coordinator/journal/outcome/verifier의 취소 prefix 통합 회귀. |
| `conditional_validation/test_restoration.py` | 추가 | 게이트 이전 미완료 상태 복원·기소비 후보 부활 금지·ACK 재시도. |
| `conditional_validation/test_watch_path.py` | 추가 | 일반 BAR WATCH의 TICK/봉마감/LIVE_PARITY 전체 worker 경로 회귀. |
| `conditional_validation/test_worker_smoke.py` | 추가 | SPECIAL1~7 실제 격리 worker 시작/관측/finish 계약 검사. |
| `conditional_validation/verification_summary.json` | 추가 | 실행한 테스트 수치, 통과 범위와 미인증/미구현 범위. |

## 유지한 수식·계약과 범위

`pit/features/percentile` native kernel/session/provider, `pit/features/hma_open.py`, WATCH `oz_rules.py`, `fvg_math.py`, `trend_math.py`, `trend.py`, `derived.py`의 수식 파일은 원본 그대로다. 변한 것은 계산 호출 시점과 현재 Part2의 실제 엔진 연결이다. SPECIAL7의 잘못된 TREND 대체 로직 및 setup별 빈 lower state와 같이 Part1과 달랐던 전략 glue는 현재 Part2 구조에서 보정했으므로 원본 이벤트와 무조건 동일하다고 주장하지 않는다.

SPECIAL source allowlist 7개 SHA와 WATCH local contract hash를 실제 파일에 대조했다. allowlist에 없는 일반 plugin은 기존 제한 loader를 사용한다. 원본 AST/import 제한을 전체 해제하거나 Part1 source contract 검사를 끄지 않았다. `part2_changes.diff`는 기존 파일 전체 diff이며 신규 파일은 위 목록과 산출물 원문에서 확인한다. `source_audit.json`은 신규 파일 자체 해시를 포함하되 자기 파일의 recursive hash만 명시적으로 제외한다.

## 실행·시험 재현

현재 Part2의 기존 실행 환경을 사용한다. 새 패키지·서비스 설치를 요구하는 기능은 추가하지 않았다. 배포본에 있는 Windows venv를 Linux 검증에 사용한 것이 아니라 이 실행 환경의 Python 3.13에서 제공 의존성으로 시험했다. 운영체제별 GUI/브로커 터미널 실제 연결 검증은 별도다.

Part2 디렉터리에서 다음 명령을 실행한다. `PART1_REFERENCE`는 사용자가 별도로 보관한 원본 Part1 경로이며 이 산출물에는 Part1을 포함하지 않는다. Part1을 지정하지 않으면 해당 실제 oracle 테스트는 skip될 수 있다.

```bash
# Linux headless GUI 포함. 데스크톱 환경에서는 xvfb-run 없이 실행 가능.
PYTHONDONTWRITEBYTECODE=1 PART1_REFERENCE='/path/to/original/모세의지팡이 Part1' xvfb-run -a python -m pytest validation_suite conditional_validation -q -ra -p no:cacheprovider

# 짧은 합성 96관측. 전체 SPECIAL 양성 parity나 한 달 benchmark가 아님.
python conditional_validation/benchmark.py --observations 96 --rounds 2

# 제공된 broker 첫 nonempty chunk의 첫96tick. 이전 시장 이력 없음.
python conditional_validation/benchmark.py --broker-prefix --observations 96 --rounds 1   --output conditional_validation/broker_prefix_benchmark.json

# 실제 native 복원 비용 별도 계측
python conditional_validation/benchmark_reconstruction.py

# 원본5~7을 별도 경로에서 읽기 전용 실행. 원본 코드를 산출물에 복사하지 않음.
PYTHONDONTWRITEBYTECODE=1 python conditional_validation/benchmark_original.py   '/path/to/original/모세의지팡이 Part2'   conditional_validation/original_part2_benchmark.json
```

시험 출력 JSON을 다시 쓰면 납품 시 `source_audit.json`에 기록된 검증 자료 해시는 달라질 수 있다. 원래 계측 파일을 보존하려면 `benchmark.py --output <별도 경로>`를 지정한다. 원본 벤치마크 스크립트의 두 위치 인수도 원본 Part2와 출력 경로를 명시한다.

기존 GUI/CLI로 SPECIAL1~7 또는 OZ composite WATCH를 실행할 때는 **TICK 또는 LIVE_PARITY를 명시적으로 선택**한다. 저장된 봉마감 설정을 자동 변환하지 않으며 실제 실행 시에는 명확한 오류로 거부한다. 날짜 입력과 자료 취득 계획 생성까지 막는 것은 아니므로 검증 목적에 맞는 실행 모드를 먼저 선택하는 편이 낫다. 일반 완료봉 WATCH의 봉마감 경로는 유지한다.

조건부 계산의 비교 스위치는 config의 `resources.conditional_specials`이다. 기본은 해당 SPECIAL/OZ WATCH에서 True이며, False는 현재 수정 전략의 eager control이다. 이것을 원본 Part2 버전으로 되돌리는 스위치로 해석하지 않는다. 다른 전략/실행 간 feature 공유나 새로운 디스크 indicator cache는 없다.

## 산출물 확인

납품 ZIP의 최상위 폴더는 `모세의지팡이 Part2` 하나이다. 원본 Part1, 전체 합본 ZIP, 과거 수정본을 동봉하지 않는다. 원본 Part2의 기존 raw/archive/result/config 파일은 변경하지 않고 포함한다. 테스트 임시 디렉터리·이번 실행의 pytest cache는 납품 파일에 넣지 않았다.
