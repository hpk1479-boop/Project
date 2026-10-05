"""Part2: logical symbol everywhere, broker symbol only for the Strategy Tester (byte-exact edits)."""
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];P2=ROOT/'Part2'

def patch(rel,pairs):
    path=P2/rel;data=path.read_bytes()
    for old,new in pairs:
        # Match the region's own line ending and write the new lines with it.
        for nl in ('\n','\r\n'):
            o=old.replace('\n',nl).encode('utf-8')
            if data.count(o)==1:break
        else:raise AssertionError((rel,old[:70]))
        i=data.find(o);end=data.find(b'\n',i+len(o)-1)
        if '\n' not in old:nl='\r\n' if end>0 and data[end-1:end]==b'\r' else '\n'
        data=data.replace(o,new.replace('\n',nl).encode('utf-8'))
    path.write_bytes(data)
    print('patched',rel)

patch('event_backtest/settings.py',[
 ("def settings(path=None):",
  "# Logical symbols come from the EA/captures; only the form is checked here\n"
  "# (same rule as the LIVE pipe: 1-64 chars, no whitespace, comma or control chars).\n"
  "import re as _re\n"
  "SYMBOL_FORM=_re.compile(r'[^\\s,\\x00-\\x1f\\x7f]{1,64}')\n"
  "\n"
  "def settings(path=None):"),
 ("    data.setdefault('oz_evaluation','selected')\n    return data",
  "    data.setdefault('oz_evaluation','selected')\n"
  "    # Per computer: logical symbol -> this broker's Strategy Tester symbol. Missing = same name.\n"
  "    data.setdefault('broker_symbols',{})\n"
  "    return data\n"
  "\n"
  "def tester_symbol(logical,config=None):\n"
  "    mapping=(config if config is not None else settings()).get('broker_symbols') or {}\n"
  "    broker=str(mapping.get(logical,logical)).strip()\n"
  "    if not SYMBOL_FORM.fullmatch(broker):raise ValueError('broker_symbols 값 형식 오류: '+repr(broker))\n"
  "    return broker\n"
  "\n"
  "def stored_symbols(warehouse):\n"
  "    \"\"\"Logical symbols that already have captures; empty when the catalog is absent or busy.\"\"\"\n"
  "    path=Path(warehouse)/'captures.duckdb'\n"
  "    if not path.is_file():return []\n"
  "    try:\n"
  "        import duckdb\n"
  "        db=duckdb.connect(str(path),read_only=True)\n"
  "        try:return [r[0] for r in db.execute('SELECT DISTINCT symbol FROM captures WHERE symbol IS NOT NULL ORDER BY symbol').fetchall()]\n"
  "        finally:db.close()\n"
  "    except Exception:return []"),
 ("    if data['symbol'] not in ('XAUUSD+','NAS100','BTCUSD'):raise ValueError('symbol not allowed')",
  "    if not isinstance(data['symbol'],str) or not SYMBOL_FORM.fullmatch(data['symbol']):raise ValueError('symbol form')"),
])

patch('event_backtest/gui.py',[
 ("            control=ttk.Combobox(line,textvariable=var,\n"
  "                                 values=('XAUUSD+','NAS100','BTCUSD') if name=='symbol' else ('BAR','TIMER'),\n"
  "                                 state='readonly',style='Arrowless.Dashboard.TCombobox')",
  "            from .settings import stored_symbols\n"
  "            control=ttk.Combobox(line,textvariable=var,\n"
  "                                 values=tuple(dict.fromkeys([var.get(),*stored_symbols(variables['warehouse'].get())])) if name=='symbol' else ('BAR','TIMER'),\n"
  "                                 state='normal' if name=='symbol' else 'readonly',style='Arrowless.Dashboard.TCombobox')"),
])

patch('event_backtest/bridge.py',[
 ("PipeReceiver(staff,cache,self.adapter,('XAUUSD+','NAS100','BTCUSD'),threading.Event())",
  "PipeReceiver(staff,cache,self.adapter,(),threading.Event())"),
])

patch('generic_backtest/native_mt5.py',[
 ("                      tester_inputs=None,capture_only=False):\n    profile=dict(profile);symbol=str(symbol).strip()",
  "                      tester_inputs=None,capture_only=False,logical_symbol=None):\n"
  "    # symbol = this broker's Tester symbol; logical_symbol = Wire/native identity checked by the EA.\n"
  "    profile=dict(profile);symbol=str(symbol).strip()"),
 ("    request=_write_request(common,session,symbol,start_ns,end_ns)",
  "    request=_write_request(common,session,str(logical_symbol or symbol).strip(),start_ns,end_ns)"),
])

patch('event_backtest/recording.py',[
 ("            gaps=omissions(lines,previous['start'],previous['end'],previous['symbol'])",
  "            gaps=omissions(lines,previous['start'],previous['end'],previous.get('tester_symbol',previous['symbol']))"),
 ("                launch=launch_once_retry(lambda:native.run_native_tester(profile,scenario['symbol'],milliseconds(start)*10**6,milliseconds(end)*10**6,\n"
  "                    work/key,emit=emit,cancel=cancel,shutdown_terminal=True,start_timeout=180,capture_only=True,\n"
  "                    tester_inputs={'InpMode':1,'InpWireVersion':2,'InpRecordingMode':1 if proposed['mode']=='BAR' else 0,\n"
  "                        'InpNativeExport':'false','InpPipeRecording':'true','STAFF_TIMER_MS':scenario['timer_ms']}),profile,emit=emit,cancel=cancel)",
  "                launch=launch_once_retry(lambda:native.run_native_tester(profile,tester,milliseconds(start)*10**6,milliseconds(end)*10**6,\n"
  "                    work/key,emit=emit,cancel=cancel,shutdown_terminal=True,start_timeout=180,capture_only=True,logical_symbol=scenario['symbol'],\n"
  "                    tester_inputs={'InpMode':1,'InpWireVersion':2,'InpRecordingMode':1 if proposed['mode']=='BAR' else 0,\n"
  "                        'InpNativeExport':'false','InpPipeRecording':'true','STAFF_TIMER_MS':scenario['timer_ms'],\n"
  "                        'InpTesterLogicalSymbol':scenario['symbol']}),profile,emit=emit,cancel=cancel)"),
 ("            from .terminal_lifecycle import launch_once_retry",
  "            from .terminal_lifecycle import launch_once_retry\n"
  "            from .settings import tester_symbol\n"
  "            tester=tester_symbol(scenario['symbol'])"),
 ("                gaps=omissions(journal,start,end,scenario['symbol'])\n"
  "                journal_warning=None if any(scenario['symbol'] in line for line in journal) else '선택 종목의 테스터 저널 확인 자료 부족'",
  "                gaps=omissions(journal,start,end,tester)\n"
  "                journal_warning=None if any(tester in line for line in journal) else '선택 종목의 테스터 저널 확인 자료 부족'"),
 ("                data={**identity,**{k:v for k,v in launch.items() if k!='export'},**stored,**calendar,",
  "                data={**identity,**{k:v for k,v in launch.items() if k!='export'},**stored,**calendar,'tester_symbol':tester,"),
])
