from pathlib import Path
P=Path(__file__).resolve().parents[1]/'Part1/program'
p=P/'event_engine/engine.py';s=p.read_bytes().decode('utf8');s=s.replace("        self._internal.append(Input('engine', None, event.source_time, Kind.STRATEGY_ERROR, details, measured_time()))", "        if event.kind != Kind.STRATEGY_ERROR:\n            self._internal.append(Input('engine', None, event.source_time, Kind.STRATEGY_ERROR, details, measured_time()))")
s=s.replace("            if strategy.name in self.disabled or not self._accepts(strategy,event):\n                continue", "            if strategy.name in self.disabled:continue\n            try:\n                accepted=self._accepts(strategy,event)\n            except Exception as exc:\n                self._error(strategy,event,exc);continue\n            if not accepted:continue")
p.write_bytes(s.encode('utf8'))
p=P/'event_host.py';s=p.read_text('utf8').replace('            self.engine.run()','            try:self.engine.run()\n            except Exception:logging.exception("엔진 이벤트 처리 오류; 다음 입력 대기")');p.write_bytes(s.encode('utf8'))
