//+------------------------------------------------------------------+
//|                    OZ Divine Shield Trading v1.0                 |
//+------------------------------------------------------------------+

#property copyright "Oilve Oil"
#property version   "1.20"
#property strict

#include <Trade\Trade.mqh>

//────────────────────────────────────────
//  동작 모드 (Execution Mode) 정의
//────────────────────────────────────────
enum ENUM_EA_MODE
{
    MODE_FULL,         // [메인 PC] 모든 기능 활성화 (모바일 자동화 및 방어 포함)
    MODE_CONTROL_ONLY  // [서브 PC] 패널 및 단축키 수동 제어만 활성화 (충돌 방지)
};


// RR 도달 동작과 트레일링 갱신 주기는 서로 독립이다.
enum ENUM_RR_ACTION
{
    RR_TAKE_PROFIT,       // 지정가 TP (진입 주문에 SL/TP 동시 설정)
    RR_HALF_BREAKEVEN,    // 반익반본
    RR_TRAILING          // 트레일링
};
enum ENUM_TRAIL_UPDATE
{
    TRAIL_REALTIME,       // 실시간 (매 틱)
    TRAIL_BAR_CLOSE       // 봉마감 (현재 차트 주기)
};

//────────────────────────────────────────
//  라이센스
//────────────────────────────────────────
bool CheckExpiry()
{
    datetime expiry = D'2026.12.31';      
    datetime now = TimeCurrent();        

    if(now > expiry)
    {
        Alert("라이센스가 만료되었습니다.");
        ExpertRemove();  
        return(false);
    }
    return(true);
}

//────────────────────────────────────────────────────────────
// 매직넘버(Magic Number) 정의 
//────────────────────────────────────────────────────────────
#define MAGIC_BUY     2001
#define MAGIC_SELL    2002

bool IsValidMagic(long m) { return (m == MAGIC_BUY || m == MAGIC_SELL || m == 0); }

//────────────────────────────────────────────────────────────
// UI 정의 및 시스템 한계값 설정
//────────────────────────────────────────────────────────────
#define BTN_BUY          "btn_buy"
#define BTN_SELL         "btn_sell"
#define BTN_HALF         "btn_half"        
#define BTN_CLOSE_PROFIT "btn_close_profit"
#define BTN_TRAIL        "btn_trail"
#define BTN_CLOSEALL     "btn_closeall"
#define BTN_RESET        "btn_reset"        
#define EDIT_RISK        "edit_risk"      
#define LINE_SL          "line_sl"        
#define LAB_EXPECTED_LOT "lab_exp_lot"

#define LAB_MODE    "lab_mode"
#define LAB_SPREAD  "lab_spread"
#define LAB_ATR     "lab_atr"          
#define LAB_BUY     "lab_buy_total"
#define LAB_SELL    "lab_sell_total"
#define LAB_PROFIT  "lab_profit"
#define LAB_EQ      "lab_equity"

#define MAX_TRACK_SIZE 500 

// [UI] 우측 정렬 오프셋
int OffsetBuy=10; int OffsetSell=10; int OffsetProfit=10; int OffsetEq=10; int Gap=15;

// [최적화] 전역 캐싱 변수
double g_basePip = 0.0;
double g_point   = 0.0;
double g_tickSize = 0.0;

// [최적화] Early Exit 및 지표 핸들 상태 저장 변수
double   g_lastBid = 0.0;
double   g_lastAsk = 0.0;
datetime g_lastBarTime = 0;
int      g_atrHandle = INVALID_HANDLE;
double   g_lastATR = 0.0;
bool     g_tradeDirty = true;
datetime g_atrBarTime = 0;      // 현재 봉에 대응하는 직전 마감봉 ATR의 취득 여부
ulong    g_atrRetryAt = 0;
datetime g_trailPendingBar = 0;
datetime g_trailDoneBar = 0;
bool     g_posStatesDirty = false;

// 포지션 스냅샷 구조체
struct PosInfo {
   ulong  ticket;
   long   type;
   double volume;
   double entry;
   double sl;
   double tp;  
   long   magic;
   long   timeMsc;
};

//────────────────────────────────────────────────────────────
// 입력값 (Inputs)
//────────────────────────────────────────────────────────────

input group "==== [동작 모드 설정] ===="
input ENUM_EA_MODE      Inp_ExecutionMode      = MODE_FULL;     // 모드 설정  

input group "==== [계좌 보호 설정] ===="
input double            Inp_MaxFloatingLossPct = 10.0;    // 최대 허용 손실 비율          
input double            Inp_MaxRiskLot         = 30.0;    // 최대 허용 랏수           

input group "==== [포지션 사이징 설정] ===="
input double            Inp_DefaultRiskPct     = 1.0;           // 기본 리스크 비율 (%)      
input color             Inp_SLLineColor        = clrRed;        // 손절선 색상 설정   
input ENUM_LINE_STYLE   Inp_SLLineStyle        = STYLE_DASH;    // 손절선 스타일 설정 
input int               Inp_SLLineWidth        = 1;             // 손절선 굵기 설정
  

input group "==== [RR 도달 동작] ===="
input double            Inp_TargetRR          = 2.0;                // 목표 손익비 (최초 SL 기준, 0=자동 RR 끔)
input ENUM_RR_ACTION    Inp_RRAction          = RR_HALF_BREAKEVEN;  // RR 도달 동작

input group "==== [트레일링 설정] ===="
input double            Inp_Trail_ATR_Mult    = 2.0;                // 추격 ATR 배수 (0=서버 최소 허용거리)
input ENUM_TRAIL_UPDATE Inp_TrailUpdate       = TRAIL_BAR_CLOSE;    // SL 갱신 시점

input group "==== [모바일 원클릭 설정] ===="
input double            Inp_AutoSL_ATR_Mult    = 1.0;            // 최초 자동 SL ATR 배수 (추격 설정과 별개)

input group "==== [단축키 설정] ===="
input int               HotKey_Buy             = 1;               // BUY
input int               HotKey_Sell            = 2;               // SELL
input int               HotKeyHalf             = 3;               // 반익반본 

input int               HotKeyTrail            = 4;               // 트레일링 시작
input int               HotKeyCloseProfit      = 5;               // 수익청산
input int               HotKeyCloseAll         = 6;               // 전체청산

input string            HotKeySLunify          = "Q";             // SL 모으기
input string            HotKeySLup             = "W";             // SL 올리기
input string            HotKeySLdown           = "E";             // SL 내리기
input string            HotKeyTPunify          = "A";             // TP 모으기 
input string            HotKeyTPup             = "S";             // TP 올리기 
input string            HotKeyTPdown           = "D";             // TP 내리기

input group "==== [UI 및 표시 설정] ===="
input bool              Inp_ShowStatus         = false;           
   
//────────────────────────────────────────────────────────────
// 유틸리티 함수 (전역)
//────────────────────────────────────────────────────────────
int KeyCode(int n) { return 48 + n; }

void UpdateSymbolInfo() {
    int d = (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS);
    g_point = _Point;
    if(g_point <= 0.0) g_point = SymbolInfoDouble(_Symbol, SYMBOL_POINT);
    
    g_basePip = (d == 3 || d == 5) ? g_point * 10 : g_point;
    if(StringFind(_Symbol, "BTC") >= 0 || StringFind(_Symbol, "ETH") >= 0) g_basePip *= 100.0;
    
    g_tickSize = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
}

double Pip() { 
    if (g_basePip <= 0.0) UpdateSymbolInfo();
    return (g_basePip > 0.0) ? g_basePip : 0.00001; 
}

double MinStopDist() { 
    if (g_point <= 0.0) UpdateSymbolInfo();
    double pnt = (g_point > 0.0) ? g_point : 0.00001;
    
    double s = SymbolInfoInteger(_Symbol, SYMBOL_TRADE_STOPS_LEVEL) * pnt; 
    double f = SymbolInfoInteger(_Symbol, SYMBOL_TRADE_FREEZE_LEVEL) * pnt; 
    double spread = SymbolInfoInteger(_Symbol, SYMBOL_SPREAD) * pnt; 
    double minDist = MathMax(s, f);
    if(minDist <= 0.0) minDist = spread * 1.2;
    return minDist + pnt; 
}

// 자동 SL 거리: ATR 기반 거리를 확보할 수 없으면 0(SL 미부착)을 반환한다.
double AutoSLDistance(double minStop, double atr, double mult)
{
    if(!(atr > 0.0) || !(mult > 0.0)) return 0.0;
    return MathMax(minStop, atr * mult);
}


// ATR=0은 ATR 확보 여부와 무관하게 원본 MinStopDist를 그대로 사용한다.
double TrailDistance(double minStop, double atr, double mult)
{
    if(mult == 0.0) return minStop;
    if(!(atr > 0.0) || !(mult > 0.0)) return 0.0;
    return MathMax(minStop, atr * mult);
}

double RRPrice(long type, double entry, double initialSL, double rr)
{
    if(!(rr > 0.0) || !(initialSL > 0.0)) return 0.0;
    if((type == POSITION_TYPE_BUY && initialSL >= entry) ||
       (type == POSITION_TYPE_SELL && initialSL <= entry)) return 0.0;
    double risk = MathAbs(entry - initialSL);
    double target = (type == POSITION_TYPE_BUY) ? entry + risk * rr : entry - risk * rr;
    return target > 0.0 ? target : 0.0;
}

bool RRReached(long type, double entry, double initialSL, double currentPrice, double rr)
{
    double target = RRPrice(type, entry, initialSL, rr);
    if(!(target > 0.0)) return false;
    return (type == POSITION_TYPE_BUY) ? currentPrice >= target : currentPrice <= target;
}

double ExitTriggerPrice(long type, double entry, double initialSL, double liveTP, double rememberedTP, double rr)
{
    if(liveTP > 0.0) return 0.0;
    if(rememberedTP > 0.0) return rememberedTP;
    return RRPrice(type, entry, initialSL, rr);
}

bool ExitPriceReached(long type, double price, double target)
{
    if(!(target > 0.0)) return false;
    return (type == POSITION_TYPE_BUY) ? price >= target : price <= target;
}

bool TrailUpdateDue(bool realtime, bool barClosed, datetime startBar, datetime currentBar)
{
    return realtime || (barClosed && startBar < currentBar);
}

bool ATRFresh(datetime bar) { return bar > 0 && g_atrBarTime == bar && g_lastATR > 0.0; }

bool RefreshATR() {
    datetime bar = iTime(_Symbol, _Period, 0);
    if(ATRFresh(bar)) return true;
    ulong now = GetTickCount64();
    if(g_atrHandle == INVALID_HANDLE || bar <= 0 || now < g_atrRetryAt) return false;
    g_atrRetryAt = now + 100;
    double atr_val[1];
    if(CopyBuffer(g_atrHandle, 0, 1, 1, atr_val) == 1 && atr_val[0] > 0.0 &&
       iTime(_Symbol, _Period, 0) == bar) {
        g_lastATR = atr_val[0];
        g_atrBarTime = bar;
        return true;
    }
    return false;
}

double NormalizePrice(double price) {
    if(g_tickSize <= 0.0) UpdateSymbolInfo();
    if(g_tickSize > 0) return MathRound(price / g_tickSize) * g_tickSize;
    return price;
}


// SL은 서버 최소거리 안쪽으로 반올림하지 않는다. 0(삭제)은 그대로 유지한다.
double NormalizeSL(long type, double price) {
    if(price <= 0.0) return price;
    if(g_tickSize <= 0.0) UpdateSymbolInfo();
    if(g_tickSize <= 0.0) return price;
    double units = price / g_tickSize;
    double nearest = MathRound(units);
    if(MathAbs(units - nearest) < 1e-9) units = nearest;
    double aligned = (type == POSITION_TYPE_BUY ? MathFloor(units) : MathCeil(units)) * g_tickSize;
    return NormalizeDouble(aligned, (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS));
}

int VolumeDigits(double step) {
    for(int d=0; d<8; d++) if(MathAbs(step - NormalizeDouble(step, d)) < 1e-10) return d;
    return 8;
}

// 평균 진입가가 틱 격자 사이에 있으면 본절 목표는 손실이 없는 쪽으로 정렬한다.
double BreakevenPrice(long type, double entry) {
    return NormalizeSL(type == POSITION_TYPE_BUY ? POSITION_TYPE_SELL : POSITION_TYPE_BUY, entry);
}

double GetPriceBelowPanel() {
    int H = (int)ChartGetInteger(0, CHART_HEIGHT_IN_PIXELS);
    int W = (int)ChartGetInteger(0, CHART_WIDTH_IN_PIXELS);
    
    int targetY = H - 30; 
    if(targetY < 0) targetY = 0;
    
    int subWindow = 0;
    datetime time;
    double price = 0.0;
    
    if(ChartXYToTimePrice(0, W / 2, targetY, subWindow, time, price)) {
        return price;
    }
    
    double closePrice = iClose(_Symbol, _Period, 0);
    return closePrice - (50 * Pip());
}

// 같은 SL/TP 재전송 억제는 짧은 시간만 유지한다 (거절된 요청이 영구히 막히지 않도록).
#define SENT_DEDUP_MS 3000
bool SentDedupActive(ulong now, ulong sentTick) { return now - sentTick < SENT_DEDUP_MS; }

//====================================================================
// [OOP] 1. CExecutionManager
//====================================================================
// One outstanding SL/TP request per position. No timeout-based blind resend.
#define EXEC_TICKET_HASH_SIZE 1024
#define EXEC_SLTP_RETRY_MS 100

class CExecutionManager
{
private:
    struct SLTPExecution {
        ulong ticket;
        bool queuedSL, queuedTP, queuedTrail, retryTrail, queuedProtective, initialSL, initialTP;
        double nextSL, nextTP;
        uint slVersion, tpVersion, cancelVersion;
        bool inflight, acknowledged, applied, uncertain;
        uint requestID;
        double sentSL, sentTP;
        bool sentHadSL, sentHadTP, sentTrail, sentProtective, sentInitialSL, sentInitialTP;
        uint sentSLVersion, sentTPVersion, sentCancelVersion;
        ulong nextTryTime;
        int activeIndex;
    };
    SLTPExecution slots[MAX_TRACK_SIZE];
    int ticketHash[EXEC_TICKET_HASH_SIZE]; // -1 empty, -2 deleted
    int activeSlots[MAX_TRACK_SIZE], freeSlots[MAX_TRACK_SIZE];
    int activeCount, freeCount;

    struct CloseExecution { ulong ticket; ulong nextTryTime; int retries; };
    CloseExecution closeQueue[MAX_TRACK_SIZE];
    int closeQueueCount;

    int HashTicket(ulong ticket) {
        return (int)((ticket ^ (ticket >> 32) ^ (ticket >> 16)) & (EXEC_TICKET_HASH_SIZE - 1));
    }

    int FindSlot(ulong ticket, bool create = false) {
        if(ticket == 0) return -1;
        int start = HashTicket(ticket), firstDeleted = -1;
        for(int n=0; n<EXEC_TICKET_HASH_SIZE; n++) {
            int h = (start + n) & (EXEC_TICKET_HASH_SIZE - 1);
            int idx = ticketHash[h];
            if(idx >= 0) {
                if(slots[idx].ticket == ticket) return idx;
                continue;
            }
            if(idx == -2) { if(firstDeleted < 0) firstDeleted = h; continue; }
            if(!create || freeCount == 0) return -1;
            if(firstDeleted >= 0) h = firstDeleted;
            idx = freeSlots[--freeCount];
            ZeroMemory(slots[idx]);
            slots[idx].ticket = ticket;
            slots[idx].activeIndex = activeCount;
            activeSlots[activeCount++] = idx;
            ticketHash[h] = idx;
            return idx;
        }
        if(!create || firstDeleted < 0 || freeCount == 0) return -1;
        int idx = freeSlots[--freeCount];
        ZeroMemory(slots[idx]);
        slots[idx].ticket = ticket;
        slots[idx].activeIndex = activeCount;
        activeSlots[activeCount++] = idx;
        ticketHash[firstDeleted] = idx;
        return idx;
    }

    void RemoveSlot(int idx) {
        int start = HashTicket(slots[idx].ticket);
        for(int n=0; n<EXEC_TICKET_HASH_SIZE; n++) {
            int h = (start + n) & (EXEC_TICKET_HASH_SIZE - 1);
            if(ticketHash[h] == idx) { ticketHash[h] = -2; break; }
            if(ticketHash[h] == -1) break;
        }
        int a = slots[idx].activeIndex;
        int moved = activeSlots[--activeCount];
        if(a < activeCount) { activeSlots[a] = moved; slots[moved].activeIndex = a; }
        freeSlots[freeCount++] = idx;
        ZeroMemory(slots[idx]);
        // Avoid accumulated tombstones when there are no pending requests.
        if(activeCount == 0) ArrayInitialize(ticketHash, -1);
    }

    bool SamePrice(double a, double b) {
        double eps = MathMax(g_point * 0.25, g_tickSize * 0.000001);
        return MathAbs(a-b) <= eps;
    }

    bool AppliedToPosition(int idx) {
        if(!PositionSelectByTicket(slots[idx].ticket)) return false;
        return (!slots[idx].sentHadSL || SamePrice(PositionGetDouble(POSITION_SL), slots[idx].sentSL)) &&
               (!slots[idx].sentHadTP || SamePrice(PositionGetDouble(POSITION_TP), slots[idx].sentTP));
    }

