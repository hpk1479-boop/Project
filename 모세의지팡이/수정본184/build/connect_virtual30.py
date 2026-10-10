from pathlib import Path
R=Path(__file__).resolve().parents[1]
def change(rel,fn):
 p=R/rel;s=p.read_bytes().decode('utf-8-sig');n=fn(s);assert n!=s,rel;p.write_bytes(n.encode('utf8'))
def profile(s):return s.replace('            completion_time=cand.completion_time,','            completion_time=cand.completion_time,\n            b0_price=decision.true_b0_price,b0_time=decision.true_b0_time,',1)
change('Part1/program/oz_engine/profile.py',profile)
def controller(s):
 a='        completion_time: Optional[float] = None,'
 assert a in s
 s=s.replace(a,a+'\n        b0_price: Optional[float] = None,\n        b0_time: Optional[int] = None,',1)
 s=s.replace('                    current_price=current_price,','                    current_price=current_price,\n                    b0_price=b0_price,b0_time=b0_time,',1)
 return s
change('Part1/program/oz_engine/controllers.py',controller)
def warehouse(s):
 s=s.replace("'recipient','signal_id')","'recipient','signal_id','b0_price','b0_time')",1)
 needle="        self.db.execute('CREATE TABLE IF NOT EXISTS timings"
 idx=s.index(needle)
 s=s[:idx]+'''        existing={row[1] for row in self.db.execute("PRAGMA table_info('alerts')").fetchall()}
        for name in ('b0_price','b0_time'):
            if name not in existing:self.db.execute('ALTER TABLE alerts ADD COLUMN '+name+' VARCHAR')
'''+s[idx:]
 s=s.replace("'strategy':raw.get('strategy',consumer),", "'b0_price':raw.get('b0_price'),'b0_time':raw.get('b0_time'),\n            'strategy':raw.get('strategy',consumer),",1)
 s=s.replace("'message':c.get('message',''),'recipient':recipient}","'message':c.get('message',''),'recipient':recipient,\n                'b0_price':context.get('b0_price',raw.get('b0_price')),'b0_time':context.get('b0_time',raw.get('b0_time'))}",1)
 return s
change('Part2/event_backtest/warehouse.py',warehouse)
def settings(s):
 s=s.replace("'triggers':{},'commands':[],","'triggers':{},'commands':[],'result_mode':'ALERT_ONLY','spread_points':{},'build_only':False,",1)
 s=s.replace("    if data['mode']=='TIMER':", "    if data['result_mode'] not in ('ALERT_ONLY','VIRTUAL_ENTRY'):raise ValueError('결과 모드: ALERT_ONLY / VIRTUAL_ENTRY')\n    if data['mode']=='TIMER':",1)
 return s
change('Part2/event_backtest/settings.py',settings)
def runner(s):
 s=s.replace("    warnings=list(s['warnings'])","    if s.get('result_mode')=='VIRTUAL_ENTRY':\n        from .virtual_entry import pricing\n        pricing(s,captures,config)\n    warnings=list(s['warnings'])")
 s=s.replace("data={'scenario':", "data={'result_mode':s.get('result_mode','ALERT_ONLY'),'scenario':",1)
 s=s.replace("        catalog.run(run_id,'COMPLETE',data);catalog.close()", "        if s.get('result_mode')=='VIRTUAL_ENTRY':\n            from .virtual_entry import calculate\n            data['virtual_entry']=calculate(export,captures,root,s,config,out,emit=emit)\n        catalog.run(run_id,'COMPLETE',data);catalog.close()")
 return s
change('Part2/event_backtest/runner.py',runner)
p=R/'Part1/program/config.txt';b=p.read_bytes();assert b'POINT_XAUUSD+=' not in b;nl=b'\r\n' if b.count(b'\r\n')>b.count(b'\n')/2 else b'\n';p.write_bytes(b.rstrip(b'\r\n')+nl+b'# Virtual entry point: user confirmed'+nl+b'POINT_XAUUSD+=0.01'+nl)
print('B native B0 metadata, result modes and confirmed point connected')
