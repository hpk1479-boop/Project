# LIVE 알림 일일 기록 — 수정본33 추가 작업

이번 작업은 수정본33에서 이어서 수행했다. 실제 텔레그램 전송은 하지 않았다. 전략 판정식·EA·Wire·녹화 형식은 변경하지 않았다. 새 증거는 `검증결과/live_daily_partition_virtual/`에 저장했다.

## 사용법

LIVE 전략 설정 창의 맨 위 헤더 오른쪽 아래에 작은 디스크 아이콘을 추가했다. 클릭하면 다른 드라이브를 포함한 폴더를 선택할 수 있다. 추천 위치는 프로젝트 루트의 `LIVE_기록`이며, 실제 선택 전에는 자동으로 기록을 시작하지 않는다. 수정본 코드 패키지 안은 선택할 수 없다.

아이콘에 마우스를 올리면 현재 폴더를 표시한다. 미지정이면 흐린 색과 `기록 폴더 미지정`을 표시한다. 전략 설정의 저장/취소와 별개로 폴더 선택 즉시 컴퓨터별 위치 설정을 저장한다. 이미 새 코드를 실행 중이면 다음 알림부터 변경된 위치를 읽는다.

사용자의 후속 지시에 따라 LIVE 기록 루트는 컴퓨터별 루트로 취급한다. 위치는 이 컴퓨터의 `%LOCALAPPDATA%/THE_STAFF_OF_MOSES/roots.json`의 `live_records` 항목에만 저장한다. 코드·공용 config·CSV에는 절대 경로를 저장하지 않는다. 이 로컬 위치 파일은 프로젝트 복사/배포 대상이 아니다. 다른 컴퓨터에서는 디스크 아이콘으로 그 컴퓨터의 폴더를 다시 선택한다. 기존 Part2 창고 위치 설정은 수정하지 않았다.

폴더를 미지정한 경우 시작 시 한 번 안내하고 기록하지 않는다. 선택한 폴더가 없어지거나 접근/쓰기 오류가 나면 그 선택에 대한 기록을 중지하고 한 번 안내한다. 알림 전송은 계속한다. 폴더를 다시 선택하면 기록을 다시 시도한다. 없어졌던 외부 루트를 임의로 재생성하지 않는다.

## CSV와 출력 정책

파일은 기록 루트 기준 `alerts/YYYY-MM-DD.csv`다. 날짜는 알림 `source_time`의 KST 날짜다. 전송이 자정을 넘겨 끝나더라도 알림이 발생한 날짜 파일에 쓴다.

열은 Part2 alerts.csv의 다음 순서를 그대로 유지하고 마지막에 `delivery_result`를 추가했다. Part2에 이미 있는 B0 열을 중복 추가하지 않았다.

```text
run_id,time_ms,strategy,profile,grade,symbol,tf,direction,trigger,message,
recipient,signal_id,b0_price,b0_time,delivery_result
```

`delivery_result`는 `전송됨 / 대표 정책으로 걸러짐 / 전송 실패 / 토큰 미설정`이다. 수신자마다 한 행을 추가하며 결과가 확정되면 즉시 flush하고 파일을 닫는다. 대표 정책에 걸린 다른 SPECIAL의 신호도 기록한다. 같은 signal_id의 재수신은 기존 전송 receipt에 따라 `전송됨`으로 남고 실제 재전송은 하지 않는다.

김매니저의 판정/선택 정책은 바꾸지 않고 각 출력 분기에서 기록 callback만 호출한다. B0·TF·방향 등 알림 생성의 원본 정보는 호스트의 메모리 관찰자로 전달한다. 전략 함수 본문과 이벤트 payload/ID를 바꾸지 않았다. 알림에 B0가 직접 실린 경우도 보존하며 값이 없으면 빈 칸으로 남긴다. Part1은 Part2를 import하지 않는다. 두 CSV의 열 일치는 시험으로 확인한다.

기록 실패는 김비서 모듈 오류 로그에 남긴다. 기기별 경로가 로그로 유출되지 않도록 오류 종류를 표시하고 폴더 절대 경로는 메시지에 넣지 않는다. 기록 callback이 실패하더라도 알림 전송은 중단하지 않는다.

