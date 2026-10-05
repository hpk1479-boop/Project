from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
p=ROOT/'Wire_v2_명세.md';s=p.read_text('utf-8')
s=s.replace('# STAFF Wire v2 — S6','# STAFF Wire v2 — S7 (기존 v2 구조 유지)')
s=s.replace('Wire v1의 45열·`<IIqIIII>` 패킹은 유지한다. 클라이언트 SNAPSHOT/ZMQ API와 `staff-wire-v1-45` 문자열 식별자도 그대로다. 아래 정수 `schema_id`는 EA↔STAFF 전송 스키마용이다. 원비 계산/열 추가는 이 단계에 포함하지 않는다.',
'''Wire v1의 45열·`<IIqIIII>` 패킹은 유지한다. S7 EA v2는 기존 45열 뒤에 원비 3열을 추가한다. ZMQ 요청 형식과 제어 요청은 동일하며 Snapshot 응답의 배열 스키마 식별자는 `staff-wire-v2-48`이다. v1/구 v2 45열은 수신 후 MT5 3σ 슬롯을 복사하여 48열로 정규화한다. Python 원비 계산은 하지 않는다.

| 인덱스 | 이름 | 의미 |
|---|---|---|
| 0–44 | S6와 동일 | 이름·순서 불변 |
| 45 | wonbi_upper | EA CalcOpenBands4 상단 |
| 46 | wonbi_lower | EA CalcOpenBands4 하단 |
| 47 | wonbi_sigma | EA가 사용한 InpWonbiSigma |
| 별칭 | wonbi_mid | 기존 12열 open_band_4_mid |

OPEN 길이 4의 2-pass 평균/모표준편차를 EA에서 계산한다. InpWonbiSigma 기본값은 3.0이며 실행 중 변경하지 않는다. `SET_WONBI_SIGMA`는 기대 σ만 바꾼다. 기대값과 MT5 값이 다르면 로그와 SOURCE_HEALTH warnings로 알리고 MT5 밴드는 그대로 사용한다. 구 45열은 upper=18열/open_band_300_upper, lower=17열/open_band_300_lower, sigma=3.0으로 복사한다. 기대 σ가 3.0이 아니면 구 입력의 데이터 요청은 LEGACY_WONBI_SIGMA 오류로 거부한다.''')
s=s.replace('| cols | u32 | 45 |','| cols | u32 | 등록된 스키마의 열 수: 기존 45 / S7 48 |')
s=s.replace('| schema_id | u32 | `0x4D2FA0E9` |','| schema_id | u32 | 기존 `0x4D2FA0E9`, S7 `0x36C28F68` |')
s=s.replace('values[f64]*bars*45','values[f64]*bars*cols')
s=s.replace('`build/generate_staff_wire_schema.py`','`build/generate_staff_s7_schema.py`')
s=s.replace('version 2, cols 45, max_bars 650','version 2, cols 45(구 캡처) 또는 48(S7), max_bars 650')
s+='\nS7 생성기와 검증 도구는 `검증결과/staff_s7`에만 쓴다. 과거 S0–S6 증거를 쓰는 이전 build 도구를 S7 재생성에 사용하지 않는다.\n'
p.write_text(s,encoding='utf-8')
s=(ROOT/'build/inventory_part3_legacy_s6.py').read_text('utf-8').replace('staff_s6','staff_s7').replace('s5_frozen_manifest','s6_frozen_manifest').replace('— S6','— S7').replace('수정본12','수정본13')
(ROOT/'build/inventory_part3_legacy_s7.py').write_text(s,encoding='utf-8')
s=(ROOT/'build/staff_s6_inventory.py').read_text('utf-8').replace('staff_s6','staff_s7')
(ROOT/'build/staff_s7_inventory.py').write_text(s,encoding='utf-8')
