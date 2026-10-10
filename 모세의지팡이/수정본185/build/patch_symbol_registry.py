"""Byte-exact edits for the EA-owned symbol registry (keeps mixed CRLF/LF)."""
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];P=ROOT/'Part1/program'

def patch(name,pairs):
    path=P/name;data=path.read_bytes()
    for old,new in pairs:
        old=old.encode('utf-8');new=new.encode('utf-8')
        assert data.count(old)==1,(name,old[:60])
        data=data.replace(old,new)
    path.write_bytes(data)

def nl(name,marker):
    data=(P/name).read_bytes();i=data.index(marker.encode('utf-8'))
    end=data.index(b'\n',i)
    return '\r\n' if data[end-1:end]==b'\r' else '\n'

e=nl('event_pipe_host.py','import staff_schema as wire')
patch('event_pipe_host.py',[
 ('import staff_schema as wire'+e,
  'import re'+e+'import staff_schema as wire'+e+e+e+
  '# The EA owns the symbol list. The pipe accepts any well-formed symbol'+e+
  '# instead of a config allowlist; CSV/state files cannot hold commas or spaces.'+e+
  "_WIRE_SYMBOL=re.compile(r'[^\\s,\\x00-\\x1f\\x7f]{1,64}')"+e+e+e+
  'class WireSymbolRule:'+e+
  '    def __contains__(self,symbol):'+e+
  '        return isinstance(symbol,str) and _WIRE_SYMBOL.fullmatch(symbol) is not None'+e+e+e+
  'WIRE_SYMBOL_RULE=WireSymbolRule()'+e),
 ('self.adapter=adapter;self.symbols=tuple(symbols);self.stop=stop',
  'self.adapter=adapter;self.seed_symbols=tuple(symbols);self.stop=stop'),
 ('self.last_status={};self.last_source={};self._state_lock=threading.Lock()',
  'self.last_status={};self.last_source={};self._state_lock=threading.Lock()'+nl('event_pipe_host.py','self._state_lock=threading.Lock()')+
  '        self._observed=()'+nl('event_pipe_host.py','self._state_lock=threading.Lock()')+
  '    @property'+nl('event_pipe_host.py','self._state_lock=threading.Lock()')+
  '    def symbols(self):'+nl('event_pipe_host.py','self._state_lock=threading.Lock()')+
  '        return tuple(dict.fromkeys((*self.seed_symbols,*self._observed)))'+nl('event_pipe_host.py','self._state_lock=threading.Lock()')+
  '    def _observe(self):'+nl('event_pipe_host.py','self._state_lock=threading.Lock()')+
  '        # Only validated publications reach the cache, so its keys are what the EA really sent.'+nl('event_pipe_host.py','self._state_lock=threading.Lock()')+
  '        seen=tuple(dict.fromkeys((*self._observed,*(symbol for symbol,_ in self.cache.keys()))))'+nl('event_pipe_host.py','self._state_lock=threading.Lock()')+
  '        if seen!=self._observed:self._observed=seen'),
 ('        # The canonical STAFF boundary decodes, validates the allowlist and',
  '        # The canonical STAFF boundary decodes, validates the symbol form and'),
 ('reply=self.adapter.receive_one(read,source_time=source_time,allowed_symbols=self.symbols)',
  'reply=self.adapter.receive_one(read,source_time=source_time,allowed_symbols=WIRE_SYMBOL_RULE)'+
  nl('event_pipe_host.py','allowed_symbols=self.symbols)')+'        self._observe()'),
])

e=nl('event_host.py','self.symbols=tuple(s.strip()')
patch('event_host.py',[
 ("        self.symbols=tuple(s.strip() for s in config.get('STAFF_ALLOWED_SYMBOLS','XAUUSD+,NAS100,BTCUSD').split(',') if s.strip())"+e+
  "        self.interpreter.allowed_symbols_provider=lambda:list(self.symbols)"+e,
  "        # Optional config hint first (keeps the default command symbol), then symbols the EA actually sends."+e+
  "        self.seed_symbols=tuple(s.strip() for s in config.get('STAFF_ALLOWED_SYMBOLS','').split(',') if s.strip())"+e+
  "        self.symbol_source=lambda:()"+e+
  "        self.interpreter.allowed_symbols_provider=lambda:list(self.symbols)"+e+e+
  "    @property"+e+
  "    def symbols(self):"+e+
  "        return tuple(dict.fromkeys((*self.seed_symbols,*self.symbol_source())))"+e),
 ("StaffIngressAdapter(cache,engine.ingress),host.inputs.symbols,host.stop,diagnostics=diagnostics)"+e,
  "StaffIngressAdapter(cache,engine.ingress),host.inputs.seed_symbols,host.stop,diagnostics=diagnostics)"+e+
  "    host.inputs.symbol_source=lambda:receiver.symbols"+e),
])

e=nl('event_application.py',"    symbols=tuple(symbols or (s.strip()")
e2=nl('event_application.py',"'XAUUSD+,NAS100,BTCUSD').split(',') if s.strip()))")
patch('event_application.py',[
 ("    symbols=tuple(symbols or (s.strip() for s in config.get('STAFF_ALLOWED_SYMBOLS',"+e+
  "                           'XAUUSD+,NAS100,BTCUSD').split(',') if s.strip()))"+e2,
  "    # Empty = every symbol the EA sends (Subscriptions wildcard). Replays pass their symbol."+e+
  "    symbols=tuple(symbols or ())"+e2),
])

patch('config.txt',[
 ('STAFF_ALLOWED_SYMBOLS=',
  '# 심볼/TF 공급 목록은 MT5 THE_STAFF_OF_MOSES EA가 소유합니다. 아래 세 키는 LIVE 입력을 제한하지 않습니다.\n'
  '# STAFF_ALLOWED_SYMBOLS는 텔레그램 기본 심볼·Health 표시 순서의 힌트로만 쓰이며, 없어도 됩니다.\n'
  'STAFF_ALLOWED_SYMBOLS='),
])
print('patched')