## 선택적 일일 요약

`Part1/program/config.txt`에 다음 값을 설정하면 활성화할 수 있다. 항목이 없으면 기본 꺼짐이며, 이번 작업에서 사용자 config를 변경하지 않았다.

```text
LIVE_DAILY_SUMMARY_ENABLED=true
```

호스트 출력 worker가 KST 07:00 이후 전날 파일을 읽고 기본 `TELEGRAM_CHAT_ID`로 요약한다. 0건도 보낸다. 07:00 이후 프로그램을 시작하면 그날의 전날 요약을 보충한다. 프로그램이 종료된 동안의 여러 날을 일괄 재전송하는 기능은 없다.

요약의 총 알림 수는 signal_id의 고유 개수다. 수신자별 전송 결과도 별도로 표시한다. 재시도/중복 행은 signal_id·수신자별 마지막 결과로 집계하고, 요약 메시지 자체는 다음 요약의 전략 알림 수에서 제외한다. 실제 전송 성공 후 `daily_summary.json`에 날짜를 기록해 재시작 중복을 막는다. 전송 실패 시 최대 분당 한 번 재시도한다. 기록 폴더가 미지정·사용 불가이거나 실제 HTTP 토큰이 비어 있으면 요약을 전송하지 않는다. 파일에 남지 못한 알림 수를 추정하지 않는다.

## 검증 결과

| 확인 | 결과 |
|---|---|
| Part2와 열 순서 + 전송 결과 열 | 통과 |
| KST 23:59:59 / 00:00:00 파일 분리 | 통과 |
| 대표 정책으로 걸러진 SPECIAL2 기록 | 통과 |
| 전송 실패·토큰 미설정 | 통과 |
| 미지정·유실·쓰기 실패·잘못된 위치 설정 | 한 번 안내, 가짜 전송기 정상 호출 확인 |
| B0 원본 정보 및 알림 자체 B0 보존 | 통과 |
| KST 07:00, 0건 요약, 재시작 중복 방지 | 통과 |
| 폴더 이동 후 재지정 | 기존 CSV에 이어 기록됨 |
| 디스크 아이콘·툴팁·폴더 선택 | 실제 Tk 창 실행 확인 |
| 기존 공유 OZ 출력 관련 시험 | 통과 |

`tests_final.xml` 29건 통과, 이후 B0 보강의 `b0_final.xml` 2건 통과(그중 1건은 재확인)다. 서로 다른 현재 시험은 30건이다. 초기 위치 제한이 프로젝트 루트 전체를 코드로 판단하던 문제는 실제 패키지 기준으로 수정했다. 실패 원본 `tests.xml`과 수정 확인 `root_fixed.xml`은 보존했다.

스크린샷: `검증결과/live_daily_partition_virtual/live_folder_unset.png`, `live_folder_selected.png`. 폴더 선택 시험은 테스트용 컴퓨터 설정 파일을 사용했으며, 사용자의 실제 로컬 설정을 바꾸지 않았다. 모든 전송기는 가짜였고 시험 네트워크를 차단했다. 실제 07:00까지 기다리거나 실제 텔레그램으로 보내는 시험은 하지 않았다.

## 수정 파일과 무결성

- 신규: `Part1/program/machine_roots.py`, `live_alert_recording.py`, `live_record_folder_ui.py`
- 수정: `Part1/program/manager_KIM.py`, `event_host.py`, `strategy_settings_view.py`
- 신규 시험: `tests/test_live_daily33.py`
- 검증 도구: `build/inspect_live_record_ui33.py`, `build/register_live_daily33.py`

Part1 해시 체인 `64-live-daily-records`에 6파일을 등록하고 `build/part1_immutable_sha256.json`을 갱신했다. 기존 무결성 오류 6건은 그대로이며 신규 오류는 0건이다. EA·Wire·SPECIAL 판정·config 등 보호 파일 24개는 기존 SHA256과 같다. 소스 차이와 해시 증거는 이번 검증 폴더의 `source_changes.diff`, `integrity.json`에 있다.
