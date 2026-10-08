import json, subprocess, sys, time
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from lab.ai import gguf_engine
original = subprocess.Popen
root = Path(sys.argv[2])
def spawn(command, **kwargs):
    return original([sys.executable, '-u', str(root / 'fake_server.py'), '--config', str(root / 'fake_config.json'), *command[1:]], **kwargs)
gguf_engine.subprocess.Popen = spawn
engine = gguf_engine.GGUFServer(json.loads(sys.argv[3]), root)
engine.start(time.monotonic() + 8)
(root / 'ready.pid').write_text(str(engine.process.pid))
time.sleep(60)
