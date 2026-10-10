# 프로세스 간 계약 — 현재 실행문 기준

범례: **R**은 해당 동작을 성립시키는 필드, **O**는 생략 가능한 필드/기본값이다. R은 모든 소비자가 엄격한 스키마 검증을 한다는 뜻이 아니다. 실제 코드는 잘못된 입력을 조용히 무시하거나 일부만 저장하는 경우가 있다. 표에 없는 필드를 일괄 금지하는 validator는 없다. 원문 전체 사용 지점은 [contract_inventory.json](audit/fixtures/contract_inventory.json)에서 `file/function/line/category/source`로 확인한다.

## 1. 전송 채널

| 채널 | 생산/서버 → 소비/클라이언트 | 형식 | 경로/설정과 실패 계약 |
|---|---|---|---|
| MT5 Named Pipe | EA writer → STAFF receiver | little-endian SMOS v1 binary | `\\.\pipe\StaffOfMoses_v1`, STAFF_PIPE_NAME. 메시지 중단 시 마지막 정상 캐시 유지 |
| STAFF ZMQ | DataServer REP ← 모든 Python 데이터 소비자 REQ | `send_pyobj/recv_pyobj`: pickle dict/DataFrame | STAFF_BIND_ENDPOINT 기본 `tcp://127.0.0.1:5555`; client STAFF_ENDPOINT, TREND/FVG/SWEEP/KIM은 bind 설정 fallback도 사용 |
| KIM ZMQ | Composer REP ← TREND/FVG/SWEEP/OZ REQ | pickle event dict/ACK dict | MANAGER_ALERT_ENDPOINT 기본 `tcp://127.0.0.1:5556`; thread-local event REQ |
| 공용 명령 | KIM OZCommandQueue → TREND/FVG/SWEEP/OZ 개별 tail | UTF-8 JSONL append | OZ_COMMAND_FILE, 기본 `program/logs/oz_watch_command.jsonl`; 상대경로는 디렉터리 부분을 버리고 basename만 logs 아래 사용 |
| SWEEP 사실 | SweepEventPublisher → OZ SweepEventFileWorker | UTF-8 JSONL append+flush+fsync | 고정 `program/logs/sweep_event.jsonl`; KIM으로 가는 ZMQ와 별도 전달 |
| Telegram | NotificationService → HTTP sendMessage | `data={chat_id,text}`, timeout 10초 | token/recipient는 config, 응답 HTTP 200이면 True; 실제 Telegram message ID를 dedup key로 저장하지 않음 |
| 명령 입력/통역 | TelegramBot getUpdates / Gemini fallback → KIM/interpreter | HTTP JSON / canonical_text | 실행 상태를 소유하지 않는 경계. offline에서는 외부 HTTP 차단 |

STAFF/KIM wire에 공통 protocol_version, event sequence, durable ACK ledger는 없다. Named Pipe에는 version=1, 디스크 상태에는 개별 version이 있다. ZMQ payload는 JSON으로 제한되지 않으며 `pd.Timestamp`, DataFrame, None 등이 포함된다.

## 2. MT5 → STAFF snapshot

header `struct.Struct("<IIqIIII")` 32 bytes: `magic:uint32=0x534D4F53`, `version:uint32=1`, `snapshot_id:int64`, `symbol_len:uint32`, `tf_len:uint32`, `bars:uint32`, `cols:uint32`. 이후 UTF-8 symbol, UTF-8 timeframe, `bars`개 int64 time, `bars`개 int64 volume, row-major `bars*cols`개 float64.

검증: symbol byte 길이 1~128, tf 길이 1~16, bars 3~650, cols=45. tf는 lower-case. 비유한 double 또는 절댓값>1e300은 NaN. time 중복 keep-last, 오름차순, STAFF_BARS tail. time은 epoch seconds를 timezone-naive Timestamp로 변환한다. Python 소비자는 naive를 UTC로 간주한다. 실제 broker time 정합은 별도 검증 대상이다.

