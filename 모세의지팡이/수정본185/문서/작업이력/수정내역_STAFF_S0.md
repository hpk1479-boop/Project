# 수정본7 — STAFF 책임 분리 S0 기준선

## 범위

사용자 지시에 따라 수정본6의 지정 문서 전부를 읽고 1,635개 파일을 전체 복사했다. 최초 복사 직후 파일별 SHA256을 모두 대조했다. 수정본7은 이전 폴더에 덮어쓰는 패치가 아니다.

설계서의 단계별 별도 수정본 원칙에 따라 이번 범위는 **S0**이다. S1 함수 이전, S2 Snapshot 교체, S7 원비 계산 출처 변경, EA 수정은 수행하지 않는다. 전략 조건과 Part1 운영 소스는 변경하지 않는다. 따라서 Part1 운영 소스 해시 체인 및 Part2 포팅 계약을 새 값으로 덮어쓰지 않는다. 기존 무결성 실패를 이번 변경의 승인 기록으로 숨기지도 않는다.

## 추가 도구

- `Part2/staff_golden`: 오프라인 골든 기록·비교·전체 경로 비교·STAFF 성능 측정. 운영 경로에서는 import하지 않는다.
- `Part2/validation_suite/test_staff_responsibility.py`: 골든 검증기의 양성·음성 대조 테스트. 부동소수점 1 ULP, dtype, 열 순서, index, 속성, epoch counter, 부호 있는 0, 누락 증거를 검출한다.
- `build/run_staff_s0_regressions.py`: 기존 네 테스트 그룹의 실행 결과·실패를 보존한다.
- `검증결과/staff_s0`: 원본 파일 목록, 복사 검증, 골든, 재생 결과, 성능, 테스트 결과.

## 골든 입력과 비교 범위

1. 2026-09-22 09:00 KST부터 합성 입력 240초, 19개 TF의 전체 wire 및 STAFF PRICE/RSI/STO/DI 응답·SOURCE_HEALTH를 기록한다.
2. 준비된 합성 650봉을 각 TF 시간축으로 옮긴 별도 fixture에서 `EMA/HMA/PRICE/RSI/STO/DI/SUPERTREND/FVG`의 모든 256개 부분집합 × 19개 TF × MA 요청 6종, 총 **29,184개 요청**을 기록한다. 각 TF의 실제 MT5 계산값이라는 뜻은 아니다.
3. MA 요청 6종은 없음, SMA20, WMA23, EMA37, HMA17, 네 MA 동시 요청이다. 임의의 양의 정수 주기를 모두 열거할 수는 없으므로 이 유한 범위를 명시적으로 고정한다. 추가로 SMA651·655행 이력 확대, 새 봉, 여러 TF 및 중복 TF 요청, sigma 3→2.5→3도 기록한다.
4. 응답은 값의 binary 표현, dtype, 열 순서, index, attrs를 포함해 해시한다. DataFrame도 저장하여 `assert_frame_equal(check_exact=True)`로 다시 비교한다. NaN을 0으로 바꾸거나 소수점 반올림하지 않는다.
5. 실행마다 달라지는 `source_epoch`의 세션 UUID 접두사만 정규화한다. 콜론 뒤 counter는 비교 대상이다.
6. 동일 열을 content-addressed 방식으로 한 번만 보관한다. 조합 수만큼 같은 650봉을 중복 저장하지 않는다. SQLite 안의 pickle은 로컬에서 이 도구가 생성한 신뢰할 수 있는 증거 파일만 읽어야 한다.

## 전체 경로 비교

기준본 LIVE / 수정본7 LIVE / 수정본7 BACKTEST를 같은 240초 입력으로 실행한다. 기준본도 수정본7 내부 `baseline_input/Part1`에 별도 복사하고 해시를 대조한 후 사용한다. Part1Runtime은 다시 임시 프로그램 폴더를 만들어 실행한다. 수정본6을 실행 경로로 사용하지 않는다.

- SPECIAL7을 기본 시나리오로 고정한다. `--specials SPECIAL1,...` 옵션으로 이후 단계의 SPECIAL 순환 검증에 재사용할 수 있다.
- Watch 11개: OZ 프로필 6종, FVG 조건→OZ, FVG 생성, MA 조건, 상단·하단 원비 터치.
- 최종 알림, SPECIAL 알림, 텔레그램 문구·가상 시각·수신자, 16개 OZ 프로필 상태, FVG 상태, 김매니저 수신 이벤트와 응답, 저장된 Watch 상태, SOURCE_HEALTH를 비교한다.
- 오프라인 UUID 공급원을 고정해 event ID와 상호 참조도 비교한다. 시장값·전략조건은 고정하거나 대체하지 않는다.
- 텔레그램은 메모리 기록만 한다. 실제 전송과 Gemini 호출은 하지 않는다.
- 등록 안내를 조건 발생으로 세지 않는다. MA·원비 실제 알림과 OZ 후보·FVG 이벤트·SPECIAL 발생을 별도로 검사한다.

## 실제 MT5 검증과 성능의 경계

