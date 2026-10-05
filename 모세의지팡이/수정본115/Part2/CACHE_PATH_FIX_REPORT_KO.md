# WinError 3 캐시 경로 길이 수정 보고서

## 기준본

- 기준 ZIP: `BACKTEST_fixed_optimized.zip`
- 기준 ZIP SHA-256: `9e1935779e02f535955112b258eded9231aa49adcc24005cd1c0a6068dc471b2`
- 전략/계산 로직은 기준본 그대로 유지하고 `generic_backtest/fast/disk_cache.py`의 계산 캐시 저장 경로만 수정했다.

## 수정 파일

1. `generic_backtest/fast/disk_cache.py`
   - 기준 SHA-256: `10b726bffe138af6089e2ec635ea07fc508f3fb9d0c22f42f70655bc794edcbd`
   - 수정 SHA-256: `6655f9c4c998756fd465a91a58a10e97ac96a5df8086f8cd01887d4ad57b88bf`
2. `validation_suite/test_cache_paths.py`
   - 신규 검증 파일
   - SHA-256: `67d06c72df63810196705b86992576ccd3932383b9aeae37fd4f9ed345485cf9`

PRICE / RSI / STO / DI 계산식, OZ 상태기계, WATCH, Alert, 진입/청산, SL/TP, 평가 순서 코드는 수정하지 않았다.

## 원인과 수정 방식

기존 계산 캐시는 다음 구조를 사용했다.

```text
<root>\.building-<64 hex cache key>-<32 hex UUID>\<64 hex stream key>\00000000.json.gz
```

사용자가 보고한 설치 위치를 기준으로 계산하면:

- cache root 길이: 107자
- 기존 임시 stream 디렉터리: 280자
- 기존 임시 block 파일: 297자
- 기존 최종 block 파일: 254자

Windows의 일반 경로 처리에서 임시 디렉터리 생성 단계가 먼저 한계를 넘어 `WinError 3`가 발생했다.

수정 후 새 캐시는 SHA-256 256비트를 잘라내지 않고 URL-safe Base64로 완전 인코딩한다.

```text
<root>\.building-<22-char UUID token>\<43-char full-SHA256 token>\00000000.json.gz
<root>\<43-char full-SHA256 cache token>\<43-char full-SHA256 stream token>\00000000.json.gz
```

동일 설치 위치 기준:

- 수정 후 임시 stream 디렉터리: 184자
- 수정 후 임시 block 파일: 201자
- 수정 후 최종 block 파일: 212자
- 기존 legacy 캐시를 읽을 때의 기존 최종 block 경로: 최대 254자

43자 token은 SHA-256 32바이트 전체를 Base64URL로 표현한 것이므로 truncation이 없고 기존 SHA-256보다 새로운 충돌 가능성을 추가하지 않는다. manifest에는 원래 64자리 `cache_key`와 64자리 stream identity를 그대로 보존한다.

## cache identity / checksum / manifest

- `self.key`는 기존 64자리 SHA-256을 그대로 사용한다.
- stream dictionary key도 기존 64자리 SHA-256을 그대로 사용한다.
- block payload SHA-256 검증은 기존과 동일하다.
- manifest 전체 identity 검증은 기존과 동일하다.
- 신규 manifest에는 전체 64자리 `cache_key`를 추가로 기록한다.
- 수치 계산 source fingerprint인 `calculation_version()`은 이 저장 경로 전용 변경 때문에 불필요하게 바뀌지 않도록 기준본의 `disk_cache.py` source hash를 storage-only compatibility 값으로 고정했다.
- 실제 확인값: 기준본과 수정본 `calculation_version()` 모두 `6bd9699565b7dcbc63a1cb76ce183c86e9311f03288d8d176794593d39404ab8`.

## 기존 캐시 호환성

기존 64-hex 디렉터리와 기존 64-hex stream 폴더 형식도 reader에서 계속 허용한다.