| slot | column 순서 |
|---|---|
| 0–3 | open, high, low, close |
| 4–7 | ema_20, ema_21, ema_50, ema_200 |
| 8–11 | hma_6, hma_17, hma_50, hma_168 |
| 12–20 | open_band_4_mid; open_band_179/279/300/400의 lower, upper 순서 |
| 21–23 | price_hma_6, price_band_lower, price_band_upper |
| 24–27 | RSI_val, RSI_db, RSI_ub, RSI_basis |
| 28–31 | STO_val, STO_db, STO_ub, STO_basis |
| 32–35 | DI_val, DI_db, DI_ub, DI_basis |
| 36–38 | price_regime_basis, price_regime_upper, price_regime_lower |
| 39–44 | RSI/STO/DI 순서로 regime_upper, regime_lower |

snapshot_id는 symbol/tf별 마지막 ID와 비교한다. `<=`는 폐기되고 freshness도 갱신되지 않는다. 새 pipe 연결 때 전체 `_snapshot` map을 clear한다. 별도 writer session ID나 snapshot ACK는 없다. 재접속 후 낮은 sequence를 받아들이는 근거는 이 clear이다.

EA는 CopyBuffer 그룹 하나라도 실패하면 해당 feed snapshot 송신을 중단한다. 따라서 Python의 선택 지표 결손 허용이 MT5 생산 단계의 결손까지 우회하지는 않는다.

## 3. STAFF request/response

| 요청 | R | O/기본 | 응답 |
|---|---|---|---|
| 데이터 | symbol 비어 있지 않음, timeframes list/tuple의 유효 feed | indicators list/tuple `[]`; kind 없어도 됨 | `{tf: pd.DataFrame}`. 한 TF 실패 시 요청 전체가 error |
| PING | kind=`PING` | 없음 | ok, pong, service, version=`2.0`, wonbi_source=`OPEN`, wonbi_length=4, wonbi_sigma |
| SET_WONBI_SIGMA | kind, sigma 양의 유한 실수 | 없음 | ok, kind, sigma. KIM proxy는 STAFF ACK 성공 후 로컬 sigma 변경 |

base 컬럼 R: time/open/high/low/close/volume. ATR/WONBI 파생값은 indicators=[]에도 생성된다. 지표 R mapping은 STAFF `MT5_REQUIRED_BY_INDICATOR`에 있고 EMA는 21/50/200, HMA는 6/17/50/168, PRICE는 price HMA6/bands/regime와 HMA6/17, RSI/STO/DI는 value/bands/basis/regime를 요구한다. 지표의 최근 20봉이 모두 NaN이면 실패한다. 파생값은 컬럼 존재를 검사하며 모든 행 값의 유한성을 보증하지 않는다.

`run()`은 처리 예외를 `{error:"request processing failed: Type: message"}`로 변환한다. 직접 `handle()` 호출은 예외를 던진다. client는 error dict를 None으로 바꾼다. 금요일 17:00~일요일 18:00 America/New_York 휴장 구간의 비-BTC/XBT 종목은 `{}` 정상 대기 응답이며 target whitelist 검사보다 먼저 적용된다. BTC/XBT는 휴장 분기 제외이다.

## 4. KIM → 엔진/모니터 command action

공용 wrapper O: `issued_at` epoch **nanoseconds**, KIM queue가 누락 시 추가. JSON default=str. 이 값은 event_time의 seconds와 단위가 다르다. message ID/소비 ACK/consumer offset 저장은 없다.

