# 수정본35 — Part2 전략 설정 화면을 Part1 새 화면으로 통일

## 원인
- `special_ui.strategy_dialog()`는 `live=True`일 때만 새 화면(`strategy_settings_view.live_strategy_dialog`)을 열었다.
- Part2 `BACKTEST CONTROL`은 `live` 없이 호출하므로 예전 ttk 화면이 떴다.

## 변경
- `Part1/program/strategy_settings_view.py`
  - 같은 창에 `live` 인자를 추가했다(기본 True, Part1은 기존과 같다).
  - `live=False`(Part2)일 때 달라지는 것:
    - 창 제목 `백테스트 전략 설정`, 부제 `BACKTEST STRATEGY SETTINGS`
    - LIVE 기록 폴더 버튼 없음
    - 하단 문구 `Part2 전용 설정 · Part1 실전 설정은 바꾸지 않습니다.`
    - 저장값이 없는 전략은 체크 해제로 시작한다(예전 Part2 화면과 같다).
  - 카드·토글·트리거 이름 클릭·거래시간 버튼·저장/취소는 Part1과 같다.
  - 상태 표시 버튼은 Part1처럼 진단 폴더가 있을 때만 나온다. Part2는 넘기지 않으므로 나오지 않는다.
- `Part1/program/special_ui.py`: `strategy_dialog()`는 두 모드 모두 새 화면을 연다. 예전 ttk 화면 코드는 삭제했다.
- 저장하는 설정 형식(`enabled/trigger/time_filters`)과 Part2 실행 입력은 바꾸지 않았다.

## 추가 — 모드별 제목 (사용자 요청)
- 창 안쪽 큰 제목과 창 제목을 모드별로 다르게 표시한다: Part1은 `라이브 전략 설정`, Part2는 `백테스트 전략 설정`.
- 두 창의 캡처: `검증결과/part2_settings_ui/live_dialog.png`, `part2_dialog.png`
- 무결성 `69-settings-window-titles` 등록. 새 오류 0건.

## 검증
- `tests/test_strategy_settings_ui31.py::test_part2_uses_same_modern_window_without_live_parts`(신규)
  - 제목과 부제, 기록 폴더 버튼 없음, 카드 7개, 기본 해제, Part2 문구
  - 체크 1개 후 저장하면 SPECIAL1=True, SPECIAL2=False
- 신규 시험과 기존 LIVE 화면 시험을 각각 3회씩 따로 실행했다: 모두 통과.
  - 여러 파일을 한 프로세스에서 연속 실행하면 Tk 초기화 오류(`tcl_findLibrary`, `Can't find a usable tk.tcl`)가 무작위로 난다.
  - 수정본34에서도 같은 현상이 있다. 이 PC의 Tk 환경 문제다.
- 실제 창 캡처: `검증결과/part2_settings_ui/part2_dialog.png`
- 무결성
  - `Part1/audit/remediation/67-part2-settings-ui` 등록
  - 앞서 재컴파일한 EA 바이너리를 `68-ea-compiled-build`로 등록
  - 새 오류 0건, 기존 6건 유지
