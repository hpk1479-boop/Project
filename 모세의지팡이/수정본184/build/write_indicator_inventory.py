"""Produce the explicit schema/Fact/consumer inventory, not a lexical usage oracle."""
from pathlib import Path
import sys,ast,json
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'Part1/program'))
import staff_schema as wire
from indicator_facts import FACTS
OLD=ROOT/'검증결과/schema_cleanup/hosts/revision22/Part1/program/staff_schema.py'
tree=ast.parse(OLD.read_text('utf-8'))
old=next(ast.literal_eval(n.value) for n in tree.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='PIPE_VALUE_COLUMNS' for t in n.targets))
for n in tree.body:
    if isinstance(n,ast.AugAssign) and isinstance(n.target,ast.Name) and n.target.id=='PIPE_VALUE_COLUMNS':old+=ast.literal_eval(n.value)

def use(name):
    if name in ('open','high','low','close'):return 'OZ·SWEEP·FVG·INDICATOR·WATCH·COMPOSER'
    if name=='ema_21':return '이전 EMA 기울기/Watch 지정 기간 → EMA20 통합'
    if name=='ema_20':return 'Watch EMA20(명령 지정), COMPOSER EMA 파생값'
    if name in ('ema_50','ema_200'):return 'Watch EMA 교차, SPECIAL4 EMA50/200 정배열·역배열'
    if name in ('hma_6','hma_17'):return 'OZ 교차·색전환, Watch HMA 교차, SPECIAL'
    if name=='hma_50':return 'INDICATOR 추세·점수, Watch 가변 MA의 고정 원본'
    if name=='hma_168':return 'SPECIAL4 HMA168 기울기, Watch HMA168 지정'
    if name.startswith('open_band_') and name!='open_band_4_mid':return '전략·처리기 실제 사용 없음: 운반/옛 호환 전용, 이번 삭제'
    if name=='wonbi_sigma':return '이전 전달 σ 검사 전용, 이번 삭제'
    if name in ('open_band_4_mid','wonbi_upper','wonbi_lower'):return 'WONBI_BANDS 공용 Fact → OZ/Watch/COMPOSER/SPECIAL; INDICATOR OPEN4 std'
    if name=='price_hma_6':return 'CLOSE 기반 PRICE 판정선: OZ·Watch·COMPOSER'
    if name.endswith(('_lower_out','_upper_out')):return 'OZ/Watch 관측별 OUT·OUT→IN, COMPOSER 상태 열'
    if name.endswith('_regime_slope'):return 'OZ 레짐, COMPOSER slope 별칭; MT5 원본'
    if name.endswith('_basis') or name=='price_regime_basis':return 'OZ SUPER 기준선, COMPOSER 레짐; RSI/STO/DI basis 자체가 레짐 basis'
    return 'OZ 퍼센타일/레짐·Watch OUT·COMPOSER 조건'

lines=['# 지표 정리 목록 — 수정본23','',
       '열 순서의 원본은 `Part1/program/staff_schema.py::PIPE_VALUE_COLUMNS`다. 기존 48열에서 EMA21 1열·원비 9열을 제거하고 OUT/기울기 12열을 붙여 50열이다.',
       f'현재 schema_id: `0x{wire.WIRE_SCHEMA_ID:08X}` (`{wire.WIRE_SCHEMA_ID}`). 새 수정본은 이 스키마만 수신한다.','',
       '## 기존 EA 48열 조사','', '| 기존 번호(0 기준) | 이름 | 처리 | 실제 소비/비고 |','|---:|---|---|---|']
