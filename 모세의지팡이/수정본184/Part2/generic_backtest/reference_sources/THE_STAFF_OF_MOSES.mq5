//+------------------------------------------------------------------+
//|                                           THE_STAFF_OF_MOSES.mq5     |
//| THE STAFF OF MOSES - MT5 resident binary Named-Pipe publisher           |
//|                                                                  |
//| 역할                                                             |
//|  - 하나의 EA가 여러 Symbol / Timeframe을 백그라운드 호출          |
//|  - OHLC + MT5 EMA + 커스텀 OZ 지표 buffer를 Named Pipe로 전달  |
//|  - 전략 HMA6/17/50/168은 OPEN 기준, PRICE 라인은 CLOSE 기반 HMA6로 엄격히 분리 |
//|                                                                  |
//| IMPORTANT HMA SEMANTICS                                          |
//|  hma_6 / hma_17 / hma_50 / hma_168 = HMA(OPEN) : 전략 HMA 전용     |
//|  price_hma_6         = HMA(CLOSE) : PRICE 라인 / PRICE Band OUT/IN 전용       |
//|  절대로 서로 대체하거나 혼용하지 마십시오.                       |
//+------------------------------------------------------------------+
#property copyright "Oilve Oil"
#property version   "1.50"
#property strict

input group "=== Targets ==="
input string InpSymbols = "XAUUSD+,NAS100,BTCUSD";  // comma-separated

// ------------------------------------------------------------------
// FIXED STAFF CONFIGURATION
// 사용자가 수정할 필요가 없는 항목은 코드 내부에 고정합니다.
// ------------------------------------------------------------------
const string STAFF_TIMEFRAMES        = "1m,2m,3m,4m,5m,6m,10m,12m,15m,20m,30m,1h,2h,3h,4h,6h,8h,12h,1D";
const int    STAFF_BARS_TO_EXPORT    = 650;
const int    STAFF_TIMER_MS          = 1000;
const int    STAFF_WARM_BARS         = 32;
const int    STAFF_VALUE_COLUMNS     = 45;
const string STAFF_PIPE_NAME         = "\\\\.\\pipe\\StaffOfMoses_v1";
const int    STAFF_PIPE_MAGIC        = 0x534D4F53; // "SMOS"
const int    STAFF_PIPE_VERSION      = 1;

// 원비(WONBI): 전략 공용 OPEN Bollinger 기본 정의.
// Telegram 실시간 표준편차 변경은 Python THE STAFF OF MOSES가 OPEN 원자료로 재계산하며,
// 아래 Staff의 1.79/2.79/3.00/4.00 전송 슬롯은 Named Pipe v1 호환을 위해 유지합니다.
const int    WONBI_LENGTH            = 4;
const double WONBI_DEFAULT_SIGMA     = 3.00;

int  g_pipe = INVALID_HANDLE;
long g_snapshot_seq = 0;
#include "STAFF_Identity_Status.mqh"

// ------------------------------------------------------------------
// EXACT CUSTOM BUFFER MAP (production set)
// PRICE_of_Moses : upper=0, lower=1, CLOSE HMA6=5, regime basis=8, up=10, down=11
// RSI_of_Moses   : lower=0, upper=1, value(cRSI)=2, regime basis=5, up=7, down=8
// STO_of_Moses   : lower=0, upper=1, value(cSTO)=2, regime basis=5, up=7, down=8
// DI_of_Moses    : lower=0, upper=1, value(cDI)=2, regime basis=5, up=7, down=8
// ------------------------------------------------------------------
const string STAFF_PRICE_INDICATOR   = "PRICE_of_Moses";
const int    STAFF_PRICE_UPPER_BUF   = 0;
const int    STAFF_PRICE_LOWER_BUF   = 1;
const int    STAFF_PRICE_HMA6_BUF    = 5;  // CLOSE-based HMA6
const int    STAFF_PRICE_REGIME_BASIS_BUF = 8;
const int    STAFF_PRICE_REGIME_UP_BUF    = 10;
const int    STAFF_PRICE_REGIME_DN_BUF    = 11;

const string STAFF_RSI_INDICATOR     = "RSI_of_Moses";
const int    STAFF_RSI_VALUE_BUF     = 2;
const int    STAFF_RSI_LOWER_BUF     = 0;
const int    STAFF_RSI_UPPER_BUF     = 1;
const int    STAFF_RSI_BASIS_BUF     = 5;
const int    STAFF_RSI_REGIME_UP_BUF = 7;
const int    STAFF_RSI_REGIME_DN_BUF = 8;

