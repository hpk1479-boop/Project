# THE STAFF OF MOSES 책임분리 설계서

- 기준: 수정본6 (완성본). 이 문서는 설계만 담고 있으며 코드는 한 줄도 바꾸지 않았습니다.
- 목표: `THE STAFF OF MOSES.py`(이하 STAFF)가 **Named Pipe 수신 → 검증 → Snapshot 전달**만 하도록 만들고, 지금 STAFF 안에 섞여 있는 계산은 각 계산의 주인 모듈로 옮깁니다.
- 개정: ATR을 두 Fact로 나눴습니다. 공용 **`ATR14_GENERAL`**(기존 STAFF 방식, OZ 외부유동성·SPECIAL4)은 indicator_facts 레지스트리의 공용 Fact로, **`FVG_WILDER_ATR`**(기존 FVG 방식)은 strategy_FVG 내부 Fact로 둡니다. 새 전략 모듈(`fvg_facts.py`)은 만들지 않습니다.
- 원비 결정(사용자 확정): **MT5 계산값을 기준 원본으로 삼습니다.** Python 원비 계산은 없애고, MT5 값을 STAFF가 그대로 전달합니다. pandas 결과와 MQL 결과를 맞추는 작업은 하지 않습니다.

---

## 0. 지켜야 할 조건

| 구분 | 조건 |
|---|---|
| 변경 금지 | 전략 조건, 알림 결과(내용·시각·수신자), Watch 결과, SPECIAL 결과, LIVE↔백테스트 parity |
| 유일한 의도된 변경 | 원비 값의 출처가 Python에서 MT5로 바뀝니다. 그래서 원비 경계에 걸리는 극소수 판정은 달라질 수 있습니다(§6.2). 이 부분만 "새 기준선"으로 다시 고정하고, 다른 결과는 모두 비트 단위로 같아야 합니다. |
| 구조 규칙 | Part1은 Part2/Part3를 부르지 않습니다. 원본 폴더는 수정하지 않고 새 수정본 폴더에서 작업합니다. 요청하지 않은 로직은 바꾸지 않습니다. 기존 테스트는 그대로 통과해야 합니다. |
| 이전 방식 | 계산 코드는 **다시 쓰지 않고 그대로 옮깁니다.** 원비를 뺀 모든 파생값은 옮긴 뒤에도 같은 입력에 같은 비트를 내야 합니다. |

---

## ① 현재 문제점

### 1.1 MT5에서 이미 받는 값 (45열)

EA(`THE_STAFF_OF_MOSES.mq5`)는 `\\.\pipe\StaffOfMoses_v1`로 다음 형식의 스냅샷을 보냅니다. 32바이트 헤더 `<IIqIIII>`(magic "SMOS", version 1, seq, sym_len, tf_len, bars, cols=45) 다음에 symbol, tf, time[bars], volume[bars], values[bars×45] 순서로 이어집니다.

| 열 | 이름 | MT5 계산 주체 |
|---|---|---|
| 0–3 | open, high, low, close | CopyRates |
| 4–7 | ema_20/21/50/200 | iMA(EMA, CLOSE) |
| 8–11 | hma_6/17/50/168 | EA `CalcHMA` (OPEN 기준) |
| 12 | open_band_4_mid | EA `CalcOpenBands4`: OPEN의 SMA4 |
| 13–20 | open_band_{179,279,300,400}_{lower,upper} | mid ± k × 모표준편차(4). **300 슬롯이 원비 4/3** |
| 21–23 | price_hma_6, price_band_lower/upper | PRICE_of_Moses |
| 24–35 | RSI/STO/DI 각각 _val, _db, _ub, _basis | 각 *_of_Moses |
| 36–44 | price_regime_basis/upper/lower, RSI/STO/DI_regime_upper/lower | 각 *_of_Moses |

> 원비 4/3 밴드는 **이미 MT5가 열 17–18(open_band_300_*)로 보내고 있습니다.** 그런데 이 값을 읽는 곳이 없고, Python이 같은 밴드를 다시 계산해 그 값을 씁니다.

### 1.2 STAFF가 Python에서 추가로 계산하는 항목 (전체)

| # | 출력 | 함수 (STAFF.py) | 입력 | 언제 | 실제 사용처 |
|---|---|---|---|---|---|
| P1 | 파이프 파싱: DataFrame 생성, 정렬, 중복 제거, tail(650), NaN 치환 | `_consume_one` :252–313 | 원시 바이트 | 메시지마다(약 57건/초) | 전체 |
| P2 | indicator_validity: 7개 지표군의 마지막 행 유효성 | :302–303 | 마지막 행 | 메시지마다 | SOURCE_HEALTH |
| P3 | source_epoch = `세션UUID:카운터` | :305–308 | seq, 유효성, 공백 | 메시지마다 | SOURCE_HEALTH, 이벤트 |
| P4 | **atr_14**: TR을 `ewm(α=1/14, adjust=False, min_periods=14)`로 평활(첫 값 TR[0] 시드) | `add_atr14_feature` :524–540 | H, L, C | **요청마다, 무조건** | monitor_OZ 외부유동성(×1.5), SPECIAL4 |
| P5 | **wonbi_mid/std/upper/lower/sigma**: OPEN rolling(4) mean/std(ddof=0) × σ | `add_wonbi_features` :543–552 | OPEN, 런타임 σ | **요청마다, 무조건** | monitor_OZ WONBI_TOUCH, OZ 원비 트리거, manager_KIM `_poll_wonbi`, SPECIAL1, SPECIAL4 |
| P6 | ema_21_slope | `add_ema_derived` :391–395 | ema_21 | EMA 요청 시 | 읽는 곳 없음 |
| P7 | price_/RSI_/STO_/DI_ percentile_zone, percentile_in | :398–495 | 판정값 vs 밴드 | 지표 요청 시 | 읽는 곳 없음(monitor_OZ는 원시 열로 직접 판정) |
| P8 | *_regime_zone, *_regime_in, *_regime_slope | :398–495 | 판정값 vs regime선, basis.diff() | 지표 요청 시 | **monitor_OZ Regime 판정**(:2992–2995) |
| P9 | RSI_/STO_/DI_slope | `add_mt5_basis_slopes` :609–615 | basis.diff() | RSI/STO/DI 요청 시 | 읽는 곳 없음(P8의 slope와 중복) |
| P10 | supertrend_is_positive | `add_supertrend` :556–593 (순수 Python 반복문) | H, L, C | SUPERTREND 요청 시 | Part1 클라이언트 없음 |
| P11 | bull/bear_fvg_gap 등 | `add_fvg_features` :596–606 (ATR 필터 없음) | H, L | FVG 요청 시 | Part2 `live_replay/event_catalog.py`만 사용 |
| P12 | SMAn/WMAn/EMAn/HMAn | `WatchMAFeatures` 호출, `MAFeatureCache`(SHA-256, LRU 128) | open/close, 이력 | MA 지표 요청 시 | watch_ma, Watch 조건 |
| P13 | 검증: 필수 열, 마지막 20행 NaN, 30초 stale, 주말, 허용 심볼 | :671–707, :755–796, :856 | 스냅샷 | 요청마다 | 전체(**STAFF에 남길 책임**) |