    bool GoalSatisfiedAfterAck(int idx) {
        // Only a final successful response permits purpose-based completion.
        // For uncertain/time-out requests the exact-match rule remains intact.
        if(!slots[idx].acknowledged || !PositionSelectByTicket(slots[idx].ticket)) return false;
        double sl=PositionGetDouble(POSITION_SL);
        long type=PositionGetInteger(POSITION_TYPE);
        bool slDone=!slots[idx].sentHadSL || SamePrice(sl, slots[idx].sentSL);
        if(slots[idx].sentHadSL && slots[idx].sentInitialSL) slDone=sl > 0.0;
        else if(slots[idx].sentHadSL && slots[idx].sentProtective)
            slDone=sl > 0.0 && (SamePrice(sl, slots[idx].sentSL) || BetterSL(type, sl, slots[idx].sentSL));
        bool tpCancelled=slots[idx].sentInitialTP && slots[idx].sentTPVersion != slots[idx].tpVersion;
        bool tpDone=!slots[idx].sentHadTP || tpCancelled || SamePrice(PositionGetDouble(POSITION_TP), slots[idx].sentTP);
        return slDone && tpDone;
    }

    bool BetterSL(long type, double candidate, double reference) {
        if(candidate <= 0.0) return false;
        if(reference <= 0.0) return true;
        return type == POSITION_TYPE_BUY ? candidate > reference : candidate < reference;
    }

    void CompleteFlight(int idx) {
        slots[idx].inflight = false;
        slots[idx].acknowledged = false;
        slots[idx].applied = false;
        slots[idx].uncertain = false;
        slots[idx].requestID = 0;
    }

    void RejectFlight(int idx) {
        // Retain a retry candidate for quiet markets and bar-close mode, but
        // exclude rejected high-water marks from the comparison reference.
        if(slots[idx].sentTrail) {
            if(slots[idx].cancelVersion == slots[idx].sentCancelVersion) {
                if(!slots[idx].queuedSL) {
                    slots[idx].queuedSL=true;
                    slots[idx].queuedTrail=true;
                    slots[idx].queuedProtective=false; slots[idx].initialSL=false;
                    slots[idx].nextSL=slots[idx].sentSL;
                }
                if(slots[idx].queuedTrail) slots[idx].retryTrail=true;
            }
        } else if(slots[idx].sentHadSL && !slots[idx].queuedSL &&
                  slots[idx].slVersion == slots[idx].sentSLVersion) {
            slots[idx].queuedSL = true;
            slots[idx].queuedTrail = false;
            slots[idx].retryTrail = false;
            slots[idx].queuedProtective=slots[idx].sentProtective;
            slots[idx].initialSL=slots[idx].sentInitialSL;
            slots[idx].nextSL = slots[idx].sentSL;
        }
        if(slots[idx].sentHadTP && !slots[idx].queuedTP &&
           slots[idx].tpVersion == slots[idx].sentTPVersion) {
            slots[idx].queuedTP = true;
            slots[idx].initialTP = slots[idx].sentInitialTP;
            slots[idx].nextTP = slots[idx].sentTP;
        }
        CompleteFlight(idx);
        slots[idx].nextTryTime = GetTickCount64() + EXEC_SLTP_RETRY_MS;
    }

public:
    CExecutionManager() {
        closeQueueCount=0; activeCount=0; freeCount=MAX_TRACK_SIZE;
        ArrayInitialize(ticketHash, -1);
        for(int i=0; i<MAX_TRACK_SIZE; i++) freeSlots[i]=MAX_TRACK_SIZE-1-i;
    }

    bool HasPendingClose() { return closeQueueCount > 0; }
    bool HasPendingSLTP() { return activeCount > 0; }

    bool IsInQueue(ulong ticket) {
        int idx=FindSlot(ticket);
        return idx >= 0 && (slots[idx].queuedSL || slots[idx].queuedTP || slots[idx].inflight);
    }

    bool IsOwnSLOnlyRequest(uint requestID) {
        if(requestID == 0) return false;
        for(int a=0; a<activeCount; a++) {
            int idx=activeSlots[a];
            if(slots[idx].inflight && slots[idx].requestID == requestID)
                return slots[idx].sentHadSL && !slots[idx].sentHadTP;
        }
        return false;
    }

    bool IsOwnRequestID(uint requestID) {
        if(requestID == 0) return false;
        for(int a=0; a<activeCount; a++) {
            int idx=activeSlots[a];
            if(slots[idx].inflight && slots[idx].requestID == requestID) return true;
        }
        return false;
    }

    bool IsOwnSLOnlyPositionEvent(const MqlTradeTransaction &trans) {
        if(trans.type != TRADE_TRANSACTION_POSITION || trans.symbol != _Symbol) return false;
        int idx=FindSlot(trans.position);
        return idx >= 0 && slots[idx].inflight && slots[idx].sentHadSL && !slots[idx].sentHadTP &&
               SamePrice(trans.price_sl, slots[idx].sentSL) && SamePrice(trans.price_tp, slots[idx].sentTP);
    }

    // Compatibility only: callbacks are correlated by request_id in
    // OnTransaction. Never release an outstanding request by ticket alone.
    void ForgetSent(ulong ticket) { }

    double EffectiveTrailSL(ulong ticket, long type, double currentSL) {
        int idx=FindSlot(ticket);
        if(idx < 0) return currentSL;
        double effective=currentSL;
        if(slots[idx].inflight && slots[idx].sentHadSL && slots[idx].sentTrail &&
           BetterSL(type, slots[idx].sentSL, effective)) effective=slots[idx].sentSL;
        if(slots[idx].queuedSL && slots[idx].queuedTrail && !slots[idx].retryTrail &&
           BetterSL(type, slots[idx].nextSL, effective)) effective=slots[idx].nextSL;
        return effective;
    }

    void RequestSL(ulong ticket, double newSL) {
        if(!PositionSelectByTicket(ticket) || PositionGetString(POSITION_SYMBOL) != _Symbol) return;
        int idx=FindSlot(ticket, true); if(idx < 0) return;
        slots[idx].queuedSL=true; slots[idx].queuedTrail=false; slots[idx].retryTrail=false;
        slots[idx].queuedProtective=false; slots[idx].initialSL=false;
        slots[idx].nextSL=NormalizeSL(PositionGetInteger(POSITION_TYPE), newSL);
        slots[idx].slVersion++;
    }

    void RequestProtectiveSL(ulong ticket, double newSL) {
        if(!PositionSelectByTicket(ticket) || PositionGetString(POSITION_SYMBOL) != _Symbol) return;
        long type=PositionGetInteger(POSITION_TYPE);
        double cleanSL=NormalizeSL(type, newSL);
        int idx=FindSlot(ticket, true); if(idx < 0) return;
        if(slots[idx].queuedSL) {
            // An explicit manual SL command wins over an automatic protector.
            if(!slots[idx].queuedTrail && !slots[idx].queuedProtective && !slots[idx].initialSL) return;
            if((slots[idx].queuedTrail || slots[idx].queuedProtective) &&
               !BetterSL(type, cleanSL, slots[idx].nextSL)) return;
        }
        RequestSL(ticket, cleanSL);
        slots[idx].queuedProtective=true;
    }

    void RequestInitialSL(ulong ticket, double newSL) {
        if(!PositionSelectByTicket(ticket) || PositionGetString(POSITION_SYMBOL) != _Symbol ||
           PositionGetDouble(POSITION_SL) > 0.0) return;
        int idx=FindSlot(ticket, true); if(idx < 0) return;
        // Initial protection must not replace a later manual/BE/trail command.
        if(slots[idx].queuedSL && !slots[idx].initialSL) return;
        RequestSL(ticket, newSL);
        slots[idx].initialSL=true;
    }

    void RequestTrailSL(ulong ticket, double newSL) {
        if(!PositionSelectByTicket(ticket) || PositionGetString(POSITION_SYMBOL) != _Symbol) return;
        long type=PositionGetInteger(POSITION_TYPE);
        double cleanSL=NormalizeSL(type, newSL);
        int idx=FindSlot(ticket, true); if(idx < 0) return;
        // A deliberate manual/BE request has priority over a trailing update.
        if(slots[idx].queuedSL && !slots[idx].queuedTrail) return;
        double effective=PositionGetDouble(POSITION_SL);
        if(slots[idx].inflight && slots[idx].sentHadSL && slots[idx].sentTrail &&
           BetterSL(type, slots[idx].sentSL, effective)) effective=slots[idx].sentSL;
        if(slots[idx].queuedSL && slots[idx].queuedTrail && !slots[idx].retryTrail &&
           BetterSL(type, slots[idx].nextSL, effective)) effective=slots[idx].nextSL;
        if(!BetterSL(type, cleanSL, effective)) return;
        slots[idx].queuedSL=true; slots[idx].queuedTrail=true; slots[idx].retryTrail=false;
        slots[idx].queuedProtective=false; slots[idx].initialSL=false;
        slots[idx].nextSL=cleanSL; slots[idx].slVersion++;
    }

    void RequestTP(ulong ticket, double newTP) {
        int idx=FindSlot(ticket, true); if(idx < 0) return;
        slots[idx].queuedTP=true; slots[idx].initialTP=false;
        slots[idx].nextTP=NormalizePrice(newTP); slots[idx].tpVersion++;
    }

    void RequestInitialTP(ulong ticket, double newTP) {
        if(!PositionSelectByTicket(ticket) || PositionGetString(POSITION_SYMBOL) != _Symbol ||
           PositionGetDouble(POSITION_TP) > 0.0) return;
        int idx=FindSlot(ticket, true); if(idx < 0) return;
        if(slots[idx].queuedTP && !slots[idx].initialTP) return;
        slots[idx].queuedTP=true; slots[idx].initialTP=true;
        slots[idx].nextTP=NormalizePrice(newTP); slots[idx].tpVersion++;
    }

    void CancelInitialTP(ulong ticket) {
        int idx=FindSlot(ticket); if(idx < 0) return;
        if(slots[idx].queuedTP && slots[idx].initialTP) slots[idx].queuedTP=false;
        // A manual TP takes precedence even if it is deleted before the next
        // tick. Invalidate initial-TP retry eligibility without releasing an
        // existing inflight request or altering any live server TP.
        if(slots[idx].initialTP || (slots[idx].inflight && slots[idx].sentInitialTP))
            slots[idx].tpVersion++;
    }

    void CancelSLRequest(ulong ticket) {
        int idx=FindSlot(ticket); if(idx < 0) return;
        slots[idx].queuedSL=false; slots[idx].queuedTrail=false; slots[idx].retryTrail=false;
        slots[idx].queuedProtective=false; slots[idx].initialSL=false;
        slots[idx].slVersion++;
        slots[idx].cancelVersion++;
        // Existing inflight must finish before any replacement may be sent.
    }

    void OnTransaction(const MqlTradeTransaction &trans, const MqlTradeRequest &request, const MqlTradeResult &result) {
        if(trans.type == TRADE_TRANSACTION_POSITION) {
            int idx=FindSlot(trans.position);
            if(idx >= 0 && slots[idx].inflight &&
               (!slots[idx].sentHadSL || SamePrice(trans.price_sl, slots[idx].sentSL)) &&
               (!slots[idx].sentHadTP || SamePrice(trans.price_tp, slots[idx].sentTP))) slots[idx].applied=true;
            return;
        }
        if(trans.type != TRADE_TRANSACTION_REQUEST || request.action != TRADE_ACTION_SLTP) return;
        int idx=FindSlot(request.position);
        if(idx < 0 || !slots[idx].inflight || result.request_id != slots[idx].requestID) return;
        if(result.retcode == TRADE_RETCODE_PLACED) return;
        if(result.retcode == TRADE_RETCODE_DONE || result.retcode == TRADE_RETCODE_DONE_PARTIAL ||
           result.retcode == TRADE_RETCODE_NO_CHANGES) {
            slots[idx].acknowledged=true;
            if(AppliedToPosition(idx)) slots[idx].applied=true;
            return;
        }
        if(result.retcode == TRADE_RETCODE_TIMEOUT) {
            // A missing/timeout response cannot prove the request was not
            // applied. Wait for position confirmation; do not resend blindly.
            slots[idx].uncertain=true;
            if(AppliedToPosition(idx)) slots[idx].applied=true;
            PrintFormat("[SL/TP 응답 불확정] Ticket=%llu Request=%u: 포지션 반영 확인 대기", request.position, result.request_id);
            return;
        }
        PrintFormat("[SL/TP 거절] Ticket=%llu Request=%u Retcode=%u: 최신 목표 재계산", request.position, result.request_id, result.retcode);
        RejectFlight(idx);
    }

    void ExecuteQueue() {
        if(!TerminalInfoInteger(TERMINAL_CONNECTED)) return;
        if(activeCount == 0) return;
        ulong now=GetTickCount64();
        MqlTick tick; ZeroMemory(tick); bool quoteRead=false, haveTick=false;
        double minStop=0.0;
        for(int a=0; a<activeCount; a++) {
            int idx=activeSlots[a]; ulong t=slots[idx].ticket;
            if(!PositionSelectByTicket(t) || PositionGetString(POSITION_SYMBOL) != _Symbol) { RemoveSlot(idx); a--; continue; }
            if(slots[idx].inflight) {
                if((slots[idx].acknowledged || slots[idx].uncertain) &&
                   (slots[idx].applied || AppliedToPosition(idx))) CompleteFlight(idx);
                else if(GoalSatisfiedAfterAck(idx)) CompleteFlight(idx);
                else if(slots[idx].acknowledged &&
                        slots[idx].cancelVersion != slots[idx].sentCancelVersion) CompleteFlight(idx);
                else continue;
            }
            if(!slots[idx].queuedSL && !slots[idx].queuedTP) { RemoveSlot(idx); a--; continue; }
            if(now < slots[idx].nextTryTime) continue;

            // Select again after state checks; preserve the TP currently on
            // the server for every SL-only request.
            if(!PositionSelectByTicket(t)) { RemoveSlot(idx); a--; continue; }
            double curSL=PositionGetDouble(POSITION_SL), curTP=PositionGetDouble(POSITION_TP);
            long type=PositionGetInteger(POSITION_TYPE);
            if(slots[idx].queuedTP && slots[idx].initialTP && curTP > 0.0) slots[idx].queuedTP=false;
            if(slots[idx].queuedSL &&
               ((slots[idx].initialSL && curSL > 0.0) ||
                ((slots[idx].queuedTrail || slots[idx].queuedProtective) &&
                 !BetterSL(type, slots[idx].nextSL, curSL)))) {
                slots[idx].queuedSL=false; slots[idx].queuedTrail=false; slots[idx].retryTrail=false;
                slots[idx].queuedProtective=false; slots[idx].initialSL=false;
            }
            double finalSL=slots[idx].queuedSL ? slots[idx].nextSL : curSL;
            double finalTP=slots[idx].queuedTP ? slots[idx].nextTP : curTP;
            if(SamePrice(curSL, finalSL) && SamePrice(curTP, finalTP)) {
                slots[idx].queuedSL=false; slots[idx].queuedTP=false;
                RemoveSlot(idx); a--; continue;
            }
            if(!quoteRead) {
                haveTick=SymbolInfoTick(_Symbol, tick); quoteRead=true;
                if(haveTick) minStop=MinStopDist();
            }
            if(!haveTick) continue;
            // Keep an invalid candidate for later recovery without letting
            // its high-water mark block a lower fresh valid candidate.
            if(slots[idx].queuedSL && finalSL > 0.0 &&
               ((type == POSITION_TYPE_BUY && finalSL > tick.bid-minStop+g_point*0.01) ||
                (type == POSITION_TYPE_SELL && finalSL < tick.ask+minStop-g_point*0.01))) {
                if(slots[idx].queuedTrail) slots[idx].retryTrail=true;
                slots[idx].nextTryTime=now+EXEC_SLTP_RETRY_MS;
                continue;
            }

            MqlTradeRequest r; MqlTradeResult s; ZeroMemory(r); ZeroMemory(s);
            r.action=TRADE_ACTION_SLTP; r.position=t; r.symbol=_Symbol; r.sl=finalSL; r.tp=finalTP;
            if(!OrderSendAsync(r,s)) {
                slots[idx].nextTryTime=now+EXEC_SLTP_RETRY_MS;
                Print("ExecManager Async SLTP Send Failed: Error ", GetLastError());
                continue; // no sent record; latest queued target survives
            }
            slots[idx].inflight=true; slots[idx].acknowledged=false;
            slots[idx].applied=false; slots[idx].uncertain=false;
            slots[idx].requestID=s.request_id;
            slots[idx].sentSL=finalSL; slots[idx].sentTP=finalTP;
            slots[idx].sentHadSL=slots[idx].queuedSL; slots[idx].sentHadTP=slots[idx].queuedTP;
            slots[idx].sentTrail=slots[idx].queuedSL && slots[idx].queuedTrail;
            slots[idx].sentProtective=slots[idx].queuedSL && slots[idx].queuedProtective;
            slots[idx].sentInitialSL=slots[idx].queuedSL && slots[idx].initialSL;
            slots[idx].sentInitialTP=slots[idx].queuedTP && slots[idx].initialTP;
            slots[idx].sentSLVersion=slots[idx].slVersion; slots[idx].sentTPVersion=slots[idx].tpVersion;
            slots[idx].sentCancelVersion=slots[idx].cancelVersion;
            slots[idx].queuedSL=false; slots[idx].queuedTP=false;
            slots[idx].queuedTrail=false; slots[idx].retryTrail=false;
            slots[idx].queuedProtective=false; slots[idx].initialSL=false;
        }
    }

    void RequestClose(ulong ticket) {
        for(int i=0; i<closeQueueCount; i++) {
            if(closeQueue[i].ticket == ticket) return;
        }
        if(closeQueueCount < MAX_TRACK_SIZE) {
            closeQueue[closeQueueCount].ticket = ticket;
            closeQueue[closeQueueCount].nextTryTime = 0;
            closeQueue[closeQueueCount].retries = 0;
            closeQueueCount++;
        }
    }

