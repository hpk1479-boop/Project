"""Same offline pipe input: broad WATCH LIVE vs specialized replay WATCH."""
from pathlib import Path
import sys,io,json,threading,os,hashlib
ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'검증결과/stop_virtual_parallel'
sys.path[:0]=[str(ROOT/'Part1/program'),str(ROOT/'Part2')]
from event_backtest.system import deny_network
deny_network();os.environ['MOSES_LOG_DIRECTORY']=str(OUT/'watch_logs')
from event_application import create_event_engine
from event_engine.model import Kind
from event_engine.domain_support import plain
from event_engine.staff_adapter import StaffIngressAdapter
from event_pipe_host import PipeReceiver
from event_host import load_staff
from event_backtest.bridge import Collector
from part1_host.wire_v2 import Publisher
from part1_host.synthetic import TIMEFRAMES
from staff_golden import scenario
from event_watch_selection import specialize
config={'STAFF_ALLOWED_SYMBOLS':'XAUUSD+','TARGET_SYMBOLS':'XAUUSD+','TELEGRAM_TOKEN':'OFFLINE','TELEGRAM_CHAT_ID':'BACKTEST','GEMINI_FALLBACK_ENABLED':'false'}
text='골드 1분 상단 원비 터치 계속 알려줘'
engines=[create_event_engine(config,symbols=('XAUUSD+',),selection=['WATCH'],backtest=True) for _ in range(2)]
for e in engines:
    e.ingress.post(Kind.COMMAND,source='test',source_seq=1,source_time=scenario.START*1000-1,payload={'symbol':'XAUUSD+','strategy':'WATCH','text':text,'chat_id':'BACKTEST'});e.run()
registrations=[s.payload['content']['command'] for s in engines[1].signals if s.payload['content'].get('type')=='WATCH_COMMAND']
assert registrations
selection=specialize(engines[1],registrations)
market=scenario.market();publisher=Publisher('XAUUSD+');staff=load_staff();pipes=[]
for i,e in enumerate(engines):
    clock=[0.];cache=staff.StaffPipeCache('',health_session='SAME',monotonic=lambda c=clock:c[0],gap_journal=OUT/f'watch_{i}_gaps.jsonl')
    adapter=StaffIngressAdapter(cache,e.ingress);receiver=PipeReceiver(staff,cache,adapter,('XAUUSD+',),threading.Event())
    pipes.append((clock,adapter,receiver))
for second in range(scenario.START,scenario.START+240):
    raw=publisher.bundle([(tf,market.payload(tf,second)) for tf in TIMEFRAMES])
    for i,(e,(clock,adapter,receiver)) in enumerate(zip(engines,pipes)):
        clock[0]+=.001
        if i==0:receiver.receive(io.BytesIO(raw).read,lambda x:None,source_time=second*1000)
        else:adapter.publish(raw,source_time=second*1000)
        e.run();assert not e.error_log,plain(e.error_log)
def alerts(e):
    return [{'time':s.source_time,**plain(s.payload)} for s in e.signals if s.payload['content'].get('type')=='NOTIFICATION']
left,right=map(alerts,engines)
result={'identical':left==right,'live_notifications':len(left),'replay_notifications':len(right),'selection':selection,
    'live_consumers':[c.name for c in engines[0].strategies],'replay_consumers':[c.name for c in engines[1].strategies],
    'notifications':right}
(OUT/'watch_parity.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
assert left==right and left
print(len(left),'notifications identical',selection,flush=True)