const string STAFF_STO_INDICATOR     = "STO_of_Moses";
const int    STAFF_STO_VALUE_BUF     = 2;
const int    STAFF_STO_LOWER_BUF     = 0;
const int    STAFF_STO_UPPER_BUF     = 1;
const int    STAFF_STO_BASIS_BUF     = 5;
const int    STAFF_STO_REGIME_UP_BUF = 7;
const int    STAFF_STO_REGIME_DN_BUF = 8;

const string STAFF_DI_INDICATOR      = "DI_of_Moses";
const int    STAFF_DI_VALUE_BUF      = 2;
const int    STAFF_DI_LOWER_BUF      = 0;
const int    STAFF_DI_UPPER_BUF      = 1;
const int    STAFF_DI_BASIS_BUF      = 5;
const int    STAFF_DI_REGIME_UP_BUF  = 7;
const int    STAFF_DI_REGIME_DN_BUF  = 8;

struct FeedContext
{
   string          symbol;
   string          tf_text;
   ENUM_TIMEFRAMES tf;
   int             ema20;
   int             ema21;
   int             ema50;
   int             ema200;
   int             price;
   int             rsi;
   int             sto;
   int             di;
   bool            warned;
};

FeedContext g_feeds[];

//+------------------------------------------------------------------+
string Trim(string s)
{
   StringTrimLeft(s);
   StringTrimRight(s);
   return s;
}

ENUM_TIMEFRAMES ParseTimeframe(string text)
{
   string s = text;
   StringToLower(s);
   s = Trim(s);

   if(s=="1m")  return PERIOD_M1;
   if(s=="2m")  return PERIOD_M2;
   if(s=="3m")  return PERIOD_M3;
   if(s=="4m")  return PERIOD_M4;
   if(s=="5m")  return PERIOD_M5;
   if(s=="6m")  return PERIOD_M6;
   if(s=="10m") return PERIOD_M10;
   if(s=="12m") return PERIOD_M12;
   if(s=="15m") return PERIOD_M15;
   if(s=="20m") return PERIOD_M20;
   if(s=="30m") return PERIOD_M30;
   if(s=="1h")  return PERIOD_H1;
   if(s=="2h")  return PERIOD_H2;
   if(s=="3h")  return PERIOD_H3;
   if(s=="4h")  return PERIOD_H4;
   if(s=="6h")  return PERIOD_H6;
   if(s=="8h")  return PERIOD_H8;
   if(s=="12h") return PERIOD_H12;
   if(s=="1d")  return PERIOD_D1;
   return PERIOD_CURRENT;
}

bool IsSupportedTF(string text)
{
   string s=text;
   StringToLower(s);
   s=Trim(s);
   string all="1m,2m,3m,4m,5m,6m,10m,12m,15m,20m,30m,1h,2h,3h,4h,6h,8h,12h,1d";
   return StringFind(","+all+",", ","+s+",") >= 0;
}

void ReleaseHandle(int &h)
{
   if(h != INVALID_HANDLE)
   {
      IndicatorRelease(h);
      h = INVALID_HANDLE;
   }
}

//+------------------------------------------------------------------+
// HMA 계산은 MT5 EA 내부에서 수행합니다.
// OPEN HMA6/17은 전략 cross 전용입니다.
// PRICE HMA6(CLOSE)는 여기서 계산하지 않고 OZ PRICE buffer를 직접 읽습니다.
//+------------------------------------------------------------------+
double WMAAt(const double &src[], int idx, int length)
{
   if(idx < length-1 || length <= 0) return EMPTY_VALUE;
   double num=0.0, den=0.0;
   int w=1;
   for(int k=idx-length+1; k<=idx; ++k, ++w)
   {
      if(src[k] == EMPTY_VALUE || !MathIsValidNumber(src[k])) return EMPTY_VALUE;
      num += src[k] * (double)w;
      den += (double)w;
   }
   if(den == 0.0) return EMPTY_VALUE;
   return num/den;
}

