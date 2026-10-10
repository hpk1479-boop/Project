//+------------------------------------------------------------------+
//|                                              PRICE_of_Moses.mq5   |
//|    (Percentile Band with Ehlers + 3 MAs + Display Mode Options)  |
//+------------------------------------------------------------------+
#property copyright "Customized for User"
#property link      ""
#property version   "1.21" 
#property indicator_chart_window
#property indicator_buffers 13
#property indicator_plots   11

//--- 디스플레이 표시 모드 열거형 (Enum)
enum ENUM_DISPLAY_MODE
{
   MODE_SHOW_ALL  = 0, // 모두 보기
   MODE_SHOW_BAND = 1, // 밴드 보기
   MODE_SHOW_MA   = 2, // 이평선 보기
   MODE_HIDE_ALL  = 3  // 모두 숨기기
};

//--- 지표 시각화 속성 설정
#property indicator_label1  "Upper Band"
#property indicator_type1   DRAW_LINE
#property indicator_color1  clrWhiteSmoke
#property indicator_style1  STYLE_SOLID
#property indicator_width1  2

#property indicator_label2  "Lower Band"
#property indicator_type2   DRAW_LINE
#property indicator_color2  clrWhiteSmoke
#property indicator_style2  STYLE_SOLID
#property indicator_width2  2

#property indicator_label3  "MA 1"
#property indicator_type3   DRAW_LINE
#property indicator_color3  clrSnow
#property indicator_style3  STYLE_DASHDOTDOT
#property indicator_width3  2

#property indicator_label4  "MA 2"
#property indicator_type4   DRAW_LINE
#property indicator_color4  clrLime
#property indicator_style4  STYLE_DASHDOTDOT
#property indicator_width4  2

#property indicator_label5  "MA 3"
#property indicator_type5   DRAW_LINE
#property indicator_color5  clrRed
#property indicator_style5  STYLE_DASHDOTDOT
#property indicator_width5  2

#property indicator_label6  "HMA 6"
#property indicator_type6   DRAW_LINE
#property indicator_color6  clrAqua
#property indicator_style6  STYLE_SOLID
#property indicator_width6  2

#property indicator_label7  "PRICE Lower OUT-IN"
#property indicator_type7   DRAW_ARROW
#property indicator_color7  clrLime
#property indicator_width7  2

#property indicator_label8  "PRICE Upper OUT-IN"
#property indicator_type8   DRAW_ARROW
#property indicator_color8  clrRed
#property indicator_width8  2

#property indicator_label9  "Regime Basis"
#property indicator_type9   DRAW_COLOR_LINE
#property indicator_color9  clrLime, clrRed, clrGray
#property indicator_style9  STYLE_SOLID
#property indicator_width9  1

#property indicator_label10 "Regime Disp Up"
#property indicator_type10  DRAW_LINE
#property indicator_color10 clrSilver
#property indicator_width10 1

#property indicator_label11 "Regime Disp Dn"
#property indicator_type11  DRAW_LINE
#property indicator_color11 clrSilver
#property indicator_width11 1

//--- 입력 파라미터 ---
input group "=== Display Settings ==="
input ENUM_DISPLAY_MODE InpDisplayMode = MODE_SHOW_BAND; // 표시 모드 선택

input group "=== Price Band Settings ==="
input bool    InpUseSmooth        = true;   // Ehlers Zero-Lag Filter 사용
input int     InpDomCycle         = 20;     // Dominant Cycle Length
input double  InpLeveling         = 10.0;   // Dominant Cycle Amplitude (%)
input bool    InpUseHighPrecision = false;   // 정밀 계산 모드 (체크 시 전수조사)
input bool    InpShowHMA6         = true;   // Price OUT-IN 판정선(HMA6) 표시
input bool    InpShowSignals      = true;   // HMA6 Price Band OUT-IN 신호 표시
input bool    InpShowRegimeBand   = true;   // 레짐밴드 보기
input bool    InpWriteValidation  = false;  // 운영 기본값: 검증 CSV 기록 OFF
const int     VIBRATION           = 10;
const int     REGIME_PERIOD       = 20;
const double  REGIME_STD_MULT     = 0.4;    

