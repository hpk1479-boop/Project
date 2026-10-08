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
#property version   "1.70"
#property strict

enum ENUM_STAFF_MODE
{
   STAFF_MODE_LIVE = 0,
   STAFF_MODE_BACKTEST = 1
};

input group "=== Mode ==="
input ENUM_STAFF_MODE InpMode = STAFF_MODE_LIVE;
// Migration/verification switch. Default is Wire v2 / MSP3; v1 remains replayable.
input int InpWireVersion = 2;
// Tester only. These switches never affect the LIVE publisher.
enum ENUM_STAFF_RECORDING { STAFF_RECORD_TIMER=0, STAFF_RECORD_BAR=1 };
input ENUM_STAFF_RECORDING InpRecordingMode = STAFF_RECORD_TIMER;
input bool InpNativeExport = true;
input bool InpPipeRecording = true;
input string InpPipeName = "\\\\.\\pipe\\StaffOfMoses_v1";
#include "STAFF_Wire_V2.mqh"

input group "=== Targets ==="
input string InpSymbols = "XAUUSD+,NAS100";  // comma-separated logical symbols
// Tester only: logical symbol written to Wire/native export. Empty = tester symbol (_Symbol).
input string InpTesterLogicalSymbol = "";

// ------------------------------------------------------------------
// FIXED STAFF CONFIGURATION
// 사용자가 수정할 필요가 없는 항목은 코드 내부에 고정합니다.
// ------------------------------------------------------------------
const string STAFF_TIMEFRAMES        = "1m,2m,3m,4m,5m,6m,10m,12m,15m,20m,30m,1h,2h,3h,4h,6h,8h,12h,1D";
const int    STAFF_BARS_TO_EXPORT    = 650;
input int    STAFF_TIMER_MS          = 1000; // TF observation interval; unchanged default
const int    STAFF_DISPATCH_MS       = 100;
const int    STAFF_WORK_BUDGET_MS    = 100;
const int    STAFF_WARM_BARS         = 32;
const int    STAFF_VALUE_COLUMNS     = STAFF_WIRE_VALUE_COLUMNS;
const int    STAFF_LEGACY_COLUMNS    = 45;
#define STAFF_PIPE_NAME InpPipeName
const int    STAFF_PIPE_MAGIC        = 0x534D4F53; // "SMOS"
const int    STAFF_PIPE_VERSION      = 1;

// 원비(WONBI): 전략 공용 OPEN Bollinger 기본 정의.
// EA computes authoritative Wonbi with fixed 3.0 sigma.
// Existing 1.79/2.79/3.00/4.00 slots retain their original v1 meanings.
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

// BACKTEST input-only buffers appended to the existing indicators.
// LIVE pipe column map remains unchanged.
const int    STAFF_PRICE_LOWER_OUT_BUF    = 13;
const int    STAFF_PRICE_UPPER_OUT_BUF    = 14;
const int    STAFF_PRICE_REGIME_SLOPE_BUF = 15;

const string STAFF_RSI_INDICATOR     = "RSI_of_Moses";
const int    STAFF_RSI_VALUE_BUF     = 2;
const int    STAFF_RSI_LOWER_BUF     = 0;
const int    STAFF_RSI_UPPER_BUF     = 1;
const int    STAFF_RSI_BASIS_BUF     = 5;
const int    STAFF_RSI_REGIME_UP_BUF = 7;
const int    STAFF_RSI_REGIME_DN_BUF = 8;
const int    STAFF_RSI_LOWER_OUT_BUF = 9;
const int    STAFF_RSI_UPPER_OUT_BUF = 10;
const int    STAFF_RSI_REGIME_SLOPE_BUF = 11;

const string STAFF_STO_INDICATOR     = "STO_of_Moses";
const int    STAFF_STO_VALUE_BUF     = 2;
const int    STAFF_STO_LOWER_BUF     = 0;
const int    STAFF_STO_UPPER_BUF     = 1;
const int    STAFF_STO_BASIS_BUF     = 5;
const int    STAFF_STO_REGIME_UP_BUF = 7;
const int    STAFF_STO_REGIME_DN_BUF = 8;
const int    STAFF_STO_LOWER_OUT_BUF = 9;
const int    STAFF_STO_UPPER_OUT_BUF = 10;
const int    STAFF_STO_REGIME_SLOPE_BUF = 11;

const string STAFF_DI_INDICATOR      = "DI_of_Moses";
const int    STAFF_DI_VALUE_BUF      = 2;
const int    STAFF_DI_LOWER_BUF      = 0;
const int    STAFF_DI_UPPER_BUF      = 1;
const int    STAFF_DI_BASIS_BUF      = 5;
const int    STAFF_DI_REGIME_UP_BUF  = 7;
const int    STAFF_DI_REGIME_DN_BUF  = 8;
const int    STAFF_DI_LOWER_OUT_BUF  = 9;
const int    STAFF_DI_UPPER_OUT_BUF  = 10;
const int    STAFF_DI_REGIME_SLOPE_BUF = 11;

struct FeedContext
{
   string          symbol;         // logical symbol: Wire / Python identity
   string          broker_symbol;  // MT5 symbol used for market data
   string          tf_text;
   ENUM_TIMEFRAMES tf;
   int             ema20;
   int             ema50;
   int             ema200;
   int             price;
   int             rsi;
   int             sto;
   int             di;
   bool            warned;
   bool            published;
   ulong           next_poll_ms;
   ulong           last_slow_log_ms;
   long            wire_seq;
   long            wire_bar;
   bool            wire_needs_full;
   int             wire_flags;
   int             wire_validity;
   uchar           wire_previous[];
   long            cached_times[],cached_volumes[];
   double          cached_values[];
   long            cached_series_count;
   long            cached_first_date;
   int             cached_calculated[8];
};

FeedContext g_feeds[];
int g_feed_cursor=0;
ulong g_last_progress_ms=0;
uchar g_pending_wire[],g_pending_full[];
int g_pending_feeds[];
long g_pending_observed=0;
string g_pending_symbol="";
long g_wire_frames=0,g_wire_bytes=0,g_wire_full=0,g_wire_row=0,g_wire_heartbeat=0;

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

void StaffLegacyValues(const double &values[],const int rows,double &legacy[])
{
   ArrayResize(legacy,rows*STAFF_LEGACY_COLUMNS);
   for(int row=0;row<rows;++row)
      ArrayCopy(legacy,values,row*STAFF_LEGACY_COLUMNS,row*STAFF_VALUE_COLUMNS,STAFF_LEGACY_COLUMNS);
}

void CalcOpenBands4(const double &src[], int count,
                    double &mid[],
                    double &wonbi_upper[], double &wonbi_lower[])
{
   ArrayResize(mid,count);      ArrayInitialize(mid,EMPTY_VALUE);

   ArrayResize(wonbi_upper,count); ArrayInitialize(wonbi_upper,EMPTY_VALUE);
   ArrayResize(wonbi_lower,count); ArrayInitialize(wonbi_lower,EMPTY_VALUE);
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
      // 3.00 슬롯 = 원비 기본값(길이 4 / 표준편차 3 / OPEN).
      wonbi_upper[i]=m+WONBI_DEFAULT_SIGMA*sd; wonbi_lower[i]=m-WONBI_DEFAULT_SIGMA*sd;
   }
}