void CalcHMA(const double &src[], int count, int length, double &out[])
{
   ArrayResize(out, count);
   ArrayInitialize(out, EMPTY_VALUE);

   int half = length/2;
   int root = (int)MathSqrt((double)length);
   if(half < 1) half=1;
   if(root < 1) root=1;

   double raw[];
   ArrayResize(raw, count);
   ArrayInitialize(raw, EMPTY_VALUE);

   for(int i=0; i<count; ++i)
   {
      double a = WMAAt(src, i, half);
      double b = WMAAt(src, i, length);
      if(a != EMPTY_VALUE && b != EMPTY_VALUE)
         raw[i] = 2.0*a - b;
   }

   for(int i=0; i<count; ++i)
      out[i] = WMAAt(raw, i, root);
}

void CalcOpenBands4(const double &src[], int count,
                    double &mid[],
                    double &upper179[], double &lower179[],
                    double &upper279[], double &lower279[],
                    double &upper300[], double &lower300[],
                    double &upper400[], double &lower400[])
{
   ArrayResize(mid,count);      ArrayInitialize(mid,EMPTY_VALUE);
   ArrayResize(upper179,count); ArrayInitialize(upper179,EMPTY_VALUE);
   ArrayResize(lower179,count); ArrayInitialize(lower179,EMPTY_VALUE);
   ArrayResize(upper279,count); ArrayInitialize(upper279,EMPTY_VALUE);
   ArrayResize(lower279,count); ArrayInitialize(lower279,EMPTY_VALUE);
   ArrayResize(upper300,count); ArrayInitialize(upper300,EMPTY_VALUE);
   ArrayResize(lower300,count); ArrayInitialize(lower300,EMPTY_VALUE);
   ArrayResize(upper400,count); ArrayInitialize(upper400,EMPTY_VALUE);
   ArrayResize(lower400,count); ArrayInitialize(lower400,EMPTY_VALUE);

   const int length=WONBI_LENGTH;
   for(int i=length-1; i<count; ++i)
   {
      double sum=0.0;
      bool valid=true;
      for(int k=i-length+1; k<=i; ++k)
      {
         if(src[k]==EMPTY_VALUE || !MathIsValidNumber(src[k])) { valid=false; break; }
         sum += src[k];
      }
      if(!valid) continue;

      double m=sum/(double)length;
      double var=0.0;
      for(int k=i-length+1; k<=i; ++k)
      {
         double d=src[k]-m;
         var += d*d;
      }
      double sd=MathSqrt(var/(double)length); // population std, ddof=0

      mid[i]=m;
      upper179[i]=m+1.79*sd; lower179[i]=m-1.79*sd;
      upper279[i]=m+2.79*sd; lower279[i]=m-2.79*sd;
      // 3.00 슬롯 = 원비 기본값(길이 4 / 표준편차 3 / OPEN).
      upper300[i]=m+WONBI_DEFAULT_SIGMA*sd; lower300[i]=m-WONBI_DEFAULT_SIGMA*sd;
      upper400[i]=m+4.00*sd; lower400[i]=m-4.00*sd;
   }
}

bool CopyOneBuffer(int handle, int buffer_num, int count, double &out[])
{
   ArrayResize(out, count);
   ArrayInitialize(out, EMPTY_VALUE);

   if(handle == INVALID_HANDLE)
      return false;

   ResetLastError();
   int copied = CopyBuffer(handle, buffer_num, 0, count, out);

   if(copied != count)
      return false;

   // CopyBuffer가 count개를 반환했어도 값 전체가 EMPTY/invalid면 실패로 봅니다.
   bool has_valid=false;
   int check_from=MathMax(0,count-20);
   for(int i=check_from; i<count; ++i)
   {
      if(out[i] != EMPTY_VALUE && MathIsValidNumber(out[i]))
      {
         has_valid=true;
         break;
      }
   }
   return has_valid;
}

int CreateCustom(string name, string symbol, ENUM_TIMEFRAMES tf)
{
   if(StringLen(Trim(name)) == 0) return INVALID_HANDLE;
   ResetLastError();
   int h = iCustom(symbol, tf, name);
   if(h == INVALID_HANDLE)
      PrintFormat("[THE STAFF OF MOSES] iCustom 실패: %s / %s / %s / err=%d",
                  symbol, EnumToString(tf), name, GetLastError());
   return h;
}

