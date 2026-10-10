"""Finalize current-schema synthetic/capture adapters and explicit MQL seeds."""
from pathlib import Path
import re
from apply_schema_clients import normalized,rep
ROOT=Path(__file__).resolve().parents[1];P=ROOT/'Part1/program'
def synthetic(s):
    s=s.replace('from .capture import VALUE_COLUMNS, MAX_BARS','from .capture import MAX_BARS\nfrom staff_schema import PIPE_VALUE_COLUMNS\nC={name:i for i,name in enumerate(PIPE_VALUE_COLUMNS)}')
    s=s.replace('OPEN_COLS = slice(8, 21)',"OPEN_COLS = [C[name] for name in ('hma_6','hma_17','hma_50','hma_168','open_band_4_mid','wonbi_upper','wonbi_lower')]")
    start=s.index('def indicator_frame(');end=s.index('\n\n@dataclass',start)
    s=s[:start]+'''def indicator_frame(bars: pd.DataFrame) -> np.ndarray:
    """Synthetic-only values in the current registry; MT5 source sigma is fixed3."""
    o,h,l,c=(bars[k].to_numpy(float) for k in ('open','high','low','close'))
    close=pd.Series(c);out=np.full((len(bars),len(PIPE_VALUE_COLUMNS)),np.nan)
    def put(name,value):out[:,C[name]]=value
    for name,value in zip(('open','high','low','close'),(o,h,l,c)):put(name,value)
    for n in (20,50,200):put(f'ema_{n}',close.ewm(span=n,adjust=False).mean())
    for n in (6,17,50,168):put(f'hma_{n}',hma(o,n))
    mid,sd=ea_open4_synthetic(o)
    put('open_band_4_mid',mid);put('wonbi_upper',mid+3*sd);put('wonbi_lower',mid-3*sd)
    k=100*(close-pd.Series(l).rolling(14).min())/(pd.Series(h).rolling(14).max()-pd.Series(l).rolling(14).min())
    values={'price':pd.Series(hma(c,6)), 'RSI':_rsi(close),'STO':k.rolling(3).mean(),
            'DI':50+50*close.diff(5)/close.diff().abs().rolling(14).sum()}
    for prefix,value in values.items():
        lo,hi,basis,rup,rdn=_bands(value)
        names=('price_hma_6','price_band_lower','price_band_upper','price_regime_basis') if prefix=='price' else tuple(prefix+'_'+v for v in ('val','db','ub','basis'))
        for name,v in zip(names,(value,lo,hi,basis)):put(name,v)
        for suffix,v in [('regime_upper',rup),('regime_lower',rdn),('lower_out',value.where(value<lo)),
                         ('upper_out',value.where(value>hi)),('regime_slope',basis.diff())]:put(prefix+'_'+suffix,v)
    return out
'''+s[end:]
    s=s.replace('    wonbi_sigma: float = 3.0\n','').replace('indicator_frame(bars, self.wonbi_sigma)','indicator_frame(bars)')
    s=s.replace("        row[45:48] = f['final'][j][45:48]\n",'')
    # Discrete OUT status is a predicate on the current synthetic observation, not interpolation of sparse buffers.
    anchor='        volumes[-1] = len(part)'
    code='''        for p in ('price','RSI','STO','DI'):
            value=row[C['price_hma_6' if p=='price' else p+'_val']]
            lo=row[C['price_band_lower' if p=='price' else p+'_db']]
            hi=row[C['price_band_upper' if p=='price' else p+'_ub']]
            row[C[p+'_lower_out']]=value if value<lo else np.nan
            row[C[p+'_upper_out']]=value if value>hi else np.nan
'''
    s=s.replace(anchor,code+anchor)
    s=s.replace('    writer=CaptureWriter(root,data.symbol,timeframes,wire_version=wire_version)',"    if wire_version!=2:raise ValueError('current schema requires MSP3')\n    writer=CaptureWriter(root,data.symbol,timeframes,wire_version=2)")
    s=s.replace('x if wire_version==2 else x[:,:45]','x')
    return s

def main():
    normalized(ROOT/'Part2/part1_host/synthetic.py',synthetic)
    normalized(P/'event_engine/capture_io.py',lambda s:s.replace('cols not in (45, 48)','cols != len(wire.PIPE_VALUE_COLUMNS)'))
    normalized(ROOT/'Part2/part1_host/capture.py',lambda s:s.replace('cols not in (45,48)','cols != len(wire_schema().PIPE_VALUE_COLUMNS)'))
    for prefix in ('RSI','STO','DI'):
        path=P/'MT5'/f'{prefix}_of_Moses.mq5'
        def seed(s):
            anchor='   double lmin = 0.0, lmax = 0.0;'
            return rep(s,anchor,'''   // First recurrence reads limit+1 before writing limit. Never depend on allocator bytes.
   // Uncomputed output cells remain EMPTY_VALUE; these are internal zero seeds only.
   if(prev_calculated == 0 && limit+1 < rates_total)
   {
      smoothBuffer[limit+1] = 0.0;
      bbBasisBuf[limit+1] = 0.0;
   }

'''+anchor)
        normalized(path,seed)
    normalized(P/'MT5/PRICE_of_Moses.mq5',lambda s:rep(s,'      for(int i = ehlers_limit; i >= 0; i--)','''      // Seed the first read explicitly (R-8 with the default parameters).
      // The existing EMPTY_VALUE fallback below uses close[i], preserving the recurrence.
      if(prev_calculated == 0 && ehlers_limit+1 < rates_total)
         EhlersBuffer[ehlers_limit+1] = EMPTY_VALUE;
      for(int i = ehlers_limit; i >= 0; i--)'''))
    print('capture, synthetic, recursive seeds updated')
if __name__=='__main__':main()