bool CopyOneBuffer(int handle, int buffer_num, int count, double &out[])
{
   ArrayResize(out, count);
   ArrayInitialize(out, EMPTY_VALUE);

   if(handle == INVALID_HANDLE)
      return false;

   // Do not synchronously wait for a cold indicator in the shared EA thread.
   if(BarsCalculated(handle) < count)
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

#include "STAFF_Symbol_Map.mqh"

string StaffTesterLogicalSymbol()
{
   string logical=Trim(InpTesterLogicalSymbol);
   return StringLen(logical)>0 ? logical : _Symbol;
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
   string broker = ResolveBrokerSymbol(symbol);
   if(StringLen(broker)==0) return false;
   for(int k=0;k<ArraySize(g_feeds);++k)
      if(g_feeds[k].broker_symbol==broker && g_feeds[k].symbol!=symbol)
      {
         PrintFormat("[SYMBOL MAP CONFLICT] %s 와 %s 가 같은 브로커 심볼 %s 로 연결됨 - %s 제외",g_feeds[k].symbol,symbol,broker,symbol);
         return false;
      }
   if(!SymbolSelect(broker, true))
   {
      PrintFormat("[THE STAFF OF MOSES] SymbolSelect 실패: %s (%s)", symbol, broker);
      return false;
   }

   f.symbol  = symbol;
   f.broker_symbol = broker;
   f.tf_text = tf_text;
   StringToLower(f.tf_text);
   f.tf      = tf;
   f.warned  = false;
   f.published = false;
   f.next_poll_ms = 0;
   f.last_slow_log_ms = 0;
   f.wire_seq=0;f.wire_bar=-1;f.wire_needs_full=true;f.wire_flags=0;f.wire_validity=0;
   ArrayResize(f.wire_previous,0);
   ArrayResize(f.cached_times,0);ArrayResize(f.cached_volumes,0);ArrayResize(f.cached_values,0);
   f.cached_series_count=0;f.cached_first_date=0;

   // EMA는 모두 CLOSE 기준입니다.
   f.ema20  = iMA(broker, tf, 20, 0, MODE_EMA, PRICE_CLOSE);
   f.ema50  = iMA(broker, tf, 50, 0, MODE_EMA, PRICE_CLOSE);
   f.ema200 = iMA(broker, tf, 200, 0, MODE_EMA, PRICE_CLOSE);

   f.price = CreateCustom(STAFF_PRICE_INDICATOR, broker, tf);
   f.rsi   = CreateCustom(STAFF_RSI_INDICATOR,   broker, tf);
   f.sto   = CreateCustom(STAFF_STO_INDICATOR,   broker, tf);
   f.di    = CreateCustom(STAFF_DI_INDICATOR,    broker, tf);

   if(f.ema20==INVALID_HANDLE || f.ema50==INVALID_HANDLE || f.ema200==INVALID_HANDLE)
   {
      PrintFormat("[THE STAFF OF MOSES] ⚠️ EMA handle 결손 (OHLC 공급 유지): %s %s", symbol, tf_text);
   }

   if(f.price==INVALID_HANDLE || f.rsi==INVALID_HANDLE || f.sto==INVALID_HANDLE || f.di==INVALID_HANDLE)
   {
      PrintFormat(
         "[THE STAFF OF MOSES] ⚠️ 커스텀 지표 handle 결손 (OHLC 공급 유지): %s %s | PRICE=%s RSI=%s STO=%s DI=%s",
         symbol, tf_text,
         (f.price==INVALID_HANDLE ? "FAIL" : "OK"),
         (f.rsi==INVALID_HANDLE   ? "FAIL" : "OK"),
         (f.sto==INVALID_HANDLE   ? "FAIL" : "OK"),
         (f.di==INVALID_HANDLE    ? "FAIL" : "OK")
      );
   }

   return true;
}

void ReleaseFeed(FeedContext &f)
{
   ReleaseHandle(f.ema20);
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
   {
      int err=GetLastError();
      static ulong last_log=0;
      ulong now=GetTickCount64();
      if(last_log==0 || now-last_log>=10000)
      {
         PrintFormat("[THE STAFF OF MOSES] Named Pipe open 실패: %s err=%d", STAFF_PIPE_NAME, err);
         last_log=now;
      }
      return false;
   }

   if(InpWireVersion==2)
   {
      if(!StaffHello(g_pipe)) { CloseStaffPipe(); return false; }
      // STAFF resets every feed on reconnect. Each feed must send its own
      // first FULL, even when it is due in a later symbol/TF bundle.
      for(int i=0;i<ArraySize(g_feeds);++i)
      {
         g_feeds[i].wire_needs_full=true;
         ArrayResize(g_feeds[i].wire_previous,0);
      }
      PrintFormat("[STAFF Wire v2] HELLO schema=%08X build=%s",STAFF_WIRE_SCHEMA_ID,STAFF_EA_BUILD_HASH);
   }
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
   FileWriteInteger(g_pipe, STAFF_LEGACY_COLUMNS, INT_VALUE);

   uint w_sym = FileWriteArray(g_pipe, sym_bytes, 0, sym_len);
   uint w_tf  = FileWriteArray(g_pipe, tf_bytes,  0, tf_len);
   uint w_t   = FileWriteArray(g_pipe, times,     0, bar_count);
   uint w_v   = FileWriteArray(g_pipe, volumes,   0, bar_count);
   double legacy[]; StaffLegacyValues(values,bar_count,legacy);
   uint w_x   = FileWriteArray(g_pipe, legacy, 0, bar_count*STAFF_LEGACY_COLUMNS);
   FileFlush(g_pipe);

   int err=GetLastError();
   if(w_sym!=(uint)sym_len || w_tf!=(uint)tf_len ||
      w_t!=(uint)bar_count || w_v!=(uint)bar_count ||
      w_x!=(uint)(bar_count*STAFF_LEGACY_COLUMNS) || err!=0)
   {
      PrintFormat("[THE STAFF OF MOSES] ⚠️ Named Pipe write 실패: %s %s err=%d", f.symbol, f.tf_text, err);
      CloseStaffPipe();
      return false;
   }
   return true;
}

// Retry every pending observation before generating another; never coalesce.
bool FlushStaffBundle()
{
   if(ArraySize(g_pending_wire)==0) return true;
   if(!EnsureStaffPipe()) return false;
   uchar send[];
   bool needs_full=false;
   for(int k=0;k<ArraySize(g_pending_feeds);++k)
      if(g_feeds[g_pending_feeds[k]].wire_needs_full) {needs_full=true;break;}
   if(needs_full)
   {
      // Pending observations are immutable until acknowledged: rebuild their
      // FULL representation only on reconnect, never discard/coalesce them.
      uchar body[];
      for(int k=0;k<ArraySize(g_pending_feeds);++k)
      {
         int i=g_pending_feeds[k];uchar full[];
         StaffFeedFrame(g_feeds[i].symbol,g_feeds[i].tf_text,g_feeds[i].wire_seq,STAFF_WIRE_FULL,
            g_feeds[i].cached_times,g_feeds[i].cached_volumes,g_feeds[i].cached_values,
            ArraySize(g_feeds[i].cached_times),full);
         StaffBundleAdd(body,full);
      }
      StaffBundleFrame(g_pending_symbol,g_pending_observed,ArraySize(g_pending_feeds),body,send);
   }
   else ArrayCopy(send,g_pending_wire);
   ResetLastError();uint n=FileWriteArray(g_pipe,send);FileFlush(g_pipe);
   if(n!=(uint)ArraySize(send) || GetLastError()!=0) { CloseStaffPipe();return false; }
   g_wire_bytes+=ArraySize(send);g_wire_frames++;
   for(int k=0;k<ArraySize(g_pending_feeds);++k)
      g_feeds[g_pending_feeds[k]].wire_needs_full=false;
   ArrayResize(g_pending_wire,0);ArrayResize(g_pending_full,0);
   return true;
}

bool StaffHistoryStable(FeedContext &f)
{
   // A family unavailable at the last complete window does not force a FULL by itself:
   // its readiness change does (StaffWireValidity / family flags).
   if(ArraySize(f.cached_times)==0) return false;
   if(SeriesInfoInteger(f.broker_symbol,f.tf,SERIES_BARS_COUNT)!=f.cached_series_count ||
      SeriesInfoInteger(f.broker_symbol,f.tf,SERIES_FIRSTDATE)!=f.cached_first_date) return false;
   int handles[]={f.ema20,f.ema50,f.ema200,f.price,f.rsi,f.sto,f.di};
   for(int i=0;i<7;++i) if(BarsCalculated(handles[i])!=f.cached_calculated[i]) return false;
   return true;
}

void StaffRememberHistory(FeedContext &f)
{
   f.cached_series_count=SeriesInfoInteger(f.broker_symbol,f.tf,SERIES_BARS_COUNT);
   f.cached_first_date=SeriesInfoInteger(f.broker_symbol,f.tf,SERIES_FIRSTDATE);
   int handles[]={f.ema20,f.ema50,f.ema200,f.price,f.rsi,f.sto,f.di};
   for(int i=0;i<7;++i) f.cached_calculated[i]=BarsCalculated(handles[i]);
}

bool StaffLastRow(FeedContext &f,const MqlRates &current,double &v[])
{
   int rows=ArraySize(f.cached_times);
   ArrayCopy(v,f.cached_values,0,(rows-1)*STAFF_VALUE_COLUMNS,STAFF_VALUE_COLUMNS);
   v[0]=IPCValue(current.open);v[1]=IPCValue(current.high);
   v[2]=IPCValue(current.low);v[3]=IPCValue(current.close);
   // OPEN HMA, OPEN bands and Wonbi are unchanged inside this bar.
   double x=EMPTY_VALUE;
   bool e=true,p=true,r=true,s=true,d=true;
   e = PipeCaptureLastValue(f.ema20,0,x) && e; v[STAFF_COL_EMA_20]=IPCValue(x);
   e = PipeCaptureLastValue(f.ema50,0,x) && e; v[STAFF_COL_EMA_50]=IPCValue(x);
   e = PipeCaptureLastValue(f.ema200,0,x) && e; v[STAFF_COL_EMA_200]=IPCValue(x);
   p = PipeCaptureLastValue(f.price,STAFF_PRICE_HMA6_BUF,x) && p; v[STAFF_COL_PRICE_HMA_6]=IPCValue(x);
   p = PipeCaptureLastValue(f.price,STAFF_PRICE_LOWER_BUF,x) && p; v[STAFF_COL_PRICE_BAND_LOWER]=IPCValue(x);
   p = PipeCaptureLastValue(f.price,STAFF_PRICE_UPPER_BUF,x) && p; v[STAFF_COL_PRICE_BAND_UPPER]=IPCValue(x);
   r = PipeCaptureLastValue(f.rsi,STAFF_RSI_VALUE_BUF,x) && r; v[STAFF_COL_RSI_VAL]=IPCValue(x);
   r = PipeCaptureLastValue(f.rsi,STAFF_RSI_LOWER_BUF,x) && r; v[STAFF_COL_RSI_DB]=IPCValue(x);
   r = PipeCaptureLastValue(f.rsi,STAFF_RSI_UPPER_BUF,x) && r; v[STAFF_COL_RSI_UB]=IPCValue(x);
   r = PipeCaptureLastValue(f.rsi,STAFF_RSI_BASIS_BUF,x) && r; v[STAFF_COL_RSI_BASIS]=IPCValue(x);
   s = PipeCaptureLastValue(f.sto,STAFF_STO_VALUE_BUF,x) && s; v[STAFF_COL_STO_VAL]=IPCValue(x);
   s = PipeCaptureLastValue(f.sto,STAFF_STO_LOWER_BUF,x) && s; v[STAFF_COL_STO_DB]=IPCValue(x);
   s = PipeCaptureLastValue(f.sto,STAFF_STO_UPPER_BUF,x) && s; v[STAFF_COL_STO_UB]=IPCValue(x);
   s = PipeCaptureLastValue(f.sto,STAFF_STO_BASIS_BUF,x) && s; v[STAFF_COL_STO_BASIS]=IPCValue(x);
   d = PipeCaptureLastValue(f.di,STAFF_DI_VALUE_BUF,x) && d; v[STAFF_COL_DI_VAL]=IPCValue(x);
   d = PipeCaptureLastValue(f.di,STAFF_DI_LOWER_BUF,x) && d; v[STAFF_COL_DI_DB]=IPCValue(x);
   d = PipeCaptureLastValue(f.di,STAFF_DI_UPPER_BUF,x) && d; v[STAFF_COL_DI_UB]=IPCValue(x);
   d = PipeCaptureLastValue(f.di,STAFF_DI_BASIS_BUF,x) && d; v[STAFF_COL_DI_BASIS]=IPCValue(x);
   p = PipeCaptureLastValue(f.price,STAFF_PRICE_REGIME_BASIS_BUF,x) && p; v[STAFF_COL_PRICE_REGIME_BASIS]=IPCValue(x);
   p = PipeCaptureLastValue(f.price,STAFF_PRICE_REGIME_UP_BUF,x) && p; v[STAFF_COL_PRICE_REGIME_UPPER]=IPCValue(x);
   p = PipeCaptureLastValue(f.price,STAFF_PRICE_REGIME_DN_BUF,x) && p; v[STAFF_COL_PRICE_REGIME_LOWER]=IPCValue(x);
   r = PipeCaptureLastValue(f.rsi,STAFF_RSI_REGIME_UP_BUF,x) && r; v[STAFF_COL_RSI_REGIME_UPPER]=IPCValue(x);
   r = PipeCaptureLastValue(f.rsi,STAFF_RSI_REGIME_DN_BUF,x) && r; v[STAFF_COL_RSI_REGIME_LOWER]=IPCValue(x);
   s = PipeCaptureLastValue(f.sto,STAFF_STO_REGIME_UP_BUF,x) && s; v[STAFF_COL_STO_REGIME_UPPER]=IPCValue(x);
   s = PipeCaptureLastValue(f.sto,STAFF_STO_REGIME_DN_BUF,x) && s; v[STAFF_COL_STO_REGIME_LOWER]=IPCValue(x);
   d = PipeCaptureLastValue(f.di,STAFF_DI_REGIME_UP_BUF,x) && d; v[STAFF_COL_DI_REGIME_UPPER]=IPCValue(x);
   d = PipeCaptureLastValue(f.di,STAFF_DI_REGIME_DN_BUF,x) && d; v[STAFF_COL_DI_REGIME_LOWER]=IPCValue(x);
   p = PipeCaptureLastValue(f.price,STAFF_PRICE_LOWER_OUT_BUF,x) && p; v[STAFF_COL_PRICE_LOWER_OUT]=IPCValue(x);
   p = PipeCaptureLastValue(f.price,STAFF_PRICE_UPPER_OUT_BUF,x) && p; v[STAFF_COL_PRICE_UPPER_OUT]=IPCValue(x);
   p = PipeCaptureLastValue(f.price,STAFF_PRICE_REGIME_SLOPE_BUF,x) && p; v[STAFF_COL_PRICE_REGIME_SLOPE]=IPCValue(x);
   r = PipeCaptureLastValue(f.rsi,STAFF_RSI_LOWER_OUT_BUF,x) && r; v[STAFF_COL_RSI_LOWER_OUT]=IPCValue(x);
   r = PipeCaptureLastValue(f.rsi,STAFF_RSI_UPPER_OUT_BUF,x) && r; v[STAFF_COL_RSI_UPPER_OUT]=IPCValue(x);
   r = PipeCaptureLastValue(f.rsi,STAFF_RSI_REGIME_SLOPE_BUF,x) && r; v[STAFF_COL_RSI_REGIME_SLOPE]=IPCValue(x);
   s = PipeCaptureLastValue(f.sto,STAFF_STO_LOWER_OUT_BUF,x) && s; v[STAFF_COL_STO_LOWER_OUT]=IPCValue(x);
   s = PipeCaptureLastValue(f.sto,STAFF_STO_UPPER_OUT_BUF,x) && s; v[STAFF_COL_STO_UPPER_OUT]=IPCValue(x);
   s = PipeCaptureLastValue(f.sto,STAFF_STO_REGIME_SLOPE_BUF,x) && s; v[STAFF_COL_STO_REGIME_SLOPE]=IPCValue(x);
   d = PipeCaptureLastValue(f.di,STAFF_DI_LOWER_OUT_BUF,x) && d; v[STAFF_COL_DI_LOWER_OUT]=IPCValue(x);
   d = PipeCaptureLastValue(f.di,STAFF_DI_UPPER_OUT_BUF,x) && d; v[STAFF_COL_DI_UPPER_OUT]=IPCValue(x);
   d = PipeCaptureLastValue(f.di,STAFF_DI_REGIME_SLOPE_BUF,x) && d; v[STAFF_COL_DI_REGIME_SLOPE]=IPCValue(x);

   // A family unavailable at the last complete window stays unavailable here; every
   // family that was available must still be read (otherwise a FULL is sent).
   int got=(e?1:0)|(p?2:0)|(r?4:0)|(s?8:0)|(d?16:0);
   return (got & f.wire_flags)==f.wire_flags;
}

// A new bar is sent as APPEND only when STAFF then holds exactly the window a FULL would
// carry: one new bar, the same earlier bars (time, OHLC, volume) and the same readiness.
// Every hour boundary still sends a FULL, which also refreshes far-past EA-computed cells.
bool StaffCanAppend(FeedContext &f,const MqlRates &current,const long &pt[],const long &pv[],const double &px[],
                    const int count,const int flags,const int validity)
{
   int old=ArraySize(f.cached_times);
   if(old<2 || count<3 || f.wire_bar<0) return false;
   if(((long)current.time)%3600==0) return false;
   if(flags!=f.wire_flags || validity!=f.wire_validity) return false;
   if(count!=MathMin(old+1,STAFF_BARS_TO_EXPORT)) return false;
   if(SeriesInfoInteger(f.broker_symbol,f.tf,SERIES_FIRSTDATE)!=f.cached_first_date) return false;
   if(pt[count-1]!=(long)current.time || pt[count-2]!=f.wire_bar || pt[count-1]<=pt[count-2]) return false;
   int shift=old+1-count;   // 1 when a full window slides, 0 while a short history still grows
   for(int i=0;i<count-1;++i) if(pt[i]!=f.cached_times[i+shift]) return false;
   StaffWireNumber a,b;
   for(int i=0;i<count-2;++i)
   {
      if(pv[i]!=f.cached_volumes[i+shift]) return false;
      for(int c=STAFF_COL_OPEN;c<=STAFF_COL_CLOSE;++c)
      {
         a.real=px[i*STAFF_VALUE_COLUMNS+c];b.real=f.cached_values[(i+shift)*STAFF_VALUE_COLUMNS+c];
         if(a.bits!=b.bits) return false;
      }
   }
   return true;
}

// One observation of a feed: HEARTBEAT, ROW (forming bar), APPEND (new bar) or FULL; 0 when
// there is nothing to send. The cache then holds the window STAFF holds. LIVE and the Tester
// capture both use this rule, so a recording carries what LIVE sends.
int StaffObserveFeed(FeedContext &f,const MqlRates &current,long &t[],long &vol[],double &x[],int &rows)
{
   int last=ArraySize(f.cached_times)-1;
   if(last>=0 && f.wire_bar==(long)current.time && StaffHistoryStable(f))
   {
      double row[];
      if(StaffLastRow(f,current,row) && StaffWireValidity(row,1)==f.wire_validity)
      {
         int base=last*STAFF_VALUE_COLUMNS;
         bool unchanged=f.cached_volumes[last]==(long)current.tick_volume;
         StaffWireNumber a,b;
         for(int c=0;c<STAFF_VALUE_COLUMNS && unchanged;++c)
         {a.real=row[c];b.real=f.cached_values[base+c];if(a.bits!=b.bits) unchanged=false;}
         if(unchanged)
         {
            rows=0;ArrayResize(t,0);ArrayResize(vol,0);ArrayResize(x,0);
            g_wire_heartbeat++;return STAFF_WIRE_HEARTBEAT;
         }
         rows=1;ArrayResize(t,1);ArrayResize(vol,1);ArrayResize(x,STAFF_VALUE_COLUMNS);
         t[0]=f.wire_bar;vol[0]=(long)current.tick_volume;ArrayCopy(x,row,0,0,STAFF_VALUE_COLUMNS);
         f.cached_volumes[last]=vol[0];ArrayCopy(f.cached_values,row,base,0,STAFF_VALUE_COLUMNS);
         g_wire_row++;return STAFF_WIRE_ROW;
      }
   }
   long pt[],pv[];double px[];int count=0,flags=0;
   if(!BuildStaffPayload(f,pt,pv,px,count,flags)) return 0;
   int validity=StaffWireValidity(px,count);
   bool append=StaffCanAppend(f,current,pt,pv,px,count,flags,validity);
   ArrayResize(f.cached_times,count);ArrayResize(f.cached_volumes,count);ArrayResize(f.cached_values,count*STAFF_VALUE_COLUMNS);
   ArrayCopy(f.cached_times,pt,0,0,count);ArrayCopy(f.cached_volumes,pv,0,0,count);
   ArrayCopy(f.cached_values,px,0,0,count*STAFF_VALUE_COLUMNS);
   f.wire_bar=pt[count-1];f.wire_flags=flags;f.wire_validity=validity;
   StaffRememberHistory(f);
   if(append)
   {
      rows=2;ArrayResize(t,2);ArrayResize(vol,2);ArrayResize(x,2*STAFF_VALUE_COLUMNS);
      for(int k=0;k<2;++k) {t[k]=pt[count-2+k];vol[k]=pv[count-2+k];}
      ArrayCopy(x,px,0,(count-2)*STAFF_VALUE_COLUMNS,2*STAFF_VALUE_COLUMNS);
      return STAFF_WIRE_APPEND;
   }
   rows=count;ArrayResize(t,count);ArrayResize(vol,count);ArrayResize(x,count*STAFF_VALUE_COLUMNS);
   ArrayCopy(t,pt,0,0,count);ArrayCopy(vol,pv,0,0,count);ArrayCopy(x,px,0,0,count*STAFF_VALUE_COLUMNS);
   g_wire_full++;return STAFF_WIRE_FULL;
}

bool BuildWireChange(FeedContext &f,uchar &frame[],uchar &full[])
{
   MqlRates current[1];
   if(CopyRates(f.broker_symbol,f.tf,0,1,current)!=1) return false;
   long t[],vol[];double x[];int rows=0;
   int kind=StaffObserveFeed(f,current[0],t,vol,x,rows);
   if(kind<=0) return false;
   long seq=f.wire_seq+1;
   StaffFeedFrame(f.symbol,f.tf_text,seq,kind,t,vol,x,rows,frame);
   f.wire_seq=seq;g_snapshot_seq++;return true;
}

void PublishWireBundles()
{
   if(!FlushStaffBundle() || !EnsureStaffPipe()) return;
   ulong now=GetTickCount64();long observed=(long)TimeGMT()*1000;
   // A symbol's due TFs are observed and delivered in one indivisible batch.
   for(int first=0;first<ArraySize(g_feeds) && !IsStopped();++first)
   {
      bool seen=false;
      for(int j=0;j<first;++j) if(g_feeds[j].symbol==g_feeds[first].symbol) {seen=true;break;}
      if(seen) continue;
      uchar body[];int count=0;ArrayResize(g_pending_feeds,0);
      for(int i=first;i<ArraySize(g_feeds);++i)
      {
         if(g_feeds[i].symbol!=g_feeds[first].symbol || now<g_feeds[i].next_poll_ms) continue;
         g_feeds[i].next_poll_ms=now+(ulong)STAFF_TIMER_MS;
         uchar frame[],full[];
         if(!BuildWireChange(g_feeds[i],frame,full)) continue;
         StaffBundleAdd(body,frame);
         ArrayResize(g_pending_feeds,count+1);g_pending_feeds[count]=i;count++;
         g_feeds[i].published=true;
      }
      if(count==0) continue;
      StaffBundleFrame(g_feeds[first].symbol,observed,count,body,g_pending_wire);
      g_pending_symbol=g_feeds[first].symbol;g_pending_observed=observed;
      if(!FlushStaffBundle()) return;
   }
}

// LIVE Named Pipe와 BACKTEST STAFF pipe capture가 같은 45열 payload를 쓰도록
// payload 작성부만 분리했습니다. 값·컬럼 순서·결손(EMPTY_VALUE) 처리는 기존 PublishFeed와 같습니다.
// family_flags: 1=EMA 2=PRICE 4=RSI 8=STO 16=DI 복사 성공.
bool BuildStaffPayload(FeedContext &f,long &times[],long &volumes[],double &values[],int &payload_count,int &family_flags)
{
   const int want = STAFF_BARS_TO_EXPORT;
   int count = want + STAFF_WARM_BARS;
   // Avoid waiting for 682 bars when enough usable history is already local.
   int available=(int)SeriesInfoInteger(f.broker_symbol,f.tf,SERIES_BARS_COUNT);
   if(available>=250) count=MathMin(count,available);

   MqlRates rates[];
   ArrayResize(rates, count);
   int got = CopyRates(f.broker_symbol, f.tf, 0, count, rates);
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

   double open_band_4_mid[], wonbi_upper[], wonbi_lower[];
   CalcOpenBands4(open_src, count, open_band_4_mid, wonbi_upper, wonbi_lower);

   double ema20[], ema50[], ema200[];
   double p_upper[], p_lower[], p_hma6[], p_regime_basis[], p_regime_up[], p_regime_dn[];
   double r_val[], r_low[], r_up[], r_basis[], r_regime_up[], r_regime_dn[];
   double s_val[], s_low[], s_up[], s_basis[], s_regime_up[], s_regime_dn[];
   double d_val[], d_low[], d_up[], d_basis[], d_regime_up[], d_regime_dn[];

   double p_lower_out[], p_upper_out[], p_regime_slope[], r_lower_out[], r_upper_out[], r_regime_slope[], s_lower_out[], s_upper_out[], s_regime_slope[], d_lower_out[], d_upper_out[], d_regime_slope[];
   CopyOneBuffer(f.price,STAFF_PRICE_LOWER_OUT_BUF,count,p_lower_out);
   CopyOneBuffer(f.price,STAFF_PRICE_UPPER_OUT_BUF,count,p_upper_out);
   CopyOneBuffer(f.price,STAFF_PRICE_REGIME_SLOPE_BUF,count,p_regime_slope);
   CopyOneBuffer(f.rsi,STAFF_RSI_LOWER_OUT_BUF,count,r_lower_out);
   CopyOneBuffer(f.rsi,STAFF_RSI_UPPER_OUT_BUF,count,r_upper_out);
   CopyOneBuffer(f.rsi,STAFF_RSI_REGIME_SLOPE_BUF,count,r_regime_slope);
   CopyOneBuffer(f.sto,STAFF_STO_LOWER_OUT_BUF,count,s_lower_out);
   CopyOneBuffer(f.sto,STAFF_STO_UPPER_OUT_BUF,count,s_upper_out);
   CopyOneBuffer(f.sto,STAFF_STO_REGIME_SLOPE_BUF,count,s_regime_slope);
   CopyOneBuffer(f.di,STAFF_DI_LOWER_OUT_BUF,count,d_lower_out);
   CopyOneBuffer(f.di,STAFF_DI_UPPER_OUT_BUF,count,d_upper_out);
   CopyOneBuffer(f.di,STAFF_DI_REGIME_SLOPE_BUF,count,d_regime_slope);

   bool ok_ema = CopyOneBuffer(f.ema20,0,count,ema20)
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
         PrintFormat("[THE STAFF OF MOSES] ⚠️ buffer 결손 (EMPTY_VALUE 전달, OHLC 공급 유지): %s %s | EMA=%s PRICE=%s RSI=%s STO=%s DI=%s err=%d",
                     f.symbol,f.tf_text,(ok_ema?"OK":"FAIL"),(ok_price?"OK":"FAIL"),
                     (ok_rsi?"OK":"FAIL"),(ok_sto?"OK":"FAIL"),(ok_di?"OK":"FAIL"),GetLastError());
      f.warned=true;
   }

   payload_count=MathMin(want,count);
   int first=MathMax(0,count-payload_count);
   ArrayResize(times,payload_count);
   ArrayResize(volumes,payload_count);
   ArrayResize(values,payload_count*STAFF_VALUE_COLUMNS);

   for(int row=0; row<payload_count; ++row)
   {
      int i=first+row;
      times[row]=(long)rates[i].time;
      volumes[row]=(long)rates[i].tick_volume;
      PutIPCValue(values,row, STAFF_COL_OPEN,rates[i].open);
      PutIPCValue(values,row, STAFF_COL_HIGH,rates[i].high);
      PutIPCValue(values,row, STAFF_COL_LOW,rates[i].low);
      PutIPCValue(values,row, STAFF_COL_CLOSE,rates[i].close);
      PutIPCValue(values,row, STAFF_COL_EMA_20,(ok_ema ? ema20[i] : EMPTY_VALUE));
      PutIPCValue(values,row, STAFF_COL_EMA_50,(ok_ema ? ema50[i] : EMPTY_VALUE));
      PutIPCValue(values,row, STAFF_COL_EMA_200,(ok_ema ? ema200[i] : EMPTY_VALUE));
      PutIPCValue(values,row, STAFF_COL_HMA_6,hma6_open[i]);
      PutIPCValue(values,row, STAFF_COL_HMA_17,hma17_open[i]);
      PutIPCValue(values,row,STAFF_COL_HMA_50,hma50_open[i]);
      PutIPCValue(values,row,STAFF_COL_HMA_168,hma168_open[i]);
      PutIPCValue(values,row,STAFF_COL_OPEN_BAND_4_MID,open_band_4_mid[i]);
      PutIPCValue(values,row,STAFF_COL_PRICE_HMA_6,(ok_price ? p_hma6[i] : EMPTY_VALUE));
      PutIPCValue(values,row,STAFF_COL_PRICE_BAND_LOWER,(ok_price ? p_lower[i] : EMPTY_VALUE));
      PutIPCValue(values,row,STAFF_COL_PRICE_BAND_UPPER,(ok_price ? p_upper[i] : EMPTY_VALUE));
      PutIPCValue(values,row,STAFF_COL_RSI_VAL,(ok_rsi ? r_val[i] : EMPTY_VALUE));
      PutIPCValue(values,row,STAFF_COL_RSI_DB,(ok_rsi ? r_low[i] : EMPTY_VALUE));
      PutIPCValue(values,row,STAFF_COL_RSI_UB,(ok_rsi ? r_up[i] : EMPTY_VALUE));
      PutIPCValue(values,row,STAFF_COL_RSI_BASIS,(ok_rsi ? r_basis[i] : EMPTY_VALUE));
      PutIPCValue(values,row,STAFF_COL_STO_VAL,(ok_sto ? s_val[i] : EMPTY_VALUE));
      PutIPCValue(values,row,STAFF_COL_STO_DB,(ok_sto ? s_low[i] : EMPTY_VALUE));
      PutIPCValue(values,row,STAFF_COL_STO_UB,(ok_sto ? s_up[i] : EMPTY_VALUE));
      PutIPCValue(values,row,STAFF_COL_STO_BASIS,(ok_sto ? s_basis[i] : EMPTY_VALUE));
      PutIPCValue(values,row,STAFF_COL_DI_VAL,(ok_di ? d_val[i] : EMPTY_VALUE));
      PutIPCValue(values,row,STAFF_COL_DI_DB,(ok_di ? d_low[i] : EMPTY_VALUE));
      PutIPCValue(values,row,STAFF_COL_DI_UB,(ok_di ? d_up[i] : EMPTY_VALUE));
      PutIPCValue(values,row,STAFF_COL_DI_BASIS,(ok_di ? d_basis[i] : EMPTY_VALUE));
      // STEP1: PRICE 추세 올존용 레짐 원본값. 기존 0~35 컬럼 순서는 유지합니다.
      PutIPCValue(values,row,STAFF_COL_PRICE_REGIME_BASIS,(ok_price ? p_regime_basis[i] : EMPTY_VALUE));
      PutIPCValue(values,row,STAFF_COL_PRICE_REGIME_UPPER,(ok_price ? p_regime_up[i] : EMPTY_VALUE));
      PutIPCValue(values,row,STAFF_COL_PRICE_REGIME_LOWER,(ok_price ? p_regime_dn[i] : EMPTY_VALUE));
      // STEP2: RSI 추세 올존용 레짐 상/하단. basis는 기존 RSI_basis(col 27)를 그대로 사용합니다.
      PutIPCValue(values,row,STAFF_COL_RSI_REGIME_UPPER,(ok_rsi ? r_regime_up[i] : EMPTY_VALUE));
      PutIPCValue(values,row,STAFF_COL_RSI_REGIME_LOWER,(ok_rsi ? r_regime_dn[i] : EMPTY_VALUE));
      // STEP3: STO 추세 올존용 레짐 상/하단. basis는 기존 STO_basis(col 31)를 그대로 사용합니다.
      PutIPCValue(values,row,STAFF_COL_STO_REGIME_UPPER,(ok_sto ? s_regime_up[i] : EMPTY_VALUE));
      PutIPCValue(values,row,STAFF_COL_STO_REGIME_LOWER,(ok_sto ? s_regime_dn[i] : EMPTY_VALUE));
      // STEP4: DI 추세 올존용 레짐 상/하단. basis는 기존 DI_basis(col 35)를 그대로 사용합니다.
      PutIPCValue(values,row,STAFF_COL_DI_REGIME_UPPER,(ok_di ? d_regime_up[i] : EMPTY_VALUE));
      PutIPCValue(values,row,STAFF_COL_DI_REGIME_LOWER,(ok_di ? d_regime_dn[i] : EMPTY_VALUE));
      PutIPCValue(values,row,STAFF_COL_WONBI_UPPER,wonbi_upper[i]);
      PutIPCValue(values,row,STAFF_COL_WONBI_LOWER,wonbi_lower[i]);
      PutIPCValue(values,row,STAFF_COL_PRICE_LOWER_OUT,p_lower_out[i]);
      PutIPCValue(values,row,STAFF_COL_PRICE_UPPER_OUT,p_upper_out[i]);
      PutIPCValue(values,row,STAFF_COL_PRICE_REGIME_SLOPE,p_regime_slope[i]);
      PutIPCValue(values,row,STAFF_COL_RSI_LOWER_OUT,r_lower_out[i]);
      PutIPCValue(values,row,STAFF_COL_RSI_UPPER_OUT,r_upper_out[i]);
      PutIPCValue(values,row,STAFF_COL_RSI_REGIME_SLOPE,r_regime_slope[i]);
      PutIPCValue(values,row,STAFF_COL_STO_LOWER_OUT,s_lower_out[i]);
      PutIPCValue(values,row,STAFF_COL_STO_UPPER_OUT,s_upper_out[i]);
      PutIPCValue(values,row,STAFF_COL_STO_REGIME_SLOPE,s_regime_slope[i]);
      PutIPCValue(values,row,STAFF_COL_DI_LOWER_OUT,d_lower_out[i]);
      PutIPCValue(values,row,STAFF_COL_DI_UPPER_OUT,d_upper_out[i]);
      PutIPCValue(values,row,STAFF_COL_DI_REGIME_SLOPE,d_regime_slope[i]);
   }

   family_flags=(ok_ema?1:0)|(ok_price?2:0)|(ok_rsi?4:0)|(ok_sto?8:0)|(ok_di?16:0);
   return true;
}

