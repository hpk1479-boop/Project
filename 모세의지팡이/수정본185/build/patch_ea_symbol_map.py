"""EA: logical symbol (Wire identity) vs broker symbol (MT5 data access).

Everything from `int OnInit()` to the end of file stays byte-identical.
"""
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
P=ROOT/'Part1/program/MT5/THE_STAFF_OF_MOSES.mq5'
N='\r\n'

RESOLVER='''
// ------------------------------------------------------------------
// SYMBOL MAP: InpSymbols are logical symbols (Wire/Python identity).
// MT5 data access uses the broker symbol resolved once per logical symbol.
// exact name -> alias name -> alias with prefix/suffix; exactly one candidate
// is used, several or none reject only that symbol's feeds.
// ------------------------------------------------------------------
const string STAFF_SYMBOL_ALIASES[] = {"XAUUSD,GOLD","NAS100,US100,USTEC","BTCUSD,XBTUSD"};
string g_map_logical[];
string g_map_broker[];

bool StaffIsAlnum(ushort c)
{
   return (c>='A' && c<='Z') || (c>='a' && c<='z') || (c>='0' && c<='9');
}

bool StaffIsSeparator(ushort c)
{
   return c>32 && c<127 && !StaffIsAlnum(c);
}

string StaffSymbolCore(string s)
{
   string u=s;StringToUpper(u);
   string out="";
   for(int i=0;i<StringLen(u);++i)
   {
      ushort c=StringGetCharacter(u,i);
      if(StaffIsAlnum(c)) out+=ShortToString(c);
   }
   return out;
}

void StaffAddUnique(string &items[],string value)
{
   for(int i=0;i<ArraySize(items);++i) if(items[i]==value) return;
   int n=ArraySize(items);ArrayResize(items,n+1);items[n]=value;
}

void StaffSymbolAliases(string logical,string &aliases[])
{
   ArrayResize(aliases,0);
   string core=StaffSymbolCore(logical);
   if(StringLen(core)==0) return;
   StaffAddUnique(aliases,core);
   for(int g=0;g<ArraySize(STAFF_SYMBOL_ALIASES);++g)
   {
      string names[];
      int n=StringSplit(STAFF_SYMBOL_ALIASES[g],',',names);
      bool member=false;
      for(int i=0;i<n;++i) if(names[i]==core) member=true;
      if(member) for(int i=0;i<n;++i) StaffAddUnique(aliases,names[i]);
   }
}

bool StaffLowerSuffix(string suffix)
{
   int n=StringLen(suffix);
   if(n<1 || n>4) return false;
   for(int i=0;i<n;++i)
   {
      ushort c=StringGetCharacter(suffix,i);
      if(c<'a' || c>'z') return false;
   }
   return true;
}

// alias with an optional short prefix ending in a separator (m.XAUUSD) and an optional
// suffix that starts with a separator (XAUUSD+, XAUUSD.pro) or is 1-4 lowercase letters (XAUUSDm).
// XAUUSDT / US1000 / GOLDEUR are not matches.
bool StaffAffixMatch(string name,string alias)
{
   string up=name;StringToUpper(up);
   int pos=StringFind(up,alias);
   while(pos>=0)
   {
      string suffix=StringSubstr(name,pos+StringLen(alias));
      bool prefix_ok=(pos==0) || (pos<=5 && StaffIsSeparator(StringGetCharacter(name,pos-1)));
      bool suffix_ok=StringLen(suffix)==0 ||
                     (StringLen(suffix)<=6 && StaffIsSeparator(StringGetCharacter(suffix,0))) ||
                     StaffLowerSuffix(suffix);
      if(prefix_ok && suffix_ok) return true;
      pos=StringFind(up,alias,pos+1);
   }
   return false;
}

string StaffJoin(string &items[])
{
   string out="";
   for(int i=0;i<ArraySize(items);++i) out+=(i>0 ? "," : "")+items[i];
   return out;
}

string ResolveBrokerSymbol(string logical)
{
   for(int i=0;i<ArraySize(g_map_logical);++i)
      if(g_map_logical[i]==logical) return g_map_broker[i];

   string broker="",how="";
   bool custom=false;
   if(SymbolExist(logical,custom)) { broker=logical;how="exact"; }
   else
   {
      string aliases[],direct[],affixed[];
      StaffSymbolAliases(logical,aliases);
      int total=SymbolsTotal(false);
      for(int i=0;i<total;++i)
      {
         string name=SymbolName(i,false);
         string up=name;StringToUpper(up);
         bool equal=false;
         for(int a=0;a<ArraySize(aliases);++a) if(up==aliases[a]) equal=true;
         if(equal) { StaffAddUnique(direct,name);continue; }
         for(int a=0;a<ArraySize(aliases);++a)
            if(StaffAffixMatch(name,aliases[a])) { StaffAddUnique(affixed,name);break; }
      }
      string chosen[];
      if(ArraySize(direct)>0) { ArrayCopy(chosen,direct);how="alias"; }
      else { ArrayCopy(chosen,affixed);how="affix"; }
      if(ArraySize(chosen)==1) broker=chosen[0];
      else if(ArraySize(chosen)>1)
         PrintFormat("[SYMBOL MAP AMBIGUOUS] logical=%s candidates=%s",logical,StaffJoin(chosen));
      else
         PrintFormat("[SYMBOL MAP MISSING] logical=%s",logical);
   }
   if(StringLen(broker)>0) PrintFormat("[SYMBOL MAP] %s -> %s (%s)",logical,broker,how);
   int n=ArraySize(g_map_logical);
   ArrayResize(g_map_logical,n+1);ArrayResize(g_map_broker,n+1);
   g_map_logical[n]=logical;g_map_broker[n]=broker;
   return broker;
}

string StaffTesterLogicalSymbol()
{
   string logical=Trim(InpTesterLogicalSymbol);
   return StringLen(logical)>0 ? logical : _Symbol;
}
'''.replace('\n',N)

