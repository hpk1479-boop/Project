# 수정본29 — SPECIAL3 장초반 필터 제거 · 최종 알림 거래시간 지정

수정본28을 복사했다(검증결과·캐시 제외). 실제 Telegram/MT5 실행은 하지 않았다.

## 변경
- `Part1/program/SPECIAL/SPECIAL3.py`
  - `final_time_filters`: `("OPENING_ASIA","OPENING_LONDON","OPENING_NEWYORK")` → `()`. 최종 FVG touch 이후 1m/2m OZ 감시가 장초반 밖에서도 등록된다.
  - `FINAL_ALERT_TIME_FILTERS`: 세션 이름 목록 → 전략 전용 시간 사전
    `{"MAIN_ASIA":"0900-1100","MAIN_LONDON":"1600-1800","MAIN_NEWYORK":"2100-2400"}` (KST, 시작·종료 포함). config의 MAIN_* 시간을 쓰지 않는다. `0`이면 24시간.
  - 거래시간 슬롯(전략 설정 화면)에 SPECIAL3 설정이 있으면 슬롯이 우선한다(기존과 같음).
  - 상단 주석의 시간 필터 설명을 현재 동작에 맞게 고쳤다. 알림 제목/전략 이름(장초반 추세 눌림)은 바꾸지 않았다.
- `Part1/program/special_time_slot.py`: `ranges_allowed()` 추가(기존 `_inside`와 같은 판정, domain_clock 사용). `24:00` 입력 허용.
- `Part1/program/special_ui.py`: 코드 기본값이 시간 사전인 전략은 거래시간 창에 그 시간을 기본으로 표시하고, 저장 시 시작/종료를 명시 저장한다(빠지면 config MAIN_* 시간으로 되돌아가는 것을 막음).
- 유지: config의 `OPENING_*` 키(Watch "장초반 …" 명령이 사용), setup `time_filters`, 공용 TimePolicy, 체인 기한 로직.

## Part3
- `reference/Part1/program/SPECIAL/SPECIAL3.py`, `reference/Part1/program/special_time_slot.py`를 새 원본으로 갱신, `reference/source_manifest.json` 해시 갱신.
- `examples/SPECIAL3.json` 재생성. `generated/Test_SPECIAL015.py`(+recipe) 새 번호로 생성. 기존 Test_SPECIAL010은 덮지 않았다.
- 문서: `docs/SPECIAL_1_TO_7.md`, `docs/STRATEGY_SCHEMA.md`, `README.md`.
- Part3 smoke 14 PASS.

## 검증
- `tests/test_special3_time.py`(신규) + `tests/test_ui_slots.py` **39 PASS** (`검증결과/special3_time/tests.xml`).
  - 발생: 09:00, 10:30, 11:00, 16:00, 18:00, 21:00, 23:01, 23:30, 23:59 KST.
  - 차단: 08:59, 11:01, 12:00(메인 아시아 안이지만 SPECIAL3 시간 밖), 15:59, 18:01, 20:59, 00:00, 01:00.
  - config MAIN_* 값과 무관하게 판정됨을 확인. 4개 체인 모두 `final_time_filters == ()`.
  - 슬롯 설정 우선(12:00-13:00 지정 시 12:30 발생, 10:00 차단), 전체 해제 시 알림 없음.
  - 거래시간 창: 09:00/11:00/16:00/18:00/21:00/24:00 표시, 저장값 명시.
  - `test_ui_slots.py`의 "코드 기본값은 항상 허용" 단언은 SPECIAL3만 제외했다(전용 시간이 생겨 의도대로 달라짐).
- LIVE와 재생은 같은 SPECIAL3 플러그인·슬롯 모듈을 쓰고 시각은 domain_clock이므로 판정이 같다.

## 1주 재생 (보존 XAU 2025-09-01~08, SPECIAL3 단독)
| | 수정본27 | 수정본29 |
| --- | --- | --- |
| 알림 | 0 | 0 |
| 최종 OZ 감시 등록 | (장초반만) | 24회(장초반 밖 포함) |

FINAL_ALERT 4건(09-03 16:27, 09-03 21:51, 09-05 11:31, 09-05 13:45 KST)이 나왔으나 **4건 모두 기존 연결 완료 기한에서 만료**되어 시간 판정 전에 걸러졌다(`_filter_config_chain_deadline_event`). 16:27·21:51은 새 시간 안이다. 즉 이번 주 0건은 시간 필터가 아니라 기존 체인 기한 때문이며, 그 로직은 바꾸지 않았다. 증거: `검증결과/special3_time/week_comparison.json`, 재생 결과 `runs/s29_week_special3`.

## 무결성
`Part1/audit/remediation/47-special3-time` 등록, `build/part1_immutable_sha256.json` 재생성(변경 3파일 + 등록 2파일). 새 오류 0건, 기존 오류 6건 유지(`검증결과/special3_time/integrity.json`).

## 수정 파일
- `Part1/program/SPECIAL/SPECIAL3.py`
- `Part1/program/special_time_slot.py`
- `Part1/program/special_ui.py`
- `Part1/audit/remediation/47-special3-time/`
- `build/part1_immutable_sha256.json`
- `build/register_special3_time.py`, `build/special3_week_case.py`
- `tests/test_special3_time.py`, `tests/test_ui_slots.py`
- `Part3/reference/Part1/program/SPECIAL/SPECIAL3.py`, `Part3/reference/Part1/program/special_time_slot.py`, `Part3/reference/source_manifest.json`
- `Part3/examples/SPECIAL3.json`, `Part3/generated/Test_SPECIAL015.py`, `Part3/generated/Test_SPECIAL015.recipe.json`
- `Part3/docs/SPECIAL_1_TO_7.md`, `Part3/docs/STRATEGY_SCHEMA.md`, `Part3/README.md`
