"""Read frozen polling startup inputs; never start its services or output."""
import sys,types,socket,logging
from event_e2_common import *
def deny(*a,**k):raise AssertionError('startup inventory network forbidden')
socket.socket.connect=deny;socket.socket.connect_ex=deny;socket.socket.sendto=deny
requests=types.ModuleType('requests');requests.post=deny;requests.get=deny;requests.Session=deny
sys.modules['requests']=requests
sys.path.insert(0,str(ROOT/'Part1/program'))
from event_application import load_modules,create_event_engine
from event_startup import load_startup
from event_engine import Kind
logging.disable(logging.CRITICAL)
program=ROOT.parent/'수정본16/Part1/program'
modules=load_modules();startup=load_startup(program,modules)
before={row['path']:row['sha256'] for row in startup.evidence if row['exists']}
engine=create_event_engine(startup.config,modules=modules,aliases=startup.aliases,
    trigger_overrides=startup.triggers,enabled_specials=startup.enabled_specials,
    state_files=startup.state_files,sweep_events=startup.sweep_events)
symbols=tuple(s.strip() for s in startup.config['STAFF_ALLOWED_SYMBOLS'].split(',') if s.strip())
for symbol in symbols:
    engine.ingress.post(Kind.COMMAND,source='offline-startup-probe',source_seq=None,source_time=1790434800000,
        payload={'symbol':symbol,'text':symbol+' 감시 목록','chat_id':'offline-startup-probe'})
engine.run()
after={path:sha(path) for path in before}
result={'inputs':startup.evidence,'enabled_specials':startup.enabled_specials,
        'configured_symbols':symbols,'state_file_count':len(startup.state_files),
        'sweep_history_events':len(startup.sweep_events),'errors':[dict(e) for e in engine.error_log],
        'polling_inputs_unchanged':before==after,'after_sha256':after,'network_blocked':True,
        'signals_generated_not_sent':len(engine.signals)}
write(OUT/'startup_inventory.json',result)
print({k:v for k,v in result.items() if k not in ('inputs','after_sha256')},flush=True)
assert before==after and not engine.error_log