bool BuildFeed(string symbol, string tf_text, FeedContext &f)
{
   symbol = Trim(symbol);
   tf_text = Trim(tf_text);
   if(StringLen(symbol)==0 || !IsSupportedTF(tf_text)) return false;

   ENUM_TIMEFRAMES tf = ParseTimeframe(tf_text);
   if(!SymbolSelect(symbol, true))
   {
      PrintFormat("[THE STAFF OF MOSES] SymbolSelect 실패: %s", symbol);
      return false;
   }

   f.symbol  = symbol;
   f.tf_text = tf_text;
   StringToLower(f.tf_text);
   f.tf      = tf;
   f.warned  = false;

   // EMA는 모두 CLOSE 기준입니다.
   f.ema20  = iMA(symbol, tf, 20, 0, MODE_EMA, PRICE_CLOSE);
   f.ema21  = iMA(symbol, tf, 21, 0, MODE_EMA, PRICE_CLOSE);
   f.ema50  = iMA(symbol, tf, 50, 0, MODE_EMA, PRICE_CLOSE);
   f.ema200 = iMA(symbol, tf, 200, 0, MODE_EMA, PRICE_CLOSE);

   f.price = CreateCustom(STAFF_PRICE_INDICATOR, symbol, tf);
   f.rsi   = CreateCustom(STAFF_RSI_INDICATOR,   symbol, tf);
   f.sto   = CreateCustom(STAFF_STO_INDICATOR,   symbol, tf);
   f.di    = CreateCustom(STAFF_DI_INDICATOR,    symbol, tf);

   if(f.ema20==INVALID_HANDLE || f.ema21==INVALID_HANDLE || f.ema50==INVALID_HANDLE || f.ema200==INVALID_HANDLE)
   {
      PrintFormat("[THE STAFF OF MOSES] ❌ EMA handle 생성 실패: %s %s", symbol, tf_text);
      ReleaseFeed(f);
      return false;
   }

   if(f.price==INVALID_HANDLE || f.rsi==INVALID_HANDLE || f.sto==INVALID_HANDLE || f.di==INVALID_HANDLE)
   {
      PrintFormat(
         "[THE STAFF OF MOSES] ❌ 필수 커스텀 지표 handle 생성 실패: %s %s | PRICE=%s RSI=%s STO=%s DI=%s",
         symbol, tf_text,
         (f.price==INVALID_HANDLE ? "FAIL" : "OK"),
         (f.rsi==INVALID_HANDLE   ? "FAIL" : "OK"),
         (f.sto==INVALID_HANDLE   ? "FAIL" : "OK"),
         (f.di==INVALID_HANDLE    ? "FAIL" : "OK")
      );
      ReleaseFeed(f);
      return false;
   }

   return true;
}

void ReleaseFeed(FeedContext &f)
{
   ReleaseHandle(f.ema20);
   ReleaseHandle(f.ema21);
   ReleaseHandle(f.ema50);
   ReleaseHandle(f.ema200);
   ReleaseHandle(f.price);
   ReleaseHandle(f.rsi);
   ReleaseHandle(f.sto);
   ReleaseHandle(f.di);
}

double IPCValue(double v)
{
   if(v==EMPTY_VALUE || !MathIsValidNumber(v)) return EMPTY_VALUE;
   return v;
}

void PutIPCValue(double &values[], int row, int col, double v)
{
   values[row*STAFF_VALUE_COLUMNS + col] = IPCValue(v);
}

void CloseStaffPipe()
{
   if(g_pipe != INVALID_HANDLE)
   {
      FileClose(g_pipe);
      g_pipe = INVALID_HANDLE;
   }
}

bool EnsureStaffPipe()
{
   if(g_pipe != INVALID_HANDLE) return true;

   ResetLastError();
   g_pipe = FileOpen(STAFF_PIPE_NAME, FILE_READ|FILE_WRITE|FILE_BIN);
   if(g_pipe == INVALID_HANDLE)
      return false;

   PrintFormat("[THE STAFF OF MOSES] ✅ Named Pipe 연결: %s", STAFF_PIPE_NAME);
   return true;
}