input group "=== Moving Average 1 ==="
input int                InpMa1Len    = 20;          
input ENUM_APPLIED_PRICE InpMa1Src    = PRICE_OPEN;  
input ENUM_MA_METHOD     InpMa1Method = MODE_EMA;    


input group "=== Moving Average 2 ==="
input int                InpMa2Len    = 50;          
input ENUM_APPLIED_PRICE InpMa2Src    = PRICE_OPEN;  
input ENUM_MA_METHOD     InpMa2Method = MODE_EMA;    

input group "=== Moving Average 3 ==="
input int                InpMa3Len    = 200;         
input ENUM_APPLIED_PRICE InpMa3Src    = PRICE_OPEN;  
input ENUM_MA_METHOD     InpMa3Method = MODE_EMA;    


//--- 지표 버퍼
double UpperBuffer[];
double LowerBuffer[];
double MA1Buffer[];
double MA2Buffer[];
double MA3Buffer[];
double EhlersBuffer[]; // 내부 연산용 버퍼
double HMA6Buffer[];
double LowerSignalBuffer[];
double UpperSignalBuffer[];
double RegimeBasisBuffer[];
double RegimeBasisColorBuffer[];
double RegimeDispUpBuffer[];
double RegimeDispDnBuffer[];

//--- 전역 변수 (에일러스 엔진용)
double torqueEhlers;
double phasingLagEhlers;
int    idx_p;
double frac_p;

//--- 이평선 핸들(Handle) 변수
int ma1_handle = INVALID_HANDLE;
int ma2_handle = INVALID_HANDLE;
int ma3_handle = INVALID_HANDLE;

string VALIDATION_FILE = "oz_price_validation.csv";
datetime lastWrittenLower = 0;
datetime lastWrittenUpper = 0;

//+------------------------------------------------------------------+
//| Custom Indicator Initialization Function                         |
//+------------------------------------------------------------------+
int OnInit()
{
   SetIndexBuffer(0, UpperBuffer, INDICATOR_DATA);
   SetIndexBuffer(1, LowerBuffer, INDICATOR_DATA);
   SetIndexBuffer(2, MA1Buffer,   INDICATOR_DATA);
   SetIndexBuffer(3, MA2Buffer,   INDICATOR_DATA);
   SetIndexBuffer(4, MA3Buffer,   INDICATOR_DATA);
   SetIndexBuffer(5, HMA6Buffer, INDICATOR_DATA);
   SetIndexBuffer(6, LowerSignalBuffer, INDICATOR_DATA);
   SetIndexBuffer(7, UpperSignalBuffer, INDICATOR_DATA);
   SetIndexBuffer(8, RegimeBasisBuffer, INDICATOR_DATA);
   SetIndexBuffer(9, RegimeBasisColorBuffer, INDICATOR_COLOR_INDEX);
   SetIndexBuffer(10, RegimeDispUpBuffer, INDICATOR_DATA);
   SetIndexBuffer(11, RegimeDispDnBuffer, INDICATOR_DATA);
   SetIndexBuffer(12, EhlersBuffer, INDICATOR_CALCULATIONS);

   ArraySetAsSeries(UpperBuffer, true);
   ArraySetAsSeries(LowerBuffer, true);
   ArraySetAsSeries(MA1Buffer, true);
   ArraySetAsSeries(MA2Buffer, true);
   ArraySetAsSeries(MA3Buffer, true);
   ArraySetAsSeries(HMA6Buffer, true);
   ArraySetAsSeries(LowerSignalBuffer, true);
   ArraySetAsSeries(UpperSignalBuffer, true);
   ArraySetAsSeries(RegimeBasisBuffer, true);
   ArraySetAsSeries(RegimeBasisColorBuffer, true);
   ArraySetAsSeries(RegimeDispUpBuffer, true);
   ArraySetAsSeries(RegimeDispDnBuffer, true);
   ArraySetAsSeries(EhlersBuffer, true);

   torqueEhlers = 2.0 / (VIBRATION + 1.0);
   phasingLagEhlers = (VIBRATION - 1.0) / 2.0;
   idx_p = (int)MathFloor(phasingLagEhlers);
   frac_p = phasingLagEhlers - idx_p;

   ma1_handle = iMA(_Symbol, _Period, InpMa1Len, 0, InpMa1Method, InpMa1Src);
   ma2_handle = iMA(_Symbol, _Period, InpMa2Len, 0, InpMa2Method, InpMa2Src);
   ma3_handle = iMA(_Symbol, _Period, InpMa3Len, 0, InpMa3Method, InpMa3Src);

   if(ma1_handle == INVALID_HANDLE || ma2_handle == INVALID_HANDLE || ma3_handle == INVALID_HANDLE)
      return(INIT_FAILED);

   for(int i = 0; i < 11; i++)
      PlotIndexSetDouble(i, PLOT_EMPTY_VALUE, EMPTY_VALUE);

   PlotIndexSetInteger(6, PLOT_ARROW, 233); // lower OUT->IN: up arrow
   PlotIndexSetInteger(7, PLOT_ARROW, 234); // upper OUT->IN: down arrow

   string short_name = StringFormat("OZ_Price_Band(%d, %d)", VIBRATION, InpDomCycle);
   IndicatorSetString(INDICATOR_SHORTNAME, short_name);
   
   return(INIT_SUCCEEDED);
}

