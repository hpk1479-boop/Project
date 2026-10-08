# Cache path fix 실패/제약 기록

## 1. 전체 pytest 단일 실행 timeout

- 단계: 회귀 검증
- 테스트명: `python -m pytest validation_suite -q`
- 관련 파일: `validation_suite/*`
- 기대값: 전체 suite 단일 프로세스 완료
- 실제값: 전체 순서 실행에서 장시간 소요되어 timeout. assertion FAIL 메시지는 발생하지 않았음.
- 예상 원인: IPC/OZ/Percentile 대형 검증을 한 프로세스에서 연속 실행할 때 누적되는 실행시간/프로세스 자원 영향. 이번 cache-path 코드의 기능 실패 징후는 확인되지 않음.
- 조치: suite를 기능군으로 분리해 전부 재실행함.
  - `test_cache_paths.py`: 6 PASS
  - Gate/IPC/Percentile/Startup: 103 PASS
  - OZ: 50 PASS
  - 합계: 159 PASS / 0 FAIL

## 2. Windows 실기기 검증 불가

- 단계: 긴 경로 검증
- 테스트명: Windows NTFS/Win32 실제 실행
- 관련 파일: `generic_backtest/fast/disk_cache.py`
- 기대값: 사용자 설치 위치에서 실제 cache 생성/재사용
- 실제값: 현재 실행 환경이 Linux이므로 Windows API 직접 실행 불가
- 예상 원인: 테스트 환경 제약
- 조치: 사용자가 보고한 정확한 Windows root 문자열로 경로 길이를 산출하고, 수정본에서 201/212자로 감소함을 검증. 별도로 200자 이상 실제 디렉터리에서 cache create/replay를 수행함.
- 상태: SKIP