bool WritePipeSnapshot(const FeedContext &f,
                       long &times[], long &volumes[], double &values[], int bar_count)
{
   if(!EnsureStaffPipe()) return false;

   uchar sym_bytes[], tf_bytes[];
   int sym_n = StringToCharArray(f.symbol, sym_bytes, 0, WHOLE_ARRAY, CP_UTF8);
   int tf_n  = StringToCharArray(f.tf_text, tf_bytes, 0, WHOLE_ARRAY, CP_UTF8);
   int sym_len = MathMax(0, sym_n-1); // trailing NUL 제외
   int tf_len  = MathMax(0, tf_n-1);

   if(sym_len<=0 || tf_len<=0 || bar_count<=0 || ArraySize(values)!=bar_count*STAFF_VALUE_COLUMNS)
      return false;

   g_snapshot_seq++;
   ResetLastError();

   // Wire header: <IIqIIII = 32 bytes, little-endian
   FileWriteInteger(g_pipe, STAFF_PIPE_MAGIC,   INT_VALUE);
   FileWriteInteger(g_pipe, STAFF_PIPE_VERSION, INT_VALUE);
   FileWriteLong(g_pipe, g_snapshot_seq);
   FileWriteInteger(g_pipe, sym_len,             INT_VALUE);
   FileWriteInteger(g_pipe, tf_len,              INT_VALUE);
   FileWriteInteger(g_pipe, bar_count,           INT_VALUE);
   FileWriteInteger(g_pipe, STAFF_VALUE_COLUMNS, INT_VALUE);

   uint w_sym = FileWriteArray(g_pipe, sym_bytes, 0, sym_len);
   uint w_tf  = FileWriteArray(g_pipe, tf_bytes,  0, tf_len);
   uint w_t   = FileWriteArray(g_pipe, times,     0, bar_count);
   uint w_v   = FileWriteArray(g_pipe, volumes,   0, bar_count);
   uint w_x   = FileWriteArray(g_pipe, values,    0, bar_count*STAFF_VALUE_COLUMNS);
   FileFlush(g_pipe);

   int err=GetLastError();
   if(w_sym!=(uint)sym_len || w_tf!=(uint)tf_len ||
      w_t!=(uint)bar_count || w_v!=(uint)bar_count ||
      w_x!=(uint)(bar_count*STAFF_VALUE_COLUMNS) || err!=0)
   {
      PrintFormat("[THE STAFF OF MOSES] ⚠️ Named Pipe write 실패: %s %s err=%d", f.symbol, f.tf_text, err);
      CloseStaffPipe();
      return false;
   }
   return true;
}

