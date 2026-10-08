# 비교측정시험

상시 검사(`tests`, `Part3/tests`, `verification`의 공식 목록)에서 뺀 시험을 모아 둔 폴더입니다. 수정본162에서 정리했습니다.

- 과거 버전 비교: 지금 파일을 옛 수정본 폴더의 파일과 비교합니다.
- 속도 측정: 결과가 아니라 걸린 시간을 봅니다.

기본 시험 실행(`pytest.ini`)과 `verify_current.py`는 이 폴더를 실행하지 않습니다. 필요할 때만 직접 실행합니다.

```
set MOSES_COMPARE_ROOT=<옛 수정본 폴더들이 있는 폴더>
python -X utf8 -B -m pytest -q -p no:cacheprovider 비교측정시험
```

`MOSES_COMPARE_ROOT`를 지정하지 않으면 옛 수정본과 비교하는 시험은 건너뜁니다. 프로젝트는 기본 실행에서 자기 루트 밖을 읽지 않습니다.

| 파일 | 내용 | 옮겨 온 곳 |
|---|---|---|
| `test_ea_live_block_past.py` | EA의 라이브 실행부(`int OnInit()`부터 끝까지)가 수정본23·수정본34와 같은지 | `tests/test_part2_event_runner.py`, `tests/test_symbol_registry.py` |
| `test_summary_baseline108.py` | AI 핵심요약 카드가 수정본108 화면보다 낮고 편집 칸이 적은지 | `tests/test_summary109.py` |