실제 검증에서 기준본이 작성한 legacy cache를 수정본 프로젝트에 복사한 뒤 같은 WATCH를 실행했고:

- cache status: `HIT`
- hits: 2,886
- misses: 0
- cache key: `ad04d24bd93072ec1f31cf0e6b448a05f9e15269781db621b4723b4e0502393e`
- event order: 기준본과 exact match
- strategy result manifest files: 기준본과 exact match

## 원자적 전환과 실패 정리

- 임시 디렉터리는 `.building-<22-char token>` 형식으로 생성한다.
- 완료 후 `os.replace()`로 최종 compact cache 디렉터리로 원자적으로 이동한다.
- 최종 compact destination이 검증 실패한 상태이면 짧은 `.invalid-<22-char token>`으로 격리한다.
- `finish()` 도중 예외가 발생하면 아직 최종 destination으로 이동되지 않은 `.building-*` 디렉터리를 즉시 제거한다.
- 기존 runner의 `abort()` 정리도 그대로 유지한다.

강제 `os.replace()` 실패 테스트에서 임시 `.building-*` 디렉터리가 남지 않는 것을 확인했다.

## 동일성 검증

### WATCH 1일 합성 raw, 1m 최하위 TF, disk cache ON

- raw 입력: 동일
- 기준/수정 cache key: 동일
- event sequence: 1,440 / 1,440 exact match
- result manifest의 전략 결과 파일 hash map: exact match
- alert population hash: exact match

### TRADE 24시간 합성 raw, TICK, disk cache ON

- event sequence: 823 / 823 exact match
- result manifest의 전략 결과 파일 hash map: exact match
- alert population hash: exact match
- entry population hash: exact match

`generic_manifest.json`과 `completion.json` 자체의 파일 SHA-256은 exact match 대상에서 제외했다. 두 파일은 실행 시각, I/O 계측 시간, 프로젝트 절대경로, source provenance hash를 기록하므로 소스 파일이 실제로 수정된 이번 작업에서는 당연히 달라진다. 그 내부의 전략 결과 파일 hash map은 exact match였다.

## 검증 결과

| 검증 | 결과 |
|---|---|
| SHA-256 전체 비트 보존 compact token | PASS |
| 사용자 보고 Windows 경로 길이 계산 | PASS |
| compact cache 생성 / replay | PASS |
| 긴 실제 디렉터리에서 cache 생성 / replay | PASS |
| 기존 64-hex legacy cache replay | PASS |
| finish 실패 시 `.building-*` 제거 | PASS |
| Gate / IPC / Percentile / Startup 기존 테스트 | 103 PASS |
| OZ 기존 테스트 | 50 PASS |
| 이번 path fix 신규 테스트 | 6 PASS |
| 그룹 실행 전체 assertion | 159 PASS / 0 FAIL |
| WATCH 결과/event 동일성 | PASS |
| TRADE 결과/event 동일성 | PASS |
| Windows 실기기 NTFS에서 직접 실행 | SKIP |

## PASS / FAIL / SKIP

- PASS: 코드 수정, cache 생성, cache 재사용, legacy cache 호환, 실패 정리, 전략 결과 동일성, event order 동일성.
- FAIL: 검증 assertion 실패 없음.
- SKIP: 현재 검증 환경이 Linux이므로 실제 Windows NTFS/Win32 API에서의 GUI 백테스트 실행 자체는 수행하지 못했다. Windows 경로 문자열 길이 계산과 동일 구조의 긴 실제 디렉터리 생성/replay는 수행했다.

## 결론

이번 변경은 계산값을 줄이거나 생략하는 최적화가 아니라 계산 캐시의 파일시스템 이름만 짧게 만든 수정이다. 새 cache write 경로의 최대 길이는 사용자 보고 설치 위치 기준 297자에서 212자로 감소하며, 기존 full SHA-256 identity와 checksum 검증 의미는 유지한다.
