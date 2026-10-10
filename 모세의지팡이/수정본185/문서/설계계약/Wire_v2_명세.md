# STAFF Wire v2 — S7 (기존 v2 구조 유지)

Wire v1의 45열·`<IIqIIII>` 패킹은 유지한다. S7 EA v2는 기존 45열 뒤에 원비 3열을 추가한다. ZMQ 요청 형식과 제어 요청은 동일하며 Snapshot 응답의 배열 스키마 식별자는 `staff-wire-v2-48`이다. v1/구 v2 45열은 수신 후 MT5 3σ 슬롯을 복사하여 48열로 정규화한다. Python 원비 계산은 하지 않는다.

| 인덱스 | 이름 | 의미 |
|---|---|---|
| 0–44 | S6와 동일 | 이름·순서 불변 |
| 45 | wonbi_upper | EA CalcOpenBands4 상단 |
| 46 | wonbi_lower | EA CalcOpenBands4 하단 |
| 47 | wonbi_sigma | EA가 사용한 InpWonbiSigma |
| 별칭 | wonbi_mid | 기존 12열 open_band_4_mid |

OPEN 길이 4의 2-pass 평균/모표준편차를 EA에서 계산한다. InpWonbiSigma 기본값은 3.0이며 실행 중 변경하지 않는다. `SET_WONBI_SIGMA`는 기대 σ만 바꾼다. 기대값과 MT5 값이 다르면 로그와 SOURCE_HEALTH warnings로 알리고 MT5 밴드는 그대로 사용한다. 구 45열은 upper=18열/open_band_300_upper, lower=17열/open_band_300_lower, sigma=3.0으로 복사한다. 기대 σ가 3.0이 아니면 구 입력의 데이터 요청은 LEGACY_WONBI_SIGMA 오류로 거부한다.

## 공통 프레임

모든 수치는 little endian이다. 헤더는 40바이트 `<IIqIIIIII>`다.

| 필드 | 형식 | 의미 |
|---|---|---|
| magic | u32 | `0x534D4F53` |
| version | u32 | 2 |
| seq | i64 | 피드별 단조 증가 번호. 제어·묶음 외피는 0 |
| symbol_len | u32 | 심볼 UTF-8 바이트 수 |
| tf_len | u32 | TF UTF-8 바이트 수 |
| bars | u32 | FULL/ROW의 행 수, HEARTBEAT는 0 |
| cols | u32 | 등록된 스키마의 열 수: 기존 45 / S7 48 |
| schema_id | u32 | 기존 `0x4D2FA0E9`, S7 `0x36C28F68` |
| kind | u32 | FULL=1, ROW=2, HEARTBEAT=3, HELLO=4, BUNDLE=5, ACK=6 |

헤더 뒤에 payload와 4바이트 CRC32(payload) trailer를 둔다. CRC는 zlib/IEEE CRC32이다. 스키마 CRC는 `staff_schema.PIPE_VALUE_COLUMNS`의 이름을 UTF-8 `\n`으로 연결한 바이트에서 계산하며 마지막 줄바꿈은 없다. 헤더의 길이·kind·version·cols는 CRC와 별개로 범위를 검증한다.

FULL/ROW payload는 `symbol + tf + time[i64]*bars + volume[i64]*bars + values[f64]*bars*cols`다. FULL은 3–650행, ROW는 1행이다. HEARTBEAT payload는 symbol+tf뿐이다. 배열은 기존 순서·원자료 그대로 전달하며 STAFF에서 기존 EMPTY_VALUE/NaN 정제를 적용한다.

FULL은 최초, 새 봉, 지표 준비 상태 변화, 재연결 직후에 보낸다. 과거 행이 정정된 경우에도 FULL을 보내 과거 수정이 유실되지 않게 한다. 같은 봉의 마지막 행만 바뀌면 ROW, 아무 값도 바뀌지 않으면 HEARTBEAT다. ROW로 새 봉이나 validity 변화가 들어오면 거부하고 FULL을 요구한다. ROW/HEARTBEAT는 source_epoch를 증가시키지 않는다. FULL의 epoch 판정은 기존 최초·stale·재연결·validity 변화 규칙을 유지한다.

## HELLO

연결 직후 EA는 HELLO를 보낸다. 이때 symbol_len=64, tf_len=bars=seq=0이고 payload는 EA 빌드 SHA256의 소문자 ASCII hex 64바이트다. STAFF는 schema_id를 검증하고 동일 build hash를 ACK로 돌려준다. EA는 ACK의 magic/version/schema/kind/CRC/build hash를 확인한다. 빌드 해시는 생성 헤더를 제외한 MT5 소스·include의 파일명 및 내용에 길이를 붙여 SHA256으로 계산한다. 생성 규칙과 입력 해시는 `build/generate_staff_s7_schema.py`, `ea_build_identity.json`에 있다.

