# Part1 전략 설정 화면 정리

- LIVE 전략 설정 창을 메인과 같은 860×720 크기의 다크 대시보드로 만들었다. SPECIAL 7개를 2열 카드로 배치했으며 최소 크기 740×650에서도 조작 영역이 잘리지 않게 확인했다.
- 카드의 트리거명을 크게 표시하고 클릭·키보드로 기존 트리거 선택 창을 연다. 별도 트리거 버튼과 `코드 기본값` 요약 문구는 제거했다. 거래시간 버튼은 카드 오른쪽 아래에 배치했다.
- SPECIAL 상태는 카드와 같은 배경 위에 상태점·문구만 표시한다. 클릭하면 기존의 SPECIAL 통합 로그 창을 연다.
- LIVE 트리거 선택 창의 하단은 `기본값 / 저장 / 취소` 한 줄, LIVE 거래시간 창은 세 세션·시작-종료 입력만 표시한다. 두 창의 기존 선택·저장 콜백은 유지했다.
- Part2에서 사용하는 설정 창은 기존 레이아웃과 문구를 유지하도록 LIVE 전용 표시 옵션을 분리했다. 설정 저장 형식, 상태 감시, 엔진 실행·종료, 전략 판정은 변경하지 않았다.

## 실제 화면

- `검증결과/strategy_settings_actual.png`
- `검증결과/strategy_trigger_actual.png`
- `검증결과/strategy_hours_actual.png`

## 검증

- 신규 UI 시험 2개: 창 크기·2열 배치·축소 시 잘림 없음·트리거명 클릭·거래시간·저장 결과, LIVE 전용 하위 창과 Part2 기존 창 분리 통과.
- 기존 SPECIAL 상태 클릭→통합 로그 이동 시험 통과. 기존 Part1 메인 Canvas UI 시험 3개 통과.
- 변경 소스 3개 AST 구문 확인 통과. 소스 무결성 체인 `61-live-strategy-settings-ui`와 `build/part1_immutable_sha256.json` 갱신. 무결성 진단 6건은 이전 수정본31에 이미 있던 항목과 동일하다.
- 실제 텔레그램 전송이나 전체 시작·종료는 실행하지 않았다.

## 수정 파일

- `Part1/program/strategy_settings_view.py` (신규)
- `Part1/program/special_ui.py`
- `Part1/program/modern_widgets.py` (전략 카드용 선택 인자 추가, 기존 기본값 유지)
- `tests/test_strategy_settings_ui31.py` (신규)
- `tests/test_module_status_navigation.py` (상태 버튼의 새 Canvas 표현에 맞게 검사 대상을 변경, 로그 이동 검사 유지)
- `Part1/audit/remediation/61-live-strategy-settings-ui/changes.json`
- `build/part1_immutable_sha256.json`
