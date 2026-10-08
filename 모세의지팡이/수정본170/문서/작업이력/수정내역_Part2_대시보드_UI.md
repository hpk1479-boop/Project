# Part2 대시보드 UI

## 범위

- `Part2/event_backtest/gui.py`: 기존 단일 세로 입력 화면을 카드형 대시보드로 배치했다. 상단에는 종목·기간·재생 모드와 데이터 경로를 두고, 가운데는 SPECIAL/WATCH와 결과 옵션을 2열로 나눴다. 그 아래 상태, 구축·재생 진행률, 주의 사항·실행 로그·결과 요약을 배치했다.
- 주요 실행 버튼은 기존 `launch` callback에 연결된 Canvas 버튼으로 표시한다. 입력, 모드 전환, 구축 선택, 상세 로그, 결과 보기 callback은 유지했다.
- 최종 기본 창 크기는 1180×810, 최소 창 크기는 1000×770이다. 초기 배치 캡처: `검증결과/part2_dashboard_actual.png`.

## 확인

- 실제 Tkinter 창을 열어 목표 시안의 상단 입력, 중간 2열, 상태·진행률, 하단 3열 구조와 간격을 시각적으로 확인했다.
- `tests/test_part2_dashboard_ui31.py`: 실제 위젯에서 입력·실행 버튼 연결, SPECIAL/WATCH 전환, WATCH 거래시간 비활성, 구축 체크박스 연동, 최소 창 크기의 카드 배치를 확인했다. 1개 시험 통과.
- 수정본30과 수정본31의 `start_process`, `save_controls`, `launch`, `show_result`, `detail`, `idle`, `poll`, `close`, `apply_special`, `change_symbol`, `build_changed` 함수 AST가 모두 동일하다.
- 백테스트 실행과 텔레그램 전송은 하지 않았다. Part1, 전략 판정, EA, 설정 저장 형식은 변경하지 않았다.

## 변경 파일

- `Part2/event_backtest/gui.py`
- `tests/test_part2_dashboard_ui31.py`
- `검증결과/part2_dashboard_actual.png`

## 최종 시각 polish

- 승인된 카드 배치를 유지하면서 입력칸 높이와 라벨 간격을 늘렸다. 대상 선택 카드의 높이를 줄이고 선택 내용을 세로 중앙에 놓았다.
- 진행률 막대는 16px 두께와 선명한 파란색 스타일을 사용한다. 하단 카드와 기본 창 높이를 줄여 비어 보이는 면적을 덜었다.
- 상세 로그 보기와 결과 보기는 Part1의 `ModernButton` 보조 스타일을 그대로 사용한다. 두 버튼 모두 hover·pressed·disabled 상태를 갖고 기존 callback에 연결된다.
- 실제 화면 캡처: `검증결과/part2_dashboard_polished.png`. 최소 1000×770에서 카드 경계를 확인했다.
- `tests/test_part2_dashboard_ui31.py` 1개 통과. 위 11개 실행·저장·상태 처리 함수의 AST는 수정본30과 동일하다.

## 메인 UI 승인 후 고정 polish

- 대상 선택 카드만 208px에서 200px로 낮췄다. 오른쪽 결과 옵션 카드와 실행 버튼 크기는 유지했다.
- 진행률 막대의 두께를 16px에서 18px로 늘렸다.
- 상단 제목과 설명의 가로 간격을 16px에서 22px로 늘렸다.
- 최종 실제 화면은 `검증결과/part2_dashboard_final.png`에 남겼다. UI 연결 시험 1개가 통과했다.
