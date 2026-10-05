"""External behavior evidence; one isolated process per mode and scenario."""
import argparse,datetime as dt,importlib.util,io,json,logging,os,socket,sys,types
import hashlib
import atexit,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'Part2'),str(ROOT/'Part1/program')]
def deny(*a,**k):raise AssertionError('E2 validation forbids network')
socket.socket.connect=deny;socket.socket.connect_ex=deny;socket.socket.sendto=deny
from part1_host import runtime,engine as capture_engine
from part1_host.synthetic import SyntheticMarket,TIMEFRAMES
from part1_host.wire_v2 import Publisher
from staff_golden import scenario

def write(path,data):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(data,ensure_ascii=False,indent=2,default=str),encoding='utf-8')

def input_case(name):
    if name=='synthetic240':return scenario.SYMBOL,scenario.START,240,scenario.market(),None
    if name in ('xau1200','btc1200'):
        btc=name=='btc1200';symbol='BTCUSD' if btc else 'XAUUSD+'
        start=int(dt.datetime(2026,9,26 if btc else 24,tzinfo=dt.timezone.utc).timestamp())
        return symbol,start,1200,SyntheticMarket(symbol,start,start+1200,history_days=30,seed=0,pattern=scenario.PATTERN),None
    case='btc_weekend_previous_sigma3_v2' if name=='actualBTC' else 'final_mt5_sigma3_v2'
    folder=ROOT/'검증결과/staff_s7'/case
    info=json.loads((folder/'result.json').read_text('utf-8'))
    return info['symbol'],info['start_s'],info['end_s']-info['start_s'],None,folder/Path(info['retained_export']).name

def wires(symbol,start,window,data,path):
    if data is None:
        from event_engine.capture_io import capture_bundles
        yield from capture_bundles(path)
    else:
        publisher=Publisher(symbol)
        for second in range(start,start+window):
            yield second*1000,publisher.bundle([(tf,data.payload(tf,second)) for tf in TIMEFRAMES])

