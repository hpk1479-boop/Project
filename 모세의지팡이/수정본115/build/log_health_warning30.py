from pathlib import Path
P=Path(__file__).resolve().parents[1]/'Part1/program'
p=P/'module_diagnostics.py';s=p.read_text('utf8');s=s.replace('self.started=clock();self.handlers={};self.symbol_health={}','self.started=clock();self.last_staff_input=self.started;self.handlers={};self.symbol_health={}')
s=s.replace('            row=self.states[module];', '            if module=="STAFF":self.last_staff_input=self.clock()\n            row=self.states[module];',1)
s=s.replace("            self.symbol_health[symbol]=status", "            previous=self.symbol_health.get(symbol)\n            self.symbol_health[symbol]=status",1)
s=s.replace("            self.states['STAFF']['status']=value", "            self.states['STAFF']['status']=value\n            if previous!=status and status in ('STALE','UNAVAILABLE','RECONNECT'):\n                self.log('STAFF',logging.WARNING,'입력 상태 · %s · %s',symbol,status)",1)
s=s.replace("row=states['STAFF'];age=self.clock()-(row['last'] or self.started)","row=states['STAFF'];age=self.clock()-self.last_staff_input\n            if self.symbol_health and all(v=='CLOSED' for v in self.symbol_health.values()):age=0")
p.write_text(s,encoding='utf8')
p=P/'event_engine/engine.py';s=p.read_bytes().decode('utf8');s=s.replace('                if pressure and not self._pressure:\r\n', '                if pressure and not self._pressure:\r\n                    if self.diagnostics is not None:self.diagnostics.log("ENGINE",30,"엔진 처리 지연 · queue=%s delay_ms=%.1f",self.metrics.queue_length,delay/1e6)\n')
# Some segments of the legacy source use LF; support either without normalizing.
if '엔진 처리 지연' not in s:s=s.replace('                if pressure and not self._pressure:\n','                if pressure and not self._pressure:\n                    if self.diagnostics is not None:self.diagnostics.log("ENGINE",30,"엔진 처리 지연 · queue=%s delay_ms=%.1f",self.metrics.queue_length,delay/1e6)\n')
p.write_bytes(s.encode('utf8'))
