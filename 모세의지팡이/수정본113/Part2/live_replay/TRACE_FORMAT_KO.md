# SPECIAL1 observation trace v1

## 목적과 제한

이 형식은 Part1이 관측한 native-buffer snapshot과 서비스 호출 순서를 전달한다. 틱이나 종가를 Percentile 표본으로 쓰는 형식이 아니다. raw tick만 넣으면 실행되는 역사 재생기는 아직 아니다. 각 poll은 현재 원자적으로 실행하므로 poll 내부의 여러 프로세스 교차 실행은 표현하지 못한다.

첫 줄은 HEADER, 이후는 이벤트 JSONL이다. 빈 이벤트 줄은 건너뛰지만 이벤트는 정렬·중복 제거하지 않는다. `timestamp_ns`는 UTC Unix epoch 정수 나노초, `sequence`는 동일 시각의 순서 식별자다. `(timestamp_ns, sequence)`는 파일 전체에서 엄격히 증가해야 한다.

```json
{"kind":"HEADER","schema":"special1-observation-trace/v1","origin":"CAPTURED_NATIVE","scope":"SPECIAL1_ISOLATED","initial_state":"COLD","health_session":"recorded-session-id","source_fingerprint":"REPLACE_WITH_CURRENT_SOURCE_FINGERPRINT","delivery_default":true}
```

`source_fingerprint`는 `live_replay.checkpoint.source_fingerprint()` 결과다. 예제 명령은 정확한 값을 써 준다. origin은 실제 native 관측 `CAPTURED_NATIVE` 또는 가상 테스트 `SYNTHETIC`만 가능하다. CAPTURED_NATIVE라고 선언한 것만으로 실제 LIVE parity를 인증하지 않는다.

`initial_state=COLD`는 명시적인 빈 상태에서 시작한다는 의미다. 과거 중간 구간을 재생하면서 이전 LIVE 상태가 없는데 이미 복원된 것으로 가정하면 안 된다. 별도의 `--checkpoint-in`은 재생 연속 실행에만 사용한다.

## 이벤트

| kind | 추가 필드 | 의미 |
|---|---|---|
| STAFF_PUBLISH | payload_b64 | Part1 native STAFF pipe 메시지 1개 전체. 관측 시점은 해당 메시지가 수신·적용된 시각이다. |
| TREND_POLL | order | 해당 시각의 TREND watch 평가. 원본 수학 함수·score·evaluate 사용. |
| TREND_FACT | event | 캡처한 원본 TREND event 또는 snapshot. source_health/stream revision 등을 원본 그대로 제공한다. |
| COMPOSER_POLL | order | 원본 `_poll_wonbi`와 SPECIAL1 조건 평가. 실제 set iteration 순서를 명시한다. |
| COMMAND_DRAIN | limit (선택) | 현재까지 대기한 MANUAL_WATCH/CANCEL_MANUAL 처리. 생략하면 현재 대기 전부 처리. |
| OZ_POLL | symbol, delivery_results (선택) | 해당 심볼의 원본 ALLZONE run_once 호출. 전달 응답을 순서대로 적용. |
| CLOCK | 없음 | 시장 관측 종료/무거래 구간의 실제 시간 진행. 지표·전략 조건을 새로 평가하지 않는다. |

`order`는 JSON 객체의 삽입 순서를 유지한다. 각 심볼은 `XAUUSD+` 또는 `NAS100`, TF 목록은 1h/2h/3h/4h 각각 한 번씩이어야 한다. 예: `{"XAUUSD+":["1h","2h","3h","4h"]}`. 실제 순서를 모르면 임의 정렬해서 동일하다고 판정하지 않는다.

각 OZ_POLL의 `delivery_results`는 최종 전달 시도에 대한 bool 배열이다. false 후 다음 poll에서 true가 되면 최초 완료 event_time은 유지되고 delivery_time만 바뀐다. 남는 결과가 있으면 trace 불일치로 오류 처리한다. 생략 시 header의 `delivery_default`를 사용하므로, 기록된 전달 결과가 없는 실행은 네트워크 재현이 아닌 가정이다. 대상 수신자는 현재 단일 replay recipient다. 실제 다중 수신자·ACK 불확실성은 미지원이다.