    void ProcessCloseQueue() {
        if(closeQueueCount == 0) return;
        ulong now = GetTickCount64();
        for(int i = 0; i < closeQueueCount; i++) {
            ulong t = closeQueue[i].ticket;
            if(!PositionSelectByTicket(t)) { RemoveFromCloseQueue(i); i--; continue; }
            if(now >= closeQueue[i].nextTryTime) {
                SendCloseOrderAsync(t);
                closeQueue[i].nextTryTime = now + 500;
                closeQueue[i].retries++;
                if(closeQueue[i].retries > 20) {
                    Print("Close Failed permanently for ticket: ", t);
                    RemoveFromCloseQueue(i); i--;
                }
            }
        }
    }

    void RemoveFromCloseQueue(int index) {
        for(int i = index; i < closeQueueCount - 1; i++) closeQueue[i] = closeQueue[i+1];
        closeQueueCount--;
    }

    void SendCloseOrderAsync(ulong ticket) {
        if(!PositionSelectByTicket(ticket) || PositionGetString(POSITION_SYMBOL) != _Symbol) return;
        MqlTradeRequest r; MqlTradeResult s; ZeroMemory(r); ZeroMemory(s);
        r.action=TRADE_ACTION_DEAL; r.symbol=PositionGetString(POSITION_SYMBOL); r.volume=PositionGetDouble(POSITION_VOLUME);
        r.position=ticket; r.type_filling=ORDER_FILLING_IOC;
        long spread = SymbolInfoInteger(_Symbol, SYMBOL_SPREAD);
        r.deviation = (ulong)spread + 50;
        long type=PositionGetInteger(POSITION_TYPE);
        MqlTick tick;
        if(SymbolInfoTick(_Symbol, tick)) {
            if(type==POSITION_TYPE_BUY){ r.type=ORDER_TYPE_SELL; r.price=tick.bid; }
            else { r.type=ORDER_TYPE_BUY; r.price=tick.ask; }
            if(!OrderSendAsync(r,s)) Print("CloseAsync Send Failed: Error ", GetLastError());
        }
    }

    void CloseAll() {
        for(int i=PositionsTotal()-1; i>=0; i--) {
            ulong t=PositionGetTicket(i);
            if(PositionSelectByTicket(t) && PositionGetString(POSITION_SYMBOL) == _Symbol) {
                long magic = PositionGetInteger(POSITION_MAGIC);
                if(!IsValidMagic(magic)) continue;
                RequestClose(t);
            }
        }
        ProcessCloseQueue();
    }

    void SendOrderRaw(int type, long magic, double volume, double price, double sl) {
        MqlTradeRequest r; MqlTradeResult s; ZeroMemory(r); ZeroMemory(s);
        r.action=TRADE_ACTION_DEAL; r.symbol=_Symbol;
        r.type=(ENUM_ORDER_TYPE)type; r.type_filling=ORDER_FILLING_IOC;
        r.magic=magic; r.volume=volume;
        r.price=NormalizePrice(price); r.sl=NormalizeSL(type, sl);
        if(Inp_RRAction == RR_TAKE_PROFIT)
            r.tp=NormalizePrice(RRPrice(type, r.price, r.sl, Inp_TargetRR));
        PrintFormat("🔎 [TRACE/MT5 ORDER SEND] Symbol=%s | Type=%d | Magic=%lld | Volume=%.8f | RequestedPrice=%.10f | RequestedSL=%.10f | NormalizedPrice=%.10f | NormalizedSL=%.10f",
                    _Symbol, type, magic, volume, price, sl, r.price, r.sl);
        if(!OrderSendAsync(r,s)) Print("SendOrderRaw Failed: Error ", GetLastError());
    }
};;

// 리스크 금액으로 랏을 계산한다. 최소 랏 미만이면 최소 랏으로 올리지 않고 0(진입 금지)을 반환한다.
double LotFromRisk(double riskAmount, double lossPerOneLot, double minLot, double maxLot, double stepLot, double maxRiskLot)
{
    if(!(riskAmount > 0.0) || !(lossPerOneLot > 0.0) || !(maxLot > 0.0) || !(maxRiskLot > 0.0)) return 0.0;
    if(stepLot <= 0.0) stepLot = 0.01;
    double cap = MathMin(riskAmount / lossPerOneLot, MathMin(maxLot, maxRiskLot));
    double lot = NormalizeDouble(MathFloor(cap / stepLot + 1e-9) * stepLot, VolumeDigits(stepLot));
    // 부동소수점 보정 때문에 리스크/랏 상한을 넘지 않게 재검증한다.
    if(lot > cap + stepLot * 1e-9) lot = NormalizeDouble(lot - stepLot, VolumeDigits(stepLot));
    if(lot <= 0.0 || lot + stepLot * 1e-9 < minLot) return 0.0;
    return lot;
}

//====================================================================
// [OOP] 2. CRiskManager
//====================================================================
class CRiskManager
{
public:
    double CalculateLotSize(double entryPrice, double slPrice, bool quiet = false) {
        long positionType = (entryPrice > slPrice) ? POSITION_TYPE_BUY : POSITION_TYPE_SELL;
        slPrice = NormalizeSL(positionType, slPrice);
        if(slPrice == 0.0 || entryPrice == slPrice) return 0.0;
        
        double minLot = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
        double maxLot = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);
        double stepLot = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
        
        if(stepLot <= 0.0) stepLot = 0.01; 
        
        string riskStr = ObjectGetString(0, EDIT_RISK, OBJPROP_TEXT);
        double riskPct = StringToDouble(riskStr);
        if(riskPct <= 0) riskPct = 1.0;

        double balance = AccountInfoDouble(ACCOUNT_BALANCE);
        double riskAmount = balance * (riskPct / 100.0);

        ENUM_ORDER_TYPE orderType = (entryPrice > slPrice) ? ORDER_TYPE_BUY : ORDER_TYPE_SELL;
        double profitForOneLot = 0.0;
        
        ResetLastError();
        if(!OrderCalcProfit(orderType, _Symbol, 1.0, entryPrice, slPrice, profitForOneLot)) {
            if(!quiet) Print("OrderCalcProfit 계산 오류: ", GetLastError(), ". 기존 계산 방식으로 대체합니다.");
            
            double tickValue = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE);
            double tickSize = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
            double slDistance = MathAbs(entryPrice - slPrice);
            
            if(slDistance > 0 && tickSize > 0 && tickValue > 0) {
                profitForOneLot = -((slDistance / tickSize) * tickValue);
            } else {
                return 0.0;
            }
        }
        
        double lossPerOneLot = MathAbs(profitForOneLot);
        double lot = LotFromRisk(riskAmount, lossPerOneLot, minLot, maxLot, stepLot, Inp_MaxRiskLot);
        if(lot <= 0.0 && !quiet)
            PrintFormat("[진입 금지] 리스크 기준 랏이 최소 랏(%.2f) 미만입니다. Risk=%.2f | LossPerLot=%.2f", minLot, riskAmount, lossPerOneLot);
        return lot;
    }
};

//────────────────────────────────────────────────────────────
// 포지션 상태 영속화: 반익반본 완료 여부(H)와 최초 SL(I)을 터미널 전역변수에 저장한다.
// EA/터미널 재시작 후 옮겨진 SL을 최초 SL로 오인해 반익반본이 중복 실행되는 것을 막는다.
//────────────────────────────────────────────────────────────
string PosStatePrefix() { return "OZDS_" + IntegerToString(AccountInfoInteger(ACCOUNT_LOGIN)) + "_"; }
string PosStateKey(ulong ticket, string kind) { return PosStatePrefix() + IntegerToString((long)ticket) + "_" + kind; }

void SavePosState(ulong ticket, string kind, double value) {
    string key = PosStateKey(ticket, kind);
    if(GlobalVariableCheck(key) && GlobalVariableGet(key) == value) return;
    if(GlobalVariableSet(key, value) == 0) Print("포지션 상태 저장 실패: ", key, " Error ", GetLastError());
    else g_posStatesDirty = true;
}

void FlushPosStates() {
    if(!g_posStatesDirty) return;
    GlobalVariablesFlush();
    g_posStatesDirty = false;
}

bool LoadPosState(ulong ticket, string kind, double &value) {
    string key = PosStateKey(ticket, kind);
    if(!GlobalVariableCheck(key)) return false;
    value = GlobalVariableGet(key);
    return true;
}

void DeletePosState(ulong ticket) {
    g_posStatesDirty = true;
    GlobalVariableDel(PosStateKey(ticket, "H"));
    GlobalVariableDel(PosStateKey(ticket, "I"));
    GlobalVariableDel(PosStateKey(ticket, "T"));
    GlobalVariableDel(PosStateKey(ticket, "B"));
    GlobalVariableDel(PosStateKey(ticket, "P"));
    GlobalVariableDel(PosStateKey(ticket, "M"));
    GlobalVariableDel(PosStateKey(ticket, "D"));
}

// EA가 꺼져 있는 동안 청산된 포지션의 상태 정리 (계좌 연결 후에만 호출)
void CleanOrphanPosStates() {
    string prefix = PosStatePrefix();
    for(int i = GlobalVariablesTotal() - 1; i >= 0; i--) {
        string name = GlobalVariableName(i);
        if(StringFind(name, prefix) != 0) continue;
        string rest = StringSubstr(name, StringLen(prefix));
        int sep = StringFind(rest, "_");
        if(sep <= 0) continue;
        ulong ticket = (ulong)StringToInteger(StringSubstr(rest, 0, sep));
        if(ticket > 0 && !PositionSelectByTicket(ticket)) GlobalVariableDel(name);
    }
}

// 재시작 복원 규칙 (순수 함수: 회귀테스트 대상)
double RestoreInitialSL(bool saved, double savedSL, double currentSL) { return (saved && savedSL > 0.0) ? savedSL : currentSL; }
bool   RestoreHalfDone(bool saved, double savedFlag) { return saved && savedFlag > 0.5; }

//====================================================================
// [OOP] 3. CPositionManager
//====================================================================
class CPositionManager
{
private:
    struct PosMemory { 
        ulong ticket; 
        double tpPrice; 
        double slPrice; 
        bool isAutoHalfDone;  
        double initialSL;     
        bool isRROverridden;
        bool isManualTrail;
        bool isTrailStopped;
        datetime trailStartBar;
        uint lastTPRequestID;
        bool tpSetupDone;
        bool deletedTPBETriggered;
    };
    PosMemory posMem[MAX_TRACK_SIZE];
    int       posMemCount;
    ulong     trailingList[MAX_TRACK_SIZE];
    int       trailingCount;
    ulong     trailLockUntil;
    int       memIndex[1024];

    int FindMem(ulong ticket) {
        int bucket = (int)(ticket % 1024);
        for(int n=0; n<1024; n++) {
            int i = memIndex[bucket];
            if(i < 0) return -1;
            if(posMem[i].ticket == ticket) return i;
            bucket = (bucket + 1) % 1024;
        }
        return -1;
    }

    void IndexMem(int i) {
        int bucket = (int)(posMem[i].ticket % 1024);
        while(memIndex[bucket] >= 0) bucket = (bucket + 1) % 1024;
        memIndex[bucket] = i;
    }

    void RebuildMemIndex() {
        ArrayInitialize(memIndex, -1);
        for(int i=0; i<posMemCount; i++) IndexMem(i);
    }

public:
    CPositionManager() { posMemCount=0; trailingCount=0; trailLockUntil=0; ArrayInitialize(trailingList,0); ArrayInitialize(memIndex,-1); }

    bool IsTracked(ulong ticket) { return FindMem(ticket) >= 0; }

    bool UpdatePosMemory(ulong ticket, double tp, double sl, CExecutionManager &exec) {
        bool manualSLDeleted = false;
        
        int i = FindMem(ticket); {
            if(i >= 0) {
                if(tp > 0) {
                    if(posMem[i].tpPrice != tp || posMem[i].deletedTPBETriggered) {
                        posMem[i].tpPrice = tp;
                        posMem[i].deletedTPBETriggered = false;
                        SavePosState(ticket, "M", tp);
                        SavePosState(ticket, "D", 0.0);
                    }
                    if(!posMem[i].tpSetupDone) { posMem[i].tpSetupDone = true; SavePosState(ticket, "P", 1.0); }
                } 
                
                if(posMem[i].slPrice > 0 && sl == 0) {
                    if(Inp_ExecutionMode == MODE_FULL) {
                        manualSLDeleted = true; 
                    }
                }
                
                if(sl > 0) {
                    if(posMem[i].initialSL <= 0) { posMem[i].initialSL = sl; SavePosState(ticket, "I", sl); }
                }

                posMem[i].slPrice = sl;
                return manualSLDeleted;
            }
        }

        if(posMemCount < MAX_TRACK_SIZE) {
            double savedI = 0.0, savedH = 0.0;
            bool hasI = LoadPosState(ticket, "I", savedI);
            bool hasH = LoadPosState(ticket, "H", savedH);
            posMem[posMemCount].ticket = ticket; posMem[posMemCount].tpPrice = tp;
            posMem[posMemCount].slPrice = sl; posMem[posMemCount].isAutoHalfDone = RestoreHalfDone(hasH, savedH);
            posMem[posMemCount].initialSL = RestoreInitialSL(hasI, savedI, sl);
            double savedT = 0.0, savedB = 0.0, savedP = 0.0, savedM = 0.0, savedD = 0.0;
            LoadPosState(ticket, "T", savedT);
            LoadPosState(ticket, "B", savedB);
            LoadPosState(ticket, "P", savedP);
            LoadPosState(ticket, "M", savedM);
            LoadPosState(ticket, "D", savedD);
            // 수동 추격은 슬롯과 무관. 자동 추격은 MAIN + TRAIL 슬롯에서만 복원.
            if(savedT == 2.0 && (Inp_ExecutionMode != MODE_FULL || Inp_RRAction != RR_TRAILING)) {
                savedT = 0.0;
                SavePosState(ticket, "T", 0.0);
                SavePosState(ticket, "B", 0.0);
            }
            bool restoreTrail = savedT == 1.0 || (savedT == 2.0 && Inp_ExecutionMode == MODE_FULL && Inp_RRAction == RR_TRAILING);
            posMem[posMemCount].isManualTrail = restoreTrail;
            posMem[posMemCount].isTrailStopped = savedT < 0.0 || (savedT == 2.0 && Inp_ExecutionMode != MODE_FULL);
            posMem[posMemCount].trailStartBar = (datetime)savedB;
            posMem[posMemCount].tpSetupDone = tp > 0.0 || savedP > 0.5;
            posMem[posMemCount].tpPrice = tp > 0.0 ? tp : savedM;
            posMem[posMemCount].deletedTPBETriggered = tp <= 0.0 && savedD > 0.5;
            if(tp > 0.0 && tp != savedM) SavePosState(ticket, "M", tp);
            if(tp > 0.0 && savedD > 0.5) SavePosState(ticket, "D", 0.0);
            posMem[posMemCount].isRROverridden = savedT != 0.0;
            if(tp > 0.0 && savedP < 0.5) SavePosState(ticket, "P", 1.0);
            posMem[posMemCount].lastTPRequestID = 0;
            IndexMem(posMemCount);
            posMemCount++;
            if(!hasI && sl > 0) SavePosState(ticket, "I", sl);
        }
        return false;
    }

    double GetSLMemory(ulong ticket) { int i=FindMem(ticket); return i >= 0 ? posMem[i].slPrice : 0.0; }

    double GetInitialSL(ulong ticket) {
        int i = FindMem(ticket); if(i >= 0) return posMem[i].initialSL;
        return 0.0;
    }

    bool IsAutoHalfDone(ulong ticket) {
        int i = FindMem(ticket); if(i >= 0) return posMem[i].isAutoHalfDone;
        return false;
    }

    void SetAutoHalfDone(ulong ticket, bool state) {
        int i = FindMem(ticket); {
            if(i >= 0) { posMem[i].isAutoHalfDone = state; SavePosState(ticket, "H", state ? 1.0 : 0.0); }
        }
    }

    double GetTPMemory(ulong ticket) { int i = FindMem(ticket); if(i >= 0) return posMem[i].tpPrice; return 0.0; }

    bool IsRROverridden(ulong ticket) { int i = FindMem(ticket); if(i >= 0) return posMem[i].isRROverridden; return false; }
    void SetRROverridden(ulong ticket) { int i = FindMem(ticket); if(i >= 0) posMem[i].isRROverridden = true; }

    void CleanMemory() {
        if(!TerminalInfoInteger(TERMINAL_CONNECTED)) return;
        int activePos = 0;
        for(int i=0; i<posMemCount; i++) {
            if(PositionSelectByTicket(posMem[i].ticket)) posMem[activePos++] = posMem[i];
            else DeletePosState(posMem[i].ticket);
        }
        posMemCount = activePos;
        RebuildMemIndex();

        int activeTrail = 0;
        for(int i=0; i<trailingCount; i++) if(PositionSelectByTicket(trailingList[i]) && PositionGetString(POSITION_SYMBOL) == _Symbol) trailingList[activeTrail++] = trailingList[i];
        trailingCount = activeTrail;
    }
    void TrailLockSet(int ms) { trailLockUntil = (ms<=0) ? 0 : GetTickCount()+(ulong)ms; }

    bool TrailLocked() { if(trailLockUntil==0) return false; if(GetTickCount()>=trailLockUntil) { trailLockUntil=0; return false; } return true; }

