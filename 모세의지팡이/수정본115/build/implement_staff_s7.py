"""One-shot, asserted S7 source migration; never touches earlier revisions."""
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def edit(name, changes):
 p=ROOT/name;s=p.read_text(encoding='utf-8-sig')
 for a,b in changes:
  assert a in s,(name,a[:100]);s=s.replace(a,b)
 p.write_text(s,encoding='utf-8')

edit('Part1/program/staff_schema.py',[
 ("SCHEMA_ID = 'staff-wire-v1-45'", "SCHEMA_ID = 'staff-wire-v2-48'"),
 ('BASE_COLUMNS =', '''LEGACY_PIPE_VALUE_COLUMNS = tuple(PIPE_VALUE_COLUMNS)
PIPE_VALUE_COLUMNS += ['wonbi_upper', 'wonbi_lower', 'wonbi_sigma']
COLUMN_ALIASES = {'wonbi_mid': 'open_band_4_mid'}

def mt5_values(values):
    """Append legacy MT5 bands by copying slots only; never calculate Wonbi."""
    values = np.asarray(values, dtype='<f8')
    if values.ndim != 2 or values.shape[1] not in (45, 48):
        raise ValueError('unknown STAFF value columns')
    if values.shape[1] == 48:
        return values
    out = np.empty((len(values), 48), dtype='<f8')
    out[:, :45] = values
    out[:, 45], out[:, 46], out[:, 47] = values[:, 18], values[:, 17], 3.0
    return out

BASE_COLUMNS ='''),
 ('values = snapshot.time, snapshot.volume, snapshot.values', 'values = snapshot.time, snapshot.volume, mt5_values(snapshot.values)'),
 ("df.attrs['source_epoch']", "df['wonbi_mid'] = df[COLUMN_ALIASES['wonbi_mid']]\n    df.attrs['source_epoch']"),
 ('WIRE_SCHEMAS = {WIRE_SCHEMA_ID: tuple(PIPE_VALUE_COLUMNS)}', "LEGACY_WIRE_SCHEMA_ID = zlib.crc32('\\n'.join(LEGACY_PIPE_VALUE_COLUMNS).encode()) & 0xffffffff\nWIRE_SCHEMAS = {WIRE_SCHEMA_ID: tuple(PIPE_VALUE_COLUMNS), LEGACY_WIRE_SCHEMA_ID: LEGACY_PIPE_VALUE_COLUMNS}"),
 ('bars, 45, schema_id, kind)', 'bars, len(WIRE_SCHEMAS.get(schema_id, PIPE_VALUE_COLUMNS)), schema_id, kind)'),
 ('            schema_id=WIRE_SCHEMA_ID):', '            schema_id=None):'),
 ("    sym, tf = symbol.encode('utf-8'), timeframe.encode('utf-8')", "    if schema_id is None:\n        schema_id = LEGACY_WIRE_SCHEMA_ID if values is not None and np.asarray(values).shape[-1] == 45 else WIRE_SCHEMA_ID\n    cols = len(WIRE_SCHEMAS.get(schema_id, PIPE_VALUE_COLUMNS))\n    sym, tf = symbol.encode('utf-8'), timeframe.encode('utf-8')"),
 ('x.shape != (rows, 45)', 'x.shape != (rows, cols)'),
 ('cols != 45 or seq < 0', 'cols not in (45, 48) or seq < 0'),
 ('rows * (16 + 45 * 8)', 'rows * (16 + cols * 8)'),
 ("    if kind in (WIRE_HELLO, WIRE_ACK):\n        if any", "    if cols != len(WIRE_SCHEMAS[schema]):\n        raise WireError('schema column count mismatch')\n    if kind in (WIRE_HELLO, WIRE_ACK):\n        if any"),
 ('rows*45, offset + rows*16).reshape(rows, 45)', 'rows*cols, offset + rows*16).reshape(rows, cols)'),
 ("f'const uint STAFF_WIRE_SCHEMA_ID", "f'const int STAFF_WIRE_VALUE_COLUMNS = {len(PIPE_VALUE_COLUMNS)};\\n'\n            f'const uint STAFF_WIRE_SCHEMA_ID"),
])

# Keep all non-Wonbi derived features and judgments unchanged.
for name in ('Part1/program/staff_compat.py','Part2/calculations/common.py'):
 p=ROOT/name;s=p.read_text(encoding='utf-8-sig');start=s.index('def add_wonbi_features(')
 end=s.index('\ndef ', start+5)
 s=s[:start]+s[end+1:]
 s='\n'.join(line for line in s.split('\n') if '= add_wonbi_features(' not in line)
 p.write_text(s,encoding='utf-8')
edit('Part1/program/monitor_OZ.py',[
 ('        from staff_compat import add_wonbi_features\n',''),
 ('return add_wonbi_features(out, sigma=sigma)', 'return out'),
])