bool PublishFeed(FeedContext &f)
{
   long times[], volumes[];
   double values[];
   int payload_count=0;
   int family_flags=0;
   if(!BuildStaffPayload(f,times,volumes,values,payload_count,family_flags))
      return false;

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
// BACKTEST INPUT SNAPSHOT
// - LIVE PublishFeed / Named Pipe 경로와 분리된 메모리 입력입니다.
// - 이 단계에서는 전략을 계산하지 않고 입력값만 준비합니다.
// ------------------------------------------------------------------
struct BacktestInputSnapshot
{
   datetime observed_time;
   long observed_time_msc;
   double wonbi_upper;
   double wonbi_lower;
   double wonbi_sigma;
   datetime bar_time;
   string   symbol;
   string   tf_text;
   double   open;
   double   high;
   double   low;
   double   close;
   long     tick_volume;

   double hma6_open;
   double hma17_open;

   double price_value;
   double price_lower;
   double price_upper;
   double price_basis;
   double price_regime_up;
   double price_regime_dn;
   double price_lower_out;
   double price_upper_out;
   double price_regime_slope;

   double rsi_value;
   double rsi_lower;
   double rsi_upper;
   double rsi_basis;
   double rsi_regime_up;
   double rsi_regime_dn;
   double rsi_lower_out;
   double rsi_upper_out;
   double rsi_regime_slope;

   double sto_value;
   double sto_lower;
   double sto_upper;
   double sto_basis;
   double sto_regime_up;
   double sto_regime_dn;
   double sto_lower_out;
   double sto_upper_out;
   double sto_regime_slope;

   double di_value;
   double di_lower;
   double di_upper;
   double di_basis;
   double di_regime_up;
   double di_regime_dn;
   double di_lower_out;
   double di_upper_out;
   double di_regime_slope;

   bool price_copy_ok;
   bool rsi_copy_ok;
   bool sto_copy_ok;
   bool di_copy_ok;
   bool ready;
};

BacktestInputSnapshot g_backtest_inputs[];


// ------------------------------------------------------------------
// DATA BUILD native export (BACKTEST only)
// - DataManager opts in by creating one FILE_COMMON request file.
// - Without that request, BACKTEST performs no export I/O.
// - LIVE never calls any function in this block.
// ------------------------------------------------------------------
const string STAFF_NATIVE_REQUEST_FILE = "MosesDataBuild\\native_request.txt";
const string STAFF_NATIVE_REQUEST_MAGIC = "MOSES_NATIVE_BUILD_V1";
const int    STAFF_NATIVE_FILE_MAGIC = 0x4D4E5331; // "MNS1"
const int    STAFF_NATIVE_FILE_VERSION = 2;
const int    STAFF_NATIVE_VALUE_COLUMNS = 45;

bool   g_native_export_enabled=false;
bool   g_capture_session_enabled=false;
bool   g_native_export_failed=false;
string g_native_session="";
string g_native_dir="";
long g_native_start_s=0;
long g_native_end_s=0;
int    g_native_handles[];
long   g_native_rows[];
int    g_native_signature[];
long   g_native_last_bar[];
long   g_native_last_minute[];

// STAFF PIPE CAPTURE 선언부 (구현은 아래 STAFF PIPE CAPTURE 블록)
const int STAFF_PIPE_CAPTURE_MAGIC   = 0x4D535032; // "MSP2"
const int STAFF_PIPE_CAPTURE_VERSION = 1;
int    g_pipe_cap_handles[];
long   g_pipe_cap_records[];

string PipeCaptureFilename(const int index)
{
   return StringFormat("pipe_%03d.bin",index);
}


bool NativeSafeToken(const string token)
{
   int n=StringLen(token);
   if(n<8 || n>64) return false;
   for(int i=0;i<n;++i)
   {
      ushort c=StringGetCharacter(token,i);
      bool ok=(c>=48 && c<=57) || (c>=65 && c<=90) || (c>=97 && c<=122) || c==95 || c==45;
      if(!ok) return false;
   }
   return true;
}

bool NativeReadBuildRequest(string &session,string &symbol)
{
   session=""; symbol="";
   if(!FileIsExist(STAFF_NATIVE_REQUEST_FILE,FILE_COMMON)) return false;
   ResetLastError();
   int h=FileOpen(STAFF_NATIVE_REQUEST_FILE,FILE_READ|FILE_TXT|FILE_ANSI|FILE_COMMON);
   if(h==INVALID_HANDLE)
   {
      PrintFormat("[THE STAFF OF MOSES][DATA BUILD] request open 실패 err=%d",GetLastError());
      return false;
   }
   string magic=FileReadString(h);
   session=FileReadString(h);
   symbol=FileReadString(h);
   if(!FileIsEnding(h)) g_native_start_s=StringToInteger(FileReadString(h));
   if(!FileIsEnding(h)) g_native_end_s=StringToInteger(FileReadString(h));
   FileClose(h);
   magic=Trim(magic); session=Trim(session); symbol=Trim(symbol);
   if(magic!=STAFF_NATIVE_REQUEST_MAGIC || !NativeSafeToken(session) || StringLen(symbol)==0)
   {
      Print("[THE STAFF OF MOSES][DATA BUILD] request 형식 오류");
      return false;
   }
   return true;
}

string NativeFeedFilename(const int index)
{
   return StringFormat("feed_%03d.bin",index);
}

void PipeCaptureClose();
bool PipeCaptureBegin();

void NativeCloseHandles()
{
   PipeCaptureClose();
   for(int i=0;i<ArraySize(g_native_handles);++i)
   {
      if(g_native_handles[i]!=INVALID_HANDLE)
      {
         FileFlush(g_native_handles[i]);
         FileClose(g_native_handles[i]);
         g_native_handles[i]=INVALID_HANDLE;
      }
   }
}

bool NativeBeginExport()
{
   // No request means ordinary MT5 BACKTEST: zero native-export file I/O.
   if(!FileIsExist(STAFF_NATIVE_REQUEST_FILE,FILE_COMMON)) return true;

   string session,requested_symbol;
   if(!NativeReadBuildRequest(session,requested_symbol)) return false;
   if(requested_symbol!=StaffTesterLogicalSymbol())
   {
      PrintFormat("[THE STAFF OF MOSES][DATA BUILD] request symbol 불일치 request=%s logical=%s tester=%s",requested_symbol,StaffTesterLogicalSymbol(),_Symbol);
      return false;
   }

   FolderCreate("MosesDataBuild",FILE_COMMON);
   g_native_session=session;
   g_native_dir="MosesDataBuild\\"+session;
   // Unique session directory; file-open checks below are authoritative.
   FolderCreate(g_native_dir,FILE_COMMON);

   int total=ArraySize(g_feeds);
   ArrayResize(g_native_handles,total);
   ArrayResize(g_native_rows,total);
   ArrayResize(g_native_signature,total);
   ArrayResize(g_native_last_bar,total);
   ArrayResize(g_native_last_minute,total);
   for(int i=0;i<total;++i)
   {
      g_native_handles[i]=INVALID_HANDLE;
      g_native_rows[i]=0;
      g_native_signature[i]=-1;
      g_native_last_bar[i]=-1;
      g_native_last_minute[i]=-1;
      if(!InpNativeExport) continue;
      string path=g_native_dir+"\\"+NativeFeedFilename(i);
      ResetLastError();
      int h=FileOpen(path,FILE_WRITE|FILE_BIN|FILE_COMMON);
      if(h==INVALID_HANDLE)
      {
         PrintFormat("[THE STAFF OF MOSES][DATA BUILD] native file open 실패: %s err=%d",path,GetLastError());
         g_native_export_failed=true;
         NativeCloseHandles();
         return false;
      }
      // Header: <IIII> = magic, version, value_columns, record_bytes.
      FileWriteInteger(h,STAFF_NATIVE_FILE_MAGIC,INT_VALUE);
      FileWriteInteger(h,STAFF_NATIVE_FILE_VERSION,INT_VALUE);
      FileWriteInteger(h,STAFF_NATIVE_VALUE_COLUMNS,INT_VALUE);
      FileWriteInteger(h,16+STAFF_NATIVE_VALUE_COLUMNS*8,INT_VALUE);
      g_native_handles[i]=h;
   }

   // STAFF pipe capture files (LIVE와 같은 45열 payload).
   if(InpPipeRecording && !PipeCaptureBegin())
   {
      g_native_export_failed=true;
      NativeCloseHandles();
      return false;
   }

   // Consume only after all files are ready, so manual BACKTEST without a request stays untouched.
   FileDelete(STAFF_NATIVE_REQUEST_FILE,FILE_COMMON);
   g_capture_session_enabled=true;
   g_native_export_enabled=InpNativeExport;
   g_native_export_failed=false;
   int started=FileOpen(g_native_dir+"\\started.txt",FILE_WRITE|FILE_TXT|FILE_ANSI|FILE_COMMON);
   if(started!=INVALID_HANDLE)
   {
      FileWriteString(started,STAFF_NATIVE_REQUEST_MAGIC+"\r\n"+g_native_session+"\r\n");
      FileFlush(started); FileClose(started);
   }
   PrintFormat("[THE STAFF OF MOSES][DATA BUILD] native export 활성화 session=%s feeds=%d",g_native_session,total);
   return true;
}

int NativeTriState(const double value,const double lower,const double upper)
{
   if(value<lower) return 0; // LOWER
   if(value>upper) return 2; // UPPER
   return 1;                // IN
}

int NativeSignState(const double value)
{
   if(value<0.0) return 0;
   if(value>0.0) return 2;
   return 1;
}

int NativeStateSignature(const BacktestInputSnapshot &s)
{
   int code=NativeSignState(s.hma6_open-s.hma17_open);
   int family=NativeTriState(s.price_value,s.price_lower,s.price_upper)
             +3*NativeTriState(s.price_value,s.price_regime_dn,s.price_regime_up)
             +9*NativeSignState(s.price_regime_slope);
   code=code*27+family;
   family=NativeTriState(s.rsi_value,s.rsi_lower,s.rsi_upper)
         +3*NativeTriState(s.rsi_value,s.rsi_regime_dn,s.rsi_regime_up)
         +9*NativeSignState(s.rsi_regime_slope);
   code=code*27+family;
   family=NativeTriState(s.sto_value,s.sto_lower,s.sto_upper)
         +3*NativeTriState(s.sto_value,s.sto_regime_dn,s.sto_regime_up)
         +9*NativeSignState(s.sto_regime_slope);
   code=code*27+family;
   family=NativeTriState(s.di_value,s.di_lower,s.di_upper)
         +3*NativeTriState(s.di_value,s.di_regime_dn,s.di_regime_up)
         +9*NativeSignState(s.di_regime_slope);
   return code*27+family;
}

bool NativeShouldWrite(const int index,const BacktestInputSnapshot &s)
{
   if(index<0 || index>=ArraySize(g_native_signature)) return false;
   int signature=NativeStateSignature(s);
   long minute=(long)s.observed_time/60;
   bool first=(g_native_signature[index]<0);
   bool state_changed=first || signature!=g_native_signature[index];
   bool bar_changed=first || (long)s.bar_time!=g_native_last_bar[index];
   bool minute_checkpoint=first || minute!=g_native_last_minute[index];
   if(!(state_changed || bar_changed || minute_checkpoint)) return false;
   g_native_signature[index]=signature;
   g_native_last_bar[index]=(long)s.bar_time;
   g_native_last_minute[index]=minute;
   return true;
}

bool NativeWriteSnapshot(const int index,const BacktestInputSnapshot &s)
{
   if(!g_native_export_enabled) return true;
   if(g_native_export_failed) return false;
   if(index<0 || index>=ArraySize(g_native_handles) || g_native_handles[index]==INVALID_HANDLE) return false;
   if(!s.ready) return true;
   if(g_native_end_s>0 && ((long)s.observed_time<g_native_start_s || (long)s.observed_time>=g_native_end_s)) return true;
   // Sparse exact-native policy: at least one full numeric checkpoint per minute,
   // plus every strategy-observable Percentile/Regime/HMA state transition and new bar.
   // Python consumes exact recorded numbers. Preserve every OnTick snapshot
   // (BacktestOnTick already limits calculation to one simulated second).

   double v[];
   ArrayResize(v,STAFF_NATIVE_VALUE_COLUMNS);
   int k=0;
   v[k++]=s.open; v[k++]=s.high; v[k++]=s.low; v[k++]=s.close;
   v[k++]=s.hma6_open; v[k++]=s.hma17_open;

   v[k++]=s.price_value; v[k++]=s.price_lower; v[k++]=s.price_upper; v[k++]=s.price_basis;
   v[k++]=s.price_regime_up; v[k++]=s.price_regime_dn; v[k++]=s.price_lower_out; v[k++]=s.price_upper_out; v[k++]=s.price_regime_slope;

   v[k++]=s.rsi_value; v[k++]=s.rsi_lower; v[k++]=s.rsi_upper; v[k++]=s.rsi_basis;
   v[k++]=s.rsi_regime_up; v[k++]=s.rsi_regime_dn; v[k++]=s.rsi_lower_out; v[k++]=s.rsi_upper_out; v[k++]=s.rsi_regime_slope;

   v[k++]=s.sto_value; v[k++]=s.sto_lower; v[k++]=s.sto_upper; v[k++]=s.sto_basis;
   v[k++]=s.sto_regime_up; v[k++]=s.sto_regime_dn; v[k++]=s.sto_lower_out; v[k++]=s.sto_upper_out; v[k++]=s.sto_regime_slope;

   v[k++]=s.di_value; v[k++]=s.di_lower; v[k++]=s.di_upper; v[k++]=s.di_basis;
   v[k++]=s.di_regime_up; v[k++]=s.di_regime_dn; v[k++]=s.di_lower_out; v[k++]=s.di_upper_out; v[k++]=s.di_regime_slope;

   v[k++]=s.wonbi_upper; v[k++]=s.wonbi_lower; v[k++]=s.wonbi_sigma;

   if(k!=STAFF_NATIVE_VALUE_COLUMNS)
   {
      g_native_export_failed=true;
      return false;
   }

   int h=g_native_handles[index];
   ResetLastError();
   FileWriteLong(h,s.observed_time_msc);
   FileWriteLong(h,(long)s.bar_time);
   uint wrote=FileWriteArray(h,v,0,STAFF_NATIVE_VALUE_COLUMNS);
   int err=GetLastError();
   if(wrote!=(uint)STAFF_NATIVE_VALUE_COLUMNS || err!=0)
   {
      PrintFormat("[THE STAFF OF MOSES][DATA BUILD] native write 실패 feed=%d err=%d",index,err);
      g_native_export_failed=true;
      return false;
   }
   g_native_rows[index]++;
   if((g_native_rows[index] & 1023)==0) FileFlush(h);
   return true;
}

void NativeEndExport()
{
   if(!g_capture_session_enabled && ArraySize(g_native_handles)==0) return;
   NativeCloseHandles();

   if(g_capture_session_enabled && !g_native_export_failed)
   {
      string manifest_path=g_native_dir+"\\manifest.tsv";
      int m=FileOpen(manifest_path,FILE_WRITE|FILE_TXT|FILE_ANSI|FILE_COMMON);
      if(m==INVALID_HANDLE)
      {
         g_native_export_failed=true;
      }
      else
      {
         FileWriteString(m,STAFF_NATIVE_REQUEST_MAGIC+"\r\n");
         FileWriteString(m,"session\t"+g_native_session+"\r\n");
         FileWriteString(m,"symbol\t"+StaffTesterLogicalSymbol()+"\r\n");
         FileWriteString(m,"format_version\t"+IntegerToString(STAFF_NATIVE_FILE_VERSION)+"\r\n");
         FileWriteString(m,"value_columns\t"+IntegerToString(STAFF_NATIVE_VALUE_COLUMNS)+"\r\n");
         FileWriteString(m,"sampling_policy\tTIMER_SNAPSHOT_V2\r\n");
         FileWriteString(m,"observation_unit\tmilliseconds\r\n");
         FileWriteString(m,"observation_interval_ms\t"+IntegerToString(STAFF_TIMER_MS)+"\r\n");
         for(int i=0;InpNativeExport && i<ArraySize(g_feeds);++i)
            FileWriteString(m,"feed\t"+IntegerToString(i)+"\t"+g_feeds[i].tf_text+"\t"+NativeFeedFilename(i)+"\t"+IntegerToString((int)g_native_rows[i])+"\r\n");
         FileWriteString(m,"recording_mode\t"+(InpRecordingMode==STAFF_RECORD_BAR ? "BAR" : "TIMER")+"\r\n");
         FileWriteString(m,"pipe_observation_unit\tmilliseconds\r\n");
         FileWriteString(m,"request_start_s\t"+IntegerToString(g_native_start_s)+"\r\n");
         FileWriteString(m,"request_end_s\t"+IntegerToString(g_native_end_s)+"\r\n");
         FileWriteString(m,"pipe_capture\t"+(InpWireVersion==2 ? "STAFF_PIPE_V2" : "STAFF_PIPE_V1")+"\r\n");
         for(int i=0;i<ArraySize(g_feeds) && i<ArraySize(g_pipe_cap_records);++i)
            FileWriteString(m,"pipe_feed\t"+IntegerToString(i)+"\t"+g_feeds[i].tf_text+"\t"+PipeCaptureFilename(i)+"\t"+IntegerToString((int)g_pipe_cap_records[i])+"\r\n");
         FileFlush(m); FileClose(m);
      }

      if(!g_native_export_failed)
      {
         int c=FileOpen(g_native_dir+"\\complete.txt",FILE_WRITE|FILE_TXT|FILE_ANSI|FILE_COMMON);
         if(c!=INVALID_HANDLE)
         {
            FileWriteString(c,STAFF_NATIVE_REQUEST_MAGIC+"\r\n"+g_native_session+"\r\n");
            FileFlush(c); FileClose(c);
         }
         else g_native_export_failed=true;
      }
   }

   if(g_native_export_failed)
      PrintFormat("[THE STAFF OF MOSES][DATA BUILD] native export 실패 session=%s",g_native_session);
   else if(g_native_export_enabled)
      PrintFormat("[THE STAFF OF MOSES][DATA BUILD] native export 완료 session=%s",g_native_session);

   g_native_export_enabled=false;
   g_capture_session_enabled=false;
   ArrayResize(g_native_handles,0);
   ArrayResize(g_native_rows,0);
   ArrayResize(g_native_signature,0);
   ArrayResize(g_native_last_bar,0);
   ArrayResize(g_native_last_minute,0);
   ArrayResize(g_pipe_cap_handles,0);
   ArrayResize(g_pipe_cap_records,0);
}

// ------------------------------------------------------------------
// STAFF PIPE CAPTURE (BACKTEST data build only)
// - LIVE Named Pipe로 보내는 것과 같은 프레임(StaffObserveFeed)을 관측마다 기록합니다.
// - 최초 / 지표 준비상태 변화 / 이력 변화 / 매 정각: 전체 payload(최대 650봉)를 FULL로 기록
// - 새 봉: 직전 봉 마감값과 새 봉 2행을 APPEND로 기록(STAFF가 FULL과 같은 창을 만듭니다)
// - 같은 봉 안: 진행봉 1행만 ROW로 기록, 바뀐 값이 없으면 HEARTBEAT
//   (OPEN 기반 HMA6/17/50/168·원비밴드는 봉 안에서 변하지 않으므로 마지막 행 값을 유지)
// - Python 백테스트는 이 기록으로 LIVE와 같은 STAFF pipe 메시지를 다시 만들어
//   Part1 THE STAFF OF MOSES 파서에 그대로 넣습니다.
// Record: <i kind><q observed_time><i rows><i family_flags> times[rows]<q> volumes[rows]<q> values[rows*45]<d>
// ------------------------------------------------------------------
void PipeCaptureClose()
{
   for(int i=0;i<ArraySize(g_pipe_cap_handles);++i)
   {
      if(g_pipe_cap_handles[i]!=INVALID_HANDLE)
      {
         FileFlush(g_pipe_cap_handles[i]);
         FileClose(g_pipe_cap_handles[i]);
         g_pipe_cap_handles[i]=INVALID_HANDLE;
      }
   }
}

bool PipeCaptureBegin()
{
   int total=ArraySize(g_feeds);
   ArrayResize(g_pipe_cap_handles,total);
   ArrayResize(g_pipe_cap_records,total);
   for(int i=0;i<total;++i)
   {
      g_pipe_cap_handles[i]=INVALID_HANDLE;
      g_pipe_cap_records[i]=0;
   }
   for(int i=0;i<total;++i)
   {
      string path=g_native_dir+"\\"+PipeCaptureFilename(i);
      ResetLastError();
      int h=FileOpen(path,FILE_WRITE|FILE_BIN|FILE_COMMON);
      if(h==INVALID_HANDLE)
      {
         PrintFormat("[THE STAFF OF MOSES][DATA BUILD] pipe capture open 실패: %s err=%d",path,GetLastError());
         PipeCaptureClose();
         return false;
      }
      // Header: <IIII> = magic, version, value_columns, max_bars.
      FileWriteInteger(h,InpWireVersion==2 ? 0x4D535033 : STAFF_PIPE_CAPTURE_MAGIC,INT_VALUE);
      FileWriteInteger(h,InpWireVersion==2 ? 2 : STAFF_PIPE_CAPTURE_VERSION,INT_VALUE);
      FileWriteInteger(h,InpWireVersion==2 ? STAFF_VALUE_COLUMNS : STAFF_LEGACY_COLUMNS,INT_VALUE);
      FileWriteInteger(h,STAFF_BARS_TO_EXPORT,INT_VALUE);
      g_pipe_cap_handles[i]=h;
   }
   return true;
}

bool PipeCaptureWriteRecord(const int index,const int kind,const long observed,
                            const long &times[],const long &volumes[],const double &values[],
                            const int rows,const int flags)
{
   int h=g_pipe_cap_handles[index];
   ResetLastError();
   if(InpWireVersion==2)
   {
      // The frame kind and the feed cache come from StaffObserveFeed, the LIVE rule.
      uchar frame[];
      StaffFeedFrame(g_feeds[index].symbol,g_feeds[index].tf_text,g_pipe_cap_records[index]+1,
                     kind,times,volumes,values,rows,frame);
      FileWriteLong(h,observed);FileWriteInteger(h,flags,INT_VALUE);
      FileWriteInteger(h,ArraySize(frame),INT_VALUE);
      uint n=FileWriteArray(h,frame);
      if(n!=(uint)ArraySize(frame) || GetLastError()!=0) {g_native_export_failed=true;return false;}
      g_pipe_cap_records[index]++;
      if((g_pipe_cap_records[index]&255)==0) FileFlush(h);
      return true;
   }
   FileWriteInteger(h,kind,INT_VALUE);
   FileWriteLong(h,observed);
   FileWriteInteger(h,rows,INT_VALUE);
   FileWriteInteger(h,flags,INT_VALUE);
   uint wt=FileWriteArray(h,times,0,rows);
   uint wv=FileWriteArray(h,volumes,0,rows);
   double legacy[]; StaffLegacyValues(values,rows,legacy);
   uint wx=FileWriteArray(h,legacy,0,rows*STAFF_LEGACY_COLUMNS);
   int err=GetLastError();
   if(wt!=(uint)rows || wv!=(uint)rows || wx!=(uint)(rows*STAFF_LEGACY_COLUMNS) || err!=0)
   {
      PrintFormat("[THE STAFF OF MOSES][DATA BUILD] pipe capture write 실패 feed=%d err=%d",index,err);
      g_native_export_failed=true;
      return false;
   }
   g_pipe_cap_records[index]++;
   if((g_pipe_cap_records[index] & 255)==0) FileFlush(h);
   return true;
}

bool PipeCaptureLastValue(const int handle,const int buffer,double &value)
{
   value=EMPTY_VALUE;
   if(handle==INVALID_HANDLE) return false;
   double one[1];
   if(CopyBuffer(handle,buffer,0,1,one)!=1) return false;
   value=one[0];
   return true;
}

bool PipeCaptureSecond(const int index,FeedContext &f,const long observed)
{
   if(!g_capture_session_enabled || g_native_export_failed) return false;
   if(index<0 || index>=ArraySize(g_pipe_cap_handles) || g_pipe_cap_handles[index]==INVALID_HANDLE) return false;

   MqlRates current[1];
   if(CopyRates(f.broker_symbol,f.tf,0,1,current)!=1) return false;

   // LIVE의 송신 규칙(StaffObserveFeed)을 그대로 씁니다. 녹화는 LIVE가 보냈을 프레임과 같습니다.
   // 이력이 부족하면 LIVE도 송신하지 않으므로 기록하지 않습니다.
   long t[],vol[];double v[];int rows=0;
   int kind=StaffObserveFeed(f,current[0],t,vol,v,rows);
   if(kind<=0) return false;
   return PipeCaptureWriteRecord(index,kind,observed,t,vol,v,rows,f.wire_flags);
}

void ResetBacktestInput(BacktestInputSnapshot &s)
{
   s.observed_time=0;
   s.observed_time_msc=0;
   s.wonbi_upper=s.wonbi_lower=EMPTY_VALUE;
   s.wonbi_sigma=WONBI_DEFAULT_SIGMA;
   s.bar_time=0;
   s.symbol="";
   s.tf_text="";
   s.open=s.high=s.low=s.close=EMPTY_VALUE;
   s.tick_volume=0;
   s.hma6_open=s.hma17_open=EMPTY_VALUE;

   s.price_value=s.price_lower=s.price_upper=s.price_basis=EMPTY_VALUE;
   s.price_regime_up=s.price_regime_dn=EMPTY_VALUE;
   s.price_lower_out=s.price_upper_out=s.price_regime_slope=EMPTY_VALUE;

   s.rsi_value=s.rsi_lower=s.rsi_upper=s.rsi_basis=EMPTY_VALUE;
   s.rsi_regime_up=s.rsi_regime_dn=EMPTY_VALUE;
   s.rsi_lower_out=s.rsi_upper_out=s.rsi_regime_slope=EMPTY_VALUE;

   s.sto_value=s.sto_lower=s.sto_upper=s.sto_basis=EMPTY_VALUE;
   s.sto_regime_up=s.sto_regime_dn=EMPTY_VALUE;
   s.sto_lower_out=s.sto_upper_out=s.sto_regime_slope=EMPTY_VALUE;

   s.di_value=s.di_lower=s.di_upper=s.di_basis=EMPTY_VALUE;
   s.di_regime_up=s.di_regime_dn=EMPTY_VALUE;
   s.di_lower_out=s.di_upper_out=s.di_regime_slope=EMPTY_VALUE;

   s.price_copy_ok=false;
   s.rsi_copy_ok=false;
   s.sto_copy_ok=false;
   s.di_copy_ok=false;
   s.ready=false;
}

bool CopyBacktestValue(const int handle,const int buffer,double &value)
{
   value=EMPTY_VALUE;
   if(handle==INVALID_HANDLE) return false;
   double one[1];
   ResetLastError();
   if(CopyBuffer(handle,buffer,0,1,one)!=1)
   {
      static int reported=0;
      if(reported<8)
      {
         PrintFormat("[THE STAFF OF MOSES][BACKTEST] CopyBuffer 대기 handle=%d buffer=%d err=%d calculated=%d",handle,buffer,GetLastError(),BarsCalculated(handle));
         reported++;
      }
      return false;
   }
   value=one[0];
   return true;
}

bool BuildBacktestFeed(string symbol,string tf_text,FeedContext &f)
{
   symbol=Trim(symbol);
   tf_text=Trim(tf_text);
   if(StringLen(symbol)==0 || !IsSupportedTF(tf_text)) return false;

   ENUM_TIMEFRAMES tf=ParseTimeframe(tf_text);
   string broker=_Symbol;
   if(!SymbolSelect(broker,true))
   {
      PrintFormat("[THE STAFF OF MOSES][BACKTEST] SymbolSelect 실패: %s (%s)",symbol,broker);
      return false;
   }

   f.symbol=symbol;
   f.broker_symbol=broker;
   f.tf_text=tf_text;
   StringToLower(f.tf_text);
   f.tf=tf;
   f.warned=false;
   f.published=false;
   f.next_poll_ms=0;
   f.last_slow_log_ms=0;
   // The capture uses the LIVE frame rule (StaffObserveFeed): the same initial wire state.
   f.wire_seq=0;f.wire_bar=-1;f.wire_needs_full=true;f.wire_flags=0;f.wire_validity=0;
   ArrayResize(f.wire_previous,0);
   ArrayResize(f.cached_times,0);ArrayResize(f.cached_volumes,0);ArrayResize(f.cached_values,0);
   f.cached_series_count=0;f.cached_first_date=0;

   // Request historical candles for indicator initialization, without replaying
   // their ticks or exporting snapshots before the user's selected interval.
   MqlRates seed_rates[];
   int seed_bars=CopyRates(broker,tf,0,800,seed_rates);
   if(seed_bars<681)
      PrintFormat("[THE STAFF OF MOSES][BACKTEST] 초기 과거 봉 준비: %s %s %d/681 (미준비 값은 unavailable)",symbol,tf_text,seed_bars);
   // STAFF pipe capture가 LIVE와 같은 45열(EMA 포함)을 기록하도록 LIVE와 같은 EMA handle을 만듭니다.
   // EMA는 모두 CLOSE 기준입니다(LIVE BuildFeed와 동일).
   f.ema20  = iMA(broker, tf, 20, 0, MODE_EMA, PRICE_CLOSE);
   f.ema50  = iMA(broker, tf, 50, 0, MODE_EMA, PRICE_CLOSE);
   f.ema200 = iMA(broker, tf, 200, 0, MODE_EMA, PRICE_CLOSE);
   f.price=CreateCustom(STAFF_PRICE_INDICATOR,broker,tf);
   f.rsi=CreateCustom(STAFF_RSI_INDICATOR,broker,tf);
   f.sto=CreateCustom(STAFF_STO_INDICATOR,broker,tf);
   f.di=CreateCustom(STAFF_DI_INDICATOR,broker,tf);

   if(f.price==INVALID_HANDLE || f.rsi==INVALID_HANDLE ||
      f.sto==INVALID_HANDLE || f.di==INVALID_HANDLE)
   {
      PrintFormat("[THE STAFF OF MOSES][BACKTEST] 지표 handle 결손: %s %s | PRICE=%s RSI=%s STO=%s DI=%s",
                  symbol,tf_text,
                  (f.price==INVALID_HANDLE ? "FAIL" : "OK"),
                  (f.rsi==INVALID_HANDLE ? "FAIL" : "OK"),
                  (f.sto==INVALID_HANDLE ? "FAIL" : "OK"),
                  (f.di==INVALID_HANDLE ? "FAIL" : "OK"));
      ReleaseFeed(f);
      return false;
   }
   return true;
}

bool RefreshBacktestInput(const FeedContext &f,BacktestInputSnapshot &s)
{
   s.observed_time=TimeCurrent();
   MqlTick observed_tick;
   s.observed_time_msc=SymbolInfoTick(_Symbol,observed_tick) ? observed_tick.time_msc : (long)s.observed_time*1000;
   s.symbol=f.symbol;
   s.tf_text=f.tf_text;

   // OHLC는 현재 진행봉을 읽습니다. OPEN HMA는 봉이 바뀔 때만 재계산합니다.
   // HMA(OPEN)는 진행봉 동안 값이 바뀌지 않으므로 매초 64봉을 다시 읽지 않습니다.
   MqlRates current[1];
   if(CopyRates(f.broker_symbol,f.tf,0,1,current)!=1)
   {
      static bool rates_reported=false;
      if(!rates_reported) { PrintFormat("[THE STAFF OF MOSES][BACKTEST] CopyRates 대기 %s %s err=%d",f.symbol,f.tf_text,GetLastError()); rates_reported=true; }
      s.ready=false;
      return false;
   }

   bool need_hma = (s.bar_time!=current[0].time ||
                    s.hma6_open==EMPTY_VALUE || !MathIsValidNumber(s.hma6_open) ||
                    s.hma17_open==EMPTY_VALUE || !MathIsValidNumber(s.hma17_open));

   s.bar_time=current[0].time;
   s.open=current[0].open;
   s.high=current[0].high;
   s.low=current[0].low;
   s.close=current[0].close;
   s.tick_volume=(long)current[0].tick_volume;

   if(need_hma)
   {
      const int HMA_INPUT_BARS=64;
      MqlRates rates[];
      ArrayResize(rates,HMA_INPUT_BARS);
      int got=CopyRates(f.broker_symbol,f.tf,0,HMA_INPUT_BARS,rates);
      if(got<32)
      {
         static bool hma_reported=false;
         if(!hma_reported) { PrintFormat("[THE STAFF OF MOSES][BACKTEST] HMA 입력 대기 %s %s bars=%d err=%d",f.symbol,f.tf_text,got,GetLastError()); hma_reported=true; }
         s.hma6_open=EMPTY_VALUE;
         s.hma17_open=EMPTY_VALUE;
         s.ready=false;
         return false;
      }

      int last=got-1;
      double opens[],hma6[],hma17[];
      ArrayResize(opens,got);
      for(int i=0;i<got;++i) opens[i]=rates[i].open;
      CalcHMA(opens,got,6,hma6);
      CalcHMA(opens,got,17,hma17);
      s.hma6_open=hma6[last];
      s.hma17_open=hma17[last];
      double band_mid[],u179[],l179[],u279[],l279[],u300[],l300[],u400[],l400[],wu[],wl[];
      CalcOpenBands4(opens,got,band_mid,wu,wl);
      s.wonbi_sigma=WONBI_DEFAULT_SIGMA;
      s.wonbi_upper=wu[last];
      s.wonbi_lower=wl[last];
   }

   bool p=true;
   p = CopyBacktestValue(f.price,STAFF_PRICE_HMA6_BUF,s.price_value) && p;
   p = CopyBacktestValue(f.price,STAFF_PRICE_LOWER_BUF,s.price_lower) && p;
   p = CopyBacktestValue(f.price,STAFF_PRICE_UPPER_BUF,s.price_upper) && p;
   p = CopyBacktestValue(f.price,STAFF_PRICE_REGIME_BASIS_BUF,s.price_basis) && p;
   p = CopyBacktestValue(f.price,STAFF_PRICE_REGIME_UP_BUF,s.price_regime_up) && p;
   p = CopyBacktestValue(f.price,STAFF_PRICE_REGIME_DN_BUF,s.price_regime_dn) && p;
   p = CopyBacktestValue(f.price,STAFF_PRICE_LOWER_OUT_BUF,s.price_lower_out) && p;
   p = CopyBacktestValue(f.price,STAFF_PRICE_UPPER_OUT_BUF,s.price_upper_out) && p;
   p = CopyBacktestValue(f.price,STAFF_PRICE_REGIME_SLOPE_BUF,s.price_regime_slope) && p;
   s.price_copy_ok=p;

   bool r=true;
   r = CopyBacktestValue(f.rsi,STAFF_RSI_VALUE_BUF,s.rsi_value) && r;
   r = CopyBacktestValue(f.rsi,STAFF_RSI_LOWER_BUF,s.rsi_lower) && r;
   r = CopyBacktestValue(f.rsi,STAFF_RSI_UPPER_BUF,s.rsi_upper) && r;
   r = CopyBacktestValue(f.rsi,STAFF_RSI_BASIS_BUF,s.rsi_basis) && r;
   r = CopyBacktestValue(f.rsi,STAFF_RSI_REGIME_UP_BUF,s.rsi_regime_up) && r;
   r = CopyBacktestValue(f.rsi,STAFF_RSI_REGIME_DN_BUF,s.rsi_regime_dn) && r;
   r = CopyBacktestValue(f.rsi,STAFF_RSI_LOWER_OUT_BUF,s.rsi_lower_out) && r;
   r = CopyBacktestValue(f.rsi,STAFF_RSI_UPPER_OUT_BUF,s.rsi_upper_out) && r;
   r = CopyBacktestValue(f.rsi,STAFF_RSI_REGIME_SLOPE_BUF,s.rsi_regime_slope) && r;
   s.rsi_copy_ok=r;

   bool st=true;
   st = CopyBacktestValue(f.sto,STAFF_STO_VALUE_BUF,s.sto_value) && st;
   st = CopyBacktestValue(f.sto,STAFF_STO_LOWER_BUF,s.sto_lower) && st;
   st = CopyBacktestValue(f.sto,STAFF_STO_UPPER_BUF,s.sto_upper) && st;
   st = CopyBacktestValue(f.sto,STAFF_STO_BASIS_BUF,s.sto_basis) && st;
   st = CopyBacktestValue(f.sto,STAFF_STO_REGIME_UP_BUF,s.sto_regime_up) && st;
   st = CopyBacktestValue(f.sto,STAFF_STO_REGIME_DN_BUF,s.sto_regime_dn) && st;
   st = CopyBacktestValue(f.sto,STAFF_STO_LOWER_OUT_BUF,s.sto_lower_out) && st;
   st = CopyBacktestValue(f.sto,STAFF_STO_UPPER_OUT_BUF,s.sto_upper_out) && st;
   st = CopyBacktestValue(f.sto,STAFF_STO_REGIME_SLOPE_BUF,s.sto_regime_slope) && st;
   s.sto_copy_ok=st;

   bool d=true;
   d = CopyBacktestValue(f.di,STAFF_DI_VALUE_BUF,s.di_value) && d;
   d = CopyBacktestValue(f.di,STAFF_DI_LOWER_BUF,s.di_lower) && d;
   d = CopyBacktestValue(f.di,STAFF_DI_UPPER_BUF,s.di_upper) && d;
   d = CopyBacktestValue(f.di,STAFF_DI_BASIS_BUF,s.di_basis) && d;
   d = CopyBacktestValue(f.di,STAFF_DI_REGIME_UP_BUF,s.di_regime_up) && d;
   d = CopyBacktestValue(f.di,STAFF_DI_REGIME_DN_BUF,s.di_regime_dn) && d;
   d = CopyBacktestValue(f.di,STAFF_DI_LOWER_OUT_BUF,s.di_lower_out) && d;
   d = CopyBacktestValue(f.di,STAFF_DI_UPPER_OUT_BUF,s.di_upper_out) && d;
   d = CopyBacktestValue(f.di,STAFF_DI_REGIME_SLOPE_BUF,s.di_regime_slope) && d;
   s.di_copy_ok=d;

   s.ready = p && r && st && d &&
             s.hma6_open!=EMPTY_VALUE && MathIsValidNumber(s.hma6_open) &&
             s.hma17_open!=EMPTY_VALUE && MathIsValidNumber(s.hma17_open);
   return s.ready;
}

bool GetBacktestInput(const int feed_index,BacktestInputSnapshot &out)
{
   if(feed_index<0 || feed_index>=ArraySize(g_backtest_inputs)) return false;
   out=g_backtest_inputs[feed_index];
   return out.ready;
}

//+------------------------------------------------------------------+
// BACKTEST MODE
// - LIVE의 timer / identity-status / Named Pipe 흐름과 완전히 분리합니다.
// - Strategy Tester의 새 simulated second에서 입력 snapshot만 갱신합니다.
// - 전략 로직은 다음 단계에서 이 snapshot을 그대로 사용합니다.
// ------------------------------------------------------------------
int BacktestOnInit()
{
   if(!MQLInfoInteger(MQL_TESTER))
   {
      Print("[THE STAFF OF MOSES][BACKTEST] Strategy Tester에서만 사용할 수 있습니다.");
      return INIT_FAILED;
   }

   string syms[], tfs[];
   // Strategy Tester owns one main symbol per pass. BACKTEST never fans out to LIVE InpSymbols.
   // Feed identity is the logical symbol; market data comes from _Symbol.
   int ns = StringSplit(StaffTesterLogicalSymbol(), ',', syms);
   int nt = StringSplit(STAFF_TIMEFRAMES, ',', tfs);
   if(ns<=0 || nt<=0)
   {
      Print("[THE STAFF OF MOSES][BACKTEST] InpSymbols 설정을 확인하십시오.");
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
            PrintFormat("[THE STAFF OF MOSES][BACKTEST] 지원하지 않는 TF: %s", t);
            continue;
         }

         FeedContext f;
         f.ema20=f.ema50=f.ema200=f.price=f.rsi=f.sto=f.di=INVALID_HANDLE;
         if(BuildBacktestFeed(sym,t,f))
         {
            int n=ArraySize(g_feeds);
            ArrayResize(g_feeds,n+1);
            g_feeds[n]=f;
         }
         else
         {
            rejected_feeds++;
            PrintFormat("[THE STAFF OF MOSES][BACKTEST] feed 생성 제외: %s %s", sym, t);
         }
      }
   }

   if(ArraySize(g_feeds)==0)
   {
      Print("[THE STAFF OF MOSES][BACKTEST] 생성된 feed가 없습니다.");
      return INIT_FAILED;
   }

   ArrayResize(g_backtest_inputs,ArraySize(g_feeds));
   for(int i=0;i<ArraySize(g_backtest_inputs);++i) ResetBacktestInput(g_backtest_inputs[i]);

   if(!NativeBeginExport())
   {
      for(int i=0;i<ArraySize(g_feeds);++i) ReleaseFeed(g_feeds[i]);
      ArrayResize(g_backtest_inputs,0);
      return INIT_FAILED;
   }

   PrintFormat("[THE STAFF OF MOSES][BACKTEST] 입력부 활성화 - feeds=%d rejected=%d",ArraySize(g_feeds),rejected_feeds);
   return INIT_SUCCEEDED;
}