### 1.3 문제점

1. **같은 계산을 요청마다 반복합니다.** ATR과 원비는 `indicators=[]`인 요청에도 매번 계산됩니다. 같은 (심볼, TF, seq)에 대해 클라이언트 수만큼 반복되고, 요청마다 DataFrame 깊은 복사가 2번씩 일어납니다.
   - 수정본6 프로파일에서 Part1 전체 경로 시간의 **절반 이상**이 STAFF 요청 처리였습니다.
2. **원비 원본이 둘입니다.** MT5는 4/3 밴드를 보내는데 Python이 같은 밴드를 다시 계산해서 씁니다. 두 값은 대부분의 봉에서 끝자리가 다릅니다(§6.2).
3. **ATR이 두 곳에서 따로 계산되고 주인이 없습니다.**
   - STAFF `atr_14`: TR을 ewm(α=1/14, adjust=False, min_periods=14)로 평활합니다(TR[0] 시드). 사용처는 **monitor_OZ 외부유동성(ATR×1.5)과 SPECIAL4**이고, FVG는 쓰지 않습니다.
   - indicator_facts `rma(true_range(df), 14)`: STAFF `atr_14`와 **같은 식**입니다. 합성 200세트 × 650봉(NaN 구간 포함) 13만 개 값이 비트 단위로 같음을 확인했습니다.
   - FVG `_wilder_atr`: SMA14로 시드한 뒤 Wilder 재귀를 적용합니다. **다른 정의**이고, FVG 크기 필터(0.25–1.75×ATR) 전용입니다.
   - STAFF `add_supertrend` 내부 RMA: 또 다른 정의이며 Part1 클라이언트는 쓰지 않습니다.
   - 같은 "ATR"이라는 이름으로 정의가 둘 이상 있는데, 공용 ATR의 주인이 정해져 있지 않습니다.
4. **OZ 상태판정(regime_zone/slope)이 STAFF에 있습니다.** 사용하는 곳은 monitor_OZ 하나뿐입니다.
5. **쓰지 않는 파생값이 있습니다.** P6, P7, P9, P10과 open_band 8열, ema_20은 Part1에서 아무도 읽지 않습니다.
6. **파이프 부하가 큽니다.** EA는 피드마다 1초에 1번, 650봉 × 45열(약 244KB)을 통째로 보냅니다. 약 57피드면 초당 약 14MB이고, Python은 이를 pandas로 여러 번 복사합니다.
   - Python이 느려지면 EA의 `FileWriteArray`가 `OnTimer` 안에서 막힙니다.
7. **Part2가 STAFF 내부에 결합되어 있습니다.** `live_replay/runtime.py`가 STAFF의 private 필드(`_cache`, `_health_epochs`, `_watch_ma_features` 등)를 직접 다시 선언합니다. STAFF `__init__`에 속성 하나만 추가해도 깨집니다.
8. **계약 기록이 낡았습니다.** `source_contract.json`의 STAFF 해시(`2b5829…`)가 현재 파일(`f72b84…`)과 다릅니다. 보고만 하고 강제하지 않아서 드러나지 않았습니다.
9. **설정이 충돌합니다.** EA 기본 심볼에는 BTCUSD가 있는데 `STAFF_ALLOWED_SYMBOLS`에는 없습니다. 그래서 수신은 하지만 요청하면 거부합니다.
10. **EA가 상태 파일을 너무 자주 씁니다.** 100ms마다 파일을 쓰고 이동하는데, 이 파일을 읽는 Python 코드는 없습니다.

---

## ② 최종 책임분리 구조

### 2.1 전체 흐름

```
[MT5 EA]  모든 지표·원비(4/σ) 계산 ─ Named Pipe (Wire v2) ─▶ [STAFF]
                                                           수신 · 검증 · 저장 · Snapshot 전달
                                                           (계산 없음, 파라미터 중계만)
                                                                  │  ZMQ (SNAPSHOT / legacy)
            ┌──────────────────────┬──────────────────────┬───────┴────────────┬───────────────────┐
     [staff_snapshot 클라이언트 라이브러리 : Snapshot → 읽기전용 배열/DataFrame, (key, seq) 단위 캐시]
            │                      │                      │                    │
      monitor_OZ             strategy_INDICATOR       strategy_FVG      manager_KIM / SPECIAL
   (OZ 상태판정 Fact,     (indicator_facts:        (내부 Fact:         (staff_compat가 기존과
    외부유동성은            일반 파생 Fact +          FVG_WILDER_ATR ·    같은 형태의 프레임 제공,
    ATR14_GENERAL 사용)     공용 ATR14_GENERAL)       FVG 구조)           atr_14 = ATR14_GENERAL)
```

### 2.2 STAFF의 최종 책임

