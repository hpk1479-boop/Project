# 수정본27 — Part1·Part2 UI와 최종 알림 시간 슬롯

## 완료 범위
수정본26 전체를 독립 복사했다. 이전 수정본 5,774개 파일 SHA256 변경 0건. 사용자 config, EA/Wire, 지표·판정식·임계값, 데이터 구축·재생·병렬 알고리즘은 유지했다. 실제 Telegram/MT5 실행은 하지 않았다.

## SPECIAL 병합
제공 파일의 슬롯 import 4줄과 최종 시간 확인 4줄만 적용했다. SPECIAL1·2·3·4·6·7은 제공 파일과 바이트 동일하다. SPECIAL5는 수정본26 원본에 정확히 8줄만 추가했고 정렬 및 CRLF를 유지했다. 증거: `검증결과/ui_slots/slot_merge.json`, `SPECIAL5_slots_only.diff`.

## 화면과 사용법
- Part1: 기존 시스템 컨트롤 → 전략 설정 → 실행 체크, 트리거 설정, 거래시간. 다음 엔진 시작 시 적용한다.
- Part2: `BACKTEST CONTROL.pyw` → SPECIAL/WATCH 모드 선택. SPECIAL에서는 전략 설정 창에서 여러 전략을 고른다. WATCH에서는 기존 감시 명령과 결과 수신자 chat_id를 입력한다.
- 두 모드의 입력은 유지하고 실행에는 현재 모드만 전달한다. SPECIAL/WATCH 대상과 BAR/TIMER 재생 방식을 별도로 표시한다. 여러 SPECIAL은 기존 한 번의 재생에서 함께 실행한다.
- 날짜 기본값은 실행 당일 UTC / 달력상 1개월 전이며 월말·윤년을 처리한다. 종료일 미포함은 그대로다. 날짜 직접 입력과 달력 선택·취소를 지원한다.
- 시나리오 JSON과 공식 전략 ID 입력 UI는 제거했다. 내부 시나리오·등록 경로는 유지한다. ‘창고 루트’ 표시는 ‘데이터 폴더’로 바꿨다.
- 기존 데이터 확인·구축 승인·재구축·진행률·로그·결과 보기는 유지한다. 성능/녹화 작업은 추가하지 않았다.
- 트리거는 공용 `oz_profiles.PROFILE_KEYS`의 16개 중 하나를 라디오로 선택한다. 기본값은 SPECIAL 소스의 선언을 읽는다.
- 거래시간 창은 **KST 최종 알림 시간**임을 표시한다. 세션별 사용/시작/종료를 보존한다. 기본값(None)과 사용자 전체 해제(빈 세션 객체)를 구분한다. setup·SPECIAL3 OPENING·SPECIAL4/5 setup TIME_FILTERS는 건드리지 않았다.
- WATCH 거래시간 버튼은 사용자 정정에 따라 **비활성화**했다. WATCH 시간 설정을 저장하지 않으며, 저장 후 실행 오류 방식도 사용하지 않는다.

## 설정 연결과 분리
Part1 `special_settings.json`의 전략별 `trigger`·`time_filters`를 다음 시작 환경의 `OZ_SPECIAL_TRIGGERS`·`OZ_SPECIAL_TIME_FILTERS`로 전달한다.
Part2는 별도 `Part2/backtest_ui.json`에 모드별 설정을 저장하고, 내부용 `Part2/backtest_ui_scenario.json`으로 기존 plan/run 경로를 호출한다. 화면에서 JSON 파일을 요구하지 않는다. Part2는 `special_time_filters`를 생성자 입력으로 명시하며 비어 있어도 Part1 환경을 상속하지 않는다. 엔진별 격리 슬롯 모듈을 사용해 동시 설정이 섞이지 않는다.
시각은 제공 슬롯의 domain_clock으로 판정한다. 과거 재생에 PC 현재 시각을 사용하지 않는다. 공용 TimePolicy의 의미를 바꾸지 않았다.
실행 기록(DuckDB runs 메타데이터)과 결과 `result.json`의 `applied_special_settings`에 전략별 적용 트리거, 시간 설정/기본값 여부, KST 및 기존 세션 설정을 남긴다. 알림 CSV와 같은 실행 ID의 결과 JSON으로 연결되며 CSV를 사후 필터링하지 않는다.
데이터 폴더는 기존 설정/폴더 선택 경로를 이용한다. 새 UI 설정 및 시나리오에는 절대 데이터 경로를 저장하지 않는다.

