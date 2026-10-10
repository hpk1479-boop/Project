// Transport only: numeric indicator payloads are supplied by the existing EA.
#ifndef STAFF_WIRE_V2_MQH
#define STAFF_WIRE_V2_MQH
#include "STAFF_Wire_Schema.mqh"
union StaffWireNumber { double real; ulong bits; };

int StaffWireValidity(const double &values[],const int rows)
{
   // Validation only, exactly the STAFF required-column groups; no formulas.
   int columns[]={STAFF_COL_EMA_20,STAFF_COL_EMA_50,STAFF_COL_EMA_200,-1,STAFF_COL_PRICE_HMA_6,STAFF_COL_PRICE_BAND_LOWER,STAFF_COL_PRICE_BAND_UPPER,STAFF_COL_HMA_6,STAFF_COL_HMA_17,STAFF_COL_PRICE_REGIME_BASIS,STAFF_COL_PRICE_REGIME_UPPER,STAFF_COL_PRICE_REGIME_LOWER,-1,STAFF_COL_HMA_6,STAFF_COL_HMA_17,STAFF_COL_HMA_50,STAFF_COL_HMA_168,-1,STAFF_COL_RSI_VAL,STAFF_COL_RSI_DB,STAFF_COL_RSI_UB,STAFF_COL_RSI_BASIS,STAFF_COL_RSI_REGIME_UPPER,STAFF_COL_RSI_REGIME_LOWER,-1,STAFF_COL_STO_VAL,STAFF_COL_STO_DB,STAFF_COL_STO_UB,STAFF_COL_STO_BASIS,STAFF_COL_STO_REGIME_UPPER,STAFF_COL_STO_REGIME_LOWER,-1,STAFF_COL_DI_VAL,STAFF_COL_DI_DB,STAFF_COL_DI_UB,STAFF_COL_DI_BASIS,STAFF_COL_DI_REGIME_UPPER,STAFF_COL_DI_REGIME_LOWER,-1,STAFF_COL_OPEN_BAND_4_MID,STAFF_COL_WONBI_UPPER,STAFF_COL_WONBI_LOWER,-1};
   int bits=0,group=0,base=(rows-1)*STAFF_WIRE_VALUE_COLUMNS;bool valid=true;
   for(int i=0;i<ArraySize(columns);++i)
   {
      if(columns[i]<0) {if(valid) bits|=1<<group;group++;valid=true;continue;}
      double x=values[base+columns[i]];
      if(!MathIsValidNumber(x) || MathAbs(x)>1.0e300) valid=false;
   }
   return bits;
}

void StaffPut32(uchar &out[],int &pos,const uint v)
{ for(int k=0;k<4;++k) out[pos++]=(uchar)(v>>(k*8)); }
void StaffPut64(uchar &out[],int &pos,const ulong v)
{ for(int k=0;k<8;++k) out[pos++]=(uchar)(v>>(k*8)); }
uint StaffGet32(const uchar &data[],const int pos)
{ uint v=0; for(int k=0;k<4;++k) v|=((uint)data[pos+k])<<(k*8); return v; }
uint StaffCRC32(const uchar &data[],const int start,const int size)
{
   static uint table[256]; static bool ready=false;
   if(!ready)
   {
      for(uint i=0;i<256;++i)
      { uint c=i; for(int k=0;k<8;++k) c=(c&1)!=0 ? (c>>1)^0xEDB88320 : c>>1; table[i]=c; }
      ready=true;
   }
   uint crc=0xFFFFFFFF;
   for(int i=start;i<start+size;++i) crc=table[(crc^data[i])&255]^(crc>>8);
   return crc^0xFFFFFFFF;
}
int StaffUTF8(const string text,uchar &data[])
{ int n=StringToCharArray(text,data,0,WHOLE_ARRAY,CP_UTF8)-1; ArrayResize(data,n); return n; }
void StaffEnvelope(const long seq,const int kind,const int ns,const int nt,const int rows,
                   const uchar &payload[],uchar &out[])
{
   int size=ArraySize(payload),p=0; ArrayResize(out,40+size+4);
   StaffPut32(out,p,0x534D4F53); StaffPut32(out,p,2); StaffPut64(out,p,(ulong)seq);
   StaffPut32(out,p,(uint)ns); StaffPut32(out,p,(uint)nt); StaffPut32(out,p,(uint)rows);
   StaffPut32(out,p,STAFF_WIRE_VALUE_COLUMNS); StaffPut32(out,p,STAFF_WIRE_SCHEMA_ID); StaffPut32(out,p,(uint)kind);
   ArrayCopy(out,payload,p,0,size); p+=size;
   StaffPut32(out,p,StaffCRC32(payload,0,size));
}
void StaffFeedFrame(const string symbol,const string tf,const long seq,const int kind,
                    const long &times[],const long &volumes[],const double &values[],
                    const int rows,uchar &out[])
{
   uchar sym[],tfb[],payload[];int ns=StaffUTF8(symbol,sym),nt=StaffUTF8(tf,tfb),p=0;
   ArrayResize(payload,ns+nt+rows*(16+STAFF_WIRE_VALUE_COLUMNS*8));
   ArrayCopy(payload,sym,p);p+=ns;ArrayCopy(payload,tfb,p);p+=nt;
   for(int i=0;i<rows;++i) StaffPut64(payload,p,(ulong)times[i]);
   for(int i=0;i<rows;++i) StaffPut64(payload,p,(ulong)volumes[i]);
   StaffWireNumber number;
   for(int i=0;i<rows*STAFF_WIRE_VALUE_COLUMNS;++i) { number.real=values[i];StaffPut64(payload,p,number.bits); }
   StaffEnvelope(seq,kind,ns,nt,rows,payload,out);
}
void StaffBundleAdd(uchar &body[],const uchar &frame[])
{
   int p=ArraySize(body),n=ArraySize(frame);ArrayResize(body,p+4+n);
   StaffPut32(body,p,(uint)n);ArrayCopy(body,frame,p);
}
void StaffBundleFrame(const string symbol,const long observed_ms,const int count,
                      const uchar &body[],uchar &out[])
{
   uchar sym[],payload[];int ns=StaffUTF8(symbol,sym),p=0;
   ArrayResize(payload,ns+12+ArraySize(body));ArrayCopy(payload,sym);p+=ns;
   StaffPut64(payload,p,(ulong)observed_ms);StaffPut32(payload,p,(uint)count);
   ArrayCopy(payload,body,p);StaffEnvelope(0,STAFF_WIRE_BUNDLE,ns,0,12+ArraySize(body),payload,out);
}
bool StaffHello(const int handle)
{
   uchar payload[],frame[],ack[];StaffUTF8(STAFF_EA_BUILD_HASH,payload);
   StaffEnvelope(0,STAFF_WIRE_HELLO,64,0,0,payload,frame);
   if(FileWriteArray(handle,frame)!=ArraySize(frame)) return false;
   FileFlush(handle);FileSeek(handle,0,SEEK_SET);
   ArrayResize(ack,108);
   if(FileReadArray(handle,ack,0,108)!=108) return false;
   bool ok=StaffGet32(ack,0)==0x534D4F53 && StaffGet32(ack,4)==2 &&
           StaffGet32(ack,32)==STAFF_WIRE_SCHEMA_ID && StaffGet32(ack,36)==STAFF_WIRE_ACK &&
           StaffGet32(ack,104)==StaffCRC32(ack,40,64);
   for(int i=0;i<64 && ok;++i) if(ack[40+i]!=payload[i]) ok=false;
   FileFlush(handle);FileSeek(handle,0,SEEK_SET);
   return ok;
}
#endif