**남기는 것**
1. Named Pipe 수신: 전용 수신 스레드. 헤더·길이·스키마 검증, seq 단조 증가 확인, EMPTY_VALUE를 NaN으로 바꾸기, 손상 프레임 폐기.
2. 저장: (심볼, TF)마다 최신 Snapshot 하나를 numpy 읽기전용 블록으로 저장합니다. 수신 시에는 pandas를 쓰지 않습니다.
3. 상태: freshness(30초 stale), 주말·허용 심볼 판정, indicator_validity(마지막 행 유효성; 계산이 아니라 검증), source_epoch 규칙(지금 규칙 그대로).
4. 전달: `SNAPSHOT`(신규), `SOURCE_HEALTH`, `PING`, 그리고 전환 기간의 legacy 요청(§2.6).
5. 파라미터 중계: `SET_WONBI_SIGMA`를 받으면 MT5가 읽는 σ 파일에 기록하고(§2.4), 현재 σ를 응답 메타데이터로 전달합니다.

**하지 않는 것**
- 계산하지 않습니다: ATR, 원비, zone/in/slope, SuperTrend, FVG, MA.
- 전략별 판단을 하지 않습니다.

### 2.3 계산 항목별 새 주인 (코드는 그대로 옮김)

| 항목 | 현재 위치 | 새 주인 | 모듈 / 함수명(안) | 비고 |
|---|---|---|---|---|
| P8 regime_zone/in/slope (PRICE/RSI/STO/DI) | STAFF | **monitor_OZ** | `monitor_OZ.py` "OZ Snapshot Facts" 절의 `oz_regime_state(df)` | 수정본6의 OZFactMemo에 넣어 16개 프로필이 1번만 계산. Part2 allzone `_CORE_METHODS`에 추가 |
| P7 percentile_zone/in | STAFF | monitor_OZ (legacy 보존용) | 같은 절의 `legacy_percentile_zone(df)` | 읽는 곳 없음. 전환 기간 `staff_compat`에서만 호출하고, 끝나면 삭제 후보 |
| 4 Percentile OUT/IN (`percentile_states`) | monitor_OZ | monitor_OZ (변경 없음) | — | 이미 원시 열로 직접 판정 |
| P4 공용 ATR14 (ewm, TR[0] 시드) | STAFF | **strategy_INDICATOR** (공용 Fact) | `indicator_facts`의 Fact 레지스트리에 **`ATR14_GENERAL`**로 등록: 기존 `tr` Fact에 의존하는 `rma(tr, 14)` | 외부유동성(monitor_OZ ×1.5)과 SPECIAL4 전용 공용 Fact. 기존 STAFF `atr_14`와 비트 단위로 같음(13만 개 값 확인). 호환 열 이름 `atr_14`는 staff_compat가 이 Fact로 채우므로 SPECIAL4와 monitor_OZ `EXTERNAL_ATR_COLUMN`은 그대로 |
| FVG 전용 Wilder ATR(SMA 시드), FVG 생성·채움·만료 | strategy_FVG | strategy_FVG (**내부 Fact**) | `strategy_FVG.FVG_WILDER_ATR`: 지금 `_wilder_atr`의 이름만 바꿈. 기존 이름은 별칭으로 남김(Part2 `fvg_math`와 동일성 테스트가 참조) | FVG 크기 필터 전용. **indicator_facts 레지스트리에는 등록하지 않아서** INDICATOR·OZ·SPECIAL이 공용 Fact로 가져다 쓸 수 없음. 수정본6 구조 유지 |
| P11 STAFF식 FVG 특징 | STAFF | strategy_FVG | `strategy_FVG.legacy_staff_fvg_features` | Part2 event_catalog 전용 |
| P6 ema_21_slope, P9 basis slope | STAFF | **strategy_INDICATOR** | `indicator_facts.ema21_slope`, `basis_slopes` | 읽는 곳 없음. legacy 보존용 |
| P10 SuperTrend (STAFF판) | STAFF | strategy_INDICATOR | `indicator_facts.legacy_staff_supertrend` | indicator_facts의 `supertrend_dir`과 다른 정의라서 이름을 구분 |
| P12 Watch MA (SMA/WMA/EMA/HMAn) | STAFF가 호출 | watch_ma (클라이언트 쪽) | `watch_ma.MAFeatureCache`를 클라이언트 프로세스에서 사용 | 이력 확장 규칙(같은 source_epoch끼리 이어 붙이기)은 그대로 |
| P5 원비 | STAFF (Python) | **MT5** | EA `CalcOpenBands4` 확장 → 새 열 | **Python 계산 삭제**(§2.4) |
| P1–P3, P13 | STAFF | STAFF | — | 수신·검증 책임 |

> 새 모듈은 모두 Part1 안에 둡니다: `staff_schema.py`, `staff_snapshot.py`, `staff_compat.py`. 새 전략 모듈은 만들지 않습니다. 공용 Fact는 기존 `indicator_facts.py` 레지스트리에, FVG 내부 Fact는 `strategy_FVG.py`에 둡니다.
> Part2 `calculations/common.py`와 `watch/engines/derived.py`는 지금은 STAFF 함수의 복사본인데, 앞으로는 새 주인 모듈을 다시 내보내기만 합니다. 공용 계산 코드는 한 곳에만 둡니다.

### 2.4 원비: MT5가 기준 원본

1. **MT5 계산**
   - EA의 `CalcOpenBands4`가 이미 OPEN 기준 길이 4 평균과 모표준편차를 계산합니다.
   - 여기에 현재 σ로 만든 밴드를 새 열로 추가합니다.
     - `wonbi_mid`: `open_band_4_mid`와 같은 값
     - `wonbi_upper`, `wonbi_lower`: mid ± σ × sd
     - `wonbi_sigma`: 이 값이 계산될 때 쓴 σ
   - σ가 3.00이면 `wonbi_upper/lower`는 기존 `open_band_300_*`와 비트 단위로 같습니다(같은 식, 같은 변수).
2. **Python 계산 삭제**
   - `add_wonbi_features`를 없앱니다.
   - 클라이언트는 `wonbi_upper`, `wonbi_lower`, `wonbi_mid`, `wonbi_sigma` 열 이름을 지금처럼 그대로 읽습니다. 소비 코드(monitor_OZ :2336, :2378, :3954 / manager_KIM :4634 / SPECIAL4 :289)는 **바꾸지 않습니다.**
   - `wonbi_std`는 지금 읽는 곳이 없으므로 새 스키마에 넣지 않습니다. 필요하면 EA에 열 하나를 더하면 됩니다.
