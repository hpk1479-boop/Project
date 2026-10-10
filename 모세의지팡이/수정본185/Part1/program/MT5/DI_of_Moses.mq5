//+------------------------------------------------------------------+
//|                                                   DI_of_Moses.mq5      |
//+------------------------------------------------------------------+
#property strict
#property indicator_separate_window
#property indicator_buffers 12 
#property indicator_plots   8

#property indicator_label1  "LowBand"
#property indicator_type1   DRAW_LINE
#property indicator_color1  clrAqua
#property indicator_width1  2

#property indicator_label2  "HighBand"
#property indicator_type2   DRAW_LINE
#property indicator_color2  clrAqua
#property indicator_width2  2

#property indicator_label3  "cDI"
#property indicator_type3   DRAW_LINE
#property indicator_color3  clrYellow
#property indicator_width3  2

#property indicator_label4  "UpArrow"
#property indicator_type4   DRAW_ARROW
#property indicator_color4  clrLime
#property indicator_width4  2

#property indicator_label5  "DnArrow"
#property indicator_type5   DRAW_ARROW
#property indicator_color5  clrRed
#property indicator_width5  2

#property indicator_label6  "Regime Basis"
#property indicator_type6   DRAW_COLOR_LINE
#property indicator_color6  clrLime, clrRed, clrGray
#property indicator_style6  STYLE_SOLID
#property indicator_width6  1

#property indicator_label7  "Regime Disp Up"
#property indicator_type7   DRAW_LINE
#property indicator_color7  clrSilver
#property indicator_width7  1

#property indicator_label8  "Regime Disp Dn"
#property indicator_type8   DRAW_LINE
#property indicator_color8  clrSilver
#property indicator_width8  1

input bool   UseHighPrecision = false; // 정밀 검사 모드
input int    DILength         = 14;   
input int    BandPeriod       = 20;   
input double Leveling         = 10.0; 
input int    Vibration        = 10;   

double lowBandBuffer[], highBandBuffer[], smoothBuffer[];
double upArrowBuf[], dnArrowBuf[];
double bbBasisBuf[], bbBasisColorBuf[], bbDispUpBuf[], bbDispDnBuf[];
double lowerOutBuf[], upperOutBuf[]; // smooth 값의 Percentile Band OUT 상태
double regimeSlopeBuf[]; // Regime Basis current - previous
double rawArr[];

int phasingLag, targetCount;
double torque, oneMinusT;
static int allocated = 0;

double GetExactPercentile(const double &arr[], int size, double percent)
{
    if (size == 0) return 0.0;
    if (size == 1) return arr[0];
    double rank = (percent / 100.0) * (size - 1);
    int idx = (int)MathFloor(rank);
    double frac = rank - idx;
    if (idx >= size - 1) return arr[size - 1];
    if (idx < 0) return arr[0];
    return arr[idx] + frac * (arr[idx + 1] - arr[idx]);
}

int OnInit()
{
   IndicatorSetString(INDICATOR_SHORTNAME, "OZ DI Band");
   phasingLag = (Vibration - 1) / 2;
   torque = 2.0 / (Vibration + 1.0);
   oneMinusT = 1.0 - torque;
   targetCount = (int)MathCeil(BandPeriod * Leveling / 100.0);

   SetIndexBuffer(0, lowBandBuffer, INDICATOR_DATA); SetIndexBuffer(1, highBandBuffer, INDICATOR_DATA);
   SetIndexBuffer(2, smoothBuffer, INDICATOR_DATA); SetIndexBuffer(3, upArrowBuf, INDICATOR_DATA);
   SetIndexBuffer(4, dnArrowBuf, INDICATOR_DATA); SetIndexBuffer(5, bbBasisBuf, INDICATOR_DATA);
   SetIndexBuffer(6, bbBasisColorBuf, INDICATOR_COLOR_INDEX); SetIndexBuffer(7, bbDispUpBuf, INDICATOR_DATA);
   SetIndexBuffer(8, bbDispDnBuf, INDICATOR_DATA);
   SetIndexBuffer(9, lowerOutBuf, INDICATOR_CALCULATIONS);
   SetIndexBuffer(10, upperOutBuf, INDICATOR_CALCULATIONS);
   SetIndexBuffer(11, regimeSlopeBuf, INDICATOR_CALCULATIONS);

   ArraySetAsSeries(lowBandBuffer, true); ArraySetAsSeries(highBandBuffer, true);
   ArraySetAsSeries(smoothBuffer, true); ArraySetAsSeries(upArrowBuf, true);
   ArraySetAsSeries(dnArrowBuf, true); ArraySetAsSeries(bbBasisBuf, true);
   ArraySetAsSeries(bbBasisColorBuf, true); ArraySetAsSeries(bbDispUpBuf, true);
   ArraySetAsSeries(bbDispDnBuf, true); ArraySetAsSeries(rawArr, true);
   ArraySetAsSeries(lowerOutBuf, true); ArraySetAsSeries(upperOutBuf, true);
   ArraySetAsSeries(regimeSlopeBuf, true);

   PlotIndexSetInteger(3, PLOT_ARROW, 108); PlotIndexSetInteger(4, PLOT_ARROW, 108);
   for(int i = 0; i < 8; i++) PlotIndexSetInteger(i, PLOT_SHOW_DATA, false);
   
   IndicatorSetInteger(INDICATOR_LEVELS, 1);
   IndicatorSetDouble(INDICATOR_LEVELVALUE, 0, 0);
   IndicatorSetInteger(INDICATOR_LEVELSTYLE, 0, STYLE_DOT);
   return INIT_SUCCEEDED;
}