void BacktestOnDeinit()
{
   NativeEndExport();
   for(int i=0;i<ArraySize(g_feeds);++i) ReleaseFeed(g_feeds[i]);
   ArrayResize(g_backtest_inputs,0);
   Print("[THE STAFF OF MOSES][BACKTEST] 종료");
}

void BacktestOnTick()
{
   static long last_bucket=-1;
   static datetime last_recorded_bar=0;
   datetime now=TimeCurrent();
   MqlTick tick;
   long observed_msc=SymbolInfoTick(_Symbol,tick) ? tick.time_msc : (long)now*1000;
   // Identical clock grid for TIMER and BAR; BAR is a subset of TIMER observations.
   long bucket=observed_msc/MathMax(1,STAFF_TIMER_MS);
   if(now<=0 || bucket==last_bucket) return;
   last_bucket=bucket;
   if(g_capture_session_enabled && g_native_end_s>0)
   {
      if((long)now<g_native_start_s) return;
      if((long)now>=g_native_end_s) { ExpertRemove(); return; }
   }
   datetime bar=iTime(_Symbol,PERIOD_M1,0);
   bool record=g_capture_session_enabled && InpPipeRecording &&
               (InpRecordingMode==STAFF_RECORD_TIMER || (bar>0 && bar!=last_recorded_bar));
   if(record) last_recorded_bar=bar;
   for(int i=0;i<ArraySize(g_feeds) && !IsStopped();++i)
   {
      if(g_native_export_enabled && RefreshBacktestInput(g_feeds[i],g_backtest_inputs[i]))
         NativeWriteSnapshot(i,g_backtest_inputs[i]);
      if(record) PipeCaptureSecond(i,g_feeds[i],observed_msc);
   }
}