//+------------------------------------------------------------------+
//| Custom Indicator Deinitialization Function                       |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   if(ma1_handle != INVALID_HANDLE) IndicatorRelease(ma1_handle);
   if(ma2_handle != INVALID_HANDLE) IndicatorRelease(ma2_handle);
   if(ma3_handle != INVALID_HANDLE) IndicatorRelease(ma3_handle);
}

//+------------------------------------------------------------------+
//| Custom Indicator Iteration Function                              |
//+------------------------------------------------------------------+
int OnCalculate(const int rates_total,
                const int prev_calculated,
                const datetime &time[],
                const double &open[],
                const double &high[],
                const double &low[],
                const double &close[],
                const long &tick_volume[],
                const long &volume[],
                const int &spread[])
{
   if(rates_total < InpDomCycle + VIBRATION + 2) return 0;

   ArraySetAsSeries(time, true);
   ArraySetAsSeries(open, true);
   ArraySetAsSeries(high, true);
   ArraySetAsSeries(low, true);
   ArraySetAsSeries(close, true);

   int limit = rates_total - prev_calculated;
   if(limit <= 0) limit = 1;
   if(prev_calculated == 0) limit = rates_total - (InpDomCycle + VIBRATION + 1);
   if(limit < 0) limit = 0;

   bool showBand = (InpDisplayMode == MODE_SHOW_ALL || InpDisplayMode == MODE_SHOW_BAND);
   bool showMA   = (InpDisplayMode == MODE_SHOW_ALL || InpDisplayMode == MODE_SHOW_MA);

   //====================================================================
   // [0] '숨기기' 모드일 경우 모든 연산 차단
   //====================================================================
   if(!showBand && !showMA)
   {
      for(int i = limit; i >= 0; i--)
      {
         UpperBuffer[i] = EMPTY_VALUE; LowerBuffer[i] = EMPTY_VALUE;
         MA1Buffer[i] = EMPTY_VALUE; MA2Buffer[i] = EMPTY_VALUE; MA3Buffer[i] = EMPTY_VALUE;
         HMA6Buffer[i] = EMPTY_VALUE; LowerSignalBuffer[i] = EMPTY_VALUE; UpperSignalBuffer[i] = EMPTY_VALUE;
         RegimeBasisBuffer[i] = EMPTY_VALUE; RegimeBasisColorBuffer[i] = 2;
         RegimeDispUpBuffer[i] = EMPTY_VALUE; RegimeDispDnBuffer[i] = EMPTY_VALUE;
      }
      return(rates_total);
   }

   //====================================================================
   // [1] 이평선 (MA) 연산 처리 구간 (기존 로직 보존)
   //====================================================================
   if(showMA)
   {
      int copy_count = limit + 1;
      double t_ma1[], t_ma2[], t_ma3[];
      
      if(CopyBuffer(ma1_handle, 0, 0, copy_count, t_ma1) > 0) {
         ArraySetAsSeries(t_ma1, true);
         for(int i = 0; i <= limit; i++) MA1Buffer[i] = t_ma1[i];
      }
      if(CopyBuffer(ma2_handle, 0, 0, copy_count, t_ma2) > 0) {
         ArraySetAsSeries(t_ma2, true);
         for(int i = 0; i <= limit; i++) MA2Buffer[i] = t_ma2[i];
      }
      if(CopyBuffer(ma3_handle, 0, 0, copy_count, t_ma3) > 0) {
         ArraySetAsSeries(t_ma3, true);
         for(int i = 0; i <= limit; i++) MA3Buffer[i] = t_ma3[i];
      }
   }
   else
   {
      for(int i = limit; i >= 0; i--) {
         MA1Buffer[i] = EMPTY_VALUE; MA2Buffer[i] = EMPTY_VALUE; MA3Buffer[i] = EMPTY_VALUE;
      }
   }

   //====================================================================
   // [2] Price Band 연산 처리 구간 (에러 수정 및 최적화 엔진 탑재)
   //====================================================================
   if(showBand)
   {
      // 1. Ehlers 스무딩 단독 선행 루프 (미래 인덱스 오염 방지)
      int ehlers_limit = limit + InpDomCycle + 2;
      if (ehlers_limit >= rates_total - idx_p - 1) ehlers_limit = rates_total - idx_p - 2;

      for(int i = ehlers_limit; i >= 0; i--)
      {
         double ref_price = close[i + idx_p] * (1.0 - frac_p) + close[i + idx_p + 1] * frac_p;
         double prev_ehlers = (i + 1 < rates_total) ? EhlersBuffer[i + 1] : close[i];
         if(prev_ehlers == 0 || prev_ehlers == EMPTY_VALUE) prev_ehlers = close[i];
         
         EhlersBuffer[i] = torqueEhlers * (2.0 * close[i] - ref_price) + (1.0 - torqueEhlers) * prev_ehlers;
      }

      // 2. 퍼센테일 밴드 엔진 (슬라이딩 윈도우 + 정밀 모드)
      double lmin = 0.0, lmax = 0.0;
      int targetCount = (int)MathCeil(InpDomCycle * InpLeveling / 100.0);

      for(int i = limit; i >= 0; i--)
      {
         int cntWin = MathMin(InpDomCycle, rates_total - i);
         if(cntWin <= 0) continue;

         if(InpUseHighPrecision) 
         {
            // [정밀 모드] 매 봉 배열 정렬 기반 정확한 선형 보간
            double temp_array[];
            ArrayResize(temp_array, cntWin);
            for(int m = 0; m < cntWin; m++) temp_array[m] = InpUseSmooth ? EhlersBuffer[i + m] : close[i + m];
            ArraySort(temp_array);
            LowerBuffer[i] = GetPercentile(temp_array, InpLeveling);
            UpperBuffer[i] = GetPercentile(temp_array, 100.0 - InpLeveling);
         } 
         else 
         {
            // [최적화 모드] 슬라이딩 윈도우 기반 100 스텝 근사 (속도 극대화)
            double cur_val = InpUseSmooth ? EhlersBuffer[i] : close[i];
            double out_val = (i + cntWin < rates_total) ? (InpUseSmooth ? EhlersBuffer[i + cntWin] : close[i + cntWin]) : 0.0;

            if(i == limit || i + cntWin >= rates_total || out_val == lmin || out_val == lmax) 
            {
               lmin = lmax = cur_val;
               for(int m = 1; m < cntWin; m++) 
               {
                  double v = InpUseSmooth ? EhlersBuffer[i + m] : close[i + m];
                  if(v < lmin) lmin = v;
                  else if(v > lmax) lmax = v;
               }
            } 
            else 
            {
               if(cur_val < lmin) lmin = cur_val;
               if(cur_val > lmax) lmax = cur_val;
            }

            if(lmin == lmax) { LowerBuffer[i] = lmin; UpperBuffer[i] = lmax; continue; }

            double step = (lmax - lmin) * 0.01;
            
            // 하단 밴드 탐색
            for(int s = 0; s <= 100; s++) {
               double thr = lmin + s * step; int cnt = 0;
               for(int m = 0; m < cntWin && cnt < targetCount; m++) {
                  double v = InpUseSmooth ? EhlersBuffer[i + m] : close[i + m];
                  if(v < thr) cnt++;
               }
               if(cnt >= targetCount){ LowerBuffer[i] = thr; break; }
            }
            
            // 상단 밴드 탐색
            for(int s = 0; s <= 100; s++) {
               double thr = lmax - s * step; int cnt = 0;
               for(int m = 0; m < cntWin && cnt < targetCount; m++) {
                  double v = InpUseSmooth ? EhlersBuffer[i + m] : close[i + m];
                  if(v >= thr) cnt++;
               }
               if(cnt >= targetCount){ UpperBuffer[i] = thr; break; }
            }
         }
      }

      // 3. 레짐밴드: Percentile과 동일한 가격 소스의 EMA20 ± 0.4 표준편차
      if(InpShowRegimeBand)
      {
         double regime_alpha = 2.0 / (REGIME_PERIOD + 1.0);
         for(int i = limit; i >= 0; i--)
         {
            if(i + REGIME_PERIOD - 1 >= rates_total)
            {
               RegimeBasisBuffer[i] = EMPTY_VALUE;
               RegimeBasisColorBuffer[i] = 2;
               RegimeDispUpBuffer[i] = EMPTY_VALUE;
               RegimeDispDnBuffer[i] = EMPTY_VALUE;
               continue;
            }

            double sum = 0.0;
            for(int j = 0; j < REGIME_PERIOD; j++)
               sum += InpUseSmooth ? EhlersBuffer[i + j] : close[i + j];
            double mean = sum / REGIME_PERIOD;

            bool need_seed = (prev_calculated == 0 && i == limit);
            if(!need_seed && i + 1 < rates_total)
               need_seed = (RegimeBasisBuffer[i + 1] == EMPTY_VALUE || RegimeBasisBuffer[i + 1] == 0.0);

            double source_now = InpUseSmooth ? EhlersBuffer[i] : close[i];
            if(need_seed || i + 1 >= rates_total)
            {
               RegimeBasisBuffer[i] = mean;
               RegimeBasisColorBuffer[i] = 2;
            }
            else
            {
               RegimeBasisBuffer[i] = regime_alpha * source_now + (1.0 - regime_alpha) * RegimeBasisBuffer[i + 1];
               if(RegimeBasisBuffer[i] > RegimeBasisBuffer[i + 1]) RegimeBasisColorBuffer[i] = 0;
               else if(RegimeBasisBuffer[i] < RegimeBasisBuffer[i + 1]) RegimeBasisColorBuffer[i] = 1;
               else RegimeBasisColorBuffer[i] = 2;
            }

            double sum_v = 0.0;
            for(int j = 0; j < REGIME_PERIOD; j++)
            {
               double v = InpUseSmooth ? EhlersBuffer[i + j] : close[i + j];
               sum_v += MathPow(v - mean, 2);
            }
            double stdDev = MathSqrt(sum_v / REGIME_PERIOD);
            RegimeDispUpBuffer[i] = RegimeBasisBuffer[i] + stdDev * REGIME_STD_MULT;
            RegimeDispDnBuffer[i] = RegimeBasisBuffer[i] - stdDev * REGIME_STD_MULT;
         }
      }
      else
      {
         for(int i = limit; i >= 0; i--)
         {
            RegimeBasisBuffer[i] = EMPTY_VALUE;
            RegimeBasisColorBuffer[i] = 2;
            RegimeDispUpBuffer[i] = EMPTY_VALUE;
            RegimeDispDnBuffer[i] = EMPTY_VALUE;
         }
      }
   }
   else
   {
      for(int i = limit; i >= 0; i--) {
         UpperBuffer[i] = EMPTY_VALUE; LowerBuffer[i] = EMPTY_VALUE; EhlersBuffer[i] = EMPTY_VALUE;
         HMA6Buffer[i] = EMPTY_VALUE; LowerSignalBuffer[i] = EMPTY_VALUE; UpperSignalBuffer[i] = EMPTY_VALUE;
         RegimeBasisBuffer[i] = EMPTY_VALUE; RegimeBasisColorBuffer[i] = 2;
         RegimeDispUpBuffer[i] = EMPTY_VALUE; RegimeDispDnBuffer[i] = EMPTY_VALUE;
      }
   }

   //====================================================================
   // [3] HMA6 Price Band OUT -> IN 시각화 + 검증 이벤트 기록
   //     IMPORTANT: PRICE OUT/IN HMA6는 CLOSE 가격으로 계산 (MOSES price_hma_6와 동일)
   //====================================================================
   if(showBand)
   {
      int hma_limit = limit + 8;
      if(hma_limit > rates_total - 8) hma_limit = rates_total - 8;
      for(int i = hma_limit; i >= 0; i--)
         HMA6Buffer[i] = CalcHMA6Close(close, i, rates_total);

      for(int i = limit; i >= 0; i--)
      {
         LowerSignalBuffer[i] = EMPTY_VALUE;
         UpperSignalBuffer[i] = EMPTY_VALUE;
      }

      // 확정봉만 신호 판정 (i >= 1).
      for(int i = limit; i >= 1; i--)
      {
         if(i + 1 >= rates_total) continue;
         if(HMA6Buffer[i] == EMPTY_VALUE || HMA6Buffer[i + 1] == EMPTY_VALUE ||
            LowerBuffer[i] == EMPTY_VALUE || LowerBuffer[i + 1] == EMPTY_VALUE ||
            UpperBuffer[i] == EMPTY_VALUE || UpperBuffer[i + 1] == EMPTY_VALUE)
            continue;

         bool lower_in = (HMA6Buffer[i + 1] < LowerBuffer[i + 1] && HMA6Buffer[i] >= LowerBuffer[i]);
         bool upper_in = (HMA6Buffer[i + 1] > UpperBuffer[i + 1] && HMA6Buffer[i] <= UpperBuffer[i]);

         double pad = MathMax((high[i] - low[i]) * 0.25, _Point * 10.0);

         if(lower_in)
         {
            double extreme = low[i];
            int k = i + 1;
            while(k < rates_total && HMA6Buffer[k] != EMPTY_VALUE && LowerBuffer[k] != EMPTY_VALUE &&
                  HMA6Buffer[k] < LowerBuffer[k])
            {
               if(low[k] < extreme) extreme = low[k];
               k++;
            }

            if(InpShowSignals) LowerSignalBuffer[i] = low[i] - pad;
            if(InpWriteValidation && time[i] != lastWrittenLower)
            {
               WriteValidationEvent(time[i], "LOWER_OUT_IN", extreme);
               lastWrittenLower = time[i];
            }
         }

         if(upper_in)
         {
            double extreme = high[i];
            int k = i + 1;
            while(k < rates_total && HMA6Buffer[k] != EMPTY_VALUE && UpperBuffer[k] != EMPTY_VALUE &&
                  HMA6Buffer[k] > UpperBuffer[k])
            {
               if(high[k] > extreme) extreme = high[k];
               k++;
            }

            if(InpShowSignals) UpperSignalBuffer[i] = high[i] + pad;
            if(InpWriteValidation && time[i] != lastWrittenUpper)
            {
               WriteValidationEvent(time[i], "UPPER_OUT_IN", extreme);
               lastWrittenUpper = time[i];
            }
         }
      }

      if(!InpShowHMA6)
      {
         for(int i = limit; i >= 0; i--) HMA6Buffer[i] = EMPTY_VALUE;
      }
   }

   return(rates_total);
}

