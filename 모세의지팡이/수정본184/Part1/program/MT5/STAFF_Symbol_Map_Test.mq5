// STAFF_Symbol_Map_Test.mq5 - MT5 script. Drop on any chart; results go to the Experts log.
// Tests the same STAFF_Symbol_Map.mqh rules the EA uses. No orders, no files, no pipe.
#property script_show_inputs false
#include "STAFF_Symbol_Map.mqh"

int g_fail=0;

void Check(string label,string logical,bool exists,string list,string expect_broker,string expect_how)
{
   string names[];
   if(StringLen(list)>0) StringSplit(list,'|',names);
   string how,candidates;
   string got=StaffPickBrokerSymbol(logical,exists,names,how,candidates);
   bool ok=(got==expect_broker && how==expect_how);
   if(!ok) g_fail++;
   PrintFormat("[SYMBOL MAP TEST] %s %s | logical=%s -> %s (%s) expected %s (%s) | candidates=%s",
               ok?"PASS":"FAIL",label,logical,got,how,expect_broker,expect_how,candidates);
}

void OnStart()
{
   Check("exact","XAUUSD+",true,"XAUUSD+|NAS100","XAUUSD+","exact");
   Check("suffix plus","XAUUSD",false,"XAUUSD+|EURUSD","XAUUSD+","affix");
   Check("suffix dot","XAUUSD+",false,"XAUUSD.pro|EURUSD","XAUUSD.pro","affix");
   Check("suffix lower","XAUUSD+",false,"XAUUSDm|XAUUSDT","XAUUSDm","affix");
   Check("prefix","XAUUSD+",false,"m.XAUUSD|EURUSD","m.XAUUSD","affix");
   Check("alias exact","XAUUSD+",false,"GOLD|GOLDEUR","GOLD","alias");
   Check("alias suffix","NAS100",false,"USTEC.pro|US30","USTEC.pro","affix");
   Check("alias BTC","BTCUSD",false,"XBTUSD.a|ETHUSD","XBTUSD.a","affix");
   Check("alias beats affix","XAUUSD+",false,"GOLD|XAUUSD.a","GOLD","alias");
   Check("ambiguous","XAUUSD+",false,"XAUUSD.a|XAUUSD.b","","ambiguous");
   Check("ambiguous alias","NAS100",false,"US100|USTEC","","ambiguous");
   Check("missing","XAUUSD+",false,"EURUSD|XAUUSDT|US1000","","missing");
   Check("no false digit","NAS100",false,"NAS1000|US1001","","missing");
   PrintFormat("[SYMBOL MAP TEST] %s - fail=%d",g_fail==0?"ALL PASS":"FAILED",g_fail);
}