3. **σ 전달 경로** (파이프는 EA → Python 한 방향이라 별도 경로가 필요합니다)
   1. manager_KIM이 기존처럼 `SET_WONBI_SIGMA`를 STAFF로 보냅니다.
   2. STAFF가 `Common/Files/StaffOfMoses/wonbi_sigma.txt`에 원자적으로 기록합니다(tmp 파일 작성 후 이름 변경).
   3. EA는 1초 주기로 파일의 mtime을 확인하고, 바뀌었으면 다시 읽습니다.
   4. 다음 전송부터 새 σ로 계산하고, `wonbi_sigma` 열에 그 값을 담아 보냅니다.
   5. STAFF의 PING/SNAPSHOT 메타데이터에는 "요청된 σ"와 "MT5가 적용한 σ"를 함께 넣어, 적용이 끝났는지 확인할 수 있게 합니다.
   6. **의미 변화:** 지금은 σ 변경이 다음 요청에 즉시 반영됩니다. 앞으로는 다음 EA 전송(최대 약 1초)부터 반영됩니다. 전환 중인 약 1초 동안은 이전 σ의 밴드가 전달되고, 열에 적힌 σ로 이를 알 수 있습니다.
4. **Strategy Tester**
   - 테스터는 공용 파일을 쓸 수 없는 경우가 있습니다. 그래서 σ는 테스트 입력값 `InpWonbiSigma`(기본 3.0)으로 고정합니다.
   - 캡처 manifest에 σ를 기록합니다.
   - Part2 백테스트는 캡처된 `wonbi_*` 열을 그대로 사용합니다(재계산 금지).
5. **이전 캡처(v1, 45열) 재생**
   - v1에는 σ 밴드 열이 없습니다. 어댑터가 `wonbi_upper/lower`에 `open_band_300_*`를 그대로 매핑합니다. 이는 MT5 값이므로 원칙에 맞습니다.
   - σ가 3.0이 아닌 v1 재생은 지원하지 않고, 명시적 오류로 알립니다.

### 2.5 Snapshot 스키마와 호환·버전업

**Wire v2 헤더** (EA → STAFF, 테스터 캡처도 같은 레코드 구조)

```
<IIqIIIIII>  magic="SMOS", version=2, seq, sym_len, tf_len, bars, cols,
             schema_id(u32: 열 이름 목록의 CRC32), kind(1=FULL, 2=ROW, 3=HEARTBEAT)
payload      symbol, tf, time[int64], volume[int64], values[float64 × cols]
trailer      crc32(payload)   # 손상 감지
```

**규칙**
- **열 순서:** v2의 0–44열은 v1과 같은 이름·같은 순서입니다. 새 열은 뒤에만 붙입니다(append-only). 45: wonbi_upper, 46: wonbi_lower, 47: wonbi_sigma. `wonbi_mid`는 열 12의 별칭으로, 스키마 레지스트리에서 이름만 붙입니다.
- **스키마 레지스트리:** `staff_schema.py`에 둡니다(Part1). 버전별 열 이름 목록, schema_id, 별칭을 담습니다.
  - EA용 `.mqh` 상수는 이 파일에서 생성합니다.
  - Part2 `capture.py`는 여기서 읽습니다(Part2 → Part1 방향 import는 허용됨).
- **공존 기간:**
  - STAFF는 v1(45열)과 v2를 모두 받습니다.
  - 모르는 schema_id는 거부하고, 한국어 오류 로그와 SOURCE_HEALTH=UNAVAILABLE로 알립니다.
  - v1 입력은 어댑터가 `wonbi_*`를 열 17–18로 채워 v2 형태로 만듭니다.
- **FULL/ROW:**
  - FULL: 전체 봉(최대 650). 새 봉, 지표군 준비 상태 변화, 재연결 직후에 보냅니다.
  - ROW: 진행 중인 봉 한 줄만 보냅니다.
  - STAFF가 마지막 FULL에 ROW를 적용해 전체 Snapshot을 복원합니다. 이는 테스터 캡처 MSP2가 이미 쓰는 방식이므로 **LIVE와 테스터의 입력 경로가 구조상 같아집니다.**
- **HEARTBEAT:** 값이 바뀌지 않은 피드는 약 50바이트짜리 신호만 보내 freshness를 유지합니다.
- **클라이언트 쪽 Snapshot 객체:** `symbol, tf, seq, schema_id, kind, source_epoch, indicator_validity, received_at, wonbi_sigma_applied, time[], volume[], values[bars × cols](../../읽기전용)`. DataFrame은 필요할 때 한 번만 만들고 (key, seq) 단위로 캐시합니다.
- **테스터 캡처:** `pipe_%03d.bin`을 MSP3(v2 레코드 + schema_id)로 올립니다. MSP2 파일은 Part2 리더가 계속 읽습니다(v1 어댑터 사용).

### 2.6 legacy 요청 호환 (`staff_compat.py`, 전환 기간)

- 기존 요청 `{"symbol", "timeframes", "indicators", "watch_ma_history_rows"}`의 응답을 **클라이언트 쪽에서** 같은 모양으로 만들어 줍니다.
  - 입력: Snapshot, 옮겨진 순수 함수들
  - 출력: 열 순서, dtype, attrs(source_epoch, indicator_validity)가 지금과 같은 DataFrame
  - 원비 열은 MT5 값을 그대로 복사합니다.
- manager_KIM의 `SpecialPluginAPI.staff_request`가 이 어댑터를 쓰면 **SPECIAL1–7 파일은 한 줄도 바꾸지 않아도 됩니다.**
- 모든 클라이언트가 옮겨 가면, STAFF 쪽 legacy 처리 경로(`apply_requested_features`)를 삭제합니다.

### 2.7 LIVE와 Strategy Tester 입력 스키마 영향