    void SetTrailStopped(ulong ticket, bool state) {
        int i = FindMem(ticket); {
            if(i >= 0) {
                posMem[i].isTrailStopped = state;
                if(state) {
                    for(int k=0; k<trailingCount; k++) {
                        if(trailingList[k] == ticket) {
                            trailingList[k] = trailingList[trailingCount-1];
                            trailingCount--;
                            break;
                        }
                    }
                }
                return;
            }
        }
    }

    bool IsTrailStopped(ulong ticket) {
        int i = FindMem(ticket); if(i >= 0) return posMem[i].isTrailStopped;
        return false;
    }

    bool IsManualTrail(ulong ticket) { int i = FindMem(ticket); if(i >= 0) return posMem[i].isManualTrail; return false; }

    void SetManualTrailFlag(ulong ticket) { int i = FindMem(ticket); if(i >= 0) { posMem[i].isManualTrail = true; return; } }

    void ProcessDecisionEngine(int count, PosInfo &pos[], double bid, double ask, double p, double mD, CExecutionManager &exec) {
        if(TrailLocked()) return;
        for(int idx=0; idx<count; idx++) {
            ulong t = pos[idx].ticket;
            int mi = FindMem(t);
            if(mi < 0 || !IsValidMagic(pos[idx].magic) || posMem[mi].isTrailStopped || !posMem[mi].isManualTrail) continue;
            bool isForceManual = posMem[mi].isManualTrail;
            // 원본 가격 산식/개선 폭 보존. 응답 전인 유리한 SL도 비교 기준에 포함한다.
            double nSL = (pos[idx].type == 0 ? bid - mD : ask + mD);
            bool isProfitSecured = (pos[idx].type == 0) ? (nSL > pos[idx].entry) : (nSL < pos[idx].entry);
            double referenceSL = exec.EffectiveTrailSL(t, pos[idx].type, pos[idx].sl);
            if(isProfitSecured || isForceManual) {
                if(referenceSL <= 0 || (pos[idx].type == 0 && nSL > referenceSL + g_point) || (pos[idx].type == 1 && nSL < referenceSL - g_point))
                    exec.RequestTrailSL(t, nSL);
            }
        }
    }

    void ButtonTrailAll(double bid, double ask, double p, double mD, CExecutionManager &exec) {
        TrailLockSet(0);

        for(int i=PositionsTotal()-1;i>=0;i--) {
            ulong t=PositionGetTicket(i); 
            if(PositionSelectByTicket(t) && PositionGetString(POSITION_SYMBOL) == _Symbol) {
                long magic = PositionGetInteger(POSITION_MAGIC);
                if(!IsValidMagic(magic)) continue;

                long ty=PositionGetInteger(POSITION_TYPE); 
                
                SetTrailStopped(t, false);
                SetManualTrailFlag(t); 

                // 서버 최소 허용 거리로만 작동하도록 일원화
                double pr=(ty==0)?bid:ask; 
                double nS=(ty==0 ? pr-mD : pr+mD);

                double cS=exec.EffectiveTrailSL(t, ty, PositionGetDouble(POSITION_SL));
                
                if((ty==0 && (cS<=0 || nS>cS)) || (ty==1 && (cS<=0 || nS<cS))) { 
                    exec.RequestTrailSL(t,nS); 
 
                    
                    bool exists=false;
                    for(int k=0; k<trailingCount; k++) if(trailingList[k] == t) { exists=true; break; }
                    if(!exists && trailingCount<MAX_TRACK_SIZE) trailingList[trailingCount++] = t; 
                }
            }
        }
    }

    // 디스크 저장은 시작/정지 전환 때만 한다. 틱별 엔진에는 저장/ATR 읽기/대기 없음.
    void EnableTrail(ulong ticket, bool manual, datetime currentBar) {
        bool wasActive = IsManualTrail(ticket) && !IsTrailStopped(ticket);
        if(!wasActive) {
            SetTrailStopped(ticket, false);
            SetManualTrailFlag(ticket);
            int i = FindMem(ticket); if(i >= 0) posMem[i].trailStartBar = currentBar;
            SavePosState(ticket, "T", manual ? 1.0 : 2.0);
            SavePosState(ticket, "B", (double)currentBar);
        } else if(manual) {
            double saved = 0.0;
            if(!LoadPosState(ticket, "T", saved) || saved != 1.0) SavePosState(ticket, "T", 1.0);
        }
        SetRROverridden(ticket);
    }

    void StopTrail(ulong ticket, bool consumeRR = true) {
        bool changed = !IsTrailStopped(ticket) || !consumeRR;
        SetTrailStopped(ticket, true);
        int i = FindMem(ticket); if(i >= 0) {
            posMem[i].isRROverridden = consumeRR;
            if(!consumeRR) {
                posMem[i].isManualTrail = false;
                posMem[i].isTrailStopped = false;
            }
        }
        if(changed) SavePosState(ticket, "T", consumeRR ? -1.0 : 0.0);
    }

    datetime GetTrailStartBar(ulong ticket) {
        int i = FindMem(ticket); if(i >= 0) return posMem[i].trailStartBar;
        return 0;
    }

    bool DeletedTPBETriggered(ulong ticket) {
        int i = FindMem(ticket); if(i >= 0) return posMem[i].deletedTPBETriggered;
        return false;
    }

    void LatchDeletedTPBE(ulong ticket) {
        int i = FindMem(ticket); if(i >= 0 && !posMem[i].deletedTPBETriggered) {
            posMem[i].deletedTPBETriggered = true;
            SavePosState(ticket, "D", 1.0);
        }
    }

    // 긍정 TP 이벤트는 틱 없이 수정 후 삭제된 경우도 마지막 가격을 보존한다.
    void RememberTPEvent(ulong ticket, double tp, CExecutionManager &exec, uint requestID = 0) {
        if(!(tp > 0.0) || !PositionSelectByTicket(ticket) || PositionGetString(POSITION_SYMBOL) != _Symbol ||
           !IsValidMagic(PositionGetInteger(POSITION_MAGIC))) return;
        if(!IsTracked(ticket)) UpdatePosMemory(ticket, 0.0, PositionGetDouble(POSITION_SL), exec);
        int i = FindMem(ticket);
        if(i < 0) return;
        if(requestID > 0 && posMem[i].lastTPRequestID > requestID) return;
        if(requestID > 0) posMem[i].lastTPRequestID = requestID;
        exec.CancelInitialTP(ticket);
        posMem[i].tpPrice = tp;
        posMem[i].tpSetupDone = true;
        posMem[i].deletedTPBETriggered = false;
        SavePosState(ticket, "M", tp);
        SavePosState(ticket, "P", 1.0);
        SavePosState(ticket, "D", 0.0);
    }

    bool HasRealtimeTrail() {
        if(Inp_TrailUpdate != TRAIL_REALTIME) return false;
        for(int i=0; i<posMemCount; i++) if(posMem[i].isManualTrail && !posMem[i].isTrailStopped) return true;
        return false;
    }

    void EnsureInitialTP(ulong ticket, long type, double entry, double currentTP, CExecutionManager &exec) {
        if(Inp_RRAction != RR_TAKE_PROFIT || Inp_ExecutionMode != MODE_FULL) return;
        int i = FindMem(ticket); if(i >= 0) {
            // 기존 TP와 사용자가 수정/삭제한 TP를 다시 덮어쓰지 않는다.
            if(posMem[i].tpSetupDone || currentTP > 0.0 ||
               (posMem[i].isManualTrail && !posMem[i].isTrailStopped)) return;
            double target = RRPrice(type, entry, posMem[i].initialSL, Inp_TargetRR);
            if(target > 0.0) exec.RequestInitialTP(ticket, target);
            return;
        }
    }

};

CExecutionManager execManager;
CRiskManager      riskManager;
CPositionManager  posManager;


// 봉마감은 새 봉의 첫 틱에서 감지한다. RR 도달 판단은 항상 틱 단위다.
void ProcessTrailing(int count, PosInfo &pos[], double bid, double ask, double p, bool barClosed, datetime currentBar) {
    if(Inp_TrailUpdate == TRAIL_BAR_CLOSE && !barClosed) return;
    if(Inp_Trail_ATR_Mult > 0.0 && !ATRFresh(currentBar)) return;
    double distance = TrailDistance(MinStopDist(), g_lastATR, Inp_Trail_ATR_Mult);
    if(!(distance > 0.0)) return;

    if(Inp_TrailUpdate == TRAIL_REALTIME) {
        // 원본 계산/갱신 엔진 직접 호출. ATR=0일 때 distance == 원본 MinStopDist().
        posManager.ProcessDecisionEngine(count, pos, bid, ask, p, distance, execManager);
        return;
    }

    // 이 첫 틱에서 RR에 새로 도달한 포지션은 다음 봉마감부터 갱신한다.
    static PosInfo eligible[MAX_TRACK_SIZE];
    int ready = 0;
    for(int i=0; i<count; i++) {
        if(TrailUpdateDue(false, barClosed, posManager.GetTrailStartBar(pos[i].ticket), currentBar))
            eligible[ready++] = pos[i];
    }
    posManager.ProcessDecisionEngine(ready, eligible, bid, ask, p, distance, execManager);
    g_trailDoneBar = currentBar;
    g_trailPendingBar = 0;
}

void StartManualTrailAll() {
    MqlTick tick;
    if(!SymbolInfoTick(_Symbol, tick)) return;
    datetime currentBar = iTime(_Symbol, _Period, 0);
    for(int i=PositionsTotal()-1; i>=0; i--) {
        ulong t = PositionGetTicket(i);
        if(!PositionSelectByTicket(t) || PositionGetString(POSITION_SYMBOL) != _Symbol ||
           !IsValidMagic(PositionGetInteger(POSITION_MAGIC))) continue;
        if(!posManager.IsTracked(t)) posManager.UpdatePosMemory(t, PositionGetDouble(POSITION_TP), PositionGetDouble(POSITION_SL), execManager);
        posManager.EnableTrail(t, true, currentBar);
    }
    if(Inp_TrailUpdate == TRAIL_REALTIME) {
        if(Inp_Trail_ATR_Mult > 0.0 && !ATRFresh(currentBar)) return;
    double distance = TrailDistance(MinStopDist(), g_lastATR, Inp_Trail_ATR_Mult);
        if(distance > 0.0) {
            // 수동 실시간 시작도 원본 ButtonTrailAll의 갱신 조건/큐 순서를 유지한다.
            posManager.ButtonTrailAll(tick.bid, tick.ask, Pip(), distance, execManager);
            execManager.ExecuteQueue();
        }
    }
    g_tradeDirty = true;
}

// 지정가 TP 삭제 후 기억 가격 도달: 청산 없이 본절만 요청한다.
// 기존 SL이 본절보다 유리하면 유지하며, 거절된 요청은 다음 틱에서 재시도한다.
void MoveDeletedTPToBreakeven(ulong t) {
    if(!PositionSelectByTicket(t) || PositionGetString(POSITION_SYMBOL) != _Symbol ||
       !IsValidMagic(PositionGetInteger(POSITION_MAGIC))) return;
    long type = PositionGetInteger(POSITION_TYPE);
    double entry = PositionGetDouble(POSITION_PRICE_OPEN);
    double sl = PositionGetDouble(POSITION_SL);
    posManager.StopTrail(t);
    if(sl <= 0.0 || (type == POSITION_TYPE_BUY && sl < entry) || (type == POSITION_TYPE_SELL && sl > entry))
        execManager.RequestProtectiveSL(t, BreakevenPrice(type, entry));
}

void ExecuteEntry(int type) {
    if(ObjectFind(0, LINE_SL) == -1) {
        Alert("손절선(LINE_SL)이 삭제되었습니다. '%' 버튼을 눌러 라인을 먼저 재생성하세요.");
        return;
    }

    MqlTick tick;
    if(!SymbolInfoTick(_Symbol, tick)) return;

    double entryPrice = (type == ORDER_TYPE_BUY) ? tick.ask : tick.bid;
    long assignedMagic = (type == ORDER_TYPE_BUY) ? MAGIC_BUY : MAGIC_SELL;
    
    double finalSL = NormalizeSL(type, ObjectGetDouble(0, LINE_SL, OBJPROP_PRICE));
    
    if(type == ORDER_TYPE_BUY && finalSL >= tick.ask) return;
    if(type == ORDER_TYPE_SELL && finalSL <= tick.bid) return;
    
    double finalLots = riskManager.CalculateLotSize(entryPrice, finalSL);
    
    if(finalLots > 0) {
        execManager.SendOrderRaw(type, assignedMagic, finalLots, entryPrice, finalSL);
    }
}

//────────────────────────────────────────────────────────────
// 모바일 사이징 지정가: 지정가(매직 0 BUY/SELL LIMIT)는 포지션 사이징 입력일 뿐 주문 목적이 없다.
//  - 발견 즉시 무조건 취소하고, 취소가 확인될 때까지 재요청한다.
//  - 시장가 진입은 지정가가 "체결 없이 취소됨"이 확인된 뒤에만 한다 (지정가 체결 + 시장가 = 이중 진입 방지).
//  - 사이징 불가(최소 랏 미만) 또는 SL 방향 불일치면 취소만 하고 진입하지 않는다.
//────────────────────────────────────────────────────────────
#define MOBILE_WAIT         0
#define MOBILE_SEND_REMOVE  1
#define MOBILE_ENTER        2
#define MOBILE_DROP         3
#define MOBILE_REMOVE_RETRY_MS   500
#define MOBILE_HISTORY_WAIT_MS   5000

int MobileSizingStep(bool orderLive, bool historyFound, bool cancelled, double filledVolume, ulong now, ulong nextTry, ulong firstSeen)
{
    if(orderLive) return (now >= nextTry) ? MOBILE_SEND_REMOVE : MOBILE_WAIT;
    if(!historyFound) return (now - firstSeen > MOBILE_HISTORY_WAIT_MS) ? MOBILE_DROP : MOBILE_WAIT;
    if(cancelled && filledVolume <= 0.0) return MOBILE_ENTER;
    return MOBILE_DROP;
}

struct MobileSizingOrder { ulong order; long type; double sl; ulong firstSeen; ulong nextTry; };
MobileSizingOrder g_mobileTrack[MAX_TRACK_SIZE];
int               g_mobileTrackCount = 0;

bool IsMobileSizingTracked(ulong order) {
    for(int i = 0; i < g_mobileTrackCount; i++) if(g_mobileTrack[i].order == order) return true;
    return false;
}

// 체결(진입) 딜이 사이징 지정가에서 나왔는지 판정 (순수 함수: 회귀테스트 대상)
bool IsSizingLimitFill(bool tracked, bool orderFound, bool isLimitType, long orderMagic, bool isEntryDeal)
{
    if(!isEntryDeal) return false;
    if(tracked) return true;
    return orderFound && isLimitType && orderMagic == 0;
}

// 사이징 지정가가 취소 전에 체결된 경우: 자동 SL을 붙이지 않고 즉시 청산한다.
// (SL이 없는 진입은 마스터가 신호를 보류하므로 슬레이브 실계좌로 전달되지 않는다)
bool HandleSizingLimitFill(ulong order, ulong position) {
    if(order == 0 || position == 0) return false;
    if(!PositionSelectByTicket(position) || PositionGetString(POSITION_SYMBOL) != _Symbol) return false;
    bool isEntryDeal = ((ulong)PositionGetInteger(POSITION_IDENTIFIER) == order);

    bool found = false, isLimit = false;
    long magic = -1;
    if(OrderSelect(order)) {
        found = true;
        long ty = OrderGetInteger(ORDER_TYPE);
        isLimit = (ty == ORDER_TYPE_BUY_LIMIT || ty == ORDER_TYPE_SELL_LIMIT);
        magic = OrderGetInteger(ORDER_MAGIC);
    } else if(HistoryOrderSelect(order)) {
        found = true;
        long ty = HistoryOrderGetInteger(order, ORDER_TYPE);
        isLimit = (ty == ORDER_TYPE_BUY_LIMIT || ty == ORDER_TYPE_SELL_LIMIT);
        magic = HistoryOrderGetInteger(order, ORDER_MAGIC);
    }

    if(!IsSizingLimitFill(IsMobileSizingTracked(order), found, isLimit, magic, isEntryDeal)) return false;

    Alert(StringFormat("[경고] 사이징 지정가 #%llu 가 취소 전에 체결됨 → 자동 SL 없이 포지션 #%llu 즉시 청산", order, position));
    execManager.RequestClose(position);
    execManager.ProcessCloseQueue();
    return true;
}

void SendMobileRemove(ulong order) {
    MqlTradeRequest r; MqlTradeResult s; ZeroMemory(r); ZeroMemory(s);
    r.action = TRADE_ACTION_REMOVE;
    r.order = order;
    if(!OrderSendAsync(r, s)) Print("모바일 사이징 지정가 취소 요청 실패: #", order, " Error ", GetLastError());
}

void EnterFromMobileSizing(ulong order, long limitType, double sl) {
    MqlTick tick;
    if(!SymbolInfoTick(_Symbol, tick)) { PrintFormat("[진입 생략] 모바일 지정가 #%llu: 시세 없음", order); return; }

    int marketType = (limitType == ORDER_TYPE_BUY_LIMIT) ? ORDER_TYPE_BUY : ORDER_TYPE_SELL;
    long assignedMagic = (marketType == ORDER_TYPE_BUY) ? MAGIC_BUY : MAGIC_SELL;
    double entryPrice = (marketType == ORDER_TYPE_BUY) ? tick.ask : tick.bid;

    if((marketType == ORDER_TYPE_BUY && sl >= entryPrice) || (marketType == ORDER_TYPE_SELL && sl <= entryPrice)) {
        PrintFormat("[진입 금지] 모바일 지정가 #%llu 취소 완료: SL(%.5f)이 진입가(%.5f) 반대편", order, sl, entryPrice);
        return;
    }

    sl = NormalizeSL(marketType, sl);
    double finalLots = riskManager.CalculateLotSize(entryPrice, sl);
    if(finalLots <= 0) {
        PrintFormat("[진입 금지] 모바일 지정가 #%llu 취소 완료: 포지션 사이징이 최소 랏 미만", order);
        return;
    }
    execManager.SendOrderRaw(marketType, assignedMagic, finalLots, entryPrice, sl);
}

