# 수정본84 공통 Strategy / Recipe v2 계약

SPECIAL은 전략 번호에 따른 실행 코드가 아니라 `settings/strategy_registry.json`의 기본 preset입니다. AI 생성 전략과 preset은 같은 계약 검증, execution_plan, IntentPort / IntentMachine을 사용합니다.

## preset 추가·수정·삭제

registry의 `presets` 배열에 항목을 추가합니다. `id`는 중복되지 않는 영문 대문자·숫자·밑줄 이름이고, `name`은 화면에 표시할 이름입니다. 예약된 엔진 이름은 사용할 수 없습니다.

```json
{
  "id": "CUSTOM_CLOSE",
  "name": "15분봉 마감 알림",
  "symbol_source": "CONFIG",
  "time_filters": [],
  "final_time_filters": 0,
  "recipe": {
    "schema_version": 2,
    "base": "AI",
    "name": "15분봉 마감 알림",
    "description": "새 15분봉 마감 사건을 알림",
    "symbols": [],
    "strategy_intent": {
      "direction": "LONG",
      "symbols": [],
      "steps": [{"kind": "BAR_CLOSE", "tf": "15m"}],
      "order_mode": "SEQUENTIAL",
      "persistent": true,
      "final": {"kind": "NOTIFY"}
    }
  }
}
```

`symbol_source: "CONFIG"`이면 실행할 때 공통 종목 설정을 사용합니다. 특정 종목을 고정할 때는 이 항목을 생략하고 Recipe와 intent의 `symbols`에 실제 종목을 지정합니다. 빈 공통 종목 설정으로 임의 종목을 만들어 실행하지 않습니다.

수정은 해당 `recipe.strategy_intent`와 표시 이름·시간 설정을 변경하고, 삭제는 해당 항목을 배열에서 제거합니다. 같은 전략을 수정할 때는 ID를 유지합니다. 변경 후 실행 중인 엔진을 종료하고 다시 시작하여 새 목록과 계약 검증을 적용합니다. 새 preset을 실행할 때는 전략 선택에서 활성화합니다. Python parser / validator / executor에 번호별 코드를 추가하지 않습니다.

## 공통 조건과 조합

| 항목 | 의미·단위 |
| --- | --- |
| `steps` | 상태 또는 사건 조건 목록. 사건은 같은 입력을 반복 수신해도 같은 사건으로 다시 소비하지 않음 |
| `order_mode` | `SIMULTANEOUS`: 현재 조건 함께 판정, `SEQUENTIAL`: 목록 순서, `UNORDERED`: 순서 없이 필요한 사건 수집 |
| `global_combine` | 동시 조건의 `ALL` / `ANY` / `INDEPENDENT`; 연쇄는 `ALL` |
| `within_sec` | 첫 사건부터 연쇄 완성까지의 양의 정수 초. SEQUENTIAL / UNORDERED에서 생략 가능 |
| `final_window_sec` | 선행 조건 완성 뒤 최종 감시 시간, 양의 정수 초. 생략하면 이 시간 제한 없음 |
| `after_conditions` | setup 완성 뒤 추가로 충족해야 하는 조건 |
| `final_conditions` | 최종 알림 시점에도 충족해야 하는 조건 |
| `cancel_conditions` | 하나라도 충족하면 진행 중 연쇄·감시 취소 |
| `branches` | 같은 공통 설정 아래 독립 조건 분기. 분기의 `final.kind`가 바뀌면 이전 종류의 전용 필드는 제거 |
| `negated` | 준비된 조건 판정의 반대. 데이터 부재를 조건 충족으로 취급하지 않음 |
| `tf` / `tfs` | 실제 시간봉 또는 `SOURCE`(소비한 선행 사건 봉), `FINAL`(해당 최종 감시 봉) 참조 |
| `tf_combine` | 여러 봉의 `ALL` / `ANY` / `INDEPENDENT`. FINAL 참조는 최종 봉별 독립 감시가 필요할 수 있음 |
| `time_filters` / `final_time_filters` | 공통 세션 이름 또는 설정된 시간 구간. 각각 조건 진행 / 최종 출력 허용 시간 |

지원 조건은 추세·MA 상태/기울기/교차/터치, FVG 상태/생성/터치, WONBI 터치, 외부유동성 터치·가격 레벨, 세션 시작, 봉 마감, OZ 사건, 레짐·Percentile 조건 및 등록된 지표 비교입니다.

`TREND_METRIC`은 등록된 Fact 지표 이름을 `GT/GTE/LT/LTE/EQ/NE`로 비교합니다. `metric_value`의 단위는 해당 지표의 기존 단위이며 새 계산식을 추가하지 않습니다. `PERCENTILE_OUT`과 `PERCENTILE_OUT_IN`은 PRICE / RSI / STO / DI 계열의 기존 상태를 사용합니다. `PRICE_LEVEL`은 숫자 가격 또는 `DAY_OPEN` 등 지원 레벨을 비교합니다.

## 사건 참조와 수명