//+------------------------------------------------------------------+
//| PRICE HMA6 helpers - MOSES calc_hma(close, 6) compatible       |
//+------------------------------------------------------------------+
double WMAClose(const double &close[], int shift, int length, int rates_total)
{
   if(shift < 0 || shift + length - 1 >= rates_total) return EMPTY_VALUE;
   double weighted = 0.0;
   double denom = 0.0;
   for(int j = 0; j < length; j++)
   {
      double weight = (double)(length - j); // newest gets largest weight
      weighted += close[shift + j] * weight;
      denom += weight;
   }
   return (denom > 0.0) ? weighted / denom : EMPTY_VALUE;
}

double RawHMA6Close(const double &close[], int shift, int rates_total)
{
   double wma3 = WMAClose(close, shift, 3, rates_total);
   double wma6 = WMAClose(close, shift, 6, rates_total);
   if(wma3 == EMPTY_VALUE || wma6 == EMPTY_VALUE) return EMPTY_VALUE;
   return 2.0 * wma3 - wma6;
}

double CalcHMA6Close(const double &close[], int shift, int rates_total)
{
   // PRICE ONLY: pandas calc_hma(close, 6). OPEN-based HMA6/17 crosses are separate strategy logic.
   double raw0 = RawHMA6Close(close, shift, rates_total);
   double raw1 = RawHMA6Close(close, shift + 1, rates_total);
   if(raw0 == EMPTY_VALUE || raw1 == EMPTY_VALUE) return EMPTY_VALUE;
   return (raw0 * 2.0 + raw1) / 3.0;
}

