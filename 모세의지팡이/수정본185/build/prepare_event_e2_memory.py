from pathlib import Path
root=Path(__file__).resolve().parents[1]/'Part1/program'
p=root/'durable_protocol.py';s=p.read_text('utf-8')
s=s.replace('import hashlib\n','import hashlib\nimport domain_memory\n',1)
s=s.replace('def state_lock(path):\n',"def state_lock(path):\n    if domain_memory.active():return threading.RLock()\n",1)
s=s.replace('def read_json(path):\n',"def read_json(path):\n    if domain_memory.active():return domain_memory.read(path)\n",1)
key='def atomic_json(path, value, *, default=str, allow_nan=False, indent=None, retry_timeout=2.0):\n'
assert key in s;s=s.replace(key,key+"    if domain_memory.active():return domain_memory.write(path,value,default=default,allow_nan=allow_nan,indent=indent)\n",1)
s=s.replace('if not self.path.exists(): return {}','if not domain_memory.exists(self.path): return {}')
p.write_text(s,encoding='utf-8')
for name in ('SPECIAL/SPECIAL4.py','watch_orchestrator.py'):
    p=root/name;s=p.read_text('utf-8')
    s=s.replace('from __future__ import annotations','from __future__ import annotations\nimport domain_memory',1)
    s=s.replace('if not self.state_path.is_file():','if not domain_memory.exists(self.state_path):')
    p.write_text(s,encoding='utf-8')
print('Explicit in-memory legacy state protocol added')
