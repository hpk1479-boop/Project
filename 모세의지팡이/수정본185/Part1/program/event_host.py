"""Production event host. I/O lives here; domain code never opens a network.

Input receiver, external HTTP worker and output worker only enqueue/dequeue.
Exactly one thread owns engine dispatch. No strategy polling scheduler exists.
"""
import argparse
import importlib.util
import json
import logging
import queue
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from event_engine.model import Kind
from event_engine.staff_adapter import StaffIngressAdapter
from event_engine.domain_support import plain
from symbol_settings import configured_symbols


def load_staff():
    spec = importlib.util.spec_from_file_location('event_staff_runtime', Path(__file__).with_name('THE STAFF OF MOSES.py'))
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


class HostInputs:
    def __init__(self, engine, config, interpreter, output):
        self.engine=engine; self.config=dict(config); self.interpreter=interpreter; self.output=output
        from telegram_routing import command_chat_ids
        self.allowed=set(command_chat_ids(config))
        # Optional config hint first (keeps the default command symbol), then symbols the EA actually sends.
        self.seed_symbols=configured_symbols(config)
        self.symbol_source=lambda:()
        self.interpreter.allowed_symbols_provider=lambda:list(self.symbols)

    @property
    def symbols(self):
        return tuple(dict.fromkeys((*self.seed_symbols,*self.symbol_source())))

    def telegram_update(self, update):
        # Commands come only from a 1:1 chat on the command list. Messages in groups, supergroups and
        # channels never reach the interpreter, whatever they say.
        message=update.get('message') or {}
        chat=message.get('chat') or {}; chat_id=str(chat.get('id',''))
        if chat.get('type')!='private' or chat_id not in self.allowed or not message.get('text'): return False
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
    def __init__(self,config,interpreter,diagnostics=None):
        import requests
        self.http=requests;self.config=dict(config);self.interpreter=interpreter
        self.diagnostics=diagnostics
        self.ai=None
        self.ai_lock=threading.Lock();self.ai_closed=False
    def send(self,data):
        token=self.config.get('TELEGRAM_TOKEN','')
        return self.http.post(f'https://api.telegram.org/bot{token}/sendMessage',data=data,timeout=10)
    def canonicalize(self,text):
        try:
            with self.ai_lock:
                if self.ai_closed:return None
            root=Path(__file__).resolve().parents[2]
            if str(root) not in sys.path:sys.path.insert(0,str(root))
            from common_ai.settings import read_settings
            if read_settings(root).get('watch_enabled',True) is False:return None
            with self.ai_lock:
                if self.ai_closed:return None
                if self.ai is None:
                    from common_ai.client import Client
                    self.ai=Client(root)
                client=self.ai
            response=client.chat(
                [{'role':'user','content':self.interpreter.gemini_prompt(text)}],tools=[],
                response_schema={'type':'object','properties':{'canonical_text':{'type':'string'}},
                                 'required':['canonical_text'],'additionalProperties':False},role='watch')
            with self.ai_lock:
                if self.ai_closed:return None
            if response.get('tool_calls'):raise ValueError('WATCH normalization cannot execute tools')
            raw=response.get('content')
            if not isinstance(raw,str):raise ValueError('WATCH normalization requires JSON text')
            return self.interpreter.validate_gemini_reply(text,raw)
        except Exception as exc:
            with self.ai_lock:
                if self.ai_closed:return None
            logging.warning('WATCH AI 문장 정규화에 실패했습니다. AI 설정 및 실행 로그를 확인하세요. (%s)',
                            type(exc).__name__,extra={'trace_module':'WATCH'})
            return None
    def close(self):
        with self.ai_lock:
            self.ai_closed=True
            client,self.ai=self.ai,None
        if client is not None:client.close()
    def telegram_loop(self,inputs,stop):
        token=str(self.config.get('TELEGRAM_TOKEN','')).strip()
        if not token:
            if self.diagnostics is not None:self.diagnostics.telegram_unconfigured()
            else:logging.warning('텔레그램 토큰 미설정',extra={'trace_module':'KIM'})
            return
        url=f'https://api.telegram.org/bot{token}/getUpdates'
        offset=None
        try:
            response=self.http.get(url,params={'offset':-1,'limit':1,'timeout':0},timeout=5)
            initial=response.json()
            if response.status_code!=200 or not initial.get('ok'):
                raise RuntimeError('Telegram input HTTP '+str(response.status_code))
            if self.diagnostics is not None:self.diagnostics.telegram_state(True)
            if initial.get('result'):offset=int(initial['result'][-1]['update_id'])+1
        except Exception:
            if self.diagnostics is not None:self.diagnostics.telegram_state(False)
            logging.exception('Telegram startup offset failed',extra={'trace_module':'KIM'})
        while not stop.is_set():
            try:
                params={'timeout':10,'allowed_updates':'["message"]'}
                if offset is not None:params['offset']=offset
                response=self.http.get(url,params=params,timeout=15)
                payload=response.json()
                if response.status_code!=200 or not payload.get('ok'):
                    raise RuntimeError('Telegram input HTTP '+str(response.status_code))
                if self.diagnostics is not None:self.diagnostics.telegram_state(True)
            except Exception:
                if self.diagnostics is not None:self.diagnostics.telegram_state(False)
                logging.exception('Telegram input failed',extra={'trace_module':'KIM'});stop.wait(1)
                continue
            try:
                for update in payload.get('result',[]):
                    inputs.telegram_update(update);offset=int(update['update_id'])+1
            except Exception:
                logging.exception('Telegram update processing failed',extra={'trace_module':'KIM'});stop.wait(1)