모르는 schema는 게시하지 않는다. 특정 피드의 오류는 해당 SOURCE_HEALTH를 UNAVAILABLE로, 연결 HELLO/묶음 외피의 schema 오류는 연결 전체를 UNAVAILABLE로 만든다. 정상 HELLO/FULL로 회복할 수 있다.

## TF 묶음

BUNDLE 헤더에서 tf_len=0이고 bars는 심볼을 제외한 payload 바이트 수다. payload는 다음과 같다.

`symbol + sent_at_unix_ms[i64] + count[u32] + (frame_length[u32] + complete_child_frame)*count`

한 묶음에 같은 심볼의 피드 프레임 1–64개를 넣는다. 중첩 묶음은 허용하지 않는다. 자식 프레임과 외피의 CRC를 모두 검사한 후, 한 잠금 구간에서 순서대로 게시한다. 늦은 요청은 일관된 묶음 뷰를 가져가고 인코딩 전에 수신 잠금을 해제한다. 중간 프레임을 최신 것 하나로 합치는 큐는 없다. EA는 실패한 전송을 먼저 재시도하고 다음 관측을 생성한다. 재연결 재시도에는 같은 관측의 FULL을 보낸다.

EA의 묶음 시각은 동일 심볼을 폴링한 회차의 관측 시각이다. 서로 다른 TF의 지표 계산 자체를 동시에 실행한다는 의미는 아니다. 이벤트 엔진 및 Fact DAG는 이 단계에서 구현하지 않는다.

## seq·연결 진단

v2는 피드별 seq를 사용한다. 같은 연결에서 마지막 수신 seq보다 큰 번호가 두 칸 이상 증가하면 누락 범위를 기록한다. 중복·역행 seq는 기존처럼 게시하지 않는다. v1의 seq는 원래 전역 번호이므로 피드별 연속성 검사로 오탐을 만들지 않는다. 새 연결의 첫 FULL은 새 출발점이며, EA 재시작으로 seq가 1부터 시작해도 수용한다.

`wire_diagnostics()`는 피드별 received/accepted/duplicate_or_reverse/missing_sequences/reconnects/last_seq/last_connection/last_receive_delay_ms와 연결 전체 횟수를 복사해 반환한다. 실제 LIVE 지연은 묶음 송신 UTC 밀리초와 STAFF 수신 UTC 밀리초의 차이다. 시계 정밀도는 EA TimeGMT의 초 단위이며, 오프라인 재생의 sent_at=0은 지연 측정에서 제외한다.

누락은 `logs/pipe_gaps_<health_session>.jsonl`에 추가 기록한다. 테스트·도구는 생성자의 `gap_journal`로 별도 파일을 지정한다. 예:

```json
{"schema":"staff-seq-gap/v1","received_at_unix_ms":1790000000123,"sent_at_unix_ms":1790000000000,"symbol":"BTCUSD","timeframe":"1m","first_missing_seq":3,"last_missing_seq":4,"next_received_seq":5,"connection":1,"ea_build_hash":"..."}
```

이는 후속 이벤트 엔진에서 재생할 수 있는 누락 사건 기록이다. 누락된 시장값을 복원한 것처럼 표시하지 않는다.

## MSP3

파일 헤더는 `<IIII>`: magic `0x4D535033`, version 2, cols 45(구 캡처) 또는 48(S7), max_bars 650이다. 각 레코드는 `<qiI>`의 관측 초·기존 family_flags·wire 길이 뒤에 완전한 v2 피드 프레임을 담는다. manifest에는 `pipe_capture=STAFF_PIPE_V2`를 기록한다. MSP2/STAFF_PIPE_V1도 계속 읽는다.

Part2의 v2 SecondFeed는 모든 기록을 순서대로 수신기에 넣고 같은 관측 시각만 묶는다. 해당 초에 새 기록이 없는 피드는 기존 v1의 매초 생존 의미를 HEARTBEAT로 유지한다. 이때 추가된 heartbeat만큼 seq에 offset을 더하여 원래 캡처의 누락 간격은 보존한다. 원본 MSP2/3 파일과 S0 골든은 수정하지 않는다.

S7 생성기와 검증 도구는 `검증결과/staff_s7`에만 쓴다. 과거 S0–S6 증거를 쓰는 이전 build 도구를 S7 재생성에 사용하지 않는다.

## S8 구현 안내

S8은 48열·schema_id·프레임 구조를 바꾸지 않는다. 생성기는 build/generate_staff_s8_schema.py이며 EA 빌드 해시만 새 소스로 갱신한다. 검증 결과는 검증결과/staff_s8에 기록하고 S0–S7 보존 증거를 덮어쓰지 않는다. 상세 구현·게이트는 수정내역_STAFF_S8.md를 따른다.