| action | 수신자 | R | O/의미 |
|---|---|---|---|
| TREND_WATCH | TREND | watch_id, symbol, source_tf | requested_fields=[]; 같은 symbol/tf의 fields union |
| FVG_WATCH | FVG | watch_id, symbol, source_tf | 같은 symbol/tf는 계산 요청 통합 |
| SWEEP_WATCH | SWEEP + OZ external | watch_id, symbol, source_tf 또는 setup_tf | levels, session_london/london, session_newyork/newyork; KIM은 atr_period/atr_mult도 전송하지만 OZ 판정은 고정 14/1.5 |
| CANCEL_TREND/FVG/SWEEP | 해당 엔진; SWEEP는 OZ도 | watch_id | 없는 ID는 no-op |
| RESET_TREND/FVG/SWEEP | 해당 엔진; SWEEP는 OZ도 | action | 해당 registry 전체 초기화; 전달 확인 없음 |
| TREND_QUERY | TREND queue | symbol, source_tf | request_id, request_chat_id, requested_fields, purpose, bar_mode=`LIVE`/`CLOSED`. `추세점수 몇 점?`은 purpose=`TREND_SCORE_QUERY`, requested_fields=trend/long/short_score |
| FVG_QUERY | FVG queue | symbol, source_tf | request_id, request_chat_id |
| SWEEP_QUERY | SWEEP queue | symbol, source_tf/setup_tf | request_id/request_chat_id/watch_id, levels, sessions; query용 detector는 과거 touch 재생 안 함 |
| MANUAL_WATCH | OZ | timeframes 중 지원 TF ≥1 | watch_id 없으면 생성, symbol=None, direction=None, persistent=False, request_chat_id, validation_mode, trigger_mode, oz_mode, trigger_type, source_spec_id, source_name, external 필드 |
| CANCEL_MANUAL | OZ | action | watch_id, timeframes, symbol, direction, persistent_only, request_chat_id, validation/trigger/oz_mode 조합 필터; 모두 없으면 광범위 매칭 |
| GENERIC_WATCH | OZ Generic | 지원 watch_type, timeframes ≥1 | watch_id 자동생성 가능, symbol/direction/level_side, ma_family/fast_period/slow_period, persistent, request_chat_id, chain_id/chain_stage, silent, evaluation_mode |
| CANCEL_GENERIC | OZ Generic | action | watch_id와 위 필드들/TF 교집합/persistent_only/request_chat_id 필터 |
| RESET_ALL | OZ Manual+Generic | action | request_chat_id 있으면 소유자 범위, 없으면 external도 reset |
| FVG_EVENT_WATCH/CANCEL_FVG_EVENT | KIM 내부 | watch_id, trigger context | `_push`가 subscription dirty만 설정; OZ로 전달하지 않음 |
| LOCAL_EVENT_WATCH/CANCEL_LOCAL_EVENT | KIM 내부 | watch_id, watch_type/context | BAR/WONBI는 canonicalize 후 GENERIC 명령으로 변환; COMPOUND는 KIM 내부 처리 |
| SWEEP_EVENT | 별도 sweep_event.jsonl/OZ | 아래 SWEEP fact 필드 | `action=SWEEP_EVENT`를 원래 event에 추가 |

TREND/FVG registry는 truthy watch_id/symbol/tf가 없으면 등록하지 않는다. TF의 상세 지원 범위 검증은 계층마다 다르다: TREND evaluate/KIM normalize는 19개 목록, FVG/SWEEP normalize는 lower-case만 한다. OZ Generic의 일부 입력은 `bool(value)`를 사용하므로 문자열 `"false"`를 False로 해석한다고 가정하지 않는다.

MANUAL external O 필드: `external_liquidity_required` 또는 `external_required`, `external_watch_id`/`sweep_watch_id`/`parent_watch_id`/`setup_watch_id`, `external_source_tf`/`setup_tf`/`source_tf`, `external_source_kind`, `external_level_price`. 수동 level 생성은 required=true + kind=`MANUAL_LEVEL` + finite price일 때만 한다. SWEEP의 external_level_id/price는 Composer 선택 추적용이며 수동 gate 생성 지시가 아니다.

Generic canonical watch_type: `EMA_CROSS,HMA_CROSS,PREV_DAY_TOUCH,BAR,WONBI_TOUCH,PERCENTILE_OUT,PERCENTILE_OUT_IN`. `BAR_CLOSE→BAR/CLOSE`, `WONBI_TOUCH_CLOSE→WONBI_TOUCH/CLOSE`. MA cross/BAR 기본 CLOSE, 나머지 기본 LIVE. `FVG_NEW`는 KIM/FVG 경로에 속하며 Generic에서 직접 계산하지 않는다.

## 5. 엔진 → KIM event kind