## Native payload

기존 STAFF EA 메시지의 header, symbol/tf, bar time 배열, volume 배열, 45개 값 컬럼을 그대로 사용한다. native message endian/column order는 동봉한 원본에서 추출한 `reference/staff.py`의 PIPE_* 상수에 고정한다.

`trace_io.pack_snapshot`은 이미 native 값이 채워진 DataFrame을 이 wire 형식으로 포장하는 도구일 뿐이다. 없는 Percentile을 계산하거나 이전 완료봉 값으로 대체하지 않는다. 3~650개 bar와 모든 native 컬럼을 요구한다. 함수의 bar `time`은 Part1이 해석하는 broker의 **timezone-naive 정수 초 좌표**다. 관측 `timestamp_ns`와 같은 시간축이라고 추정하면 안 된다.

입력값을 만든 custom indicator의 파라미터, 초기화 및 prev_calculated 경로, CopyBuffer 수신 상태는 실제 native capture로 검증해야 한다. MQL 파일이 동봉되어 있다고 Python에서 MQL을 실행하는 것은 아니다.

## Alert 결과

| 필드 | 의미 |
|---|---|
| alert_time_ns | 원본 FINAL_ALERT.event_time을 Decimal(str(float)) 경유로 ns 표현한 신호 완료시각. 원본 float보다 정밀한 관측을 창작하지 않는다. |
| condition_event_time | Part1 원본 부동소수 초 값. |
| delivery_time_ns | 재생 서비스의 성공 전달 관측시각. 실제 Telegram 서버 수신 시각을 뜻하지 않는다. |
| display_time_ns | 실시간은 alert_time_ns, 봉마감은 `(alert_time_ns // 60000000000 + 1) * 60000000000`. |
| release_time_ns | `max(delivery_time_ns, display_time_ns)`. 전달 성공 이전 표시 금지. |
| presentation_emitted_ns | 실제 재생 시계상 출력 가능해진 시각. EOF에서 미래 시각을 만들어 출력하지 않는다. |
| late_delivery | 봉마감의 nominal close 뒤에 전달 성공이 관측되었는지 여부. |
| session | 성공 전달 시 원본 TimePolicy가 허용하는 MAIN_* 목록. 봉마감 표시 때문에 재분류하지 않는다. |
| source_spec_id / source_spec_ids | 원본 Composer/Watch에 실린 branch 출처. 임의 병합 보정하지 않는다. |
| event_order | 성공 전달 순서. 입력을 정렬해서 순서를 맞추지 않는다. |
| event_id | 원본 최종 이벤트 ID. TREND_POLL의 transport UUID는 결정적으로 합성되며 실제 LIVE UUID와 동일하다고 주장하지 않는다. |

## 비교 ledger

다음은 형식만 나타낸 예다. 실제 자료로 사용할 수 없다.

```json
{
  "origin": "PART1_LIVE",
  "timestamp_policy": "PART1_EVENT_TIME",
  "alerts": [
    {
      "strategy": "SPECIAL1",
      "symbol": "XAUUSD+",
      "direction": "LONG",
      "session": ["MAIN_ASIA"],
      "alert_time_ns": 1785721290000000000,
      "source_tf": "1m",
      "source_spec_id": "PIPELINE_1@1h",
      "source_spec_ids": ["PIPELINE_1@1h"]
    }
  ]
}
```

배열은 실제 전달 순서를 유지해야 한다. 모든 필드를 갖춘 비어 있지 않은 ledger가 일치해야 `MATCH_NONEMPTY`다. 이 결과도 **원장 동등성 판정**이지 LIVE 수집 진위, native 수학, 실제 종단 간 동작 인증이 아니다. event_id 및 실제 Telegram 수신 시각은 현재 비교 필수키가 아니며 별도 검증 대상이다. source TF/branch까지 완전히 동일한 중복 이벤트 사이의 순서는 외부 수신자·원본 transport ID 증거 없이는 추가로 구별하지 못한다.
