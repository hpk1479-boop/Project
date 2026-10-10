"""One-time, asserted edits for the revision23 schema migration (no old-tree writes)."""
from pathlib import Path
import importlib.util,re,hashlib,json
ROOT=Path(__file__).resolve().parents[1];P=ROOT/'Part1/program'
OLD=ROOT/'검증결과/schema_cleanup/hosts/revision22/Part1/program'
spec=importlib.util.spec_from_file_location('old_schema',OLD/'staff_schema.py')
old=importlib.util.module_from_spec(spec);spec.loader.exec_module(old)
removed={'ema_21','wonbi_sigma'}|{f'open_band_{n}_{side}' for n in (179,279,300,400) for side in ('lower','upper')}
added=[f'{prefix}_{name}' for prefix in ('price','RSI','STO','DI') for name in ('lower_out','upper_out','regime_slope')]
columns=[c for c in old.PIPE_VALUE_COLUMNS if c not in removed]+added
def const(name):return 'STAFF_COL_'+name.upper()
def read(path):return path.read_bytes().decode('utf-8-sig').replace('\r\n','\n')
def write(path,text):
    original=path.read_bytes();newline='\r\n' if original.count(b'\r\n')>original.count(b'\n')/2 else '\n'
    path.write_bytes(text.replace('\n',newline).encode('utf-8'))
def replace(text,before,after):
    assert before in text,before[:100]
    return text.replace(before,after)

def schema():
    path=P/'staff_schema.py';s=read(path)
    start=s.index('SCHEMA_ID =');end=s.index('COLUMN_ALIASES =')
    s=s[:start]+"SCHEMA_ID = 'staff-wire-v2-50'\nPIPE_VALUE_COLUMNS = "+repr(columns)+"\n\n"+s[end:]
    start=s.index('def mt5_values(');end=s.index('BASE_COLUMNS =',start)
    s=s[:start]+'''def mt5_values(values):
    """This release accepts only the finalized schema, with no legacy conversion."""
    values = np.asarray(values, dtype='<f8')
    if values.ndim != 2 or values.shape[1] != len(PIPE_VALUE_COLUMNS):
        raise ValueError('STAFF schema mismatch: replay old captures with their original revision')
    return values

'''+s[end:]
    s=re.sub(r'LEGACY_WIRE_SCHEMA_ID = .*\nWIRE_SCHEMAS = .*\n',
        'WIRE_SCHEMAS = {WIRE_SCHEMA_ID: tuple(PIPE_VALUE_COLUMNS)}\n',s)
    s=replace(s,'schema_id = LEGACY_WIRE_SCHEMA_ID if values is not None and np.asarray(values).shape[-1] == 45 else WIRE_SCHEMA_ID','schema_id = WIRE_SCHEMA_ID')
    s=replace(s,'cols not in (45, 48)','not 1 <= cols <= 256')
    s=replace(s,"+ ''.join(f'// {i}: {name}\\n' for i,name in enumerate(PIPE_VALUE_COLUMNS))",
        "+ ''.join(f'const int STAFF_COL_{name.upper()} = {i}; // {name}\\n' for i,name in enumerate(PIPE_VALUE_COLUMNS))")
    s=replace(s,'"""Snapshot API v1 schema. This does not change the EA wire protocol."""',
              '"""Finalized 50-column Wire v2 registry and Snapshot API codec."""')
    write(path,s)