string TimeframeText()
{
   switch(_Period)
   {
      case PERIOD_M1:  return "1m";
      case PERIOD_M2:  return "2m";
      case PERIOD_M3:  return "3m";
      case PERIOD_M4:  return "4m";
      case PERIOD_M5:  return "5m";
      case PERIOD_M6:  return "6m";
      case PERIOD_M10: return "10m";
      case PERIOD_M12: return "12m";
      case PERIOD_M15: return "15m";
      case PERIOD_M20: return "20m";
      case PERIOD_M30: return "30m";
      case PERIOD_H1:  return "1h";
      case PERIOD_H2:  return "2h";
      case PERIOD_H3:  return "3h";
      case PERIOD_H4:  return "4h";
      case PERIOD_H6:  return "6h";
      case PERIOD_H8:  return "8h";
      case PERIOD_H12: return "12h";
      case PERIOD_D1:  return "1d";
   }
   return EnumToString(_Period);
}

void WriteValidationEvent(datetime bar_time, string direction, double extreme)
{
   int handle = FileOpen(VALIDATION_FILE, FILE_COMMON|FILE_CSV|FILE_READ|FILE_WRITE|FILE_ANSI, ',');
   if(handle == INVALID_HANDLE)
   {
      Print("OZ PRICE validation CSV open failed: ", GetLastError());
      return;
   }

   if(FileSize(handle) == 0)
      FileWrite(handle, "epoch", "symbol", "timeframe", "indicator", "direction", "extreme");

   FileSeek(handle, 0, SEEK_END);
   FileWrite(handle,
             IntegerToString((long)bar_time),
             _Symbol,
             TimeframeText(),
             "PRICE",
             direction,
             DoubleToString(extreme, _Digits));
   FileFlush(handle);
   FileClose(handle);
}

//+------------------------------------------------------------------+
//| Helper Function: Linear Interpolation Percentile                 |
//+------------------------------------------------------------------+
double GetPercentile(double &arr[], double percent)
{
   int n = ArraySize(arr);
   if(n == 0) return 0.0;

   double rank = (percent / 100.0) * (n - 1);
   int lower_idx = (int)MathFloor(rank);
   int upper_idx = (int)MathCeil(rank);
   
   if(lower_idx < 0) lower_idx = 0;
   if(upper_idx >= n) upper_idx = n - 1;
   if(lower_idx == upper_idx) return arr[lower_idx];
   
   double weight = rank - lower_idx;
   return arr[lower_idx] * (1.0 - weight) + arr[upper_idx] * weight;
}