void ProcessMobilePendingOrders() {
    int total = OrdersTotal();
    if(total == 0 && g_mobileTrackCount == 0) return;
    ulong now = GetTickCount64();

    // 1) 새 사이징 지정가 등록 (상태와 무관하게 전부 취소 대상)
    for(int i = total - 1; i >= 0; i--) {
        ulong ticket = OrderGetTicket(i);
        if(ticket == 0) continue;
        if(OrderGetString(ORDER_SYMBOL) != _Symbol) continue;
        if(OrderGetInteger(ORDER_MAGIC) != 0) continue;
        long type = OrderGetInteger(ORDER_TYPE);
        if(type != ORDER_TYPE_BUY_LIMIT && type != ORDER_TYPE_SELL_LIMIT) continue;

        bool known = false;
        for(int k = 0; k < g_mobileTrackCount; k++) if(g_mobileTrack[k].order == ticket) { known = true; break; }
        if(known) continue;

        if(g_mobileTrackCount < MAX_TRACK_SIZE) {
            g_mobileTrack[g_mobileTrackCount].order = ticket; g_mobileTrack[g_mobileTrackCount].type = type;
            g_mobileTrack[g_mobileTrackCount].sl = OrderGetDouble(ORDER_PRICE_OPEN);
            g_mobileTrack[g_mobileTrackCount].firstSeen = now; g_mobileTrack[g_mobileTrackCount].nextTry = 0;
            g_mobileTrackCount++;
        } else {
            SendMobileRemove(ticket); // 추적 공간 부족: 진입 없이 취소만
        }
    }

    // 2) 취소 확인될 때까지 재요청, 체결 없이 취소된 것만 시장가 진입
    for(int i = 0; i < g_mobileTrackCount; i++) {
        ulong t = g_mobileTrack[i].order;
        bool live = OrderSelect(t);
        if(live) g_mobileTrack[i].sl = OrderGetDouble(ORDER_PRICE_OPEN);

        bool found = false, cancelled = false;
        double filled = 0.0;
        if(!live && HistoryOrderSelect(t)) {
            found = true;
            cancelled = (HistoryOrderGetInteger(t, ORDER_STATE) == ORDER_STATE_CANCELED);
            filled = HistoryOrderGetDouble(t, ORDER_VOLUME_INITIAL) - HistoryOrderGetDouble(t, ORDER_VOLUME_CURRENT);
        }

        int step = MobileSizingStep(live, found, cancelled, filled, now, g_mobileTrack[i].nextTry, g_mobileTrack[i].firstSeen);
        if(step == MOBILE_SEND_REMOVE) { SendMobileRemove(t); g_mobileTrack[i].nextTry = now + MOBILE_REMOVE_RETRY_MS; }
        if(step == MOBILE_WAIT || step == MOBILE_SEND_REMOVE) continue;

        if(step == MOBILE_ENTER) EnterFromMobileSizing(t, g_mobileTrack[i].type, g_mobileTrack[i].sl);
        else if(found && filled > 0.0) PrintFormat("[진입 생략] 사이징 지정가 #%llu 체결됨(%.2f Lot): 시장가 진입 없음, 체결분은 즉시 청산 처리", t, filled);
        else PrintFormat("[진입 생략] 모바일 지정가 #%llu: 체결 없는 취소를 확인할 수 없음", t);

        g_mobileTrack[i] = g_mobileTrack[g_mobileTrackCount - 1]; g_mobileTrackCount--; i--;
    }
}

void UnifySL(long type) {
    double uSL = 0; bool f = true;
    int total = PositionsTotal();
    static PosInfo temp[MAX_TRACK_SIZE]; int tCount = 0; 
    
    for(int i=total-1; i>=0; i--) {
        if(tCount >= MAX_TRACK_SIZE) break;
        ulong t=PositionGetTicket(i); if(!PositionSelectByTicket(t) || PositionGetString(POSITION_SYMBOL) != _Symbol || PositionGetInteger(POSITION_TYPE)!=type) continue; 
        long magic = PositionGetInteger(POSITION_MAGIC); if(!IsValidMagic(magic)) continue;
        double sl = PositionGetDouble(POSITION_SL);
        if(sl > 0) { if(f) { uSL = sl; f = false; } else uSL = (type==POSITION_TYPE_BUY) ? MathMin(uSL,sl) : MathMax(uSL,sl); }
        temp[tCount].ticket = t; tCount++;
    }
    if(uSL > 0) { for(int i=0; i<tCount; i++) execManager.RequestSL(temp[i].ticket, uSL); }
    execManager.ExecuteQueue();
}

void UnifyTP(long type) {
    double uTP = 0; bool hasTP = false;
    int total = PositionsTotal();
    static PosInfo temp[MAX_TRACK_SIZE]; int tCount = 0;

    for(int i=total-1; i>=0; i--) {
        if(tCount >= MAX_TRACK_SIZE) break;
        ulong t=PositionGetTicket(i); if(!PositionSelectByTicket(t) || PositionGetString(POSITION_SYMBOL) != _Symbol || PositionGetInteger(POSITION_TYPE)!=type) continue;
        long magic = PositionGetInteger(POSITION_MAGIC); if(!IsValidMagic(magic)) continue;
        double tp = PositionGetDouble(POSITION_TP);
        if(tp > 0) { if(!hasTP) { uTP = tp; hasTP = true; } else uTP = (type==POSITION_TYPE_BUY) ? MathMax(uTP,tp) : MathMin(uTP,tp); }
        temp[tCount].ticket = t; temp[tCount].sl = PositionGetDouble(POSITION_SL); temp[tCount].entry = PositionGetDouble(POSITION_PRICE_OPEN); tCount++;
    }

    for(int i=0; i<tCount; i++) {
        double targetTP = uTP;
        if(!hasTP) {
            if(temp[i].sl > 0) {
                double dist = MathAbs(temp[i].entry - temp[i].sl);
                targetTP = (type==POSITION_TYPE_BUY) ? temp[i].entry + (dist * 2.0) : temp[i].entry - (dist * 2.0);
            } else continue; 
        }
        if(targetTP > 0) execManager.RequestTP(temp[i].ticket, targetTP);
    }
    execManager.ExecuteQueue();
}

void MoveSL(int pips) { 
    if(pips==0) return; double p=Pip(); 
    for(int i=PositionsTotal()-1; i>=0; i--) { 
        ulong t=PositionGetTicket(i); if(PositionSelectByTicket(t) && PositionGetString(POSITION_SYMBOL) == _Symbol) { 
            long magic = PositionGetInteger(POSITION_MAGIC); if(!IsValidMagic(magic)) continue;
            long type=PositionGetInteger(POSITION_TYPE); double sl=PositionGetDouble(POSITION_SL); 
            if(sl>0) execManager.RequestSL(t, (type==POSITION_TYPE_BUY)?sl+pips*p:sl-pips*p); 
        } 
    } 
    execManager.ExecuteQueue();
}

void MoveTP(int pips) {
    if(pips==0) return; double p=Pip();
    for(int i=PositionsTotal()-1; i>=0; i--) {
        ulong t=PositionGetTicket(i); if(PositionSelectByTicket(t) && PositionGetString(POSITION_SYMBOL) == _Symbol) {
            long magic = PositionGetInteger(POSITION_MAGIC); if(!IsValidMagic(magic)) continue;
            long type=PositionGetInteger(POSITION_TYPE); double tp=PositionGetDouble(POSITION_TP);
            if(tp>0) execManager.RequestTP(t, (type==POSITION_TYPE_BUY)?tp+pips*p:tp-pips*p);
        }
    }
    execManager.ExecuteQueue();
}

void CloseProfitPositions() { 
    for(int i=PositionsTotal()-1; i>=0; i--) { 
        ulong t=PositionGetTicket(i); 
        if(PositionSelectByTicket(t) && PositionGetString(POSITION_SYMBOL) == _Symbol) {
            long magic = PositionGetInteger(POSITION_MAGIC); if(!IsValidMagic(magic)) continue;
            double netProfit = PositionGetDouble(POSITION_PROFIT) + PositionGetDouble(POSITION_SWAP) + PositionGetDouble(POSITION_COMMISSION);
            if(netProfit > 0) execManager.RequestClose(t); 
        }
    } 
    execManager.ProcessCloseQueue(); 
}



// 반익반본 실행 여부를 반환한다: 손실 구간에서 최초 SL 복구만 한 경우 false (반익반본은 아직 실행되지 않음)
// Confirmed asynchronous half-close / breakeven workflow.
// Included after execManager and posManager. No changes to the deleted-TP D latch.
#define HALF_IDLE          0
#define HALF_CLOSE_READY   1
#define HALF_CLOSE_WAIT    2
#define HALF_BE_READY      3
#define HALF_RESTORE       4
#define HALF_DONE          5
#define HALF_PAUSED        6

struct HalfOperation
{
    ulong ticket;
    ulong positionId;
    long type;
    int phase;
    double initialVolume;
    double targetVolume;
    double filledVolume;
    double filledBeforeSend;
    double requestedVolume;
    double bePrice;
    uint tokenA;
    uint tokenB;
    uint attempt;
    ulong order;
    uint requestId;
    datetime started;
    ulong nextTry;
    ulong nextBE;
    ulong nextHistory;
    ulong sentAt;
    bool warned;
};

HalfOperation g_halfOps[MAX_TRACK_SIZE];
int g_halfOpCount = 0;
bool g_halfLoaded = false;
ulong g_halfLoadUntil = 0;
ulong g_halfNextLoad = 0;

double HalfRemainingVolume(double target, double filled, double step)
{
    if(step <= 0.0) step = 0.01;
    double remaining = MathMax(0.0, target - filled);
    return NormalizeDouble(MathFloor((remaining + step * 1e-8) / step) * step, 8);
}

double HalfSafeRequestVolume(double remaining, double currentVolume, double minLot, double step)
{
    if(step <= 0.0) step = 0.01;
    double available = MathMax(0.0, currentVolume - minLot);
    double amount = MathMin(remaining, available);
    return NormalizeDouble(MathFloor((amount + step * 1e-8) / step) * step, 8);
}

string HalfToken(HalfOperation &op)
{
    return StringFormat("OZH%08X%08X", op.tokenA, op.tokenB);
}

string HalfAttemptComment(HalfOperation &op)
{
    return HalfToken(op) + "-" + IntegerToString((long)op.attempt);
}

bool HalfSetValue(ulong ticket, string kind, double value)
{
    if(GlobalVariableSet(PosStateKey(ticket, kind), value) != 0) return true;
    PrintFormat("[반익반본 상태 저장 실패] Ticket=%llu Key=%s Error=%d", ticket, kind, GetLastError());
    return false;
}

// Metadata first, phase last. Sending is allowed only after this critical commit.
bool SaveHalfOperation(HalfOperation &op)
{
    bool ok = true;
    if(!HalfSetValue(op.ticket, "HI", (double)op.positionId)) ok = false;
    if(!HalfSetValue(op.ticket, "HY", (double)op.type)) ok = false;
    if(!HalfSetValue(op.ticket, "HV", op.initialVolume)) ok = false;
    if(!HalfSetValue(op.ticket, "HQ", op.targetVolume)) ok = false;
    if(!HalfSetValue(op.ticket, "HF", op.filledVolume)) ok = false;
    if(!HalfSetValue(op.ticket, "HC", op.filledBeforeSend)) ok = false;
    if(!HalfSetValue(op.ticket, "HX", op.requestedVolume)) ok = false;
    if(!HalfSetValue(op.ticket, "HL", op.bePrice)) ok = false;
    if(!HalfSetValue(op.ticket, "HA", (double)op.tokenA)) ok = false;
    if(!HalfSetValue(op.ticket, "HB", (double)op.tokenB)) ok = false;
    if(!HalfSetValue(op.ticket, "HN", (double)op.attempt)) ok = false;
    if(!HalfSetValue(op.ticket, "HJ", (double)op.order)) ok = false;
    if(!HalfSetValue(op.ticket, "HE", (double)op.started)) ok = false;
    if(ok && !HalfSetValue(op.ticket, "HS", (double)op.phase)) ok = false;
    GlobalVariablesFlush();
    return ok;
}

void DeleteHalfOperationState(ulong ticket)
{
    g_posStatesDirty = true;
    string kinds[] = {"HS","HI","HY","HV","HQ","HF","HC","HX","HL","HA","HB","HN","HJ","HE"};
    for(int i=0; i<ArraySize(kinds); i++) GlobalVariableDel(PosStateKey(ticket, kinds[i]));
}

int FindHalfOperation(ulong ticket)
{
    for(int i=0; i<g_halfOpCount; i++) if(g_halfOps[i].ticket == ticket) return i;
    return -1;
}

bool LoadHalfOperation(ulong ticket, HalfOperation &op)
{
    double value = 0.0;
    if(!LoadPosState(ticket, "HS", value) || value < HALF_CLOSE_READY || value > HALF_PAUSED) return false;
    ZeroMemory(op);
    op.ticket = ticket;
    op.phase = (int)value;
    if(LoadPosState(ticket, "HI", value)) op.positionId = (ulong)value;
    if(LoadPosState(ticket, "HY", value)) op.type = (long)value;
    if(LoadPosState(ticket, "HV", value)) op.initialVolume = value;
    if(LoadPosState(ticket, "HQ", value)) op.targetVolume = value;
    if(LoadPosState(ticket, "HF", value)) op.filledVolume = value;
    if(LoadPosState(ticket, "HC", value)) op.filledBeforeSend = value;
    if(LoadPosState(ticket, "HX", value)) op.requestedVolume = value;
    if(LoadPosState(ticket, "HL", value)) op.bePrice = value;
    if(LoadPosState(ticket, "HA", value)) op.tokenA = (uint)value;
    if(LoadPosState(ticket, "HB", value)) op.tokenB = (uint)value;
    if(LoadPosState(ticket, "HN", value)) op.attempt = (uint)value;
    if(LoadPosState(ticket, "HJ", value)) op.order = (ulong)value;
    if(LoadPosState(ticket, "HE", value)) op.started = (datetime)value;
    op.sentAt = GetTickCount64();
    return op.positionId > 0 && op.bePrice > 0.0;
}

void InitHalfOperations()
{
    g_halfLoaded = false;
    g_halfOpCount = 0;
    g_halfLoadUntil = GetTickCount64() + 60000;
    g_halfNextLoad = 0;
}

void RegisterHalfPosition(ulong ticket)
{
    if(FindHalfOperation(ticket) >= 0 || g_halfOpCount >= MAX_TRACK_SIZE ||
       !PositionSelectByTicket(ticket) || PositionGetString(POSITION_SYMBOL) != _Symbol ||
       !IsValidMagic(PositionGetInteger(POSITION_MAGIC))) return;
    HalfOperation op;
    if(LoadHalfOperation(ticket, op) && op.phase != HALF_DONE) g_halfOps[g_halfOpCount++] = op;
}

void LoadHalfOperationsIfReady()
{
    if(!TerminalInfoInteger(TERMINAL_CONNECTED)) return;
    ulong now = GetTickCount64();
    if(g_halfLoaded && (now >= g_halfLoadUntil || now < g_halfNextLoad)) return;
    g_halfNextLoad = now + 1000;
    for(int i=PositionsTotal()-1; i>=0 && g_halfOpCount<MAX_TRACK_SIZE; i--) {
        ulong ticket = PositionGetTicket(i);
        if(!PositionSelectByTicket(ticket) || PositionGetString(POSITION_SYMBOL) != _Symbol ||
           !IsValidMagic(PositionGetInteger(POSITION_MAGIC)) || FindHalfOperation(ticket) >= 0) continue;
        RegisterHalfPosition(ticket);
    }
    g_halfLoaded = true;
}

bool HalfOperationActive(ulong ticket)
{
    int i = FindHalfOperation(ticket);
    if(i < 0) return false;
    int phase = g_halfOps[i].phase;
    return phase == HALF_CLOSE_READY || phase == HALF_CLOSE_WAIT || phase == HALF_BE_READY || phase == HALF_RESTORE;
}

bool HalfOperationsPending()
{
    if(!g_halfLoaded) return true;
    for(int i=0; i<g_halfOpCount; i++) {
        int phase = g_halfOps[i].phase;
        if(phase == HALF_CLOSE_READY || phase == HALF_CLOSE_WAIT || phase == HALF_BE_READY || phase == HALF_RESTORE) return true;
    }
    return false;
}

void PauseHalfOperation(HalfOperation &op, string reason)
{
    op.phase = HALF_PAUSED;
    SaveHalfOperation(op);
    PrintFormat("[반익반본 보류] Ticket=%llu | %s | 중복 또는 과다 청산 방지를 위해 추가 청산하지 않습니다.", op.ticket, reason);
}

bool HalfIsSelectedPosition(HalfOperation &op)
{
    return PositionSelectByTicket(op.ticket) && PositionGetString(POSITION_SYMBOL) == _Symbol &&
           (ulong)PositionGetInteger(POSITION_IDENTIFIER) == op.positionId &&
           PositionGetInteger(POSITION_TYPE) == op.type;
}

