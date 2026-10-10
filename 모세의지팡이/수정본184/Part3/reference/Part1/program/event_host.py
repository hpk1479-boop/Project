"""Production event host. I/O lives here; domain code never opens a network.

Input receiver, external HTTP worker and output worker only enqueue/dequeue.
Exactly one thread owns engine dispatch. No strategy polling scheduler exists.
"""
import argparse
import importlib.util
import json
import logging
import queue
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from event_engine.model import Kind
from event_engine.staff_adapter import StaffIngressAdapter
from event_engine.domain_support import plain


def load_staff():
    spec = importlib.util.spec_from_file_location('event_staff_runtime', Path(__file__).with_name('THE STAFF OF MOSES.py'))
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


class HostInputs:
    def __init__(self, engine, config, interpreter, output):
        self.engine=engine; self.config=dict(config); self.interpreter=interpreter; self.output=output
        self.allowed={v.strip() for v in str(config.get('TELEGRAM_COMMAND_CHAT_IDS',config.get('TELEGRAM_CHAT_ID',''))).split(',') if v.strip()}
        self.symbols=tuple(s.strip() for s in config.get('STAFF_ALLOWED_SYMBOLS','XAUUSD+,NAS100,BTCUSD').split(',') if s.strip())
        self.interpreter.allowed_symbols_provider=lambda:list(self.symbols)

    def telegram_update(self, update):
        channel='channel_post' in update; message=update.get('channel_post' if channel else 'message') or {}
        chat=message.get('chat') or {}; chat_id=str(chat.get('id',''))
        permitted=(chat_id==str(self.config.get('TELEGRAM_CHAT_ID','')) if channel else
                   chat.get('type')=='private' and chat_id in self.allowed)
        if not permitted or not message.get('text'): return False
        text=str(message['text'])
        reply=(message.get('reply_to_message') or {}).get('message_id')
        symbol=(self.output.reply_symbol(chat_id,reply) or self.interpreter.parse_symbol(text)
                or (self.symbols[0] if self.symbols else ''))
        reply=self.output.logical_reply_id(chat_id,reply)
        # Telegram's date is the input source time; no domain wall-clock read.
        self.engine.ingress.post(Kind.COMMAND,source='telegram',source_seq=update.get('update_id'),
            source_time=int(message['date'])*1000,payload={'symbol':symbol,'text':text,'chat_id':chat_id,
                'message_id':message.get('message_id'),'reply_to_message_id':reply})
        return True

    def external_reply(self, request, canonical, *, received_time):
        pending=getattr(self,'pending_external',{})
        if pending.pop(request.payload['signal_id'],None) is not request:return False
        content=request.payload['content']
        self.engine.ingress.post(Kind.EXTERNAL_REPLY,source='gemini',source_seq=None,source_time=received_time,
            payload={'symbol':request.payload['symbol'],'request_id':request.payload['signal_id'],
                     'command':plain(content['command']),'canonical_text':canonical})
        return True


class HTTPServices:
    def __init__(self,config,interpreter):
        import requests
        self.http=requests;self.config=dict(config);self.interpreter=interpreter
    def send(self,data):
        token=self.config.get('TELEGRAM_TOKEN','')
        return self.http.post(f'https://api.telegram.org/bot{token}/sendMessage',data=data,timeout=10)
    def canonicalize(self,text):
        if str(self.config.get('GEMINI_FALLBACK_ENABLED','true')).lower() in ('false','0','no'): return None
        key=self.config.get('GEMINI_API_KEY','')
        if not key:return None
        model=self.config.get('GEMINI_MODEL','gemini-3.5-flash-lite')
        try:
            response=self.http.post(f'https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent',
                params={'key':key},json={'contents':[{'parts':[{'text':self.interpreter.gemini_prompt(text)}]}]},
                timeout=float(self.config.get('GEMINI_TIMEOUT_SEC','8')))
            raw=response.json()['candidates'][0]['content']['parts'][0]['text']
            return self.interpreter.validate_gemini_reply(text,raw)
        except Exception:
            logging.exception('Gemini command normalization failed');return None
    def telegram_loop(self,inputs,stop):
        token=self.config.get('TELEGRAM_TOKEN','')
        if not token:return
        url=f'https://api.telegram.org/bot{token}/getUpdates'
        offset=None
        try:
            initial=self.http.get(url,params={'offset':-1,'limit':1,'timeout':0},timeout=5).json()
            if initial.get('result'):offset=int(initial['result'][-1]['update_id'])+1
        except Exception:logging.exception('Telegram startup offset failed')
        while not stop.is_set():
            try:
                params={'timeout':10,'allowed_updates':'["message","channel_post"]'}
                if offset is not None:params['offset']=offset
                response=self.http.get(url,params=params,timeout=15)
                payload=response.json()
                if not payload.get('ok'):raise RuntimeError('Telegram input HTTP '+str(response.status_code))
                for update in payload.get('result',[]):
                    inputs.telegram_update(update);offset=int(update['update_id'])+1
            except Exception:
                logging.exception('Telegram input failed');stop.wait(1)


