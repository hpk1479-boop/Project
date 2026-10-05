# Offline regression harness

운영 소스를 수정하거나 import하지 않고 임시 폴더의 바이트 동일 복사본을 실행한다. Python 3.12, NumPy 2.3.5, pandas 3.0.1에서 검증한다. 다른 환경에서는 이 디렉터리의 requirements만 별도 가상환경에 설치한다. `requests`, `pyzmq`, MT5, Telegram 계정, API key는 필요 없다.

프로젝트 루트에서:

```powershell
python -B audit/run_tests.py
```

이 PC에는 `python`이 PATH에 없으므로 검증 시 사용한 실행 명령은:

```powershell
& "$env:USERPROFILE/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe" -B audit/run_tests.py
```

개별 재현:

```powershell
python -B -W ignore::ResourceWarning audit/test_baseline.py Baseline.test_manager_restart_loses_unchanged_trend_fact
```

결과는 [results/test_report.json](results/test_report.json)에 기록한다. 실패/오류/원본 해시 변화가 있으면 exit code 1이다. 알려진 문제는 `expectedFailure`나 skip이 아니라 **현재 잘못된 결과를 명시적으로 assert**한다. 향후 수정 시에는 해당 문제의 기대 결과를 승인된 새 계약으로 바꾸고, 기존 기준선과 차이를 검토해야 한다.

## 구성

| 파일 | 역할 |
|---|---|
| `harness.py` | 임시 복사, 가상 시간, 실제 pyobj 직렬화 왕복, fault/ACK loss, 합성 시장, 실제 binary decoder 진입, 단일 주기 JSONL 실행 |
| `test_baseline.py` | 전략 의미/경계값/상태 저장·복원/단독 재시작/중복/최종 알림 검증 |
| `fixtures/replay.json` | 고정 epoch, 80행 OHLCV·45개 MT5 slot의 합성 recipe, tail override, OUT→IN→NEXT_BAR, 예상 최종 event |
| `fixtures/source_manifest.json` | 전달 원본 전체 SHA-256. 테스트 중 자동 갱신 금지 |
| `fixtures/source_index.json` | 전체 Python/pyw class/function 위치 |
| `fixtures/contract_inventory.json` | 원문 payload/dispatch/필드 get/I/O 사용 구문. 정적 보조자료 |
| `run_tests.py` | unittest 실행과 결과 기록 |
| `tools/source_inventory.py` | 최초 기준선 추출 도구. **회귀 실패를 없애려고 재실행하지 말 것** |

## 재현 설계

1. 매 테스트마다 새 임시 `program/`을 만든다. 운영 config/기존 logs는 복사하지 않는다.
2. 실제 모듈들을 복사본 경로로 로드한다. 실제 SPECIAL 6개도 로드한다. 합성 조합 테스트는 이후 spec/handler/child 레지스트리를 비우고 테스트 전용 spec을 넣는다. startup plugin 검증은 별도 테스트이다.
3. STAFF input은 `<IIqIIII>` header+UTF-8 symbol/tf+int64 time/volume+45개 little-endian double이다. `_consume_one`이 실제 decode/정렬/sequence/cache 갱신을 수행한다.
4. 기존 StaffClient/ManagerClient/TelegramSender가 offline `send_pyobj/recv_pyobj` adapter를 사용한다. 보내는 객체와 응답은 pickle serialize/deserialize하므로 reference 공유로 통신을 우회하지 않는다.
5. JSONL은 실제 파일에 기록하며 worker의 실제 `run()`을 한 주기 실행한다. sleep/OS thread scheduling은 쓰지 않는다.
6. 재시작은 같은 임시 디스크 위에서 해당 객체를 재생성한다. 다른 엔진의 event suppression 메모리는 그대로 남는다. SIGKILL 또는 interpreter global 전체 재시작의 대체 증명은 아니다.
7. `fail_next`는 수신 전 timeout, `lose_ack_next`는 handler 처리 후 응답 유실을 재현한다. 실제 libzmq EFSM/HWM/네트워크 buffer는 검증하지 않는다.
8. NotificationService의 실제 send 경로까지 호출하되 HTTP만 가짜 200/500 응답으로 기록한다. 가짜 token/recipient 이외 HTTP 호출은 거부한다.

가상 시계는 UTC epoch와 monotonic을 함께 전진시킨다. source 모듈의 time/datetime 참조만 바꾸며 전역 stdlib time은 바꾸지 않는다. dataclass 기본 factory와 logging timestamp처럼 판정에 사용하지 않는 생성 메타데이터에는 실제 시간이 남을 수 있다. chain fixture는 created_at을 명시하며 회귀 assertion은 실시간 로그에 의존하지 않는다. fixture는 실 tick 녹화가 아니고 지표 수식을 재구현한 oracle도 아니다.

## 변경 동결 규칙

`source_manifest.json`이 운영 28개 파일의 바이트 변화를 감지한다. 숫자 및 이벤트 결과 assertion은 의미 변화를 감지한다. 해시 검사를 제거하거나 기대 결과를 자동 재생성하는 update-golden 모드는 제공하지 않는다. 새 운영 수정은 이 회귀 세트를 먼저 실행한 뒤 별도 승인된 작업에서만 진행한다.

현재 실제 wire·MQL·SPECIAL별 전체 전략 회귀·thread race의 미검증 목록은 상위 [테스트 매트릭스](../REGRESSION_TEST_MATRIX.md)를 따른다.
