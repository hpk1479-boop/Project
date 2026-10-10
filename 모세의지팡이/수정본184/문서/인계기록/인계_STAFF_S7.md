# 수정본14 S7 인계

S7 완료. S8은 시작하지 않았다. `수정내역_STAFF_S7.md`와 `검증결과/staff_s7/status.json`을 먼저 읽는다.

## 정책

최신 AGENTS.md.txt 및 검증정책_S6이후.md 우선. 원비는 MT5 값이 원본이며 이전 Python 결과와 회귀 비교하지 않는다. 원비 변경 알림은 승인된 기준 변경으로 건수만 기록한다. Part2 기존 일반 회귀 4개 묶음은 실행하지 않는다. Part1 audit·루트 tests·part1_host 외부 시나리오·대표 입력·해당 단계 신규 테스트는 유지한다. 전체 게이트는 단계 구현 완료 후 1회, 실패 원인 관련 검사만 재실행한다. Part3 동결, S0–S6 골든/expected 및 성능 정책 불변.

## 현재 계약

- EA v1.70 InpWonbiSigma=3.0. 시작 시 입력만 사용하며 실행 중 변경 기능은 없다.
- Wire v2 기존 구조에 45 upper, 46 lower, 47 sigma 추가. wonbi_mid=기존12열 별칭. 새 schema `0x36C28F68`, 구45열 `0x4D2FA0E9`도 수용한다.
- 생성기: `build/generate_staff_s7_schema.py`. 컴파일: `build/compile_staff_s7_mt5.py`. 과거 S6 증거를 쓰는 이전 생성기는 S7 갱신에 사용하지 않는다.
- STAFF는 numpy 수신/검증/48열 Snapshot 저장·전달/health/제어만 한다. Python 원비 계산 없음. v1·구v2 45열은 MT5 3σ 슬롯 복사, 기대 sigma!=3이면 LEGACY_WONBI_SIGMA 오류.
- SET_WONBI_SIGMA는 기대 σ다. mismatch 로그+SOURCE_HEALTH warnings만 추가하고 실제 MT5 값·health 상태를 바꾸지 않는다.
- Part2 OHLCV/틱만으로 Wonbi를 만들지 않는다. MT5_WONBI_REQUIRED면 STAFF MSP3/native 경로를 사용한다. 기존 Python Wonbi 이벤트 캐시는 revision2로 분리했다.
- MT5 계산 포트는 synthetic 생성기 및 audit fixture에서만 허용한다. OHLC transform 뒤에 합성 MT5 원비 열을 완성한다.

## 동결 기준선 및 근거

`검증결과/staff_s7/baseline_S7/manifest.json`을 다음 단계 기준으로 사용한다. 해시 `19933bf3b67773c7943f4ba8e25a668d097acc7925bcf77fbaffdddddcc0f17c`. 240초/실제 XAU/BTC/확대 XAU/BTC LIVE↔BACKTEST 동일, S6 비원비 외부 결과 동일. 최종 알림 14건·조건 충족 전달 29건. 원비 승인 기준 변경 알림: 제거 0, 추가 0.

실제 sigma3/2.5 캡처 114 대표 샘플·57 폭 비교·19 클라이언트 TF 확인, EA/지표5개 0오류0경고, BTC LIVE19TF. 상세 파일은 `mt5_wonbi_verification.json`, `live_BTC/result.json`, `external_behavior_summary.json`.

G1 새 실패0: audit176 PASS+기존1 FAIL, root267 PASS+기존5 FAIL/1 ERROR/6 SKIP, 신규S7 20 PASS. 기존 Part2 일반 묶음은 실행하지 않았다. 실패 수정 내역과 원본/재실행 결과는 보고서 및 `baseline_signature_compare.json`에서 확인한다.

무결성 단위30, 기존진단22개/신규0. 현재불변목록 재생성. 수정본13 원본22,223개 및 S0–S6/Part3/성능정책 해시 불변. 사용자config BTC설정 원문 보존.

성능 참고 각1회: 수신 CPU 비율 1.2500, 바이트 비율 1.0637; 판정없음. S5/S8 성능정책을 바꾸지 않았다. 실행된 MT5는 수정본14의 격리 설치이며 배포용 소스는 Part1/program/MT5에 있다.
