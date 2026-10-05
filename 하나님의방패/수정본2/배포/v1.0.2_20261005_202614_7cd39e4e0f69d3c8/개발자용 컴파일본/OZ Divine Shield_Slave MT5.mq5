// BEGIN DIVINE_SHIELD_DEPLOYMENT_5
// Startup-only, same policy as the Moses deployment. No mid-position forced unload.
// Expiry is PC-local calendar midnight after 2026-12-31 (inclusive).
bool DS5DeploymentAllowed(bool refresh=false)
{
   static bool checked=false;
   static bool allowed=false;
   if(refresh) { checked=false; allowed=false; }
   if(checked) return allowed;
   checked=true;
   string probe="__ds5_DivineShield_Slave_"+IntegerToString(ChartID())+"_"+
      IntegerToString((long)GetTickCount64())+"_"+
      IntegerToString((long)GetMicrosecondCount())+".tmp";
   ResetLastError();
   int handle=FileOpen(probe,FILE_WRITE|FILE_BIN);
   if(handle==INVALID_HANDLE) { Print("[DivineShield] License clock unavailable."); return false; }
   uint written=FileWriteInteger(handle,1,CHAR_VALUE);
   FileFlush(handle);
   FileClose(handle);
   long wall=FileGetInteger(probe,FILE_MODIFY_DATE);
   bool deleted=FileDelete(probe);
   ResetLastError();
   allowed=(written==1 && deleted && wall>0 && wall<1798761600);
   if(!allowed) Print("[DivineShield] License expired or clock verification failed.");
   return allowed;
}
// END DIVINE_SHIELD_DEPLOYMENT_5

//+------------------------------------------------------------------+
//                                   OZ Divine Shield_Slave MT5      |
//+------------------------------------------------------------------+

#property copyright "Oilve Oil"
#property version   "1.70"
#property strict

#include <Trade\Trade.mqh>
CTrade trade;

input group "==== [Trade Settings] ===="
input double RiskPercent = 0.5;                 // 1회 진입 리스크(%)       
input double MaxDailyLossPercent = 2.5;         // 일일 최대 잔고 손실 한도(%)   
input int    Inp_MaxPositions          = 1;     // 최대 허용 포지션 갯수 (0: OFF)
input int    Inp_MaxDailyLossTrades    = 4;     // 일일 누적 최대 손실 횟수 (0: OFF)
input int    Inp_ConsecutiveLossTrades = 2;     // 연속 손실 허용 횟수 (0: OFF)
input int    Inp_CooldownMinutes       = 60;    // 연속 손실 후 쿨다운 대기 시간 (분)

input group "==== [Symbol Mapping] ===="        // 심볼 수동 맵핑
input string Inp_SymbolMapping = ""; 

input group "==== [System Settings] ===="
input ulong  MagicNumber = 777777;              // 매직넘버  
input int    MaxRetries = 1;                    // 주문 실패 시 재시도 횟수  
input int    Inp_SignalDelayMs = 0;             // 신호 딜레이 (0: OFF)

input group "==== [Telegram Settings] ===="
input bool   Inp_EnableTelegram = false;     // 텔레그램 알림 활성화
input string Inp_AccountAlias   = "";        // 계좌 별칭
input string Inp_BotToken       = "";        // Bot Token
input string Inp_ChatID         = "";        // Chat ID

double initialBalance = 0;

ulong  lastSyncTick = 0;
bool   isMasterDisconnected = false;
string telegramQueue[1024];


bool isDailyLocked = false;
int r8_dailyDay=0;
ulong r8_dailyCancelTick=0; 

struct TicketMap { ulong masterTicket; ulong slaveTicket; ulong slaveIdentifier; double initialSL; double initialTP; };
TicketMap mapping[];

struct PendingOrder { ulong orderTicket; ulong masterTicket; ulong positionId; ulong nextCancelTick; double initialSL; double initialTP; };
PendingOrder pendingOrders[];

struct R8CloseIntent {
    ulong masterId,positionId,order,nextTick;
    double ratio,target;
    bool all,awaiting,uncertain;
    int attempts;
};
R8CloseIntent r8_closes[];
int r8_closeScan=0;

struct SymbolMapConfig { 
    string masterSym; 
    string slaveSym; 
};
SymbolMapConfig symMapping[];

// [5단계] MMF_Protocol_step03.md v2.0 수신 전용. 매매 처리기는 기존 코드를 사용한다.
#define FILE_MAP_READ 0x0004
#define INVALID_HANDLE_VALUE -1
#define R5_N 4096
#define R5_HEADER 256
#define R5_SLOT 1024
#define R5_EVENTS 4194560
#define R5_STATE 65536
#define R5_STATE_BODY 65408
#define R5_BATCH 16
#define R5_MAX 9223372036854775807
#import "kernel32.dll"
long OpenFileMappingW(uint access, int inherit, string name);
long MapViewOfFile(long mapping, uint access, uint high, uint low, ulong bytes);
int UnmapViewOfFile(long address);
int CloseHandle(long handle);
long OpenMutexW(uint access, int inherit, string name);
uint WaitForSingleObject(long handle, uint milliseconds);
int ReleaseMutex(long handle);
void RtlMoveMemory(uchar &destination[], long source, ulong bytes);
#import


#import "kernel32.dll"
long CreateFileMappingW(long file,long attributes,uint protect,uint high,uint low,string name);
long CreateMutexW(long attributes,int owner,string name);
long CreateSemaphoreW(long attributes,int initial,int maximum,string name);
int ReleaseSemaphore(long handle,int releaseCount,long previousCount);
void RtlMoveMemory(long destination,uchar &source[],ulong bytes);
#import
#import "ole32.dll"
int CoCreateGuid(uchar &guid[]);
#import

input group "==== [MMF v2 수신] ===="
input string Inp_StreamId = "OZ_MAIN";
input ulong Inp_ExpectedSourceLogin = 0; // 0: 빈 계좌 최초 연결 때 소스에 바인딩
input string Inp_ExpectedSourceServer = "";

struct R5Envelope {
    string session;
    ulong sequence;
    ulong publishedTick;
    ulong executeTick;
    string payload;
};
struct R5Raw { uchar data[R5_SLOT]; };
R5Raw r5_raw[R5_BATCH];
R5Envelope r5_inbox[R5_N];
int r5_head = 0, r5_count = 0;
long r5_eventsMap = 0, r5_eventsView = 0, r5_stateMap = 0, r5_stateView = 0, r5_mutex = 0;
bool r5_bound = false, r5_hold = false, r5_lockHeld = false;
string r5_reason = "", r5_sessionText = "", r5_sourceServer = "";
ulong r5_sourceLogin = 0, r5_targetLogin = 0, r5_accepted = 0, r5_published = 0, r5_alive = 0;
string r5_targetServer = "";
ulong r5_stateVersion = 0, r5_stateBase = 0, r5_appliedState = 0;
string r5_sync = "";
uchar r5_session[16], r5_header[R5_HEADER], r5_stateHeader[128], r5_stateBody[R5_STATE_BODY];

