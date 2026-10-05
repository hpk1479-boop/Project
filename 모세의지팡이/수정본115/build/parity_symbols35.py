"""Offline pipe-vs-replay signal/output validation with mock Telegram only."""
from pathlib import Path
import argparse,collections,gzip,hashlib,io,json,logging,os,sys,threading,time
from types import SimpleNamespace
ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'검증결과/symbol_registry'
parser=argparse.ArgumentParser();parser.add_argument('mode',choices=['live','replay'])
parser.add_argument('case',choices=['synthetic240']);parser.add_argument('--capture')
args=parser.parse_args();sys.dont_write_bytecode=True
sys.path[:0]=[str(ROOT/'Part1/program'),str(ROOT/'Part2')]
from event_backtest.system import deny_network,restrict_writes
deny_network();dest=OUT/(args.case+'_'+args.mode);dest.mkdir(parents=True,exist_ok=True)
restrict_writes(dest);os.environ['MOSES_LOG_DIRECTORY']=str(dest/'logs');logging.disable(logging.CRITICAL)
from event_application import create_event_engine
from event_host import EventHost,load_staff
from event_engine.model import Kind
from event_engine.domain_support import plain
from event_engine.staff_adapter import StaffIngressAdapter
from event_backtest.bridge import CaptureInputs,Collector
from event_engine.capture_io import capture_bundles
from event_pipe_host import PipeReceiver
from event_composer_domain import load_config
from command_interpreter import CommandInterpreter
from staff_golden import scenario
config=load_config(str(ROOT/'Part1/program/config.txt'));symbol='XAUUSD+'
# LIVE: no config symbol/TF lists and no explicit engine symbols (EA-owned supply).
for key in ('STAFF_ALLOWED_SYMBOLS','TARGET_SYMBOLS','ACTIVE_SYMBOLS','STAFF_ALLOWED_TIMEFRAMES'):
    if args.mode=='live':config.pop(key,None)
if args.mode=='replay':config.update(STAFF_ALLOWED_SYMBOLS=symbol,TARGET_SYMBOLS=symbol)
config.update(TELEGRAM_TOKEN='OFFLINE',
              TELEGRAM_CHAT_ID='OFFICIAL',GEMINI_API_KEY='',GEMINI_FALLBACK_ENABLED='false',ECONOMY_ENABLED='false')
engine=create_event_engine(config,symbols=() if args.mode=='live' else (symbol,),selection=['ALL'],backtest=True)
engine.retain_signals=False
sent=[]
def transport(data):
    sent.append(dict(data));return SimpleNamespace(status_code=200,json=lambda:{'ok':True,'result':{'message_id':len(sent)}})
host=EventHost(engine,config,CommandInterpreter(config,ROOT/'Part1/program/command_aliases.json'),transport=transport)
digest=hashlib.sha256();counts=collections.Counter();alerts=[]
def accept(e):
    row={'source_time':e.source_time,**plain(e.payload)}
    digest.update(json.dumps(row,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode());digest.update(b'\n')
    content=row['content'];counts[content.get('type','')]+=1
    if content.get('type')=='NOTIFICATION':alerts.append(row)
    host._signal(e)
engine.signal_sink=accept
start=scenario.START*1000 if args.case=='synthetic240' else next(iter(capture_bundles(args.capture)))[0]
for i,(chat,text) in enumerate(scenario.WATCHES):
    engine.ingress.post(Kind.COMMAND,source='commands',source_seq=i,source_time=start-1,payload={'symbol':symbol,'chat_id':chat,'text':text})
engine.run();host.drain_outputs();assert not engine.error_log,engine.error_log
clock=[0.];staff=load_staff();cache=staff.StaffPipeCache('',health_session='PARITY',monotonic=lambda:clock[0],gap_journal=dest/'gaps.jsonl')
if args.case=='synthetic240':
    from part1_host.wire_v2 import Publisher
    from part1_host.synthetic import TIMEFRAMES
    market=scenario.market();publisher=Publisher(symbol);collector=Collector()
    adapter=StaffIngressAdapter(cache,collector);receiver=PipeReceiver(staff,cache,adapter,(),threading.Event())
    def source():
        for second in range(scenario.START,scenario.START+240):
            raw=publisher.bundle([(tf,market.payload(tf,second)) for tf in TIMEFRAMES]);clock[0]+=.001
            if args.mode=='live':receiver.receive(io.BytesIO(raw).read,lambda x:None,source_time=second*1000)
            else:adapter.publish(raw,source_time=second*1000)
            pending=collector.pending;collector.pending=[];yield from pending
    inputs=source()
else:inputs=CaptureInputs(staff,cache,[args.capture],transport=args.mode,clock=clock,start_ms=start,end_ms=start+239000)
began=time.perf_counter();bundles=0
for item in inputs:
    engine.ingress.post(item.kind,source=item.source,source_seq=item.source_seq,source_time=item.source_time,payload=item.payload)
    engine.run();host.drain_outputs();assert not engine.error_log,plain(engine.error_log)
    bundles+=item.kind==Kind.MARKET_BUNDLE
result={'case':args.case,'mode':args.mode,'bundles':bundles,'signals':sum(counts.values()),'counts':dict(counts),
        'signal_sha256':digest.hexdigest(),'alerts':alerts,'deliveries':host.output.results,'seconds':time.perf_counter()-began}
(dest/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
print(args.case,args.mode,bundles,result['signals'],len(alerts),flush=True)
