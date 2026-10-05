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
   string probe="__ds5_DivineShield_Notifier_"+IntegerToString(ChartID())+"_"+
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

// OZ Copy — Telegram notifier EA. Attach to a separate chart in the SAME MT5 terminal.
#property copyright "Oilve Oil"
#property version "1.70"
#property strict
input string Inp_StreamId="OZ_MAIN";
input ulong MagicNumber=777777;
#import "kernel32.dll"
long OpenFileMappingW(uint access,int inherit,string name);
long OpenMutexW(uint access,int inherit,string name);
long MapViewOfFile(long mapping,uint access,uint high,uint low,ulong bytes);
int UnmapViewOfFile(long address);
int CloseHandle(long handle);
uint WaitForSingleObject(long handle,uint milliseconds);
int ReleaseMutex(long handle);
long CreateSemaphoreW(long attributes,int initial,int maximum,string name);
int ReleaseSemaphore(long handle,int releaseCount,long previousCount);
void RtlMoveMemory(uchar &destination[],long source,ulong bytes);
void RtlMoveMemory(long destination,uchar &source[],ulong bytes);
#import
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

ulong n7_lastRequest=0;

bool N7OpenConsumer() {
    if(n7_view!=0) return true;
    n7_map=OpenFileMappingW(0x0006,0,n7_name+".Mailbox");
    if(n7_map==0) return false;
    n7_mutex=OpenMutexW(0x00100001,0,n7_name+".CopyLock");
    if(n7_mutex!=0) n7_view=MapViewOfFile(n7_map,0x0006,0,0,N7_SIZE);
    if(n7_view!=0) return true;
    CloseHandle(n7_map); n7_map=0;
    if(n7_mutex!=0) { CloseHandle(n7_mutex); n7_mutex=0; }
    return false;
}
string N7FormEncode(string value) {
    uchar bytes[]; int n=StringToCharArray(value,bytes,0,WHOLE_ARRAY,CP_UTF8)-1;
    string encoded="";
    for(int i=0;i<n;i++) encoded+="%"+StringFormat("%02X",(uint)bytes[i]);
    return encoded;
}
bool N7TakeNotification(string &token,string &chat,string &message) {
    if(!N7Lock()) return false;
    RtlMoveMemory(n7_header,n7_view,(ulong)N7_HEADER);
    bool valid=N7HeaderValid(n7_header),available=false;
    if(valid) {
        available=N7Get64(n7_header,24)>N7Get64(n7_header,32);
        if(available) RtlMoveMemory(n7_body,n7_view+N7_HEADER,(ulong)N7_BODY);
    }
    if(!N7Unlock()) return false;
    if(!valid||!available) return false; // 초기화 중/빈 우편함은 다음 타이머에서 확인
    int length=(int)N7Get32(n7_header,40);
    bool payloadOK=N7CRC(n7_body,0,length)==N7Get32(n7_header,44)&&N7Unpack(length,token,chat,message);
    if(!N7Lock()) return false;
    RtlMoveMemory(n7_verify,n7_view,(ulong)N7_HEADER);
    bool same=N7Equal(n7_header,0,n7_verify,0,N7_HEADER);
    if(same) {
        // 네트워크 전송 시도 전에 가져옴을 기록한다. 재시작 시 모호한 전송을 반복하지 않는다.
        N7Put64(n7_verify,32,N7Get64(n7_verify,24)); N7HeaderCRC(n7_verify);
        ArrayInitialize(n7_body,0);
        RtlMoveMemory(n7_view+N7_HEADER,n7_body,(ulong)N7_BODY);
        RtlMoveMemory(n7_view,n7_verify,(ulong)N7_HEADER);
    }
    if(!N7Unlock()||!same) return false;
    if(!payloadOK) { Print("[NOTIFY_BAD_PAYLOAD] 알림 본문을 전송하지 않았습니다."); return false; }
    return true;
}
int OnInit() {
   if(!DS5DeploymentAllowed(true)) return INIT_FAILED;
    if(!_IsX64||!MQLInfoInteger(MQL_DLLS_ALLOWED)||!N7SetIdentity()||!N7Owner(".Consumer")) {
        Print("[NOTIFY_INIT_FAILED] x64/DLL/설정 또는 중복 실행을 확인해 주세요."); N7Close(); return INIT_FAILED;
    }
    if(!EventSetMillisecondTimer(100)) { N7Close(); return INIT_FAILED; }
    Print("[NOTIFY_READY] 알림 전용 EA 시작 | Account=",n7_login," | Stream=",Inp_StreamId);
    return INIT_SUCCEEDED;
}
void OnDeinit(const int reason) { EventKillTimer(); N7Close(); }
void OnTimer() {
    if(n7_fault||!N7SameAccount()||!N7OpenConsumer()) return;
    ulong now=GetTickCount64();
    if(n7_lastRequest!=0&&now-n7_lastRequest<1000) return;
    string token,chat,message;
    if(!N7TakeNotification(token,chat,message)||!N7SameAccount()) return;
    string url="https://api.telegram.org/bot"+token+"/sendMessage";
    string params="chat_id="+N7FormEncode(chat)+"&text="+N7FormEncode(message);
    char post[],result[]; string headers;
    int n=StringToCharArray(params,post,0,WHOLE_ARRAY,CP_UTF8)-1;
    ArrayResize(post,n); // HTTP 본문에 C 문자열의 종료 NUL을 포함하지 않는다.
    n7_lastRequest=GetTickCount64(); ResetLastError();
    int status=WebRequest("POST",url,"Content-Type: application/x-www-form-urlencoded\r\n",500,post,result,headers);
    int error=GetLastError();
    Print("[NOTIFY_HTTP_RESULT] Sequence=",N7Get64(n7_header,24)," | HTTP=",status," | Error=",error);
    // 토큰·URL·메시지·응답 본문을 로그나 파일에 남기지 않는다. 실패/응답 불명 시 자동 재전송하지 않는다.
    ArrayInitialize(post,0); ArrayInitialize(result,0); token=""; chat=""; message=""; url=""; params="";
}
