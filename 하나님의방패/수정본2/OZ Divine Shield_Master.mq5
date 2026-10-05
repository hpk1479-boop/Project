//+------------------------------------------------------------------+
//|                    OZ Divine Shield_Master                            |
//+------------------------------------------------------------------+
#property copyright "Oilve Oil"
#property version   "1.60"
#property strict

// [4단계] MMF_Protocol_step03.md / v2.0 고정 규격. 레이아웃 변경 없음.
#define PAGE_READWRITE          0x04
#define FILE_MAP_ALL_ACCESS     0xF001F
#define INVALID_HANDLE_VALUE    -1
#define V2_HEADER_BYTES         256
#define V2_SLOT_BYTES           1024
#define V2_CAPACITY             4096
#define V2_EVENTS_BYTES         4194560
#define V2_COMMAND_BYTES        960
#define V2_STATE_BYTES          65536
#define V2_STATE_HEADER_BYTES   128
#define V2_STATE_PAYLOAD_BYTES  65408
#define V2_BATCH_MAX            16
#define V2_INITIALIZING         0
#define V2_READY                1
#define V2_FAULT                2
#define V2_STOPPED              3
#define V2_WAIT_OK              0x00000000
#define V2_WAIT_ABANDONED       0x00000080
#define V2_WAIT_TIMEOUT         0x00000102
#define V2_SEQ_MAX              9223372036854775807

#import "kernel32.dll"
long CreateFileMappingW(long hFile, long attributes, uint protect, uint sizeHigh, uint sizeLow, string name);
long MapViewOfFile(long mapping, uint access, uint offsetHigh, uint offsetLow, ulong bytes);
int UnmapViewOfFile(long address);
int CloseHandle(long handle);
long CreateSemaphoreW(long attributes, int initialCount, int maximumCount, string name);
int ReleaseSemaphore(long handle, int releaseCount, long previousCount);
long CreateMutexW(long attributes, int initialOwner, string name);
uint WaitForSingleObject(long handle, uint milliseconds);
int ReleaseMutex(long handle);
// SIZE_T는 x64에서 8바이트. 읽기/쓰기 오버로드를 명시한다.
void RtlMoveMemory(long destination, uchar &source[], ulong length);
void RtlMoveMemory(uchar &destination[], long source, ulong length);
#import

#import "ole32.dll"
int CoCreateGuid(uchar &guidBytes[]);
#import

input group "==== [마스터 추적 설정] ===="
input bool  Inp_TrackAllPositions = true;
input ulong Inp_MasterMagic       = 0;

input group "==== [MMF v2 통신] ===="
input string Inp_StreamId = "OZ_MAIN"; // A-Z / 0-9 / _, 1~32자

// 이하 상태는 MT5 EA 이벤트 흐름 한 곳에서만 변경한다.
struct V2Outbound {
    string message;
    uchar payload[V2_COMMAND_BYTES];
    int length;
    uint payloadCrc;
};
struct V2Prepared {
    uchar slot[V2_SLOT_BYTES];
    uchar header[V2_HEADER_BYTES];
    ulong sequence;
};
V2Outbound signalQueue[V2_CAPACITY];
V2Prepared v2_batch[V2_BATCH_MAX];
int v2_queueHead = 0;
int v2_queueCount = 0;
ulong msgSequence = 0;
ulong v2_stateVersion = 0;
ulong v2_lastAliveTick = 0;
ulong v2_lastSyncTick = 0;
ulong v2_sourceLogin = 0;
string v2_sourceServer = "";
string v2_sessionText = "";
string v2_faultReason = "";
uchar v2_session[16];
uchar v2_headerTemplate[V2_HEADER_BYTES];
uchar v2_readHeader[V2_HEADER_BYTES];
uchar v2_readSlot[V2_SLOT_BYTES];
uchar v2_stateBody[V2_STATE_PAYLOAD_BYTES];
uchar v2_stateHeader[V2_STATE_HEADER_BYTES];
uchar v2_stateRead[V2_STATE_BYTES];
uchar v2_committed[4] = {2, 0, 0, 0};
long hMapFile = 0;
long pBuf = 0;
long v2_stateMap = 0;
long v2_stateView = 0;
long v2_copyMutex = 0;
long v2_publisher = 0;
bool v2_publisherOwned = false;
bool v2_copyHeld = false;
bool v2_lockBroken = false;
bool v2_sessionInstalled = false;
bool v2_ready = false;
bool v2_faulted = false;
bool v2_faultHeaderPending = false;
bool v2_publishing = false;