| 경로 | 현재 | 변경 후 |
|---|---|---|
| LIVE 파이프 | v1, 45열, 매초 FULL | v2, 48열, FULL/ROW/HEARTBEAT, CRC |
| 테스터 캡처 `pipe_*.bin` | MSP2 (FULL/ROW, 45열) | MSP3 (v2 레코드). MSP2도 계속 읽음 |
| 테스터 네이티브 `feed_*.bin` (MNS1, 42열) | 퍼센타일 사전계산용 | 영향 없음. 원비 열을 추가할지는 별도 결정 |
| Part2 합성 입력 (`synthetic.py`) | 45열 생성 | 48열 생성. 원비는 EA식(2-pass 평균·분산)의 Python 포트로 **합성 데이터에서만** 생성. LIVE 경로에는 쓰지 않음 |
| Part2 `SecondFeed` / `pack_wire` | v1 바이트 재현 | v1과 v2 모두 바이트 재현 |

---

## ③ 단계별 마이그레이션 순서

각 단계는 별도 수정본 폴더에서 진행합니다. 단계마다 ⑤의 게이트를 통과해야 다음 단계로 넘어갑니다.

| 단계 | 내용 | 결과 변화 |
|---|---|---|
| **S0 기준선 고정** | `staff_golden` 기록기를 만듭니다. 합성일 240초, 테스터 캡처 샘플, 모든 요청 조합(지표 조합 × TF × Watch MA)에 대해 STAFF 응답 DataFrame의 해시를 저장합니다. 전체 경로 3-way(전 LIVE / 후 LIVE / 후 BACKTEST) 비교 도구를 수정본6의 `test_oz_fvg_optimization`을 일반화해 만들고, 성능 벤치마크(STAFF CPU, 파싱 시간, 요청 지연)도 기록합니다. | 없음 |
| **S1 순수 함수 이전** | STAFF의 P4, P6–P12 함수를 §2.3의 새 주인 모듈로 **그대로** 옮기고, STAFF는 그 함수를 import해서 씁니다. Part2 `common.py`/`derived.py`는 다시 내보내기로 바꿉니다. 원비(P5)는 이 단계에서도 아직 Python이 계산합니다. | 없음(비트 동일) |
| **S2 STAFF 내부 저장 구조 교체** | 수신 스레드가 numpy로 파싱하고 Snapshot 객체에 저장합니다. legacy 응답은 저장된 Snapshot에서 DataFrame을 만들어 기존과 똑같이 반환합니다. Part2 `live_replay/runtime.py`의 private 결합은 공개 생성자 인자(주입)로 바꿉니다. | 없음 |
| **S3 SNAPSHOT API와 클라이언트 라이브러리** | ZMQ에 `SNAPSHOT` 요청을 추가합니다(메타데이터 dict + 원시 배열 multipart, pickle 없음). `staff_snapshot.py`(캐시)와 `staff_compat.py`를 만듭니다. legacy API는 유지합니다. | 없음 |
| **S4 클라이언트 이전** (하위 단계마다 게이트) | S4a strategy_SWEEP/FVG/INDICATOR(원시 열만 사용) → S4b watch_ma/watch_orchestrator(MA 캐시를 클라이언트로) → S4c monitor_OZ(regime Fact를 직접 계산, OZFactMemo 사용, 외부유동성은 `ATR14_GENERAL` 사용) → S4d manager_KIM과 SpecialPluginAPI(staff_compat 경유, SPECIAL 파일은 그대로) | 없음 |
| **S5 STAFF 계산 제거** | STAFF에서 `apply_requested_features`와 모든 파생 함수 호출을 없앱니다. legacy 요청이 오면 "SNAPSHOT 사용" 오류를 보냅니다(클라이언트가 모두 옮긴 뒤). STAFF는 수신·검증·저장·전달만 합니다. | 없음 |
| **S6 Wire v2 (원비 열 제외)** | EA와 STAFF에 헤더 v2, schema_id, CRC, FULL/ROW/HEARTBEAT, HELLO를 넣습니다. 테스터 캡처는 MSP3로 바꾸고, Part2 capture/engine/synthetic을 v2 대응으로 고칩니다. 열은 아직 45열이고 v1 입력도 계속 받습니다. | 없음 |
| **S7 원비 MT5 원본 전환** | EA가 `wonbi_upper/lower/sigma`(σ 파일 반영)를 보냅니다. STAFF는 σ 파일을 기록하고 메타데이터로 전달합니다. `staff_compat`는 원비를 MT5 열에서 복사하고, Python `add_wonbi_features`는 삭제합니다. manager_KIM `_sync_wonbi_sigma`는 그대로 두되, 적용 확인 로그를 추가합니다. | **의도된 변경**: 원비 경계 판정만 새 기준선으로 고정 |
| **S8 파이프·EA 최적화** | §6.1의 최적화 항목을 넣습니다(변경 없음 피드 전송 생략, 상태 파일 주기 1초, 증분 HMA 등). | 없음 |

> **순서의 이유**
> - 결과 불변 단계(S1–S6)를 먼저 모두 끝냅니다. 그래야 유일한 의도된 변경(S7)이 다른 변경과 섞이지 않아서, S7에서 달라지는 판정을 원비 때문이라고 증명할 수 있습니다.
> - S7은 S6(v2 열 추가 체계) 없이는 할 수 없습니다.
> - EA를 바꾸는 단계는 S6·S7·S8 세 번뿐입니다. 이 단계들은 MT5 컴파일과 테스터 실행으로 확인해야 하며, 이 샌드박스에서는 검증할 수 없습니다(§6.3).

---

## ④ 영향 파일 목록

표기: **수정** / *신규* / (확인만) — 괄호 안은 해당 단계입니다.