//+------------------------------------------------------------------+
bool StaffBacktestRuntime()
{
   // Strategy Tester is always the BACKTEST runtime; LIVE behavior is never entered there.
   return (InpMode==STAFF_MODE_BACKTEST || MQLInfoInteger(MQL_TESTER));
}

//+------------------------------------------------------------------+
int OnInit()
{
   if(InpWireVersion!=2) return INIT_PARAMETERS_INCORRECT; // finalized schema requires Wire v2
   if(InpWireVersion!=1 && InpWireVersion!=2) return INIT_PARAMETERS_INCORRECT;
   if(StaffBacktestRuntime())
      return BacktestOnInit();

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
         f.ema20=f.ema50=f.ema200=f.price=f.rsi=f.sto=f.di=INVALID_HANDLE;
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

   int ms=STAFF_DISPATCH_MS;
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


   // Return from initialization before polling asynchronously calculated indicators.
   Print("[THE STAFF OF MOSES] READINESS_V2: 준비된 지표만 복사, 순환 분할 송신 시작");
   return INIT_SUCCEEDED;
}

void OnDeinit(const int reason)
{
   if(StaffBacktestRuntime())
   {
      BacktestOnDeinit();
      return;
   }

   EventKillTimer();
   for(int i=0;i<ArraySize(g_feeds);++i) ReleaseFeed(g_feeds[i]);
   PrintFormat("[STAFF Wire] version=%d bundles=%I64d bytes=%I64d FULL=%I64d ROW=%I64d HEARTBEAT=%I64d",
               InpWireVersion,g_wire_frames,g_wire_bytes,g_wire_full,g_wire_row,g_wire_heartbeat);
   CloseStaffPipe();
   StaffPublishIdentityStatus(STAFF_PIPE_NAME,false,false,g_snapshot_seq);
   Print("[THE STAFF OF MOSES] 종료");
}

