# 수정본13 S6 인계

S6 최종 판정: **완료**. `수정내역_STAFF_S6.md`와 `검증결과/staff_s6/status.json`을 먼저 읽는다. S7 이후는 시작하지 않았다.

수정본12는 S5 사용자 승인 예외 기록 후 전체 복사했고 원본 18,853개 파일의 불변을 확인했다. S5 원래 실패·표본을 보존했다. S0–S5 골든·expected·동결 성능 정책은 수정하지 않았다. Part3는 레거시 동결이며 참조 정리는 S8 이후 별도 작업이다.

## 최신 정책

`검증정책_S6이후.md`와 `AGENTS.md.txt`의 사용자 최신 지시가 우선한다. 외부 전략 조건·상태 전이·알림 발생/시각/방향/문구/수신자·source health/stale/reconnect/seq 의미·LIVE↔BACKTEST를 100% 보존한다. 내부 DF 비트/캐시/private/set/구현 차이는 진단이다. 판정 입력은 대표 샘플만 확인하며 대규모 내부 조합 반복을 하지 않는다. 의도된 원비/이벤트 시점 변경은 별도 승인과 달라진 알림 목록·원인을 기록한 뒤 기준선을 고정한다.

## 현재 구현

EA 기본 v2, v1도 선택 가능하다. Wire 45열은 동일하며 FULL/ROW/HEARTBEAT, HELLO/ACK, TF BUNDLE, CRC/schema 검증을 지원한다. 상세 형식은 `Wire_v2_명세.md`. 생성기는 `build/generate_staff_wire_schema.py`; schema `0x4D2FA0E9`, 빌드 `1f94beda8ca91693e556bd7a1f21fa19da288e3b9f70f70435d297c77142b7f5`. EA/지표 변경 후에는 헤더 생성 → 전체 compile → build 해시 확인 순서로 작업한다.

STAFF는 numpy 수신/검증/불변 Snapshot 전달/제어만 한다. 원비는 staff_compat Python 계산 그대로다. payload CRC 손상과 잘린 프레임은 부분 게시하지 않는다. ROW/HB는 epoch를 올리지 않는다. 누락은 `logs/pipe_gaps_<session>.jsonl`에 재생 가능한 범위 기록으로 남는다. 수신 프레임 coalescing은 없다. 클라이언트 ZMQ SNAPSHOT 식별자 `staff-wire-v1-45`는 바꾸지 않았다.

MSP2와 MSP3를 모두 지원하며 SecondFeed가 모든 레코드를 재생한다. 새 캡처가 없는 초의 liveness heartbeat와 seq offset 규칙을 다음 단계에서 임의로 바꾸지 않는다.

## 사용자 설정

사용자가 수정본13의 config.txt를 직접 수정했다. STAFF_ALLOWED_SYMBOLS와 TARGET_SYMBOLS는 모두 `XAUUSD+,NAS100, BTCUSD`다. 공백은 parser가 제거한다. 에이전트가 파일을 다시 쓰지 않았으며 실제 설정으로 세 종목의 STAFF FRESH와 개인명령 등록을 확인했다. 근거: `user_symbol_validation.json`, 무결성 단위 29.

## 검증 인계

Part2 기존 824개 + S6 신규 14개 = 838개 수집. 정책상 내부 600조합 1개는 미실행 진단으로 남겼다. G1 새 외부 회귀 없음. 내부 EA/STAFF AST 고정 4개 및 자정 checkpoint set 순서 1개는 원래 실패 기록을 유지하고 정책 진단으로 구분했다. audit 로딩 순서와 전역 임시폴더 간섭은 원인별 관련 테스트만 다시 실행했다. 기존 루트 불변 목록 검사의 Windows 경로 구분자 실패는 그대로 남기고 실제 파일 집합/해시는 immutable_verify.json에서 확인했다. 전체 게이트를 불필요하게 반복하지 않는다.

240초 S0 기준 + XAU/BTC 확대 + 실제 XAU/BTC 캡처에서 3-way 외부 결과 동일. 기준 경로 합계 최종 알림 14건, 등록 확인 제외 조건 충족 전달 29건. 이 두 수는 중복될 수 있다. 실제 BTC LIVE 31.395초/19TF, EA와 지표 5개 모두 0 errors/0 warnings. 대표 입력 95+S0 실제 18개 비교. 연속 여러 날 전체 검증은 아니며 범위는 보고서 표를 따른다.

성능은 각 1회 참고만 기록했다. 동일 실제 관측 v1 환산 대비 v2 바이트 비율 0.073919, 수신 CPU 비율 0.600000; 공식 판정/허용치 변경 없음. `performance_reference.json`의 경계를 유지한다.

기존 D1 워밍업 극소값, 22개 기준선 무결성 진단, 기존 GUI/파일 누락 실패는 보고서와 raw signature에 있다. 실패를 골든 갱신으로 해결하지 않는다. 알림이 0건이었던 최초 BTC 허용 설정 오류 실행은 유효 비교에서 제외했다. `external_behavior_summary.json`의 configured 시나리오를 사용한다.