class EventHost:
    def __init__(self,engine,config,interpreter,*,transport,external=None,receipts=None):
        from manager_KIM import SignalOutput
        self.engine=engine;self.config=dict(config);self.stop=threading.Event()
        self.output=SignalOutput(config,transport=transport,receipts=receipts)
        self.inputs=HostInputs(engine,config,interpreter,self.output)
        self.external=external;self.outputs=queue.Queue();self.external_requests=queue.Queue()
        self.pending_external={};self.inputs.pending_external=self.pending_external
        self.delivery_condition=threading.Condition()
        self.engine.signal_sink=self._signal
        self.workers=[]
    def _signal(self,event):
        if event.payload['content'].get('type')=='EXTERNAL_REQUEST':
            self.pending_external[event.payload['signal_id']]=event
            self.external_requests.put(event)
        else:self.outputs.put(event)
    def _deliver(self,event):
        result=self.output.accept(event)
        with self.delivery_condition:self.delivery_condition.notify_all()
        return result
    def drain_outputs(self):
        while True:
            try:event=self.outputs.get_nowait()
            except queue.Empty:break
            try:self._deliver(event)
            finally:self.outputs.task_done()
    def _output_loop(self):
        while True:
            item=self.outputs.get()
            try:
                if item is None:return
                self._deliver(item)
            finally:self.outputs.task_done()
    def _external_loop(self):
        while True:
            event=self.external_requests.get()
            try:
                if event is None:return
                canonical=self.external.canonicalize(event.payload['content']['text']) if self.external else None
                self.inputs.external_reply(event,canonical,received_time=time.time_ns()//1_000_000)
            finally:self.external_requests.task_done()
    def start_io(self):
        for name,fn,args in [('SignalOutput',self._output_loop,()),('ExternalReply',self._external_loop,())]:
            t=threading.Thread(target=fn,args=args,name=name,daemon=True);t.start();self.workers.append(t)
        if self.external:
            t=threading.Thread(target=self.external.telegram_loop,args=(self.inputs,self.stop),name='TelegramInput',daemon=True)
            t.start();self.workers.append(t)
    def run(self):
        while not self.stop.is_set():
            self.engine.ingress.wait(0.5)
            self.engine.run()
    def close(self):
        self.stop.set();self.outputs.put(None);self.external_requests.put(None)
        for worker in self.workers:worker.join(timeout=16)


class EconomyOutputPort:
    """Host notice -> sequenced SIGNAL -> the sole output worker -> receipt."""
    def __init__(self,host):self.host=host
    def send(self,text):
        import hashlib
        now=time.time_ns()//1_000_000
        key=hashlib.sha256(str(text).encode('utf-8')).hexdigest()
        symbol=self.host.inputs.symbols[0] if self.host.inputs.symbols else ''
        payload={'strategy':'ECONOMY_HOST','symbol':symbol,'condition_key':key,'signal_id':'economy:'+key,
                 'content':{'type':'NOTIFICATION','message':text,'recipients':[self.host.config.get('TELEGRAM_CHAT_ID','')]}}
        self.host.engine.ingress.post(Kind.SIGNAL,source='economy_host',source_seq=None,source_time=now,payload=payload)
        receipt=json.dumps([payload['signal_id'],str(self.host.config.get('TELEGRAM_CHAT_ID',''))],ensure_ascii=False,separators=(',',':'))
        with self.host.delivery_condition:
            return self.host.delivery_condition.wait_for(lambda:receipt in self.host.output.receipts,timeout=25)


def main():
    parser=argparse.ArgumentParser(description='MOSES Event Engine')
    parser.add_argument('--program',type=Path,default=Path(__file__).parent)
    parser.add_argument('--state-directory',type=Path)
    args=parser.parse_args()
    from event_startup import create_live_event_engine,export_engine_state,write_event_state_files
    from command_interpreter import CommandInterpreter
    engine=create_live_event_engine(args.program,state_directory=args.state_directory)
    config=engine.startup_config
    state=Path(engine.event_state_directory)
    state.mkdir(parents=True,exist_ok=True)
    logging.basicConfig(level=logging.INFO,handlers=[logging.FileHandler(state/'event_host.log',encoding='utf-8'),logging.StreamHandler()],force=True)
    engine._logger=lambda details:logging.error('STRATEGY_ERROR %s',dict(details))
    interpreter=CommandInterpreter(config,args.program/'command_aliases.json',allowed_symbols_provider=lambda:[s.strip() for s in config.get('STAFF_ALLOWED_SYMBOLS','').split(',') if s.strip()])
    interpreter.command_aliases=engine.startup_aliases
    http=HTTPServices(config,interpreter)
    receipts_path=state/'signal_receipts.json'
    try:receipts=json.loads(receipts_path.read_text('utf-8'))
    except (OSError,ValueError):receipts={}
    host=EventHost(engine,config,interpreter,transport=http.send,external=http,receipts=receipts)
    engine.retain_signals=False
    staff=load_staff()
    cache=staff.StaffPipeCache(config.get('STAFF_PIPE_NAME',''),stale_seconds=float(config.get('STAFF_STALE_SEC','30')),
                              gap_journal=state/'pipe_gaps.jsonl')
    from event_pipe_host import PipeReceiver
    receiver=PipeReceiver(staff,cache,StaffIngressAdapter(cache,engine.ingress),host.inputs.symbols,host.stop)
    host.start_io();receiver.start()
    if str(config.get('ECONOMY_ENABLED','true')).lower() not in ('false','0','no'):
        from event_economy_host import EconomyWorker
        economy=EconomyWorker(config,host.stop,EconomyOutputPort(host),program=args.program,state_directory=state)
        economy.start();host.workers.append(economy)
    try:host.run()
    except KeyboardInterrupt:logging.info('Event Engine stopping')
    finally:
        receiver.close();host.close()
        files=export_engine_state(engine)
        files['signal_receipts.json']=json.dumps(host.output.receipts,ensure_ascii=False)
        write_event_state_files(files,output_directory=state,polling_program=args.program)


if __name__=='__main__':main()