void OnTick()
{
   if(StaffBacktestRuntime())
   {
      BacktestOnTick();
      return;
   }

   // 의도적으로 비워둡니다. 다른 종목에 tick이 없어도 OnTimer로 전체 TF를 갱신합니다.
}

void OnTimer()
{
   if(StaffBacktestRuntime()) return;
   if(InpWireVersion==2)
   {
      PublishWireBundles();
      StaffPublishIdentityStatus(STAFF_PIPE_NAME,true,g_pipe!=INVALID_HANDLE,g_snapshot_seq);
      return;
   }

   int total=ArraySize(g_feeds);
   if(total==0) return;
   ulong started=GetTickCount64();
   for(int visited=0; visited<total && !IsStopped(); ++visited)
   {
      int index=g_feed_cursor;
      g_feed_cursor=(g_feed_cursor+1)%total;
      ulong now=GetTickCount64();
      if(now < g_feeds[index].next_poll_ms) continue;
      g_feeds[index].next_poll_ms=now+(ulong)STAFF_TIMER_MS;
      if(PublishFeed(g_feeds[index])) g_feeds[index].published=true;
      ulong elapsed=GetTickCount64()-now;
      if(elapsed>=1000 && (g_feeds[index].last_slow_log_ms==0 ||
                          now-g_feeds[index].last_slow_log_ms>=10000))
      {
         PrintFormat("[THE STAFF OF MOSES] feed 처리 지연: %s %s elapsed=%I64u ms",
                     g_feeds[index].symbol,g_feeds[index].tf_text,elapsed);
         g_feeds[index].last_slow_log_ms=now;
      }
      // A slow feed cannot make every callback restart at the first symbol/TF.
      if(GetTickCount64()-started >= (ulong)STAFF_WORK_BUDGET_MS) break;
   }
   ulong now=GetTickCount64();
   if(g_last_progress_ms==0 || now-g_last_progress_ms>=10000)
   {
      int published=0;
      for(int i=0;i<total;++i) if(g_feeds[i].published) published++;
      if(published<total)
         PrintFormat("[THE STAFF OF MOSES] 초기 데이터 준비: 최초 송신 완료 %d/%d feeds",published,total);
      g_last_progress_ms=now;
   }
   StaffPublishIdentityStatus(STAFF_PIPE_NAME,true,g_pipe!=INVALID_HANDLE,g_snapshot_seq);
}
//+------------------------------------------------------------------+
