"""Focused development scenario. Not a full gate or performance sample."""
import sys,types,socket,logging,json,importlib.util,io
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'Part1/program'),str(ROOT/'Part2')]
def deny(*a,**k):raise AssertionError('E2 network blocked')
socket.socket.connect=deny;socket.socket.connect_ex=deny;socket.socket.sendto=deny
requests=types.ModuleType('requests');requests.Session=deny;requests.get=deny;requests.post=deny
sys.modules['requests']=requests
from event_application import create_event_engine
from event_engine import Kind
from event_engine.domain_support import plain
from event_engine.staff_adapter import StaffIngressAdapter
from staff_golden import scenario
from part1_host.wire_v2 import Publisher
from part1_host.synthetic import TIMEFRAMES
from part1_host.runtime import backtest_config

logging.disable(logging.CRITICAL)
config=backtest_config(ROOT/'Part1',{'STAFF_ALLOWED_SYMBOLS':'XAUUSD+,NAS100,BTCUSD'})
engine=create_event_engine(config,symbols=('XAUUSD+',),collect_timings=False)
spec=importlib.util.spec_from_file_location('e2_probe_staff',ROOT/'Part1/program/THE STAFF OF MOSES.py')
staff=importlib.util.module_from_spec(spec);spec.loader.exec_module(staff)
clock=[scenario.START]
cache=staff.StaffPipeCache('',health_session='E2',monotonic=lambda:clock[0])
adapter=StaffIngressAdapter(cache,engine.ingress)
for chat,text in scenario.WATCHES:
    engine.ingress.post(Kind.COMMAND,source='commands',source_seq=None,source_time=(scenario.START-1)*1000,
        payload={'symbol':'XAUUSD+','chat_id':chat,'text':text})
engine.run()
market=scenario.market();pub=Publisher('XAUUSD+')
for offset in range(int(sys.argv[1]) if len(sys.argv)>1 else 15):
    clock[0]=scenario.START+offset
    raw=pub.bundle([(tf,market.payload(tf,clock[0])) for tf in TIMEFRAMES])
    stream=io.BytesIO(raw);adapter.receive_one(stream.read,source_time=clock[0]*1000)
    engine.run()
    if engine.error_log:break
    if offset%10==0:print('offset',offset,'signals',len(engine.signals),flush=True)
out={'errors':plain(engine.error_log),'signals':[{'source_time':s.source_time,**plain(s.payload)} for s in engine.signals],
     'disabled':list(engine.disabled),'processor_keys':{k:list(v) for k,v in engine.processor_state.items()}}
path=ROOT/'검증결과/event_e2'/('probe_'+sys.argv[2]+'.json' if len(sys.argv)>2 else 'probe.json')
path.write_text(json.dumps(out,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
print('errors',out['errors'],flush=True)
print('notifications',sum(s['content'].get('type')=='NOTIFICATION' for s in out['signals']),flush=True)
assert not engine.error_log