공통: dict payload. `strategy`는 TREND/FVG/SWEEP/OZ로 라우팅하며 kind는 대문자로 정규화한다. event의 `source_tf` 대신 `tf`를 받는 KIM fact 경로도 있다. 최소 입력이 부족해 아무것도 저장하지 않았어도 fact handler가 `{ok:true,composed:false}`를 반환할 수 있다.

| kind | 생산 필드와 의미 |
|---|---|
| PING | R kind; O strategy. TREND/FVG/SWEEP/OZ이면 현재 watch 재전달, 다른 값이면 단순 pong |
| TREND_STATE | symbol, source_tf, trend=`UP/DOWN/NEUTRAL`, direction=`LONG/SHORT/NEUTRAL`, trend_basis, sma20_slope, hma50_slope, price, bar_time=live Timestamp. watch_id 없음. 전체 지표 점수(score/long_score/short_score)는 더 이상 포함하지 않음 (수정본5, strategy_INDICATOR) |
| TREND_METRIC_STATE | symbol/source_tf, requested_fields list, bar_time, metrics dict. 허용 metric의 finite 값만 KIM에 저장; received_mono는 수신 시 부여 |
| TREND_QUERY_RESULT | request_id/request_chat_id/purpose, symbol/source_tf, bar_mode, ok; 성공은 상태 필드 또는 requested_fields+metrics/bar_time, 실패 error |
| FVG_CREATED | zone 필드 + event_time=fvg_time. 생성 시각은 3번 확정봉의 **시작 시각** |
| FVG_TOUCH/FVG_TOUCH_END | zone 필드 + price=live_close, event_time=live_time |
| FVG_FILLED | zone 필드 + fill_time/event_time=최초 fill 확정봉 시작 시각 |
| FVG_EXPIRED | zone 필드 + expired_at/event_time=latest_closed_time, max_age_bars=30 |
| FVG_QUERY_RESULT | request_id/request_chat_id, symbol/source_tf, ok, 성공 zones/latest_closed_time/live_price/live_time; 실패 error=`fvg_data_unavailable`, zones=[] |
| SWEEP_TOUCH | watch_id/symbol/source_tf/direction, level_code/name/id/price, touch_time, event_time=touch_time, touch_high/low/close; O restored=true |
| SWEEP_INVALIDATED | 위 identity/context + event_time; touch_time은 원래 touch 시각. 기준 level 소멸/감시 제거/정의 변경에 발생 |
| SWEEP_QUERY_RESULT | request_id/request_chat_id/symbol/source_tf/ok, levels 배열; 실패 error=`sweep_data_unavailable`. 조회 levels는 새 detector이므로 touched/consumed=False |

zone 필드: `symbol,source_tf,fvg_side(BULL/BEAR),direction(LONG/SHORT),fvg_time,zone_bot,zone_top,gap,zone_id,age_bars,touched_now`. 내부 evaluate result에는 `oldest_allowed_time,filled_zones(fill_time),eligible_zone_ids`가 더 있다. query는 이 세 필드를 제외한다. 내부 삭제 이벤트가 old zone의 `touched_now`를 그대로 포함할 수 있으므로 kind를 우선해야 한다.

TREND metrics 허용 이름: `price,long_score,short_score,trend_score,ema10_open,ema50_open,sma20_open,wma17_open,hma50_open,hma50_slope,supertrend,psar,plus_di,minus_di,adx,linreg20,rsi14,cci20,macd,macd_signal,aroon_up,aroon_down,vortex_plus,vortex_minus,vwap,bop,cmf20,mfi14,chop14,hv20,hvma20,mss,vol_state,vol_surge`. comparator는 GT/GTE/LT/LTE/EQ/NE이며 EQ/NE는 isclose 허용오차 사용이다.

## 6. OZ → KIM과 ACK