class EventHost:
    def __init__(self,engine,config,interpreter,*,transport,external=None,receipts=None,diagnostics=None,recorder=None,stop=None):
        from manager_KIM import SignalOutput
        self.engine=engine;self.config=dict(config);self.stop=stop if stop is not None else threading.Event()
        self.diagnostics=diagnostics
        self.recorder=recorder
        self.output=SignalOutput(config,transport=transport,receipts=receipts,result_observer=recorder.record if recorder else None)
        if recorder is not None:recorder.bind(engine)
        self.inputs=HostInputs(engine,config,interpreter,self.output)
        self.external=external;self.outputs=queue.Queue();self.external_requests=queue.Queue()
        self.pending_external={};self.inputs.pending_external=self.pending_external
        self.delivery_condition=threading.Condition()
        # signal_id -> whether its delivery attempt has ended, for notices whose producer waits (EconomyOutputPort).
        self.notice_attempts={}
        self.engine.signal_sink=self._signal
        self.workers=[]
        self._output_worker=None;self._external_worker=None
        self._worker_stops_sent=False
    def _signal(self,event):
        if event.payload['content'].get('type')=='EXTERNAL_REQUEST':
            self.pending_external[event.payload['signal_id']]=event
            self.external_requests.put(event)
        else:self.outputs.put(event)
    def _deliver(self,event):
        try:return self._deliver_recorded(event)
        finally:
            try:
                if self.recorder is not None:self.recorder.release(event)
            finally:self._attempted(event)
    def _attempted(self,event):
        signal_id=(getattr(event,'payload',None) or {}).get('signal_id')
        if signal_id not in self.notice_attempts:return
        with self.delivery_condition:
            if signal_id in self.notice_attempts:
                self.notice_attempts[signal_id]=True;self.delivery_condition.notify_all()
    def delivering(self):
        """While stopping, whether a queued notice can still reach the output (수정본164)."""
        worker=self._output_worker
        return worker.is_alive() if worker is not None else not self._worker_stops_sent
    def _deliver_recorded(self,event):
        if self.external is not None and isinstance(self.external,HTTPServices) and not str(self.config.get('TELEGRAM_TOKEN','')).strip():
            if self.diagnostics is not None:self.diagnostics.telegram_unconfigured()
            self.output.record_skipped(event,'토큰 미설정')
            return ()
        before=len(self.output.results)
        result=self.output.accept(event)
        if result and self.recorder is not None:self.recorder.mark_summary_sent(event)
        if self.diagnostics is not None and len(self.output.results)>before:
            self.diagnostics.log('KIM',20,'알림 전송 완료 · %s · 수신자 %s명',
                event.payload.get('symbol',''),len(self.output.results)-before)
        with self.delivery_condition:self.delivery_condition.notify_all()
        return result
    def drain_outputs(self):
        while True:
            try:event=self.outputs.get_nowait()
            except queue.Empty:break
            try:self._deliver(event)
            except Exception:logging.exception("김매니저 출력 처리 오류",extra={"trace_module":"KIM"})
            finally:self.outputs.task_done()
    def _output_loop(self):
        self._output_worker=threading.current_thread()
        while True:
            summary_available=not isinstance(self.external,HTTPServices) or bool(str(self.config.get('TELEGRAM_TOKEN','')).strip())
            if self.recorder is not None and not self.stop.is_set() and summary_available:
                try:
                    import datetime as dt
                    summary=self.recorder.summary_due(dt.datetime.now(dt.timezone.utc))
                    if summary is not None:self._deliver(summary)
                except Exception:logging.exception('LIVE 일일 요약 처리 오류',extra={'trace_module':'KIM'})
            try:item=self.outputs.get(timeout=1 if self.recorder is not None else None)
            except queue.Empty:continue
            try:
                if item is None:return
                self._deliver(item)
            except Exception:logging.exception("김매니저 출력 처리 오류",extra={"trace_module":"KIM"})
            finally:self.outputs.task_done()
    def _external_loop(self):
        self._external_worker=threading.current_thread()
        while True:
            event=self.external_requests.get()
            try:
                if event is None:return
                self._resolve_external(event)
            except Exception:logging.exception("외부 응답 처리 오류")
            finally:self.external_requests.task_done()
    def _resolve_external(self,event):
        canonical=self.external.canonicalize(event.payload['content']['text']) if self.external else None
        self.inputs.external_reply(event,canonical,received_time=time.time_ns()//1_000_000)
    def start_io(self):
        for name,fn,args in [('SignalOutput',self._output_loop,()),('ExternalReply',self._external_loop,())]:
            t=threading.Thread(target=fn,args=args,name=name,daemon=True)
            if name=='SignalOutput':self._output_worker=t
            else:self._external_worker=t
            t.start();self.workers.append(t)
        if self.external:
            t=threading.Thread(target=self.external.telegram_loop,args=(self.inputs,self.stop),name='TelegramInput',daemon=True)
            t.start();self.workers.append(t)
    def run(self):
        while not self.stop.is_set():
            self.engine.ingress.wait(0.5)
            try:self.engine.run()
            except Exception:logging.exception("엔진 이벤트 처리 오류; 다음 입력 대기")
    def close(self,timeout=30):
        """Called on the engine owner thread after the pipe producer stops.

        Input workers may publish a final event while stopping. Keep the engine
        and output worker available until producers and external replies finish;
        economy input can itself be waiting for an output receipt.
        """
        self.stop.set()
        with self.delivery_condition:self.delivery_condition.notify_all()
        deadline=time.monotonic()+max(0,timeout)
        close=getattr(getattr(self,'external',None),'close',None)
        if callable(close):close()
        drained=True
        while not self._worker_stops_sent:
            try:
                if len(self.engine.ingress):self.engine.run()
                if self.engine._internal:
                    drained=False;break
                if self._external_worker is None:
                    while True:
                        try:event=self.external_requests.get_nowait()
                        except queue.Empty:break
                        try:self._resolve_external(event)
                        finally:self.external_requests.task_done()
                if self._output_worker is None:self.drain_outputs()
            except Exception:
                logging.exception('종료 중 접수 이벤트 처리 실패')
                drained=False;break
            producers=[worker for worker in self.workers
                       if worker not in (self._output_worker,self._external_worker) and worker.is_alive()]
            if not producers and not self.external_requests.unfinished_tasks and not len(self.engine.ingress):
                break
            remaining=deadline-time.monotonic()
            if remaining<=0:
                drained=False;break
            if producers:producers[0].join(timeout=min(.02,remaining))
            else:
                with self.external_requests.all_tasks_done:
                    if self.external_requests.unfinished_tasks:
                        self.external_requests.all_tasks_done.wait(timeout=min(.02,remaining))
        if not self._worker_stops_sent:
            if self._external_worker is not None:self.external_requests.put(None)
            if self._output_worker is not None:self.outputs.put(None)
            self._worker_stops_sent=True
        for worker in self.workers:worker.join(timeout=max(0,deadline-time.monotonic()))
        return (drained and not any(worker.is_alive() for worker in self.workers)
                and not len(self.engine.ingress) and not self.engine._internal
                and not self.outputs.unfinished_tasks and not self.external_requests.unfinished_tasks
                and not self.pending_external)


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
        host=self.host;signal_id=payload['signal_id']
        # Registered before posting, so the end of its delivery attempt cannot be missed.
        with host.delivery_condition:host.notice_attempts[signal_id]=False
        try:
            host.engine.ingress.post(Kind.SIGNAL,source='economy_host',source_seq=None,source_time=now,payload=payload)
            # Delivered = the alert room got it; without an alert room, any 1:1 command chat that opted in.
            from telegram_routing import private_alert_chats
            room=str(host.config.get('TELEGRAM_CHAT_ID',''))
            targets=[room] if room else list(private_alert_chats(host.config,'economy')) or [room]
            receipts=[json.dumps([signal_id,chat],ensure_ascii=False,separators=(',',':')) for chat in targets]
            delivered=lambda:any(receipt in host.output.receipts for receipt in receipts)
            # Stopping ends the wait only once this notice's delivery attempt has ended or nothing can deliver it:
            # close() keeps the engine and the output worker running for producers, and a notice reported unsent
            # after it was delivered would be sent again after a restart (수정본164).
            ended=lambda:host.notice_attempts.get(signal_id) or not host.delivering()
            deadline=time.monotonic()+25
            with host.delivery_condition:
                while not delivered() and not (host.stop.is_set() and ended()):
                    remaining=deadline-time.monotonic()
                    if remaining<=0:break
                    # A worker's exit is not notified, so a stopping wait looks again shortly.
                    host.delivery_condition.wait(min(remaining,.1) if host.stop.is_set() else remaining)
                return delivered()
        finally:
            with host.delivery_condition:host.notice_attempts.pop(signal_id,None)


def special_file_notice(config,notices,now_ms):
    """SPECIAL files left out at start, one line each; sent once per engine start, not a strategy alert."""
    return SimpleNamespace(kind=Kind.SIGNAL,source_time=now_ms,payload={
        'signal_id':'special-files:'+str(now_ms),'strategy':'HOST_NOTICE','symbol':'',
        'content':{'type':'NOTIFICATION','message':'\n'.join('[라이브 시작] '+line for line in notices),
                   'recipients':[str(config.get('TELEGRAM_CHAT_ID',''))]}})


def announce_skipped_specials(host,diagnostics,now_ms=None):
    """Engine log line and one queued Telegram notice for the SPECIAL files this start left out."""
    from strategy_recipe.registry import skipped_builtins
    notices=[row['notice'] for row in skipped_builtins()]
    for line in notices:diagnostics.log('ENGINE',logging.INFO,'%s',line)
    if notices:host.outputs.put(special_file_notice(host.config,notices,time.time_ns()//1_000_000 if now_ms is None else now_ms))
    return notices


def main(argv=None):
    parser=argparse.ArgumentParser(description='MOSES Event Engine')
    parser.add_argument('--program',type=Path,default=Path(__file__).parent)
    parser.add_argument('--state-directory',type=Path)
    args=parser.parse_args(argv)
    from event_startup import create_live_event_engine,export_engine_state,write_event_state_files
    from command_interpreter import CommandInterpreter
    from module_diagnostics import configure,DiagnosticServer
    from event_composer_domain import load_config
    from event_lifecycle import HostLifecycle
    stop=threading.Event()
    lifecycle=HostLifecycle(Path(__file__).resolve().parent.parent,stop)
    diagnostics=status_server=engine=host=receiver=None
    saved=False
    try:
        lifecycle.start()
        diagnostics=configure(args.program.parent,load_config(str(args.program/"config.txt")))
        diagnostics.lifecycle_state=lambda:lifecycle.row['state']
        # LIVE alerts take CPU before backtests or other work on the same PC.
        from process_priority import prefer_live,mark_live_running
        priority=prefer_live()
        if priority is not None and not all(priority.values()):
            diagnostics.log('ENGINE',logging.WARNING,'라이브 우선순위 설정 실패 · 다른 작업과 CPU를 같은 순서로 나눕니다')
        # Backtests on this PC see the mark and run below normal while it is held (수정본167).
        if mark_live_running() is False:
            diagnostics.log('ENGINE',logging.WARNING,'라이브 실행 표시 실패 · 백테스트가 보통 우선순위로 돌 수 있습니다')
        engine=create_live_event_engine(args.program,state_directory=args.state_directory,set_aside_old_state=True)
        if getattr(engine,'retired_state_directory',None):
            diagnostics.log('ENGINE',logging.WARNING,'봉 시각 기준이 실제 시각으로 바뀌어 이전 라이브 상태를 %s 폴더로 옮겼습니다 · '
                            '텔레그램으로 등록한 개인 감시는 다시 등록하세요',Path(engine.retired_state_directory).name)
        config=engine.startup_config
        # MT5 bar times are the broker's server clock; the engine reads them in real time (수정본172).
        import server_time
        broker_clock=server_time.activate(server_time.ServerTime.from_config(config))
        state=Path(engine.event_state_directory)
        state.mkdir(parents=True,exist_ok=True)
        engine.diagnostics=diagnostics
        diagnostics.configure_specials(engine.startup_enabled_specials)
        status_server=DiagnosticServer(diagnostics)
        from command_interpreter import resolve_command_language_path
        interpreter=CommandInterpreter(config,resolve_command_language_path(config,args.program),allowed_symbols_provider=lambda:list(configured_symbols(config)))
        interpreter.command_aliases=engine.startup_aliases
        http=HTTPServices(config,interpreter,diagnostics=diagnostics)
        receipts_path=state/'signal_receipts.json'
        try:receipts=json.loads(receipts_path.read_text('utf-8'))
        except (OSError,ValueError):receipts={}
        from live_alert_recording import LiveAlertRecorder
        recorder=LiveAlertRecorder(config,program=args.program)
        host=EventHost(engine,config,interpreter,transport=http.send,external=http,receipts=receipts,diagnostics=diagnostics,recorder=recorder,stop=stop)
        engine.retain_signals=False
        announce_skipped_specials(host,diagnostics)
        staff=load_staff()
        cache=staff.StaffPipeCache(config.get('STAFF_PIPE_NAME',''),stale_seconds=float(config.get('STAFF_STALE_SEC','30')),
                                  gap_journal=state/'pipe_gaps.jsonl')
        from event_pipe_host import PipeReceiver
        def clock_differs(symbol,hours,expected):
            diagnostics.log('STAFF',logging.WARNING,'%s 브로커 서버 시각이 설정과 다릅니다 · MT5 서버 UTC%+d · 설정 UTC%+d (%s) · '
                            '설정의 브로커 서버 시간을 확인하세요',symbol,hours,expected,broker_clock.label())
        adapter=StaffIngressAdapter(cache,engine.ingress,server_time=broker_clock,on_clock=clock_differs)
        receiver=PipeReceiver(staff,cache,adapter,host.inputs.seed_symbols,stop,diagnostics=diagnostics,
                              on_bound=lifecycle.pipe_bound,on_connected=lifecycle.pipe_connected,on_fatal=lifecycle.fail)
        host.inputs.symbol_source=lambda:receiver.symbols
        if not stop.is_set():
            host.start_io();receiver.start()
            if not stop.is_set() and str(config.get('ECONOMY_ENABLED','true')).lower() not in ('false','0','no'):
                from event_economy_host import EconomyWorker
                economy=EconomyWorker(config,stop,EconomyOutputPort(host),state_directory=state)
                economy.start();host.workers.append(economy)
            lifecycle.services_started()
            host.run()
    except KeyboardInterrupt:
        logging.info('Event Engine stopping')
    except Exception as exc:
        logging.exception('Event Engine 시작·실행 실패')
        lifecycle.fail('엔진 시작·실행 실패: '+str(exc))
    finally:
        lifecycle.begin_stopping()
        clean=True
        for component in (receiver,host):
            if component is None:continue
            try:
                if component.close() is False:
                    clean=False
                    lifecycle.fail('종료 대기 시간 안에 입출력 작업이 끝나지 않아 상태 저장을 완료하지 못했습니다.')
            except Exception as exc:
                clean=False
                logging.exception('Event Engine 입출력 종료 실패')
                lifecycle.fail('엔진 입출력 종료 실패: '+str(exc))
        if clean and engine is not None and host is not None:
            try:
                files=export_engine_state(engine)
                files['signal_receipts.json']=json.dumps(host.output.receipts,ensure_ascii=False)
                write_event_state_files(files,output_directory=state,polling_program=args.program)
                saved=True
            except Exception as exc:
                logging.exception('Event Engine 상태 저장 실패')
                lifecycle.fail('엔진 상태 저장 실패: '+str(exc))
        for component in (status_server,diagnostics):
            if component is None:continue
            try:component.close()
            except Exception as exc:
                lifecycle.fail('엔진 진단 서비스 종료 실패: '+str(exc))
        lifecycle.finish(saved)
        lifecycle.close()
    return 0 if saved and lifecycle.row['state']=='stopped' else 1


if __name__=='__main__':raise SystemExit(main())
