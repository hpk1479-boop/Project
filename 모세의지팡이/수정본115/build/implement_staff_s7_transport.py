from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def edit(name, changes):
 p=ROOT/name;s=p.read_text(encoding='utf-8-sig')
 for a,b in changes:
  assert a in s,(name,a[:100]);s=s.replace(a,b)
 p.write_text(s,encoding='utf-8')

edit('Part1/program/MT5/THE_STAFF_OF_MOSES.mq5',[
 ('#property version   "1.60"','#property version   "1.70"'),
 ('input int InpWireVersion = 2;', 'input int InpWireVersion = 2;\ninput double InpWonbiSigma = 3.0; // Fixed for this EA execution; no runtime mutation.'),
 ('const int    STAFF_VALUE_COLUMNS     = 45;', 'const int    STAFF_VALUE_COLUMNS     = STAFF_WIRE_VALUE_COLUMNS;\nconst int    STAFF_LEGACY_COLUMNS    = 45;'),
 ('// Telegram 실시간 표준편차 변경은 Python THE STAFF OF MOSES가 OPEN 원자료로 재계산하며,\n// 아래 Staff의 1.79/2.79/3.00/4.00 전송 슬롯은 Named Pipe v1 호환을 위해 유지합니다.', '// EA computes authoritative Wonbi with fixed InpWonbiSigma.\n// Existing 1.79/2.79/3.00/4.00 slots retain their original v1 meanings.'),
 ('double &upper400[], double &lower400[])', 'double &upper400[], double &lower400[],\n                    double &wonbi_upper[], double &wonbi_lower[])'),
 ('   const int length=WONBI_LENGTH;', '   ArrayResize(wonbi_upper,count); ArrayInitialize(wonbi_upper,EMPTY_VALUE);\n   ArrayResize(wonbi_lower,count); ArrayInitialize(wonbi_lower,EMPTY_VALUE);\n   const int length=WONBI_LENGTH;'),
 ('      upper400[i]=m+4.00*sd; lower400[i]=m-4.00*sd;', '      upper400[i]=m+4.00*sd; lower400[i]=m-4.00*sd;\n      wonbi_upper[i]=m+InpWonbiSigma*sd; wonbi_lower[i]=m-InpWonbiSigma*sd;'),
 ('   double open_band_4_mid[];', '   double open_band_4_mid[], wonbi_upper[], wonbi_lower[];'),
 ('open_band_400_upper, open_band_400_lower);', 'open_band_400_upper, open_band_400_lower, wonbi_upper, wonbi_lower);'),
 ('      PutIPCValue(values,row,44,(ok_di ? d_regime_dn[i] : EMPTY_VALUE));', '      PutIPCValue(values,row,44,(ok_di ? d_regime_dn[i] : EMPTY_VALUE));\n      PutIPCValue(values,row,45,wonbi_upper[i]);\n      PutIPCValue(values,row,46,wonbi_lower[i]);\n      PutIPCValue(values,row,47,InpWonbiSigma);'),
 ('(rows-1)*360', '(rows-1)*STAFF_VALUE_COLUMNS*8'),
 ('ArrayResize(rx,n*45)', 'ArrayResize(rx,n*STAFF_VALUE_COLUMNS)'),
 ('(rows-1)*45,45)', '(rows-1)*STAFF_VALUE_COLUMNS,STAFF_VALUE_COLUMNS)'),
 ('FileWriteInteger(g_pipe, STAFF_VALUE_COLUMNS, INT_VALUE);', 'FileWriteInteger(g_pipe, STAFF_LEGACY_COLUMNS, INT_VALUE);'),
 ('uint w_x   = FileWriteArray(g_pipe, values,    0, bar_count*STAFF_VALUE_COLUMNS);', 'double legacy[]; StaffLegacyValues(values,bar_count,legacy);\n   uint w_x   = FileWriteArray(g_pipe, legacy, 0, bar_count*STAFF_LEGACY_COLUMNS);'),
 ('w_x!=(uint)(bar_count*STAFF_VALUE_COLUMNS)', 'w_x!=(uint)(bar_count*STAFF_LEGACY_COLUMNS)'),
 ('FileWriteInteger(h,STAFF_VALUE_COLUMNS,INT_VALUE);', 'FileWriteInteger(h,InpWireVersion==2 ? STAFF_VALUE_COLUMNS : STAFF_LEGACY_COLUMNS,INT_VALUE);'),
 ('uint wx=FileWriteArray(h,values,0,rows*STAFF_VALUE_COLUMNS);', 'double legacy[]; StaffLegacyValues(values,rows,legacy);\n   uint wx=FileWriteArray(h,legacy,0,rows*STAFF_LEGACY_COLUMNS);'),
 ('wx!=(uint)(rows*STAFF_VALUE_COLUMNS)', 'wx!=(uint)(rows*STAFF_LEGACY_COLUMNS)'),
 ('void CalcOpenBands4(', '''void StaffLegacyValues(const double &values[],const int rows,double &legacy[])
{
   ArrayResize(legacy,rows*STAFF_LEGACY_COLUMNS);
   for(int row=0;row<rows;++row)
      ArrayCopy(legacy,values,row*STAFF_LEGACY_COLUMNS,row*STAFF_VALUE_COLUMNS,STAFF_LEGACY_COLUMNS);
}

void CalcOpenBands4('''),
 ('int OnInit()\n{', 'int OnInit()\n{\n   if(!MathIsValidNumber(InpWonbiSigma) || InpWonbiSigma<=0) return INIT_PARAMETERS_INCORRECT;'),
])
# MSP3 forming rows reuse open-based bands from the latest FULL, including Wonbi.
edit('Part1/program/MT5/THE_STAFF_OF_MOSES.mq5',[
 ('double g_pipe_cap_open_cols[];', 'double g_pipe_cap_open_cols[];\ndouble g_pipe_cap_wonbi[];'),
 ('   ArrayResize(g_pipe_cap_open_cols,total*STAFF_PIPE_OPEN_COL_COUNT);', '   ArrayResize(g_pipe_cap_open_cols,total*STAFF_PIPE_OPEN_COL_COUNT);\n   ArrayResize(g_pipe_cap_wonbi,total*3);'),
 ('   int base=(rows-1)*STAFF_VALUE_COLUMNS;', '   int base=(rows-1)*STAFF_VALUE_COLUMNS;\n   for(int k=0;k<3;++k) g_pipe_cap_wonbi[index*3+k]=values[base+45+k];'),
 ('   double x=EMPTY_VALUE;\n   bool ok=true;', '   for(int k=0;k<3;++k) v[45+k]=g_pipe_cap_wonbi[index*3+k];\n   double x=EMPTY_VALUE;\n   bool ok=true;'),
])
edit('Part1/program/MT5/STAFF_Wire_V2.mqh',[
 ('32,33,34,35,43,44,-1};','32,33,34,35,43,44,-1,12,45,46,47,-1};'),
 ('(rows-1)*45','(rows-1)*STAFF_WIRE_VALUE_COLUMNS'),
 ('StaffPut32(out,p,45)', 'StaffPut32(out,p,STAFF_WIRE_VALUE_COLUMNS)'),
 ('rows*376','rows*(16+STAFF_WIRE_VALUE_COLUMNS*8)'),
 ('rows*45','rows*STAFF_WIRE_VALUE_COLUMNS'),
])