### Part1
| 파일 | 영향 |
|---|---|
| `program/THE STAFF OF MOSES.py` | **수정**: 계산 제거, 수신 스레드, Snapshot 저장, SNAPSHOT API, σ 파일 중계 (S1–S7) |
| `program/MT5/THE_STAFF_OF_MOSES.mq5` | **수정**: Wire v2, 원비 σ 열, σ 파일 읽기, FULL/ROW/HEARTBEAT, 최적화 (S6–S8) |
| `program/MT5/STAFF_Identity_Status.mqh` | **수정**: 기록 주기 1초 또는 변경 시에만 (S8) |
| `program/MT5/STAFF_Schema.mqh` | *신규*: `staff_schema.py`에서 생성하는 열 상수 (S6) |
| `program/staff_schema.py`, `staff_snapshot.py`, `staff_compat.py` | *신규* (S3, S6) |
| `program/monitor_OZ.py` | **수정**: regime Fact 이전, SNAPSHOT 사용, 외부유동성 ATR을 `ATR14_GENERAL` Fact에서 가져옴 (S1, S4c) |
| `program/indicator_facts.py`, `strategy_INDICATOR.py` | **수정**: `ATR14_GENERAL` Fact 등록, legacy 파생 함수 받기, SNAPSHOT 사용 (S1, S4a) |
| `program/strategy_FVG.py` | **수정**: `_wilder_atr` → `FVG_WILDER_ATR` 이름 변경(별칭 유지), STAFF식 FVG 특징 받기, SNAPSHOT 사용 (S1, S4a) |
| `program/strategy_SWEEP.py` | **수정**: SNAPSHOT 사용 (S4a) |
| `program/watch_ma.py`, `watch_ma_features.py`, `watch_orchestrator.py` | **수정**: MA 캐시를 클라이언트 쪽으로 (S4b) |
| `program/manager_KIM.py` | **수정**: StaffClient, `_poll_wonbi`, `SpecialPluginAPI.staff_request` → staff_compat, σ 적용 확인 (S4d, S7) |
| `program/durable_protocol.py` | (확인만) source_health 형식 유지 |
| `program/SPECIAL/SPECIAL1–7.py` | (확인만) **변경 없음**. staff_compat가 같은 프레임을 제공 |
| `program/config.txt` | **수정**: `STAFF_WONBI_SIGMA_FILE`, `STAFF_WIRE_VERSION` 추가, BTC 허용 심볼 정리 여부 결정 (S6, S7) |
| `audit/harness.py`, `test_baseline.py`, `test_ack_pressure.py`, `test_slow_feed.py`, `test_oz_trigger_profiles.py`, `test_pipe_security.py` | **수정**: Snapshot 구조와 v2에 맞춤. 기존 계약(30초 stale, 중복 seq, 주말, 요청 범위 오류)은 유지 |
| `audit/source_integrity.py`, `fixtures/source_manifest.json`, `remediation/2x-*/changes.json` | **수정**: 단계마다 해시 체인 단위 등록 |
| `watch_ma_validation/*` | **수정**: MA 계산 위치 변경 반영 |

### Part2
| 파일 | 영향 |
|---|---|
| `part1_host/capture.py` | **수정**: v2 헤더/MSP3, `pack_wire` v1·v2, 스키마 레지스트리 사용 |
| `part1_host/engine.py` (`SecondFeed`) | **수정**: FULL/ROW 재생, v2 발행 |
| `part1_host/runtime.py` | **수정**: private 메서드 몽키패치(`_read_exact`, `_consume_one`) 대신 공개 주입점 사용 |
| `part1_host/synthetic.py` | **수정**: 48열 생성(원비는 EA식 포트, 합성 전용) |
| `part1_host/loader.py` | (확인만) 새 Part1 모듈 로드 목록 |
| `live_replay/runtime.py` | **수정**: `NativeCache`, `StaffServer`의 private 재선언 제거 |
| `live_replay/trace_io.py`, `synthetic.py`, `synthetic_specials.py`, `event_catalog.py` | **수정**: 상수는 staff_schema에서, 파생 함수는 새 주인 모듈에서 가져옴 |
| `calculations/common.py`, `generic_backtest/watch/engines/derived.py` | **수정**: 복사본 대신 다시 내보내기. 원비 함수 삭제 |
| `generic_backtest/watch/engines/frames.py`, `fvg_math.py`, `oz.py`, `oz_rules.py` | **수정**: 열 이름·ATR 출처 정리. `fvg_math`는 `FVG_WILDER_ATR`을 참조(기존 `_wilder_atr` 별칭도 유지) |
| `validation_suite/test_shared_live_calculations.py` | **수정**: FVG ATR 동일성 검사를 새 이름으로. `ATR14_GENERAL` 동일성 검사 추가 |
| `calculations/allzone.py` | **수정**: `_CORE_METHODS`에 regime Fact 메서드 추가 |
| `generic_backtest/watch/engines/source_contract.json`, `local_contract.json` | **수정**: 해시 갱신. 낡은 STAFF 해시 문제 해소 |
| `data_warehouse/native.py`, `generic_backtest/native_mt5.py` | (확인만) MNS1 42열은 영향 없음 |
| `validation_suite/test_part1_host_parity.py`, `test_oz.py`, `test_oz_fvg_optimization.py`, `test_indicator_regression.py`, `test_native_*` | **수정**: v2 입력과 원비 열 반영 |
| `validation_suite/test_staff_responsibility.py` | *신규*: 단계별 게이트(§⑤) |

### Part3 / 루트
| 파일 | 영향 |
|---|---|
| `Part3/calculations/common.py`, `reference_sources/*.mq5`, `generic_backtest/reference_sources/*.mq5` | **수정**: Part2 복사본과 동기화(DataManager 참조용) |
| `tests/live_parity/test_source_and_standalone.py`, `tests/sparse_events/test_event_catalog.py` | **수정**: v2 헤더 seq 위치 등 |
| `build/part1_immutable_sha256.json` | **수정**: 단계마다 재생성 |

---

## ⑤ 각 단계 회귀테스트 기준

### 공통 게이트 (모든 단계)
- **G1 기존 전체 테스트:** Part1 audit 178개(원본 해시 1건만 기존 실패), watch_ma 187개, Part2 660개(새 실패 0), 루트 263–264개. 루트의 자정 체크포인트 테스트는 기존의 간헐적 실패로 따로 표시합니다.
- **G3 전체 경로 3-way parity:** 전 LIVE / 후 LIVE / 후 BACKTEST를 비교합니다.
  - 합성일 240초, SPECIAL1–7(단계마다 순환), Watch 8개 이상(OZ 6종, FVG 조건, FVG 생성, MA Watch, 원비 Watch)
  - 비교 항목: finals, SPECIAL 알림, 텔레그램 문구·시각·수신자, 16개 OZ 프로필 상태, FVG 엔진 상태, 김매니저가 받은 이벤트, Watch 결과
  - 비어 있는 시나리오가 아님을 확인(원비 터치·OZ 후보·SPECIAL 발생)
- **G5 성능:** 단계 전보다 느려지지 않아야 합니다(S0 벤치마크 기준 +5% 이내).