// Only this operation's closing order contributes to its fixed target.
// Earlier confirmed attempts are in filledBeforeSend; history is re-read after restart.
bool RefreshHalfFillProgress(HalfOperation &op)
{
    if(!HistorySelectByPosition(op.positionId)) return false;
    string comment = HalfAttemptComment(op);
    if(op.phase == HALF_CLOSE_WAIT && op.order == 0) {
        for(int i=OrdersTotal()-1; i>=0; i--) {
            ulong ticket = OrderGetTicket(i);
            if(ticket > 0 && OrderGetString(ORDER_SYMBOL) == _Symbol &&
               OrderGetString(ORDER_COMMENT) == comment &&
               (ulong)OrderGetInteger(ORDER_POSITION_ID) == op.positionId) {
                op.order = ticket;
                SaveHalfOperation(op);
                break;
            }
        }
        if(op.order == 0) {
            for(int i=HistoryOrdersTotal()-1; i>=0; i--) {
                ulong ticket = HistoryOrderGetTicket(i);
                if(ticket > 0 && HistoryOrderGetString(ticket, ORDER_SYMBOL) == _Symbol &&
                   HistoryOrderGetString(ticket, ORDER_COMMENT) == comment) {
                    op.order = ticket;
                    SaveHalfOperation(op);
                    break;
                }
            }
        }
    }
    if(op.order == 0) return true;
    double filled = 0.0;
    for(int i=0; i<HistoryDealsTotal(); i++) {
        ulong deal = HistoryDealGetTicket(i);
        if(deal == 0 || (ulong)HistoryDealGetInteger(deal, DEAL_ORDER) != op.order ||
           (ulong)HistoryDealGetInteger(deal, DEAL_POSITION_ID) != op.positionId) continue;
        long entry = HistoryDealGetInteger(deal, DEAL_ENTRY);
        if(entry == DEAL_ENTRY_OUT || entry == DEAL_ENTRY_OUT_BY)
            filled += HistoryDealGetDouble(deal, DEAL_VOLUME);
    }
    op.filledVolume = MathMax(op.filledVolume, op.filledBeforeSend + filled);
    return true;
}

bool HalfAttemptFinished(HalfOperation &op, double step)
{
    if(op.order == 0 || OrderSelect(op.order) || !HistoryOrderSelect(op.order)) return false;
    long state = HistoryOrderGetInteger(op.order, ORDER_STATE);
    if(state != ORDER_STATE_FILLED && state != ORDER_STATE_CANCELED &&
       state != ORDER_STATE_REJECTED && state != ORDER_STATE_EXPIRED) return false;
    double expected = HistoryOrderGetDouble(op.order, ORDER_VOLUME_INITIAL) -
                      HistoryOrderGetDouble(op.order, ORDER_VOLUME_CURRENT);
    return op.filledVolume + step * 1e-6 >= op.filledBeforeSend + MathMax(0.0, expected);
}

ENUM_ORDER_TYPE_FILLING HalfFillingMode()
{
    long filling = SymbolInfoInteger(_Symbol, SYMBOL_FILLING_MODE);
    if((filling & SYMBOL_FILLING_IOC) != 0) return ORDER_FILLING_IOC;
    if((filling & SYMBOL_FILLING_FOK) != 0) return ORDER_FILLING_FOK;
    return ORDER_FILLING_RETURN;
}

void CompleteHalfOperation(HalfOperation &op)
{
    bool restoreOnly = op.phase == HALF_RESTORE;
    op.phase = HALF_DONE;
    if(!restoreOnly) posManager.SetAutoHalfDone(op.ticket, true);
    SaveHalfOperation(op);
}

bool HalfBreakevenAcknowledged(HalfOperation &op)
{
    double currentSL = PositionGetDouble(POSITION_SL);
    double targetSL = BreakevenPrice(op.type, op.bePrice);
    return currentSL > 0.0 && ((op.type == POSITION_TYPE_BUY && currentSL >= targetSL - g_point * 0.5) ||
           (op.type == POSITION_TYPE_SELL && currentSL <= targetSL + g_point * 0.5));
}

// Protect the position while half-close is in flight, as in the original workflow.
// Completion is still withheld until BOTH the fixed close quantity and SL are confirmed.
void RequestHalfBreakeven(HalfOperation &op, ulong now)
{
    if(HalfBreakevenAcknowledged(op) || now < op.nextBE) return;
    if(MathAbs(PositionGetDouble(POSITION_PRICE_OPEN) - op.bePrice) > g_point * 0.5) return;
    execManager.RequestProtectiveSL(op.ticket, BreakevenPrice(op.type, op.bePrice));
    op.nextBE = now + 500;
}

void ProcessOneHalfOperation(HalfOperation &op)
{
    if(op.phase == HALF_DONE || op.phase == HALF_IDLE || op.phase == HALF_PAUSED) return;
    if(!PositionSelectByTicket(op.ticket) || PositionGetString(POSITION_SYMBOL) != _Symbol) return;
    if((ulong)PositionGetInteger(POSITION_IDENTIFIER) != op.positionId || PositionGetInteger(POSITION_TYPE) != op.type) {
        PauseHalfOperation(op, "포지션 식별자 또는 방향이 변경됨");
        return;
    }
    if(!posManager.IsTracked(op.ticket))
        posManager.UpdatePosMemory(op.ticket, PositionGetDouble(POSITION_TP), PositionGetDouble(POSITION_SL), execManager);

    double step = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
    if(step <= 0.0) step = 0.01;
    double minLot = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
    ulong now = GetTickCount64();

    if(op.phase == HALF_CLOSE_READY || op.phase == HALF_CLOSE_WAIT)
        RequestHalfBreakeven(op, now);

    if(op.phase == HALF_CLOSE_WAIT) {
        if(now < op.nextHistory) return;
        op.nextHistory = now + 100;
        if(!RefreshHalfFillProgress(op)) return;
        if(HalfAttemptFinished(op, step)) {
            if(op.filledVolume + step * 1e-6 >= op.targetVolume)
                op.phase = HALF_BE_READY;
            else op.phase = HALF_CLOSE_READY;
            op.order = 0;
            op.requestId = 0;
            op.nextTry = now + 200;
            if(!SaveHalfOperation(op)) { op.phase = HALF_PAUSED; return; }
        } else {
            if(!op.warned && now - op.sentAt > 10000) {
                op.warned = true;
                PrintFormat("[반익반본 응답 대기] Ticket=%llu | 주문 확인 전에는 반청산을 중복 전송하지 않습니다.", op.ticket);
            }
            return;
        }
    }

    if(op.phase == HALF_RESTORE || op.phase == HALF_BE_READY) {
        if(!HalfIsSelectedPosition(op)) return;
        double currentSL = PositionGetDouble(POSITION_SL);
        double targetSL = (op.phase == HALF_RESTORE) ? NormalizeSL(op.type, op.bePrice) : BreakevenPrice(op.type, op.bePrice);
        if(op.phase == HALF_BE_READY) {
            // Allow terminal position/deal events to converge before inspecting volume.
            if(now < op.nextTry) return;
            double expected = op.initialVolume - op.filledVolume;
            if(MathAbs(PositionGetDouble(POSITION_VOLUME) - expected) > step * 0.25 ||
               MathAbs(PositionGetDouble(POSITION_PRICE_OPEN) - op.bePrice) > g_point * 0.5) {
                PauseHalfOperation(op, "본절 확인 전 외부 수량 또는 평균 진입가 변경이 확인됨");
                return;
            }
        }
        bool ack = (op.phase == HALF_RESTORE) ? currentSL > 0.0 : HalfBreakevenAcknowledged(op);
        if(ack) { CompleteHalfOperation(op); return; }
        if(now < op.nextTry) return;
        if(op.phase == HALF_RESTORE) {
            execManager.RequestInitialSL(op.ticket, targetSL);
            op.nextTry = now + 500;
        } else RequestHalfBreakeven(op, now);
        return;
    }

    if(op.phase != HALF_CLOSE_READY || now < op.nextTry || !HalfIsSelectedPosition(op)) return;
    double volume = PositionGetDouble(POSITION_VOLUME);
    double expectedVolume = op.initialVolume - op.filledVolume;
    if(MathAbs(volume - expectedVolume) > step * 0.25 ||
       MathAbs(PositionGetDouble(POSITION_PRICE_OPEN) - op.bePrice) > g_point * 0.5) {
        PauseHalfOperation(op, "EA 반청산 이외의 수량 또는 평균 진입가 변경이 확인됨");
        return;
    }
    double remaining = HalfRemainingVolume(op.targetVolume, op.filledVolume, step);
    if(op.filledVolume + step * 1e-6 >= op.targetVolume) {
        op.phase = HALF_BE_READY;
        SaveHalfOperation(op);
        return;
    }
    if(remaining < minLot) {
        PauseHalfOperation(op, "부분 체결 후 목표 잔량이 주문 가능한 최소 랏 미만");
        return;
    }
    double amount = HalfSafeRequestVolume(remaining, volume, minLot, step);
    if(amount < minLot || amount + step * 1e-6 < remaining) {
        PauseHalfOperation(op, "목표 잔량 청산 시 남은 포지션 최소 랏을 지킬 수 없음");
        return;
    }
    // 네팅 포지션 누적량이 주문 최대 랏보다 커도 고정 목표를 나누어 청산한다.
    double maxLot = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);
    if(maxLot > 0.0) amount = HalfRemainingVolume(MathMin(amount, maxLot), 0.0, step);
    if(amount < minLot) { PauseHalfOperation(op, "주문 최대/최소 랏 조건 불일치"); return; }
    MqlTick tick;
    if(!SymbolInfoTick(_Symbol, tick)) { op.nextTry = now + 500; return; }

    op.filledBeforeSend = op.filledVolume;
    op.requestedVolume = amount;
    op.attempt++;
    op.order = 0;
    op.requestId = 0;
    op.nextHistory = 0;
    op.phase = HALF_CLOSE_WAIT;
    op.sentAt = now;
    op.warned = false;
    if(!SaveHalfOperation(op)) { op.phase = HALF_PAUSED; return; }

    MqlTradeRequest request;
    MqlTradeResult result;
    ZeroMemory(request);
    ZeroMemory(result);
    request.action = TRADE_ACTION_DEAL;
    request.position = op.ticket;
    request.symbol = _Symbol;
    request.volume = amount;
    request.type_filling = HalfFillingMode();
    request.deviation = (ulong)SymbolInfoInteger(_Symbol, SYMBOL_SPREAD) + 50;
    request.type = (op.type == POSITION_TYPE_BUY) ? ORDER_TYPE_SELL : ORDER_TYPE_BUY;
    request.price = (op.type == POSITION_TYPE_BUY) ? tick.bid : tick.ask;
    request.comment = HalfAttemptComment(op);
    if(!OrderSendAsync(request, result)) {
        PrintFormat("[반청산 발송 재시도] Ticket=%llu Error=%d", op.ticket, GetLastError());
        op.phase = HALF_CLOSE_READY;
        op.nextTry = now + 500;
        SaveHalfOperation(op);
        return;
    }
    op.requestId = result.request_id;
    if(result.order > 0) {
        op.order = result.order;
        SaveHalfOperation(op);
    }
}

void ProcessHalfOperations()
{
    LoadHalfOperationsIfReady();
    for(int i=g_halfOpCount-1; i>=0; i--) {
        if(!PositionSelectByTicket(g_halfOps[i].ticket)) {
            if(!TerminalInfoInteger(TERMINAL_CONNECTED) || GetTickCount64() < g_halfLoadUntil) continue;
            DeleteHalfOperationState(g_halfOps[i].ticket);
            g_halfOps[i] = g_halfOps[g_halfOpCount-1];
            g_halfOpCount--;
            continue;
        }
        ProcessOneHalfOperation(g_halfOps[i]);
        if(g_halfOps[i].phase == HALF_DONE) {
            g_halfOps[i] = g_halfOps[g_halfOpCount-1];
            g_halfOpCount--;
        }
    }
}

bool HalfKnownRejection(uint retcode)
{
    return retcode == TRADE_RETCODE_REQUOTE || retcode == TRADE_RETCODE_REJECT ||
           retcode == TRADE_RETCODE_CANCEL || retcode == TRADE_RETCODE_INVALID ||
           retcode == TRADE_RETCODE_INVALID_VOLUME || retcode == TRADE_RETCODE_INVALID_PRICE ||
           retcode == TRADE_RETCODE_INVALID_STOPS || retcode == TRADE_RETCODE_TRADE_DISABLED ||
           retcode == TRADE_RETCODE_MARKET_CLOSED || retcode == TRADE_RETCODE_NO_MONEY ||
           retcode == TRADE_RETCODE_PRICE_CHANGED || retcode == TRADE_RETCODE_PRICE_OFF ||
           retcode == TRADE_RETCODE_INVALID_EXPIRATION || retcode == TRADE_RETCODE_TOO_MANY_REQUESTS ||
           retcode == TRADE_RETCODE_SERVER_DISABLES_AT || retcode == TRADE_RETCODE_CLIENT_DISABLES_AT ||
           retcode == TRADE_RETCODE_FROZEN || retcode == TRADE_RETCODE_INVALID_FILL ||
           retcode == TRADE_RETCODE_ONLY_REAL || retcode == TRADE_RETCODE_LIMIT_ORDERS ||
           retcode == TRADE_RETCODE_LIMIT_VOLUME || retcode == TRADE_RETCODE_INVALID_ORDER ||
           retcode == TRADE_RETCODE_POSITION_CLOSED || retcode == TRADE_RETCODE_INVALID_CLOSE_VOLUME;
}

void HandleHalfTransaction(HalfOperation &op, const MqlTradeRequest &request, const MqlTradeResult &result)
{
    if(op.phase != HALF_CLOSE_WAIT || request.comment != HalfAttemptComment(op) ||
       (op.requestId != 0 && result.request_id != op.requestId)) return;
    op.nextHistory = 0;
    if(result.order > 0) op.order = result.order;
    if(result.retcode == TRADE_RETCODE_DONE || result.retcode == TRADE_RETCODE_DONE_PARTIAL ||
       result.retcode == TRADE_RETCODE_PLACED) {
        SaveHalfOperation(op);
        return;
    }
    if(!HalfKnownRejection(result.retcode)) {
        SaveHalfOperation(op);
        if(!op.warned) {
            op.warned = true;
            PrintFormat("[반청산 결과 확인 대기] Ticket=%llu Retcode=%u | 체결 여부 확인 전에는 다시 전송하지 않습니다.", op.ticket, result.retcode);
        }
        return;
    }
    // A known rejection is safe to retry. Reconcile any reported order first.
    if(op.order > 0 && (!RefreshHalfFillProgress(op) || !HalfAttemptFinished(op, SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP)))) return;
    PrintFormat("[반청산 서버 거절 재시도] Ticket=%llu Retcode=%u", op.ticket, result.retcode);
    op.phase = HALF_CLOSE_READY;
    op.order = 0;
    op.requestId = 0;
    op.nextTry = GetTickCount64() + 500;
    SaveHalfOperation(op);
}

void OnHalfTransaction(const MqlTradeTransaction &trans, const MqlTradeRequest &request, const MqlTradeResult &result)
{
    if(trans.type != TRADE_TRANSACTION_REQUEST || request.action != TRADE_ACTION_DEAL || request.symbol != _Symbol) return;
    int index = FindHalfOperation(request.position);
    if(index >= 0) HandleHalfTransaction(g_halfOps[index], request, result);
}

bool ExecuteHalfAndBreakeven(ulong ticket)
{
    LoadHalfOperationsIfReady();
    RegisterHalfPosition(ticket);
    if(!PositionSelectByTicket(ticket) || PositionGetString(POSITION_SYMBOL) != _Symbol ||
       !IsValidMagic(PositionGetInteger(POSITION_MAGIC))) return false;
    if(!posManager.IsTracked(ticket))
        posManager.UpdatePosMemory(ticket, PositionGetDouble(POSITION_TP), PositionGetDouble(POSITION_SL), execManager);
    int index = FindHalfOperation(ticket);
    if(index >= 0 && g_halfOps[index].phase != HALF_DONE) return false;

    HalfOperation op;
    ZeroMemory(op);
    op.ticket = ticket;
    op.positionId = (ulong)PositionGetInteger(POSITION_IDENTIFIER);
    op.type = PositionGetInteger(POSITION_TYPE);
    op.initialVolume = PositionGetDouble(POSITION_VOLUME);
    op.bePrice = PositionGetDouble(POSITION_PRICE_OPEN);
    op.started = TimeCurrent();
    op.tokenA = (uint)TimeLocal();
    op.tokenB = (uint)GetMicrosecondCount();
    double step = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
    if(step <= 0.0) step = 0.01;
    double minLot = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
    double profit = PositionGetDouble(POSITION_PROFIT) + PositionGetDouble(POSITION_SWAP) + PositionGetDouble(POSITION_COMMISSION);
    if(profit <= 0.0) {
        if(PositionGetDouble(POSITION_SL) > 0.0) {
            return false;
        }
        op.bePrice = posManager.GetInitialSL(ticket);
        if(op.bePrice <= 0.0) return false;
        op.phase = HALF_RESTORE;
    } else {
        op.targetVolume = HalfRemainingVolume(op.initialVolume / 2.0, 0.0, step);
        op.phase = (op.targetVolume >= minLot) ? HALF_CLOSE_READY : HALF_BE_READY;
        if(op.phase == HALF_BE_READY) op.targetVolume = 0.0;
        posManager.StopTrail(ticket);
        execManager.CancelSLRequest(ticket);
    }
    if(index < 0) {
        if(g_halfOpCount >= MAX_TRACK_SIZE) return false;
        index = g_halfOpCount++;
    }
    g_halfOps[index] = op;
    if(!SaveHalfOperation(g_halfOps[index])) { g_halfOps[index].phase = HALF_PAUSED; return false; }
    ProcessOneHalfOperation(g_halfOps[index]);
    return g_halfOps[index].phase == HALF_DONE && op.phase != HALF_RESTORE;
}

