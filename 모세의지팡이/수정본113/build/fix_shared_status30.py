from pathlib import Path
P=Path(__file__).resolve().parents[1]/'Part1/program'
p=P/'module_diagnostics.py';s=p.read_text('utf8').replace('self.serial=0;self.backlog=False','self.serial=0;self.backlog=False;self.components={};self.begins={}')
s=s.replace("row['last']=self.clock();row['status']=status", "row['last']=self.clock();row['status']='오류·끊김' if any(not ok and ALIASES.get(n,n)==module for n,ok in self.components.items()) else status")
s=s.replace('    def success(self,name,event):\n        self.touch(name)', '''    def begin(self,name):
        module=ALIASES.get(name,name)
        if module in self.states:
            with self.lock:self.begins[name]=self.states[module]['errors']

    def success(self,name,event):
        module=ALIASES.get(name,name)
        if module in self.states:
            with self.lock:self.components[name]=self.states[module]['errors']==self.begins.get(name,self.states[module]['errors'])
        self.touch(name)''')
s=s.replace('    def failure(self,name,event,exc):\n        self.log', '    def failure(self,name,event,exc):\n        with self.lock:self.components[name]=False\n        self.log')
s=s.replace("                row['status']='오류·끊김' if record.levelno>=logging.ERROR else '지연' if record.levelno>=logging.WARNING else '정상'", "                if record.levelno>=logging.WARNING:row['status']='오류·끊김' if record.levelno>=logging.ERROR else '지연'")
p.write_text(s,encoding='utf8')
p=P/'event_engine/engine.py';s=p.read_bytes().decode('utf8');s=s.replace('                processor.on_event(event, view, self.processor_state[processor.name])','                if self.diagnostics is not None:self.diagnostics.begin(processor.name)\n                processor.on_event(event, view, self.processor_state[processor.name])');s=s.replace('                strategy.on_event(event, view, self.strategy_state[strategy.name],','                if self.diagnostics is not None:self.diagnostics.begin(strategy.name)\n                strategy.on_event(event, view, self.strategy_state[strategy.name],');p.write_bytes(s.encode('utf8'))