bool PublishFeed(FeedContext &f)
{
   const int want = STAFF_BARS_TO_EXPORT;
   int count = want + STAFF_WARM_BARS;

   MqlRates rates[];
   ArrayResize(rates, count);
   int got = CopyRates(f.symbol, f.tf, 0, count, rates);
   if(got < 250)
   {
      if(!f.warned)
      {
         PrintFormat("[THE STAFF OF MOSES] 시세 부족: %s %s got=%d", f.symbol, f.tf_text, got);
         f.warned=true;
      }
      return false;
   }
   count = got;

   double open_src[];
   ArrayResize(open_src, count);
   for(int i=0; i<count; ++i) open_src[i]=rates[i].open;

   double hma6_open[], hma17_open[], hma50_open[], hma168_open[];
   CalcHMA(open_src, count, 6, hma6_open);
   CalcHMA(open_src, count, 17, hma17_open);
   CalcHMA(open_src, count, 50, hma50_open);
   CalcHMA(open_src, count, 168, hma168_open);

   double open_band_4_mid[];
   double open_band_179_upper[], open_band_179_lower[];
   double open_band_279_upper[], open_band_279_lower[];
   double open_band_300_upper[], open_band_300_lower[];
   double open_band_400_upper[], open_band_400_lower[];
   CalcOpenBands4(open_src, count, open_band_4_mid,
                  open_band_179_upper, open_band_179_lower,
                  open_band_279_upper, open_band_279_lower,
                  open_band_300_upper, open_band_300_lower,
                  open_band_400_upper, open_band_400_lower);

   double ema20[], ema21[], ema50[], ema200[];
   double p_upper[], p_lower[], p_hma6[], p_regime_basis[], p_regime_up[], p_regime_dn[];
   double r_val[], r_low[], r_up[], r_basis[], r_regime_up[], r_regime_dn[];
   double s_val[], s_low[], s_up[], s_basis[], s_regime_up[], s_regime_dn[];
   double d_val[], d_low[], d_up[], d_basis[], d_regime_up[], d_regime_dn[];

   bool ok_ema = CopyOneBuffer(f.ema20,0,count,ema20)
              && CopyOneBuffer(f.ema21,0,count,ema21)
              && CopyOneBuffer(f.ema50,0,count,ema50)
              && CopyOneBuffer(f.ema200,0,count,ema200);
   bool ok_price = CopyOneBuffer(f.price,STAFF_PRICE_UPPER_BUF,count,p_upper)
                && CopyOneBuffer(f.price,STAFF_PRICE_LOWER_BUF,count,p_lower)
                && CopyOneBuffer(f.price,STAFF_PRICE_HMA6_BUF,count,p_hma6)
                && CopyOneBuffer(f.price,STAFF_PRICE_REGIME_BASIS_BUF,count,p_regime_basis)
                && CopyOneBuffer(f.price,STAFF_PRICE_REGIME_UP_BUF,count,p_regime_up)
                && CopyOneBuffer(f.price,STAFF_PRICE_REGIME_DN_BUF,count,p_regime_dn);
   bool ok_rsi = CopyOneBuffer(f.rsi,STAFF_RSI_VALUE_BUF,count,r_val)
              && CopyOneBuffer(f.rsi,STAFF_RSI_LOWER_BUF,count,r_low)
              && CopyOneBuffer(f.rsi,STAFF_RSI_UPPER_BUF,count,r_up)
              && CopyOneBuffer(f.rsi,STAFF_RSI_BASIS_BUF,count,r_basis)
              && CopyOneBuffer(f.rsi,STAFF_RSI_REGIME_UP_BUF,count,r_regime_up)
              && CopyOneBuffer(f.rsi,STAFF_RSI_REGIME_DN_BUF,count,r_regime_dn);
   bool ok_sto = CopyOneBuffer(f.sto,STAFF_STO_VALUE_BUF,count,s_val)
              && CopyOneBuffer(f.sto,STAFF_STO_LOWER_BUF,count,s_low)
              && CopyOneBuffer(f.sto,STAFF_STO_UPPER_BUF,count,s_up)
              && CopyOneBuffer(f.sto,STAFF_STO_BASIS_BUF,count,s_basis)
              && CopyOneBuffer(f.sto,STAFF_STO_REGIME_UP_BUF,count,s_regime_up)
              && CopyOneBuffer(f.sto,STAFF_STO_REGIME_DN_BUF,count,s_regime_dn);
   bool ok_di = CopyOneBuffer(f.di,STAFF_DI_VALUE_BUF,count,d_val)
             && CopyOneBuffer(f.di,STAFF_DI_LOWER_BUF,count,d_low)
             && CopyOneBuffer(f.di,STAFF_DI_UPPER_BUF,count,d_up)
             && CopyOneBuffer(f.di,STAFF_DI_BASIS_BUF,count,d_basis)
             && CopyOneBuffer(f.di,STAFF_DI_REGIME_UP_BUF,count,d_regime_up)
             && CopyOneBuffer(f.di,STAFF_DI_REGIME_DN_BUF,count,d_regime_dn);

   if(!ok_ema || !ok_price || !ok_rsi || !ok_sto || !ok_di)
   {
      if(!f.warned)
         PrintFormat("[THE STAFF OF MOSES] ❌ buffer 실패: %s %s | EMA=%s PRICE=%s RSI=%s STO=%s DI=%s err=%d",
                     f.symbol,f.tf_text,(ok_ema?"OK":"FAIL"),(ok_price?"OK":"FAIL"),
                     (ok_rsi?"OK":"FAIL"),(ok_sto?"OK":"FAIL"),(ok_di?"OK":"FAIL"),GetLastError());
      f.warned=true;
      // OHLC remains available; failed optional groups use EMPTY_VALUE below.
   }

   int payload_count=MathMin(want,count);
   int first=MathMax(0,count-payload_count);
   long times[], volumes[];
   double values[];
   ArrayResize(times,payload_count);
   ArrayResize(volumes,payload_count);
   ArrayResize(values,payload_count*STAFF_VALUE_COLUMNS);

   for(int row=0; row<payload_count; ++row)
   {
      int i=first+row;
      times[row]=(long)rates[i].time;
      volumes[row]=(long)rates[i].tick_volume;
      PutIPCValue(values,row, 0,rates[i].open);
      PutIPCValue(values,row, 1,rates[i].high);
      PutIPCValue(values,row, 2,rates[i].low);
      PutIPCValue(values,row, 3,rates[i].close);
      PutIPCValue(values,row, 4,(ok_ema ? ema20[i] : EMPTY_VALUE));
      PutIPCValue(values,row, 5,(ok_ema ? ema21[i] : EMPTY_VALUE));
      PutIPCValue(values,row, 6,(ok_ema ? ema50[i] : EMPTY_VALUE));
      PutIPCValue(values,row, 7,(ok_ema ? ema200[i] : EMPTY_VALUE));
      PutIPCValue(values,row, 8,hma6_open[i]);
      PutIPCValue(values,row, 9,hma17_open[i]);
      PutIPCValue(values,row,10,hma50_open[i]);
      PutIPCValue(values,row,11,hma168_open[i]);
      PutIPCValue(values,row,12,open_band_4_mid[i]);
      PutIPCValue(values,row,13,open_band_179_lower[i]);
      PutIPCValue(values,row,14,open_band_179_upper[i]);
      PutIPCValue(values,row,15,open_band_279_lower[i]);
      PutIPCValue(values,row,16,open_band_279_upper[i]);
      PutIPCValue(values,row,17,open_band_300_lower[i]);
      PutIPCValue(values,row,18,open_band_300_upper[i]);
      PutIPCValue(values,row,19,open_band_400_lower[i]);
      PutIPCValue(values,row,20,open_band_400_upper[i]);
      PutIPCValue(values,row,21,(ok_price ? p_hma6[i] : EMPTY_VALUE));
      PutIPCValue(values,row,22,(ok_price ? p_lower[i] : EMPTY_VALUE));
      PutIPCValue(values,row,23,(ok_price ? p_upper[i] : EMPTY_VALUE));
      PutIPCValue(values,row,24,(ok_rsi ? r_val[i] : EMPTY_VALUE));
      PutIPCValue(values,row,25,(ok_rsi ? r_low[i] : EMPTY_VALUE));
      PutIPCValue(values,row,26,(ok_rsi ? r_up[i] : EMPTY_VALUE));
      PutIPCValue(values,row,27,(ok_rsi ? r_basis[i] : EMPTY_VALUE));
      PutIPCValue(values,row,28,(ok_sto ? s_val[i] : EMPTY_VALUE));
      PutIPCValue(values,row,29,(ok_sto ? s_low[i] : EMPTY_VALUE));
      PutIPCValue(values,row,30,(ok_sto ? s_up[i] : EMPTY_VALUE));
      PutIPCValue(values,row,31,(ok_sto ? s_basis[i] : EMPTY_VALUE));
      PutIPCValue(values,row,32,(ok_di ? d_val[i] : EMPTY_VALUE));
      PutIPCValue(values,row,33,(ok_di ? d_low[i] : EMPTY_VALUE));
      PutIPCValue(values,row,34,(ok_di ? d_up[i] : EMPTY_VALUE));
      PutIPCValue(values,row,35,(ok_di ? d_basis[i] : EMPTY_VALUE));
      // STEP1: PRICE 추세 올존용 레짐 원본값. 기존 0~35 컬럼 순서는 유지합니다.
      PutIPCValue(values,row,36,(ok_price ? p_regime_basis[i] : EMPTY_VALUE));
      PutIPCValue(values,row,37,(ok_price ? p_regime_up[i] : EMPTY_VALUE));
      PutIPCValue(values,row,38,(ok_price ? p_regime_dn[i] : EMPTY_VALUE));
      // STEP2: RSI 추세 올존용 레짐 상/하단. basis는 기존 RSI_basis(col 27)를 그대로 사용합니다.
      PutIPCValue(values,row,39,(ok_rsi ? r_regime_up[i] : EMPTY_VALUE));
      PutIPCValue(values,row,40,(ok_rsi ? r_regime_dn[i] : EMPTY_VALUE));
      // STEP3: STO 추세 올존용 레짐 상/하단. basis는 기존 STO_basis(col 31)를 그대로 사용합니다.
      PutIPCValue(values,row,41,(ok_sto ? s_regime_up[i] : EMPTY_VALUE));
      PutIPCValue(values,row,42,(ok_sto ? s_regime_dn[i] : EMPTY_VALUE));
      // STEP4: DI 추세 올존용 레짐 상/하단. basis는 기존 DI_basis(col 35)를 그대로 사용합니다.
      PutIPCValue(values,row,43,(ok_di ? d_regime_up[i] : EMPTY_VALUE));
      PutIPCValue(values,row,44,(ok_di ? d_regime_dn[i] : EMPTY_VALUE));
   }

   if(!WritePipeSnapshot(f,times,volumes,values,payload_count))
   {
      if(!f.warned)
         PrintFormat("[THE STAFF OF MOSES] ⚠️ Python THE STAFF OF MOSES Named Pipe 미연결/전송 실패: %s %s",f.symbol,f.tf_text);
      f.warned=true;
      return false;
   }

   f.warned=false;
   return true;
}