| 항목 | 의미·단위 |
| --- | --- |
| `capture` | FVG / OZ 사건 자원을 이름으로 고정 |
| `ref` | 후속 FVG / OZ 조건에서 같은 자원을 참조 |
| `scope_ref` | 앞서 캡처한 자원의 범위·생존 조건으로 후속 조건 또는 최종 OZ를 제한 |
| `lifecycle.expires.seconds` | 활성화부터 양의 정수 초; 만료 시각부터 취소 |
| `lifecycle.expires.bars` + `tf` | 활성화 뒤 지정 시간봉의 마감 봉 수, 양의 정수. 지정 수를 초과하면 취소. seconds와 동시 사용 금지 |
| `lifecycle.snapshots` | 활성화 경계에서 이미 확정된 봉의 open / high / low / close / ATR14를 이름별로 저장. 이후 값이 바뀌어도 저장값 유지 |
| `lifecycle.excursion` | 지정 tf에서 anchor 스냅샷 대비 이동 폭이 snapshot 값 × 양의 multiplier 이상이면 취소. `FAVORABLE`은 전략 방향, `ADVERSE`는 반대 방향 |
| `lifecycle.replace.scope` | 새로운 선행 사건으로 활성화를 교체. `SYMBOL_DIRECTION`은 동일 종목·방향의 이전 감시 묶음을 교체 |
| `lifecycle.first_success` | 해당 활성화의 첫 최종 성공 뒤 같은 부모 사건에서 파생된 감시 종료 |
| `lifecycle.invalidate_refs` | 기존 FVG / OZ 처리기가 캡처 자원의 무효화를 확인하면 그 단계와 후속 단계를 되돌림. 일시적 데이터 부재는 무효화 증거가 아님 |
| `lifecycle.restart_on` | 지정된 새 사건이 발생하면 기존 진행을 초기화하고 새 연쇄를 시작 |
| `persistent` | 기본 true. false면 해당 실행의 첫 출력 뒤 추가 활성화 중단 |

시간 제한 없는 연쇄는 첫 사건을 소비한 뒤 다음 사건을 기다립니다. 자동으로 한 봉이나 일정 시간 뒤 초기화하지 않습니다. SEQUENTIAL의 연속 사건은 실제 시간상 뒤에 발생해야 하며, 같은 시각의 두 사건은 “A 이후 B”가 아닙니다. 앞선 상태 조건은 뒤 단계와 최종 판정에서도 유지되어야 합니다.

명시적 취소·restart·수명 만료·참조 무효화와 현재 상태 조건의 해제는 진행 상태를 종료하거나 필요한 단계로 되돌립니다. 사건형 NOTIFY는 소비 후 다음 새 사건을 기다리고, 상태형 조건은 계속 참이라는 이유만으로 반복 활성화하지 않습니다. 조건이 해제되고 다시 충족되거나 새 선행 사건이 있어야 다음 활성화가 가능합니다. 엔진 종료도 무제한으로 계속 실행된다는 뜻은 아닙니다.

## MA와 봉 기준

MA는 기존 Part1 Fact / 비교 계층을 사용하며 SMA / WMA / EMA / HMA와 임의 양의 정수 기간을 지원합니다. 전략 안에 MA 계산식을 복제하지 않습니다.

`MA_CROSS`, `MA_PRICE_CROSS`, `MA_PRICE_TOUCH`는 `bar_state`를 생략하면 확정봉 기준입니다. CLOSED는 마지막 확정봉과 필요한 이전 확정봉을 비교하고, 등록 때 이미 존재하던 과거 사건을 새 사건으로 알리지 않습니다. FORMING을 명시하면 진행봉 변화를 관찰하며 사건의 새 진입을 구분합니다. 상태형 MA 조건은 봉 기준을 생략하면 현재 진행봉을 사용하므로 확정봉 의도라면 CLOSED를 지정합니다.

`MA_PRICE_CROSS.relation`은 BREAK_UP / BREAK_DOWN / BOTH를 지원합니다. `MA_PRICE_TOUCH`는 봉 저가 ≤ MA ≤ 봉 고가로 판정합니다. `MA_PRICE_STATE.price_tf`로 가격 봉과 MA 봉을 구분할 수 있습니다. 일반 PRICE_LEVEL / LIQUIDITY_LEVEL의 교차에는 BOTH를 허용하지 않습니다. FVG_NEW / 외부유동성 터치 / BAR_CLOSE는 기존 처리기의 확정봉 사건이고, OZ_ALERT는 기존 OZ 사건 시각을 사용합니다.

## 실행 경로와 이전 생성 파일

정식 경로는 canonical schema → intent / Recipe v2 → 공통 contract → execution_plan → compiler → 생성 Python → register입니다. Part1의 `strategy_recipe`가 공통 계약과 실행을 제공하며 Part1 / Part2는 Part3를 import하지 않습니다. 생성 전략도 독립적인 register와 공통 IntentPort를 사용합니다. 기존 창고 데이터를 새로 구축할 필요는 없습니다.

과거 `INHERITED` 방식으로 생성한 파일은 원본을 보존하며 자동으로 내용을 덮어쓰지 않습니다. 공통 Recipe 경로를 사용하려면 저장된 조건을 확인하고 재생성해야 합니다. 이전 파일의 전략별 내장 실행 규칙이 이번 공통 계약으로 자동 전환되었다고 간주하지 않습니다.

이 문서는 지원되는 schema 표현과 실행 계약을 설명합니다. 교육 1~100의 문장을 실제 AI 모델이 모두 정확하게 해석했다는 의미가 아닙니다. 자연어 해석 정확도와 공통 schema 표현 가능성은 별도로 확인하며, 적용 전에 해석된 전체 조건을 사용자가 확인합니다.