int OnCalculate(const int rates_total, const int prev_calculated, const datetime &time[], const double &open[], const double &high[], const double &low[], const double &close[], const long &tick_volume[], const long &volume[], const int &spread[])
{
   // E3: uncomputed history is explicitly unavailable.
   if(prev_calculated == 0)
   {
      ArrayInitialize(lowBandBuffer, EMPTY_VALUE);
      ArrayInitialize(highBandBuffer, EMPTY_VALUE);
      ArrayInitialize(smoothBuffer, EMPTY_VALUE);
      ArrayInitialize(upArrowBuf, EMPTY_VALUE);
      ArrayInitialize(dnArrowBuf, EMPTY_VALUE);
      ArrayInitialize(bbBasisBuf, EMPTY_VALUE);
      ArrayInitialize(bbBasisColorBuf, EMPTY_VALUE);
      ArrayInitialize(bbDispUpBuf, EMPTY_VALUE);
      ArrayInitialize(bbDispDnBuf, EMPTY_VALUE);
      ArrayInitialize(lowerOutBuf, EMPTY_VALUE);
      ArrayInitialize(upperOutBuf, EMPTY_VALUE);
      ArrayInitialize(regimeSlopeBuf, EMPTY_VALUE);
   }

   if(rates_total <= BandPeriod + phasingLag + DILength) return 0;
   if(prev_calculated == 0)
   {
      ArrayInitialize(lowerOutBuf, EMPTY_VALUE);
      ArrayInitialize(upperOutBuf, EMPTY_VALUE);
      ArrayInitialize(regimeSlopeBuf, EMPTY_VALUE);
   }
   if(rates_total > allocated)
   {
      allocated = rates_total + 1024;
      ArrayResize(rawArr, allocated); ArrayResize(lowBandBuffer, allocated); ArrayResize(highBandBuffer, allocated);
      ArrayResize(smoothBuffer, allocated); ArrayResize(upArrowBuf, allocated); ArrayResize(dnArrowBuf, allocated);
      ArrayResize(bbBasisBuf, allocated); ArrayResize(bbBasisColorBuf, allocated); ArrayResize(bbDispUpBuf, allocated); ArrayResize(bbDispDnBuf, allocated);
   }

   int limit = (prev_calculated == 0) ? rates_total - BandPeriod - phasingLag - DILength : rates_total - prev_calculated + 1;
   ArraySetAsSeries(close, true);

   for(int i = limit + phasingLag + 1; i >= 0; i--) {
      double sum = 0; for(int j = 0; j < DILength; j++) sum += close[i+j];
      double sma = sum / DILength;
      rawArr[i] = (sma == 0) ? 0 : ((close[i] - sma) / sma) * 100.0;
   }

   if(prev_calculated == 0)
      for(int j=limit+1;j<rates_total;++j) smoothBuffer[j]=0.0;

   // First recurrence reads limit+1 before writing limit. Never depend on allocator bytes.
   // Uncomputed output cells remain EMPTY_VALUE; these are internal zero seeds only.
   if(prev_calculated == 0 && limit+1 < rates_total)
   {
      smoothBuffer[limit+1] = 0.0;
      bbBasisBuf[limit+1] = 0.0;
   }

   double lmin = 0.0, lmax = 0.0;
   for(int i = limit; i >= 0; i--)
   {
      int refIdx = i + phasingLag; int nxtIdx = (i + 1 < rates_total ? i + 1 : i);
      smoothBuffer[i] = torque * (2.0 * rawArr[i] - rawArr[refIdx]) + oneMinusT * smoothBuffer[nxtIdx];

      int cntWin = MathMin(BandPeriod, rates_total - i);
      if(cntWin <= 0) continue;

      if(UseHighPrecision) {
          double temp[];
          ArrayResize(temp, cntWin);
          for(int m = 0; m < cntWin; m++) temp[m] = smoothBuffer[i + m];
          ArraySort(temp);
          lowBandBuffer[i] = GetExactPercentile(temp, cntWin, Leveling);
          highBandBuffer[i] = GetExactPercentile(temp, cntWin, 100.0 - Leveling);
      } else {
          if(i == limit || i + cntWin >= rates_total || smoothBuffer[i + cntWin] == lmin || smoothBuffer[i + cntWin] == lmax) {
             lmin = lmax = smoothBuffer[i];
             for(int m = 1; m < cntWin; m++) {
                if(smoothBuffer[i + m] < lmin) lmin = smoothBuffer[i + m];
                else if(smoothBuffer[i + m] > lmax) lmax = smoothBuffer[i + m];
             }
          } else {
             if(smoothBuffer[i] < lmin) lmin = smoothBuffer[i];
             if(smoothBuffer[i] > lmax) lmax = smoothBuffer[i];
          }

          if(lmin == lmax) { lowBandBuffer[i] = highBandBuffer[i] = lmin; continue; }

          double step = (lmax - lmin) * 0.01;
          for(int s = 0; s <= 100; s++) {
             double thr = lmin + s * step; int cnt = 0;
             for(int m = 0; m < cntWin && cnt < targetCount; m++) if(smoothBuffer[i + m] < thr) cnt++;
             if(cnt >= targetCount){ lowBandBuffer[i] = thr; break; }
          }
          for(int s = 0; s <= 100; s++) {
             double thr = lmax - s * step; int cnt = 0;
             for(int m = 0; m < cntWin && cnt < targetCount; m++) if(smoothBuffer[i + m] >= thr) cnt++;
             if(cnt >= targetCount){ highBandBuffer[i] = thr; break; }
          }
      }
   }

   double alpha = 2.0 / (BandPeriod + 1.0);
   for(int i = limit; i >= 0; i--)
   {
      upArrowBuf[i] = EMPTY_VALUE; dnArrowBuf[i] = EMPTY_VALUE;
      lowerOutBuf[i] = EMPTY_VALUE; upperOutBuf[i] = EMPTY_VALUE;
      regimeSlopeBuf[i] = EMPTY_VALUE;
      if(smoothBuffer[i] != EMPTY_VALUE && lowBandBuffer[i] != EMPTY_VALUE && highBandBuffer[i] != EMPTY_VALUE)
      {
         if(smoothBuffer[i] < lowBandBuffer[i])
            lowerOutBuf[i] = smoothBuffer[i];
         else if(smoothBuffer[i] > highBandBuffer[i])
            upperOutBuf[i] = smoothBuffer[i];
      }
      int next = (i + 1 < rates_total ? i + 1 : i);
      if(i != next) {
         double bandW = highBandBuffer[next] - lowBandBuffer[next];
         if(bandW == 0) bandW = 0.1;
         if(smoothBuffer[next] < lowBandBuffer[next] && smoothBuffer[i] >= lowBandBuffer[i]) upArrowBuf[i] = lowBandBuffer[i] - bandW * 0.15;
         if(smoothBuffer[next] > highBandBuffer[next] && smoothBuffer[i] <= highBandBuffer[i]) dnArrowBuf[i] = highBandBuffer[i] + bandW * 0.15;
      }

      if(rates_total - i == BandPeriod) {
         double sum = 0; for(int j=0; j<BandPeriod; j++) sum += smoothBuffer[i+j];
         bbBasisBuf[i] = sum / BandPeriod; bbBasisColorBuf[i] = 2;
      } else if(rates_total - i > BandPeriod) {
         bbBasisBuf[i] = alpha * smoothBuffer[i] + (1.0 - alpha) * bbBasisBuf[i+1];
         regimeSlopeBuf[i] = bbBasisBuf[i] - bbBasisBuf[i+1];
         if(bbBasisBuf[i] > bbBasisBuf[i+1]) bbBasisColorBuf[i] = 0;
         else if(bbBasisBuf[i] < bbBasisBuf[i+1]) bbBasisColorBuf[i] = 1;
         else bbBasisColorBuf[i] = 2;
      }

      double sum_m = 0; for(int j=0; j<BandPeriod; j++) sum_m += smoothBuffer[i+j];
      double mean = sum_m / BandPeriod; double sum_v = 0;
      for(int j=0; j<BandPeriod; j++) sum_v += MathPow(smoothBuffer[i+j] - mean, 2);
      
      double stdDev = MathSqrt(sum_v / BandPeriod);
      bbDispUpBuf[i] = bbBasisBuf[i] + stdDev * 0.4;
      bbDispDnBuf[i] = bbBasisBuf[i] - stdDev * 0.4;
   }
   if(prev_calculated == 0)
      for(int j=limit+1;j<rates_total;++j) smoothBuffer[j]=EMPTY_VALUE;
   return rates_total;
}
