# 짧은 기간 병렬 실행과 WATCH 의존성 — 수정본33

## 작업 분할

기본 월 작업 수가 worker 수보다 적으면 주 단위로 나눈다. 주 작업 수도 부족하면 일 단위로 나눈다. 각 작업은 기존 거래일 기준 워밍업을 적용하고 워밍업 알림은 출력하지 않는다. 완료한 worker가 다음 작업을 가져가는 기존 ProcessPool 구조를 유지했다.

`--work-size WEEK` 또는 `DAY`를 명시할 수 있다. 비교용 월 단위 고정은 시나리오 `adaptive_chunks=false`, 순차는 기존 `--sequential`을 쓴다. 실제 선택한 분할은 result.json의 `partition`에 기록한다. 날짜 분할을 늘리면 워밍업 중복도 늘며, 이번 작업에서 모든 분할이 더 빠르다고 판정하지 않았다. 1회성 감시의 청크별 재등록 경고는 유지하고 문구의 월 한정을 없앴다.

## WATCH

기존 WATCH 선택은 여러 감시 종류를 지원하려고 넓은 처리기 집합을 구성했다. 이제 시작 명령을 기존 해석기로 등록한 뒤, 아직 시장 입력을 넣기 전에 실제 등록 결과에서 필요한 처리기를 추린다. 별도 명령 해석기나 전략 판정식을 만들지 않았다.

| 등록 내용 | 필요한 경로의 예 |
|---|---|
| 일반 원비·MA·퍼센타일 감시 | WATCH_CONDITIONS + COMPOSER, 해당 Fact 기능 |
| OZ | OZ_STATE/OZ + 기존 SWEEP 의존 + COMPOSER |
| FVG | FVG_STATE/FVG + COMPOSER |
| 추세 조건 | INDICATOR + COMPOSER |
| 연쇄/최종 OZ | 현재 단계뿐 아니라 이후 trigger·해제 trigger·final_action의 의존성까지 포함 |

등록된 공식 조건, FVG 감시의 최종 행동, 지연 연쇄의 미래 단계도 확인한다. 새 종류여서 대응을 확정할 수 없으면 넓은 기존 집합을 유지하고 `conservative_dependencies`로 기록한다. COMPOSER 등록·출력은 항상 남는다. LIVE 경로에는 이 축소 함수를 연결하지 않았다.

## 검증

### XAUUSD+ 2025-09 월 vs 주

같은 보존 BAR 녹화와 SPECIAL1, 워밍업 3거래일로 월 1작업과 주 5작업을 비교했다. 양쪽 **52건**, 시각·방향·문구·수신자·signal_id 등 알림 CSV 필드가 모두 같았다. 차이 목록은 비어 있다. 증거는 `검증결과/stop_virtual_parallel/partition_comparison.json` 및 `partition_MONTH/`, `partition_WEEK/`다.

진단 실행 이력: 시험 도구의 출력 부모 폴더 준비와 외부 녹화 경로의 상대 경로 변환에서 오류가 있었다. 월 작업과 첫 주는 시장 처리 100%와 alerts.csv 저장을 마쳤지만 마지막 result.json 메타데이터 작성이 실패했다. 나머지 주 작업을 포함해 모든 progress.json이 100%인 것을 확인한 뒤, 이미 완성된 CSV를 `--summarize-existing`으로 비교했다. 이 두 작업의 메타데이터 저장 실패를 성공으로 바꾸거나 경과 시간을 만들어 넣지 않았다. 진단 도구의 경로 처리는 수정했다. 실제 공개 실행기의 중단 시험은 정상 창고 루트를 사용해 result.json까지 저장됐다.

위는 SPECIAL1의 한 달을 주 단위로 나눈 확인 결과다. 모든 전략과 일 단위 분할까지 동일하다고 확대 해석하지 않는다. 일 단위 선택·범위·워밍업 연결은 관련 시험으로 확인했다.

### WATCH 로직·LIVE 수신 = 재생

- 일반 감시와 OZ 명령의 실제 등록 및 필요한 consumer 집합 확인.
- 이후 FVG/원비/MA 단계와 최종 OZ를 쓰는 연쇄의 의존성 보존 확인.
- synthetic240에서 같은 `골드 1분 상단 원비 터치 계속 알려줘` 입력을 넓은 LIVE 수신 경로와 축소된 재생 경로로 처리: **알림 2건, ID·시각·내용 포함 동일**. 증거 `watch_parity.json`.
- 초기 시험의 `XAUUSD+ 1분봉 HMA6 위로 계속 알려줘`는 기존 해석기의 정상 등록 문장이 아니었다. 등록되는 위 원비 감시 명령으로 시험 입력을 교체했으며, 실제 등록·의존성 검사는 유지했다. 원시 실패 `tests.xml`은 보존했다. 최종 관련 시험 결과는 `current_logic_tests.xml`이다.

## 수정 파일과 무결성

- `Part2/event_backtest/partition.py` 신규
- `Part2/event_backtest/settings.py`, `runner.py`, `__main__.py`
- `Part1/program/event_watch_selection.py` 신규
- `tests/test_stop_virtual33.py`
- 진단 도구: `build/compare_partition33.py`, `build/watch_parity33.py`

Part1 변경은 `Part1/audit/remediation/63-stop-virtual-watch/changes.json`에 등록했고 `build/part1_immutable_sha256.json`을 갱신했다. 기존 무결성 오류 6건은 전후 그대로이며 신규 오류 0건이다. 기존 오류 목록은 `integrity.json`에 남겼다. EA·schema·config·SPECIAL 등 보호 파일 25개는 수정본32와 SHA256이 같다.