void HalfAndBreakevenAll() {
    for(int i = PositionsTotal() - 1; i >= 0; i--) {
        ulong t = PositionGetTicket(i);
        if(PositionSelectByTicket(t) && PositionGetString(POSITION_SYMBOL) == _Symbol) {
            ExecuteHalfAndBreakeven(t);
        }
    }
    execManager.ExecuteQueue();
}

void UpdateExpectedLot(bool force = false) {
    static ulong nextUpdate = 0;
    ulong now = GetTickCount64();
    if(!force && now < nextUpdate) return;
    nextUpdate = now + 100;
    MqlTick tick;
    if(!SymbolInfoTick(_Symbol, tick)) return;
    double sl = ObjectFind(0, LINE_SL) >= 0 ? ObjectGetDouble(0, LINE_SL, OBJPROP_PRICE) : 0.0;
    if(sl <= 0.0) {
        ObjectSetString(0, LAB_EXPECTED_LOT, OBJPROP_TEXT, "SL 라인 미설정 (버튼을 눌러 재생성하세요)");
        ObjectSetInteger(0, LAB_EXPECTED_LOT, OBJPROP_COLOR, clrTomato);
        return;
    }
    double entry = sl < tick.bid ? tick.ask : (sl > tick.ask ? tick.bid : 0.0);
    if(entry == 0.0) {
        ObjectSetString(0, LAB_EXPECTED_LOT, OBJPROP_TEXT, "SL 위치 오류 (현재가 부근)");
        ObjectSetInteger(0, LAB_EXPECTED_LOT, OBJPROP_COLOR, clrTomato);
        return;
    }
    double lot = riskManager.CalculateLotSize(entry, sl, true);
    int digits = VolumeDigits(SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP));
    string label = lot > 0.0 ? "예상 진입: " + DoubleToString(lot, digits) + " Lot" : "진입 금지: 최소 랏 미만";
    ObjectSetString(0, LAB_EXPECTED_LOT, OBJPROP_TEXT, label);
    ObjectSetInteger(0, LAB_EXPECTED_LOT, OBJPROP_COLOR, lot > 0.0 ? clrGoldenrod : clrTomato);
}

void UpdateLabelPositions() {
    int H = (int)ChartGetInteger(0, CHART_HEIGHT_IN_PIXELS);
    
    int bh = 38, gapY = 1;
    int startY = H - (4 * bh + 3 * gapY) - 50; 
    int modeY = startY - bh - gapY; 
    
    int y0 = modeY - (Gap * 4) - 20;
    if(y0 < 50) y0 = 50;
    
    string modeStr = (Inp_ExecutionMode == MODE_FULL) ? "MODE: MAIN (FULL)" : "MODE: SUB (CONTROL ONLY)";
    color modeClr = (Inp_ExecutionMode == MODE_FULL) ? clrLimeGreen : clrGold;
    
    CreateOrMoveLabel(LAB_MODE, modeStr, 10, 20, ANCHOR_RIGHT_UPPER); 
    ObjectSetInteger(0, LAB_MODE, OBJPROP_COLOR, modeClr);
    
    CreateOrMoveLabel(LAB_SPREAD, "Spread: 0.00", 10, 20 + Gap + 5, ANCHOR_RIGHT_UPPER); 
    CreateOrMoveLabel(LAB_ATR, "ATR: 0.00", 10, 20 + (Gap * 2) + 5, ANCHOR_RIGHT_UPPER); 
    
    if(!Inp_ShowStatus) {
        ObjectDelete(0, LAB_BUY); ObjectDelete(0, LAB_SELL); ObjectDelete(0, LAB_PROFIT); ObjectDelete(0, LAB_EQ);
        return; 
    }

    CreateOrMoveLabel(LAB_BUY, "Buy Total: 0.00", OffsetBuy, y0);
    CreateOrMoveLabel(LAB_SELL, "Sell Total: 0.00", OffsetSell, y0 + Gap); 
    CreateOrMoveLabel(LAB_PROFIT, "Profit: $0.00", OffsetProfit, y0 + Gap * 2);
    CreateOrMoveLabel(LAB_EQ, "Equity: $0.00", OffsetEq, y0 + Gap * 3);
}

void CreateOrMoveLabel(string name, string txt, int x, int y, ENUM_ANCHOR_POINT anchor = ANCHOR_RIGHT_UPPER) {
    if(ObjectFind(0, name) == -1) {
        ObjectCreate(0, name, OBJ_LABEL, 0, 0, 0);
        ObjectSetString(0, name, OBJPROP_TEXT, txt);
    }
    ObjectSetInteger(0, name, OBJPROP_COLOR, clrGainsboro);
    ObjectSetInteger(0, name, OBJPROP_FONTSIZE, 10); ObjectSetString(0, name, OBJPROP_FONT, "Arial");
    ObjectSetInteger(0, name, OBJPROP_CORNER, CORNER_RIGHT_UPPER); ObjectSetInteger(0, name, OBJPROP_ANCHOR, anchor);
    ObjectSetInteger(0, name, OBJPROP_XDISTANCE, x); ObjectSetInteger(0, name, OBJPROP_YDISTANCE, y);
}

void CreateBtn(string name, string text, color bg) {
    if(ObjectFind(0, name) == -1) ObjectCreate(0, name, OBJ_BUTTON, 0, 0, 0);
    ObjectSetInteger(0, name, OBJPROP_BGCOLOR, bg); ObjectSetString(0, name, OBJPROP_FONT, "Malgun Gothic");
    ObjectSetInteger(0, name, OBJPROP_FONTSIZE, 11); ObjectSetString(0, name, OBJPROP_TEXT, text);
    ObjectSetInteger(0, name, OBJPROP_CORNER, CORNER_LEFT_UPPER);
}

void ArrangeButtons() {
    int W = (int)ChartGetInteger(0, CHART_WIDTH_IN_PIXELS), H = (int)ChartGetInteger(0, CHART_HEIGHT_IN_PIXELS);
    int bw = 55, bh = 38, gapX = 1, gapY = 1;
    int startX = W - (2 * bw + gapX) - 10;
    int startY = H - (4 * bh + 3 * gapY) - 50; 
    int modeY = startY - bh - gapY;

    ObjectSetInteger(0, EDIT_RISK, OBJPROP_XDISTANCE, startX); 
    ObjectSetInteger(0, EDIT_RISK, OBJPROP_YDISTANCE, modeY); 
    ObjectSetInteger(0, EDIT_RISK, OBJPROP_XSIZE, bw); 
    ObjectSetInteger(0, EDIT_RISK, OBJPROP_YSIZE, bh);
    
    ObjectSetInteger(0, BTN_RESET, OBJPROP_XDISTANCE, startX + bw + gapX); 
    ObjectSetInteger(0, BTN_RESET, OBJPROP_YDISTANCE, modeY); 
    ObjectSetInteger(0, BTN_RESET, OBJPROP_XSIZE, bw); 
    ObjectSetInteger(0, BTN_RESET, OBJPROP_YSIZE, bh);
    
    ObjectSetInteger(0, LAB_EXPECTED_LOT, OBJPROP_TIMEFRAMES, OBJ_ALL_PERIODS);
    ObjectSetInteger(0, LAB_EXPECTED_LOT, OBJPROP_XDISTANCE, startX);
    ObjectSetInteger(0, LAB_EXPECTED_LOT, OBJPROP_YDISTANCE, startY + 3 * (bh + gapY) + 5);

    ObjectSetInteger(0, BTN_SELL, OBJPROP_XDISTANCE, startX); 
    ObjectSetInteger(0, BTN_SELL, OBJPROP_YDISTANCE, startY); 
    ObjectSetInteger(0, BTN_SELL, OBJPROP_XSIZE, bw); 
    ObjectSetInteger(0, BTN_SELL, OBJPROP_YSIZE, bh);
    
    ObjectSetInteger(0, BTN_BUY, OBJPROP_XDISTANCE, startX + bw + gapX); 
    ObjectSetInteger(0, BTN_BUY, OBJPROP_YDISTANCE, startY); 
    ObjectSetInteger(0, BTN_BUY, OBJPROP_XSIZE, bw); 
    ObjectSetInteger(0, BTN_BUY, OBJPROP_YSIZE, bh);
    
    ObjectSetInteger(0, BTN_HALF, OBJPROP_XDISTANCE, startX); 
    ObjectSetInteger(0, BTN_HALF, OBJPROP_YDISTANCE, startY + bh + gapY); 
    ObjectSetInteger(0, BTN_HALF, OBJPROP_XSIZE, bw); 
    ObjectSetInteger(0, BTN_HALF, OBJPROP_YSIZE, bh);
    
    ObjectSetInteger(0, BTN_TRAIL, OBJPROP_XDISTANCE, startX + bw + gapX); 
    ObjectSetInteger(0, BTN_TRAIL, OBJPROP_YDISTANCE, startY + bh + gapY); 
    ObjectSetInteger(0, BTN_TRAIL, OBJPROP_XSIZE, bw); 
    ObjectSetInteger(0, BTN_TRAIL, OBJPROP_YSIZE, bh);
    
    ObjectSetInteger(0, BTN_CLOSE_PROFIT, OBJPROP_XDISTANCE, startX); 
    ObjectSetInteger(0, BTN_CLOSE_PROFIT, OBJPROP_YDISTANCE, startY + 2 * (bh + gapY)); 
    ObjectSetInteger(0, BTN_CLOSE_PROFIT, OBJPROP_XSIZE, bw); 
    ObjectSetInteger(0, BTN_CLOSE_PROFIT, OBJPROP_YSIZE, bh);
    
    ObjectSetInteger(0, BTN_CLOSEALL, OBJPROP_XDISTANCE, startX + bw + gapX); 
    ObjectSetInteger(0, BTN_CLOSEALL, OBJPROP_YDISTANCE, startY + 2 * (bh + gapY)); 
    ObjectSetInteger(0, BTN_CLOSEALL, OBJPROP_XSIZE, bw); 
    ObjectSetInteger(0, BTN_CLOSEALL, OBJPROP_YSIZE, bh);
}

void ResetSLLine(bool forceReset = false) {
    double targetPrice = GetPriceBelowPanel();
    
    if(ObjectFind(0, LINE_SL) == -1) {
        ObjectCreate(0, LINE_SL, OBJ_HLINE, 0, 0, targetPrice);
        ObjectSetInteger(0, LINE_SL, OBJPROP_COLOR, Inp_SLLineColor); 
        ObjectSetInteger(0, LINE_SL, OBJPROP_STYLE, Inp_SLLineStyle);
        ObjectSetInteger(0, LINE_SL, OBJPROP_WIDTH, Inp_SLLineWidth);
        ObjectSetInteger(0, LINE_SL, OBJPROP_SELECTABLE, true);
        ObjectSetInteger(0, LINE_SL, OBJPROP_SELECTED, true);
        ObjectSetInteger(0, LINE_SL, OBJPROP_TIMEFRAMES, OBJ_ALL_PERIODS); 
    } else if(forceReset) { 
        ObjectSetDouble(0, LINE_SL, OBJPROP_PRICE, targetPrice);
    }
    ChartRedraw(0);
}

void UpdateUI(double buyTotal, double sellTotal, double bid, double ask, double p) {
    static ulong nextUI=0; ulong now = GetTickCount64(); 
    static double prevB=-1, prevS=-1, prevP=999999, prevE=999999, prevSp=-1, prevAtr=-1; 

    if(now >= nextUI) {
        nextUI = now + 100; 
        
        double sp = (p > 0.0) ? (ask-bid)/p : 0.0; 
        if(sp != prevSp) { ObjectSetString(0,LAB_SPREAD,OBJPROP_TEXT,StringFormat("Spread: %.1f",sp)); prevSp=sp; }
        
        if(g_lastATR != prevAtr) { 
            ObjectSetString(0,LAB_ATR,OBJPROP_TEXT,StringFormat("ATR: %.2f", g_lastATR)); 
            prevAtr=g_lastATR; 
        }

        if(Inp_ShowStatus) {
            if(buyTotal != prevB) { ObjectSetString(0,LAB_BUY,OBJPROP_TEXT,StringFormat("Buy Total: %.2f",buyTotal)); prevB=buyTotal; }
            if(sellTotal != prevS) { ObjectSetString(0,LAB_SELL,OBJPROP_TEXT,StringFormat("Sell Total: %.2f",sellTotal)); prevS=sellTotal; }
            
            double prof=AccountInfoDouble(ACCOUNT_PROFIT), eq=AccountInfoDouble(ACCOUNT_EQUITY);

            if(prof != prevP) { ObjectSetString(0,LAB_PROFIT,OBJPROP_TEXT,StringFormat("Profit: $%1.2f",prof)); ObjectSetInteger(0,LAB_PROFIT,OBJPROP_COLOR,(prof>0?clrSeaGreen:(prof<0?clrIndianRed:clrSlateGray))); prevP=prof; }
            if(eq != prevE) { ObjectSetString(0,LAB_EQ,OBJPROP_TEXT,StringFormat("Equity: $%1.2f",eq)); ObjectSetInteger(0,LAB_EQ,OBJPROP_COLOR,(eq>0?clrSeaGreen:(eq<0?clrIndianRed:clrSlateGray))); prevE=eq; }
        }
    }
}

//────────────────────────────────────────────────────────────
// 라이프사이클 이벤트
//────────────────────────────────────────────────────────────
int OnInit() {
    if(!CheckExpiry()) return INIT_FAILED;
    if(!MathIsValidNumber(Inp_TargetRR) || Inp_TargetRR < 0.0 ||
       !MathIsValidNumber(Inp_Trail_ATR_Mult) || Inp_Trail_ATR_Mult < 0.0) {
        Print("RR과 트레일링 ATR 배수는 0 이상이어야 합니다.");
        return INIT_PARAMETERS_INCORRECT;
    }
    g_lastBarTime = iTime(_Symbol, _Period, 0);
    
    UpdateSymbolInfo(); 

    g_atrHandle = iATR(_Symbol, _Period, 14);
    RefreshATR();
    InitHalfOperations();
    EventSetMillisecondTimer(100);

    CreateBtn(BTN_RESET, "%", clrGoldenrod);
    
    if(ObjectFind(0, EDIT_RISK) == -1) {
        ObjectCreate(0, EDIT_RISK, OBJ_EDIT, 0, 0, 0);
        ObjectSetString(0, EDIT_RISK, OBJPROP_TEXT, StringFormat("%.1f", Inp_DefaultRiskPct)); 
        ObjectSetInteger(0, EDIT_RISK, OBJPROP_BGCOLOR, clrWhite);
        ObjectSetInteger(0, EDIT_RISK, OBJPROP_COLOR, clrBlack);
        ObjectSetInteger(0, EDIT_RISK, OBJPROP_CORNER, CORNER_LEFT_UPPER);
        ObjectSetInteger(0, EDIT_RISK, OBJPROP_ALIGN, ALIGN_CENTER);
        ObjectSetInteger(0, EDIT_RISK, OBJPROP_FONTSIZE, 11);
        ObjectSetString(0, EDIT_RISK, OBJPROP_FONT, "Malgun Gothic");
        ObjectSetInteger(0, EDIT_RISK, OBJPROP_TIMEFRAMES, OBJ_ALL_PERIODS); 
    } else {
        ObjectSetInteger(0, EDIT_RISK, OBJPROP_TIMEFRAMES, OBJ_ALL_PERIODS); 
    }

    ObjectCreate(0, LAB_EXPECTED_LOT, OBJ_LABEL, 0, 0, 0);
    ObjectSetInteger(0, LAB_EXPECTED_LOT, OBJPROP_CORNER, CORNER_LEFT_UPPER);
    ObjectSetInteger(0, LAB_EXPECTED_LOT, OBJPROP_ANCHOR, ANCHOR_LEFT_UPPER);
    ObjectSetInteger(0, LAB_EXPECTED_LOT, OBJPROP_COLOR, clrGoldenrod);
    ObjectSetString(0, LAB_EXPECTED_LOT, OBJPROP_FONT, "Malgun Gothic");
    ObjectSetInteger(0, LAB_EXPECTED_LOT, OBJPROP_FONTSIZE, 10);
    ObjectSetString(0, LAB_EXPECTED_LOT, OBJPROP_TEXT, " ");
    ObjectSetInteger(0, LAB_EXPECTED_LOT, OBJPROP_TIMEFRAMES, OBJ_ALL_PERIODS);

    ResetSLLine(false); 

    CreateBtn(BTN_BUY, "BUY", clrLimeGreen);
    CreateBtn(BTN_SELL, "SELL", clrTomato);
    CreateBtn(BTN_HALF, "HALF", clrPlum);
    CreateBtn(BTN_CLOSE_PROFIT, "PROFIT", clrLightSteelBlue); 
    CreateBtn(BTN_TRAIL, "TRAIL", clrSlateBlue); 
    CreateBtn(BTN_CLOSEALL, "CLOSE", clrSilver);
    
    ArrangeButtons(); UpdateLabelPositions(); 
    UpdateExpectedLot();
    
    if(Inp_ExecutionMode == MODE_FULL) 
        Print("▶ [EA 시작] 동작 모드: MAIN (모든 자동화 및 UI 활성화)");
    else 
        Print("▶ [EA 시작] 동작 모드: SUB (충돌 방지를 위해 수동 제어만 활성화됨)");

    return INIT_SUCCEEDED;
}