def ea():
    path=P/'MT5/THE_STAFF_OF_MOSES.mq5';s=read(path)
    s=re.sub(r'^input double InpWonbiSigma.*\n','',s,flags=re.M)
    s=s.replace('fixed InpWonbiSigma','fixed 3.0 sigma')
    s=re.sub(r'^.*f\.ema21.*\n','',s,flags=re.M) if False else s
    s=s.replace('   int             ema21;\n','')
    s=re.sub(r'^.*f\.ema21\s*= iMA.*\n','',s,flags=re.M)
    s=s.replace(' || f.ema21==INVALID_HANDLE','').replace('   ReleaseHandle(f.ema21);\n','')
    s=s.replace('f.ema20,f.ema21,f.ema50','f.ema20,f.ema50').replace('i<8;++i) if(BarsCalculated','i<7;++i) if(BarsCalculated').replace('i<8;++i) f.cached_calculated','i<7;++i) f.cached_calculated')
    s=s.replace('f.ema20=f.ema21=f.ema50','f.ema20=f.ema50')
    # One calculation for the fixed MT5 source bands; the consumer config never enters the EA.
    for suffix in ('179','279','300','400'):
        s=re.sub(r'^\s*double &upper'+suffix+r'\[\], double &lower'+suffix+r'\[\],\n','',s,flags=re.M)
        s=re.sub(r'^\s*ArrayResize\((?:upper|lower)'+suffix+r'.*\n','',s,flags=re.M)
        s=re.sub(r'^\s*upper'+suffix+r'\[i\]=.*\n','',s,flags=re.M)
        s=re.sub(r'^\s*double open_band_'+suffix+r'_upper\[\], open_band_'+suffix+r'_lower\[\];\n','',s,flags=re.M)
    s=re.sub(r'CalcOpenBands4\(open_src, count, open_band_4_mid,.*?wonbi_upper, wonbi_lower\);',
        'CalcOpenBands4(open_src, count, open_band_4_mid, wonbi_upper, wonbi_lower);',s,flags=re.S)
    s=s.replace('ema20[], ema21[], ema50[]','ema20[], ema50[]')
    s=re.sub(r'^\s*&& CopyOneBuffer\(f\.ema21.*\n','',s,flags=re.M)
    # Replace every literal wire position with a generated schema constant.
    def put(match):
        name=old.PIPE_VALUE_COLUMNS[int(match[1])]
        return '' if name in removed else match[0].replace(match[1],const(name),1)
    s=re.sub(r'^.*PutIPCValue\(values,row,\s*(\d+),.*\n',put,s,flags=re.M)
    def row(match):
        name=old.PIPE_VALUE_COLUMNS[int(match[1])]
        return '' if name in removed else match[0].replace('v['+match[1]+']','v['+const(name)+']')
    s=re.sub(r'^.*PipeCaptureLastValue.*v\[(\d+)\].*\n',row,s,flags=re.M)
    # OPEN columns now contain HMA6/17/50/168 and open_band_4_mid only.
    s=re.sub(r'(STAFF_PIPE_OPEN_COL_FIRST\s*=)\s*\d+',r'\1 '+const('hma_6'),s)
    s=re.sub(r'(STAFF_PIPE_OPEN_COL_COUNT\s*=)\s*\d+',r'\1 5',s)
    s=s.replace('total*3','total*2').replace('k<3;++k) g_pipe_cap_wonbi[index*3+k]=values[base+45+k]',
        'k<2;++k) g_pipe_cap_wonbi[index*2+k]=values[base+STAFF_COL_WONBI_UPPER+k]')
    s=s.replace('k<3;++k) v[45+k]=g_pipe_cap_wonbi[index*3+k]',
        'k<2;++k) v[STAFF_COL_WONBI_UPPER+k]=g_pipe_cap_wonbi[index*2+k]')
    # Native extraction retains its existing layout (separate from Wire); its sigma metadata is fixed3.
    s=s.replace('InpWonbiSigma','WONBI_DEFAULT_SIGMA')
    s=s.replace('CalcOpenBands4(opens,got,band_mid,u179,l179,u279,l279,u300,l300,u400,l400,wu,wl)',
        'CalcOpenBands4(opens,got,band_mid,wu,wl)')
    s=re.sub(r'^\s*if\(!MathIsValidNumber\(WONBI_DEFAULT_SIGMA\).*\n','',s,flags=re.M)
    s=s.replace('int OnInit()\n{','int OnInit()\n{\n   if(InpWireVersion!=2) return INIT_PARAMETERS_INCORRECT; // finalized schema requires Wire v2')
    # Read native OUT buffers independently: EMPTY is the normal IN value, never readiness failure.
    declarations=[];copies=[];puts=[];reads=[]
    for prefix,handle,short in [('price','price','p'),('RSI','rsi','r'),('STO','sto','s'),('DI','di','d')]:
        for tail,buf in [('lower_out','LOWER_OUT'),('upper_out','UPPER_OUT'),('regime_slope','REGIME_SLOPE')]:
            name=prefix+'_'+tail;var=short+'_'+tail;buffer='STAFF_'+prefix.upper()+'_'+buf+'_BUF'
            declarations.append(var+'[]')
            copies.append(f'   CopyOneBuffer(f.{handle},{buffer},count,{var});')
            puts.append(f'      PutIPCValue(values,row,{const(name)},{var}[i]);')
            reads.append(f'   ok = PipeCaptureLastValue(f.{handle},{buffer},x) && ok; v[{const(name)}]=IPCValue(x);')
    anchor='   bool ok_ema = CopyOneBuffer'
    s=replace(s,anchor,'   double '+', '.join(declarations)+';\n'+'\n'.join(copies)+'\n\n'+anchor)
    anchor='      PutIPCValue(values,row,STAFF_COL_WONBI_LOWER,wonbi_lower[i]);'
    # Whitespace in the old positions is retained by the position replacement.
    pattern=r'(      PutIPCValue\(values,row,\s*STAFF_COL_WONBI_LOWER,wonbi_lower\[i\]\);)'
    s,n=re.subn(pattern,lambda m:m[1]+'\n'+'\n'.join(puts),s);assert n==1,n
    pattern=r'(   ok = PipeCaptureLastValue\(f.di,STAFF_DI_REGIME_DN_BUF,x\) && ok; v\[STAFF_COL_DI_REGIME_LOWER\]=IPCValue\(x\);)'
    s,n=re.subn(pattern,lambda m:m[1]+'\n'+'\n'.join(reads),s);assert n==2,n
    write(path,s)
    path=P/'MT5/STAFF_Wire_V2.mqh';s=read(path)
    groups=[['ema_20','ema_50','ema_200'],['price_hma_6','price_band_lower','price_band_upper','hma_6','hma_17','price_regime_basis','price_regime_upper','price_regime_lower'],
        ['hma_6','hma_17','hma_50','hma_168']]+[[f'{p}_{t}' for t in ('val','db','ub','basis','regime_upper','regime_lower')] for p in ('RSI','STO','DI')]+[['open_band_4_mid','wonbi_upper','wonbi_lower']]
    s,n=re.subn(r'int columns\[\]=\{.*?\};','int columns[]={'+','.join(','.join(map(const,g))+',-1' for g in groups)+'};',s,flags=re.S);assert n==1
    write(path,s)

if __name__=='__main__':
    assert len(columns)==50
    assert (ROOT/'진단_판다스제거_신호차이.md').exists()
    schema();ea()
    print('new columns',len(columns))