edit('Part2/part1_host/capture.py',[
 ('if cols!=45 or not 3<=bars<=650:', 'if cols not in (45,48) or not 3<=bars<=650:'),
 ('if (magic,version)==(CAPTURE_MAGIC,1):return 1','if (magic,version,cols)==(CAPTURE_MAGIC,1,45):return 1'),
 ("*, wire_version=1):", "*, wire_version=1, value_columns=None):"),
 ('        self.wire_version = int(wire_version)', '        self.wire_version = int(wire_version)\n        self.value_columns = value_columns or (48 if self.wire_version==2 else 45)\n        if self.value_columns not in (45,48) or (self.wire_version==1 and self.value_columns!=45):raise CaptureError("CAPTURE_COLUMNS")'),
 ('self.wire_version, VALUE_COLUMNS, MAX_BARS)', 'self.wire_version, self.value_columns, MAX_BARS)'),
 ("        f = self.files[index]\n", "        if values.shape != (len(times), self.value_columns):raise CaptureError('CAPTURE_COLUMNS_MISMATCH')\n        f = self.files[index]\n"),
 ('seq=self.counts[index]+1,kind=kind)', 'seq=self.counts[index]+1,kind=kind,schema_id=w.LEGACY_WIRE_SCHEMA_ID if self.value_columns==45 else w.WIRE_SCHEMA_ID)'),
])
edit('Part2/part1_host/wire_v2.py',[
 ('    row=values[-1]', '    row=values[-1]\n    groups=GROUPS+(((12,45,46,47),) if len(row)==48 else ((12,17,18),))'),
 ('for group in GROUPS)', 'for group in groups)'),
 ('seq=seq,kind=kind)', 'seq=seq,kind=kind,schema_id=w.LEGACY_WIRE_SCHEMA_ID if data[2].shape[1]==45 else w.WIRE_SCHEMA_ID)'),
])
edit('Part2/part1_host/synthetic.py',[
 ('def indicator_frame(bars: pd.DataFrame)', 'def indicator_frame(bars: pd.DataFrame, wonbi_sigma=3.0)'),
 ('(len(bars), VALUE_COLUMNS)', '(len(bars), 48)'),
 ('    mid, sd = op.rolling(4).mean(), op.rolling(4).std(ddof=0)', '    mid, sd = ea_open4_synthetic(o)'),
 ('    phma = pd.Series(hma(c, 6))', '    out[:,45], out[:,46], out[:,47] = mid+wonbi_sigma*sd, mid-wonbi_sigma*sd, wonbi_sigma\n    phma = pd.Series(hma(c, 6))'),
 ('    pattern: tuple = ()', '    pattern: tuple = ()\n    wonbi_sigma: float = 3.0'),
 ('final = indicator_frame(bars)', 'final = indicator_frame(bars, self.wonbi_sigma)'),
 ("        row[OPEN_COLS] = f['final'][j][OPEN_COLS]", "        row[OPEN_COLS] = f['final'][j][OPEN_COLS]\n        row[45:48] = f['final'][j][45:48]"),
 ('                writer.write(index,second,*data.payload(tf,second))', '                t,v,x=data.payload(tf,second)\n                writer.write(index,second,t,v,x if wire_version==2 else x[:,:45])'),
 ('def indicator_frame(', '''def ea_open4_synthetic(opens):
    """Synthetic-only EA CalcOpenBands4 port; two passes in the original order."""
    mid=np.full(len(opens),np.nan);std=np.full(len(opens),np.nan)
    for i in range(3,len(opens)):
        total=0.0
        for k in range(i-3,i+1):total+=float(opens[k])
        mean=total/4.0;variance=0.0
        for k in range(i-3,i+1):
            delta=float(opens[k])-mean;variance+=delta*delta
        mid[i]=mean;std[i]=math.sqrt(variance/4.0)
    return mid,std


def indicator_frame('''),
])

for old,new in [('generate_staff_wire_schema.py','generate_staff_s7_schema.py'),
                ('compile_staff_s6_mt5.py','compile_staff_s7_mt5.py')]:
 s=(ROOT/'build'/old).read_text(encoding='utf-8').replace('staff_s6','staff_s7').replace("source=ROOT/'검증결과/staff_s0/mt5_terminal'", "source=ROOT/'검증결과/staff_s6/mt5_terminal'")
 (ROOT/'build'/new).write_text(s,encoding='utf-8')
print('S7 EA and capture transport migrated')
