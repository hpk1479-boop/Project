# STAFF SNAPSHOT API — S3

S3는 API와 opt-in 라이브러리만 추가한다. 기존 전략 클라이언트는 legacy API를 계속 사용한다. 실제 클라이언트 전환은 S4다. Part3는 레거시로 고정한다.

## 사용

Part1/program을 모듈 경로로 사용하는 프로세스에서:

```python
from staff_snapshot import SnapshotClient
from staff_compat import StaffCompat

client = SnapshotClient('tcp://127.0.0.1:5555', timeout_ms=5000)
request = {
    'symbol': 'XAUUSD+',
    'timeframes': ['1m', '5m'],
    'indicators': ['PRICE', 'RSI', 'SMA20'],
    'watch_ma_history_rows': 650,
}

# 기존 데이터 요청 모양의 응답. 오류는 기존 error dict로 전달한다.
compat = StaffCompat(client)  # WATCH 이력을 위해 같은 인스턴스를 유지한다.
response = compat.request(request)

# 원시 배열이 필요한 소비자는 아래 API를 사용한다.
batch = client.request(request)
if batch.error is None and not batch.closed:
    snapshot = batch.feeds['1m']
    values = snapshot.values  # 읽기 전용, v1 45열
    frame = client.frame(snapshot)  # raw legacy DataFrame의 독립 복사본
```

이 API는 데이터 요청용이다. PING/SOURCE_HEALTH/SET_WONBI_SIGMA 제어 요청의 기존 API는 그대로 유지한다. `SnapshotClient.request`는 입력의 kind를 SNAPSHOT으로 설정한다.

## 전송 계약

- 같은 STAFF ZMQ REP endpoint를 사용한다.
- 요청: `[b'STAFF_SNAPSHOT_V1', JSON UTF-8]`. JSON의 kind는 `SNAPSHOT`이다.
- 응답: `[JSON UTF-8, time_0, volume_0, values_0, time_1, ...]`.
- 헤더에는 protocol=`staff-snapshot/1`, schema_id=`staff-wire-v1-45`, kind=`FULL`, 현재 Python 원비 sigma, max_bars, closed, error, 순서 있는 feeds 목록이 있다.
- feed 메타데이터: symbol, tf, seq, source_epoch, received_at, indicator_validity, bars.
- 배열은 little-endian이다. time과 volume은 int64, values는 float64 C-order `(bars, 45)`다. 시간은 기존 wire의 UTC epoch seconds다.
- received_at은 서버의 monotonic 시각이다. 클라이언트 wall clock과 직접 빼서 age로 사용하지 않는다. 서버가 30초 stale을 판정한다.
- 주말 대기는 closed=true, feeds=[]이다. 오류는 error dict, feeds=[]이다. 새 경로에서 pickle을 사용하지 않는다.
- 기존 단일 pickle 요청/응답은 유지한다. pickle로 보낸 SNAPSHOT 요청은 명시적 오류를 돌려준다.

이것은 EA Named Pipe wire v2가 아니다. 기존 wire v1·45열·FULL 수신은 그대로이며 CRC/ROW/HEARTBEAT/MT5 원비 전환은 S3에 포함하지 않는다.

## 캐시와 호환

배열과 Snapshot 메타데이터는 불변이다. 클라이언트는 symbol/TF/schema/seq/epoch/수신 시각/max_bars로 publication을 구분하고 최신 Snapshot과 raw DataFrame을 보관한다. 재연결의 seq 재사용은 epoch로 구분한다. 반환 DataFrame을 수정해도 다음 요청이 오염되지 않는다.

호환 어댑터는 S2의 지표 파생·원비 Python 산술·WATCH MA 이력 처리를 그대로 사용한다. sigma와 요청 지표가 달라지면 그 요청에 맞게 파생 응답을 만든다. 원비 MT5 전환은 S7이며 이 단계에서 밴드 열을 MT5 값으로 대체하지 않는다.

검증 근거는 `수정내역_STAFF_S3.md`와 `검증결과/staff_s3`에 기록한다.
