# 읽기용 원본 참조본

사용자가 제공한 현재 프로젝트에서 SPECIAL1~7, 관련 PART1 도메인/지표/감시 코드, PART2 백테스트 연결 코드를 복사했습니다. GPT가 Part3 폴더만 받아도 실제 인터페이스와 구현을 확인하기 위한 자료입니다.

`source_manifest.json`은 이 폴더에 복사한 원본 파일의 상대 경로와 SHA256입니다. 해당 파일의 바이트는 수정하지 않았습니다. `tests/test_smoke.py`는 선택 실행 시 이 해시를 확인합니다. 프로그램 실행 때 복잡한 검증이나 잠금 정책을 적용하지 않습니다.

기본 원본 전략: `Part1/program/SPECIAL/SPECIAL1.py`~`SPECIAL7.py`.
실행 계약: `Part1/program/event_composer_domain.py`.
사건/시간/상태: `Part1/program/event_engine/`, `event_composition.py`, `domain_clock.py`, `domain_memory.py`.
PART2 호출: `Part2/event_backtest/`.

이는 운영 환경의 백업/배포 대체본이 아닙니다. 이 자료를 실제 Part1 또는 Part2에 덮어쓰지 마세요. Part3 생성기는 이 참조 코드 중 순수 OZ 프로필 어휘 모듈만 읽고, 전체 원본 전략을 UI 초기화에서 실행하지 않습니다.

계정/API 설정, 텔레그램 토큰 파일, 거래 데이터 창고, 로그, MT5 실행 파일은 복사 대상이 아닙니다. 사용자가 이후 직접 추가한 파일은 GPT에게 공유하기 전에 확인하세요.

## 수정본63 문서 정리

`Part2/START_BACKTEST_KO.md` 사본은 프로젝트의 `Part2/START_BACKTEST_KO.md`와 바이트가 완전히 같아 삭제했다. 보존된 실제 문서는 [백테스트 실행 안내](../../Part2/START_BACKTEST_KO.md)다. `source_manifest.json`에서도 삭제한 사본 항목만 제거했다. 나머지 참조 소스와 해시는 유지했다. 정리 목록은 프로젝트 루트의 `문서/README.md`, 증거는 `검증결과/문서정리63/`에서 확인한다.