//+------------------------------------------------------------------+
int OnInit()
{
   string syms[], tfs[];
   int ns = StringSplit(InpSymbols, ',', syms);
   int nt = StringSplit(STAFF_TIMEFRAMES, ',', tfs);
   if(ns<=0 || nt<=0)
   {
      Print("[THE STAFF OF MOSES] InpSymbols 설정을 확인하십시오.");
      return INIT_PARAMETERS_INCORRECT;
   }

   ArrayResize(g_feeds,0);
   int rejected_feeds=0;

   for(int i=0; i<ns; ++i)
   {
      string sym=Trim(syms[i]);
      if(StringLen(sym)==0) continue;

      for(int j=0; j<nt; ++j)
      {
         string t=Trim(tfs[j]);
         if(StringLen(t)==0) continue;
         if(!IsSupportedTF(t))
         {
            PrintFormat("[THE STAFF OF MOSES] 지원하지 않는 TF: %s", t);
            continue;
         }

         FeedContext f;
         f.ema20=f.ema21=f.ema50=f.ema200=f.price=f.rsi=f.sto=f.di=INVALID_HANDLE;
         if(BuildFeed(sym,t,f))
         {
            int n=ArraySize(g_feeds);
            ArrayResize(g_feeds,n+1);
            g_feeds[n]=f;
         }
         else
         {
            rejected_feeds++;
            PrintFormat("[THE STAFF OF MOSES] ❌ feed 생성 제외: %s %s", sym, t);
         }
      }
   }

   if(ArraySize(g_feeds)==0)
   {
      Print("[THE STAFF OF MOSES] ❌ 생성된 feed가 없습니다. 심볼/TF/필수 지표 설치 상태를 확인하십시오.");
      return INIT_FAILED;
   }

   if(rejected_feeds > 0)
      PrintFormat("[THE STAFF OF MOSES] ⚠️ 생성 실패 feed=%d / 정상 feed=%d", rejected_feeds, ArraySize(g_feeds));

   int ms=MathMax(250,STAFF_TIMER_MS);
   if(!EventSetMillisecondTimer(ms))
   {
      PrintFormat("[THE STAFF OF MOSES] Millisecond timer 실패 err=%d",GetLastError());
      return INIT_FAILED;
   }

   PrintFormat("[THE STAFF OF MOSES] ✅ 활성화 완료 - feeds=%d, interval=%dms, Named Pipe=%s",
               ArraySize(g_feeds),ms,STAFF_PIPE_NAME);
   PrintFormat("[THE STAFF OF MOSES] 대상 심볼: %s", InpSymbols);
   PrintFormat("[THE STAFF OF MOSES] 대상 TF: %s", STAFF_TIMEFRAMES);
   Print("[THE STAFF OF MOSES] 필수 지표: EMA20/21/50/200 + 원비 기본 OPEN 4/3.00 (Pipe v1 호환 1.79/2.79/3.00/4.00 유지) + PRICE_of_Moses + RSI_of_Moses + STO_of_Moses + DI_of_Moses");


   // 시작 직후 한 번 즉시 full snapshot publish
   OnTimer();
   return INIT_SUCCEEDED;
}

void OnDeinit(const int reason)
{
   EventKillTimer();
   for(int i=0;i<ArraySize(g_feeds);++i) ReleaseFeed(g_feeds[i]);
   CloseStaffPipe();
   StaffPublishIdentityStatus(STAFF_PIPE_NAME,false,false,g_snapshot_seq);
   Print("[THE STAFF OF MOSES] 종료");
}

void OnTick()
{
   // 의도적으로 비워둡니다. 다른 종목에 tick이 없어도 OnTimer로 전체 TF를 갱신합니다.
}

void OnTimer()
{
   for(int i=0; i<ArraySize(g_feeds); ++i)
      PublishFeed(g_feeds[i]);
   StaffPublishIdentityStatus(STAFF_PIPE_NAME,true,g_pipe!=INVALID_HANDLE,g_snapshot_seq);
}
//+------------------------------------------------------------------+