### 단계별 게이트
| 단계 | 추가 게이트 |
|---|---|
| S0 | 골든 기록기가 두 번 실행에서 같은 해시를 내야 합니다(결정성). |
| S1 | **G2 골든 동일성:** 모든 요청 조합에서 STAFF 응답이 S0 골든과 `assert_frame_equal(check_exact=True)`, dtype, 열 순서, attrs까지 같아야 합니다. 옮긴 함수는 원본과 AST가 같아야 합니다(`common.py` 검사와 같은 방식). 새 주인 모듈이 STAFF를 import하지 않는지 정적 검사합니다. **ATR 섞임 방지 테스트:** ① `ATR14_GENERAL`이 S0 골든의 STAFF `atr_14`와 비트 단위로 같음. ② 고정 fixture에서 `ATR14_GENERAL ≠ FVG_WILDER_ATR`(시드가 달라 처음 구간이 다름). 누가 둘을 합치면 이 테스트가 실패하므로 합칠 때는 명시적 결정이 필요. ③ 정적 검사: strategy_FVG는 `ATR14_GENERAL`을 참조하지 않고, monitor_OZ·manager_KIM·SPECIAL은 `FVG_WILDER_ATR`/`_wilder_atr`을 참조하지 않음. `FVG_WILDER_ATR`은 indicator_facts `FACTS`에 없음. |
| S2 | G2, 그리고 파이프 견고성 테스트(신규): 잘린 프레임, 중복·역행 seq, 재연결 뒤 seq 1 재시작, 느린 소비자(수신 스레드는 막히지 않음), 손상 헤더. 기존 30초 stale, 주말, FEED_NOT_READY 계약도 유지되어야 합니다. |
| S3 | 모든 legacy 요청 조합에서 "legacy 응답"과 "SNAPSHOT + staff_compat 결과"가 비트 단위로 같아야 합니다(속성 기반 테스트, 무작위 조합 500개 이상). (key, seq) 캐시는 같은 seq에서만 재사용되고, 반환 객체를 바꿔도 캐시가 오염되지 않아야 합니다. |
| S4a–d | 하위 단계마다 G3. S4c: OZFactMemo 재사용 횟수 > 계산 횟수. S4d: SPECIAL1–7 파일 해시가 그대로여야 합니다. |
| S5 | G2의 대상을 "staff_compat 결과"로 바꿔 S0 골든과 같아야 합니다. STAFF 소스에 파생 함수 이름이 없어야 하고, 수신 경로에 pandas를 쓰지 않아야 합니다(정적 검사). |
| S6 | **G4 Wire:** v1 `pack_wire` 결과가 S0와 바이트 단위로 같아야 합니다. v2 왕복 인코딩이 동일해야 하고, FULL+ROW를 복원한 Snapshot이 같은 초의 FULL 전체와 같아야 합니다. 예전 MSP2 캡처를 재생한 결과가 S5와 같아야 합니다. MT5 테스터에서 만든 MSP3 캡처와 같은 구간 LIVE 기록을 비교해 같아야 합니다(Windows 실측). CRC 손상 프레임은 폐기되고 기록되어야 합니다. |
| S7 | **원비 전환 게이트** (아래 별도 설명) |
| S8 | G3. EA의 변경 없음 피드 생략과 HEARTBEAT가 freshness·source_epoch 규칙을 바꾸지 않아야 합니다(SOURCE_HEALTH 골든과 비교). |

### S7 원비 전환 게이트 (의도된 변경만 허용)
1. **원비 외 동일성:** 원비를 읽지 않는 모든 결과(OZ 비원비 트리거, FVG, INDICATOR, SWEEP, Watch MA, 원비를 쓰지 않는 SPECIAL)가 S6와 비트 단위로 같아야 합니다.
2. **LIVE↔백테스트 동일성:** 원비를 포함한 모든 결과가 후 LIVE와 후 BACKTEST에서 완전히 같아야 합니다(둘 다 MT5 값을 쓰므로).
3. **원비 차이 감사:** S6 대비 달라진 알림을 모두 나열합니다. 각 항목이 `|가격 − 원비선| < 1e-6`인 경계 사례인지 확인해 보고서(`검증결과/wonbi_mt5_switch_audit.json`)로 남깁니다. 경계가 아닌 차이가 1건이라도 있으면 실패입니다.
4. **σ 테스트:** σ를 3.0 → 2.5 → 3.0으로 바꾸면 `wonbi_sigma` 열이 1초 안에 새 값으로 바뀌어야 합니다. 바뀌기 전 스냅샷은 이전 σ로 판정되어야 합니다. 테스터 입력 σ가 manifest에 기록되어야 합니다.
5. **기준선 재고정:** 1–4를 통과하면 S7 결과를 새 골든으로 저장하고, 이후 단계의 기준으로 씁니다.

---

## ⑥ 예상 성능개선과 위험요소

### 6.1 Named Pipe 안정성·CPU 최적화 포인트
| 위치 | 현재 | 제안 | 예상 효과 |
|---|---|---|---|
| EA 전송량 | 피드마다 매초 650봉 FULL(약 244KB) | FULL은 새 봉일 때만, 그 외에는 ROW(약 0.4KB), 변화 없으면 HEARTBEAT | 파이프 바이트 **약 99% 감소**. 초당 약 14MB → 수십 KB |
| EA 계산 | 매 전송마다 HMA 4개 × 682봉, CopyBuffer 24번 × 682 | ROW는 마지막 봉만 계산·복사(OPEN 기준 값은 봉 안에서 고정). 증분 HMA | EA CPU 크게 감소(실측 필요) |
| EA 상태 파일 | 100ms마다 기록·이동 | 1초마다 또는 변경 시에만 | 디스크 I/O 90% 감소 |
| EA 막힘 | Python이 느리면 `OnTimer`가 멈춤 | 프레임 축소와 STAFF 전용 수신 스레드(memcpy만)로 항상 즉시 비움 | 막힘 위험 제거 |
| STAFF 파싱 | 메시지마다 pandas 생성, 정렬, 중복 제거, 복사 여러 번 | numpy `frombuffer` 뒤 읽기전용 보관. 정렬·중복 검사는 time 배열 단조성만 확인 | 수신 CPU **80–90% 감소(추정)** |
| STAFF 응답 | 요청마다 ATR·원비 계산, 깊은 복사 2번, DataFrame pickle | 계산 없음. 원시 배열 multipart 전송, (key, seq) 인코딩 캐시 | 요청 처리 시간 대부분 제거 |
| 무결성 | 없음 | HELLO(schema_id, EA 빌드 해시), CRC32, seq 간격 감지, 재연결 백오프 | 손상·버전 불일치를 조용히 넘기지 않음 |
| 클라이언트 | 요청마다 DataFrame 받음 | (key, seq) 캐시. 같은 seq면 재사용(16개 OZ 프로필 공유) | 중복 계산 제거 |