for i,c in enumerate(old):lines.append(f'| {i} | `{c}` | '+('유지' if c in wire.PIPE_VALUE_COLUMNS else '삭제')+f' | {use(c)} |')
lines+=['','## 최종 Wire 50열','', '| 새 번호 | 열 | 소비 |','|---:|---|---|']
for i,c in enumerate(wire.PIPE_VALUE_COLUMNS):lines.append(f'| {i} | `{c}` | {use(c)} |')
lines+=['','별칭 `wonbi_mid = open_band_4_mid`는 새 Wire 열을 추가하지 않는다. time/volume은 별도 배열이다. FULL/ROW/HEARTBEAT·CRC·seq·묶음 구조는 유지한다.',
'','## 공용 Python Fact 등록부','', '| Fact | 직접 의존 | 소유자 | 무효화 | 설명 |','|---|---|---|---|---|']
for name,f in FACTS.items():lines.append(f'| `{name}` | '+', '.join(f.deps)+f' | {f.owner} | {f.invalidation} | {f.description} |')
lines+=['','## 처리기 내부 Fact와 실제 입력','',
'| 처리기/소비자 | 입력·계산 | 갱신 단위 |','|---|---|---|',
'| OZ_STATE | OPEN HMA6/17·캔들·MT5 PRICE/RSI/STO/DI OUT 및 레짐 slope/경계, WONBI_BANDS, ATR14_GENERAL | 변경 TF publication; 확정봉/진행봉 의미 유지 |',
'| SWEEP_STATE | PDH/PDL, PWH/PWL, 이전4H·8H, 전 세션 H/L, 터치 OHLC | 확정봉/세션 경계; source TF 확정봉 터치 |',
'| FVG_STATE | FVG_WILDER_ATR, 3봉 gap 구조·채움·나이, 현재 high/low 터치 | 구조=확정봉, 터치=매 publication |',
'| INDICATOR | 등록부 trend/score 의존군, SMA20(OPEN), HMA50(MT5), OPEN4 std(MT5 폭에서 복원), DMI·RSI14·CCI·MACD·VWAP 등 | 닫힌 prefix 보관, 진행행 갱신 |',
'| WATCH_CONDITIONS | 봉 마감, 원비 터치, MA 교차/표현식, OUT/OUT→IN, 전일 고저 | 조건 선언의 CLOSE/LIVE |',
'| WATCH_MA | SMA/WMA/HMA=OPEN, EMA=CLOSE; 고정 native 열 있으면 읽기 | 기간/확정봉 또는 진행 CLOSE |',
'| COMPOSER/SPECIAL | Fact·상태 조합, native HMA/EMA/밴드, 공용 원비, 기존 ATR/Supertrend/FVG 요청 | 기존 판정/조합 유지 |',
'','## 중복과 처리 결정','',
'| 비교 | 계산 의미 | 이번 처리 |','|---|---|---|',
'| Python OPEN4 std ↔ EA OPEN4 모표준편차 | 같은 OPEN4 모집단 분산; 연산 순서/float 반올림 차이 가능 | 지표 std는 `(native_upper-mid)/3` 공용 함수로 통합. 점수 조건은 유지 |',
'| Python OUT 상태 ↔ 네이티브 lower_out/upper_out | 같은 엄격한 <lower/>upper; 경계값은 IN | 하루 TIMER의 관측별 상태·OUT→IN 전환 대조 후 native 읽기 |',
'| Python basis.diff ↔ native regime_slope | 같은 이전 봉 차분 | native slope 사용; 윈도우 선두를 임의 NaN으로 덮지 않음 |',
'| Watch 고정 EMA/HMA ↔ native 해당 열 | 같은 입력/기간 | 기존 native 우선 읽기 유지; EMA21 별칭은 EMA20 |',
'| price_hma_6 ↔ hma_6 | CLOSE HMA6 ↔ OPEN HMA6: 다른 입력 | 둘 다 유지 |',
'| INDICATOR e50 ↔ ema_50 | OPEN EMA50 ↔ CLOSE EMA50: 다른 입력/초기 seed | 바꾸지 않음 |',
'| INDICATOR rv/DMI ↔ MT5 RSI/STO/DI | 기본 RSI14·DMI 점수 ↔ custom 평활·퍼센타일 지표 | 바꾸지 않음 |',
'| MT5 iATR ↔ ATR14_GENERAL ↔ FVG_WILDER_ATR | iATR SMA, GENERAL EWM(TR[0] seed), FVG Wilder 최초 N봉 SMA seed | 바꾸지 않음. FVG 전용 ATR 분리 유지 |',
'| 같은 ATR 수식의 서로 다른 입력 창 | COMPOSER 창/전체 Snapshot 입력 이력이 달라 누적값 다를 수 있음 | 창/판정 구조를 임의 변경하지 않음; 공용 GENERAL 수식 유지 |',
'','## 사용처 없는 열 및 보존 결정','',
'삭제하는 미사용 열은 과거 1.79/2.79/3/4σ 밴드 8열, 전달 σ 1열과 EMA21뿐이다. HMA168·EMA50·EMA200은 SPECIAL4의 실제 사용처가 있어 미사용 열이 아니다. PRICE에만 `price_regime_basis`라는 별도 이름이 있는 이유는 PRICE 퍼센타일 판정선이 CLOSE HMA6이고, RSI/STO/DI는 기존 `*_basis` 열이 이미 레짐 기준선이기 때문이다.',
'', '여러 설정 σ를 동시에 계산·사용하는 활성 소비 경로는 발견하지 않았다. 과거 8밴드는 운반만 했으며 현재 원비 소비는 config의 WONBI_SIGMA 하나를 쓴다. σ=3은 native upper/lower를 그대로 전달하고, 다른 σ만 공용 Fact에서 환산한다. 원비 계산용 표준편차를 Wire에 새로 추가하지 않는다.',
'', '네이티브 마지막행 추출(native_snapshots)은 이벤트 입력이 아니다. 그 기존 별도 파일 레이아웃의 고정 σ=3 메타 열은 호환 보존한다. Wire에는 wonbi_sigma가 없으며 녹화/실행 σ 설정으로 사용하지 않는다. SNAPSHOT 제어 응답의 source σ=3 메타도 고정 원본 의미이며 변경 명령/불일치 경고는 없다.',
'', '원비 Fact 결과를 Board에서 OZ·Watch·COMPOSER가 공유하며 SPECIAL은 COMPOSER 입력을 읽는다. 사용한 config σ는 Part2 실행 기록의 `scenario.wonbi_sigma` 및 chunk 결과에 남는다. 녹화 캐시 식별자와 EA 입력에서 σ를 제거하여 config 변경만으로 같은 녹화를 재사용한다.']
(ROOT/'지표정리_목록.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
(ROOT/'검증결과/schema_cleanup/indicator_inventory.json').write_text(json.dumps({'old_columns':old,'new_columns':wire.PIPE_VALUE_COLUMNS,'schema_id':wire.WIRE_SCHEMA_ID,'facts':{n:{'dependencies':f.deps,'owner':f.owner,'invalidation':f.invalidation,'parameters':f.parameters} for n,f in FACTS.items()}},ensure_ascii=False,indent=2),encoding='utf-8')
print('Inventory',len(old),len(wire.PIPE_VALUE_COLUMNS),len(FACTS))