| kind | R/성립 조건 | O/출력 필드 |
|---|---|---|
| FINAL_ALERT | strategy=`OZ`, 비어 있지 않은 message | direction, request_chat_id, watch_ids, source_spec_id(s), source_name, validation_mode, trigger_mode, trigger_name, source_tf, symbol, grade, indicators_text, current_price |
| CONTROL_ACK | message; 사용자 전송에는 request_chat_id 필요 | watch_id. OZARM:/CFGCHAINTRG:/CHAINTRG: registration ACK는 내부 억제. recipient 없는 내부 ACK도 억제 |
| GENERIC_TRIGGER | 유효 chain_id+chain_stage+watch_id 또는 SPECIAL handler context | strategy=OZ, direction, message, source_tf, symbol, watch_type/evaluation_mode, level_side, MA periods, event_time, event_id |

Generic BAR/CROSS event_time은 확정봉 시작 시각+TF seconds인 **봉마감 시각**이다. FVG_CREATED의 시각과 다르다. KIM FILTER FVG 변환은 TF seconds를 더한다. SEQUENTIAL deadline은 수신 처리 wall time 기준이다. 시간 형식/단위를 혼동하면 replay 허용 기간이 달라진다.

ACK 주요 shape:

- PING: `{ok:true,delivered:false,pong:true,service:"manager_KIM Composer 2.0"}`.
- fact: `{ok:true,delivered:false,composed:bool,fvg_created_watches_fired:int,fvg_chain_events:int,subscription_changed:bool}`.
- 최종/조회: `{ok:delivered,delivered:bool,error:None|"telegram_send_failed"}` 등 경로별 부분집합.
- 내부 억제/chain: `suppressed,chain_stage,internal_watch_ids,ignored,stale,expired,reason,invalidated,order_mode,matched,count,required_count,active_until` 중 해당 필드.
- 실패: invalid_event, empty_message, unknown_kind:..., handler exception. client timeout은 manager_timeout, 기타 manager_error, 비-dict ACK는 invalid_ack.

OZ `require_delivery=True`는 ok와 delivered가 모두 참이어야 성공이다. chain Generic은 require_delivery=False라 ok만으로 성공 처리·일회성 watch 소진이 가능하다. `watch_id` 단일과 `watch_ids` 배열은 모든 경로에서 동일하지 않다. Composer `_active_children` 제거는 배열을 사용하고 orchestrator는 단일도 병합한다. 전역 FINAL_ALERT dedup 검사는 없다.

KIM dispatch 순서: PING → `kind.endswith("QUERY_RESULT")` → GENERIC_TRIGGER → strategy가 세 fact 엔진이면 fact handler → strategy=OZ 또는 CONTROL_ACK → 일반 FINAL_ALERT → unknown_kind. 미지의 fact kind도 fact 경로에서 ok/no-op가 될 수 있다.

## 7. identity / 상태키 / 중복

| 대상 | identity / key | 중복 처리 |
|---|---|---|
| feed | (symbol, lower(tf)) + snapshot_id | 동일 연결 ID≤마지막 무시 |
| TREND state | (symbol,tf)→trend | 메모리의 동일 trend suppression, ACK 전에 갱신 |
| FVG zone | `symbol\|tf\|side\|int(epoch)\|bot:.12g\|top:.12g` | created set, touch bool, closed time, active zones 모두 RAM |
| SWEEP level | PDH/PDL:이전 일봉 time, PREV_4H/8H_HIGH/LOW:bar time, PWH/PWL:UTC previous week, session:anchor | watch별 touched/consumed/last_closed_time 디스크 |
| KIM facts | TREND (symbol,tf); FVG (symbol,tf,zone_id); SWEEP (symbol,tf,watch_id,level_id) | dict overwrite/pop, tombstone/역순 event 방어 없음 |
| engine subscription | stable_id SHA1 앞16 hex, `CMP:TREND`, `CMP:FVG`, `CMP:SWEEP` | TREND/FVG는 symbol/tf, SWEEP는 selectors/ATR 파라미터/session까지 해시 입력 |
| Composer child | `OZARM:` + spec/direction/signature SHA1 앞20 | 같은 profile 병합, 성공 signature RAM, child payload 저장 |
| chain trigger | CHAINTRG/CHAINUNORD/CHAINFILTER, chain_id+stage 앞20 | 현재 stage+watch_id 비교. CANCEL_ON은 CHAIN invalidation ID와 `-(index+1)` stage |
| final chain child | OZARM:CHAIN + chain_id+현재 millisecond 해시 | payload/active_until 저장 |
| official chain | CFGCHAIN:spec_id / CFGCHAINTRG | signature 비교하여 재시작 state 채택/폐기 |
| OZ external | watch_id+direction+level_id | 동일 state 존재 시 TOUCH `event_time<=previous` 무시, INVALIDATED `<previous` 무시; 삭제 뒤 watermark 없음 |
| OZ alert | (tf,direction,true_b0_time) | monitor instance별 RAM, profile 간 공유 안 함 |
| unordered latch | upper(correlation_key)→upper(condition_key)→ts/token/meta | 같은 조건 1개만, 오래된 갱신 무시, K-of-N signature; wall cutoff 밖 event 거절 |