## 검증
관련 로직/UI/명령·출력 시험 **29 PASS** (`final_tests.xml`). 실제 Tk에서 단일 라디오 16개, 달력 취소, 모드 전환, WATCH 비활성화를 확인했다. Part1/Part2 분리 저장, 환경 격리, 전체 해제, 세션 해제, 시간 변경, 포함 경계와 자정 통과, 원본 8줄 diff를 확인했다.
거래시간 override를 넣은 합성 240초는 LIVE 파이프 수신 경로와 재생 경로에서 240묶음·5,794신호·19알림, 신호 해시·출력·오류가 같다. 네트워크는 차단하고 출력은 테스트 대체 전송기를 사용했다.
설정 없음: 보존 XAU 첫 주(2025-09-01~08 미포함)를 SPECIAL별 단독 재생했다. 수정본26 완료 결과와 알림 시각·방향·문구·수신자·ID·순서가 모두 같다.

| 전략 | 수정본26 | 수정본27 | 결과 |
| --- | --- | --- | --- |
| SPECIAL1 | 6 | 6 | 일치 |
| SPECIAL2 | 6 | 6 | 일치 |
| SPECIAL3 | 0 | 0 | 일치 |
| SPECIAL4 | 0 | 0 | 일치 |
| SPECIAL5 | 4 | 4 | 일치 |
| SPECIAL6 | 7 | 7 | 일치 |
| SPECIAL7 | 0 | 0 | 일치 |

0건 전략은 양성 알림 증거로 해석하지 않는다. 각 SPECIAL 시간 슬롯의 발생/비발생 경계 시험은 별도로 수행했다.
무결성 체인 `Part1/audit/remediation/46-ui-time-slots` 등록, `build/part1_immutable_sha256.json` 재생성. 새 무결성 오류 0건, 남은 기존 오류 6건은 `검증결과/ui_slots/integrity.json`에 보존했다.

## 별도 승인 필요
1. **WATCH 거래시간 슬롯**: COMPOSER 감시 알림 경로의 요청별 시간 입력/전체 해제 연결. 현재 버튼 비활성화, 설정 미저장이다.
2. **SPECIAL8 이상 및 Test_SPECIAL 실행 연결**: 폴더 자동 검색과 번호순 표시/연구용 구분은 구현했다. 현재 실행 로더와 의존 선언은 SPECIAL1~7만 지원하므로 나머지는 미지원 이유를 표시하고 선택을 비활성화한다. 실전 자동 목록에는 Test_를 넣지 않는다. 기존 저장값으로 미지원 전략 실행을 요청해도 오류를 명시하며 조용히 제외하지 않는다. 새 전략 로더/의존성 확장은 수행하지 않았다.

## 수정 파일
- `Part1/OZ_SYSTEM CONTROL.pyw`
- `Part1/program/SPECIAL/SPECIAL1.py`
- `Part1/program/SPECIAL/SPECIAL2.py`
- `Part1/program/SPECIAL/SPECIAL3.py`
- `Part1/program/SPECIAL/SPECIAL4.py`
- `Part1/program/SPECIAL/SPECIAL5.py`
- `Part1/program/SPECIAL/SPECIAL6.py`
- `Part1/program/SPECIAL/SPECIAL7.py`
- `Part1/program/event_application.py`
- `Part1/program/special_settings_model.py`
- `Part1/program/special_time_slot.py`
- `Part1/program/special_ui.py`
- `Part2/event_backtest/gui.py`
- `Part2/event_backtest/runner.py`
- `Part2/event_backtest/ui_model.py`
- `build/connect_live_slot_ui.py`
- `build/finalize_ui_slots.py`
- `build/setup_ui_slots.py`
- `build/split_ui_metadata.py`
- `build/ui_week_case.py`
- `build/verify_ui_behavior.py`
- `build/verify_ui_week.py`
- `tests/test_ui_slots.py`
- `AGENTS.md.txt` (이번 범위·정정 지시 기록)
- `build/part1_immutable_sha256.json`
- `Part1/audit/remediation/46-ui-time-slots/`
- `검증결과/ui_slots/` (비교·시험·보존 증거)
