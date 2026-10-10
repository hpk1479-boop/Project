// Observation only. Separate from the unchanged 45-slot Named Pipe wire.
string StaffStatusEscape(string value)
{
   string result="";
   for(int i=0;i<StringLen(value);i++)
   {
      ushort c=StringGetCharacter(value,i);
      if(c==34) result+="\\\"";
      else if(c==92) result+="\\\\";
      else if(c<32) result+=StringFormat("\\u%04X",(int)c);
      else result+=StringSubstr(value,i,1);
   }
   return "\""+result+"\"";
}

void StaffPublishIdentityStatus(const string pipe_name,const bool active,
                                const bool pipe_open,const long sequence)
{
   if(MQLInfoInteger(MQL_TESTER)) return;
   static string filename="";
   if(filename=="")
   {
      uchar bytes[],key[],hash[];
      string identity=TerminalInfoString(TERMINAL_DATA_PATH)+"|"+IntegerToString(ChartID());
      int count=StringToCharArray(identity,bytes,0,WHOLE_ARRAY,CP_UTF8);
      if(count<=1) return;
      ArrayResize(bytes,count-1);
      if(CryptEncode(CRYPT_HASH_SHA256,bytes,key,hash)!=32) return;
      string hex="";
      for(int i=0;i<ArraySize(hash);i++) hex+=StringFormat("%02x",(int)hash[i]);
      FolderCreate("StaffOfMosesStatus",FILE_COMMON);
      filename="StaffOfMosesStatus\\"+hex+".json";
   }
   static ulong last_write_ms=0;
   static string last_state="";
   string state=pipe_name+"|"+(active?"1":"0")+"|"+(pipe_open?"1":"0")+"|"+
      IntegerToString(AccountInfoInteger(ACCOUNT_LOGIN))+"|"+AccountInfoString(ACCOUNT_SERVER)+"|"+
      IntegerToString(TerminalInfoInteger(TERMINAL_CONNECTED))+"|"+
      IntegerToString(TerminalInfoInteger(TERMINAL_BUILD));
   ulong now=GetTickCount64();
   if(last_write_ms!=0 && now-last_write_ms<1000 && state==last_state) return;
   string value="{\"schema\":\"STAFF_MT5_IDENTITY_V1\","
      "\"terminal_path\":"+StaffStatusEscape(TerminalInfoString(TERMINAL_PATH))+","+
      "\"data_path\":"+StaffStatusEscape(TerminalInfoString(TERMINAL_DATA_PATH))+","+
      "\"account_server\":"+StaffStatusEscape(AccountInfoString(ACCOUNT_SERVER))+","+
      "\"account_login\":"+IntegerToString(AccountInfoInteger(ACCOUNT_LOGIN))+","+
      "\"terminal_connected\":"+(TerminalInfoInteger(TERMINAL_CONNECTED)?"true":"false")+","+
      "\"terminal_build\":"+IntegerToString(TerminalInfoInteger(TERMINAL_BUILD))+","+
      "\"heartbeat_timestamp\":"+IntegerToString((long)TimeGMT())+","+
      "\"active\":"+(active?"true":"false")+","+
      "\"pipe_open\":"+(pipe_open?"true":"false")+","+
      "\"pipe_name\":"+StaffStatusEscape(pipe_name)+","+
      "\"snapshot_sequence\":"+IntegerToString(sequence)+"}";
   string temporary=filename+".tmp";
   int handle=FileOpen(temporary,FILE_WRITE|FILE_TXT|FILE_ANSI|FILE_COMMON,0,CP_UTF8);
   if(handle==INVALID_HANDLE) return;
   uint written=FileWriteString(handle,value);
   FileClose(handle);
   if(written==0) return;
   if(FileMove(temporary,FILE_COMMON,filename,FILE_COMMON|FILE_REWRITE))
   {last_write_ms=now;last_state=state;}
}