`stable_id`는 `"|".join(str(parts))` SHA-1이며 기본 길이 16, 호출마다 20/24가 사용된다. 임의로 UUID로 바꾸거나 숫자 표현/필드 순서를 바꾸면 재시작 identity 계약이 달라진다.

## 8. persistence / 재시작

기본 root는 각 Python의 `program/logs/`. `.tmp→flush→fsync→os.replace`가 일반 JSON 상태 저장 방식이다. SWEEP registry는 PermissionError 때 3회 교체 재시도한다. 여러 상태파일과 queue append/전송은 하나의 transaction이 아니다. 소비 offset은 디스크에 없다. 상태파일 version을 모두 엄격하게 거절하는 공통 migration 계층도 없다.

| 파일 | 소유자/version | 저장/복원되는 것 | 저장되지 않는 것 |
|---|---|---|---|
| trend_watch_state.json | TREND/3 | watches[id]=[symbol,tf,fields]; 구형 길이2 수용 | 마지막 방향/metric push 시각 |
| fvg_watch_state.json | FVG/2 | watches[id]=[symbol,tf] | active zones/touches/seen_created/closed watermark |
| sweep_watch_state.json | SWEEP/2 | watches[id]={symbol,source_tf,levels,sessions} | detector는 별도 파일 |
| sweep_detector_state.json | SWEEP/1 | detectors[id]={spec,last_closed_time,states}; touched/consumed/time/OHLC | ZMQ 성공 확인 ledger |
| composer_private_watches.json | KIM/2 | PRIVATE+owner StrategySpec 배열 | facts/last_signatures |
| composer_timed_chains.json | orchestrator/1 | spec + stage/deadline/latch/ID/active_until/start_at | notification delivery transaction |
| composer_fvg_created_watches.json | KIM/1 | direct FVG-created watch | 전역 event dedup |
| composer_active_oz_watches.json | KIM/1 | OZARM: MANUAL_WATCH child payload/소유권 | OZ base candidate |
| composer_config_timed_chain_state.json | KIM/3 | 공식 chain states, cross/FVG/latch/post-touch/spec signature | 별도 임시 cache |
| oz_manual_watch_state.json | OZ/3 | WatchSpec, external 링크, profile/source | candidate/episode/cross/alert_keys |
| oz_generic_watch_state.json | OZ/2 | GenericWatchSpec/chain link/evaluation_mode | last_bar/touch/percentile 관찰 메모리 |
| oz_external_liquidity_state.json | OZ/4 | specs와 states, fixed ATR14/1.5, status/reason | 삭제된 level의 tombstone |
| special4_state*.json | SPECIAL4 | 종목별 고유 cycle 저장 | 전체 SPECIAL이 동일 저장 방식을 쓴다는 보장 없음 |
| alerted_events.txt/last_briefing.txt | EconomyWorker | 경제 알림/브리핑 기억 | core 시장 fact와 독립 |

OZ external 상태: WAIT_SWEEP → PENDING_ATR → ACTIVE → CONFIRMED 또는 INVALID. pending은 event time±0.5초에 해당하는 STAFF row와 양수 ATR14를 찾는다. 최초 extreme와 이후 segment가 level±1.5ATR을 **엄격히** 넘으면 INVALID. TRUE B0 absolute distance는 `<=1.5ATR` 허용. CONFIRMED는 update_market의 ACTIVE 생존 검사 대상이 아니다.

