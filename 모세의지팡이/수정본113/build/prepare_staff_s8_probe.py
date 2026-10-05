"""Diagnostic EA only: compare LIVE incremental state with a fresh full build
at the same tester observation. Production source/config/schema stay untouched."""
from staff_s8_evidence import *
import shutil
folder=OUT/'ea_live_path_probe_source'
if not folder.exists():shutil.copytree(ROOT/'Part1/program/MT5',folder)
p=folder/'THE_STAFF_OF_MOSES.mq5';s=(ROOT/'Part1/program/MT5/THE_STAFF_OF_MOSES.mq5').read_text('utf-8')
s=s.replace('FeedContext g_feeds[];', 'FeedContext g_feeds[];\nFeedContext g_s8_probe[];')
function=r'''
void StaffS8Probe(const int index,const long observed)
{
   if(ArraySize(g_s8_probe)==0)
   {
      ArrayResize(g_s8_probe,ArraySize(g_feeds));
      for(int j=0;j<ArraySize(g_feeds);++j) g_s8_probe[j]=g_feeds[j];
   }
   uchar frame[],unused[];
   bool fast=BuildWireChange(g_s8_probe[index],frame,unused);
   long t[],vol[];double val[];int rows=0,flags=0;
   bool full=BuildStaffPayload(g_feeds[index],t,vol,val,rows,flags);
   int bad=0,first=-1;
   if(fast!=full) bad=-1;
   else if(full)
   {
      if(rows!=ArraySize(g_s8_probe[index].cached_times)) bad=-2;
      else
      {
         for(int i=0;i<rows;++i)
            if(t[i]!=g_s8_probe[index].cached_times[i] || vol[i]!=g_s8_probe[index].cached_volumes[i]) bad++;
         StaffWireNumber a,b;
         for(int i=0;i<rows*STAFF_VALUE_COLUMNS;++i)
         {
            a.real=val[i];b.real=g_s8_probe[index].cached_values[i];
            if(a.bits!=b.bits) {bad++;if(first<0) first=i;}
         }
      }
   }
   int h=FileOpen(g_native_dir+"\\live_path_probe.tsv",FILE_READ|FILE_WRITE|FILE_TXT|FILE_ANSI|FILE_COMMON);
   if(h!=INVALID_HANDLE)
   {
      FileSeek(h,0,SEEK_END);
      FileWriteString(h,IntegerToString(observed)+"\t"+g_feeds[index].tf_text+"\t"+
         IntegerToString(fast ? (int)StaffGet32(frame,36) : 0)+"\t"+IntegerToString(bad)+"\t"+IntegerToString(first)+"\r\n");
      FileClose(h);
   }
}
'''
pos=s.index('void BacktestOnTick()');s=s[:pos]+function+'\n'+s[pos:]
s=s.replace('if(g_native_export_enabled) PipeCaptureSecond(i,g_feeds[i],(long)now);',
    'if(g_native_export_enabled) {StaffS8Probe(i,(long)now);PipeCaptureSecond(i,g_feeds[i],(long)now);}')
p.write_text(s,'utf-8')
s=(ROOT/'build/capture_staff_s8_mt5.py').read_text('utf-8')
s=s.replace("if options.before:label='before_'+label", "label='probe_'+label")
s=s.replace("lambda: ROOT.parent/'수정본14/Part1/program/MT5' if options.before else ROOT/'Part1/program/MT5'", "lambda: OUT/'ea_live_path_probe_source'")
(ROOT/'build/capture_staff_s8_probe.py').write_text(s,'utf-8')
print('Diagnostic probe source prepared; production EA unmodified')