합성 캡처는 기존 `CaptureWriter`가 MSP2 FULL/ROW 형식으로 생성한 파일이다. 이와 별도로 사용자 지시에 따라 설치된 MT5/MetaEditor를 찾고, 수정본7 내부 독립 portable 터미널에서 **수정본6 원본과 해시가 같은 EA·지표**를 컴파일해 실제 Strategy Tester 캡처를 생성했다. 기존 실행 중인 터미널은 종료하지 않았다.

EA와 PRICE/RSI/STO/DI 모두 컴파일 오류 0·경고 0이다. XAUUSD+ 실틱 모델로 숫자 시각 2026-09-23 12:00:00 이상 12:04:00 미만을 캡처했다. 19개 시간봉, 4,541개 wire 기록, `pipe_*.bin`·`manifest.tsv`·`complete.txt`를 `actual_mt5/capture_1470f4cb1dc04acf8e00abe7f14c40f0`에 보관했다. 원본 export와 사본 SHA256, 헤더·길이·45열·행 수·시각 경계·FULL/ROW 재구성을 검증했다. 실제 관측된 전송 시각은 239개이며 누락된 초를 합성해서 채우지 않았다.

`actual` 명령으로 기준본과 수정본7의 STAFF에 각각 재생한 결과 4,542개 사례와 4,302개 정상 DataFrame이 정확히 일치했다. 기준본의 **일봉 EMA21/50/200 미준비 응답 239개**도 동일하게 보존했다. 이는 정상값으로 보정하거나 무시한 것이 아니다. `actual` 기록 명령은 미준비 응답이 있으면 자료 저장 후 종료 코드 1을 내며, 별도 `compare`는 동일성 통과로 0을 반환한다. 실제 캡처 검증 PASS는 모든 지표가 준비되었다는 의미가 아니다.

골든 기록과 전체 테스트를 동시에 실행한 구간의 wall time은 속도 개선 근거로 쓰지 않는다. `benchmark`는 다른 검증이 끝난 뒤 별도로 실행하며, 입력 생성·파일 기록·해시·프로그램 시작 시간을 제외한 STAFF parser와 요청 처리의 CPU, p50/p95 지연을 세 번 측정한다. OS Named Pipe/ZMQ 전송 지연은 이 오프라인 측정에 포함되지 않는다.

## 재현

설치된 Python 환경에 numpy/pandas/pytest 등 기존 프로젝트 의존성이 필요하다. 실행 위치는 이 완성본의 `Part2`이다. 아래 경로는 모두 수정본7 안에서 해석된다. 기존 결과를 덮어쓰지 않도록 새 출력 폴더 이름을 쓴다.

```powershell
python -X utf8 -B -m staff_golden record --part1 ../검증결과/staff_s0/baseline_input/Part1 --out ../검증결과/staff_s0/repeat_a
python -X utf8 -B -m staff_golden record --part1 ../Part1 --out ../검증결과/staff_s0/repeat_b
python -X utf8 -B -m staff_golden compare ../검증결과/staff_s0/repeat_a/golden.sqlite ../검증결과/staff_s0/repeat_b/golden.sqlite --out ../검증결과/staff_s0/repeat_compare.json
python -X utf8 -B -m staff_golden parity --before ../검증결과/staff_s0/baseline_input/Part1 --after ../Part1 --out ../검증결과/staff_s0/repeat_parity
python -X utf8 -B -m staff_golden benchmark --part1 ../Part1 --out ../검증결과/staff_s0/repeat_benchmark.json
python -X utf8 -B -m staff_golden actual --part1 ../검증결과/staff_s0/baseline_input/Part1 --capture ../검증결과/staff_s0/actual_mt5/capture_1470f4cb1dc04acf8e00abe7f14c40f0 --out ../검증결과/staff_s0/repeat_actual_a
python -X utf8 -B -m staff_golden actual --part1 ../Part1 --capture ../검증결과/staff_s0/actual_mt5/capture_1470f4cb1dc04acf8e00abe7f14c40f0 --out ../검증결과/staff_s0/repeat_actual_b
python -X utf8 -B -m staff_golden compare ../검증결과/staff_s0/repeat_actual_a/golden.sqlite ../검증결과/staff_s0/repeat_actual_b/golden.sqlite --out ../검증결과/staff_s0/repeat_actual_compare.json
python -X utf8 -B -m pytest validation_suite/test_staff_responsibility.py -q
python -X utf8 -B ../build/run_staff_s0_regressions.py
```

실제 MT5 실행 스크립트는 `build/run_staff_s0_mt5.py`이고, 설치 위치·독립 터미널 설정은 `검증결과/staff_s0/mt5_profile.json`, 컴파일 증거는 `actual_mt5/compile.json`, 실행 로그는 `actual_mt5/tester_agent.log`에 있다. 이미 고정한 캡처를 덮어쓰지 말고, 재실행 시 스크립트의 작업 폴더와 세션 출력을 새 경로로 지정한다.

최종 실측 결과와 기존 실패 항목은 `검증결과/staff_s0/결과.md`에 정리한다. 이전 수정내역의 테스트 수·시간을 이번 실행 결과로 재사용하지 않는다. S0 자료 고정 완료와 기존 테스트 전체 통과는 구분한다. 기존 실패는 수정본6의 해시 검증 복사본에서도 재현했으며 운영 소스와 무결성 기록을 임의로 수정하지 않았다.
