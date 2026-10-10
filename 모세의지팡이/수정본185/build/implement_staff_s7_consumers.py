from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def edit(name, changes):
 p=ROOT/name;s=p.read_text(encoding='utf-8-sig')
 for a,b in changes:
  assert a in s,(name,a[:90]);s=s.replace(a,b)
 p.write_text(s,encoding='utf-8')
for name in ('Part1/program/staff_compat.py','Part1/program/THE STAFF OF MOSES.py'):
 edit(name,[("\"WONBI\": [],", "\"WONBI\": ['open_band_4_mid', 'wonbi_upper', 'wonbi_lower', 'wonbi_sigma'],"),
            ('WONBI는 MT5 buffer가 아니라 STAFF Python 파생값이므로 raw 요구 컬럼이 없습니다.', 'WONBI는 EA가 계산한 원본 열만 사용합니다.')])
edit('Part1/program/staff_compat.py',[
 ('The temporary Python wonbi body is byte-for-byte S2 arithmetic, guarded by AST\ntests. MT5 wonbi mapping is deliberately deferred to S7.', 'Wonbi columns are authoritative MT5 inputs (S7); this adapter never computes them.'),
 ('    # - 원비: length=4, runtime sigma', '    # - 원비는 이미 수신한 MT5 열을 그대로 유지합니다.'),
])
edit('Part2/live_replay/trace_io.py',[
 ('    return b\'\'.join([PIPE_HEADER.pack(PIPE_MAGIC,PIPE_VERSION,snapshot_id,len(symbol_b),len(tf_b),len(frame),len(PIPE_VALUE_COLUMNS)),\n        symbol_b,tf_b,np.asarray(nanos//1_000_000_000,dtype=\'<i8\').tobytes(),\n        np.asarray(volumes,dtype=\'<i8\').tobytes(),values.tobytes()])',
  "    from part1_host.capture import wire_schema\n    return wire_schema().pack_v2(symbol,timeframe,np.asarray(nanos//1_000_000_000,dtype='<i8'),\n        np.asarray(volumes,dtype='<i8'),values,seq=snapshot_id)"),
])
edit('Part2/live_replay/synthetic.py',[
 ('    return frame\n', '''    # Synthetic-only EA two-pass port. Production replay must receive MT5 bands.
    from part1_host.synthetic import ea_open4_synthetic
    mid,sd=ea_open4_synthetic(frame['open'].to_numpy())
    frame['open_band_4_mid']=mid
    for sigma in (1.79,2.79,3.,4.):
        suffix=str(int(round(sigma*100)))
        frame['open_band_'+suffix+'_lower']=mid-sigma*sd
        frame['open_band_'+suffix+'_upper']=mid+sigma*sd
    frame['wonbi_upper']=mid+3.*sd;frame['wonbi_lower']=mid-3.*sd;frame['wonbi_sigma']=3.
    return frame
'''),
])
# Separate generic BB's independent calculation from authoritative WONBI data.
# Pure OHLCV archives cannot supply MT5 Wonbi: fail explicitly instead of inventing it.
edit('Part2/live_replay/event_catalog.py',[
 ('    config = config or {}\n    if any(et not in B_TYPES', "    config = config or {}\n    if 'WONBI' in event_types:\n        raise ReplayError('MT5_WONBI_REQUIRED: replay STAFF MSP3/native observations, not OHLCV-only data')\n    if any(et not in B_TYPES"),
 ("sigma=float(config.get('WONBI_SIGMA',3.)) if key[2]=='WONBI' else 3.", "sigma=3. # Generic BB only; Wonbi requires MT5 observations above."),
 ("bb=(float(row.get('wonbi_mid',float('nan'))),float(row.get('wonbi_std',float('nan'))))", "bb=bb_open4(frame['open'].tail(4).tolist()) if any(k[2]=='BB' for k in group) else None"),
 ('                                if not all(math.isfinite(v) for v in bb):bb=None', '                                if bb is not None and not all(math.isfinite(v) for v in bb):bb=None'),
 ('                                elif bb is not None:\n                                    mean,std=bb;', "                                elif k[2]=='WONBI' or bb is not None:\n                                    mean,std=bb if bb is not None else (0.,0.);"),
 ("sigma=float(self.config.get('WONBI_SIGMA','3')) if k[2]=='WONBI' else 3.", "sigma=float(row['wonbi_sigma']) if k[2]=='WONBI' else 3."),
 ("                frame['wonbi_mid']=mids;frame['wonbi_std']=stds\n                sigma=float(self.archive.header.get('config',{}).get('WONBI_SIGMA',3))\n                frame['wonbi_lower']=frame['wonbi_mid']-sigma*frame['wonbi_std'];frame['wonbi_upper']=frame['wonbi_mid']+sigma*frame['wonbi_std']\n                frame['wonbi_sigma']=sigma", "                frame['bb_mid']=mids;frame['bb_std']=stds"),
 ("band=(float(row.get('wonbi_mid',float('nan'))),float(row.get('wonbi_std',float('nan'))))", "band=(float(row.get('bb_mid',float('nan'))),float(row.get('bb_std',float('nan'))))"),
 ('        self.bb_tfs={k[1] for k in self.keys if k[2] in B_TYPES}|self.environment_tfs', "        if any(k[2]=='WONBI' for k in self.keys):\n            raise ReplayError('MT5_WONBI_REQUIRED: use native STAFF observations for Wonbi')\n        self.bb_tfs={k[1] for k in self.keys if k[2] in B_TYPES}|self.environment_tfs"),
])

# New evidence helpers write only S7; all prior samples remain untouched.
s=(ROOT/'build/staff_s6_evidence.py').read_text(encoding='utf-8').replace("SOURCE=ROOT.parent/'수정본12'", "SOURCE=ROOT.parent/'수정본13'").replace("OUT=ROOT/'검증결과/staff_s6'", "OUT=ROOT/'검증결과/staff_s7'").replace('s5_frozen_manifest','s6_frozen_manifest').replace("'검증결과/staff_s5/','Part3/'", "'검증결과/staff_s5/','검증결과/staff_s6/','Part3/'").replace('수정본12 after user approval','수정본13 S6 complete with user BTC config')
(ROOT/'build/staff_s7_evidence.py').write_text(s,encoding='utf-8')
print('S7 consumers and synthetic adapters migrated')
