"""Move the EA symbol resolver into STAFF_Symbol_Map.mqh (pure picker + MT5 wrapper) and add a test script."""
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];MT5=ROOT/'Part1/program/MT5'
N='\r\n'

MQH='''// STAFF_Symbol_Map.mqh
// InpSymbols are logical symbols (Wire/Python identity). MT5 data access uses the
// broker symbol: exact name -> alias name -> alias with prefix/suffix.
// Exactly one candidate is used; several or none reject only that symbol's feeds.
#ifndef STAFF_SYMBOL_MAP_MQH
#define STAFF_SYMBOL_MAP_MQH

const string STAFF_SYMBOL_ALIASES[] = {"XAUUSD,GOLD","NAS100,US100,USTEC","BTCUSD,XBTUSD"};
string g_map_logical[];
string g_map_broker[];

bool StaffIsAlnum(ushort c)
{
   return (c>='A' && c<='Z') || (c>='a' && c<='z') || (c>='0' && c<='9');
}

bool StaffIsSeparator(ushort c)
{
   return c>32 && c<127 && !StaffIsAlnum(c);
}

string StaffSymbolCore(string s)
{
   string u=s;StringToUpper(u);
   string out="";
   for(int i=0;i<StringLen(u);++i)
   {
      ushort c=StringGetCharacter(u,i);
      if(StaffIsAlnum(c)) out+=ShortToString(c);
   }
   return out;
}

void StaffAddUnique(string &items[],string value)
{
   for(int i=0;i<ArraySize(items);++i) if(items[i]==value) return;
   int n=ArraySize(items);ArrayResize(items,n+1);items[n]=value;
}

void StaffSymbolAliases(string logical,string &aliases[])
{
   ArrayResize(aliases,0);
   string core=StaffSymbolCore(logical);
   if(StringLen(core)==0) return;
   StaffAddUnique(aliases,core);
   for(int g=0;g<ArraySize(STAFF_SYMBOL_ALIASES);++g)
   {
      string names[];
      int n=StringSplit(STAFF_SYMBOL_ALIASES[g],',',names);
      bool member=false;
      for(int i=0;i<n;++i) if(names[i]==core) member=true;
      if(member) for(int i=0;i<n;++i) StaffAddUnique(aliases,names[i]);
   }
}

bool StaffLowerSuffix(string suffix)
{
   int n=StringLen(suffix);
   if(n<1 || n>4) return false;
   for(int i=0;i<n;++i)
   {
      ushort c=StringGetCharacter(suffix,i);
      if(c<'a' || c>'z') return false;
   }
   return true;
}

// alias with an optional short prefix ending in a separator (m.XAUUSD) and an optional
// suffix that starts with a separator (XAUUSD+, XAUUSD.pro) or is 1-4 lowercase letters (XAUUSDm).
// XAUUSDT / US1000 / GOLDEUR are not matches.
bool StaffAffixMatch(string name,string alias)
{
   string up=name;StringToUpper(up);
   int pos=StringFind(up,alias);
   while(pos>=0)
   {
      string suffix=StringSubstr(name,pos+StringLen(alias));
      bool prefix_ok=(pos==0) || (pos<=5 && StaffIsSeparator(StringGetCharacter(name,pos-1)));
      bool suffix_ok=StringLen(suffix)==0 ||
                     (StringLen(suffix)<=6 && StaffIsSeparator(StringGetCharacter(suffix,0))) ||
                     StaffLowerSuffix(suffix);
      if(prefix_ok && suffix_ok) return true;
      pos=StringFind(up,alias,pos+1);
   }
   return false;
}

string StaffJoin(string &items[])
{
   string out="";
   for(int i=0;i<ArraySize(items);++i) out+=(i>0 ? "," : "")+items[i];
   return out;
}

// Pure rule over a list of broker names. Returns the broker symbol or "".
// how = exact / alias / affix / ambiguous / missing; candidates = what was considered.
string StaffPickBrokerSymbol(string logical,bool logical_exists,string &names[],string &how,string &candidates)
{
   candidates="";
   if(logical_exists) { how="exact";return logical; }
   string aliases[],direct[],affixed[];
   StaffSymbolAliases(logical,aliases);
   for(int i=0;i<ArraySize(names);++i)
   {
      string up=names[i];StringToUpper(up);
      bool equal=false;
      for(int a=0;a<ArraySize(aliases);++a) if(up==aliases[a]) equal=true;
      if(equal) { StaffAddUnique(direct,names[i]);continue; }
      for(int a=0;a<ArraySize(aliases);++a)
         if(StaffAffixMatch(names[i],aliases[a])) { StaffAddUnique(affixed,names[i]);break; }
   }
   string chosen[];
   if(ArraySize(direct)>0) { ArrayCopy(chosen,direct);how="alias"; }
   else { ArrayCopy(chosen,affixed);how="affix"; }
   candidates=StaffJoin(chosen);
   if(ArraySize(chosen)==1) return chosen[0];
   how=(ArraySize(chosen)>1 ? "ambiguous" : "missing");
   return "";
}

string ResolveBrokerSymbol(string logical)
{
   for(int i=0;i<ArraySize(g_map_logical);++i)
      if(g_map_logical[i]==logical) return g_map_broker[i];
   bool custom=false;
   bool exists=SymbolExist(logical,custom);
   string names[];
   if(!exists)
   {
      int total=SymbolsTotal(false);
      ArrayResize(names,total);
      for(int i=0;i<total;++i) names[i]=SymbolName(i,false);
   }
   string how,candidates;
   string broker=StaffPickBrokerSymbol(logical,exists,names,how,candidates);
   if(how=="ambiguous") PrintFormat("[SYMBOL MAP AMBIGUOUS] logical=%s candidates=%s",logical,candidates);
   else if(how=="missing") PrintFormat("[SYMBOL MAP MISSING] logical=%s",logical);
   else PrintFormat("[SYMBOL MAP] %s -> %s (%s)",logical,broker,how);
   int n=ArraySize(g_map_logical);
   ArrayResize(g_map_logical,n+1);ArrayResize(g_map_broker,n+1);
   g_map_logical[n]=logical;g_map_broker[n]=broker;
   return broker;
}

#endif
'''.replace('\n',N)

TEST='''// STAFF_Symbol_Map_Test.mq5 - MT5 script. Drop on any chart; results go to the Experts log.
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
'''.replace('\n',N)

def main():
    ea=MT5/'THE_STAFF_OF_MOSES.mq5';text=ea.read_bytes().decode('utf-8')
    tail=text[text.index('int OnInit()'):]
    start=text.index(N+'// ------------------------------------------------------------------'+N+'// SYMBOL MAP:')
    end=text.index('string StaffTesterLogicalSymbol()')
    text=text[:start]+N+'#include "STAFF_Symbol_Map.mqh"'+N+N+text[end:]
    assert text[text.index('int OnInit()'):]==tail
    ea.write_bytes(text.encode('utf-8'))
    (MT5/'STAFF_Symbol_Map.mqh').write_bytes(MQH.encode('utf-8'))
    (MT5/'STAFF_Symbol_Map_Test.mq5').write_bytes(TEST.encode('utf-8'))
    print('split ok')
if __name__=='__main__':main()