**전체 예상**
- 수정본6 합성 240초 기준 Part1 전체 경로는 111초입니다. 그중 절반 이상이 STAFF 요청 처리이므로 **약 55–70초(시장 1초당 0.25–0.3초)로 줄 것으로 추정**합니다. 정확한 값은 S0 벤치마크로 확정합니다.
- LIVE CPU는 파이프 수신과 pandas 제거 효과가 가장 큽니다.

### 6.2 원비 MT5 전환으로 생기는 변화 (측정값)
- **값 차이:** pandas(rolling 누적 합계 방식)와 MQL(창마다 2-pass 평균·분산)은 계산 순서가 달라 끝자리가 다릅니다. 합성 OPEN 200개 × 650봉에서 원비 상단 값의 **99.4%가 끝자리에서 달랐고**, 최대 차이는 1.4×10⁻⁸이었습니다.
- **판정 차이:** `low ≤ 원비하단`, `high ≥ 원비상단` 판정 388,200번 중 **2번(약 백만 번에 5번)**이 바뀌었습니다. 주로 가격이 0.01 단위라서 밴드가 가격과 거의 같은 값에 놓이는 경우(창 안의 가격이 같을 때 등)입니다.
- **결론:** 사용자 결정에 따라 MT5를 원본으로 삼으면 이 극소수 경계 판정이 바뀝니다. S7에서만 허용하는 **의도된 변경**이며, 감사 보고서로 한 건씩 확인합니다(⑤ S7-3).
- **장점:** 원비가 LIVE, 테스터, Part2에서 하나의 출처(MT5)가 되어, 이후에는 이 불일치가 생길 수 없습니다.

### 6.3 위험요소와 대응
| 위험 | 내용 | 대응 |
|---|---|---|
| EA 변경은 여기서 검증할 수 없음 | MQL 컴파일, 테스터, 실제 파이프는 Windows와 MT5에서만 확인 가능 | S6–S8은 사용자 PC 실측 게이트를 둠. 샌드박스에서는 Python 쪽 v2 재현과 바이트 테스트까지 |
| σ 적용 지연 | 변경이 즉시가 아니라 다음 전송(최대 약 1초)부터 반영 | `wonbi_sigma` 열로 적용 여부를 드러냄. 전환 로그. S7-4 테스트 |
| 테스터 σ | 테스터는 공용 파일 대신 입력값 사용 | manifest에 기록. Part2가 σ 불일치를 감지하면 경고 |
| ATR 정의 혼동 | 공용 ATR14(외부유동성·SPECIAL4)와 FVG 전용 Wilder ATR은 정의가 다름 | 이름과 위치를 나눔: 공용 `ATR14_GENERAL`(indicator_facts 레지스트리), FVG 내부 `FVG_WILDER_ATR`(strategy_FVG, 레지스트리 미등록). 섞임 방지 테스트 3종(⑤ S1)으로 고정. **두 정의의 통합은 결과가 바뀌므로 이번 범위에서 제외**(별도 결정 사항) |
| Part2 private 결합 | STAFF 내부 필드가 바뀌면 live_replay가 깨짐 | S2에서 공개 주입점으로 먼저 바꾼 뒤 내부를 교체 |
| Watch MA 이력 의미 | STAFF가 요청 시점에 이력을 이어 붙이던 규칙이 클라이언트로 이동 | 같은 source_epoch 규칙과 요청 시점 확장을 그대로 옮기고, S4b에서 MA Watch 골든 비교 |
| source_epoch 의미 | FULL/ROW·HEARTBEAT 도입으로 카운터 증가 조건이 달라질 수 있음 | 기존 증가 조건(최초, stale 공백, 재연결, 유효성 변화)만 유지. HEARTBEAT와 ROW는 증가시키지 않음. SOURCE_HEALTH 골든으로 검증 |
| v1/v2 공존 | 설정 실수로 섞여 들어올 수 있음 | 피드별 schema_id 기록. 섞이면 경고. v1 지원 종료 시점은 S7 이후 별도 결정 |
| 무결성 해시 체인 | STAFF/mq5를 수정할 때마다 audit 해시 체인 등록이 필요 | 단계마다 `remediation/2x-*` 단위 등록을 체크리스트에 포함 |
| 멀티 프로세스 중복 계산 | 계산이 클라이언트로 옮겨가면서 프로세스마다 같은 Fact를 계산할 수 있음 | 파생값을 실제로 쓰는 곳이 거의 monitor_OZ 하나라 중복은 작음. 필요하면 Fact 결과를 Snapshot 메타데이터로 공유하는 것은 후속 과제 |
| 쓰지 않는 열·파생값 삭제 | 삭제하면 외부 도구가 깨질 수 있음 | S5까지는 legacy 경로로 보존. 삭제는 별도 승인 후 |

### 6.4 결정이 필요한 사항 (구현 착수 전)
1. **v1 파이프 지원 종료 시점:** S7 이후 몇 주 동안 병행할지.
2. **BTCUSD:** EA가 보내는데 STAFF가 거부하는 현재 상태를 정리할지(허용 목록에 추가 또는 EA 기본값에서 제거).
3. **쓰지 않는 파생값(P6, P7, P9, P10)과 open_band 8열, ema_20:** 삭제할지 보존할지.
4. **MNS1(백테스트 네이티브 42열)에 원비 열을 추가할지:** 현재 설계는 pipe 캡처(MSP3)만 원비를 담습니다.
