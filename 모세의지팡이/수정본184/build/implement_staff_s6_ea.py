from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
p=ROOT/'Part1/program/MT5/THE_STAFF_OF_MOSES.mq5'
s=p.read_text('utf-8')
s=s.replace('#property version   "1.51"','#property version   "1.60"')
s=s.replace('input ENUM_STAFF_MODE InpMode = STAFF_MODE_LIVE;', '''input ENUM_STAFF_MODE InpMode = STAFF_MODE_LIVE;
// Migration/verification switch. Default is Wire v2 / MSP3; v1 remains replayable.
input int InpWireVersion = 2;
#include "STAFF_Wire_V2.mqh"''')
s=s.replace('   ulong           last_slow_log_ms;','''   ulong           last_slow_log_ms;
   long            wire_seq;
   long            wire_bar;
   int             wire_flags;
   uchar           wire_previous[];''')
s=s.replace('ulong g_last_progress_ms=0;', '''ulong g_last_progress_ms=0;
uchar g_pending_wire[],g_pending_full[];
bool g_wire_reconnected=false;
long g_wire_frames=0,g_wire_bytes=0,g_wire_full=0,g_wire_row=0,g_wire_heartbeat=0;''')
s=s.replace('   PrintFormat("[THE STAFF OF MOSES] ✅ Named Pipe 연결: %s", STAFF_PIPE_NAME);', '''   if(InpWireVersion==2)
   {
      if(!StaffHello(g_pipe)) { CloseStaffPipe(); return false; }
      g_wire_reconnected=true;
      for(int i=0;i<ArraySize(g_feeds);++i) ArrayResize(g_feeds[i].wire_previous,0);
      PrintFormat("[STAFF Wire v2] HELLO schema=%08X build=%s",STAFF_WIRE_SCHEMA_ID,STAFF_EA_BUILD_HASH);
   }
   PrintFormat("[THE STAFF OF MOSES] ✅ Named Pipe 연결: %s", STAFF_PIPE_NAME);''')