void R5Put16(uchar &data[], int offset, ushort value) {
    for(int i = 0; i < 2; i++) data[offset + i] = (uchar)(value >> (8 * i));
}
void R5Put32(uchar &data[], int offset, uint value) {
    for(int i = 0; i < 4; i++) data[offset + i] = (uchar)(value >> (8 * i));
}
void R5Put64(uchar &data[], int offset, ulong value) {
    for(int i = 0; i < 8; i++) data[offset + i] = (uchar)(value >> (8 * i));
}
uint R5Get32(const uchar &data[], int offset) {
    uint value = 0;
    for(int i = 0; i < 4; i++) value |= ((uint)data[offset + i]) << (8 * i);
    return value;
}
ulong R5Get64(const uchar &data[], int offset) {
    ulong value = 0;
    for(int i = 0; i < 8; i++) value |= ((ulong)data[offset + i]) << (8 * i);
    return value;
}
uint R5Crc32(const uchar &data[], int offset, int count, int zeroOffset = -1) {
    uint crc = 0xFFFFFFFF;
    for(int i = offset; i < offset + count; i++) {
        uchar value = data[i];
        if(zeroOffset >= 0 && i >= zeroOffset && i < zeroOffset + 4) value = 0;
        crc ^= (uint)value;
        for(int bit = 0; bit < 8; bit++) {
            if((crc & 1) != 0) crc = (crc >> 1) ^ (uint)0xEDB88320;
            else crc >>= 1;
        }
    }
    return crc ^ (uint)0xFFFFFFFF;
}
bool R5EqualBytes(const uchar &a[], int aOffset, const uchar &b[], int bOffset, int count) {
    for(int i = 0; i < count; i++) if(a[aOffset + i] != b[bOffset + i]) return false;
    return true;
}
bool R5ValidStream(string value) {
    int n = StringLen(value);
    if(n < 1 || n > 32) return false;
    for(int i = 0; i < n; i++) {
        ushort c = StringGetCharacter(value, i);
        if(!((c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9') || c == '_')) return false;
    }
    return true;
}
bool R5SafeValue(string value) {
    if(StringLen(value) == 0) return false;
    for(int i = 0; i < StringLen(value); i++) {
        ushort c = StringGetCharacter(value, i);
        if(c == 0 || c == '\r' || c == '\n' || c == '|' || c == '=') return false;
    }
    return true;
}
bool R5UInt64(string value, ulong &number) {
    number = 0;
    int n = StringLen(value);
    if(n == 0 || n > 20) return false;
    for(int i = 0; i < n; i++) {
        ushort c = StringGetCharacter(value, i);
        if(c < '0' || c > '9') return false;
        uint digit = (uint)(c - '0');
        // UINT64_MAX / 10 및 마지막 자리. 부동소수점 변환 없이 범위를 검사한다.
        if(number > 1844674407370955161 || (number == 1844674407370955161 && digit > 5)) return false;
        number = number * 10 + digit;
    }
    return true;
}
bool R5Decimal(string value, bool positive) {
    int n = StringLen(value);
    if(n == 0) return false;
    bool dot = false;
    for(int i = 0; i < n; i++) {
        ushort c = StringGetCharacter(value, i);
        if(c == '.') {
            if(dot || i == 0 || i == n - 1) return false;
            dot = true;
        } else if(c < '0' || c > '9') return false;
    }
    double number = StringToDouble(value);
    return MathIsValidNumber(number) && (positive ? number > 0.0 : number >= 0.0);
}
bool R5Encode(string text, int maximum, uchar &bytes[], int &length, string &error) {
    length = 0;
    if(StringLen(text) == 0) { error = "EMPTY_PAYLOAD"; return false; }
    for(int i = 0; i < StringLen(text); i++) {
        ushort c = StringGetCharacter(text, i);
        if(c == 0 || c == '\r' || c == '\n' || c == 0xFEFF) {
            error = "PAYLOAD_NUL_LINE_OR_BOM"; return false;
        }
    }
    int copied = StringToCharArray(text, bytes, 0, WHOLE_ARRAY, CP_UTF8);
    if(copied <= 1 || bytes[copied - 1] != 0) { error = "UTF8_ENCODING_FAILED"; return false; }
    length = copied - 1;
    if(length > maximum) { error = "PAYLOAD_TOO_LONG"; return false; }
    if(ArrayResize(bytes, length) != length) { error = "PAYLOAD_ALLOCATION_FAILED"; return false; }
    return true;
}
int R5FieldIndex(string key) {
    if(key == "ACTION") return 0;
    if(key == "SYMBOL") return 1;
    if(key == "PRICE") return 2;
    if(key == "SL") return 3;
    if(key == "TP") return 4;
    if(key == "SL_DIST") return 5;
    if(key == "TICKET") return 6;
    if(key == "TARGET_VOL") return 7;
    if(key == "CLOSED_VOL") return 8;
    return -1;
}
bool R5ValidateCommand(string msg, string &error) {
    if(StringLen(msg) == 0 || StringGetCharacter(msg, StringLen(msg) - 1) == '|') {
        error = "EMPTY_OR_TRAILING_FIELD"; return false;
    }
    string parts[];
    string values[9];
    int mask = 0;
    int count = StringSplit(msg, '|', parts);
    if(count <= 0) { error = "EMPTY_COMMAND"; return false; }
    for(int i = 0; i < count; i++) {
        int eq = StringFind(parts[i], "=");
        if(eq <= 0) { error = "INVALID_FIELD"; return false; }
        int field = R5FieldIndex(StringSubstr(parts[i], 0, eq));
        string value = StringSubstr(parts[i], eq + 1);
        if(field < 0 || (mask & (1 << field)) != 0 || !R5SafeValue(value)) {
            error = "UNKNOWN_DUPLICATE_OR_INVALID_FIELD"; return false;
        }
        values[field] = value;
        mask |= (1 << field);
    }
    string action = values[0];
    bool entry = action == "BUY" || action == "SELL";
    int expected = entry ? 127 : (action == "MODIFY" ? 123 : (action == "CLOSE" ? 451 : 0));
    if(expected == 0 || mask != expected) { error = "ACTION_OR_REQUIRED_FIELDS"; return false; }
    ulong ticket;
    if(!R5UInt64(values[6], ticket) || ticket == 0) { error = "INVALID_TICKET"; return false; }
    if(entry && (!R5Decimal(values[2], true) || !R5Decimal(values[3], true) ||
                 !R5Decimal(values[4], false) || !R5Decimal(values[5], true))) {
        error = "INVALID_ENTRY_NUMBER"; return false;
    }
    if(action == "MODIFY" && (!R5Decimal(values[3], false) || !R5Decimal(values[4], false) || !R5Decimal(values[5], false))) {
        error = "INVALID_MODIFY_NUMBER"; return false;
    }
    if(action == "CLOSE" && (!R5Decimal(values[7], false) || !R5Decimal(values[8], true))) {
        error = "INVALID_CLOSE_NUMBER"; return false;
    }
    return true;
}
bool R5ValidateSync(string msg, string &error) {
    string prefix = "ACTION=SYNC|TICKETS=";
    if(StringFind(msg, prefix) != 0) { error = "INVALID_SYNC_PREFIX"; return false; }
    string tickets = StringSubstr(msg, StringLen(prefix));
    if(tickets == "NONE") return true;
    if(!R5SafeValue(tickets)) { error = "INVALID_SYNC_LIST"; return false; }
    if(StringGetCharacter(tickets, StringLen(tickets) - 1) == ',') { error = "EMPTY_SYNC_ROW"; return false; }
    string rows[];
    int count = StringSplit(tickets, ',', rows);
    if(count <= 0) { error = "EMPTY_SYNC_LIST"; return false; }
    for(int i = 0; i < count; i++) {
        string fields[];
        if(StringSplit(rows[i], ':', fields) != 3) { error = "INVALID_SYNC_ROW"; return false; }
        ulong ticket;
        if(!R5UInt64(fields[0], ticket) || ticket == 0 || !R5Decimal(fields[1], false) || !R5Decimal(fields[2], false)) {
            error = "INVALID_SYNC_VALUE"; return false;
        }
    }
    return true;
}


// [6단계] State Kind=2 / PayloadVersion=1. v2 헤더/링 배치는 변경하지 않는다.
struct S6Row {
    ulong id, ticket, entrySeq, lastSeq;
    string symbol, side;
    double initialVolume, volume, price, sl, tp, step, copyPrice, copyDistance;
    int status; // 0=BASELINE, 1=PENDING_ENTRY, 2=PUBLISHED, 3=UNOBSERVED
};
bool S6UInt(string value, ulong &number) { return R5UInt64(value,number); }
string S6Field(string text, string key) {
    string parts[]; StringSplit(text,'|',parts);
    string prefix=key+"=";
    for(int i=0;i<ArraySize(parts);i++) if(StringFind(parts[i],prefix)==0) return StringSubstr(parts[i],StringLen(prefix));
    return "";
}
string S6HexSymbol(string symbol) {
    uchar bytes[]; int n; string error;
    if(!R5Encode(symbol,256,bytes,n,error)) return "";
    string encoded="";
    for(int i=0;i<n;i++) encoded+=StringFormat("%02X",(uint)bytes[i]);
    return encoded;
}
int S6Nibble(ushort c) {
    if(c>='0'&&c<='9') return (int)(c-'0');
    if(c>='A'&&c<='F') return (int)(c-'A')+10;
    return -1;
}
bool S6Symbol(string hex, string &symbol) {
    int n=StringLen(hex);
    if(n<2||n>512||n%2!=0) return false;
    uchar bytes[]; ArrayResize(bytes,n/2);
    for(int i=0;i<n;i+=2) {
        int a=S6Nibble(StringGetCharacter(hex,i)), b=S6Nibble(StringGetCharacter(hex,i+1));
        if(a<0||b<0) return false;
        bytes[i/2]=(uchar)(a*16+b);
    }
    symbol=CharArrayToString(bytes,0,n/2,CP_UTF8);
    return S6HexSymbol(symbol)==hex;
}
bool S6Parse(string payload, ulong boundary, S6Row &rows[], bool &consistent, ulong &revision, string &reason) {
    ArrayResize(rows,0); consistent=false; revision=0; reason="";
    string parts[];
    if(StringSplit(payload,'|',parts)!=8) return false;
    string keys[8]={"ACTION=SNAPSHOT","SCHEMA=1","CUT=","REV=","CONSISTENT=","REASON=","COUNT=","ROWS="};
    if(parts[0]!=keys[0]||parts[1]!=keys[1]) return false;
    for(int i=2;i<8;i++) if(StringFind(parts[i],keys[i])!=0) return false;
    ulong cut,count;
    if(!S6UInt(StringSubstr(parts[2],4),cut)||cut!=boundary||!S6UInt(StringSubstr(parts[3],4),revision)||!S6UInt(StringSubstr(parts[6],6),count)||count>1024) return false;
    if(parts[4]!="CONSISTENT=0"&&parts[4]!="CONSISTENT=1") return false;
    consistent=parts[4]=="CONSISTENT=1"; reason=StringSubstr(parts[5],7);
    if(reason!="OK"&&reason!="PENDING_EVENTS"&&reason!="CHANGING"&&reason!="UNSUPPORTED"&&reason!="HISTORY_UNAVAILABLE"&&reason!="SIGNAL_STATE_MISMATCH") return false;
    if(consistent!=(reason=="OK")) return false;
    string list=StringSubstr(parts[7],5);
    if(count==0) return list=="NONE";
    if(StringLen(list)==0||StringGetCharacter(list,StringLen(list)-1)==';') return false;
    string entries[];
    if(StringSplit(list,';',entries)!=(int)count) return false;
    if(ArrayResize(rows,(int)count)!=(int)count) return false;
    ulong previous=0;
    for(int i=0;i<(int)count;i++) {
        string f[];
        if(StringLen(entries[i])==0||StringGetCharacter(entries[i],StringLen(entries[i])-1)==','||StringSplit(entries[i],',',f)!=15) return false;
        S6Row row; ulong status;
        if(!S6UInt(f[0],row.id)||row.id<=previous||!S6UInt(f[1],row.ticket)||row.ticket==0||!S6Symbol(f[2],row.symbol)||(f[3]!="BUY"&&f[3]!="SELL")) return false;
        row.side=f[3];
        for(int j=4;j<=9;j++) if(!R5Decimal(f[j],j==4||j==5||j==6||j==9)) return false;
        if(!S6UInt(f[10],row.entrySeq)||!S6UInt(f[11],row.lastSeq)||row.entrySeq>row.lastSeq||row.lastSeq>cut) return false;
        if(!R5Decimal(f[12],false)||!R5Decimal(f[13],false)||!S6UInt(f[14],status)||status>3) return false;
        row.initialVolume=StringToDouble(f[4]); row.volume=StringToDouble(f[5]); row.price=StringToDouble(f[6]);
        row.sl=StringToDouble(f[7]); row.tp=StringToDouble(f[8]); row.step=StringToDouble(f[9]);
        row.copyPrice=StringToDouble(f[12]); row.copyDistance=StringToDouble(f[13]); row.status=(int)status;
        if(status==2) { if(row.entrySeq==0||row.copyPrice<=0||row.copyDistance<=0) return false; }
        else if(row.entrySeq!=0||row.copyPrice!=0||row.copyDistance!=0) return false;
        if(consistent&&(status==3||row.volume>row.initialVolume+row.step*0.0001)) return false;
        rows[i]=row; previous=row.id;
    }
    return true;
}
string S6Legacy(const S6Row &rows[]) {
    string body="";
    for(int i=0;i<ArraySize(rows);i++) {
        if(i>0) body+=",";
        body+=StringFormat("%I64u",rows[i].id)+":"+DoubleToString(rows[i].sl,8)+":"+DoubleToString(rows[i].tp,8);
    }
    return "ACTION=SYNC|TICKETS="+(body==""?"NONE":body);
}

struct R6Local {
    ulong id, entrySeq, lastSeq, slaveId;
    string symbol, side;
    bool attempted, noOpen, localExit, closeSeen, adopted;
    int closeCount;
    double sourceRemaining, expectedVolume, initialVolume, sourceInitial;
};
R6Local r6_local[];
S6Row r6_rows[];
bool r6_consistent=false;
ulong r6_revision=0, r6_reported=0;
string r6_reason="";
uint r6_kind=1;

int R6Index(ulong id, bool create=false) {
    for(int i=0;i<ArraySize(r6_local);i++) if(r6_local[i].id==id) return i;
    if(!create) return -1;
    int n=ArraySize(r6_local);
    if(n>=65536||ArrayResize(r6_local,n+1)!=n+1) { R5Hold("LOCAL_AUDIT_CAPACITY"); return -1; }
    r6_local[n].id=id; r6_local[n].entrySeq=0; r6_local[n].lastSeq=0; r6_local[n].slaveId=0;
    r6_local[n].adopted=false; r6_local[n].attempted=false; r6_local[n].noOpen=false; r6_local[n].localExit=false; r6_local[n].closeSeen=false;
    r6_local[n].closeCount=0; r6_local[n].sourceRemaining=-1; r6_local[n].expectedVolume=-1; r6_local[n].initialVolume=0; r6_local[n].sourceInitial=0;
    return n;
}
void R6Before(const R5Envelope &item) {
    ulong id; if(!S6UInt(S6Field(item.payload,"TICKET"),id)) return;
    int n=R6Index(id,true); if(n<0) return;
    string action=S6Field(item.payload,"ACTION"); r6_local[n].lastSeq=item.sequence;
    if(action=="BUY"||action=="SELL") {
        r6_local[n].entrySeq=item.sequence; r6_local[n].side=action; r6_local[n].symbol=S6Field(item.payload,"SYMBOL");
    }
    if(action=="CLOSE") {
        r6_local[n].closeSeen=true; r6_local[n].closeCount++;
        r6_local[n].sourceRemaining=StringToDouble(S6Field(item.payload,"TARGET_VOL"));
    }
}
void R6After(const R5Envelope &item) {
    string action=S6Field(item.payload,"ACTION"); if(action!="BUY"&&action!="SELL") return;
    ulong id; S6UInt(S6Field(item.payload,"TICKET"),id); int n=R6Index(id); if(n<0) return;
    bool active=false;
    for(int i=0;i<ArraySize(mapping);i++) if(mapping[i].masterTicket==id) active=true;
    for(int i=0;i<ArraySize(pendingOrders);i++) if(pendingOrders[i].masterTicket==id) active=true;
    r6_local[n].noOpen=!active;
}
void R6Attempt(ulong id) { int n=R6Index(id,true); if(n>=0) r6_local[n].attempted=true; }
void R6PlanClose(ulong id, double remaining) { int n=R6Index(id,true); if(n>=0) r6_local[n].expectedVolume=remaining; }
void R6Bind(ulong id, ulong slave) {
    int n=R6Index(id,true); if(n<0) return;
    r6_local[n].slaveId=slave;
    if(PositionSelectByTicket(slave)) r6_local[n].slaveId=(ulong)PositionGetInteger(POSITION_IDENTIFIER);
    r6_local[n].noOpen=false;
}
void R6LocalExit(ulong slaveId) {
    for(int i=0;i<ArraySize(r6_local);i++) if(r6_local[i].slaveId==slaveId) r6_local[i].localExit=true;
}
double R6FilledInitial(ulong slaveId) {
    if(slaveId==0||!HistorySelectByPosition(slaveId)) return 0;
    double total=0;
    for(int i=0;i<HistoryDealsTotal();i++) {
        ulong d=HistoryDealGetTicket(i); long type=HistoryDealGetInteger(d,DEAL_TYPE);
        if((type==DEAL_TYPE_BUY||type==DEAL_TYPE_SELL)&&HistoryDealGetInteger(d,DEAL_ENTRY)==DEAL_ENTRY_IN) total+=HistoryDealGetDouble(d,DEAL_VOLUME);
    }
    return total;
}
bool R6Load(string payload, ulong baseSeq, string &legacy) {
    if(!S6Parse(payload,baseSeq,r6_rows,r6_consistent,r6_revision,r6_reason)) return false;
    legacy=S6Legacy(r6_rows); return true;
}
string r12_compareKeys[],r12_compareStates[];
void R12SnapshotLog(string key,string state,string message) {
    int found=-1;
    for(int i=0;i<ArraySize(r12_compareKeys);i++)if(r12_compareKeys[i]==key){found=i;break;}
    if(found<0){found=ArraySize(r12_compareKeys);if(found>=4096)return;ArrayResize(r12_compareKeys,found+1);ArrayResize(r12_compareStates,found+1);r12_compareKeys[found]=key;}
    if(r12_compareStates[found]==state)return;r12_compareStates[found]=state;Print(message);
}
bool R6Reconcile() {
    bool report=r6_reported!=r5_stateVersion; r6_reported=r5_stateVersion;
    if(!r6_consistent) {
        if(report) R12SnapshotLog("snapshot",r6_reason,"[SNAPSHOT_DEFERRED] Reason="+r6_reason);
        return false;
    }
    R12SnapshotLog("snapshot","OK", "[SNAPSHOT] CONSISTENT");
    bool ok=true;
    for(int i=0;i<ArraySize(r6_rows);i++) {
        S6Row row=r6_rows[i]; int n=R6Index(row.id); string status="MATCH";
        int m=-1;
        for(int j=0;j<ArraySize(mapping);j++) if(mapping[j].masterTicket==row.id) { m=j; break; }
        if(row.status!=2&&!(n>=0&&r6_local[n].adopted)) {
            if(m>=0) { status="UNPUBLISHED_SOURCE_MAPPING"; ok=false; }
            else status=row.status==1?"SOURCE_PENDING_ENTRY":"SOURCE_BASELINE";
        } else if(n<0||r6_local[n].entrySeq!=row.entrySeq||r6_local[n].lastSeq!=row.lastSeq) { status="SIGNAL_HISTORY_MISMATCH"; ok=false; }
        else if(r6_local[n].symbol!=row.symbol||r6_local[n].side!=row.side) { status="SOURCE_IDENTITY_MISMATCH"; ok=false; }
        else if(r6_local[n].closeSeen&&MathAbs(r6_local[n].sourceRemaining-row.volume)>row.step*0.0001) { status="SOURCE_VOLUME_MISMATCH"; ok=false; }
        else if(m<0||!R8SelectMapping(m)) {
            if(r6_local[n].localExit) status="LOCAL_EXIT";
            else if(r6_local[n].noOpen) status=r6_local[n].attempted?"ENTRY_NOT_OPENED":"ENTRY_NOT_SUBMITTED";
            else { status="MISSING_LOCAL_POSITION"; ok=false; }
        } else {
            ulong slave=mapping[m].slaveTicket;
            string symbol=PositionGetString(POSITION_SYMBOL);
            string side=PositionGetInteger(POSITION_TYPE)==POSITION_TYPE_BUY?"BUY":"SELL";
            double volume=PositionGetDouble(POSITION_VOLUME),step=SymbolInfoDouble(symbol,SYMBOL_VOLUME_STEP);
            double localSL=PositionGetDouble(POSITION_SL),localTP=PositionGetDouble(POSITION_TP);
            for(int j=0;j<ArraySize(mapping);j++) if(j!=m&&mapping[j].slaveTicket==slave) { status="SHARED_LOCAL_POSITION"; ok=false; }
            if(side!=row.side||symbol!=GetMappedSymbol(row.symbol)) { status="LOCAL_IDENTITY_MISMATCH"; ok=false; }
            r6_local[n].initialVolume=R6FilledInitial(r6_local[n].slaveId);
            if(r6_local[n].localExit) status="LOCAL_EXIT";
            else if(r6_local[n].closeSeen&&r6_local[n].expectedVolume>=0&&MathAbs(volume-r6_local[n].expectedVolume)>step*0.0001) { status="CLOSE_TARGET_MISMATCH"; ok=false; }
            else if(r6_local[n].initialVolume<=0) { status="LOCAL_HISTORY_UNAVAILABLE"; ok=false; }
            else {
                double ideal=r6_local[n].initialVolume*row.volume/row.initialVolume;
                double rounding=(double)r6_local[n].closeCount*step;
                if(volume<ideal-step*0.0001||volume>ideal+rounding+step*0.0001) { status="VOLUME_RATIO_MISMATCH"; ok=false; }
            }
            if(status=="MATCH") {
                double point=SymbolInfoDouble(symbol,SYMBOL_POINT);
                if((row.sl>0&&MathAbs(localSL-row.sl)>point)||MathAbs(localTP-row.tp)>point) status="PROTECTION_DIFFERENCE";
            }
        }
        if(report) R12SnapshotLog("ticket:"+StringFormat("%I64u",row.id),status,
            "[SNAPSHOT_COMPARE] MasterId="+StringFormat("%I64u",row.id)+" | "+status+
            " | SourceInitial="+DoubleToString(row.initialVolume,8)+" | SourceRemaining="+DoubleToString(row.volume,8));
    }
    for(int i=0;i<ArraySize(mapping);i++) {
        bool found=false;
        for(int j=0;j<ArraySize(r6_rows);j++) if(r6_rows[j].id==mapping[i].masterTicket) found=true;
        if(!found&&R8SelectMapping(i)) {
            ok=false;
            if(report) R12SnapshotLog("ticket:"+StringFormat("%I64u",mapping[i].masterTicket),"SOURCE_CLOSED_LOCAL_REMAINS", "[SNAPSHOT_COMPARE] SOURCE_CLOSED_LOCAL_REMAINS | MasterId="+StringFormat("%I64u",mapping[i].masterTicket));
        }
    }
    for(int i=0;i<PositionsTotal();i++) {
        ulong ticket=PositionGetTicket(i);
        if(ticket==0||(ulong)PositionGetInteger(POSITION_MAGIC)!=MagicNumber) continue;
        bool found=false;
        for(int j=0;j<ArraySize(mapping);j++) if(mapping[j].slaveTicket==ticket) found=true;
        if(!found) {
            ok=false;
            if(report) R12SnapshotLog("local:"+StringFormat("%I64u",ticket),"UNMAPPED_LOCAL_POSITION", "[SNAPSHOT_COMPARE] UNMAPPED_LOCAL_POSITION | SlaveTicket="+StringFormat("%I64u",ticket));
        }
    }
    // 차이를 관측했다고 주문을 생성하지 않는다. 전체 상태 복구 주문은 영구 이력 대조 이후 단계다.
    return ok;
}

// [7단계] 재시도 간격은 타이머로 기다린다. Inbox의 같은 명령을 보유하여 순서를 유지한다.
struct R7EntryRetry {
    bool active;
    ulong masterId, nextTick;
    string action, symbol, ticket;
    double volume, sl, tp;
    int attempted, limit;
};
R7EntryRetry r7_entry;
ulong r7_dispatchSequence=0;
string r7_dispatchSession="";
int r7_pendingScan=0;

// R8: 주문의 terminal 상태와 해당 주문의 전체 체결 이력 반영을 함께 확인한다.
bool R8OrderSettled(ulong order,ulong &positionId) {
    if(OrderSelect(order)) {
        ulong id=(ulong)OrderGetInteger(ORDER_POSITION_ID);
        if(id>0) positionId=id;
        return false;
    }
    if(!HistoryOrderSelect(order)) return false;
    long state=HistoryOrderGetInteger(order,ORDER_STATE);
    if(state!=ORDER_STATE_FILLED&&state!=ORDER_STATE_CANCELED&&state!=ORDER_STATE_REJECTED&&state!=ORDER_STATE_EXPIRED) return false;
    double filled=HistoryOrderGetDouble(order,ORDER_VOLUME_INITIAL)-HistoryOrderGetDouble(order,ORDER_VOLUME_CURRENT);
    ulong id=(ulong)HistoryOrderGetInteger(order,ORDER_POSITION_ID);
    if(id>0) positionId=id;
    if(filled<=0.00000001) return state!=ORDER_STATE_FILLED;
    if(positionId==0||!HistorySelectByPosition(positionId)) return false;
    double seen=0;
    for(int d=0;d<HistoryDealsTotal();d++) {
        ulong deal=HistoryDealGetTicket(d);
        if((ulong)HistoryDealGetInteger(deal,DEAL_ORDER)==order) seen+=HistoryDealGetDouble(deal,DEAL_VOLUME);
    }
    return seen+0.00000001>=filled;
}
void R7SettlePending() {
    if((ulong)AccountInfoInteger(ACCOUNT_LOGIN)!=r5_targetLogin||AccountInfoString(ACCOUNT_SERVER)!=r5_targetServer) return;
    int budget=MathMin(16,ArraySize(pendingOrders));
    for(int pass=0;pass<budget&&ArraySize(pendingOrders)>0;pass++) {
        if(r7_pendingScan>=ArraySize(pendingOrders)) r7_pendingScan=0;
        int i=r7_pendingScan;
        bool settled=R8OrderSettled(pendingOrders[i].orderTicket,pendingOrders[i].positionId);
        ulong id=pendingOrders[i].masterTicket,current=0;
        if(pendingOrders[i].positionId>0&&R8SelectIdentifier(pendingOrders[i].positionId,current)) {
            AddMapping(id,current,pendingOrders[i].initialSL,pendingOrders[i].initialTP);
        } else if(settled&&pendingOrders[i].positionId>0&&!R8ConfirmedClosed(pendingOrders[i].positionId)) {
            // 이력/포지션 반영이 지연되는 동안 추적 상태를 버리지 않는다.
            r7_pendingScan++; continue;
        }
        if(!settled) { r7_pendingScan++; continue; }
        int n=R6Index(id);
        if(n>=0) {
            r6_local[n].noOpen=current==0&&pendingOrders[i].positionId==0;
            if(current==0&&pendingOrders[i].positionId>0) r6_local[n].localExit=true;
        }
        Print("[ENTRY_ORDER_SETTLED] MasterId=",id," | Order=",pendingOrders[i].orderTicket);
        for(int j=i;j<ArraySize(pendingOrders)-1;j++) pendingOrders[j]=pendingOrders[j+1];
        ArrayResize(pendingOrders,ArraySize(pendingOrders)-1);
    }
}
void R8CancelPendingEntry(ulong masterId) {
    ulong now=GetTickCount64();
    for(int i=0;i<ArraySize(pendingOrders);i++) {
        if(pendingOrders[i].masterTicket!=masterId||now<pendingOrders[i].nextCancelTick) continue;
        if(!OrderSelect(pendingOrders[i].orderTicket)) continue;
        pendingOrders[i].nextCancelTick=now+1000;
        CTrade cancel; cancel.SetExpertMagicNumber(MagicNumber);
        bool ok=cancel.OrderDelete(pendingOrders[i].orderTicket);
        Print("[ENTRY_CANCEL_REQUEST] MasterId=",masterId," | Order=",pendingOrders[i].orderTicket,
              " | LocalResult=",ok," | Retcode=",cancel.ResultRetcode());
    }
}

bool R7HasPending(ulong id) {
    for(int i=0;i<ArraySize(pendingOrders);i++) if(pendingOrders[i].masterTicket==id) return true;
    return false;
}
bool R7Retryable(uint code) {
    return code==TRADE_RETCODE_REQUOTE||code==TRADE_RETCODE_PRICE_CHANGED||
           code==TRADE_RETCODE_PRICE_OFF||code==TRADE_RETCODE_TOO_MANY_REQUESTS;
}
bool R7DefiniteReject(uint code) {
    return code==TRADE_RETCODE_REJECT||code==TRADE_RETCODE_CANCEL||
           (code>=10013&&code<=10019)||code==10022||code==10026||code==10027||
           code==10029||code==10030||(code>=10032&&code<=10046);
}
void R7AttemptEntry() {
    if(!r7_entry.active||GetTickCount64()<r7_entry.nextTick) return;
    if(GetSlaveTicket(r7_entry.ticket)>0||R7HasPending(r7_entry.masterId)) {
        r7_entry.active=false; return;
    }
    // 첫 시도는 ProcessMessage의 검사 결과를 사용한다. 대기 뒤에는 기존 리스크 조건을 다시 확인한다.
    if(r7_entry.attempted>0&&!IsEntryAllowed()) {
        Print("[RETRY_CANCELLED_BY_RISK] MasterId=",r7_entry.masterId);
        r7_entry.active=false; return;
    }
    r7_entry.attempted++;
    R6Attempt(r7_entry.masterId);
    // 이전 보호/청산 호출의 Result를 재사용하지 않도록 시도마다 독립 CTrade 결과를 사용한다.
    CTrade entryTrade; entryTrade.SetExpertMagicNumber(MagicNumber);
    if(!entryTrade.SetTypeFillingBySymbol(r7_entry.symbol)) {
        Print("[ENTRY_FILLING_UNAVAILABLE] ",r7_entry.symbol); r7_entry.active=false; return;
    }
    bool localResult=false;
    if(r7_entry.action=="BUY") localResult=entryTrade.Buy(r7_entry.volume,r7_entry.symbol,0,r7_entry.sl,r7_entry.tp,"");
    else if(r7_entry.action=="SELL") localResult=entryTrade.Sell(r7_entry.volume,r7_entry.symbol,0,r7_entry.sl,r7_entry.tp,"");
    uint code=entryTrade.ResultRetcode();
    ulong order=entryTrade.ResultOrder(),deal=entryTrade.ResultDeal();
    Print("[RETRY_ORDER_RESULT] MasterId=",r7_entry.masterId," | Attempt=",r7_entry.attempted,
          " | Limit=",r7_entry.limit," | LocalResult=",localResult," | Retcode=",code," | Order=",order," | Deal=",deal);
    if(code==TRADE_RETCODE_DONE||code==TRADE_RETCODE_PLACED||code==TRADE_RETCODE_DONE_PARTIAL) {
        if(order==0&&deal>0&&HistoryDealSelect(deal)) order=(ulong)HistoryDealGetInteger(deal,DEAL_ORDER);
        if(order==0) { R5Hold("ENTRY_ACCEPTED_WITHOUT_TRACKING_ID"); return; }
        AddPendingOrder(order,r7_entry.masterId,r7_entry.sl,r7_entry.tp);
        AddToTelegramQueue("?? [진입 접수] "+r7_entry.symbol+" | 방향: "+r7_entry.action+
                           " | 요청 물량: "+DoubleToString(r7_entry.volume,2)+" Lot");
        r7_entry.active=false; return;
    }
    // 결과가 불명확하면 반복 제출하지 않는다. 이후 체결은 기존 pending 매핑으로 추적한다.
    if(order>0||deal>0||(!R7Retryable(code)&&!R7DefiniteReject(code))) {
        if(order>0) AddPendingOrder(order,r7_entry.masterId,r7_entry.sl,r7_entry.tp);
        R5Hold("ENTRY_RESULT_UNCERTAIN"); return;
    }
    if(R7Retryable(code)&&r7_entry.attempted<r7_entry.limit) {
        r7_entry.nextTick=GetTickCount64()+200;
        Print("[RETRY_WAIT] MasterId=",r7_entry.masterId," | NextTick=",r7_entry.nextTick);
        return;
    }
    Print("[ENTRY_NOT_OPENED] MasterId=",r7_entry.masterId," | Retcode=",code," | Attempts=",r7_entry.attempted);
    if(code==TRADE_RETCODE_SERVER_DISABLES_AT)
        AddToTelegramQueue("?? [진입 차단] 서버에서 자동매매가 비활성화되었습니다. 에러 10026");
    r7_entry.active=false;
}





// R11 control channel: independent of the unchanged trade MMF v2 layout.
#define C11_BYTES 33024
#define C11_HEAD 256
#define C11_SLOT 512
#define C11_COUNT 64
#define C11_MAGIC 0x3131524F
#define C11_ACKMAGIC 0x3131414F
void C11Put32(uchar &b[],int p,uint n) { for(int j=0;j<4;j++) b[p+j]=(uchar)(n>>(8*j)); }
void C11Put64(uchar &b[],int p,ulong n) { for(int j=0;j<8;j++) b[p+j]=(uchar)(n>>(8*j)); }
uint C11U32(const uchar &b[],int p) { uint n=0; for(int j=0;j<4;j++) n|=(uint)b[p+j]<<(8*j); return n; }
ulong C11U64(const uchar &b[],int p) { ulong n=0; for(int j=0;j<8;j++) n|=(ulong)b[p+j]<<(8*j); return n; }
uint C11CRC(const uchar &b[],int count,int zero) {
    uint c=0xFFFFFFFF;
    for(int i=0;i<count;i++) { c^=(uint)((i>=zero&&i<zero+4)?0:b[i]); for(int j=0;j<8;j++) c=(c&1)!=0?(c>>1)^(uint)0xEDB88320:c>>1; }
    return c^(uint)0xFFFFFFFF;
}
bool C11Same(const uchar &a[],int ap,const uchar &b[],int bp,int n) { for(int i=0;i<n;i++) if(a[ap+i]!=b[bp+i]) return false; return true; }
bool C11Zero(const uchar &a[],int p,int n) { for(int i=0;i<n;i++) if(a[p+i]!=0) return false; return true; }
bool C11Text(uchar &b[],int p,int max,int lp,string value) {
    uchar data[]; int n=StringToCharArray(value,data,0,WHOLE_ARRAY,CP_UTF8)-1;
    if(n<0||n>max||StringFind(value,"\n")>=0||StringFind(value,"\r")>=0) return false;
    C11Put32(b,lp,(uint)n); if(n>0) ArrayCopy(b,data,p,0,n); return true;
}
string C11String(const uchar &b[],int p,int lp) { return CharArrayToString(b,p,(int)C11U32(b,lp),CP_UTF8); }
string C11Hex(const uchar &b[],int p) { string s="";for(int i=0;i<16;i++) s+=StringFormat("%02X",(uint)b[p+i]);return s; }
bool C11HeaderValid(const uchar &b[]) {
    uint n=C11U32(b,72);
    return C11U32(b,0)==C11_MAGIC&&C11U32(b,4)==1&&C11U32(b,8)==C11_BYTES&&C11U32(b,12)==256&&
        C11U32(b,16)==512&&C11U32(b,20)==64&&C11U64(b,24)>0&&!C11Zero(b,32,16)&&
        n>0&&n<=96&&C11Zero(b,80+(int)n,96-(int)n)&&C11U32(b,184)<=3&&C11Zero(b,188,68)&&
        C11U32(b,76)==C11CRC(b,256,76);
}
bool C11AckValid(const uchar &b[]) {
    uint a=C11U32(b,72),s=C11U32(b,76),r=C11U32(b,80),p=C11U32(b,8);
    return C11U32(b,0)==C11_ACKMAGIC&&(C11U32(b,4)==1||C11U32(b,4)==2)&&(p==1||p==2)&&C11U32(b,12)<=6&&
        a>0&&a<=96&&s>0&&s<=96&&r<=128&&!C11Zero(b,56,16)&&C11U32(b,440)<=1&&
        ((C11U32(b,4)==1&&C11Zero(b,444,68))||(C11U32(b,4)==2&&p==2&&C11U32(b,444)<=1&&C11U64(b,448)>0&&C11Zero(b,464,48)))&&
        C11U32(b,84)==C11CRC(b,512,84);
}
bool C11Fresh(ulong now,ulong then,uint limit=5000) { return then>0&&now>=then&&now-then<=limit; }
void C11Burst(string tag,string reason,string detail="") {
    string line="========== ["+tag+"] "+reason+" ==========";
    for(int i=0;i<5;i++) Print(line);
    if(detail!="") Print("[MMF DETAIL] ",detail);
}

// R11 transport recovery. Existing order tracking/risk/close intents are NEVER cleared by rebind.
enum R11CommState { R11_RUNNING=0,R11_TEMP=1,R11_HARD=2 };
R11CommState r11_state=R11_TEMP;
bool r11_needRebase=true,r11_manual=false,r11_adopting=false,r11_checkFailureLogged=false;
ulong r11_pollTick=0,r11_stateTick=0,r11_lastAdoptVersion=0,r11_requestSeen=0,r11_requestStarted=0;
string r11_businessFault="";
string r11_requestSession="",r11_riskReason="",r11_pinFile="",r11_lastFailure="";
long c11_map=0,c11_view=0,c11_mutex=0;
int c11_slot=-1;
uchar c11_h[256],c11_guid[16];
ulong c11_next=0,r11_riskNext=0;
bool c11_intentionalStop=false;
bool r11_riskDirty=true,r11_cachedRisk=false;
string r11_cachedRiskReason="";
void R11Temp(string reason) {
    if(r11_state==R11_HARD) return;
    if(r11_state!=R11_TEMP) C11Burst("MMF TEMP DISCONNECTED",reason,"Stream="+Inp_StreamId);
    r11_state=R11_TEMP;r11_needRebase=true;r11_lastAdoptVersion=0;isMasterDisconnected=true;r5_reason=reason;
    r7_entry.active=false; // unsubmitted retries are never replayed after an outage
}
bool R11CommunicationFault(string reason) {
    return reason=="FIRST_BIND_REQUIRES_FLAT"||reason=="UNMAPPED_LOCAL_POSITION"||reason=="LOCAL_IDENTITY_MISMATCH"||reason=="UNTRACKED_LIVE_ORDER"||reason=="RECOVERY_REQUIRES_FULL_SNAPSHOT"||reason=="EVENT_HEADER_CRC_OR_LAYOUT"||reason=="STATE_HEADER_CRC_OR_LAYOUT"||reason=="STATE_PAYLOAD_INVALID"||
        reason=="FULL_SNAPSHOT_INVALID"||reason=="SOURCE_UTF8"||reason=="COMMAND_FRAME_OR_PAYLOAD_INVALID"||
        reason=="SOURCE_CHANGED"||reason=="TARGET_ACCOUNT_CHANGED"||reason=="EXECUTION_HEADER_INVALID"||reason=="COPY_WAIT_FAILED"||reason=="COPY_RELEASE_FAILED"||
        reason=="RING_GAP"||reason=="SEQUENCE_REGRESSION"||reason=="STATE_VERSION_REGRESSION"||reason=="SOURCE_REVISION_REGRESSION";
}
// R5Hold implemented at original call site
bool R11Pin(bool save) {
    r11_pinFile="OZ_R11_Source_"+R8LocalPrefix()+"txt";
    if(save) {
        int f=FileOpen(r11_pinFile,FILE_WRITE|FILE_TXT|FILE_UNICODE);
        if(f==INVALID_HANDLE) return false;
        FileWrite(f,StringFormat("%I64u",r5_sourceLogin));FileWrite(f,r5_sourceServer);FileFlush(f);FileClose(f);return true;
    }
    if(!FileIsExist(r11_pinFile)) return true;
    int f=FileOpen(r11_pinFile,FILE_READ|FILE_TXT|FILE_UNICODE);if(f==INVALID_HANDLE) return false;
    string id=FileReadString(f),server=FileReadString(f);FileClose(f);ulong login=0;
    if(!R5UInt64(id,login)||login==0||server=="") return false;
    if((Inp_ExpectedSourceLogin!=0&&login!=Inp_ExpectedSourceLogin)||(Inp_ExpectedSourceServer!=""&&server!=Inp_ExpectedSourceServer)) return false;
    r5_sourceLogin=login;r5_sourceServer=server;return true;
}
bool R11SameSource(ulong login,string server) {
    if(login==0||server=="") return false;
    if((Inp_ExpectedSourceLogin!=0&&login!=Inp_ExpectedSourceLogin)||(Inp_ExpectedSourceServer!=""&&server!=Inp_ExpectedSourceServer)) return false;
    return r5_sourceLogin==0||(login==r5_sourceLogin&&server==r5_sourceServer);
}
bool R11EntryReady() {
    r11_riskReason="";
    if((ulong)AccountInfoInteger(ACCOUNT_LOGIN)!=r5_targetLogin||AccountInfoString(ACCOUNT_SERVER)!=r5_targetServer) { r11_riskReason="TARGET_ACCOUNT_CHANGED";return false; }
    if(r11_state!=R11_RUNNING||r5_hold||!r5_bound||!C11Fresh(GetTickCount64(),r5_alive)) { r11_riskReason="TRANSPORT_NOT_READY";return false; }
    if(!TerminalInfoInteger(TERMINAL_CONNECTED)) { r11_riskReason="BROKER_DISCONNECTED";return false; }
    if(!TerminalInfoInteger(TERMINAL_TRADE_ALLOWED)||!MQLInfoInteger(MQL_TRADE_ALLOWED)||
       !AccountInfoInteger(ACCOUNT_TRADE_ALLOWED)||!AccountInfoInteger(ACCOUNT_TRADE_EXPERT)) { r11_riskReason="TRADING_DISABLED";return false; }
    if(isDailyLocked) { r11_riskReason="DAILY_LOCK";return false; }
    if(!r6_consistent||r5_stateBase!=r5_published||r5_accepted!=r5_published) { r11_riskReason="SOURCE_SYNC_PENDING";return false; }
    if(r5_count>0||r7_entry.active||ArraySize(r8_closes)>0||ArraySize(pendingOrders)>0) { r11_riskReason="ACCOUNT_PROCESSING";return false; }
    ulong now=GetTickCount64();
    if(r11_riskDirty||now>=r11_riskNext) {
        r11_cachedRisk=IsEntryAllowed(false);r11_cachedRiskReason=r11_riskReason;r11_riskDirty=false;
        r11_riskNext=now+(r11_cachedRiskReason=="COOLDOWN"?1000:30000);
    }
    r11_riskReason=r11_cachedRiskReason;return r11_cachedRisk;
}
void C11Close() {
    if(c11_view!=0) { UnmapViewOfFile(c11_view);c11_view=0; }
    if(c11_map!=0) { CloseHandle(c11_map);c11_map=0; }
    if(c11_mutex!=0) { CloseHandle(c11_mutex);c11_mutex=0; }c11_slot=-1;
}
bool C11Open() {
    if(c11_view!=0&&c11_mutex!=0) return true;
    string root="Local\\OZCopy.Control11."+Inp_StreamId;
    c11_map=OpenFileMappingW(0xF001F,0,root+".State");c11_mutex=OpenMutexW(0x00100001,0,root+".Lock");
    if(c11_map!=0) c11_view=MapViewOfFile(c11_map,0xF001F,0,0,C11_BYTES);
    if(c11_view==0||c11_mutex==0) { C11Close();return false; }return true;
}
void C11Service() {
    ulong now=GetTickCount64();if(now<c11_next) return;c11_next=now+250;
    if(!C11Open()) return;
    uint rc=WaitForSingleObject(c11_mutex,0);if(rc!=0&&rc!=0x80) return;
    RtlMoveMemory(c11_h,c11_view,(ulong)256);uchar all[C11_BYTES-C11_HEAD];
    if(c11_slot<0) RtlMoveMemory(all,c11_view+256,(ulong)(C11_BYTES-256));
    bool released=ReleaseMutex(c11_mutex)!=0;
    if(!released) { C11Close();return; }
    if(!C11HeaderValid(c11_h)||!C11Fresh(now,C11U64(c11_h,176))) return;
    ulong request=C11U64(c11_h,48);string session=C11Hex(c11_h,32),server=C11String(c11_h,80,72);
    bool same=R11SameSource(C11U64(c11_h,24),server);
    if(request>0&&(session!=r11_requestSession||request!=r11_requestSeen)) {
        r11_requestSeen=request;r11_requestSession=session;r11_requestStarted=now;r11_lastFailure="";r11_checkFailureLogged=false;r11_riskDirty=true;
        if(!same) { R5Hold("SOURCE_CHANGED");C11Burst("MMF RECOVERY FAILED","SOURCE_ACCOUNT_MISMATCH"); }
        else if(C11U32(c11_h,184)==1&&r11_state==R11_RUNNING&&C11Fresh(now,r5_alive)&&session==r5_sessionText) {
            C11Burst("MMF CONNECTION CHECK","ALREADY CONNECTED","Receiver=MT5:"+StringFormat("%I64u",r5_targetLogin));
        } else {
            r11_manual=true;
            if(r11_state==R11_HARD&&R11CommunicationFault(r5_reason)) { r5_hold=false;r11_state=R11_TEMP; }
            if(r11_state!=R11_HARD) { R11Temp("MANUAL_RECOVERY");R5Disconnect(); }
        }
    }
    string target=StringFormat("%I64u",r5_targetLogin);
    if(c11_slot<0) {
        int empty=-1;
        for(int i=0;i<64;i++) {
            uchar a[512];ArrayCopy(a,all,0,i*512,512);
            if(C11Zero(a,0,512)&&empty<0) empty=i;
            if(C11AckValid(a)&&C11U32(a,8)==1&&C11String(a,96,72)==target&&C11String(a,192,76)==r5_targetServer) { c11_slot=i;break; }
        }
        if(c11_slot<0) c11_slot=empty;
        if(c11_slot<0) { if(r11_lastFailure!="CONTROL_CAPACITY") C11Burst("MMF RECOVERY FAILED","CONTROL_CAPACITY");r11_lastFailure="CONTROL_CAPACITY";return; }
    }
    bool intentionalDisabled=c11_intentionalStop||!TerminalInfoInteger(TERMINAL_TRADE_ALLOWED)||!MQLInfoInteger(MQL_TRADE_ALLOWED);
    bool link=C11U32(c11_h,184)==1&&same&&r11_state==R11_RUNNING&&C11Fresh(now,r5_alive)&&session==r5_sessionText;
    bool ready=!intentionalDisabled&&link&&R11EntryReady();
    string reason=intentionalDisabled?(c11_intentionalStop?"USER_STOPPED":"TRADING_DISABLED"):(link?(ready?"READY":r11_riskReason):r5_reason);
    uint status=intentionalDisabled?6:(link?(ready?1:4):(r11_state==R11_HARD?3:2));
    if(r11_manual&&!link&&now-r11_requestStarted>5000&&!r11_checkFailureLogged) {
        C11Burst("MMF RECOVERY FAILED",reason==""?"WAITING_FOR_VALID_MASTER":reason);r11_lastFailure=reason;r11_checkFailureLogged=true;
    }
    uchar out[512];ArrayInitialize(out,0);C11Put32(out,0,C11_ACKMAGIC);C11Put32(out,4,1);C11Put32(out,8,1);C11Put32(out,12,status);
    C11Put64(out,16,r5_targetLogin);C11Put64(out,24,r11_requestSeen);C11Put64(out,32,now);
    if(session==r11_requestSession) ArrayCopy(out,c11_h,40,32,16);
    ArrayCopy(out,c11_guid,56,0,16);C11Put64(out,88,r5_sourceLogin);
    if(!C11Text(out,96,96,72,target)||!C11Text(out,192,96,76,r5_targetServer)||!C11Text(out,288,128,80,reason)) return;
    ArrayCopy(out,r5_session,416,0,16);C11Put64(out,432,r5_accepted);C11Put32(out,440,ready?1:0);C11Put32(out,84,C11CRC(out,512,84));
    rc=WaitForSingleObject(c11_mutex,0);if(rc!=0&&rc!=0x80) return;
    uchar old[512];RtlMoveMemory(old,c11_view+256+c11_slot*512,(ulong)512);
    bool own=C11Zero(old,0,512)||(C11AckValid(old)&&C11U32(old,8)==1&&C11U64(old,16)==r5_targetLogin&&C11Same(old,192,out,192,96));
    if(own) RtlMoveMemory(c11_view+256+c11_slot*512,out,(ulong)512);
    released=ReleaseMutex(c11_mutex)!=0;
    if(!own) c11_slot=-1;if(!released) C11Close();
}
// Recovery target = original LOCAL fill * source remaining fraction, never raw source lots.
bool R11AdoptSnapshot(ulong login,string server,ulong published,ulong version,ulong baseSeq) {
    if(!r6_consistent||baseSeq!=published||!C11Fresh(GetTickCount64(),r11_stateTick)||!C11Fresh(GetTickCount64(),r5_alive)) return false;
    if(r11_businessFault!=""||(r11_state==R11_HARD&&!R11CommunicationFault(r5_reason))) return false;
    if(!R11SameSource(login,server)||!R5TargetSame()) return false;
    if(!TerminalInfoInteger(TERMINAL_CONNECTED)) { R11Temp("BROKER_DISCONNECTED");return false; }
    r7_entry.active=false;
    for(int p=0;p<ArraySize(pendingOrders);p++) R8CancelPendingEntry(pendingOrders[p].masterTicket);
    if(ArraySize(pendingOrders)>0) return false;
    // No foreign/untracked own-magic position or live order is silently adopted.
    for(int i=0;i<OrdersTotal();i++) {
        ulong order=OrderGetTicket(i);if(order>0&&(ulong)OrderGetInteger(ORDER_MAGIC)==MagicNumber&&!R8IsCloseOrder(order)) { R5Hold("UNTRACKED_LIVE_ORDER");return false; }
    }
    for(int i=0;i<PositionsTotal();i++) {
        ulong t=PositionGetTicket(i);if(t==0||(ulong)PositionGetInteger(POSITION_MAGIC)!=MagicNumber) continue;
        ulong id=(ulong)PositionGetInteger(POSITION_IDENTIFIER);bool known=false;
        for(int m=0;m<ArraySize(mapping);m++) if(mapping[m].slaveIdentifier==id) known=true;
        if(!known) { R5Hold("UNMAPPED_LOCAL_POSITION");return false; }
    }
    // First validate all live mappings; only then schedule any corrective actions.
    for(int m=0;m<ArraySize(mapping);m++) {
        if(!R8SelectMapping(m)) { if(!R8ConfirmedClosed(mapping[m].slaveIdentifier)) return false;continue; }
        string symbol=PositionGetString(POSITION_SYMBOL),side=PositionGetInteger(POSITION_TYPE)==POSITION_TYPE_BUY?"BUY":"SELL";
        for(int j=0;j<ArraySize(r6_rows);j++) if(r6_rows[j].id==mapping[m].masterTicket) {
            if(r6_rows[j].side!=side||GetMappedSymbol(r6_rows[j].symbol)!=symbol) { R5Hold("LOCAL_IDENTITY_MISMATCH");return false; }
        }
    }
    for(int j=0;j<ArraySize(r6_rows);j++) {
        S6Row row=r6_rows[j];int n=R6Index(row.id,true);if(n<0) return false;
        r6_local[n].entrySeq=row.entrySeq;r6_local[n].lastSeq=row.lastSeq;r6_local[n].symbol=row.symbol;r6_local[n].side=row.side;
        r6_local[n].sourceInitial=row.initialVolume;r6_local[n].sourceRemaining=row.volume;r6_local[n].adopted=true;
        int m=-1;for(int k=0;k<ArraySize(mapping);k++) if(mapping[k].masterTicket==row.id) { m=k;break; }
        if(m<0||!R8SelectMapping(m)) {
            r6_local[n].noOpen=true; // skipped during outage; never catch up a BUY
            continue;
        }
        double current=PositionGetDouble(POSITION_VOLUME),initial=0,exited=0;
        string symbol=PositionGetString(POSITION_SYMBOL);
        if(!R8HistoryVolume(mapping[m].slaveIdentifier,initial,exited)) return false;
        double step=SymbolInfoDouble(symbol,SYMBOL_VOLUME_STEP),minimum=SymbolInfoDouble(symbol,SYMBOL_VOLUME_MIN);
        if(MathAbs(initial-exited-current)>step*0.0001) return false;
        double ratio=row.volume/row.initialVolume,target=R8RemainingTarget(initial,ratio,step,minimum);
        if(target<0) { R5Hold("RECOVERY_TARGET_INVALID");return false; }
        r6_local[n].slaveId=mapping[m].slaveIdentifier;r6_local[n].initialVolume=initial;r6_local[n].noOpen=false;
        r6_local[n].expectedVolume=MathMin(target,current);r6_local[n].closeSeen=true;
        if(current<target-step*0.0001) r6_local[n].localExit=true; // no automatic top-up
        if(current>target+step*0.0001) {
            int c=R8CloseIndex(row.id,mapping[m].slaveIdentifier,true);if(c<0) return false;
            if(!r8_closes[c].all) r8_closes[c].ratio=MathMin(r8_closes[c].ratio,ratio);
        }
    }
    for(int m=0;m<ArraySize(mapping);m++) {
        bool found=false;for(int j=0;j<ArraySize(r6_rows);j++) if(r6_rows[j].id==mapping[m].masterTicket) found=true;
        if(!found&&R8SelectMapping(m)) R8QueueFlat(mapping[m].slaveIdentifier,"R11 verified source closed");
    }
    // Commit transport boundary only after complete validated snapshot; preserve all order state.
    bool newPin=r5_sourceLogin==0;
    r5_sourceLogin=login;r5_sourceServer=server;
    if(newPin&&!R11Pin(true)) { R5Hold("SOURCE_PIN_SAVE_FAILED");return false; }
    ArrayCopy(r5_session,r5_header,0,32,16);r5_sessionText=R5Hex(r5_session,0);
    r5_accepted=published;r5_published=published;r5_bound=true;r5_stateBase=baseSeq;r5_stateVersion=version;
    for(int i=0;i<R5_N;i++) r5_inbox[i].payload="";
    r5_head=0;r5_count=0;r7_dispatchSequence=0;r7_dispatchSession="";r5_appliedState=0;
    r5_hold=false;r11_state=R11_TEMP;r11_lastAdoptVersion=version;
    // Existing targets/protection may need settlement. Do not report READY before reconciliation.
    if(ArraySize(r8_closes)>0) return false;
    r11_adopting=true;bool matched=R6Reconcile();r11_adopting=false;
    if(!matched) return false;
    ProcessMessage(r5_sync); // only verified existing mappings; no entry handler
    if(ArraySize(r8_closes)>0) return false;
    r11_needRebase=false;r11_state=R11_RUNNING;isMasterDisconnected=false;r5_reason="";
    C11Burst(r11_manual?"MMF FORCE RECOVERED":"MMF AUTO RECOVERED","SAME MASTER CONNECTED",
        "Stream="+Inp_StreamId+" | Session="+r5_sessionText+" | Cut="+StringFormat("%I64u",published));
    r11_manual=false;r11_lastFailure="";return true;
}
// ReadFromMMF implemented at original call site
// R5ObserveHeader implemented at original call site

bool R5Zeros(const uchar &b[], int start, int count) {
    for(int i = start; i < start + count; i++) if(b[i] != 0) return false;
    return true;
}
uint R5Get16(const uchar &b[], int p) { return (uint)b[p] | ((uint)b[p + 1] << 8); }
bool R5HeaderValid(const uchar &b[]) {
    if(b[0]!='O'||b[1]!='Z'||b[2]!='M'||b[3]!='M'||b[4]!='F'||b[5]!='R'||b[6]!='2'||b[7]!=0) return false;
    if(R5Get16(b,8)!=2||R5Get16(b,10)!=0||R5Get32(b,12)!=256||R5Get32(b,16)!=1024||R5Get32(b,20)!=4096||R5Get64(b,24)!=R5_EVENTS) return false;
    if(R5Zeros(b,32,16)||R5Get64(b,48)>(ulong)R5_MAX||R5Get32(b,64)>3||R5Get32(b,68)!=0||R5Get64(b,72)==0) return false;
    uint n=R5Get32(b,84);
    if(n<1||n>96||!R5Zeros(b,88+(int)n,168-(int)n)) return false;
    return R5Get32(b,80)==R5Crc32(b,0,256,80);
}
bool R5StateHeaderValid() {
    if(r5_stateHeader[0]!='O'||r5_stateHeader[1]!='Z'||r5_stateHeader[2]!='M'||r5_stateHeader[3]!='M'||r5_stateHeader[4]!='F'||r5_stateHeader[5]!='S'||r5_stateHeader[6]!='2'||r5_stateHeader[7]!=0) return false;
    if(R5Get16(r5_stateHeader,8)!=2||R5Get16(r5_stateHeader,10)!=0||R5Get32(r5_stateHeader,12)!=128||R5Get32(r5_stateHeader,16)!=65536) return false;
    uint n=R5Get32(r5_stateHeader,20);
    if(n<1||n>R5_STATE_BODY||!R5EqualBytes(r5_stateHeader,24,r5_header,32,16)) return false;
    if(R5Get64(r5_stateHeader,40)==0||R5Get64(r5_stateHeader,48)>R5Get64(r5_header,48)) return false;
    if((R5Get16(r5_stateHeader,72)!=1&&R5Get16(r5_stateHeader,72)!=2)||R5Get16(r5_stateHeader,74)!=1||R5Get32(r5_stateHeader,76)!=1||!R5Zeros(r5_stateHeader,80,48)) return false;
    return R5Get32(r5_stateHeader,68)==R5Crc32(r5_stateHeader,0,128,68);
}
bool R5Decode(const uchar &b[], int start, int n, string &text) {
    text=CharArrayToString(b,start,n,CP_UTF8);
    uchar encoded[]; int length; string error;
    if(!R5Encode(text,n,encoded,length,error)||length!=n) return false;
    return R5EqualBytes(b,start,encoded,0,n); // 불완전 UTF-8/종료 NUL/치환 문자 왕복 오류 거부
}
string R5Hex(const uchar &b[], int start) {
    string result="";
    for(int i=0;i<16;i++) result+=StringFormat("%02X",(uint)b[start+i]);
    return result;
}
void R5Hold(string reason) {
    if(!R11CommunicationFault(reason)) { if(r11_businessFault=="") r11_businessFault=reason; reason=r11_businessFault; }
    else if(r11_businessFault!="") return; // transport errors must not mask an uncertain live order
    if(r11_state!=R11_HARD) C11Burst("MMF RECOVERY REQUIRED",reason,"Stream="+Inp_StreamId);
    r5_hold=true;r5_reason=reason;r11_state=R11_HARD;r11_needRebase=true;isMasterDisconnected=true;
}
void R5Disconnect() {
    if(r5_lockHeld && r5_mutex!=0) { if(ReleaseMutex(r5_mutex)!=0) r5_lockHeld=false; }
    if(r5_eventsView!=0) { UnmapViewOfFile(r5_eventsView); r5_eventsView=0; }
    if(r5_stateView!=0) { UnmapViewOfFile(r5_stateView); r5_stateView=0; }
    if(r5_eventsMap!=0) { CloseHandle(r5_eventsMap); r5_eventsMap=0; }
    if(r5_stateMap!=0) { CloseHandle(r5_stateMap); r5_stateMap=0; }
    if(r5_mutex!=0) { CloseHandle(r5_mutex); r5_mutex=0; }
}
bool R5Connect() {
    if(r5_eventsView!=0&&r5_stateView!=0&&r5_mutex!=0) return true;
    string root="Local\\OZCopy.v2."+Inp_StreamId;
    r5_eventsMap=OpenFileMappingW(FILE_MAP_READ,0,root+".Events");
    r5_stateMap=OpenFileMappingW(FILE_MAP_READ,0,root+".State");
    r5_mutex=OpenMutexW(0x00100001,0,root+".CopyLock"); // SYNCHRONIZE | MUTEX_MODIFY_STATE
    if(r5_eventsMap!=0&&r5_stateMap!=0&&r5_mutex!=0) {
        r5_eventsView=MapViewOfFile(r5_eventsMap,FILE_MAP_READ,0,0,R5_EVENTS);
        r5_stateView=MapViewOfFile(r5_stateMap,FILE_MAP_READ,0,0,R5_STATE);
    }
    if(r5_eventsView==0||r5_stateView==0||r5_mutex==0) { R5Disconnect(); return false; }
    return true;
}
bool R5TakeLock() {
    if(r5_lockHeld) { R5Hold("COPY_LOCK_REENTRY"); return false; }
    uint rc=WaitForSingleObject(r5_mutex,0);
    if(rc==0||rc==0x80) { r5_lockHeld=true; return true; } // abandoned도 소유권 획득
    if(rc!=0x102) { R5Hold("COPY_WAIT_FAILED"); R5Disconnect(); }
    return false;
}
bool R5Release() {
    if(ReleaseMutex(r5_mutex)==0) { R5Hold("COPY_RELEASE_FAILED"); return false; }
    r5_lockHeld=false; return true;
}
bool R5TargetSame() {
    if((ulong)AccountInfoInteger(ACCOUNT_LOGIN)==r5_targetLogin&&AccountInfoString(ACCOUNT_SERVER)==r5_targetServer) return true;
    R5Hold("TARGET_ACCOUNT_CHANGED"); return false;
}
bool R5ManagedFlat() {
    if(ArraySize(mapping)>0||ArraySize(pendingOrders)>0) return false;
    for(int i=0;i<PositionsTotal();i++) { PositionGetTicket(i); if((ulong)PositionGetInteger(POSITION_MAGIC)==MagicNumber) return false; }
    for(int i=0;i<OrdersTotal();i++) { OrderGetTicket(i); if((ulong)OrderGetInteger(ORDER_MAGIC)==MagicNumber) return false; }
    return true;
}
bool R5TransitionsDone() {
    if(r7_entry.active||ArraySize(r8_closes)>0) return false;
    if(ArraySize(pendingOrders)>0) return false;
    for(int i=0;i<OrdersTotal();i++) { OrderGetTicket(i); if((ulong)OrderGetInteger(ORDER_MAGIC)==MagicNumber) return false; }
    return true; // MT5의 정상 SL/TP는 포지션 속성이므로 진행 중 주문으로 세지 않는다.
}
bool R5ObserveHeader() {
    string server;if(!R5Decode(r5_header,88,(int)R5Get32(r5_header,84),server)) { R5Hold("SOURCE_UTF8");return false; }
    ulong login=R5Get64(r5_header,72);
    if(!R11SameSource(login,server)) { R5Hold("SOURCE_CHANGED");return false; }
    if(!R5EqualBytes(r5_header,32,r5_session,0,16)) { R11Temp("SAME_ACCOUNT_NEW_SESSION");return false; }
    r5_alive=R5Get64(r5_header,56);r5_published=R5Get64(r5_header,48);
    if(R5Get32(r5_header,64)!=1) { R11Temp("PUBLISHER_NOT_READY");return false; }
    if(!C11Fresh(GetTickCount64(),r5_alive)) { R11Temp("HEARTBEAT_EXPIRED");return false; }
    if(r5_published<r5_accepted||r5_published-r5_accepted>R5_N) { R11Temp("RING_GAP");return false; }
    if(!TerminalInfoInteger(TERMINAL_CONNECTED)) { R11Temp("BROKER_DISCONNECTED");return false; }
    return r11_state==R11_RUNNING&&!r5_hold;
}
bool R5Frame(const uchar &b[], ulong expected, R5Envelope &item) {
    uint n=R5Get32(b,4);
    if(R5Get32(b,0)!=2||n<1||n>960||R5Get64(b,8)!=expected||!R5EqualBytes(b,16,r5_session,0,16)) return false;
    if(R5Get16(b,40)!=1||R5Get16(b,42)!=1||R5Get32(b,44)!=0||!R5Zeros(b,56,8)||!R5Zeros(b,64+(int)n,960-(int)n)) return false;
    if(R5Get32(b,52)!=R5Crc32(b,4,48)||R5Get32(b,48)!=R5Crc32(b,64,(int)n)) return false;
    string payload,error;
    if(!R5Decode(b,64,(int)n,payload)||!R5ValidateCommand(payload,error)) return false;
    item.session=r5_sessionText; item.sequence=expected; item.publishedTick=R5Get64(b,32);
    item.executeTick=GetTickCount64()+(ulong)MathMax(0,Inp_SignalDelayMs); item.payload=payload;
    return true;
}
void ReadFromMMF() {
    if(!R5TargetSame()) return;
    ulong now=GetTickCount64();
    if(r11_pollTick>0&&!C11Fresh(now,r11_pollTick)) R11Temp("READER_SCHEDULING_GAP");
    r11_pollTick=now;
    if(!R5Connect()) { R11Temp("MMF_DISCONNECTED");return; }
    if(!R5TakeLock()) { if(r5_bound&&!C11Fresh(now,r5_alive)) R11Temp("HEARTBEAT_EXPIRED");return; }
    RtlMoveMemory(r5_header,r5_eventsView,(ulong)256);
    bool headerOk=R5HeaderValid(r5_header),stateOk=false;int count=0;
    if(headerOk&&R5Get32(r5_header,64)==1) {
        RtlMoveMemory(r5_stateHeader,r5_stateView,(ulong)128);stateOk=R5StateHeaderValid();
        if(stateOk) RtlMoveMemory(r5_stateBody,r5_stateView+128,(ulong)R5_STATE_BODY);
        ulong w=R5Get64(r5_header,48);
        if(r11_state==R11_RUNNING&&r5_bound&&R5EqualBytes(r5_header,32,r5_session,0,16)&&w>=r5_accepted&&w-r5_accepted<=R5_N) {
            count=(int)MathMin((ulong)MathMin(R5_BATCH,R5_N-r5_count),w-r5_accepted);
            for(int i=0;i<count;i++) {
                ulong n=r5_accepted+(ulong)i+1;long offset=256+(long)((n-1)%R5_N)*R5_SLOT;
                RtlMoveMemory(r5_raw[i].data,r5_eventsView+offset,(ulong)R5_SLOT);
            }
        }
    }
    if(!R5Release()) return;
    if(!headerOk) { R5Hold("EVENT_HEADER_CRC_OR_LAYOUT");return; }
    string server;if(!R5Decode(r5_header,88,(int)R5Get32(r5_header,84),server)) { R5Hold("SOURCE_UTF8");return; }
    ulong login=R5Get64(r5_header,72),published=R5Get64(r5_header,48);
    if(!R11SameSource(login,server)) { R5Hold("SOURCE_CHANGED");return; }
    if(R5Get32(r5_header,64)!=1) { R11Temp("PUBLISHER_NOT_READY");return; }
    // Read current bytes BEFORE the freshness verdict; cached pre-read timeout falsely latched old Slave.
    r5_alive=R5Get64(r5_header,56);
    if(!C11Fresh(GetTickCount64(),r5_alive)) { R11Temp("HEARTBEAT_EXPIRED");return; }
    if(!TerminalInfoInteger(TERMINAL_CONNECTED)) R11Temp("BROKER_DISCONNECTED");
    bool changed=r5_bound&&!R5EqualBytes(r5_header,32,r5_session,0,16);
    if(changed) R11Temp("SAME_ACCOUNT_NEW_SESSION");
    if(r5_bound&&!changed&&(published<r5_accepted||published-r5_accepted>R5_N)) R11Temp(published<r5_accepted?"SEQUENCE_REGRESSION":"RING_GAP");
    if(!stateOk) { R5Hold("STATE_HEADER_CRC_OR_LAYOUT");return; }
    int length=(int)R5Get32(r5_stateHeader,20);string sync;
    if(R5Crc32(r5_stateBody,0,length)!=R5Get32(r5_stateHeader,64)||!R5Zeros(r5_stateBody,length,R5_STATE_BODY-length)||!R5Decode(r5_stateBody,0,length,sync)) { R5Hold("STATE_PAYLOAD_INVALID");return; }
    ulong version=R5Get64(r5_stateHeader,40),baseSeq=R5Get64(r5_stateHeader,48);r11_stateTick=R5Get64(r5_stateHeader,56);
    r6_kind=R5Get16(r5_stateHeader,72);
    if(r6_kind!=2) { R5Hold("RECOVERY_REQUIRES_FULL_SNAPSHOT");return; }
    ulong previous=r6_revision;string legacy;
    if(!R6Load(sync,baseSeq,legacy)) { R5Hold("FULL_SNAPSHOT_INVALID");return; }
    r5_sync=legacy;
    if(r11_state==R11_RUNNING&&(version<r5_stateVersion||r6_revision<previous)) { R5Hold("STATE_VERSION_REGRESSION");return; }
    if(r11_state!=R11_RUNNING||r11_needRebase||!r5_bound) {
        if(r5_sourceLogin==0&&(!R5ManagedFlat()||ArraySize(r6_rows)>0)) { R5Hold("FIRST_BIND_REQUIRES_FLAT");return; }
        R11AdoptSnapshot(login,server,published,version,baseSeq);return;
    }
    r5_published=published;
    for(int i=0;i<count;i++) {
        R5Envelope item;if(!R5Frame(r5_raw[i].data,r5_accepted+1,item)) { R5Hold("COMMAND_FRAME_OR_PAYLOAD_INVALID");return; }
        r5_inbox[(r5_head+r5_count)%R5_N]=item;r5_count++;r5_accepted=item.sequence;
        Print("[MMF v2 ACCEPTED_LOCAL] Session=",item.session," | Sequence=",item.sequence," | RAW=",item.payload);
    }
    r5_stateVersion=version;r5_stateBase=baseSeq;lastSyncTick=GetTickCount();isMasterDisconnected=false;
}
bool R5ExecutionGate() {
    if(r11_state!=R11_RUNNING||r5_hold||!r5_bound||!R5TargetSame()||!R5Connect()||!R5TakeLock()) return false;
    RtlMoveMemory(r5_header,r5_eventsView,(ulong)R5_HEADER);
    bool valid=R5HeaderValid(r5_header);
    if(!R5Release()) return false;
    if(!valid) { R5Hold("EXECUTION_HEADER_INVALID"); return false; }
    return R5ObserveHeader();
}
void ProcessDelayedSignals() {
    if(r5_count==0||!R5ExecutionGate()) return;
    R5Envelope item=r5_inbox[r5_head];
    if(item.session!=r5_sessionText) { R5Hold("INBOX_SESSION_MISMATCH"); return; }
    if(GetTickCount64()<item.executeTick) return;
    if(r7_dispatchSequence!=0) {
        if(r7_dispatchSequence!=item.sequence||r7_dispatchSession!=item.session) { R5Hold("RETRY_INBOX_IDENTITY"); return; }
        R7AttemptEntry();
    } else {
        // 아직 매핑되지 않은 해당 티켓의 청산/수정은 체결 이벤트가 매핑을 만들 때까지 보유한다.
        string action=S6Field(item.payload,"ACTION"); ulong id=0; S6UInt(S6Field(item.payload,"TICKET"),id);
        if(action=="MODIFY"&&R7HasPending(id)) return;
        // CLOSE는 먼저 목표를 인계하여 미체결 진입 잔량의 취소를 진행한다.
        Print("[MMF v2 EXECUTOR_DISPATCH] Session=",item.session," | Sequence=",item.sequence," | RAW=",item.payload);
        r7_dispatchSequence=item.sequence; r7_dispatchSession=item.session;
        R6Before(item);
        if(r5_hold) return;
        ProcessMessage(item.payload);
    }
    if(r5_hold||r7_entry.active) return;
    R6After(item);
    r5_inbox[r5_head].payload=""; r5_head=(r5_head+1)%R5_N; r5_count--;
    r7_dispatchSequence=0; r7_dispatchSession="";
}
void R5ApplySync() {
    if(r5_count!=0||r5_stateVersion<=r5_appliedState||!R5TransitionsDone()||!R5ExecutionGate()) return;
    if(r5_accepted!=r5_published||r5_stateBase!=r5_published) return;
    if(r6_kind==2&&!R6Reconcile()) { if(r6_consistent) R11Temp("SNAPSHOT_RECONCILE_REQUIRED"); return; }
    ProcessMessage(r5_sync);
    r5_appliedState=r5_stateVersion;
}

bool CheckExpiry() {
    return DS5DeploymentAllowed();
}

void ParseSymbolMapping() {
    if(Inp_SymbolMapping == "") return;
    string pairs[]; ushort comma = ','; StringSplit(Inp_SymbolMapping, comma, pairs);
    
    for(int i = 0; i < ArraySize(pairs); i++) {
        string pair = pairs[i];
        StringTrimLeft(pair); StringTrimRight(pair);
        if(pair == "") continue;
        string kv[]; ushort eq = '='; StringSplit(pair, eq, kv);
        
        if(ArraySize(kv) == 2) {
            int size = ArraySize(symMapping); ArrayResize(symMapping, size + 1);
            StringTrimLeft(kv[0]); StringTrimRight(kv[0]); StringTrimLeft(kv[1]); StringTrimRight(kv[1]);
            symMapping[size].masterSym = kv[0]; symMapping[size].slaveSym = kv[1];
        }
    }
}

string GetMappedSymbol(string mSym) {
    // [TRACE ONLY] 수신 심볼과 수동 매핑 설정 개수 기록
    Print("[TRACE/SYMBOL MAP IN] input=", mSym, " | manualMapCount=", ArraySize(symMapping));

    for(int i = 0; i < ArraySize(symMapping); i++) {
        if(symMapping[i].masterSym == mSym) return symMapping[i].slaveSym;
    }

    // 수신된 정확한 심볼이 브로커에 존재하면 그대로 사용
    if((bool)SymbolInfoInteger(mSym, SYMBOL_EXIST)) return mSym;

    string baseSym = mSym;
    if(StringSubstr(baseSym, StringLen(baseSym)-1, 1) == "+") {
        baseSym = StringSubstr(baseSym, 0, StringLen(baseSym)-1);
    }
    string plusSym = baseSym + "+";

    bool hasNormal = (bool)SymbolInfoInteger(baseSym, SYMBOL_EXIST);
    bool hasPlus   = (bool)SymbolInfoInteger(plusSym, SYMBOL_EXIST);

    // [TRACE ONLY] 대체 심볼 탐색 시 실제 심볼 존재(EXIST) 값 기록
    bool selectNormal = (bool)SymbolInfoInteger(baseSym, SYMBOL_SELECT);
    bool selectPlus   = (bool)SymbolInfoInteger(plusSym, SYMBOL_SELECT);
    Print("[TRACE/SYMBOL PROBE] input=", mSym,
          " | base=", baseSym, " exist=", (hasNormal ? "true" : "false"), " select=", (selectNormal ? "true" : "false"),
          " | plus=", plusSym, " exist=", (hasPlus ? "true" : "false"), " select=", (selectPlus ? "true" : "false"));

    if(hasNormal && hasPlus) return "ERROR_DUPLICATE_SYMBOL";
    if(hasNormal) return baseSym;
    if(hasPlus)   return plusSym;

    return mSym; 
}





// [7단계 알림 전용] 주문 Events/State와 별개의 16 KiB 메모리 우편함.
// 소비자가 가져가기 전에는 덮어쓰지 않는다. HTTP 요청 중에는 이 잠금을 보유하지 않는다.
#define N7_SIZE 16384
#define N7_HEADER 96
#define N7_BODY 16288
#define N7_TAG 0x374E5A4F
long n7_map=0,n7_view=0,n7_mutex=0,n7_owner=0;
bool n7_owned=false,n7_fault=false;
string n7_name="",n7_server="",n7_terminal="";
ulong n7_login=0;
uint n7_serverHash=0,n7_terminalHash=0;
uchar n7_header[N7_HEADER],n7_body[N7_BODY],n7_verify[N7_HEADER],n7_epoch[16];

void N7Put32(uchar &a[],int p,uint value) { for(int i=0;i<4;i++) a[p+i]=(uchar)(value>>(8*i)); }
void N7Put64(uchar &a[],int p,ulong value) { for(int i=0;i<8;i++) a[p+i]=(uchar)(value>>(8*i)); }
uint N7Get32(const uchar &a[],int p) { uint v=0; for(int i=0;i<4;i++) v|=((uint)a[p+i])<<(8*i); return v; }
ulong N7Get64(const uchar &a[],int p) { ulong v=0; for(int i=0;i<8;i++) v|=((ulong)a[p+i])<<(8*i); return v; }
uint N7CRC(const uchar &a[],int p,int count,int zero=-1) {
    uint crc=0xFFFFFFFF;
    for(int i=p;i<p+count;i++) {
        uchar b=a[i]; if(zero>=0&&i>=zero&&i<zero+4) b=0;
        crc^=(uint)b;
        for(int j=0;j<8;j++) crc=(crc&1)!=0?(crc>>1)^(uint)0xEDB88320:crc>>1;
    }
    return crc^(uint)0xFFFFFFFF;
}
bool N7Equal(const uchar &a[],int ap,const uchar &b[],int bp,int n) {
    for(int i=0;i<n;i++) if(a[ap+i]!=b[bp+i]) return false;
    return true;
}
bool N7Encode(string text,int limit,uchar &bytes[]) {
    if(StringLen(text)==0) return false;
    for(int i=0;i<StringLen(text);i++) if(StringGetCharacter(text,i)==0||StringGetCharacter(text,i)==0xFEFF) return false;
    int n=StringToCharArray(text,bytes,0,WHOLE_ARRAY,CP_UTF8)-1;
    if(n<1||n>limit) return false;
    ArrayResize(bytes,n); return true;
}
bool N7Decode(const uchar &bytes[],int p,int n,string &text) {
    if(n<1||p<0||p+n>ArraySize(bytes)) return false;
    text=CharArrayToString(bytes,p,n,CP_UTF8);
    uchar encoded[];
    return N7Encode(text,n,encoded)&&ArraySize(encoded)==n&&N7Equal(bytes,p,encoded,0,n);
}
bool N7TokenValid(string token) {
    bool colon=false;
    for(int i=0;i<StringLen(token);i++) {
        ushort c=StringGetCharacter(token,i);
        if(c==':') { if(colon||i==0||i==StringLen(token)-1) return false; colon=true; continue; }
        if(!((c>='0'&&c<='9')||(colon&&((c>='A'&&c<='Z')||(c>='a'&&c<='z')||c=='_'||c=='-')))) return false;
    }
    return colon;
}
bool N7SetIdentity() {
    n7_login=(ulong)AccountInfoInteger(ACCOUNT_LOGIN); n7_server=AccountInfoString(ACCOUNT_SERVER);
    n7_terminal=TerminalInfoString(TERMINAL_DATA_PATH);
    if(n7_login==0||StringLen(Inp_StreamId)<1||StringLen(Inp_StreamId)>32) return false;
    for(int i=0;i<StringLen(Inp_StreamId);i++) {
        ushort c=StringGetCharacter(Inp_StreamId,i);
        if(!((c>='A'&&c<='Z')||(c>='0'&&c<='9')||c=='_')) return false;
    }
    uchar server[],terminal[];
    if(!N7Encode(n7_server,192,server)||!N7Encode(n7_terminal,2048,terminal)) return false;
    n7_serverHash=N7CRC(server,0,ArraySize(server)); n7_terminalHash=N7CRC(terminal,0,ArraySize(terminal));
    n7_name="Local\\OZCopy.Notify7."+StringFormat("%08X",n7_terminalHash)+"."+StringFormat("%08X",n7_serverHash)+
            "."+StringFormat("%I64u",n7_login)+"."+StringFormat("%I64u",MagicNumber)+"."+Inp_StreamId;
    return true;
}
bool N7SameAccount() {
    return n7_login==(ulong)AccountInfoInteger(ACCOUNT_LOGIN)&&n7_server==AccountInfoString(ACCOUNT_SERVER)&&
           n7_terminal==TerminalInfoString(TERMINAL_DATA_PATH);
}
void N7Close() {
    if(n7_view!=0) { UnmapViewOfFile(n7_view); n7_view=0; }
    if(n7_map!=0) { CloseHandle(n7_map); n7_map=0; }
    if(n7_mutex!=0) { CloseHandle(n7_mutex); n7_mutex=0; }
    if(n7_owned) { ReleaseSemaphore(n7_owner,1,0); n7_owned=false; }
    if(n7_owner!=0) { CloseHandle(n7_owner); n7_owner=0; }
    ArrayInitialize(n7_body,0);
}
void N7Fault(string reason) {
    n7_fault=true;
    Print("[NOTIFY_CHANNEL_PAUSED] ",reason," | 매매 신호 처리는 별도로 유지됩니다.");
}
bool N7Lock() {
    uint result=WaitForSingleObject(n7_mutex,0);
    if(result==0||result==0x80) return true;
    if(result!=0x102) N7Fault("LOCK_FAILED");
    return false;
}
bool N7Unlock() {
    if(ReleaseMutex(n7_mutex)!=0) return true;
    N7Fault("RELEASE_FAILED"); return false;
}
bool N7HeaderValid(const uchar &h[]) {
    if(N7Get32(h,0)!=N7_TAG||N7Get32(h,4)!=1||N7Get32(h,48)!=N7CRC(h,0,N7_HEADER,48)) return false;
    if(N7Get64(h,64)!=n7_login||N7Get64(h,72)!=MagicNumber||N7Get32(h,80)!=n7_serverHash||N7Get32(h,84)!=n7_terminalHash) return false;
    if(N7Get32(h,52)!=0||N7Get64(h,88)!=0) return false;
    ulong seq=N7Get64(h,24),claimed=N7Get64(h,32);
    if(seq<claimed||seq-claimed>1||seq>9223372036854775807) return false;
    uint length=N7Get32(h,40);
    if(seq==0) { if(length!=0||N7Get32(h,44)!=0) return false; }
    else if(length<25||length>N7_BODY) return false;
    bool nonzero=false; for(int i=8;i<24;i++) if(h[i]!=0) nonzero=true;
    return nonzero;
}
void N7HeaderCRC(uchar &h[]) { N7Put32(h,48,N7CRC(h,0,N7_HEADER,48)); }
bool N7Owner(string role) {
    n7_owner=CreateSemaphoreW(0,1,1,n7_name+role);
    if(n7_owner==0) return false;
    if(WaitForSingleObject(n7_owner,0)!=0) { CloseHandle(n7_owner); n7_owner=0; return false; }
    n7_owned=true; return true;
}
bool N7Pack(string token,string chat,string message) {
    if(!N7TokenValid(token)) return false;
    string values[5]; values[0]=token; values[1]=chat; values[2]=n7_server; values[3]=n7_terminal; values[4]=message;
    int limits[5]={128,128,192,2048,8192}; int p=20;
    ArrayInitialize(n7_body,0);
    for(int i=0;i<5;i++) {
        uchar b[]; if(!N7Encode(values[i],limits[i],b)) return false;
        int length=ArraySize(b); if(p+length>N7_BODY) return false;
        N7Put32(n7_body,i*4,(uint)length); ArrayCopy(n7_body,b,p,0,length); p+=length;
    }
    return true;
}
int N7PackedLength() {
    int length=20;
    for(int i=0;i<5;i++) { uint n=N7Get32(n7_body,i*4); if(n<1||n>N7_BODY) return 0; length+=(int)n; }
    return length<=N7_BODY?length:0;
}
bool N7Unpack(int length,string &token,string &chat,string &message) {
    if(length<25||length>N7_BODY||N7PackedLength()!=length) return false;
    int limits[5]={128,128,192,2048,8192}; string values[5]; int p=20;
    for(int i=0;i<5;i++) {
        uint n=N7Get32(n7_body,i*4);
        if(n>(uint)limits[i]||!N7Decode(n7_body,p,(int)n,values[i])) return false;
        p+=(int)n;
    }
    if(values[2]!=n7_server||values[3]!=n7_terminal||!N7TokenValid(values[0])) return false;
    token=values[0]; chat=values[1]; message=values[4]; return true;
}

int n7_queueHead=0,n7_queueCount=0;
ulong n7_lastOpen=0,n7_dropped=0,n7_lastWarning=0;
bool n7_waitWarning=false;
#define N7_QUEUE 1024

bool N7StartProducer() {
    if(n7_fault) return false;
    if(!N7SetIdentity()||!N7Owner(".Producer")) { N7Close(); return false; }
    n7_mutex=CreateMutexW(0,0,n7_name+".CopyLock");
    n7_map=CreateFileMappingW(-1,0,4,0,N7_SIZE,n7_name+".Mailbox");
    if(n7_mutex==0||n7_map==0) { N7Close(); return false; }
    n7_view=MapViewOfFile(n7_map,0x000F001F,0,0,N7_SIZE);
    if(n7_view==0||CoCreateGuid(n7_epoch)!=0) { N7Close(); return false; }
    ArrayInitialize(n7_header,0); ArrayInitialize(n7_body,0);
    N7Put32(n7_header,0,N7_TAG); N7Put32(n7_header,4,1); ArrayCopy(n7_header,n7_epoch,8,0,16);
    N7Put64(n7_header,64,n7_login); N7Put64(n7_header,72,MagicNumber);
    N7Put32(n7_header,80,n7_serverHash); N7Put32(n7_header,84,n7_terminalHash); N7HeaderCRC(n7_header);
    if(!N7Lock()) { N7Close(); return false; }
    RtlMoveMemory(n7_view+N7_HEADER,n7_body,(ulong)N7_BODY);
    RtlMoveMemory(n7_view,n7_header,(ulong)N7_HEADER);
    if(!N7Unlock()) { N7Close(); return false; }
    return true;
}
void AddToTelegramQueue(string msg) {
    if(!Inp_EnableTelegram||Inp_BotToken==""||Inp_ChatID=="") return;
    if(n7_queueCount>=N7_QUEUE) {
        n7_dropped++;
        if(n7_lastWarning==0||GetTickCount64()-n7_lastWarning>=5000) {
            Print("[NOTIFY_QUEUE_FULL] Dropped=",n7_dropped," | 알림 전용 EA 상태를 확인해 주세요.");
            n7_lastWarning=GetTickCount64();
        }
        return;
    }
    string prefix=Inp_AccountAlias!=""?"["+Inp_AccountAlias+"] ":"";
    telegramQueue[(n7_queueHead+n7_queueCount)%N7_QUEUE]=prefix+msg; n7_queueCount++;
}
void N7Pop() {
    telegramQueue[n7_queueHead]=""; n7_queueHead=(n7_queueHead+1)%N7_QUEUE; n7_queueCount--;
}
void ProcessTelegramQueue() {
    if(!Inp_EnableTelegram||n7_queueCount==0||n7_fault) return;
    if((ulong)AccountInfoInteger(ACCOUNT_LOGIN)!=r5_targetLogin||AccountInfoString(ACCOUNT_SERVER)!=r5_targetServer) {
        N7Fault("TARGET_ACCOUNT_CHANGED"); return;
    }
    ulong now=GetTickCount64();
    if(n7_view==0) {
        if(n7_lastOpen!=0&&now-n7_lastOpen<1000) return;
        n7_lastOpen=now;
        if(!N7StartProducer()) {
            if(n7_lastWarning==0||now-n7_lastWarning>=5000) { Print("[NOTIFY_NOT_READY] 알림 채널을 열지 못했습니다."); n7_lastWarning=now; }
            return;
        }
    }
    if(!N7SameAccount()) { N7Fault("ACCOUNT_CHANGED"); return; }
    if(!N7Pack(Inp_BotToken,Inp_ChatID,telegramQueue[n7_queueHead])) {
        Print("[NOTIFY_INVALID_MESSAGE] 토큰 형식 또는 알림 크기를 확인해 주세요. 내용은 기록하지 않습니다.");
        N7Pop(); return;
    }
    int length=N7PackedLength(); uint crc=N7CRC(n7_body,0,length);
    if(!N7Lock()) return;
    RtlMoveMemory(n7_header,n7_view,(ulong)N7_HEADER);
    bool valid=N7HeaderValid(n7_header)&&N7Equal(n7_header,8,n7_epoch,0,16);
    bool free=valid&&N7Get64(n7_header,24)==N7Get64(n7_header,32);
    bool limit=valid&&N7Get64(n7_header,24)>=9223372036854775807;
    if(free&&!limit) {
        N7Put64(n7_header,24,N7Get64(n7_header,24)+1);
        N7Put32(n7_header,40,(uint)length); N7Put32(n7_header,44,crc); N7Put64(n7_header,56,now); N7HeaderCRC(n7_header);
        RtlMoveMemory(n7_view+N7_HEADER,n7_body,(ulong)N7_BODY);
        RtlMoveMemory(n7_view,n7_header,(ulong)N7_HEADER);
    }
    bool released=N7Unlock();
    if(!valid||limit) { N7Fault(!valid?"HEADER_OR_SESSION_INVALID":"SEQUENCE_LIMIT"); return; }
    if(!released) return;
    if(free) { N7Pop(); n7_waitWarning=false; }
    else if(!n7_waitWarning&&now-N7Get64(n7_header,56)>5000) {
        Print("[NOTIFY_WORKER_WAIT] 알림 EA가 메시지를 가져오기를 기다리고 있습니다."); n7_waitWarning=true;
    }
    ArrayInitialize(n7_body,0);
}

int OnInit() {
   if(!DS5DeploymentAllowed(true)) return INIT_FAILED;
    if(!CheckExpiry()) return(INIT_FAILED);
    if(!_IsX64 || !MQLInfoInteger(MQL_DLLS_ALLOWED) || !R5ValidStream(Inp_StreamId) || Inp_SignalDelayMs < 0) {
        Print("[MMF v2 INIT] x64/DLL/StreamId/SignalDelay 설정을 확인해 주세요."); return INIT_FAILED;
    }
    if(AccountInfoInteger(ACCOUNT_MARGIN_MODE)!=ACCOUNT_MARGIN_MODE_RETAIL_HEDGING) {
        Print("[INIT_UNSUPPORTED_ACCOUNT_MODE] 이 MT5 Slave는 티켓별 hedging 계좌만 지원합니다."); return INIT_FAILED;
    }
    if(!MathIsValidNumber(RiskPercent)||RiskPercent<=0||!MathIsValidNumber(MaxDailyLossPercent)||MaxDailyLossPercent<=0||MagicNumber==0) {
        Print("[INIT_INVALID_RISK] 리스크 값과 자기 주문 식별용 MagicNumber를 확인해 주세요."); return INIT_FAILED;
    }
    r5_targetLogin=(ulong)AccountInfoInteger(ACCOUNT_LOGIN); r5_targetServer=AccountInfoString(ACCOUNT_SERVER);
    ParseSymbolMapping();
    if(!R8LoadDaily()) { Print("[INIT_DAILY_STATE_FAILED]"); return INIT_FAILED; }
    
    trade.SetExpertMagicNumber(MagicNumber);
    
    R8RestoreMappings();
    if(CoCreateGuid(c11_guid)!=0||!R11Pin(false)) { Print("[INIT_RECOVERY_IDENTITY_FAILED]");return INIT_FAILED; }
    if(!EventSetMillisecondTimer(50)) return INIT_FAILED; 
    Print("System Init OK (SL/TP Sync Enabled)."); 
    AddToTelegramQueue("?? 슬레이브 EA가 정상적으로 구동을 시작했습니다.");
    return(INIT_SUCCEEDED);
}

void OnDeinit(const int reason) {
    EventKillTimer();
    c11_intentionalStop=true;c11_next=0;C11Service();
    R5Disconnect(); N7Close(); C11Close();
}

void OnTimer() {
    if(!R5TargetSame()) { C11Service(); return; }
    ReadFromMMF();             // 먼저 최대 16건을 Inbox에 확보
    CheckDrawdown();           // 기존 리스크 관리
    R7SettlePending();         // 무체결 취소·거절된 진입의 대기 상태 정리
    ProcessDelayedSignals();   // 잠금 밖에서 실행 한 건
    R8ProcessCloses();          // 수신 HOLD와 무관하게 이미 승인된 청산 목표를 관리
    CleanupMappings();
    CheckSLWatchdog();         // 통신 보류 중에도 보호 관리 유지
    R5ApplySync();
    C11Service();             // diagnostics follow copying, close intents and protection
    ProcessTelegramQueue();    // 로컬 알림 우편함에만 전달. HTTP는 알림 EA에서 실행
}

void CheckSLWatchdog() {
    for(int i = PositionsTotal() - 1; i >= 0; i--) {
        ulong sTicket = PositionGetTicket(i);
        if(PositionGetInteger(POSITION_MAGIC) != MagicNumber) continue;
        
        double currentSL = PositionGetDouble(POSITION_SL);
        if(currentSL <= 0.0) { 
            // [TRACE ONLY] 실제 슬레이브 포지션에서 SL 누락 감지 시 현재 상태 기록
            Print("[TRACE/MT5 SLAVE SL WATCHDOG] slaveTicket=", sTicket,
                  " | currentSL=", DoubleToString(currentSL, 8),
                  " | currentTP=", DoubleToString(PositionGetDouble(POSITION_TP), 8),
                  " | symbol=", PositionGetString(POSITION_SYMBOL));
            double initSL = 0.0;
            double initTP = PositionGetDouble(POSITION_TP);
            string sym = PositionGetString(POSITION_SYMBOL);
            long type = PositionGetInteger(POSITION_TYPE);
            
            for(int j = 0; j < ArraySize(mapping); j++) {
                if(mapping[j].slaveTicket == sTicket) {
                    initSL = mapping[j].initialSL;
                    break;
                }
            }
            
            if(initSL > 0.0) {
                double currentPrice = (type == POSITION_TYPE_BUY) ? SymbolInfoDouble(sym, SYMBOL_BID) : SymbolInfoDouble(sym, SYMBOL_ASK);
                // [TRACE ONLY] SL 복구/강제청산 판단값 기록
                Print("[TRACE/MT5 SLAVE SL WATCHDOG DECISION] slaveTicket=", sTicket,
                      " | type=", type, " | currentPrice=", DoubleToString(currentPrice, 8),
                      " | initSL=", DoubleToString(initSL, 8), " | initTP=", DoubleToString(initTP, 8));
                bool isLossExceeded = false;
                
                if(type == POSITION_TYPE_BUY && currentPrice <= initSL) isLossExceeded = true;
                if(type == POSITION_TYPE_SELL && currentPrice >= initSL) isLossExceeded = true;
                
                if(isLossExceeded) {
                    R8QueueFlat((ulong)PositionGetInteger(POSITION_IDENTIFIER),"SL watchdog");
                } else {
                    if(trade.PositionModify(sTicket, initSL, initTP)) AddToTelegramQueue("?? [비상 프로토콜] SL 누락 감지. 최초 SL로 안전하게 복구 완료: " + sym);
                }
            }
        }
    }
}






// R8: 안정 식별자와 현재 거래용 티켓을 분리한다. 아래 함수는 선택된 포지션을 유지한다.
bool R8SelectIdentifier(ulong id,ulong &ticket) {
    ticket=0;
    for(int i=0;i<PositionsTotal();i++) {
        ulong t=PositionGetTicket(i);
        if(t>0&&(ulong)PositionGetInteger(POSITION_IDENTIFIER)==id&&
           (ulong)PositionGetInteger(POSITION_MAGIC)==MagicNumber) { ticket=t; return true; }
    }
    return false;
}
bool R8SelectMapping(int n) {
    if(n<0||n>=ArraySize(mapping)) return false;
    ulong ticket=0;
    if(!R8SelectIdentifier(mapping[n].slaveIdentifier,ticket)) return false;
    mapping[n].slaveTicket=ticket; return true;
}
bool R8HistoryVolume(ulong id,double &entered,double &exited) {
    entered=0; exited=0;
    if(id==0||!HistorySelectByPosition(id)) return false;
    for(int i=0;i<HistoryDealsTotal();i++) {
        ulong deal=HistoryDealGetTicket(i);
        long type=HistoryDealGetInteger(deal,DEAL_TYPE);
        if(type!=DEAL_TYPE_BUY&&type!=DEAL_TYPE_SELL) continue;
        long entry=HistoryDealGetInteger(deal,DEAL_ENTRY);
        double v=HistoryDealGetDouble(deal,DEAL_VOLUME);
        if(entry==DEAL_ENTRY_IN) entered+=v;
        else if(entry==DEAL_ENTRY_OUT||entry==DEAL_ENTRY_OUT_BY) exited+=v;
        else return false;
    }
    return entered>0;
}
bool R8ConfirmedClosed(ulong id) {
    double entered=0,exited=0;
    return R8HistoryVolume(id,entered,exited)&&MathAbs(entered-exited)<0.00000001;
}
string R8LocalPrefix() {
    uchar bytes[];
    string identity=AccountInfoString(ACCOUNT_SERVER)+"|"+Inp_StreamId+"|"+StringFormat("%I64u",MagicNumber);
    int n=StringToCharArray(identity,bytes,0,WHOLE_ARRAY,CP_UTF8)-1;
    uint hash=R5Crc32(bytes,0,n);
    return "R8."+StringFormat("%08X",hash)+"."+StringFormat("%I64u",(ulong)AccountInfoInteger(ACCOUNT_LOGIN))+".";
}
void R8SaveMapping(ulong masterId,ulong positionId) {
    string key=R8LocalPrefix()+"M."+StringFormat("%I64u",masterId);
    // 64비트 식별자를 double 한 개에 넣지 않는다. 32비트 두 조각은 정확하게 저장된다.
    double high=(double)(uint)(positionId>>32),low=(double)(uint)positionId;
    if(GlobalVariableCheck(key+"H")&&GlobalVariableCheck(key+"L")&&
       GlobalVariableGet(key+"H")==high&&GlobalVariableGet(key+"L")==low) return;
    bool ok=GlobalVariableSet(key+"H",high)>0;
    ok=(GlobalVariableSet(key+"L",low)>0)&&ok;
    GlobalVariablesFlush();
    if(!ok) R5Hold("LOCAL_MAPPING_SAVE_FAILED");
}
void R8DeleteMapping(ulong masterId) {
    string key=R8LocalPrefix()+"M."+StringFormat("%I64u",masterId);
    GlobalVariableDel(key+"H"); GlobalVariableDel(key+"L");
}
void R8RestoreMappings() {
    string prefix=R8LocalPrefix()+"M.";
    for(int g=0;g<GlobalVariablesTotal();g++) {
        string key=GlobalVariableName(g);
        if(StringFind(key,prefix)!=0||StringSubstr(key,StringLen(key)-1)!="H") continue;
        string idText=StringSubstr(key,StringLen(prefix),StringLen(key)-StringLen(prefix)-1);
        ulong master=0; if(!R5UInt64(idText,master)||master==0) continue;
        string lowKey=StringSubstr(key,0,StringLen(key)-1)+"L";
        if(!GlobalVariableCheck(lowKey)) continue;
        double h=GlobalVariableGet(key),l=GlobalVariableGet(lowKey);
        if(h<0||l<0||h>4294967295.0||l>4294967295.0||MathFloor(h)!=h||MathFloor(l)!=l) continue;
        ulong id=((ulong)(uint)h<<32)|(ulong)(uint)l,ticket=0;
        if(!R8SelectIdentifier(id,ticket)) continue;
        double sl=PositionGetDouble(POSITION_SL),tp=PositionGetDouble(POSITION_TP);
        AddMapping(master,ticket,sl,tp);
    }
    // SL 관리용 로컬 식별자만 복원한다. 세션/순번의 자동 승계는 허용하지 않는다.
}

void CleanupMappings() {
    int activeCount = 0;
    for(int i = 0; i < ArraySize(mapping); i++) {
        ulong sTicket = mapping[i].slaveTicket;
        ulong mTicket = mapping[i].masterTicket;
        if(R8SelectMapping(i)||R7HasPending(mTicket)||!R8ConfirmedClosed(mapping[i].slaveIdentifier))
            mapping[activeCount++]=mapping[i];
        else R8DeleteMapping(mTicket);
    }
    ArrayResize(mapping, activeCount);
}

void AddPendingOrder(ulong orderTicket, ulong masterTicket, double initSL, double initTP) {
    r11_riskDirty=true;
    for(int i=0;i<ArraySize(pendingOrders);i++) if(pendingOrders[i].orderTicket==orderTicket) return;
    int size = ArraySize(pendingOrders);
    if(ArrayResize(pendingOrders,size+1)!=size+1) { R5Hold("PENDING_ENTRY_CAPACITY"); return; }
    pendingOrders[size].orderTicket = orderTicket;
    pendingOrders[size].masterTicket = masterTicket;
    pendingOrders[size].positionId=0; pendingOrders[size].nextCancelTick=0;
    pendingOrders[size].initialSL = initSL;
    pendingOrders[size].initialTP = initTP;

    // [TRACE ONLY] 브로커 체결 대기 주문과 마스터 티켓 연결 상태 기록
    Print("[TRACE/MT5 SLAVE PENDING ADD] masterTicket=", masterTicket,
          " | orderTicket=", orderTicket, " | initSL=", DoubleToString(initSL, 8),
          " | initTP=", DoubleToString(initTP, 8), " | pendingCount=", ArraySize(pendingOrders));
}

void AddMapping(ulong mTicket, ulong sTicket, double initSL = 0.0, double initTP = 0.0) {
    if(!PositionSelectByTicket(sTicket)||(ulong)PositionGetInteger(POSITION_MAGIC)!=MagicNumber) return;
    ulong stableId=(ulong)PositionGetInteger(POSITION_IDENTIFIER);
    for(int i = 0; i < ArraySize(mapping); i++) {
        if(mapping[i].masterTicket == mTicket) { 
            // 덮어쓰기 금지 및 매핑 충돌 방지 방어 코드 (버그 수정)
            if(mapping[i].slaveIdentifier != stableId && mapping[i].slaveIdentifier != 0) {
                Print("?? [매핑 충돌 방지] 마스터 티켓 ", mTicket, "은(는) 이미 슬레이브 티켓 ", mapping[i].slaveTicket, "과 매핑되어 있습니다. 새 티켓 ", sTicket, " 등록을 차단합니다.");
                return;
            }
            R6Bind(mTicket,sTicket);
            mapping[i].slaveTicket = sTicket;
            mapping[i].slaveIdentifier=stableId;
            R8SaveMapping(mTicket,stableId); 
            if(initSL > 0) mapping[i].initialSL = initSL;
            if(initTP > 0) mapping[i].initialTP = initTP;
            // [TRACE ONLY] 기존 매핑 갱신 결과 기록
            Print("[TRACE/MT5 SLAVE MAP UPDATE] masterTicket=", mTicket, " | slaveTicket=", sTicket,
                  " | initialSL=", DoubleToString(mapping[i].initialSL, 8),
                  " | initialTP=", DoubleToString(mapping[i].initialTP, 8));
            return; 
        }
    }
    int size = ArraySize(mapping);
    if(size>=65536||ArrayResize(mapping,size+1)!=size+1) { R5Hold("MAPPING_CAPACITY"); return; }
    mapping[size].masterTicket = mTicket; 
    R6Bind(mTicket,sTicket);
    mapping[size].slaveTicket = sTicket;
    mapping[size].slaveIdentifier=stableId;
    R8SaveMapping(mTicket,stableId);
    mapping[size].initialSL = initSL;
    mapping[size].initialTP = initTP;

    // [TRACE ONLY] 신규 마스터↔슬레이브 포지션 매핑 기록
    Print("[TRACE/MT5 SLAVE MAP ADD] masterTicket=", mTicket, " | slaveTicket=", sTicket,
          " | initialSL=", DoubleToString(initSL, 8), " | initialTP=", DoubleToString(initTP, 8),
          " | mappingCount=", ArraySize(mapping));
}

ulong GetSlaveTicket(string masterTicketStr) {
    ulong mTicket = StringToInteger(masterTicketStr);
    for(int i = 0; i < ArraySize(mapping); i++) {
        if(mapping[i].masterTicket == mTicket) {
            if(R8SelectMapping(i)) return mapping[i].slaveTicket;
        }
    }
    return 0;
}

bool IsEntryAllowed(bool report=true) {
    if(isDailyLocked) {
        r11_riskReason="DAILY_LOCK";
        string msg = "?? [진입 차단] 일일 최대 잔고 손실 한도 도달로 금일 신규 매매가 완전히 차단되었습니다.";
        if(report) { Print(msg); AddToTelegramQueue(msg); }
        return false;
    }

    if(Inp_MaxPositions > 0) {
        int currentPosCount = 0;
        for(int i = 0; i < PositionsTotal(); i++) {
            ulong t = PositionGetTicket(i);
            if(PositionSelectByTicket(t) && PositionGetInteger(POSITION_MAGIC) == MagicNumber) currentPosCount++;
        }
        // 아직 포지션으로 나타나지 않은 서로 다른 진입 주문도 한 슬롯을 예약한다.
        for(int i=0;i<ArraySize(pendingOrders);i++) {
            bool counted=false;
            for(int j=0;j<i;j++) if(pendingOrders[j].masterTicket==pendingOrders[i].masterTicket) counted=true;
            if(!counted&&GetSlaveTicket(StringFormat("%I64u",pendingOrders[i].masterTicket))==0) currentPosCount++;
        }
        if(currentPosCount >= Inp_MaxPositions) {
            r11_riskReason="MAX_POSITIONS";
            string msg = StringFormat("?? [진입 차단] 최대 포지션 수 초과 (설정: %d개)", Inp_MaxPositions);
            if(report) { Print(msg); AddToTelegramQueue(msg); }
            return false;
        }
    }

    datetime now = TimeCurrent();

    if(Inp_MaxDailyLossTrades > 0) {
        MqlDateTime dt; TimeToStruct(now, dt);
        datetime startOfDay = now - (dt.hour * 3600 + dt.min * 60 + dt.sec);
        HistorySelect(startOfDay, now);
        int dailyLossCount = 0;

        for(int i = 0; i < HistoryDealsTotal(); i++) {
            ulong deal = HistoryDealGetTicket(i);
            if(HistoryDealGetInteger(deal, DEAL_MAGIC) == MagicNumber) {
                long entry = HistoryDealGetInteger(deal, DEAL_ENTRY);
                if(entry == DEAL_ENTRY_OUT || entry == DEAL_ENTRY_INOUT) {
                    double netProfit = HistoryDealGetDouble(deal, DEAL_PROFIT) + HistoryDealGetDouble(deal, DEAL_SWAP) + HistoryDealGetDouble(deal, DEAL_COMMISSION);
                    if(netProfit < 0) dailyLossCount++;
                }
            }
        }
        
        if(dailyLossCount >= Inp_MaxDailyLossTrades) {
            r11_riskReason="DAILY_LOSS_COUNT";
            string msg = StringFormat("?? [진입 차단] 일일 총 누적 손실 횟수 초과 (%d회 도달)", Inp_MaxDailyLossTrades);
            if(report) { Print(msg); AddToTelegramQueue(msg); }
            return false;
        }
    }

    if(Inp_ConsecutiveLossTrades > 0 && Inp_CooldownMinutes > 0) {
        HistorySelect(0, now);
        int consecLossCount = 0;
        datetime lastLossTime = 0;

        for(int i = HistoryDealsTotal() - 1; i >= 0; i--) {
            ulong deal = HistoryDealGetTicket(i);
            if(HistoryDealGetInteger(deal, DEAL_MAGIC) == MagicNumber) {
                long entry = HistoryDealGetInteger(deal, DEAL_ENTRY);
                if(entry == DEAL_ENTRY_OUT || entry == DEAL_ENTRY_INOUT) {
                    double netProfit = HistoryDealGetDouble(deal, DEAL_PROFIT) + HistoryDealGetDouble(deal, DEAL_SWAP) + HistoryDealGetDouble(deal, DEAL_COMMISSION);
                    if(netProfit < 0) {
                        if(consecLossCount == 0) lastLossTime = (datetime)HistoryDealGetInteger(deal, DEAL_TIME);
                        consecLossCount++;
                    } else break; 
                }
            }
        }

        if(consecLossCount >= Inp_ConsecutiveLossTrades && lastLossTime > 0) {
            datetime coolDownEnd = lastLossTime + (Inp_CooldownMinutes * 60);
            if(now < coolDownEnd) {
                r11_riskReason="COOLDOWN";
                long remainSec = coolDownEnd - now;
                string msg = StringFormat("? [진입 차단] %d연속 손실로 인한 쿨다운 대기 중 (남은 시간: %02d분 %02d초)", consecLossCount, remainSec / 60, remainSec % 60);
                if(report) { Print(msg); AddToTelegramQueue(msg); }
                return false;
            }
        }
    }
    return true; 
}

double NormalizeLot(string sym, double vol) {
    double minLot = SymbolInfoDouble(sym, SYMBOL_VOLUME_MIN);
    double maxLot = SymbolInfoDouble(sym, SYMBOL_VOLUME_MAX);
    double step = SymbolInfoDouble(sym, SYMBOL_VOLUME_STEP);
    double normalized = MathFloor((vol + step * 1e-7) / step) * step;
    
    if (normalized < minLot) {
        string msg = StringFormat("?? [진입 차단] 랏수 계산 오류 (계산된 %.2f가 최소 %.2f 미달)", normalized, minLot);
        Print(msg); AddToTelegramQueue(msg);
        return 0.0;
    }
    if (normalized > maxLot) normalized = maxLot;
    return normalized;
}

double CalculateLotByRisk(string sym, double entryPrice, double slPrice) {
    // [TRACE ONLY] 랏 계산에 실제 사용되는 입력값 기록
    Print("[TRACE/MT5 SLAVE RISK INPUT] symbol=", sym,
          " | entry=", DoubleToString(entryPrice, 8), " | SL=", DoubleToString(slPrice, 8),
          " | distance=", DoubleToString(MathAbs(entryPrice - slPrice), 8),
          " | RiskPercent=", DoubleToString(RiskPercent, 4));
    if (slPrice == 0.0 || entryPrice == slPrice) return 0.0;
    
    double balance = AccountInfoDouble(ACCOUNT_BALANCE);
    double riskAmount = balance * (RiskPercent / 100.0);
    ENUM_ORDER_TYPE orderType = (entryPrice > slPrice) ? ORDER_TYPE_BUY : ORDER_TYPE_SELL;
    double profitForOneLot = 0.0;
    
    ResetLastError();
    if(!OrderCalcProfit(orderType, sym, 1.0, entryPrice, slPrice, profitForOneLot)) {
        double tickSize = SymbolInfoDouble(sym, SYMBOL_TRADE_TICK_SIZE);
        double tickValue = SymbolInfoDouble(sym, SYMBOL_TRADE_TICK_VALUE);
        if (tickSize == 0 || tickValue == 0) return 0.0;
        double distanceInTicks = MathAbs(entryPrice - slPrice) / tickSize;
        profitForOneLot = -(distanceInTicks * tickValue);
    }
    
    double lossPerOneLot = MathAbs(profitForOneLot);
    if (lossPerOneLot == 0.0) return 0.0;
    
    double rawLot = riskAmount / lossPerOneLot;
    // [TRACE ONLY] 정규화 직전 리스크 계산 중간값 기록
    Print("[TRACE/MT5 SLAVE RISK CALC] symbol=", sym,
          " | balance=", DoubleToString(balance, 2), " | riskAmount=", DoubleToString(riskAmount, 2),
          " | profitFor1Lot=", DoubleToString(profitForOneLot, 8),
          " | lossPer1Lot=", DoubleToString(lossPerOneLot, 8), " | rawLot=", DoubleToString(rawLot, 8));
    return NormalizeLot(sym, rawLot);
}

void ExecuteTradeSafe(string action, string sym, double vol, double sl, double tp, string masterTicketStr) {
    ulong id=(ulong)StringToInteger(masterTicketStr);
    if(GetSlaveTicket(masterTicketStr)>0||R7HasPending(id)) return;
    if(r7_entry.active) { R5Hold("RETRY_CONTEXT_COLLISION"); return; }
    r7_entry.masterId=id; r7_entry.action=action; r7_entry.symbol=sym; r7_entry.ticket=masterTicketStr;
    r7_entry.volume=vol; r7_entry.sl=sl; r7_entry.tp=tp;
    r7_entry.limit=MaxRetries>0?MaxRetries:1; // 기존 MaxRetries의 전체 시도 횟수 의미 유지
    r7_entry.attempted=0; r7_entry.nextTick=GetTickCount64(); r7_entry.active=true;
    R7AttemptEntry();
}


// R8: 청산 목표는 메시지 소비와 별개로 실제 포지션/주문 종결까지 보유한다.

int R8CloseIndex(ulong masterId,ulong positionId,bool create=false) {
    for(int i=0;i<ArraySize(r8_closes);i++) {
        if((masterId>0&&r8_closes[i].masterId==masterId)||(positionId>0&&r8_closes[i].positionId==positionId)) return i;
    }
    if(!create) return -1;
    int n=ArraySize(r8_closes);
    if(n>=65536||ArrayResize(r8_closes,n+1)!=n+1) { R5Hold("CLOSE_INTENT_CAPACITY"); return -1; }
    r8_closes[n].masterId=masterId; r8_closes[n].positionId=positionId; r8_closes[n].order=0;
    r8_closes[n].nextTick=0; r8_closes[n].ratio=1; r8_closes[n].target=-1;
    r8_closes[n].all=false; r8_closes[n].awaiting=false; r8_closes[n].uncertain=false; r8_closes[n].attempts=0;
    return n;
}
void R8RemoveClose(int n) {
    for(int i=n;i<ArraySize(r8_closes)-1;i++) r8_closes[i]=r8_closes[i+1];
    ArrayResize(r8_closes,ArraySize(r8_closes)-1);
}
ulong R8MasterForPosition(ulong id) {
    for(int i=0;i<ArraySize(mapping);i++) if(mapping[i].slaveIdentifier==id) return mapping[i].masterTicket;
    for(int i=0;i<ArraySize(pendingOrders);i++) if(pendingOrders[i].positionId==id) return pendingOrders[i].masterTicket;
    return 0;
}
bool R8IsCloseOrder(ulong order) {
    for(int i=0;i<ArraySize(r8_closes);i++) if(order>0&&r8_closes[i].order==order) return true;
    return false;
}
double R8RemainingTarget(double initial,double ratio,double step,double minimum) {
    if(!MathIsValidNumber(initial)||!MathIsValidNumber(ratio)||!MathIsValidNumber(step)||
       !MathIsValidNumber(minimum)||initial<=0||step<=0||minimum<=0||ratio<0||ratio>1) return -1;
    if(ratio==0) return 0;
    double target=MathCeil(initial*ratio/step-0.0000001)*step;
    double smallest=MathCeil(minimum/step-0.0000001)*step;
    return NormalizeDouble(MathMin(initial,MathMax(target,smallest)),8);
}
ulong R8CloseRetryDelay(int attempts) {
    return (ulong)MathMin(15000.0,1000.0*MathPow(2.0,MathMin(4,MathMax(0,attempts-1))));
}
void R8QueueFlat(ulong positionId,string reason) {
    if(positionId==0) return;
    ulong masterId=R8MasterForPosition(positionId);
    int n=R8CloseIndex(masterId,positionId,true); if(n<0) return;
    bool first=!r8_closes[n].all;
    if(masterId>0) r8_closes[n].masterId=masterId;
    r8_closes[n].positionId=positionId; r8_closes[n].all=true; r8_closes[n].ratio=0; r8_closes[n].target=0;
    if(masterId>0) R6PlanClose(masterId,0);
    if(first) Print("[CLOSE_FLAT_TARGET] PositionId=",positionId," | ",reason);
}
void R8QueueSourceClose(ulong masterId,double sourceRemaining,double sourceClosed) {
    ulong ticket=GetSlaveTicket(StringFormat("%I64u",masterId)),positionId=0;
    if(ticket>0&&PositionSelectByTicket(ticket)) positionId=(ulong)PositionGetInteger(POSITION_IDENTIFIER);
    if(positionId==0&&!R7HasPending(masterId)) return;
    int local=R6Index(masterId,true); if(local<0) return;
    if(r6_local[local].sourceInitial<=0) r6_local[local].sourceInitial=sourceRemaining+sourceClosed;
    double ratio=sourceRemaining/r6_local[local].sourceInitial;
    if(!MathIsValidNumber(ratio)||ratio<0||ratio>1) { R5Hold("CLOSE_SOURCE_RATIO_INVALID"); return; }
    int n=R8CloseIndex(masterId,positionId,true); if(n<0) return;
    r8_closes[n].masterId=masterId;
    if(positionId>0) r8_closes[n].positionId=positionId;
    // 이미 전량청산을 목표로 잡았다면 후속 부분청산으로 목표를 완화하지 않는다.
    if(sourceRemaining==0) r8_closes[n].all=true;
    r8_closes[n].ratio=r8_closes[n].all?0:MathMin(r8_closes[n].ratio,ratio);
}
bool R8DailyEntryLive() {
    for(int i=OrdersTotal()-1;i>=0;i--) {
        ulong order=OrderGetTicket(i);
        if(order>0&&(ulong)OrderGetInteger(ORDER_MAGIC)==MagicNumber&&!R8IsCloseOrder(order)) return true;
    }
    return false;
}
void R8ProcessCloses() {
    if((ulong)AccountInfoInteger(ACCOUNT_LOGIN)!=r5_targetLogin||AccountInfoString(ACCOUNT_SERVER)!=r5_targetServer) return;
    int budget=MathMin(16,ArraySize(r8_closes));
    for(int pass=0;pass<budget&&ArraySize(r8_closes)>0;pass++) {
        if(r8_closeScan>=ArraySize(r8_closes)) r8_closeScan=0;
        int n=r8_closeScan++;
        ulong now=GetTickCount64(),master=r8_closes[n].masterId;
        if(master==0&&r8_closes[n].positionId>0) {
            master=R8MasterForPosition(r8_closes[n].positionId); r8_closes[n].masterId=master;
        }
        if(master>0&&R7HasPending(master)) { R8CancelPendingEntry(master); continue; }
        // 재시작으로 pending 메모리가 없어도 일일 긴급 정리는 실제 진입 주문 종료를 먼저 기다린다.
        if(isDailyLocked&&R8DailyEntryLive()) continue;
        if(r8_closes[n].positionId==0&&master>0) {
            ulong t=GetSlaveTicket(StringFormat("%I64u",master));
            if(t>0&&PositionSelectByTicket(t)) r8_closes[n].positionId=(ulong)PositionGetInteger(POSITION_IDENTIFIER);
            else { R8RemoveClose(n); continue; } // 진입 주문이 무체결로 종결됨
        }
        if(r8_closes[n].awaiting&&r8_closes[n].order>0) {
            ulong orderPosition=r8_closes[n].positionId;
            if(!R8OrderSettled(r8_closes[n].order,orderPosition)) continue;
            if(orderPosition!=r8_closes[n].positionId) { R5Hold("CLOSE_ORDER_POSITION_MISMATCH"); continue; }
            r8_closes[n].awaiting=false; r8_closes[n].order=0; r8_closes[n].uncertain=false;
        }
        ulong ticket=0;
        if(!R8SelectIdentifier(r8_closes[n].positionId,ticket)) {
            if(R8ConfirmedClosed(r8_closes[n].positionId)) {
                if(master>0) R6PlanClose(master,0);
                Print("[CLOSE_TARGET_CONFIRMED] PositionId=",r8_closes[n].positionId," | Flat");
                R8RemoveClose(n);
            }
            continue;
        }
        string symbol=PositionGetString(POSITION_SYMBOL);
        double current=PositionGetDouble(POSITION_VOLUME),initial=0,exited=0;
        double step=SymbolInfoDouble(symbol,SYMBOL_VOLUME_STEP),minimum=SymbolInfoDouble(symbol,SYMBOL_VOLUME_MIN);
        if(step<=0||minimum<=0||!R8HistoryVolume(r8_closes[n].positionId,initial,exited)||
           MathAbs(initial-exited-current)>step*0.0001) continue;
        double target=r8_closes[n].all?0:R8RemainingTarget(initial,r8_closes[n].ratio,step,minimum);
        if(target<0) continue;
        r8_closes[n].target=target;
        if(master>0) R6PlanClose(master,MathMin(target,current));
        double amount=NormalizeDouble(MathFloor((current-target)/step+0.0000001)*step,8);
        if(current<=target+step*0.0001||(!r8_closes[n].all&&amount<minimum-step*0.0001)) {
            if(master>0) R6PlanClose(master,current);
            Print("[CLOSE_TARGET_CONFIRMED] PositionId=",r8_closes[n].positionId," | Remaining=",current);
            R8RemoveClose(n); continue;
        }
        // 불명확한 접수 결과에 대해 시간 경과만으로 추가 주문을 제출하지 않는다.
        if(r8_closes[n].uncertain||r8_closes[n].awaiting||now<r8_closes[n].nextTick) continue;
        CTrade closeTrade; closeTrade.SetExpertMagicNumber(MagicNumber);
        r8_closes[n].attempts=MathMin(32,r8_closes[n].attempts+1);
        r8_closes[n].nextTick=now+R8CloseRetryDelay(r8_closes[n].attempts);
        if(!closeTrade.SetTypeFillingBySymbol(symbol)) continue;
        bool ok=r8_closes[n].all?closeTrade.PositionClose(ticket):closeTrade.PositionClosePartial(ticket,amount);
        uint code=closeTrade.ResultRetcode();
        ulong order=closeTrade.ResultOrder(),deal=closeTrade.ResultDeal();
        if(order==0&&deal>0&&HistoryDealSelect(deal)) order=(ulong)HistoryDealGetInteger(deal,DEAL_ORDER);
        Print("[CLOSE_ORDER_RESULT] PositionId=",r8_closes[n].positionId," | Target=",target,
              " | LocalResult=",ok," | Retcode=",code," | Order=",order," | Deal=",deal);
        bool accepted=code==TRADE_RETCODE_DONE||code==TRADE_RETCODE_DONE_PARTIAL||code==TRADE_RETCODE_PLACED;
        if(order>0) { r8_closes[n].order=order; r8_closes[n].awaiting=true; }
        else if(accepted||deal>0||(!R7Retryable(code)&&!R7DefiniteReject(code))) {
            r8_closes[n].uncertain=true;
            R5Hold("CLOSE_RESULT_UNCERTAIN");
        }
        // 명확한 거절은 목표를 보존하고 backoff 후 다시 실제 잔량부터 확인한다.
    }
}

void CloseMappedPosition(string sym, string masterTicketStr, double targetVol, double masterClosedVol) {
    ulong masterId=0;
    if(!R5UInt64(masterTicketStr,masterId)||masterId==0) return;
    R8QueueSourceClose(masterId,targetVol,masterClosedVol);
}

void ModifyMappedPosition(string sym, string masterTicketStr, double newSL, double newTP) {
    ulong sTicket = GetSlaveTicket(masterTicketStr);
    // [TRACE ONLY] MODIFY 신호와 현재 티켓 매핑 조회 결과 기록
    Print("[TRACE/MT5 SLAVE MODIFY INPUT] masterTicket=", masterTicketStr, " | mappedSlaveTicket=", sTicket,
          " | symbol=", sym, " | newSL=", DoubleToString(newSL, 8), " | newTP=", DoubleToString(newTP, 8));
    if(sTicket == 0) return;

    if(PositionSelectByTicket(sTicket)) {
        string posSym = PositionGetString(POSITION_SYMBOL);
        double curTP = PositionGetDouble(POSITION_TP);
        double curSL = PositionGetDouble(POSITION_SL);
        double point = SymbolInfoDouble(posSym, SYMBOL_POINT);
        
        bool slChanged = (newSL > 0 && MathAbs(curSL - newSL) > point);
        bool tpChanged = (MathAbs(curTP - newTP) > point); 

        // [TRACE ONLY] 실제 변경 판정에 사용되는 현재값/목표값 기록
        Print("[TRACE/MT5 SLAVE MODIFY CHECK] masterTicket=", masterTicketStr, " | slaveTicket=", sTicket,
              " | curSL=", DoubleToString(curSL, 8), " | newSL=", DoubleToString(newSL, 8),
              " | curTP=", DoubleToString(curTP, 8), " | newTP=", DoubleToString(newTP, 8),
              " | point=", DoubleToString(point, 10), " | slChanged=", slChanged, " | tpChanged=", tpChanged);
        
        if (slChanged || tpChanged) {
            double reqSL = (newSL > 0) ? newSL : curSL;
            if(!trade.PositionModify(sTicket, reqSL, newTP)) {
                uint retCode = trade.ResultRetcode();
                string msg = StringFormat("?? [SL/TP 변경 실패] %s | 에러코드: %d", posSym, retCode);
                AddToTelegramQueue(msg); Print(msg);
            }
        }
    }
}

void ProcessHeartbeatSync(string activeMasterTicketsStr) {
    if(activeMasterTicketsStr == "NONE") {
        for(int i = ArraySize(mapping) - 1; i >= 0; i--) {
            ulong sTicket = mapping[i].slaveTicket;
            if(R8SelectMapping(i)) R8QueueFlat(mapping[i].slaveIdentifier,"SYNC source flat");
        }
        return;
    }
    
    string mPairs[]; ushort u_sep = ','; StringSplit(activeMasterTicketsStr, u_sep, mPairs);
    
    for(int i = 0; i < ArraySize(mapping); i++) {
        ulong sTicket = mapping[i].slaveTicket;
        if(R8SelectMapping(i)) {
            sTicket=mapping[i].slaveTicket;
            bool found = false;
            double masterSL = 0.0;
            double masterTP = 0.0;
            
            for(int j = 0; j < ArraySize(mPairs); j++) {
                string kv[]; ushort colon = ':'; StringSplit(mPairs[j], colon, kv);
                if(ArraySize(kv) >= 2 && StringToInteger(kv[0]) == mapping[i].masterTicket) { 
                    found = true; 
                    masterSL = StringToDouble(kv[1]);
                    if(ArraySize(kv) == 3) masterTP = StringToDouble(kv[2]);
                    break; 
                }
            }
            
            if(!found) R8QueueFlat(mapping[i].slaveIdentifier,"SYNC source closed");
            else {
                double slaveSL = PositionGetDouble(POSITION_SL);
                double slaveTP = PositionGetDouble(POSITION_TP);
                string sym = PositionGetString(POSITION_SYMBOL);
                double point = SymbolInfoDouble(sym, SYMBOL_POINT);
                
                bool needsSyncSL = (masterSL > 0 && MathAbs(slaveSL - masterSL) > point);
                bool needsSyncTP = (MathAbs(slaveTP - masterTP) > point);
                
                if(needsSyncSL || needsSyncTP) {
                    double reqSL = (masterSL > 0) ? masterSL : slaveSL;
                    if(trade.PositionModify(sTicket, reqSL, masterTP)) {
                        Print("?? [SYNC 교정] 누락된 주문선(SL/TP) 실시간 강제 재동기화 완료");
                    }
                }
            }
        }
    }
}

void ProcessMessage(string msg) {
    string result[]; ushort u_sep = '|'; StringSplit(msg, u_sep, result);
    string action = "", symbol = "", ticketStr = "", ticketsStr = "";
    double entryPrice = 0.0, slPrice = 0.0, tpPrice = 0.0, targetVol = 0.0, closedVol = 0.0;

    // [TRACE ONLY] ProcessMessage에 전달된 비-SYNC 원문 기록
    if(StringFind(msg, "ACTION=SYNC") < 0)
        Print("[TRACE/MT5 SLAVE PROCESS RAW] ", msg);
    
    for(int i=0; i<ArraySize(result); i++) {
        string part = result[i];
        StringTrimLeft(part); StringTrimRight(part); 
        
        if(StringFind(part, "ACTION=") >= 0) action = StringSubstr(part, 7);
        else if(StringFind(part, "SYMBOL=") >= 0) { symbol = StringSubstr(part, 7); symbol = GetMappedSymbol(symbol); }
        else if(StringFind(part, "TICKETS=") >= 0) ticketsStr = StringSubstr(part, 8);
        else if(StringFind(part, "TICKET=") >= 0) ticketStr = StringSubstr(part, 7);
        else if(StringFind(part, "PRICE=") >= 0) entryPrice = StringToDouble(StringSubstr(part, 6));
        else if(StringFind(part, "SL=") >= 0) slPrice = StringToDouble(StringSubstr(part, 3));
        else if(StringFind(part, "TP=") >= 0) tpPrice = StringToDouble(StringSubstr(part, 3)); 
        else if(StringFind(part, "TARGET_VOL=") >= 0) targetVol = StringToDouble(StringSubstr(part, 11));
        else if(StringFind(part, "CLOSED_VOL=") >= 0) closedVol = StringToDouble(StringSubstr(part, 11));
    }
    
    // [TRACE ONLY] 기존 파서가 실제 업무 로직에 넘기는 값 기록
    if(action != "SYNC")
        Print("[TRACE/MT5 SLAVE PARSED] action=", action, " | symbol=", symbol, " | masterTicket=", ticketStr,
              " | entry=", DoubleToString(entryPrice, 8), " | SL=", DoubleToString(slPrice, 8),
              " | TP=", DoubleToString(tpPrice, 8), " | targetVol=", DoubleToString(targetVol, 8),
              " | closedVol=", DoubleToString(closedVol, 8));

    if (action == "SYNC") ProcessHeartbeatSync(ticketsStr);
    else if (action == "BUY" || action == "SELL") {
        if(symbol == "ERROR_DUPLICATE_SYMBOL") {
            string blockMsg = "?? [진입 차단] 종합시세 심볼 중복. 수동 맵핑 설정 필요.";
            Print(blockMsg); AddToTelegramQueue(blockMsg); return;
        }

        // [TRACE ONLY] 진입 차단 판정 직전 실제 최종 심볼의 존재/선택 상태 기록
        bool finalSymbolExists = (bool)SymbolInfoInteger(symbol, SYMBOL_EXIST);
        bool finalSymbolSelect = (bool)SymbolInfoInteger(symbol, SYMBOL_SELECT);
        Print("[TRACE/SYMBOL FINAL CHECK] masterTicket=", ticketStr, " | action=", action, " | finalSymbol=", symbol,
              " | exist=", (finalSymbolExists ? "true" : "false"), " | select=", (finalSymbolSelect ? "true" : "false"));

        if(!SymbolInfoInteger(symbol, SYMBOL_EXIST)) {
            string blockMsg = StringFormat("?? [진입 차단] 미존재 심볼 (%s)", symbol);
            Print(blockMsg); AddToTelegramQueue(blockMsg); return;
        }
        if(!SymbolInfoInteger(symbol, SYMBOL_SELECT)) {
            ResetLastError();
            if(!SymbolSelect(symbol, true)) {
                string blockMsg = StringFormat("?? [진입 차단] 심볼 활성화 실패 (%s) | error=%d", symbol, GetLastError());
                Print(blockMsg); AddToTelegramQueue(blockMsg); return;
            }
        }
        if(!IsEntryAllowed()) return; 
        double calculatedLot = CalculateLotByRisk(symbol, entryPrice, slPrice);
        // [TRACE ONLY] 신규진입 직전 최종 계산값 기록
        Print("[TRACE/MT5 SLAVE ENTRY DECISION] masterTicket=", ticketStr, " | action=", action, " | symbol=", symbol,
              " | entry=", DoubleToString(entryPrice, 8), " | SL=", DoubleToString(slPrice, 8),
              " | TP=", DoubleToString(tpPrice, 8), " | calculatedLot=", DoubleToString(calculatedLot, 8));
        if (calculatedLot > 0.0) ExecuteTradeSafe(action, symbol, calculatedLot, slPrice, tpPrice, ticketStr);
    } 
    else if (action == "CLOSE") CloseMappedPosition(symbol, ticketStr, targetVol, closedVol);
    else if (action == "MODIFY") ModifyMappedPosition(symbol, ticketStr, slPrice, tpPrice);
}

void OnTradeTransaction(const MqlTradeTransaction& trans, const MqlTradeRequest& request, const MqlTradeResult& result) {
    if((ulong)AccountInfoInteger(ACCOUNT_LOGIN)!=r5_targetLogin||AccountInfoString(ACCOUNT_SERVER)!=r5_targetServer) return;
    if (trans.type == TRADE_TRANSACTION_DEAL_ADD) {
        r11_riskDirty=true;
        if (HistoryDealSelect(trans.deal)) {
            long magic = HistoryDealGetInteger(trans.deal, DEAL_MAGIC);
            ulong eventId=(ulong)HistoryDealGetInteger(trans.deal,DEAL_POSITION_ID);
            bool managed=(ulong)magic==MagicNumber;
            for(int k=0;!managed&&k<ArraySize(mapping);k++) if(mapping[k].slaveIdentifier==eventId) managed=true;
            if(!managed) return;

            long deal_entry = HistoryDealGetInteger(trans.deal, DEAL_ENTRY);
            if (deal_entry == DEAL_ENTRY_IN) {
                ulong deal_order = (ulong)HistoryDealGetInteger(trans.deal, DEAL_ORDER);
                ulong pos_id = (ulong)HistoryDealGetInteger(trans.deal, DEAL_POSITION_ID);

                // [TRACE ONLY] 브로커 체결 이벤트의 실제 order/position 식별자 기록
                Print("[TRACE/MT5 SLAVE FILL] deal=", trans.deal, " | dealOrder=", deal_order,
                      " | positionId=", pos_id, " | symbol=", trans.symbol, " | price=", DoubleToString(trans.price, 8),
                      " | volume=", DoubleToString(trans.volume, 8), " | pendingCount=", ArraySize(pendingOrders));

                for(int i = 0; i < ArraySize(pendingOrders); i++) {
                    if(pendingOrders[i].orderTicket == deal_order) {
                        // [TRACE ONLY] 체결 order와 pending master ticket 매칭 결과 기록
                        Print("[TRACE/MT5 SLAVE FILL MATCH] dealOrder=", deal_order,
                              " | masterTicket=", pendingOrders[i].masterTicket, " | positionId=", pos_id,
                              " | initialSL=", DoubleToString(pendingOrders[i].initialSL, 8),
                              " | initialTP=", DoubleToString(pendingOrders[i].initialTP, 8));
                        pendingOrders[i].positionId=pos_id;
                        ulong currentTicket=0;
                        if(!R8SelectIdentifier(pos_id,currentTicket)) return;
                        AddMapping(pendingOrders[i].masterTicket,currentTicket,pendingOrders[i].initialSL,pendingOrders[i].initialTP);
                        // 첫 체결에서는 pending을 제거하지 않는다. 최종 주문/체결 대조는 R7SettlePending이 담당한다.
                        break;
                    }
                }
            }
            else if (deal_entry == DEAL_ENTRY_OUT || deal_entry == DEAL_ENTRY_OUT_BY || deal_entry == DEAL_ENTRY_INOUT) {
                ulong pos_id = (ulong)HistoryDealGetInteger(trans.deal, DEAL_POSITION_ID);
                long exitReason=HistoryDealGetInteger(trans.deal,DEAL_REASON);
                if(exitReason==DEAL_REASON_SL||exitReason==DEAL_REASON_TP||exitReason==DEAL_REASON_CLIENT||exitReason==DEAL_REASON_MOBILE||exitReason==DEAL_REASON_WEB) R6LocalExit(pos_id);
                double profit = HistoryDealGetDouble(trans.deal, DEAL_PROFIT) + HistoryDealGetDouble(trans.deal, DEAL_SWAP) + HistoryDealGetDouble(trans.deal, DEAL_COMMISSION);
                double closed_vol = HistoryDealGetDouble(trans.deal, DEAL_VOLUME);
                string sym = HistoryDealGetString(trans.deal, DEAL_SYMBOL);
                
                double pct = 0.0;
                if(initialBalance > 0) pct = (profit / initialBalance) * 100.0;
                
                string sign = (profit > 0) ? "+" : "";
                ulong currentTicket=0;
                bool isPartial = R8SelectIdentifier(pos_id,currentTicket);
                
                string header = isPartial ? "?? [반익반본]" : "?? [전체 청산]";
                string msg = StringFormat("%s %s | 물량: %.2f Lot | 확정 손익: %s$%.2f (%s%.2f%%)", header, sym, closed_vol, sign, profit, sign, pct);
                
                AddToTelegramQueue(msg);
            }
        }
    }
}


// R8: 일일 신규 진입 잠금과 실제 청산 완료를 별개의 상태로 유지한다.
int R8DayKey() {
    MqlDateTime dt; if(!TimeToStruct(TimeCurrent(),dt)) return 0;
    return dt.year*10000+dt.mon*100+dt.day;
}
bool R8OwnExposure() {
    if(ArraySize(r8_closes)>0||ArraySize(pendingOrders)>0) return true;
    for(int i=PositionsTotal()-1;i>=0;i--) {
        if(PositionGetTicket(i)>0&&(ulong)PositionGetInteger(POSITION_MAGIC)==MagicNumber) return true;
    }
    for(int i=OrdersTotal()-1;i>=0;i--) {
        if(OrderGetTicket(i)>0&&(ulong)OrderGetInteger(ORDER_MAGIC)==MagicNumber) return true;
    }
    return false;
}
bool R8SaveDaily() {
    string p=R8LocalPrefix()+"D.";
    // 잠금 먼저 저장한다. 실패하면 실행 중 신규 진입은 계속 차단한다.
    bool ok=GlobalVariableSet(p+"L",isDailyLocked?1.0:0.0)>0;
    ok=(GlobalVariableSet(p+"B",initialBalance)>0)&&ok;
    ok=(GlobalVariableSet(p+"Y",r8_dailyDay)>0)&&ok;
    GlobalVariablesFlush();
    if(!ok) { isDailyLocked=true; Print("[DAILY_STATE_SAVE_FAILED] 신규 진입 차단 / 저장소 확인 필요"); }
    return ok;
}
bool R8LoadDaily() {
    string p=R8LocalPrefix()+"D.";
    bool y=GlobalVariableCheck(p+"Y"),b=GlobalVariableCheck(p+"B"),l=GlobalVariableCheck(p+"L");
    if(y||b||l) {
        if(!(y&&b&&l)) { Print("[DAILY_STATE_INCOMPLETE]"); return false; }
        r8_dailyDay=(int)GlobalVariableGet(p+"Y"); initialBalance=GlobalVariableGet(p+"B");
        double flag=GlobalVariableGet(p+"L");
        if(r8_dailyDay<=0||!MathIsValidNumber(initialBalance)||initialBalance<=0||(flag!=0.0&&flag!=1.0)) return false;
        isDailyLocked=(flag==1.0);
        return true;
    }
    r8_dailyDay=R8DayKey(); initialBalance=AccountInfoDouble(ACCOUNT_BALANCE);
    if(r8_dailyDay<=0||!MathIsValidNumber(initialBalance)||initialBalance<=0) return false;
    isDailyLocked=false; return R8SaveDaily();
}
void R8DailyLiquidate() {
    r7_entry.active=false; // 아직 제출하지 않은 진입 재시도는 중단한다.
    ulong now=GetTickCount64();
    if(now>=r8_dailyCancelTick) {
        r8_dailyCancelTick=now+1000;
        for(int i=OrdersTotal()-1;i>=0;i--) {
            ulong order=OrderGetTicket(i);
            if(order==0||(ulong)OrderGetInteger(ORDER_MAGIC)!=MagicNumber||R8IsCloseOrder(order)) continue;
            CTrade cancel; cancel.SetExpertMagicNumber(MagicNumber);
            bool ok=cancel.OrderDelete(order);
            Print("[DAILY_ENTRY_CANCEL] Order=",order," | LocalResult=",ok," | Retcode=",cancel.ResultRetcode());
        }
    }
    for(int i=PositionsTotal()-1;i>=0;i--) {
        if(PositionGetTicket(i)>0&&(ulong)PositionGetInteger(POSITION_MAGIC)==MagicNumber)
            R8QueueFlat((ulong)PositionGetInteger(POSITION_IDENTIFIER),"daily loss limit");
    }
}
void CheckDrawdown() {
    if((ulong)AccountInfoInteger(ACCOUNT_LOGIN)!=r5_targetLogin||AccountInfoString(ACCOUNT_SERVER)!=r5_targetServer) return;
    int today=R8DayKey();
    if(today<=0) { isDailyLocked=true; return; }
    if(today!=r8_dailyDay) {
        // 전일 긴급 정리가 끝나지 않은 상태에서 날짜만으로 신규 진입을 다시 허용하지 않는다.
        if(isDailyLocked&&R8OwnExposure()) { R8DailyLiquidate(); return; }
        double balance=AccountInfoDouble(ACCOUNT_BALANCE);
        if(!MathIsValidNumber(balance)||balance<=0) { isDailyLocked=true; return; }
        r11_riskDirty=true; r8_dailyDay=today; initialBalance=balance; isDailyLocked=false;
        if(!R8SaveDaily()) return;
        AddToTelegramQueue("?? [일일 기준 갱신] 브로커 서버 날짜와 잔고 기준을 저장했습니다.");
    }
    if(!isDailyLocked) {
        double equity=AccountInfoDouble(ACCOUNT_EQUITY);
        if(!MathIsValidNumber(equity)||initialBalance<=0) { isDailyLocked=true; R8SaveDaily(); }
        else if(((initialBalance-equity)/initialBalance)*100.0>=MaxDailyLossPercent) {
            isDailyLocked=true; R8SaveDaily(); // 주문 요청보다 먼저 신규 진입 차단을 확정한다.
            AddToTelegramQueue("?? [긴급 보호] 일일 손실 한도 도달. 진입 잔량 취소 및 실제 청산 완료까지 관리합니다.");
        }
    }
    if(isDailyLocked) R8DailyLiquidate();
}