PING 복구:

1. engine registry 복원 → command worker EOF offset 설정/시작 → PING.
2. KIM은 현재 desired subscription과 저장 registry ID 차집합으로 stale watch를 cancel하고 desired를 재전달한다. RESET 전체를 먼저 보내지 않는다.
3. OZ도 worker 준비 후 PING. KIM은 자신이 아는 active child/개인 chain/공식 chain/external desired와 OZ 저장 ID를 비교한다.
4. PING 성공은 **watch 구독 복구**이며 모든 fact snapshot 재전송을 뜻하지 않는다. KIM 단독 재시작의 빈 fact store를 다른 엔진의 억제 메모리가 채워주지 못하는 경우가 있다.
5. SWEEP 자체 재시작은 저장 detector를 현재 level과 대조한 후 surviving touches를 KIM에만 재전송한다. ACK 실패 시 다음 주기에 재시도한다.

## 9. timeout/stale/expiry

| 항목 | 현재 기준 |
|---|---|
| STAFF stale | monotonic age `>30초` 실패; 정확히30초 허용; PING은 데이터 freshness 확인 아님 |
| ZMQ STAFF | config ZMQ_TIMEOUT_MS=5000; OZ 클래스 기본3000, 전략 기본5000; 송/수신 동일 timeout, LINGER=0, 실패 REQ 재생성 |
| KIM ACK | 기본15000ms; 전략 MANAGER_ALERT_TIMEOUT_MS→MANAGER_TIMEOUT_MS fallback |
| poll/refresh | 엔진0.5초, 명령tail0.2초, KIM0.5초(min0.2), subscription30초(min5), chain30초(min5), metrics1초 |
| metric freshness | monotonic received 기준5초 기본(min1); `>` 만료 |
| MA freshness | wall observed_at 기준5초 또는 poll×3 기본(min1); `>` 만료 |
| FVG | age 0~29, 이후 제외; fill은 확정봉 |
| OZ | TRUE B0 max10, HMA cross7, OUT→IN7 기본, `>` 무효; min B0 1. event time 행이 snapshot 밖이면 age None 경로 |
| chain | SEQUENTIAL event는 now>deadline 거절, maintenance는 now>=deadline 삭제; active_until은 >= 종료 |
| unordered/filter | unordered cutoff보다 작은 ts 제거(정확한 cutoff는 남음); FILTER expires_at<=now 만료 |

명령 tail은 시작 시 EOF에서 읽고, SWEEP event tail만 offset0에서 전량 replay한다. 둘 다 line을 읽은 직후 offset을 전진시킨 뒤 JSON decode/apply한다. partial line·파일 truncate/rotation·consumer crash에서 exactly-once를 보장하지 않는다. producer KIM command append는 flush만 하고 fsync하지 않는다. 세 엔진 일반 이벤트 전송은 ACK 실패 후 durable outbox 재시도를 하지 않는다.

## 10. 해석/설정 경계

aliases version2는 defaults/symbols/phrase_aliases/oz_direction/ma_family/cross/wonbi_side/percentile_side/condition_macros를 가진다. load는 기본 사전에 nested merge하며 오류 시 마지막 정상 사전을 유지한다. `기본더블비`는 `$tf/$direction`을 치환한 TREND+WONBI ALL descriptor다. 기본 symbol=XAUUSD, timeframe=1m, EMA50/200이며 실제 허용 종목의 broker suffix 매칭이 별도로 적용된다.

normalize_tf는 19개 MT5 TF만 통과시킨다. OZ base TF는 그 중 4h까지 15개이고 higher mapping은 ARCHITECTURE에 설명한 모니터 상수 기준이다. Gemini는 canonical_text만 반환하며 새로운 OZ 의미·종목·TF 추가 일부를 검사한다. 통역 결과를 전략 사실로 사용하지 않는다. alias reload 기본5초, SPECIAL scan 기본1초이다.

정확한 dataclass 필드/조건별 validation과 SPECIAL의 가변 payload는 원문 inventory를 함께 사용한다. 본 표를 더 엄격한 새 validator로 운영 코드에 적용하지 않았다.