at=s.index('// LIVE Named Pipe와 BACKTEST STAFF pipe capture')
s=s[:at]+'''// Retry every pending observation before generating another; never coalesce.
bool FlushStaffBundle()
{
   if(ArraySize(g_pending_wire)==0) return true;
   if(!EnsureStaffPipe()) return false;
   uchar send[];
   if(g_wire_reconnected) ArrayCopy(send,g_pending_full); else ArrayCopy(send,g_pending_wire);
   ResetLastError();uint n=FileWriteArray(g_pipe,send);FileFlush(g_pipe);
   if(n!=(uint)ArraySize(send) || GetLastError()!=0) { CloseStaffPipe();return false; }
   g_wire_bytes+=ArraySize(send);g_wire_frames++;
   ArrayResize(g_pending_wire,0);ArrayResize(g_pending_full,0);g_wire_reconnected=false;
   return true;
}

bool BuildWireChange(FeedContext &f,uchar &frame[],uchar &full[])
{
   long t[],vol[];double v[];int rows=0,flags=0;
   if(!BuildStaffPayload(f,t,vol,v,rows,flags)) return false;
   long seq=f.wire_seq+1;
   StaffFeedFrame(f.symbol,f.tf_text,seq,STAFF_WIRE_FULL,t,vol,v,rows,full);
   int kind=STAFF_WIRE_FULL;
   int prior=ArraySize(f.wire_previous);
   // Header seq and CRC are excluded. Compare every unchanged historical cell;
   // even an indicator-history correction forces FULL rather than losing data.
   if(prior==ArraySize(full) && f.wire_bar==t[rows-1] && f.wire_flags==flags)
   {
      uchar sb[],tb[];int offset=40+StaffUTF8(f.symbol,sb)+StaffUTF8(f.tf_text,tb);
      bool history=true,unchanged=true;
      for(int i=offset;i<ArraySize(full)-4;++i)
      {
         if(full[i]==f.wire_previous[i]) continue;
         unchanged=false;
         bool last_volume=(i>=offset+rows*8+(rows-1)*8 && i<offset+rows*16);
         bool last_values=(i>=offset+rows*16+(rows-1)*360);
         if(!last_volume && !last_values) history=false;
      }
      if(unchanged) kind=STAFF_WIRE_HEARTBEAT;
      else if(history) kind=STAFF_WIRE_ROW;
   }
   if(kind==STAFF_WIRE_FULL) { ArrayCopy(frame,full);g_wire_full++; }
   else
   {
      long rt[],rv[];double rx[];int n=kind==STAFF_WIRE_ROW ? 1 : 0;
      ArrayResize(rt,n);ArrayResize(rv,n);ArrayResize(rx,n*45);
      if(n==1) {rt[0]=t[rows-1];rv[0]=vol[rows-1];ArrayCopy(rx,v,0,(rows-1)*45,45);g_wire_row++;}
      else g_wire_heartbeat++;
      StaffFeedFrame(f.symbol,f.tf_text,seq,kind,rt,rv,rx,n,frame);
   }
   f.wire_seq=seq;f.wire_bar=t[rows-1];f.wire_flags=flags;ArrayCopy(f.wire_previous,full);
   g_snapshot_seq++;return true;
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
      uchar body[],fullbody[];int count=0;
      for(int i=first;i<ArraySize(g_feeds);++i)
      {
         if(g_feeds[i].symbol!=g_feeds[first].symbol || now<g_feeds[i].next_poll_ms) continue;
         g_feeds[i].next_poll_ms=now+(ulong)STAFF_TIMER_MS;
         uchar frame[],full[];
         if(!BuildWireChange(g_feeds[i],frame,full)) continue;
         StaffBundleAdd(body,frame);StaffBundleAdd(fullbody,full);count++;
         g_feeds[i].published=true;
      }
      if(count==0) continue;
      StaffBundleFrame(g_feeds[first].symbol,observed,count,body,g_pending_wire);
      StaffBundleFrame(g_feeds[first].symbol,observed,count,fullbody,g_pending_full);
      if(!FlushStaffBundle()) return;
   }
}

'''+s[at:]
s=s.replace('   if(StaffBacktestRuntime()) return;\n\n   int total=ArraySize(g_feeds);', '''   if(StaffBacktestRuntime()) return;
   if(InpWireVersion==2)
   {
      PublishWireBundles();
      StaffPublishIdentityStatus(STAFF_PIPE_NAME,true,g_pipe!=INVALID_HANDLE,g_snapshot_seq);
      return;
   }

   int total=ArraySize(g_feeds);''')
s=s.replace('   CloseStaffPipe();\n   StaffPublishIdentityStatus', '''   PrintFormat("[STAFF Wire] version=%d bundles=%I64d bytes=%I64d FULL=%I64d ROW=%I64d HEARTBEAT=%I64d",
               InpWireVersion,g_wire_frames,g_wire_bytes,g_wire_full,g_wire_row,g_wire_heartbeat);
   CloseStaffPipe();
   StaffPublishIdentityStatus''')
# Keep the existing capture payload construction exactly, encode its records as v2.
s=s.replace('FileWriteString(m,"pipe_capture\\tSTAFF_PIPE_V1\\r\\n");',
'''FileWriteString(m,"pipe_capture\\t"+(InpWireVersion==2 ? "STAFF_PIPE_V2" : "STAFF_PIPE_V1")+"\\r\\n");''')
s=s.replace('FileWriteInteger(h,STAFF_PIPE_CAPTURE_MAGIC,INT_VALUE);',
            'FileWriteInteger(h,InpWireVersion==2 ? 0x4D535033 : STAFF_PIPE_CAPTURE_MAGIC,INT_VALUE);')
s=s.replace('FileWriteInteger(h,STAFF_PIPE_CAPTURE_VERSION,INT_VALUE);',
            'FileWriteInteger(h,InpWireVersion==2 ? 2 : STAFF_PIPE_CAPTURE_VERSION,INT_VALUE);')
needle='''   int h=g_pipe_cap_handles[index];
   ResetLastError();
   FileWriteInteger(h,kind,INT_VALUE);'''
assert needle in s
s=s.replace(needle,'''   int h=g_pipe_cap_handles[index];
   ResetLastError();
   if(InpWireVersion==2)
   {
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
   FileWriteInteger(h,kind,INT_VALUE);''')
p.write_text(s,encoding='utf-8')