void OnDeinit(const int reason) {
    EventKillTimer();
    FlushPosStates();
    string b[7]={BTN_BUY, BTN_SELL, BTN_HALF, BTN_CLOSE_PROFIT, BTN_TRAIL, BTN_CLOSEALL, BTN_RESET};
    for(int i=0;i<7;i++) ObjectDelete(0,b[i]);
    
    ObjectDelete(0,LAB_EXPECTED_LOT);
    ObjectDelete(0,LAB_MODE);
    ObjectDelete(0,LAB_SPREAD); ObjectDelete(0,LAB_ATR); 
    ObjectDelete(0,LAB_BUY); ObjectDelete(0,LAB_SELL); ObjectDelete(0,LAB_PROFIT); ObjectDelete(0,LAB_EQ);
    
    if(reason != REASON_CHARTCHANGE) {
        ObjectDelete(0,EDIT_RISK); 
        ObjectDelete(0,LINE_SL); 
    }
    
    if(g_atrHandle != INVALID_HANDLE) IndicatorRelease(g_atrHandle);
}

ulong g_pendingAutoSL[MAX_TRACK_SIZE];
int g_pendingAutoSLCount = 0;

bool HasPendingAutoSL() { return g_pendingAutoSLCount > 0; }

void QueuePendingAutoSL(ulong ticket) {
    for(int i=0; i<g_pendingAutoSLCount; i++) if(g_pendingAutoSL[i] == ticket) return;
    if(g_pendingAutoSLCount < MAX_TRACK_SIZE) g_pendingAutoSL[g_pendingAutoSLCount++] = ticket;
}

void ProcessPendingAutoSL() {
    if(g_pendingAutoSLCount == 0 || Inp_ExecutionMode != MODE_FULL || !RefreshATR()) return;
    for(int i=g_pendingAutoSLCount-1; i>=0; i--) {
        ulong ticket = g_pendingAutoSL[i];
        bool remove = !PositionSelectByTicket(ticket);
        if(!remove) remove = PositionGetString(POSITION_SYMBOL) != _Symbol ||
            PositionGetInteger(POSITION_MAGIC) != 0 || PositionGetDouble(POSITION_SL) > 0.0 ||
            posManager.GetInitialSL(ticket) > 0.0;
        if(remove) {
            g_pendingAutoSL[i] = g_pendingAutoSL[--g_pendingAutoSLCount];
            continue;
        }
        double distance = AutoSLDistance(MinStopDist(), g_lastATR, Inp_AutoSL_ATR_Mult);
        if(distance <= 0.0) continue;
        long type = PositionGetInteger(POSITION_TYPE);
        double entry = PositionGetDouble(POSITION_PRICE_OPEN);
        double sl = NormalizeSL(type, type == POSITION_TYPE_BUY ? entry-distance : entry+distance);
        execManager.RequestInitialSL(ticket, sl);
        if(Inp_RRAction == RR_TAKE_PROFIT && PositionGetDouble(POSITION_TP) <= 0.0 &&
           posManager.GetTPMemory(ticket) <= 0.0) {
            double tp = RRPrice(type, entry, sl, Inp_TargetRR);
            if(tp > 0.0) execManager.RequestInitialTP(ticket, tp);
        }
    }
}

void OnTradeTransaction(const MqlTradeTransaction &trans, const MqlTradeRequest &request, const MqlTradeResult &result) {
    g_tradeDirty = true;
    bool ownRequest = trans.type == TRADE_TRANSACTION_REQUEST && execManager.IsOwnRequestID(result.request_id);
    bool ownSLOnly = trans.type == TRADE_TRANSACTION_REQUEST && execManager.IsOwnSLOnlyRequest(result.request_id);
    if(trans.symbol == _Symbol && (trans.type == TRADE_TRANSACTION_POSITION || trans.type == TRADE_TRANSACTION_DEAL_ADD))
        RegisterHalfPosition(trans.position);
    if(trans.type == TRADE_TRANSACTION_POSITION && trans.symbol == _Symbol && trans.price_tp > 0.0 &&
       !execManager.IsOwnSLOnlyPositionEvent(trans))
        posManager.RememberTPEvent(trans.position, trans.price_tp, execManager);
    if(trans.type == TRADE_TRANSACTION_REQUEST && request.action == TRADE_ACTION_SLTP && !ownSLOnly &&
       (result.retcode == TRADE_RETCODE_DONE || result.retcode == TRADE_RETCODE_DONE_PARTIAL))
        posManager.RememberTPEvent(request.position, request.tp, execManager, result.request_id);
    if(trans.type == TRADE_TRANSACTION_POSITION && trans.symbol == _Symbol && trans.price_sl <= 0.0 &&
       Inp_ExecutionMode == MODE_FULL && posManager.GetSLMemory(trans.position) > 0.0)
        execManager.CancelSLRequest(trans.position);
    if(trans.type == TRADE_TRANSACTION_REQUEST && request.action == TRADE_ACTION_SLTP && !ownRequest &&
       (result.retcode == TRADE_RETCODE_DONE || result.retcode == TRADE_RETCODE_DONE_PARTIAL) &&
       PositionSelectByTicket(request.position) && PositionGetString(POSITION_SYMBOL) == _Symbol &&
       MathAbs(PositionGetDouble(POSITION_SL) - request.sl) < g_point * 0.5 &&
       MathAbs(posManager.GetSLMemory(request.position) - request.sl) > g_point * 0.5)
        execManager.CancelSLRequest(request.position);
    execManager.OnTransaction(trans, request, result);
    OnHalfTransaction(trans, request, result);

    if(Inp_ExecutionMode == MODE_FULL) {
        // 사이징 지정가는 다음 틱을 기다리지 않고 즉시 취소/진입 처리
        if(trans.symbol == _Symbol && (trans.type == TRADE_TRANSACTION_ORDER_ADD || trans.type == TRADE_TRANSACTION_ORDER_DELETE || trans.type == TRADE_TRANSACTION_HISTORY_ADD))
            ProcessMobilePendingOrders();

        // 사이징 지정가 체결분: 자동 SL 부착 금지 + 즉시 청산 (아래 자동 SL 로직보다 먼저 판정)
        if(trans.type == TRADE_TRANSACTION_DEAL_ADD && trans.symbol == _Symbol && HandleSizingLimitFill(trans.order, trans.position))
            return;

        if(trans.type == TRADE_TRANSACTION_DEAL_ADD) {
            ulong t = trans.position;
            if(PositionSelectByTicket(t) && PositionGetString(POSITION_SYMBOL) == _Symbol) {
                long magic = PositionGetInteger(POSITION_MAGIC);
                double sl = PositionGetDouble(POSITION_SL);

                if(magic == 0 && sl <= 0.0 && posManager.GetInitialSL(t) <= 0.0) {
                    QueuePendingAutoSL(t);
                    ProcessPendingAutoSL();
                    execManager.ExecuteQueue();
                }
            }
        }
    }
}

//────────────────────────────────────────────────────────────
// 메인 루프 OnTick 
//────────────────────────────────────────────────────────────
void OnTick() {
    if(!CheckExpiry()) return;

    MqlTick tick;
    if(!SymbolInfoTick(_Symbol, tick)) return;
    
    if(Inp_ExecutionMode == MODE_FULL) ProcessMobilePendingOrders();

    // 꺼져 있던 동안 청산된 포지션의 저장 상태 정리: 시작 60초 후 계좌 연결 상태에서 1회만 (시작 직후 포지션 목록 미동기화 오삭제 방지)
    static ulong posStateCleanAt = 0;
    if(posStateCleanAt == 0) posStateCleanAt = GetTickCount64() + 60000;
    else if(posStateCleanAt != ULONG_MAX && GetTickCount64() >= posStateCleanAt && TerminalInfoInteger(TERMINAL_CONNECTED)) {
        CleanOrphanPosStates();
        posStateCleanAt = ULONG_MAX;
    }

    datetime currentBar = iTime(_Symbol, _Period, 0);
    bool isNewBar = (currentBar != g_lastBarTime);
    bool barClosed = (g_lastBarTime > 0 && currentBar > g_lastBarTime);
    bool priceChanged = (tick.bid != g_lastBid || tick.ask != g_lastAsk);
    
    if(barClosed) g_trailPendingBar = currentBar;
    bool trailBarDue = Inp_TrailUpdate == TRAIL_BAR_CLOSE && g_trailPendingBar == currentBar && g_trailDoneBar != currentBar;
    if(Inp_Trail_ATR_Mult > 0.0 && !ATRFresh(currentBar)) RefreshATR();
    if(!priceChanged && !isNewBar && !execManager.HasPendingClose() && !execManager.HasPendingSLTP() &&
       !HalfOperationsPending() && !HasPendingAutoSL() && !trailBarDue && !g_tradeDirty && !posManager.HasRealtimeTrail()) return;
    g_tradeDirty = false;
    g_lastBid = tick.bid; g_lastAsk = tick.ask; g_lastBarTime = currentBar;

    execManager.ProcessCloseQueue();

    double p = Pip(); 

    posManager.CleanMemory(); 

    int tTotal = PositionsTotal();
    int count = 0;
    
    static PosInfo pos[MAX_TRACK_SIZE]; 
    
    double buyTotal = 0.0, sellTotal = 0.0;
    double floatingProfit = 0.0;

    for(int i = tTotal - 1; i >= 0; i--) {
        if(count >= MAX_TRACK_SIZE) break; 

        ulong t = PositionGetTicket(i);
        if(!PositionSelectByTicket(t) || PositionGetString(POSITION_SYMBOL) != _Symbol) continue;

        pos[count].ticket   = t; pos[count].type      = PositionGetInteger(POSITION_TYPE);
        pos[count].volume   = PositionGetDouble(POSITION_VOLUME); pos[count].entry    = PositionGetDouble(POSITION_PRICE_OPEN);
        pos[count].sl       = PositionGetDouble(POSITION_SL); pos[count].tp       = PositionGetDouble(POSITION_TP); 
        pos[count].magic    = PositionGetInteger(POSITION_MAGIC); pos[count].timeMsc  = PositionGetInteger(POSITION_TIME_MSC);

        if(IsValidMagic(pos[count].magic)) {
            if(pos[count].type == POSITION_TYPE_BUY) buyTotal += pos[count].volume;
            else sellTotal += pos[count].volume;
            
            floatingProfit += PositionGetDouble(POSITION_PROFIT) + PositionGetDouble(POSITION_SWAP) + PositionGetDouble(POSITION_COMMISSION);
        }

        if(!posManager.IsTracked(t) && IsValidMagic(pos[count].magic)) RegisterHalfPosition(t);
        bool isManualSLDeleted = posManager.UpdatePosMemory(t, pos[count].tp, pos[count].sl, execManager); 
        
        if(isManualSLDeleted && IsValidMagic(pos[count].magic) && Inp_ExecutionMode == MODE_FULL) {
            // SL 삭제는 계속 반익반본/최초 SL 복구이다. 같은 틱 추격이 본절을 덮어쓰지 못하게 정지.
            posManager.StopTrail(t, posManager.IsAutoHalfDone(t));
            if(!posManager.IsAutoHalfDone(t)) {
                // 손실 구간 SL 삭제는 최초 SL 복구만 하고 반익반본 완료로 표시하지 않는다 (이후 수익권 SL 삭제/RR 도달 시 정상 실행)
                ExecuteHalfAndBreakeven(t);
            }
        }

        if(IsValidMagic(pos[count].magic) && !isManualSLDeleted) {
            double initialSL = posManager.GetInitialSL(t);
            bool isAutoHalfDone = posManager.IsAutoHalfDone(t);
            double memTP = posManager.GetTPMemory(t);
            double currentPrice = (pos[count].type == POSITION_TYPE_BUY) ? tick.bid : tick.ask;

            if(Inp_RRAction == RR_TAKE_PROFIT) {
                if(pos[count].tp <= 0.0 && memTP > 0.0) {
                    if(Inp_ExecutionMode == MODE_FULL &&
                       !(posManager.IsManualTrail(t) && !posManager.IsTrailStopped(t)) &&
                       (posManager.DeletedTPBETriggered(t) || ExitPriceReached(pos[count].type, currentPrice, memTP))) {
                        posManager.LatchDeletedTPBE(t);
                        MoveDeletedTPToBreakeven(t);
                    }
                } else {
                    // 수동 무TP 진입은 최초 SL 확인 후 한 번만 TP 설정.
                    posManager.EnsureInitialTP(t, pos[count].type, pos[count].entry, pos[count].tp, execManager);
                }
            }
            else {
                // 현재 TP는 유지. 사용자가 삭제한 TP 가격은 설정 RR보다 우선한다.
                double target = ExitTriggerPrice(pos[count].type, pos[count].entry, initialSL, pos[count].tp, memTP, Inp_TargetRR);
                if(Inp_ExecutionMode == MODE_FULL && !posManager.IsRROverridden(t) &&
                   ExitPriceReached(pos[count].type, currentPrice, target)) {
                    if(Inp_RRAction == RR_TRAILING) {
                        if(!posManager.IsTrailStopped(t)) posManager.EnableTrail(t, false, currentBar);
                    } else if(!isAutoHalfDone) {
                        ExecuteHalfAndBreakeven(t);
                    }
                }
            }
        }
        count++;
    }

    if(Inp_ExecutionMode == MODE_FULL && Inp_MaxFloatingLossPct > 0.0 && floatingProfit < 0) {
        double maxLossAmount = AccountInfoDouble(ACCOUNT_BALANCE) * (Inp_MaxFloatingLossPct / 100.0);
        if(MathAbs(floatingProfit) >= maxLossAmount) {
            static ulong lastCloseTime = 0;
            if(GetTickCount64() - lastCloseTime > 1000) { 
                PrintFormat("[계좌 보호] 최대 허용 손실 도달. 전체 청산을 실행합니다.");
                execManager.CloseAll(); lastCloseTime = GetTickCount64();
            }
        }
    }

    ProcessHalfOperations();
    ProcessPendingAutoSL();
    ProcessTrailing(count, pos, tick.bid, tick.ask, p, trailBarDue, currentBar);
    execManager.ExecuteQueue();
    UpdateUI(buyTotal, sellTotal, tick.bid, tick.ask, p);
    
    UpdateExpectedLot();
    if(Inp_Trail_ATR_Mult == 0.0 && (isNewBar || !ATRFresh(currentBar))) RefreshATR();
}

// 로컬 전송 실패 재시도/본절 ACK 확인은 시세가 정지해도 진행한다.
// 서버 ACK를 받지 못한 반청산 요청은 시간 경과만으로 재발송하지 않는다.
void OnTimer() {
    if(!CheckExpiry()) return;
    ProcessHalfOperations();
    ProcessPendingAutoSL();
    execManager.ExecuteQueue();
    FlushPosStates();
}

//────────────────────────────────────────────────────────────
// OnChartEvent
//────────────────────────────────────────────────────────────
void OnChartEvent(const int id,const long &l,const double &d,const string &s) {
    if(id==CHARTEVENT_CHART_CHANGE) { ArrangeButtons(); UpdateLabelPositions(); return; }
    
    if(id==CHARTEVENT_KEYDOWN) {
        int k=(int)l;
        int key_sl_up = StringGetCharacter(HotKeySLup, 0); if(key_sl_up>='a' && key_sl_up<='z') key_sl_up-=32;
        int key_sl_down = StringGetCharacter(HotKeySLdown, 0); if(key_sl_down>='a' && key_sl_down<='z') key_sl_down-=32;
        int key_sl_unify = StringGetCharacter(HotKeySLunify, 0); if(key_sl_unify>='a' && key_sl_unify<='z') key_sl_unify-=32;

        int key_tp_up = StringGetCharacter(HotKeyTPup, 0); if(key_tp_up>='a' && key_tp_up<='z') key_tp_up-=32;
        int key_tp_down = StringGetCharacter(HotKeyTPdown, 0); if(key_tp_down>='a' && key_tp_down<='z') key_tp_down-=32;
        int key_tp_unify = StringGetCharacter(HotKeyTPunify, 0); if(key_tp_unify>='a' && key_tp_unify<='z') key_tp_unify-=32;

        if(k==key_sl_up) MoveSL(25); if(k==key_sl_down) MoveSL(-25); if(k==key_sl_unify) { UnifySL(0); UnifySL(1); }
        if(k==key_tp_up) MoveTP(25); if(k==key_tp_down) MoveTP(-25); if(k==key_tp_unify) { UnifyTP(0); UnifyTP(1); }
        
        if(k==KeyCode(HotKey_Buy)) ExecuteEntry(ORDER_TYPE_BUY);
        if(k==KeyCode(HotKey_Sell)) ExecuteEntry(ORDER_TYPE_SELL);
        if(k==KeyCode(HotKeyCloseProfit)) CloseProfitPositions();
        if(k==KeyCode(HotKeyCloseAll)) execManager.CloseAll();
        if(k==KeyCode(HotKeyHalf)) HalfAndBreakevenAll(); 
        if(k==KeyCode(HotKeyTrail)) StartManualTrailAll(); 
    }
    
    if(id==CHARTEVENT_OBJECT_CLICK) {
        if(s==BTN_RESET) ResetSLLine(true); 
        if(s==BTN_BUY) ExecuteEntry(ORDER_TYPE_BUY);
        if(s==BTN_SELL) ExecuteEntry(ORDER_TYPE_SELL);
        if(s==BTN_HALF) HalfAndBreakevenAll(); 
        if(s==BTN_CLOSE_PROFIT) CloseProfitPositions();
        if(s==BTN_CLOSEALL) execManager.CloseAll();
        if(s==BTN_TRAIL) StartManualTrailAll(); 
        
        ObjectSetInteger(0, s, OBJPROP_STATE, false);
    }
    UpdateExpectedLot(true);
    FlushPosStates();
}