def main():
    p=argparse.ArgumentParser();p.add_argument('mode',choices=('polling','live','replay'))
    p.add_argument('case');p.add_argument('output');p.add_argument('--seconds',type=int)
    p.add_argument('--measure',action='store_true');p.add_argument('--checkpoint',action='store_true');a=p.parse_args()
    output=ROOT/'검증결과/event_perf1'/a.output
    code_hashes=lambda:{p.relative_to(ROOT/'Part1').as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
                       for p in sorted((ROOT/'Part1/program').rglob('*.py')) if '__pycache__' not in p.parts}
    candidate_hashes=code_hashes()
    # Independent cases can be dispatched in parallel. A batch reaching an
    # already dispatched case waits for that result instead of measuring twice.
    claim=output.with_suffix('.running')
    deadline=time.monotonic()+7200
    while claim.exists():
        if time.monotonic()>deadline:raise TimeoutError('unfinished evidence worker: '+str(claim))
        time.sleep(.25)
    if output.exists():
        prior=json.loads(output.read_text('utf-8'))
        assert prior['mode']==a.mode and prior['case']==a.case
        assert prior['source_hashes']==candidate_hashes and prior.get('source_unchanged_during_run')
        assert not prior.get('errors')
        if a.checkpoint:assert prior.get('checkpoint_equal')
        if a.measure:assert prior.get('measurement',{}).get('samples')==1
        print('REUSE verified completed evidence',a.mode,a.case,flush=True)
        return
    output.parent.mkdir(parents=True,exist_ok=True)
    with claim.open('x',encoding='utf-8') as marker:marker.write(str(os.getpid()))
    atexit.register(lambda:claim.unlink(missing_ok=True))
    symbol,start,window,data,path=input_case(a.case)
    if a.seconds:window=min(window,a.seconds)
    watches=[(chat,text if symbol=='XAUUSD+' else text.replace('골드',symbol)) for chat,text in scenario.WATCHES]
    if a.mode=='polling':
        # Runtime copies the complete frozen Part1 source tree before importing.
        rt=runtime.Part1Runtime(symbols=[symbol],start_epoch=start-1,part1_root=ROOT.parent/'수정본16/Part1',
            specials=[f'SPECIAL{i}' for i in range(1,8)],trigger_overrides={'SPECIAL7':'무지성 올존'},
            config_overrides={'STAFF_ALLOWED_SYMBOLS':symbol,'TARGET_SYMBOLS':symbol},
            work_dir=ROOT/'검증결과/event_perf1',log_level=logging.CRITICAL)
        logging.disable(logging.CRITICAL)
        try:
            for chat,text in watches:rt.manager.handle_command(text,chat)
            rt.start_services()
            feed=capture_engine.SecondFeed(path,symbol) if path else None
            publisher=Publisher(symbol)
            for second in range(start,start+window):
                rt.clock.set(second)
                if feed:
                    feed.advance_to(second)
                    for tf,raw in feed.wires():rt.publish(raw)
                else:
                    raw=publisher.bundle([(tf,data.payload(tf,second)) for tf in TIMEFRAMES]);rt.publish(raw)
                rt.run_until(second+.999)
                if (second-start+1)%60==0:print(a.case,a.mode,second-start+1,flush=True)
            result={'telegram':[(d['virtual_time'],str(d['data'].get('chat_id')),str(d['data'].get('text',''))) for d in rt.http.deliveries],
                    'finals':[v.to_json() for v in rt.alerts],'source_hashes':rt.source_hashes,'loaded_specials':rt.loaded_specials}
        finally:rt.close()
    else:
        requests=types.ModuleType('requests');requests.Session=deny;requests.get=deny;requests.post=deny
        sys.modules['requests']=requests
        os.environ['OZ_SPECIAL_SELECTION_ACTIVE']='1'
        os.environ['OZ_ENABLED_SPECIALS']=','.join(f'SPECIAL{i}' for i in range(1,8))
        os.environ['OZ_SPECIAL_TRIGGERS']=json.dumps({'SPECIAL7':'무지성 올존'},ensure_ascii=False)
        from event_application import create_event_engine
        from event_engine import Kind
        from event_engine.domain_support import plain
        from event_engine.staff_adapter import StaffIngressAdapter
        from event_engine.replay import replay
        config=runtime.backtest_config(ROOT/'Part1',{'STAFF_ALLOWED_SYMBOLS':symbol,'TARGET_SYMBOLS':symbol})
        logging.disable(logging.CRITICAL)
        e=create_event_engine(config,symbols=(symbol,),collect_timings=a.measure)
        spec=importlib.util.spec_from_file_location('e2_validation_staff',ROOT/'Part1/program/THE STAFF OF MOSES.py')
        staff=importlib.util.module_from_spec(spec);spec.loader.exec_module(staff)
        clock=[start];cache=staff.StaffPipeCache('',health_session='E2',monotonic=lambda:clock[0],
            gap_journal=output.with_suffix('.gaps.jsonl'))
        adapter=StaffIngressAdapter(cache,e.ingress)
        resumed=None;resume_offset=0
        for chat,text in watches:
            e.ingress.post(Kind.COMMAND,source='commands',source_seq=None,source_time=(start-1)*1000,
                          payload={'symbol':symbol,'chat_id':chat,'text':text})
        e.run()
        for index,(observed,raw) in enumerate(wires(symbol,start,window,data,path)):
            if observed>=1000*(start+window):break
            clock[0]=observed/1000
            if a.mode=='live':adapter.receive_one(io.BytesIO(raw).read,source_time=observed)
            else:adapter.publish(raw,source_time=observed)
            # Both paths use the same engine; replay never waits on a clock.
            e.run()
            if resumed is not None:resumed.run()
            if e.error_log:break
            if a.checkpoint and resumed is None and index==window//2:
                resumed=create_event_engine(config,symbols=(symbol,));resumed.restore(e.checkpoint())
                resume_offset=len(e.signals)
                class Fanout:
                    def post(self,*args,**kwargs):
                        e.ingress.post(*args,**kwargs);resumed.ingress.post(*args,**kwargs)
                adapter.ingress=Fanout()
            if index%60==59:print(a.case,a.mode,index+1,flush=True)
        result={'signals':[{'source_time':s.source_time,**plain(s.payload)} for s in e.signals],
                'errors':plain(e.error_log),'disabled':list(e.disabled),'bundles':e.metrics.bundle_count,
                'telegram':[(s.source_time/1000,str(recipient),s.payload['content']['message'])
                            for s in e.signals if s.payload['content'].get('type')=='NOTIFICATION'
                            for recipient in s.payload['content']['recipients']]}
        if a.checkpoint:
            signature=lambda events:[(s.source_time,s.payload['signal_id'],plain(s.payload['content'])) for s in events]
            result['checkpoint_equal']=resumed is not None and signature(e.signals[resume_offset:])==signature(resumed.signals) and not resumed.error_log
            result['checkpoint_errors']=plain(resumed.error_log) if resumed is not None else ['checkpoint not reached']
        if a.measure:
            import numpy as np
            times=np.asarray(e.metrics.bundle_ns,dtype=float)/1e6
            result['measurement']={'samples':1,'bundles':len(times),'mean_ms':float(times.mean()),'p99_ms':float(np.percentile(times,99))}
    result.update(case=a.case,mode=a.mode,symbol=symbol,start=start,seconds=window)
    if a.mode!='polling':
        result['source_hashes']=candidate_hashes
        result['source_unchanged_during_run']=candidate_hashes==code_hashes()
        result['loaded_specials']=[f'SPECIAL{i}' for i in range(1,8)]
    write(output,result)
    print('DONE',a.mode,a.case,'notifications',len(result['telegram']),'errors',result.get('errors',[]),flush=True)
    assert not result.get('errors')
    assert result.get('source_unchanged_during_run',True)
    if a.checkpoint:assert result.get('checkpoint_equal'),result.get('checkpoint_errors')

if __name__=='__main__':main()

