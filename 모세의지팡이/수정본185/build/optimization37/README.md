# 수정본37 검증 도구

운영 코드 변경 내용, 측정 조건, 검증 미수행 항목은 루트 `수정내역_Part2_최적화37.md`에 있습니다.

- `step1_check.py`: 원본과 수정본 ResultWriter 288조합·CSV·내부 상태·거절 신호 시간 비교.
- `step2_check.py`: 실제 파일 해시·캐시·변경 거절·창고 이동 검사. SELECT 반환 행만 대역을 사용하며 DB 통합 검사가 아닙니다.
- `step3_check.py`: 원본/수정본의 실제 워커 루프 AST와 결정적 시각을 사용하는 정지·CPU·월 경계 검사 및 관리 비용 측정.
- `step4_check.py`: STAFF의 실제 판정식·전체 발행·불변화·FULL/ROW·epoch 비교 및 시간 측정.
- `replay_check.py`: 실제 녹화 1개를 실제 run_chunk/STAFF/전략/CSV 경로로 재생. 부모 DB 경로는 실행하지 않습니다.
- `run_regressions.py`: 선택한 기존 회귀 검사. `--new-tests`를 주면 신규 37개 검사만 실행합니다.
- `verify_week.py`: 정상 의존성과 실제 1주 창고 데이터가 있는 환경에서 부모 runner·SQL·CSV까지 비교합니다. 이번 환경에서는 실제 실행하지 않았습니다.

단계별 도구는 `원본프로젝트 수정프로젝트 결과폴더` 순서의 인자를 받습니다. 결과 폴더에는 새 검증 경로를 지정해 제공된 측정 기록을 덮어쓰지 마십시오. `replay_check.py`와 `verify_week.py`는 `--help`에서 인자를 확인할 수 있습니다.

DuckDB가 없을 때 `support.py`는 검증 프로세스에서만 warehouse 모듈의 사용하지 않는 DuckDB import를 생략합니다. ResultWriter와 find_capture 함수 본문은 실제 파일 그대로 사용합니다. 이 상태에서 Warehouse 생성자를 사용하면 명확히 실패합니다. 가짜 DB 엔진이나 운영 의존성 우회 기능은 없습니다.

신규 검사:

```text
python -B build/optimization37/run_regressions.py . "<새 결과 폴더>" --new-tests
```

GUI 검사에는 표시 가능한 디스플레이가 필요합니다. 기존 검사 3건은 첨부 원본에서도 실패하며 자세한 내용은 보고서와 제공된 XML에 기록했습니다.
