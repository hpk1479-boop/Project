from pathlib import Path
P=Path(__file__).resolve().parents[1]/'Part1/program'
p=P/'module_diagnostics.py';s=p.read_text('utf8').replace('self.serial=0','self.serial=0;self.backlog=False')
s=s.replace("            row=states['STAFF'];age=", "            if self.backlog and states['ENGINE']['status']!='오류·끊김':states['ENGINE']['status']='지연'\n            row=states['STAFF'];age=")
p.write_bytes(s.encode('utf8'))
p=P/'event_engine/engine.py';s=p.read_bytes().decode('utf8').replace('                self._pressure = pressure','                self._pressure = pressure\n                if self.diagnostics is not None:self.diagnostics.backlog=pressure');p.write_bytes(s.encode('utf8'))
p=P/'manager_KIM.py';s=p.read_bytes().decode('utf8');s=s.replace('                    if response.status_code < 500 and response.status_code != 429: break','                    if response.status_code < 500 and response.status_code != 429:\n                        logging.error("김매니저 전송 거부 · %s · HTTP %s",signal_id,response.status_code)\n                        break');p.write_bytes(s.encode('utf8'))