def main():
    data=P.read_bytes();text=data.decode('utf-8')
    assert '\r\n' in text and '\n' not in text.replace('\r\n','')
    tail=text[text.index('int OnInit()'):]
    reps=[
     ('input string InpSymbols = "XAUUSD+,NAS100,BTCUSD";  // comma-separated'+N,
      'input string InpSymbols = "XAUUSD+,NAS100,BTCUSD";  // comma-separated logical symbols'+N+
      '// Tester only: logical symbol written to Wire/native export. Empty = tester symbol (_Symbol).'+N+
      'input string InpTesterLogicalSymbol = "";'+N),
     ('   string          symbol;'+N+'   string          tf_text;'+N,
      '   string          symbol;         // logical symbol: Wire / Python identity'+N+
      '   string          broker_symbol;  // MT5 symbol used for market data'+N+
      '   string          tf_text;'+N),
     ('int CreateCustom(string name, string symbol, ENUM_TIMEFRAMES tf)'+N,
      RESOLVER.lstrip('\r\n')+N+'int CreateCustom(string name, string symbol, ENUM_TIMEFRAMES tf)'+N),
     # LIVE BuildFeed: resolve, reject conflicts, use broker symbol for data.
     ('   ENUM_TIMEFRAMES tf = ParseTimeframe(tf_text);'+N+
      '   if(!SymbolSelect(symbol, true))'+N+
      '   {'+N+
      '      PrintFormat("[THE STAFF OF MOSES] SymbolSelect 실패: %s", symbol);'+N+
      '      return false;'+N+
      '   }'+N+N+
      '   f.symbol  = symbol;'+N,
      '   ENUM_TIMEFRAMES tf = ParseTimeframe(tf_text);'+N+
      '   string broker = ResolveBrokerSymbol(symbol);'+N+
      '   if(StringLen(broker)==0) return false;'+N+
      '   for(int k=0;k<ArraySize(g_feeds);++k)'+N+
      '      if(g_feeds[k].broker_symbol==broker && g_feeds[k].symbol!=symbol)'+N+
      '      {'+N+
      '         PrintFormat("[SYMBOL MAP CONFLICT] %s 와 %s 가 같은 브로커 심볼 %s 로 연결됨 - %s 제외",g_feeds[k].symbol,symbol,broker,symbol);'+N+
      '         return false;'+N+
      '      }'+N+
      '   if(!SymbolSelect(broker, true))'+N+
      '   {'+N+
      '      PrintFormat("[THE STAFF OF MOSES] SymbolSelect 실패: %s (%s)", symbol, broker);'+N+
      '      return false;'+N+
      '   }'+N+N+
      '   f.symbol  = symbol;'+N+
      '   f.broker_symbol = broker;'+N),
     ('   f.ema20  = iMA(symbol, tf, 20, 0, MODE_EMA, PRICE_CLOSE);'+N+
      '   f.ema50  = iMA(symbol, tf, 50, 0, MODE_EMA, PRICE_CLOSE);'+N+
      '   f.ema200 = iMA(symbol, tf, 200, 0, MODE_EMA, PRICE_CLOSE);'+N+N+
      '   f.price = CreateCustom(STAFF_PRICE_INDICATOR, symbol, tf);'+N+
      '   f.rsi   = CreateCustom(STAFF_RSI_INDICATOR,   symbol, tf);'+N+
      '   f.sto   = CreateCustom(STAFF_STO_INDICATOR,   symbol, tf);'+N+
      '   f.di    = CreateCustom(STAFF_DI_INDICATOR,    symbol, tf);'+N,
      '   f.ema20  = iMA(broker, tf, 20, 0, MODE_EMA, PRICE_CLOSE);'+N+
      '   f.ema50  = iMA(broker, tf, 50, 0, MODE_EMA, PRICE_CLOSE);'+N+
      '   f.ema200 = iMA(broker, tf, 200, 0, MODE_EMA, PRICE_CLOSE);'+N+N+
      '   f.price = CreateCustom(STAFF_PRICE_INDICATOR, broker, tf);'+N+
      '   f.rsi   = CreateCustom(STAFF_RSI_INDICATOR,   broker, tf);'+N+
      '   f.sto   = CreateCustom(STAFF_STO_INDICATOR,   broker, tf);'+N+
      '   f.di    = CreateCustom(STAFF_DI_INDICATOR,    broker, tf);'+N),
     ('   if(SeriesInfoInteger(f.symbol,f.tf,SERIES_BARS_COUNT)!=f.cached_series_count ||'+N+
      '      SeriesInfoInteger(f.symbol,f.tf,SERIES_FIRSTDATE)!=f.cached_first_date) return false;'+N,
      '   if(SeriesInfoInteger(f.broker_symbol,f.tf,SERIES_BARS_COUNT)!=f.cached_series_count ||'+N+
      '      SeriesInfoInteger(f.broker_symbol,f.tf,SERIES_FIRSTDATE)!=f.cached_first_date) return false;'+N),
     ('   f.cached_series_count=SeriesInfoInteger(f.symbol,f.tf,SERIES_BARS_COUNT);'+N+
      '   f.cached_first_date=SeriesInfoInteger(f.symbol,f.tf,SERIES_FIRSTDATE);'+N,
      '   f.cached_series_count=SeriesInfoInteger(f.broker_symbol,f.tf,SERIES_BARS_COUNT);'+N+
      '   f.cached_first_date=SeriesInfoInteger(f.broker_symbol,f.tf,SERIES_FIRSTDATE);'+N),
     ('   MqlRates current[1];'+N+'   if(CopyRates(f.symbol,f.tf,0,1,current)!=1) return false;'+N+'   long seq=f.wire_seq+1;'+N,
      '   MqlRates current[1];'+N+'   if(CopyRates(f.broker_symbol,f.tf,0,1,current)!=1) return false;'+N+'   long seq=f.wire_seq+1;'+N),
     ('   int available=(int)SeriesInfoInteger(f.symbol,f.tf,SERIES_BARS_COUNT);'+N,
      '   int available=(int)SeriesInfoInteger(f.broker_symbol,f.tf,SERIES_BARS_COUNT);'+N),
     ('   int got = CopyRates(f.symbol, f.tf, 0, count, rates);'+N,
      '   int got = CopyRates(f.broker_symbol, f.tf, 0, count, rates);'+N),
     ('   MqlRates current[1];'+N+'   if(CopyRates(f.symbol,f.tf,0,1,current)!=1) return false;'+N+N+
      '   // 최초 / 새 봉',
      '   MqlRates current[1];'+N+'   if(CopyRates(f.broker_symbol,f.tf,0,1,current)!=1) return false;'+N+N+
      '   // 최초 / 새 봉'),
     # Tester: logical identity from input (default _Symbol), data from _Symbol.
     ('   if(requested_symbol!=_Symbol)'+N+'   {'+N+
      '      PrintFormat("[THE STAFF OF MOSES][DATA BUILD] request symbol 불일치 request=%s tester=%s",requested_symbol,_Symbol);'+N,
      '   if(requested_symbol!=StaffTesterLogicalSymbol())'+N+'   {'+N+
      '      PrintFormat("[THE STAFF OF MOSES][DATA BUILD] request symbol 불일치 request=%s logical=%s tester=%s",requested_symbol,StaffTesterLogicalSymbol(),_Symbol);'+N),
     ('         FileWriteString(m,"symbol\\t"+_Symbol+"\\r\\n");'+N,
      '         FileWriteString(m,"symbol\\t"+StaffTesterLogicalSymbol()+"\\r\\n");'+N),
     ('   ENUM_TIMEFRAMES tf=ParseTimeframe(tf_text);'+N+
      '   if(!SymbolSelect(symbol,true))'+N+'   {'+N+
      '      PrintFormat("[THE STAFF OF MOSES][BACKTEST] SymbolSelect 실패: %s",symbol);'+N+
      '      return false;'+N+'   }'+N+N+
      '   f.symbol=symbol;'+N,
      '   ENUM_TIMEFRAMES tf=ParseTimeframe(tf_text);'+N+
      '   string broker=_Symbol;'+N+
      '   if(!SymbolSelect(broker,true))'+N+'   {'+N+
      '      PrintFormat("[THE STAFF OF MOSES][BACKTEST] SymbolSelect 실패: %s (%s)",symbol,broker);'+N+
      '      return false;'+N+'   }'+N+N+
      '   f.symbol=symbol;'+N+
      '   f.broker_symbol=broker;'+N),
     ('   int seed_bars=CopyRates(symbol,tf,0,800,seed_rates);'+N,
      '   int seed_bars=CopyRates(broker,tf,0,800,seed_rates);'+N),
     ('   f.ema20  = iMA(symbol, tf, 20, 0, MODE_EMA, PRICE_CLOSE);'+N+
      '   f.ema50  = iMA(symbol, tf, 50, 0, MODE_EMA, PRICE_CLOSE);'+N+
      '   f.ema200 = iMA(symbol, tf, 200, 0, MODE_EMA, PRICE_CLOSE);'+N+
      '   f.price=CreateCustom(STAFF_PRICE_INDICATOR,symbol,tf);'+N+
      '   f.rsi=CreateCustom(STAFF_RSI_INDICATOR,symbol,tf);'+N+
      '   f.sto=CreateCustom(STAFF_STO_INDICATOR,symbol,tf);'+N+
      '   f.di=CreateCustom(STAFF_DI_INDICATOR,symbol,tf);'+N,
      '   f.ema20  = iMA(broker, tf, 20, 0, MODE_EMA, PRICE_CLOSE);'+N+
      '   f.ema50  = iMA(broker, tf, 50, 0, MODE_EMA, PRICE_CLOSE);'+N+
      '   f.ema200 = iMA(broker, tf, 200, 0, MODE_EMA, PRICE_CLOSE);'+N+
      '   f.price=CreateCustom(STAFF_PRICE_INDICATOR,broker,tf);'+N+
      '   f.rsi=CreateCustom(STAFF_RSI_INDICATOR,broker,tf);'+N+
      '   f.sto=CreateCustom(STAFF_STO_INDICATOR,broker,tf);'+N+
      '   f.di=CreateCustom(STAFF_DI_INDICATOR,broker,tf);'+N),
     ('   if(CopyRates(f.symbol,f.tf,0,1,current)!=1)'+N+'   {'+N+'      static bool rates_reported=false;'+N,
      '   if(CopyRates(f.broker_symbol,f.tf,0,1,current)!=1)'+N+'   {'+N+'      static bool rates_reported=false;'+N),
     ('      int got=CopyRates(f.symbol,f.tf,0,HMA_INPUT_BARS,rates);'+N,
      '      int got=CopyRates(f.broker_symbol,f.tf,0,HMA_INPUT_BARS,rates);'+N),
     ('   // Strategy Tester owns one main symbol per pass. BACKTEST never fans out to LIVE InpSymbols.'+N+
      '   int ns = StringSplit(_Symbol, \',\', syms);'+N,
      '   // Strategy Tester owns one main symbol per pass. BACKTEST never fans out to LIVE InpSymbols.'+N+
      '   // Feed identity is the logical symbol; market data comes from _Symbol.'+N+
      '   int ns = StringSplit(StaffTesterLogicalSymbol(), \',\', syms);'+N),
    ]
    for old,new in reps:
        assert text.count(old)==1,old[:90]
        text=text.replace(old,new)
    assert text[text.index('int OnInit()'):]==tail,'OnInit..EOF must stay identical'
    assert '\n' not in text.replace('\r\n','')
    P.write_bytes(text.encode('utf-8'))
    print('patched',len(reps))
if __name__=='__main__':main()