p=ROOT/'Part1/program/THE STAFF OF MOSES.py';s=p.read_text(encoding='utf-8-sig')
a=s.index('PIPE_VALUE_COLUMNS = [');b=s.index('\n]',a)+2
s=s[:a]+'PIPE_VALUE_COLUMNS = wire.PIPE_VALUE_COLUMNS'+s[b:]
p.write_text(s,encoding='utf-8')
edit('Part1/program/THE STAFF OF MOSES.py',[
 ('    received_at: float\n','    received_at: float\n    legacy_wonbi: bool = False\n'),
 ("'received_at': item.received_at,", "'received_at': item.received_at, 'legacy_wonbi': item.legacy_wonbi,"),
 ("values = np.asarray(item['values'], dtype='<f8')", "values = wire.mt5_values(item['values'])"),
 ("item['received_at'])", "item['received_at'], item.get('legacy_wonbi', len(item['values'][0]) == 45))"),
 ('if cols != len(PIPE_VALUE_COLUMNS):', 'if cols != len(wire.LEGACY_PIPE_VALUE_COLUMNS):'),
 ('*, advance_epoch=True):\n        bars, cols = values.shape', '''*, advance_epoch=True, legacy_wonbi=None):
        if legacy_wonbi is None:
            legacy_wonbi = values.shape[1] == 45
        values = wire.mt5_values(values)
        bars, cols = values.shape'''),
 ('MappingProxyType(valid), received_at)', 'MappingProxyType(valid), received_at, legacy_wonbi)'),
 ('wire.pack_hello(packet.build_hash, ack=True)', 'wire.pack_hello(packet.build_hash, ack=True, schema_id=packet.schema_id)'),
 ('values[-1]=frame.values[0]', 'values[-1]=wire.mt5_values(frame.values)[0]'),
 ('values,advance_epoch=False)', 'values,advance_epoch=False,legacy_wonbi=old.legacy_wonbi)'),
 ("                if age > self.cache.stale_seconds:", "                self._check_wonbi_sigma(symbol, tf, snapshot, sigma, reject_legacy=True)\n                if age > self.cache.stale_seconds:"),
 ('원비 sigma 변경:', '원비 기대 sigma 변경 (EA 계산값 유지):'),
 ("            return {'ok':True, 'feeds':self.cache.health(symbol, health_tfs, closed)}", '''            feeds = self.cache.health(symbol, health_tfs, closed)
            for tf, (snapshot, age) in self.cache.snapshots_with_age(symbol, health_tfs).items():
                if snapshot is not None:
                    warning = self._check_wonbi_sigma(symbol, tf, snapshot, self.wonbi_state.get_sigma())
                    if warning:
                        feeds[tf]['warnings'] = [warning]
            return {'ok':True, 'feeds':feeds}'''),
 ('    def snapshot_reply(self, req):', '''    def _check_wonbi_sigma(self, symbol, tf, snapshot, expected, *, reject_legacy=False):
        applied = float(snapshot.values[-1, 47])
        if snapshot.legacy_wonbi and expected != 3.0 and reject_legacy:
            raise ValueError('LEGACY_WONBI_SIGMA: 45열 캡처는 WONBI_SIGMA=3.0만 재생할 수 있습니다.')
        if applied == expected:
            return None
        warning = {'code':'WONBI_SIGMA_MISMATCH', 'expected_sigma':expected,
                   'applied_sigma':applied if np.isfinite(applied) else None,
                   'message':'EA 원비 계산값을 그대로 사용합니다.'}
        key = (symbol, tf, expected, applied)
        seen = getattr(self, '_wonbi_warning_keys', set())
        if key not in seen:
            logging.warning('[WONBI_SIGMA_MISMATCH] %s %s expected=%g MT5=%g; EA bands retained', symbol, tf, expected, applied)
            # At most one active warning per feed; bounded across config changes.
            self._wonbi_warning_keys = {k for k in seen if k[:2] != key[:2]} | {key}
        return warning

    def snapshot_reply(self, req):'''),
])

policy='''

## S7 사용자 지시 및 검증 정책 (2026-09-26)
- 수정본13은 읽기 전용 기준이며 독립 복사본 수정본14에서 S7만 구현한다. S8은 시작하지 않는다.
- 원비 기준은 EA CalcOpenBands4의 OPEN 길이 4, 2-pass 모표준편차 계산이다. InpWonbiSigma는 시작 시 고정 입력이다. 설계서의 실행 중 sigma 파일 갱신안은 적용하지 않는다.
- SET_WONBI_SIGMA는 기대 sigma만 전달한다. 실제 밴드는 MT5 값을 그대로 사용한다. Python 원비 계산은 삭제하며 합성 입력 생성기만 EA식 포트를 허용한다.
- Part2 일반 회귀 묶음(validation_suite 기존 테스트, cadence_input_validation, conditional_validation, watch_ma_validation)은 게이트와 전체 실행 대상에서 제외한다. 파일은 보존하고 실행하지 않는다.
- part1_host 240초 3-way, 합성/실제 MT5/BTC 외부 동작 비교, 대표 판정 입력 샘플, S7 신규 테스트는 필수다. Part1 audit와 루트 tests는 계속 실행한다.
- 내부 DataFrame 비트/객체/private/set 순서는 진단만 한다. 외부 동작과 전략 의미 보존이 게이트다. 이전 Python 원비와 새 원비 수치 회귀 비교는 금지한다.
- 원비 변경 알림은 원인을 개별 분석하지 않고 건수와 '원비 원본 MT5 전환에 따른 승인된 기준 변경'으로 기록한다. S7 결과를 새 기준선으로 동결하고 S0 골든은 참고용으로 불변 보존한다.
- 개발 중 관련 파일/신규 테스트만 실행한다. 구현 완료 후 필수 전체 게이트는 최종 1회 실행하며 원인 분석에 필요한 실패 항목만 재실행한다. 성능은 참고 1회, 판정 없음.
'''
for name in ('AGENTS.md.txt','검증정책_S6이후.md'):
 p=ROOT/name;p.write_text(p.read_text(encoding='utf-8-sig')+policy,encoding='utf-8')
print('S7 schema, receiver, client migration and policy recorded')