// [V2 PURE HELPERS BEGIN]
void V2Put16(uchar &data[], int offset, ushort value) {
    for(int i = 0; i < 2; i++) data[offset + i] = (uchar)(value >> (8 * i));
}
void V2Put32(uchar &data[], int offset, uint value) {
    for(int i = 0; i < 4; i++) data[offset + i] = (uchar)(value >> (8 * i));
}
void V2Put64(uchar &data[], int offset, ulong value) {
    for(int i = 0; i < 8; i++) data[offset + i] = (uchar)(value >> (8 * i));
}
uint V2Get32(const uchar &data[], int offset) {
    uint value = 0;
    for(int i = 0; i < 4; i++) value |= ((uint)data[offset + i]) << (8 * i);
    return value;
}
ulong V2Get64(const uchar &data[], int offset) {
    ulong value = 0;
    for(int i = 0; i < 8; i++) value |= ((ulong)data[offset + i]) << (8 * i);
    return value;
}
uint V2Crc32(const uchar &data[], int offset, int count, int zeroOffset = -1) {
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
bool V2EqualBytes(const uchar &a[], int aOffset, const uchar &b[], int bOffset, int count) {
    for(int i = 0; i < count; i++) if(a[aOffset + i] != b[bOffset + i]) return false;
    return true;
}
bool V2ValidStream(string value) {
    int n = StringLen(value);
    if(n < 1 || n > 32) return false;
    for(int i = 0; i < n; i++) {
        ushort c = StringGetCharacter(value, i);
        if(!((c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9') || c == '_')) return false;
    }
    return true;
}
bool V2SafeValue(string value) {
    if(StringLen(value) == 0) return false;
    for(int i = 0; i < StringLen(value); i++) {
        ushort c = StringGetCharacter(value, i);
        if(c == 0 || c == '\r' || c == '\n' || c == '|' || c == '=') return false;
    }
    return true;
}
bool V2UInt64(string value, ulong &number) {
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
bool V2Decimal(string value, bool positive) {
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
bool V2Encode(string text, int maximum, uchar &bytes[], int &length, string &error) {
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
int V2FieldIndex(string key) {
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
bool V2ValidateCommand(string msg, string &error) {
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
        int field = V2FieldIndex(StringSubstr(parts[i], 0, eq));
        string value = StringSubstr(parts[i], eq + 1);
        if(field < 0 || (mask & (1 << field)) != 0 || !V2SafeValue(value)) {
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
    if(!V2UInt64(values[6], ticket) || ticket == 0) { error = "INVALID_TICKET"; return false; }
    if(entry && (!V2Decimal(values[2], true) || !V2Decimal(values[3], true) ||
                 !V2Decimal(values[4], false) || !V2Decimal(values[5], true))) {
        error = "INVALID_ENTRY_NUMBER"; return false;
    }
    if(action == "MODIFY" && (!V2Decimal(values[3], false) || !V2Decimal(values[4], false) || !V2Decimal(values[5], false))) {
        error = "INVALID_MODIFY_NUMBER"; return false;
    }
    if(action == "CLOSE" && (!V2Decimal(values[7], false) || !V2Decimal(values[8], true))) {
        error = "INVALID_CLOSE_NUMBER"; return false;
    }
    return true;
}
bool V2ValidateSync(string msg, string &error) {
    string prefix = "ACTION=SYNC|TICKETS=";
    if(StringFind(msg, prefix) != 0) { error = "INVALID_SYNC_PREFIX"; return false; }
    string tickets = StringSubstr(msg, StringLen(prefix));
    if(tickets == "NONE") return true;
    if(!V2SafeValue(tickets)) { error = "INVALID_SYNC_LIST"; return false; }
    if(StringGetCharacter(tickets, StringLen(tickets) - 1) == ',') { error = "EMPTY_SYNC_ROW"; return false; }
    string rows[];
    int count = StringSplit(tickets, ',', rows);
    if(count <= 0) { error = "EMPTY_SYNC_LIST"; return false; }
    for(int i = 0; i < count; i++) {
        string fields[];
        if(StringSplit(rows[i], ':', fields) != 3) { error = "INVALID_SYNC_ROW"; return false; }
        ulong ticket;
        if(!V2UInt64(fields[0], ticket) || ticket == 0 || !V2Decimal(fields[1], false) || !V2Decimal(fields[2], false)) {
            error = "INVALID_SYNC_VALUE"; return false;
        }
    }
    return true;
}
void V2BuildHeader(ulong sequence, ulong tick, uint status, uchar &header[]) {
    ArrayCopy(header, v2_headerTemplate, 0, 0, V2_HEADER_BYTES);
    V2Put64(header, 48, sequence);
    V2Put64(header, 56, tick);
    V2Put32(header, 64, status);
    V2Put32(header, 80, V2Crc32(header, 0, V2_HEADER_BYTES, 80));
}
bool V2HeaderIdentity(const uchar &header[]) {
    if(V2Get32(header, 80) != V2Crc32(header, 0, V2_HEADER_BYTES, 80)) return false;
    if(!V2EqualBytes(header, 0, v2_headerTemplate, 0, 48)) return false;
    if(!V2EqualBytes(header, 68, v2_headerTemplate, 68, 12)) return false;
    if(!V2EqualBytes(header, 84, v2_headerTemplate, 84, V2_HEADER_BYTES - 84)) return false;
    return V2Get64(header, 48) <= (ulong)V2_SEQ_MAX && V2Get32(header, 64) <= V2_STOPPED;
}
bool V2HeaderMatches(const uchar &header[], ulong sequence, uint status) {
    return V2HeaderIdentity(header) && V2Get64(header, 48) == sequence && V2Get32(header, 64) == status;
}
void V2BuildSlot(const uchar &payload[], int length, uint payloadCrc, ulong sequence, ulong tick, uchar &slot[]) {
    ArrayInitialize(slot, 0);
    V2Put32(slot, 0, 1); // WRITING. COMMITTED는 실제 기록 후 별도로 쓴다.
    V2Put32(slot, 4, (uint)length);
    V2Put64(slot, 8, sequence);
    ArrayCopy(slot, v2_session, 16, 0, 16);
    V2Put64(slot, 32, tick);
    V2Put16(slot, 40, 1);
    V2Put16(slot, 42, 1);
    V2Put32(slot, 48, payloadCrc);
    V2Put32(slot, 52, V2Crc32(slot, 4, 48));
    ArrayCopy(slot, payload, 64, 0, length);
}
void V2BuildStateHeader(int length, uint payloadCrc, ulong version, ulong baseSeq, ulong tick, uchar &header[]) {
    ArrayInitialize(header, 0);
    header[0] = 'O'; header[1] = 'Z'; header[2] = 'M'; header[3] = 'M';
    header[4] = 'F'; header[5] = 'S'; header[6] = '2';
    V2Put16(header, 8, 2);
    V2Put32(header, 12, V2_STATE_HEADER_BYTES);
    V2Put32(header, 16, V2_STATE_BYTES);
    V2Put32(header, 20, (uint)length);
    ArrayCopy(header, v2_session, 24, 0, 16);
    V2Put64(header, 40, version);
    V2Put64(header, 48, baseSeq);
    V2Put64(header, 56, tick);
    V2Put32(header, 64, payloadCrc);
    V2Put16(header, 72, 2); // FULL_SNAPSHOT
    V2Put16(header, 74, 1);
    V2Put32(header, 76, 1); // OUTBOX_EMPTY
    V2Put32(header, 68, V2Crc32(header, 0, V2_STATE_HEADER_BYTES, 68));
}
// [V2 PURE HELPERS END]

// 반환값: 0=busy, 1=owned, 2=abandoned+owned, -1=실패.
int V2TryCopyLock(uint waitMs = 0) {
    if(v2_copyMutex == 0 || v2_lockBroken || v2_copyHeld) return -1;
    uint result = WaitForSingleObject(v2_copyMutex, waitMs);
    if(result == V2_WAIT_TIMEOUT) return 0;
    if(result == V2_WAIT_OK || result == V2_WAIT_ABANDONED) {
        v2_copyHeld = true;
        return result == V2_WAIT_ABANDONED ? 2 : 1;
    }
    return -1;
}
bool V2LeaveCopyLock() {
    if(!v2_copyHeld) return false;
    if(ReleaseMutex(v2_copyMutex) == 0) { v2_lockBroken = true; return false; }
    v2_copyHeld = false;
    return true;
}
// 잠금 밖에서만 호출. 최초 원인과 본문을 보존하고 정상 발행을 멈춘다.
void V2Fault(string reason, string raw = "") {
    if(!v2_faulted) {
        v2_faulted = true;
        v2_faultHeaderPending = true;
        v2_faultReason = reason;
        Print("[MMF v2 FAULT] ", reason, " | Stream=", Inp_StreamId, " | SourceLogin=", v2_sourceLogin, " | Session=", v2_sessionText,
              " | Published=", msgSequence, " | Queue=", v2_queueCount, " | RAW=", raw);
    }
}
bool V2CheckSource() {
    if((ulong)AccountInfoInteger(ACCOUNT_LOGIN) == v2_sourceLogin && AccountInfoString(ACCOUNT_SERVER) == v2_sourceServer)
        return true;
    V2Fault("SOURCE_ACCOUNT_CHANGED");
    return false;
}
// 상태만 바꾼다. 불분명한 게시 결과에서 PublishedSeq를 과거 로컬 값으로 되돌리지 않는다.
bool V2TryStatus(uint status) {
    if(!v2_sessionInstalled || pBuf == 0) return false;
    int acquired = V2TryCopyLock();
    if(acquired <= 0) return false;
    RtlMoveMemory(v2_readHeader, pBuf, (ulong)V2_HEADER_BYTES);
    bool valid = V2HeaderIdentity(v2_readHeader);
    if(valid) {
        V2Put32(v2_readHeader, 64, status);
        V2Put32(v2_readHeader, 80, V2Crc32(v2_readHeader, 0, V2_HEADER_BYTES, 80));
        RtlMoveMemory(pBuf, v2_readHeader, (ulong)V2_HEADER_BYTES);
    }
    bool released = V2LeaveCopyLock();
    return valid && released;
}
void V2ServiceFault() {
    if(v2_faultHeaderPending && V2TryStatus(V2_FAULT)) v2_faultHeaderPending = false;
}
void V2Cleanup() {
    v2_ready = false;
    if(v2_copyHeld && v2_copyMutex != 0) V2LeaveCopyLock();
    if(pBuf != 0) { UnmapViewOfFile(pBuf); pBuf = 0; }
    if(v2_stateView != 0) { UnmapViewOfFile(v2_stateView); v2_stateView = 0; }
    if(hMapFile != 0) { CloseHandle(hMapFile); hMapFile = 0; }
    if(v2_stateMap != 0) { CloseHandle(v2_stateMap); v2_stateMap = 0; }
    if(v2_copyMutex != 0) { CloseHandle(v2_copyMutex); v2_copyMutex = 0; }
    // MMF 접근을 끝낸 뒤 Publisher를 반납한다. 획득하지 않은 카운트는 반납하지 않는다.
    if(v2_publisher != 0) {
        if(v2_publisherOwned) ReleaseSemaphore(v2_publisher, 1, 0);
        CloseHandle(v2_publisher);
        v2_publisher = 0;
        v2_publisherOwned = false;
    }
}
// [6단계] State Kind=2 / PayloadVersion=1. v2 헤더/링 배치는 변경하지 않는다.
struct S6Row {
    ulong id, ticket, entrySeq, lastSeq;
    string symbol, side;
    double initialVolume, volume, price, sl, tp, step, copyPrice, copyDistance;
    int status; // 0=BASELINE, 1=PENDING_ENTRY, 2=PUBLISHED, 3=UNOBSERVED
};
bool S6UInt(string value, ulong &number) { return V2UInt64(value,number); }
string S6Field(string text, string key) {
    string parts[]; StringSplit(text,'|',parts);
    string prefix=key+"=";
    for(int i=0;i<ArraySize(parts);i++) if(StringFind(parts[i],prefix)==0) return StringSubstr(parts[i],StringLen(prefix));
    return "";
}
string S6HexSymbol(string symbol) {
    uchar bytes[]; int n; string error;
    if(!V2Encode(symbol,256,bytes,n,error)) return "";
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
        for(int j=4;j<=9;j++) if(!V2Decimal(f[j],j==4||j==5||j==6||j==9)) return false;
        if(!S6UInt(f[10],row.entrySeq)||!S6UInt(f[11],row.lastSeq)||row.entrySeq>row.lastSeq||row.lastSeq>cut) return false;
        if(!V2Decimal(f[12],false)||!V2Decimal(f[13],false)||!S6UInt(f[14],status)||status>3) return false;
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

bool V2PrepareState(string msg, ulong tick, string &error) {
    if(v2_queueCount != 0) { error = "SYNC_OUTBOX_NOT_EMPTY"; return false; }
    if(v2_stateVersion >= (ulong)V2_SEQ_MAX) { error = "STATE_VERSION_LIMIT_REINITIALIZE"; return false; }
    S6Row rows[]; bool consistent; ulong revision; string reason;
    if(!S6Parse(msg,msgSequence,rows,consistent,revision,reason)) { error="INVALID_FULL_SNAPSHOT"; return false; }
    uchar encoded[];
    int length;
    if(!V2Encode(msg, V2_STATE_PAYLOAD_BYTES, encoded, length, error)) return false;
    ArrayInitialize(v2_stateBody, 0);
    ArrayCopy(v2_stateBody, encoded, 0, 0, length);
    V2BuildStateHeader(length, V2Crc32(encoded, 0, length), v2_stateVersion + 1, msgSequence, tick, v2_stateHeader);
    return true;
}
bool V2StateReadbackMatches() {
    if(!V2EqualBytes(v2_stateRead, 0, v2_stateHeader, 0, V2_STATE_HEADER_BYTES)) return false;
    int length = (int)V2Get32(v2_stateHeader, 20);
    if(length < 1 || length > V2_STATE_PAYLOAD_BYTES) return false;
    if(V2Crc32(v2_stateRead, V2_STATE_HEADER_BYTES, length) != V2Get32(v2_stateHeader, 64)) return false;
    return V2EqualBytes(v2_stateRead, V2_STATE_HEADER_BYTES, v2_stateBody, 0, V2_STATE_PAYLOAD_BYTES);
}
bool V2Initialize() {
    if(!_IsX64 || !MQLInfoInteger(MQL_DLLS_ALLOWED)) {
        Print("[MMF v2 INIT] Windows x64 및 DLL 호출 허용이 필요합니다."); return false;
    }
    if(!V2ValidStream(Inp_StreamId)) { Print("[MMF v2 INIT] INVALID_STREAM_ID"); return false; }
    v2_sourceLogin = (ulong)AccountInfoInteger(ACCOUNT_LOGIN);
    v2_sourceServer = AccountInfoString(ACCOUNT_SERVER);
    uchar serverBytes[];
    int serverLength;
    string error;
    if(v2_sourceLogin == 0 || !V2Encode(v2_sourceServer, 96, serverBytes, serverLength, error)) {
        Print("[MMF v2 INIT] SOURCE_IDENTITY_INVALID | ", error); return false;
    }
    string root = "Local\\OZCopy.v2." + Inp_StreamId;
    v2_publisher = CreateSemaphoreW(0, 1, 1, root + ".Publisher");
    if(v2_publisher == 0) { Print("[MMF v2 INIT] PUBLISHER_CREATE_FAILED"); return false; }
    if(WaitForSingleObject(v2_publisher, 0) != V2_WAIT_OK) {
        Print("[MMF v2 INIT] PUBLISHER_ALREADY_ACTIVE_OR_UNAVAILABLE"); V2Cleanup(); return false;
    }
    v2_publisherOwned = true;
    v2_copyMutex = CreateMutexW(0, 0, root + ".CopyLock");
    hMapFile = CreateFileMappingW(INVALID_HANDLE_VALUE, 0, PAGE_READWRITE, 0, V2_EVENTS_BYTES, root + ".Events");
    v2_stateMap = CreateFileMappingW(INVALID_HANDLE_VALUE, 0, PAGE_READWRITE, 0, V2_STATE_BYTES, root + ".State");
    if(v2_copyMutex == 0 || hMapFile == 0 || v2_stateMap == 0) {
        Print("[MMF v2 INIT] OBJECT_CREATE_FAILED"); V2Cleanup(); return false;
    }
    pBuf = MapViewOfFile(hMapFile, FILE_MAP_ALL_ACCESS, 0, 0, V2_EVENTS_BYTES);
    v2_stateView = MapViewOfFile(v2_stateMap, FILE_MAP_ALL_ACCESS, 0, 0, V2_STATE_BYTES);
    if(pBuf == 0 || v2_stateView == 0) { Print("[MMF v2 INIT] MAP_VIEW_FAILED"); V2Cleanup(); return false; }
    ArrayInitialize(v2_session, 0);
    if(CoCreateGuid(v2_session) != 0) { Print("[MMF v2 INIT] SESSION_GUID_FAILED"); V2Cleanup(); return false; }
    bool anyByte = false;
    for(int i = 0; i < 16; i++) {
        if(v2_session[i] != 0) anyByte = true;
        v2_sessionText += StringFormat("%02X", (uint)v2_session[i]);
    }
    if(!anyByte) { Print("[MMF v2 INIT] ZERO_SESSION_GUID"); V2Cleanup(); return false; }
    ArrayInitialize(v2_headerTemplate, 0);
    v2_headerTemplate[0] = 'O'; v2_headerTemplate[1] = 'Z'; v2_headerTemplate[2] = 'M'; v2_headerTemplate[3] = 'M';
    v2_headerTemplate[4] = 'F'; v2_headerTemplate[5] = 'R'; v2_headerTemplate[6] = '2';
    V2Put16(v2_headerTemplate, 8, 2);
    V2Put32(v2_headerTemplate, 12, V2_HEADER_BYTES);
    V2Put32(v2_headerTemplate, 16, V2_SLOT_BYTES);
    V2Put32(v2_headerTemplate, 20, V2_CAPACITY);
    V2Put64(v2_headerTemplate, 24, V2_EVENTS_BYTES);
    ArrayCopy(v2_headerTemplate, v2_session, 32, 0, 16);
    V2Put64(v2_headerTemplate, 72, v2_sourceLogin);
    V2Put32(v2_headerTemplate, 84, (uint)serverLength);
    ArrayCopy(v2_headerTemplate, serverBytes, 88, 0, serverLength);

    ulong now = GetTickCount64();
    string sync = M6BuildSnapshot();
    if(!V2PrepareState(sync, now, error)) { Print("[MMF v2 INIT] ", error, " | RAW=", sync); V2Cleanup(); return false; }
    uchar initialHeader[V2_HEADER_BYTES];
    uchar readyHeader[V2_HEADER_BYTES];
    V2BuildHeader(0, now, V2_INITIALIZING, initialHeader);
    V2BuildHeader(0, now, V2_READY, readyHeader);
    int acquired = V2TryCopyLock(250);
    if(acquired <= 0) { Print("[MMF v2 INIT] COPY_LOCK_BUSY_OR_FAILED"); V2Cleanup(); return false; }
    // 새 소유권/새 세션 초기화는 이전 슬롯을 덮어 지우지 않고 헤더로 구분한다.
    RtlMoveMemory(pBuf, initialHeader, (ulong)V2_HEADER_BYTES);
    v2_sessionInstalled = true;
    RtlMoveMemory(v2_stateView + V2_STATE_HEADER_BYTES, v2_stateBody, (ulong)V2_STATE_PAYLOAD_BYTES);
    RtlMoveMemory(v2_stateView, v2_stateHeader, (ulong)V2_STATE_HEADER_BYTES);
    RtlMoveMemory(v2_stateRead, v2_stateView, (ulong)V2_STATE_BYTES);
    RtlMoveMemory(pBuf, readyHeader, (ulong)V2_HEADER_BYTES);
    RtlMoveMemory(v2_readHeader, pBuf, (ulong)V2_HEADER_BYTES);
    bool headerOk = V2HeaderMatches(v2_readHeader, 0, V2_READY);
    bool released = V2LeaveCopyLock();
    if(!released || !headerOk || !V2StateReadbackMatches()) {
        V2Fault("INITIALIZATION_READBACK_FAILED"); V2ServiceFault(); V2Cleanup(); return false;
    }
    v2_stateVersion = 1;
    v2_lastAliveTick = now;
    v2_lastSyncTick = now;
    v2_ready = true;
    Print("[MMF v2 READY] Stream=", Inp_StreamId, " | Session=", v2_sessionText, " | Slots=4096 | SlotBytes=1024");
    return true;
}

void V2IdleHeartbeat() {
    if(!v2_ready || v2_faulted || GetTickCount64() - v2_lastAliveTick < 500) return;
    ulong now = GetTickCount64();
    uchar nextHeader[V2_HEADER_BYTES];
    V2BuildHeader(msgSequence, now, V2_READY, nextHeader);
    int acquired = V2TryCopyLock();
    if(acquired == 0) return;
    if(acquired < 0) { V2Fault("HEARTBEAT_WAIT_FAILED"); return; }
    RtlMoveMemory(v2_readHeader, pBuf, (ulong)V2_HEADER_BYTES);
    bool valid = V2HeaderMatches(v2_readHeader, msgSequence, V2_READY);
    if(valid) {
        RtlMoveMemory(pBuf, nextHeader, (ulong)V2_HEADER_BYTES);
        RtlMoveMemory(v2_readHeader, pBuf, (ulong)V2_HEADER_BYTES);
        valid = V2EqualBytes(v2_readHeader, 0, nextHeader, 0, V2_HEADER_BYTES);
    }
    bool released = V2LeaveCopyLock();
    if(!valid || !released) { V2Fault("HEARTBEAT_HEADER_OR_RELEASE_FAILED"); return; }
    v2_lastAliveTick = now;
}

// SL/TP 수정 추적용 캐시 구조체
struct PosCache { ulong ticket; double sl; double tp; };
PosCache m_cache[];

// 설정 대기열 구조체 (모바일/원클릭 대응)
struct PendingTrade { ulong ticket; string action; string sym; double entryPrice; int digits; double tp; };
PendingTrade pendingTrades[];

// 중복 진입 발송 방지용 캐시
ulong processedEntries[];

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

struct M6Record {
    ulong id, entrySeq, lastSeq;
    string symbol, side;
    double price, distance, sl, tp, remaining;
    bool baseline;
};
M6Record m6_records[];
ulong m6_seenDeals[];
ulong m6_revision=0;
datetime m6_since=0;

int M6Index(ulong id, bool create=false) {
    for(int i=0;i<ArraySize(m6_records);i++) if(m6_records[i].id==id) return i;
    if(!create) return -1;
    int n=ArraySize(m6_records);
    if(n>=65536||ArrayResize(m6_records,n+1)!=n+1) { V2Fault("SNAPSHOT_LEDGER_CAPACITY"); return -1; }
    m6_records[n].id=id; m6_records[n].entrySeq=0; m6_records[n].lastSeq=0;
    m6_records[n].price=0; m6_records[n].distance=0; m6_records[n].sl=0; m6_records[n].tp=0;
    m6_records[n].remaining=-1; m6_records[n].baseline=false;
    return n;
}
bool M6Select(ulong id) {
    for(int i=0;i<PositionsTotal();i++) {
        ulong ticket=PositionGetTicket(i);
        if(ticket>0&&(ulong)PositionGetInteger(POSITION_IDENTIFIER)==id) return PositionSelectByTicket(ticket);
    }
    return false;
}
int M6DealIndex(ulong id) {
    int lo=0,hi=ArraySize(m6_seenDeals);
    while(lo<hi) { int mid=lo+(hi-lo)/2; if(m6_seenDeals[mid]<id) lo=mid+1; else hi=mid; }
    return lo;
}
bool M6Seen(ulong id) { int p=M6DealIndex(id); return p<ArraySize(m6_seenDeals)&&m6_seenDeals[p]==id; }
void M6Remember(ulong id) {
    int p=M6DealIndex(id),n=ArraySize(m6_seenDeals);
    if(p<n&&m6_seenDeals[p]==id) return;
    if(n>=65536||ArrayResize(m6_seenDeals,n+1)!=n+1) { V2Fault("SNAPSHOT_DEAL_CAPACITY"); return; }
    for(int i=n;i>p;i--) m6_seenDeals[i]=m6_seenDeals[i-1];
    m6_seenDeals[p]=id; m6_revision++;
}
bool M6Relevant(ulong deal) {
    long type=HistoryDealGetInteger(deal,DEAL_TYPE);
    if(type!=DEAL_TYPE_BUY&&type!=DEAL_TYPE_SELL) return false;
    ulong id=(ulong)HistoryDealGetInteger(deal,DEAL_POSITION_ID);
    return Inp_TrackAllPositions||(ulong)HistoryDealGetInteger(deal,DEAL_MAGIC)==Inp_MasterMagic||M6Index(id)>=0;
}
void M6Observe(ulong deal) {
    if(!M6Relevant(deal)) return;
    M6Remember(deal);
    M6Index((ulong)HistoryDealGetInteger(deal,DEAL_POSITION_ID),true);
}
bool M6Start() {
    m6_since=TimeCurrent()-1;
    for(int i=0;i<PositionsTotal();i++) {
        if(PositionGetTicket(i)==0) return false;
        if(!Inp_TrackAllPositions&&(ulong)PositionGetInteger(POSITION_MAGIC)!=Inp_MasterMagic) continue;
        int n=M6Index((ulong)PositionGetInteger(POSITION_IDENTIFIER),true);
        if(n<0) return false;
        m6_records[n].baseline=true;
    }
    if(!HistorySelect(m6_since,TimeCurrent()+60)) return false;
    for(int i=0;i<HistoryDealsTotal();i++) { ulong d=HistoryDealGetTicket(i); if(M6Relevant(d)) M6Remember(d); }
    return !v2_faulted;
}
bool M6AuditHistory() {
    if(!HistorySelect(m6_since,TimeCurrent()+60)) return false;
    for(int i=0;i<HistoryDealsTotal();i++) { ulong d=HistoryDealGetTicket(i); if(M6Relevant(d)&&!M6Seen(d)) return false; }
    return true;
}
void M6Published(string payload, ulong seq) {
    ulong id; if(!V2UInt64(S6Field(payload,"TICKET"),id)) { V2Fault("SNAPSHOT_SIGNAL_TICKET",payload); return; }
    int n=M6Index(id,true); if(n<0) return;
    string action=S6Field(payload,"ACTION");
    m6_records[n].lastSeq=seq; m6_records[n].symbol=S6Field(payload,"SYMBOL");
    if(action=="BUY"||action=="SELL") {
        m6_records[n].entrySeq=seq; m6_records[n].side=action;
        m6_records[n].price=StringToDouble(S6Field(payload,"PRICE"));
        m6_records[n].distance=StringToDouble(S6Field(payload,"SL_DIST"));
    }
    if(action=="BUY"||action=="SELL"||action=="MODIFY") {
        m6_records[n].sl=StringToDouble(S6Field(payload,"SL")); m6_records[n].tp=StringToDouble(S6Field(payload,"TP"));
    }
    if(action=="CLOSE") m6_records[n].remaining=StringToDouble(S6Field(payload,"TARGET_VOL"));
}
bool M6Initial(ulong id, double current, double step, double &initial) {
    initial=0; double exits=0; ulong firstOrder=0; bool supported=true;
    if(!HistorySelectByPosition(id)) return false;
    for(int i=0;i<HistoryDealsTotal();i++) {
        ulong d=HistoryDealGetTicket(i); long type=HistoryDealGetInteger(d,DEAL_TYPE);
        if(type!=DEAL_TYPE_BUY&&type!=DEAL_TYPE_SELL) continue;
        long entry=HistoryDealGetInteger(d,DEAL_ENTRY); double volume=HistoryDealGetDouble(d,DEAL_VOLUME);
        if(entry==DEAL_ENTRY_IN) {
            ulong order=(ulong)HistoryDealGetInteger(d,DEAL_ORDER);
            if(firstOrder==0) firstOrder=order;
            if(order!=firstOrder) supported=false; // 같은 netting 식별자에 증액된 별도 진입 주문
            initial+=volume;
        } else if(entry==DEAL_ENTRY_OUT||entry==DEAL_ENTRY_OUT_BY) exits+=volume;
        else supported=false; // INOUT 반전은 기존 티켓을 새 BUY로 복원하지 않는다.
    }
    if(firstOrder>0&&OrderSelect(firstOrder)) supported=false; // 원 진입 주문이 아직 체결 중
    return supported&&initial>0&&MathAbs(initial-exits-current)<=step*0.0001;
}
string M6RowText(const S6Row &r) {
    return StringFormat("%I64u",r.id)+","+StringFormat("%I64u",r.ticket)+","+S6HexSymbol(r.symbol)+","+r.side+","+
      DoubleToString(r.initialVolume,8)+","+DoubleToString(r.volume,8)+","+DoubleToString(r.price,8)+","+
      DoubleToString(r.sl,8)+","+DoubleToString(r.tp,8)+","+DoubleToString(r.step,8)+","+
      StringFormat("%I64u",r.entrySeq)+","+StringFormat("%I64u",r.lastSeq)+","+
      DoubleToString(r.copyPrice,8)+","+DoubleToString(r.copyDistance,8)+","+IntegerToString(r.status);
}
bool M6Collect(S6Row &rows[], string &reason) {
    ArrayResize(rows,0); reason="OK";
    for(int i=0;i<PositionsTotal();i++) {
        ulong ticket=PositionGetTicket(i); if(ticket==0) { reason="CHANGING"; return false; }
        if(!Inp_TrackAllPositions&&(ulong)PositionGetInteger(POSITION_MAGIC)!=Inp_MasterMagic) continue;
        S6Row r; r.id=(ulong)PositionGetInteger(POSITION_IDENTIFIER); r.ticket=ticket;
        r.symbol=PositionGetString(POSITION_SYMBOL); r.side=PositionGetInteger(POSITION_TYPE)==POSITION_TYPE_BUY?"BUY":"SELL";
        r.volume=PositionGetDouble(POSITION_VOLUME); r.price=PositionGetDouble(POSITION_PRICE_OPEN);
        r.sl=PositionGetDouble(POSITION_SL); r.tp=PositionGetDouble(POSITION_TP); r.step=SymbolInfoDouble(r.symbol,SYMBOL_VOLUME_STEP);
        r.entrySeq=0; r.lastSeq=0; r.copyPrice=0; r.copyDistance=0; r.status=3;
        int n=M6Index(r.id);
        if(n>=0) {
            r.entrySeq=m6_records[n].entrySeq; r.lastSeq=m6_records[n].lastSeq;
            r.copyPrice=m6_records[n].price; r.copyDistance=m6_records[n].distance;
            r.status=r.entrySeq>0?2:(m6_records[n].baseline?0:1);
        }
        if(r.step<=0||r.volume<=0||r.price<=0||S6HexSymbol(r.symbol)=="") { reason="HISTORY_UNAVAILABLE"; return false; }
        if(!M6Initial(r.id,r.volume,r.step,r.initialVolume)) { reason="UNSUPPORTED"; return false; }
        if(r.status==3) reason="PENDING_EVENTS";
        if(n>=0&&r.status==2) {
            double point=SymbolInfoDouble(r.symbol,SYMBOL_POINT);
            if(m6_records[n].symbol!=r.symbol||m6_records[n].side!=r.side||
               MathAbs(m6_records[n].sl-r.sl)>point*0.1||MathAbs(m6_records[n].tp-r.tp)>point*0.1||
               (m6_records[n].remaining>=0&&MathAbs(m6_records[n].remaining-r.volume)>r.step*0.0001)) reason="SIGNAL_STATE_MISMATCH";
        }
        int size=ArraySize(rows);
        if(size>=1024||ArrayResize(rows,size+1)!=size+1) { reason="HISTORY_UNAVAILABLE"; return false; }
        rows[size]=r;
    }
    // 목록 순서는 브로커의 열거 순서와 분리한다. 중복 식별자는 게시하지 않는다.
    for(int i=1;i<ArraySize(rows);i++) {
        S6Row r=rows[i]; int j=i-1;
        while(j>=0&&rows[j].id>r.id) { rows[j+1]=rows[j]; j--; }
        rows[j+1]=r;
    }
    for(int i=1;i<ArraySize(rows);i++) if(rows[i-1].id==rows[i].id) { reason="UNSUPPORTED"; return false; }
    for(int i=0;i<ArraySize(m6_records);i++) {
        if(m6_records[i].entrySeq==0) continue;
        bool active=false;
        for(int j=0;j<ArraySize(rows);j++) if(rows[j].id==m6_records[i].id) active=true;
        if(!active&&m6_records[i].remaining!=0) reason="SIGNAL_STATE_MISMATCH";
    }
    return true;
}

// R8: 개별 거래 이벤트량과 처리 순간 잔량을 섞지 않는다.
// 동일 포지션의 연속 OUT 알림은 타이머에서 확정 이력/현재 잔량을 대조한 한 번의 감소량으로 합친다.
struct M8CloseUpdate { ulong id; string symbol; double lastRemaining; bool dirty; ulong warned; };
M8CloseUpdate m8_closes[];
int m8_closeScan=0;
void M8QueueClose(ulong id,string symbol) {
    for(int i=0;i<ArraySize(m8_closes);i++) if(m8_closes[i].id==id) { m8_closes[i].dirty=true; return; }
    int n=ArraySize(m8_closes);
    if(n>=65536||ArrayResize(m8_closes,n+1)!=n+1) { V2Fault("CLOSE_LEDGER_CAPACITY"); return; }
    m8_closes[n].id=id; m8_closes[n].symbol=symbol; m8_closes[n].lastRemaining=-1;
    m8_closes[n].dirty=true; m8_closes[n].warned=0;
}
bool M8ClosesPending() {
    for(int i=0;i<ArraySize(m8_closes);i++) if(m8_closes[i].dirty) return true;
    return false;
}
void M8ProcessCloses() {
    int size=ArraySize(m8_closes),processed=0;
    for(int pass=0;pass<size&&processed<16&&!v2_faulted;pass++) {
        if(m8_closeScan>=size) m8_closeScan=0;
        int n=m8_closeScan++;
        if(!m8_closes[n].dirty) continue;
        processed++;
        ulong id=m8_closes[n].id;
        string symbol=m8_closes[n].symbol;
        double current=0,initial=0,step=SymbolInfoDouble(symbol,SYMBOL_VOLUME_STEP);
        if(M6Select(id)) current=PositionGetDouble(POSITION_VOLUME);
        // 추가 체결 중이거나 이력과 현재 포지션이 어긋나면 다음 타이머에서 재확인한다.
        if(step<=0||!M6Initial(id,current,step,initial)) {
            ulong now=GetTickCount64();
            if(now-m8_closes[n].warned>=5000) {
                m8_closes[n].warned=now;
                Print("[CLOSE_HISTORY_DEFERRED] PositionId=",id," | current/history not yet consistent");
            }
            continue;
        }
        double before=m8_closes[n].lastRemaining<0?initial:m8_closes[n].lastRemaining;
        if(current>before+step*0.0001) continue; // 지원하지 않는 증액을 청산으로 오인하지 않는다.
        double closed=NormalizeDouble(before-current,8);
        if(closed<=step*0.0001) { m8_closes[n].dirty=false; continue; }
        string msg="ACTION=CLOSE|SYMBOL="+symbol+"|TICKET="+StringFormat("%I64u",id)+
                   "|TARGET_VOL="+DoubleToString(current,8)+"|CLOSED_VOL="+DoubleToString(closed,8);
        AddToQueue(msg);
        if(!v2_faulted) {
            m8_closes[n].lastRemaining=current; m8_closes[n].dirty=false;
        }
    }
}

string M6BuildSnapshot() {
    ulong revision=m6_revision, cut=msgSequence; S6Row a[],b[]; string ra="OK",rb="OK";
    bool historyA=M6AuditHistory(), first=M6Collect(a,ra);
    bool second=M6Collect(b,rb), historyB=M6AuditHistory();
    string one="",two="";
    for(int i=0;i<ArraySize(a);i++) { if(i>0) one+=";"; one+=M6RowText(a[i]); }
    for(int i=0;i<ArraySize(b);i++) { if(i>0) two+=";"; two+=M6RowText(b[i]); }
    string reason="OK";
    if(!historyA||!historyB||M8ClosesPending()) reason="PENDING_EVENTS";
    else if(!first||!second) reason=ra!="OK"?ra:rb;
    else if(one!=two||revision!=m6_revision||cut!=msgSequence||v2_queueCount!=0) reason="CHANGING";
    else if(ra!="OK"||rb!="OK") reason=ra!="OK"?ra:rb;
    // 불완전 목록은 빈 계좌로 해석되지 않도록 CONSISTENT=0으로 명시한다.
    if(reason!="OK") { ArrayResize(b,0); two=""; }
    return "ACTION=SNAPSHOT|SCHEMA=1|CUT="+StringFormat("%I64u",cut)+"|REV="+StringFormat("%I64u",revision)+
       "|CONSISTENT="+(reason=="OK"?"1":"0")+"|REASON="+reason+"|COUNT="+IntegerToString(ArraySize(b))+"|ROWS="+(two==""?"NONE":two);
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

// R12: chart-only UI. Trade MMF v2 is unchanged; ACK work runs after trade service.
input group "==== [Recovery 확인] ===="
input string Inp_ExpectedReceivers=""; // MT5:login@server only. Ninja membership is declared by its Use checkboxes.
input int Inp_RecoveryX=15;
input int Inp_RecoveryY=30;
long c11_map=0,c11_view=0,c11_mutex=0;
bool c11_requestPending=false;
ulong c11_request=0,c11_requestTick=0,c11_serviceTick=0,c12_lastReadTick=0;
int c12_lastMt5Total=0,c12_lastNinjaTotal=0;
uchar c11_slots[C11_BYTES-C11_HEAD];
string c11_roster[],c11_lastResult[];
string c11_button="OZ_R11_Recovery",c11_label="OZ_R11_Status";
string c12_ninjaLabel="OZ_R12_Ninja",c12_mt5Text="",c12_ninjaText="",c12_tooltip="";
color c12_mt5Color=clrNONE,c12_ninjaColor=clrNONE;
string c12_logKeys[],c12_logValues[];
struct C12Receiver {
    string key,reason;
    uint platform,version,state;
    ulong tick,membershipTick;
    bool enabled,ack,source,entry;
};
int C11RosterIndex(string key) { for(int i=0;i<ArraySize(c11_roster);i++) if(c11_roster[i]==key) return i; return -1; }
void C11AddRoster(string key) {
    // Do not retain old NINJA receiver names in the denominator.
    if(StringFind(key,"MT5:")!=0||C11RosterIndex(key)>=0||ArraySize(c11_roster)>=64) return;
    int n=ArraySize(c11_roster);ArrayResize(c11_roster,n+1);ArrayResize(c11_lastResult,n+1);
    c11_roster[n]=key;c11_lastResult[n]="";
}
void C11RemoveRoster(string key) {
    int n=C11RosterIndex(key);if(n<0)return;
    int size=ArraySize(c11_roster);
    for(int i=n;i<size-1;i++){c11_roster[i]=c11_roster[i+1];c11_lastResult[i]=c11_lastResult[i+1];}
    ArrayResize(c11_roster,size-1);ArrayResize(c11_lastResult,size-1);
}
void C12SetLabel(string name,string text,color value,string &oldText,color &oldColor) {
    if(text!=oldText) { ObjectSetString(0,name,OBJPROP_TEXT,text);oldText=text; }
    if(value!=oldColor) { ObjectSetInteger(0,name,OBJPROP_COLOR,value);oldColor=value; }
}
void C11UIStart() {
    ArrayResize(c11_roster,0);ArrayResize(c11_lastResult,0);
    if(Inp_ExpectedReceivers!="") {
        string names[];StringSplit(Inp_ExpectedReceivers,';',names);
        for(int i=0;i<ArraySize(names);i++) { StringTrimLeft(names[i]);StringTrimRight(names[i]);C11AddRoster(names[i]); }
    }
    ObjectDelete(0,c11_button);ObjectDelete(0,c11_label);ObjectDelete(0,c12_ninjaLabel);
    if(!ObjectCreate(0,c11_button,OBJ_BUTTON,0,0,0)) { Print("[RECOVERY_UI_FAILED] ",GetLastError());return; }
    ObjectSetInteger(0,c11_button,OBJPROP_CORNER,CORNER_LEFT_UPPER);
    ObjectSetInteger(0,c11_button,OBJPROP_XDISTANCE,Inp_RecoveryX);ObjectSetInteger(0,c11_button,OBJPROP_YDISTANCE,Inp_RecoveryY);
    ObjectSetInteger(0,c11_button,OBJPROP_XSIZE,130);ObjectSetInteger(0,c11_button,OBJPROP_YSIZE,34);
    ObjectSetInteger(0,c11_button,OBJPROP_BGCOLOR,clrRed);ObjectSetInteger(0,c11_button,OBJPROP_COLOR,clrWhite);
    ObjectSetInteger(0,c11_button,OBJPROP_FONTSIZE,12);ObjectSetInteger(0,c11_button,OBJPROP_ZORDER,100);
    ObjectSetInteger(0,c11_button,OBJPROP_SELECTABLE,false);ObjectSetString(0,c11_button,OBJPROP_TEXT,"Recovery");
    string labels[2];labels[0]=c11_label;labels[1]=c12_ninjaLabel;
    for(int i=0;i<2;i++) {
        ObjectCreate(0,labels[i],OBJ_LABEL,0,0,0);
        ObjectSetInteger(0,labels[i],OBJPROP_CORNER,CORNER_LEFT_UPPER);
        ObjectSetInteger(0,labels[i],OBJPROP_XDISTANCE,Inp_RecoveryX);
        ObjectSetInteger(0,labels[i],OBJPROP_YDISTANCE,Inp_RecoveryY+44+i*24);
        ObjectSetInteger(0,labels[i],OBJPROP_FONTSIZE,11);
        ObjectSetInteger(0,labels[i],OBJPROP_SELECTABLE,false);
    }
    C12SetLabel(c11_label,StringFormat("MT5 Slave   0 / %d",ArraySize(c11_roster)),clrSilver,c12_mt5Text,c12_mt5Color);
    C12SetLabel(c12_ninjaLabel,"Ninja Slave 0 / 0",clrSilver,c12_ninjaText,c12_ninjaColor);
    ChartRedraw();
}
void C11Close() {
    if(c11_view!=0) { UnmapViewOfFile(c11_view);c11_view=0; }
    if(c11_map!=0) { CloseHandle(c11_map);c11_map=0; }
    if(c11_mutex!=0) { CloseHandle(c11_mutex);c11_mutex=0; }
}
bool C11Open() {
    if(c11_view!=0&&c11_mutex!=0) return true;
    string root="Local\\OZCopy.Control11."+Inp_StreamId;
    c11_mutex=CreateMutexW(0,0,root+".Lock");c11_map=CreateFileMappingW(-1,0,4,0,C11_BYTES,root+".State");
    if(c11_map!=0) c11_view=MapViewOfFile(c11_map,0xF001F,0,0,C11_BYTES);
    if(c11_mutex==0||c11_view==0) { C11Close();return false; }return true;
}
// Pure predicates are shared by display and regression tests. Offline ON accounts stay in the denominator.
bool C12Target(const C12Receiver &r) { return r.platform==1||(r.platform==2&&r.version==2&&r.enabled); }
bool C12Linked(const C12Receiver &r,bool publisherReady) { return publisherReady&&r.ack&&r.source&&(r.state==1||r.state==4); }
int C12Find(C12Receiver &rows[],string key) { for(int i=0;i<ArraySize(rows);i++)if(rows[i].key==key)return i;return -1; }
void C12LogResult(string key,string result) {
    int n=-1;for(int i=0;i<ArraySize(c12_logKeys);i++)if(c12_logKeys[i]==key){n=i;break;}
    if(n<0) { n=ArraySize(c12_logKeys);if(n>=64)return;ArrayResize(c12_logKeys,n+1);ArrayResize(c12_logValues,n+1);c12_logKeys[n]=key; }
    if(c12_logValues[n]==result)return;c12_logValues[n]=result;
    if(result!="WAITING")C11Burst("RECOVERY RESULT",key+" | "+result,StringFormat("Request=%I64u",c11_request));
}
string C12Result(const C12Receiver &r,bool link) {
    if(!r.ack)return "NO RESPONSE";
    if(link)return r.entry?"CONNECTED / READY":"CONNECTED / "+r.reason;
    return (r.state==3?"HOLD / ":"DISCONNECTED / ")+r.reason;
}
void C11Service() {
    // All callers are below the trade work. Busy outbox means monitoring yields, not the reverse.
    if(v2_queueCount>0&&!v2_faulted)return;
    ulong now=GetTickCount64();if(c11_serviceTick!=0&&now-c11_serviceTick<250)return;c11_serviceTick=now;
    if(c12_lastReadTick>0&&!C11Fresh(now,c12_lastReadTick)) {
        C12SetLabel(c11_label,StringFormat("MT5 Slave   0 / %d",c12_lastMt5Total),clrOrange,c12_mt5Text,c12_mt5Color);
        C12SetLabel(c12_ninjaLabel,StringFormat("Ninja Slave 0 / %d",c12_lastNinjaTotal),clrOrange,c12_ninjaText,c12_ninjaColor);
    }
    if(!C11Open())return;
    uchar next[256];ArrayInitialize(next,0);
    C11Put32(next,0,C11_MAGIC);C11Put32(next,4,1);C11Put32(next,8,C11_BYTES);
    C11Put32(next,12,256);C11Put32(next,16,512);C11Put32(next,20,64);C11Put64(next,24,v2_sourceLogin);
    ArrayCopy(next,v2_session,32,0,16);
    bool send=c11_requestPending;
    ulong request=c11_request+(send?1:0),requestTick=send?now:c11_requestTick;
    C11Put64(next,48,request);C11Put64(next,56,requestTick);C11Put64(next,64,msgSequence);
    if(!C11Text(next,80,96,72,v2_sourceServer))return;
    C11Put64(next,176,now);C11Put32(next,184,v2_faulted?2:(v2_ready?1:0));C11Put32(next,76,C11CRC(next,256,76));
    uint rc=WaitForSingleObject(c11_mutex,0);if(rc!=0&&rc!=0x80)return;
    RtlMoveMemory(c11_view,next,(ulong)256);RtlMoveMemory(c11_slots,c11_view+256,(ulong)(C11_BYTES-256));
    if(ReleaseMutex(c11_mutex)==0){C11Close();Print("[RECOVERY_CHANNEL_RELEASE_FAILED]");return;}
    if(send) {
        c11_request=request;c11_requestTick=requestTick;c11_requestPending=false;
        ArrayResize(c12_logKeys,0);ArrayResize(c12_logValues,0);
        C11Burst("RECOVERY CHECK",StringFormat("REQUEST #%I64u",request));
    }
    C12Receiver receivers[];
    for(int i=0;i<64;i++) {
        uchar a[512];ArrayCopy(a,c11_slots,0,i*512,512);if(!C11AckValid(a))continue;
        C12Receiver r;r.platform=C11U32(a,8);r.version=C11U32(a,4);r.state=C11U32(a,12);
        // Old Ninja ACKs cannot declare checkbox membership; never guess it from READY.
        if(r.platform==2&&r.version!=2)continue;
        r.key=(r.platform==1?"MT5:":"NINJA:")+C11String(a,96,72)+"@"+C11String(a,192,76);
        r.tick=C11U64(a,32);r.membershipTick=r.platform==2?C11U64(a,448):r.tick;
        r.enabled=r.platform==1||C11U32(a,444)==1;r.reason=C11String(a,288,80);
        r.ack=C11Fresh(now,r.tick)&&(c11_request==0||(C11Same(a,40,v2_session,0,16)&&C11U64(a,24)==c11_request&&r.tick>=c11_requestTick));
        r.source=C11U64(a,88)==v2_sourceLogin&&C11Same(a,416,v2_session,0,16);r.entry=C11U32(a,440)==1;
        int n=C12Find(receivers,r.key);
        if(n<0){n=ArraySize(receivers);ArrayResize(receivers,n+1);receivers[n]=r;}
        else if(r.membershipTick>receivers[n].membershipTick||(r.membershipTick==receivers[n].membershipTick&&r.tick>receivers[n].tick))receivers[n]=r;
        if(r.platform==1&&Inp_ExpectedReceivers==""&&r.source&&C11Fresh(now,r.tick)) {
            if(r.state==6)C11RemoveRoster(r.key);else C11AddRoster(r.key);
        }
    }
    bool publisher=v2_ready&&!v2_faulted;
    int mt5Total=ArraySize(c11_roster),mt5Normal=0,ninjaTotal=0,ninjaNormal=0;
    string details="Counts = current connection / configured targets. Ninja: Use ON only.\n";
    for(int i=0;i<mt5Total;i++) {
        int n=C12Find(receivers,c11_roster[i]);string result="NO RESPONSE";
        if(n>=0){bool link=C12Linked(receivers[n],publisher);if(link)mt5Normal++;result=C12Result(receivers[n],link);}
        if(c11_request>0){if(result=="NO RESPONSE"&&now-c11_requestTick<=5000)result="WAITING";C12LogResult(c11_roster[i],result);}
        details+=c11_roster[i]+" : "+result+"\n";
    }
    for(int i=0;i<ArraySize(receivers);i++) {
        if(receivers[i].platform!=2||!C12Target(receivers[i]))continue;
        ninjaTotal++;bool link=C12Linked(receivers[i],publisher);if(link)ninjaNormal++;
        string result=C12Result(receivers[i],link);
        if(c11_request>0){if(result=="NO RESPONSE"&&now-c11_requestTick<=5000)result="WAITING";C12LogResult(receivers[i].key,result);}
        details+=receivers[i].key+" : "+result+"\n";
    }
    c12_lastReadTick=now;c12_lastMt5Total=mt5Total;c12_lastNinjaTotal=ninjaTotal;
    C12SetLabel(c11_label,StringFormat("MT5 Slave   %d / %d",mt5Normal,mt5Total),mt5Total==0?clrSilver:(mt5Normal==mt5Total?clrLime:clrOrange),c12_mt5Text,c12_mt5Color);
    C12SetLabel(c12_ninjaLabel,StringFormat("Ninja Slave %d / %d",ninjaNormal,ninjaTotal),ninjaTotal==0?clrSilver:(ninjaNormal==ninjaTotal?clrLime:clrOrange),c12_ninjaText,c12_ninjaColor);
    if(details!=c12_tooltip){ObjectSetString(0,c11_button,OBJPROP_TOOLTIP,details);c12_tooltip=details;}
}
void OnChartEvent(const int id,const long &lparam,const double &dparam,const string &sparam) {
    if(id!=CHARTEVENT_OBJECT_CLICK||sparam!=c11_button)return;
    ObjectSetInteger(0,c11_button,OBJPROP_STATE,false);
    if(c11_requestPending)return;
    c11_requestPending=true;c11_serviceTick=0; // request only; no order/sequence/reset or blocking waits
}

int OnInit() {
    if(!CheckExpiry()) return(INIT_FAILED);
    if(AccountInfoInteger(ACCOUNT_MARGIN_MODE)!=ACCOUNT_MARGIN_MODE_RETAIL_HEDGING) {
        Print("[INIT_UNSUPPORTED_ACCOUNT_MODE] 이 Master는 티켓별 hedging 복사 전용입니다. netting 증액/반전을 조용히 누락하지 않습니다.");
        return INIT_FAILED;
    }
    if(!M6Start()) { Print("[SNAPSHOT INIT] History baseline unavailable"); return INIT_FAILED; }
    if(!V2Initialize()) return(INIT_FAILED);
    if(!EventSetMillisecondTimer(50)) {
        V2Fault("TIMER_START_FAILED");
        V2ServiceFault();
        V2Cleanup();
        return(INIT_FAILED);
    }
    C11UIStart();
    Print("▶ [통합 마스터 4단계] MMF v2.0 링버퍼 활성화. v2 수신기와 함께 사용합니다.");
    return(INIT_SUCCEEDED);
}

void OnDeinit(const int reason) {
    EventKillTimer(); C11Close(); ObjectDelete(0,c11_button); ObjectDelete(0,c11_label); ObjectDelete(0,c12_ninjaLabel);
    if(v2_sessionInstalled && v2_publisherOwned) {
        if(v2_faulted) V2ServiceFault();
        else V2TryStatus(V2_STOPPED);
    }
    V2Cleanup();
    Print("■ [통합 마스터 4단계] 전송 종료 | 미발행 큐=", v2_queueCount,
          " | 마지막 확인 순번=", msgSequence, " | Fault=", v2_faultReason);
}

void OnTimer() {
    if(v2_faulted) { V2ServiceFault(); C11Service(); return; }
    if(!v2_ready || !V2CheckSource()) { V2ServiceFault(); C11Service(); return; }
    CheckPendingTrades();
    if(v2_faulted) { V2ServiceFault(); C11Service(); return; }
    M8ProcessCloses();
    if(v2_faulted) { V2ServiceFault(); C11Service(); return; }
    TrackModifications();
    ProcessQueue();
    SendHeartbeatSync();
    V2IdleHeartbeat();
    V2ServiceFault();
    C11Service();
}

void AddToQueue(string msg) {
    if(v2_faulted || !v2_ready) {
        Print("[MMF v2 발행 보류] ", v2_faultReason, " | RAW=", msg);
        return;
    }
    if(!V2CheckSource()) { V2ServiceFault(); return; }
    string error;
    uchar encoded[];
    int length;
    if(!V2ValidateCommand(msg, error) || !V2Encode(msg, V2_COMMAND_BYTES, encoded, length, error)) {
        V2Fault(error, msg); V2ServiceFault(); return;
    }
    if(v2_queueCount >= V2_CAPACITY) {
        V2Fault("OUTBOX_FULL", msg); V2ServiceFault(); return;
    }
    int index = (v2_queueHead + v2_queueCount) % V2_CAPACITY;
    signalQueue[index].message = msg;
    signalQueue[index].length = length;
    signalQueue[index].payloadCrc = V2Crc32(encoded, 0, length);
    ArrayInitialize(signalQueue[index].payload, 0);
    ArrayCopy(signalQueue[index].payload, encoded, 0, 0, length);
    v2_queueCount++;
    // 생성 직후 즉시 시도한다. mutex busy이면 큐는 그대로 두고 타이머에서 재시도한다.
    ProcessQueue();
}

void ProcessQueue() {
    if(!v2_ready || v2_faulted || v2_publishing || v2_queueCount == 0) return;
    if(!V2CheckSource()) return;
    int count = (int)MathMin(v2_queueCount, V2_BATCH_MAX);
    // 같은 세션 순번을 순환시키지 않는다. 상한 전 정지 후 EA 재초기화로 새 세션을 만든다.
    if(msgSequence >= (ulong)V2_SEQ_MAX - (ulong)count) {
        V2Fault("SEQUENCE_LIMIT_REINITIALIZE_NEW_SESSION"); return;
    }
    v2_publishing = true;
    ulong now = GetTickCount64();
    ulong before = msgSequence;
    // 배열/CRC/본문 준비는 잠금 전에 끝낸다. 한 잠금 구간에서 최대 16개를 기록한다.
    for(int i = 0; i < count; i++) {
        int index = (v2_queueHead + i) % V2_CAPACITY;
        ulong sequence = before + (ulong)i + 1;
        v2_batch[i].sequence = sequence;
        V2BuildSlot(signalQueue[index].payload, signalQueue[index].length, signalQueue[index].payloadCrc,
                    sequence, now, v2_batch[i].slot);
        V2BuildHeader(sequence, now, V2_READY, v2_batch[i].header);
    }
    int acquired = V2TryCopyLock();
    if(acquired == 0) { v2_publishing = false; return; }
    if(acquired < 0) { v2_publishing = false; V2Fault("PUBLISH_WAIT_FAILED"); return; }

    RtlMoveMemory(v2_readHeader, pBuf, (ulong)V2_HEADER_BYTES);
    bool valid = V2HeaderMatches(v2_readHeader, before, V2_READY);
    int confirmed = 0;
    if(valid) {
        for(int i = 0; i < count; i++) {
            ulong sequence = v2_batch[i].sequence;
            long offset = V2_HEADER_BYTES + (long)((sequence - 1) % V2_CAPACITY) * V2_SLOT_BYTES;
            RtlMoveMemory(pBuf + offset, v2_batch[i].slot, (ulong)V2_SLOT_BYTES); // WRITING + 전체 본문
            RtlMoveMemory(pBuf + offset, v2_committed, (ulong)4);               // COMMITTED
            RtlMoveMemory(pBuf, v2_batch[i].header, (ulong)V2_HEADER_BYTES);    // PublishedSeq 마지막

            // 결과가 불분명한 머리 항목을 새 순번으로 보내지 않도록 동일 슬롯/헤더를 대조한다.
            RtlMoveMemory(v2_readHeader, pBuf, (ulong)V2_HEADER_BYTES);
            RtlMoveMemory(v2_readSlot, pBuf + offset, (ulong)V2_SLOT_BYTES);
            valid = V2EqualBytes(v2_readHeader, 0, v2_batch[i].header, 0, V2_HEADER_BYTES) &&
                    V2Get32(v2_readSlot, 0) == 2 &&
                    V2EqualBytes(v2_readSlot, 4, v2_batch[i].slot, 4, V2_SLOT_BYTES - 4);
            if(!valid) break;
            confirmed++;
            msgSequence = sequence;
        }
    }
    bool released = V2LeaveCopyLock();
    // 로그/문자열 정리/큐 제거는 잠금 밖. 검증된 연속 구간만 제거한다.
    for(int i = 0; i < confirmed; i++) {
        int index = v2_queueHead;
        Print("[MMF v2 PUBLISHED] Stream=", Inp_StreamId, " | SourceLogin=", v2_sourceLogin,
              " | Session=", v2_sessionText, " | Sequence=", v2_batch[i].sequence,
              " | RAW=", signalQueue[index].message);
        M6Published(signalQueue[index].message,v2_batch[i].sequence);
        signalQueue[index].message = "";
        v2_queueHead = (v2_queueHead + 1) % V2_CAPACITY;
        v2_queueCount--;
    }
    if(confirmed > 0) v2_lastAliveTick = now;
    v2_publishing = false;
    if(!valid || !released) {
        string raw = v2_queueCount > 0 ? signalQueue[v2_queueHead].message : "";
        V2Fault("PUBLISH_IDENTITY_READBACK_OR_RELEASE_FAILED", raw);
        V2ServiceFault();
    }
}

// 기존 SendHeartbeatSync의 포지션 필터·티켓·SL/TP 생성 방식을 그대로 분리했다.
string V2BuildLegacySync() {
    string activeTickets = "";
    for(int i = 0; i < PositionsTotal(); i++) {
        ulong t = PositionGetTicket(i);
        if(PositionSelectByTicket(t)) {
            if(!Inp_TrackAllPositions && PositionGetInteger(POSITION_MAGIC) != Inp_MasterMagic) continue;
            double currentSL = PositionGetDouble(POSITION_SL);
            double currentTP = PositionGetDouble(POSITION_TP);
            string sym = PositionGetString(POSITION_SYMBOL);
            int digits = (int)SymbolInfoInteger(sym, SYMBOL_DIGITS);
            if(activeTickets != "") activeTickets += ",";
            activeTickets += IntegerToString(t) + ":" + DoubleToString(currentSL, digits) + ":" + DoubleToString(currentTP, digits);
        }
    }
    if(activeTickets == "") activeTickets = "NONE";
    return "ACTION=SYNC|TICKETS=" + activeTickets;
}

void SendHeartbeatSync() {
    if(!v2_ready || v2_faulted || v2_queueCount != 0) return;
    ulong now = GetTickCount64();
    if(now - v2_lastSyncTick < 2000) return;
    if(!V2CheckSource()) return;
    string msg = M6BuildSnapshot();
    string error;
    if(!V2PrepareState(msg, now, error)) { V2Fault(error, msg); return; }
    uchar nextHeader[V2_HEADER_BYTES];
    V2BuildHeader(msgSequence, now, V2_READY, nextHeader);
    int acquired = V2TryCopyLock();
    if(acquired == 0) return;
    if(acquired < 0) { V2Fault("SYNC_WAIT_FAILED"); return; }
    RtlMoveMemory(v2_readHeader, pBuf, (ulong)V2_HEADER_BYTES);
    bool valid = V2HeaderMatches(v2_readHeader, msgSequence, V2_READY);
    if(valid) {
        RtlMoveMemory(v2_stateView + V2_STATE_HEADER_BYTES, v2_stateBody, (ulong)V2_STATE_PAYLOAD_BYTES);
        RtlMoveMemory(v2_stateView, v2_stateHeader, (ulong)V2_STATE_HEADER_BYTES);
        RtlMoveMemory(v2_stateRead, v2_stateView, (ulong)V2_STATE_BYTES);
        RtlMoveMemory(pBuf, nextHeader, (ulong)V2_HEADER_BYTES);
        RtlMoveMemory(v2_readHeader, pBuf, (ulong)V2_HEADER_BYTES);
        valid = V2EqualBytes(v2_readHeader, 0, nextHeader, 0, V2_HEADER_BYTES);
    }
    bool released = V2LeaveCopyLock();
    // State의 큰 본문 CRC/검증은 복사 잠금 해제 후 수행한다.
    if(!valid || !released || !V2StateReadbackMatches()) { V2Fault("SYNC_READBACK_OR_RELEASE_FAILED", msg); return; }
    v2_stateVersion++;
    v2_lastSyncTick = now;
    v2_lastAliveTick = now;
}

void CheckPendingTrades() {
    int remaining = 0;
    for(int i = 0; i < ArraySize(pendingTrades); i++) {
        if(M6Select(pendingTrades[i].ticket)) {
            double sl = PositionGetDouble(POSITION_SL);
            double tp = PositionGetDouble(POSITION_TP);
            
            if(sl > 0) {
                // 진입가와 SL의 거리(Distance) 계산 추가
                double sl_dist = NormalizeDouble(MathAbs(pendingTrades[i].entryPrice - sl), pendingTrades[i].digits);
                // R8: 복사 불가한 개별 진입은 보류한다. 전역 MMF FAULT를 만들지 않는다.
                if(!MathIsValidNumber(sl_dist)||sl_dist<=0.0) {
                    pendingTrades[remaining++]=pendingTrades[i];
                    continue;
                }

                // [TRACE ONLY] SL 누락 대기 후 실제 확인값과 계산 결과
                PrintFormat("🔎 [TRACE/PENDING RESOLVED] Ticket=%llu | Action=%s | Symbol=%s | SavedEntry=%.*f | CurrentSL=%.*f | CurrentTP=%.*f | SL_DIST=%.*f",
                            pendingTrades[i].ticket, pendingTrades[i].action, pendingTrades[i].sym,
                            pendingTrades[i].digits, pendingTrades[i].entryPrice,
                            pendingTrades[i].digits, sl, pendingTrades[i].digits, tp,
                            pendingTrades[i].digits, sl_dist);
                
                string msg = "ACTION=" + pendingTrades[i].action + 
                             "|SYMBOL=" + pendingTrades[i].sym + 
                             "|PRICE=" + DoubleToString(pendingTrades[i].entryPrice, pendingTrades[i].digits) + 
                             "|SL=" + DoubleToString(sl, pendingTrades[i].digits) + 
                             "|TP=" + DoubleToString(tp, pendingTrades[i].digits) +
                             "|SL_DIST=" + DoubleToString(sl_dist, pendingTrades[i].digits) +
                             "|TICKET=" + IntegerToString(pendingTrades[i].ticket);
                AddToQueue(msg);

                bool cached = false;
                for(int j = 0; j < ArraySize(m_cache); j++) {
                    if(m_cache[j].ticket == pendingTrades[i].ticket) {
                        m_cache[j].sl = sl; m_cache[j].tp = tp; cached = true; break;
                    }
                }
                if(!cached) {
                    int size = ArraySize(m_cache);
                    ArrayResize(m_cache, size + 1);
                    m_cache[size].ticket = pendingTrades[i].ticket;
                    m_cache[size].sl = sl;
                    m_cache[size].tp = tp;
                }
            } else {
                pendingTrades[remaining] = pendingTrades[i];
                remaining++;
            }
        }
    }
    ArrayResize(pendingTrades, remaining);
}

void TrackModifications() {
    int activeCount = 0;
    for(int i = 0; i < PositionsTotal(); i++) {
        ulong currentTicket = PositionGetTicket(i);
        if(PositionSelectByTicket(currentTicket)) {
            ulong t=(ulong)PositionGetInteger(POSITION_IDENTIFIER);
            if(!Inp_TrackAllPositions && PositionGetInteger(POSITION_MAGIC) != Inp_MasterMagic) continue;
            
            double sl = PositionGetDouble(POSITION_SL);
            double tp = PositionGetDouble(POSITION_TP);
            double entryPrice = PositionGetDouble(POSITION_PRICE_OPEN);
            string sym = PositionGetString(POSITION_SYMBOL);
            int digits = (int)SymbolInfoInteger(sym, SYMBOL_DIGITS);
            
            bool found = false;
            for(int j = 0; j < ArraySize(m_cache); j++) {
                if(m_cache[j].ticket == t) {
                    found = true;
                    if(m_cache[j].sl != sl || m_cache[j].tp != tp) {
                        m_cache[j].sl = sl;
                        m_cache[j].tp = tp;
                        
                        // 수정 발생 시에도 거리(Distance) 계산 추가
                        double sl_dist = 0.0;
                        if(sl > 0) sl_dist = NormalizeDouble(MathAbs(entryPrice - sl), digits);

                        string msg = "ACTION=MODIFY|SYMBOL=" + sym + 
                                     "|TICKET=" + IntegerToString(t) + 
                                     "|SL=" + DoubleToString(sl, digits) +
                                     "|TP=" + DoubleToString(tp, digits) +
                                     "|SL_DIST=" + DoubleToString(sl_dist, digits);
                        AddToQueue(msg);
                    }
                    break;
                }
            }
            if(!found) {
                int size = ArraySize(m_cache);
                ArrayResize(m_cache, size + 1);
                m_cache[size].ticket = t; m_cache[size].sl = sl; m_cache[size].tp = tp;
            }
        }
    }
    
    for(int j = 0; j < ArraySize(m_cache); j++) {
        if(M6Select(m_cache[j].ticket)) m_cache[activeCount++] = m_cache[j];
    }
    ArrayResize(m_cache, activeCount);
}

void OnTradeTransaction(const MqlTradeTransaction& trans, const MqlTradeRequest& request, const MqlTradeResult& result) {
    if (trans.type == TRADE_TRANSACTION_DEAL_ADD) {
        if (HistoryDealSelect(trans.deal)) {
            M6Observe(trans.deal);
            long magic = HistoryDealGetInteger(trans.deal, DEAL_MAGIC);
            if(!Inp_TrackAllPositions && magic != Inp_MasterMagic && M6Index((ulong)HistoryDealGetInteger(trans.deal,DEAL_POSITION_ID))<0) return;
            
            long deal_entry = HistoryDealGetInteger(trans.deal, DEAL_ENTRY);
            long pos_id = HistoryDealGetInteger(trans.deal, DEAL_POSITION_ID); 
            int digits = (int)SymbolInfoInteger(trans.symbol, SYMBOL_DIGITS);
            
            if (deal_entry == DEAL_ENTRY_IN) {
                // 분할 체결 시 동일 pos_id에 대한 중복 신호 발송 차단 
                bool isDuplicate = false;
                for(int k=0; k<ArraySize(processedEntries); k++) {
                    if(processedEntries[k] == pos_id) { isDuplicate = true; break; }
                }
                if(isDuplicate) return;
                
                int pSize = ArraySize(processedEntries);
                ArrayResize(processedEntries, pSize + 1);
                processedEntries[pSize] = pos_id;

                string action = (trans.deal_type == DEAL_TYPE_BUY) ? "BUY" : (trans.deal_type == DEAL_TYPE_SELL) ? "SELL" : "";
                if (action == "") return;
                
                double sl_price = trans.price_sl;
                double tp_price = trans.price_tp;

                // [TRACE ONLY] Deal 이벤트에서 들어온 원본 보호가격
                PrintFormat("🔎 [TRACE/ENTRY DEAL] Ticket=%llu | Deal=%llu | Action=%s | Symbol=%s | DealPrice=%.*f | trans.price_sl=%.*f | trans.price_tp=%.*f",
                            pos_id, trans.deal, action, trans.symbol, digits, trans.price, digits, sl_price, digits, tp_price);

                if ((sl_price == 0 || tp_price == 0) && M6Select((ulong)pos_id)) {
                    sl_price = PositionGetDouble(POSITION_SL);
                    tp_price = PositionGetDouble(POSITION_TP);
                    PrintFormat("🔎 [TRACE/ENTRY POSITION READ] Ticket=%llu | PositionSL=%.*f | PositionTP=%.*f",
                                pos_id, digits, sl_price, digits, tp_price);
                }
                
                if (!MathIsValidNumber(sl_price)||sl_price<=0.0||
                    NormalizeDouble(MathAbs(trans.price-sl_price),digits)<=0.0) {
                    // SL 누락 감지 시 대기열 추가 (이후 CheckPendingTrades에서 거리 계산 후 발송)
                    int size = ArraySize(pendingTrades);
                    ArrayResize(pendingTrades, size + 1);
                    pendingTrades[size].ticket = pos_id;
                    pendingTrades[size].action = action;
                    pendingTrades[size].sym = trans.symbol;
                    pendingTrades[size].entryPrice = trans.price;
                    pendingTrades[size].digits = digits;
                    pendingTrades[size].tp = tp_price;
                    PrintFormat("🔎 [TRACE/PENDING ADD] Ticket=%llu | Action=%s | Symbol=%s | SavedEntry=%.*f | SL=0 -> BUY/SELL 신호 전송 보류",
                                pos_id, action, trans.symbol, digits, trans.price);
                    return; 
                }
                
                // 진입가와 SL의 거리(Distance) 계산 추가
                double sl_dist = NormalizeDouble(MathAbs(trans.price - sl_price), digits);

                // [TRACE ONLY] 신규 BUY/SELL 신호 생성 직전 핵심 값
                PrintFormat("🔎 [TRACE/ENTRY BUILD] Ticket=%llu | Action=%s | Symbol=%s | Entry=%.*f | SL=%.*f | TP=%.*f | SL_DIST=%.*f",
                            pos_id, action, trans.symbol, digits, trans.price, digits, sl_price, digits, tp_price, digits, sl_dist);
                
                string msg = "ACTION=" + action + 
                             "|SYMBOL=" + trans.symbol + 
                             "|PRICE=" + DoubleToString(trans.price, digits) + 
                             "|SL=" + DoubleToString(sl_price, digits) + 
                             "|TP=" + DoubleToString(tp_price, digits) + 
                             "|SL_DIST=" + DoubleToString(sl_dist, digits) +
                             "|TICKET=" + IntegerToString(pos_id);
                AddToQueue(msg);
            } 
            else if (deal_entry == DEAL_ENTRY_OUT || deal_entry == DEAL_ENTRY_OUT_BY) {
                M8QueueClose((ulong)pos_id,trans.symbol);
            }
        }
    }
}