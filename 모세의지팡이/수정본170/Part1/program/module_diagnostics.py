"""Host-owned diagnostics: bounded RAM traces and rotating warnings.

No market decision depends on this service. TRACE_ENABLED, TRACE_RING_LINES,
TRACE_WARNING_BYTES and TRACE_WARNING_BACKUPS may be set in config.txt.
"""
from collections import deque
from pathlib import Path
from threading import RLock, Thread
from logging.handlers import RotatingFileHandler
import hashlib
import json
import logging
import re
import time

MODULES = ('STAFF','ENGINE','OZ','SWEEP','FVG','INDICATOR','WATCH','KIM')
DELAY_ALERT_SECONDS = 10    # engine inputs waiting this long: alerts are late, EVENT shows 오류
DELAY_REPORT_SECONDS = 60   # repeat the warning while the delay lasts
ALIASES = {'OZ_STATE':'OZ','FVG_STATE':'FVG','SWEEP_STATE':'SWEEP',
           'TREND':'INDICATOR','WATCH_CONDITIONS':'WATCH','COMPOSER':'ENGINE','SignalOutput':'KIM'}

def module_names():
    from strategy_recipe.registry import list_presets
    return tuple(dict.fromkeys((*MODULES, *list_presets('Part1'))))

def module_name(record, known=None):
    known=module_names() if known is None else known
    explicit=getattr(record,'trace_module',None)
    if explicit:return ALIASES.get(explicit,explicit) if explicit in known or explicit in ALIASES else 'ENGINE'
    name=record.pathname.replace('\\','/').lower()
    stem=name.rsplit('/',1)[-1].rsplit('.',1)[0]
    for module in known:
        if module.lower()==stem:return module
    for token,module in [('staff','STAFF'),('pipe_host','STAFF'),('oz_','OZ'),('sweep','SWEEP'),
                         ('fvg','FVG'),('indicator','INDICATOR'),('watch','WATCH'),('manager_kim','KIM')]:
        if token in name:return module
    return 'ENGINE'

class Diagnostics(logging.Handler):
    def __init__(self,root,config=None,*,trace=None,clock=time.time):
        super().__init__(logging.INFO)
        config=config or {};self.root=Path(root).resolve();self.clock=clock
        self.trace_enabled=(str(config.get('TRACE_ENABLED','true')).lower() not in ('false','0','no')) if trace is None else trace
        self.detail_enabled=False
        self.capacity=max(1,int(config.get('TRACE_RING_LINES',2000)))
        self.max_bytes=max(1024,int(config.get('TRACE_WARNING_BYTES',5*1024*1024)))
        self.backups=max(1,int(config.get('TRACE_WARNING_BACKUPS',3)))
        names=module_names()
        self.lock=RLock();self.buffers={n:deque(maxlen=self.capacity) for n in names}
        self.states={n:dict(status='대기',last=None,errors=0,monitoring=False) for n in names}
        self.started=clock();self.last_staff_input=self.started;self.handlers={};self.symbol_health={}
        self.pipe_connected=False;self.pipe_seen=False
        self.telegram_connected=False;self.telegram_attempted=False
        self.enabled_specials=()
        self.stale=float(config.get('STAFF_STALE_SEC',30));self.disconnect=max(self.stale*2,60)
        self.serial=0;self.backlog=False;self.components={};self.begins={};self.activity={}
        self.lifecycle_state=None
        self.delay_seconds=0.;self._delay_reported=None

    def set_detail(self,enabled):
        with self.lock:self.detail_enabled=bool(enabled) and self.trace_enabled
        if self in logging.getLogger().handlers:
            logging.getLogger().setLevel(logging.INFO if self.detail_enabled else logging.WARNING)

    def _clean(self,text):
        # Exception tracebacks must remain useful after moving the project.
        text=str(text).replace(str(self.root)+'\\','').replace(str(self.root)+'/','')
        return re.sub(r'[A-Za-z]:[\\/][^\s\"\n]+',lambda m:Path(m[0]).name,text)

    def touch(self,module,*,status='정상'):
        module=ALIASES.get(module,module)
        if module not in self.states:return
        with self.lock:
            first=module=='ENGINE' and self.states[module]['last'] is None
            if module=="STAFF":self.last_staff_input=self.clock()
            row=self.states[module];row['last']=self.clock();row['status']='오류·끊김' if any(not ok and ALIASES.get(n,n)==module for n,ok in self.components.items()) else status
        if first:self.log('ENGINE',logging.INFO,'이벤트 처리 시작')

    def health(self,symbol,status):
        with self.lock:
            previous=self.symbol_health.get(symbol)
            self.symbol_health[symbol]=status
            active=[v for v in self.symbol_health.values() if v!='CLOSED']
            value='오류·끊김' if any(v in ('UNAVAILABLE','RECONNECT') for v in active) else '지연' if 'STALE' in active else '정상'
            if previous!=status and status in ('STALE','UNAVAILABLE','RECONNECT'):
                self.log('STAFF',logging.WARNING,'입력 상태 · %s · %s',symbol,status)
            self.states['STAFF']['status']=value

    def pipe_state(self,connected):
        with self.lock:
            if self.pipe_connected==connected:return
            self.pipe_connected=connected
            if connected:self.pipe_seen=True
        self.log('STAFF',logging.INFO,'MT5 Named Pipe %s','연결' if connected else '끊김')

    def telegram_unconfigured(self):
        with self.lock:
            if getattr(self,'_token_missing_reported',False):return
            self._token_missing_reported=True
            self.telegram_attempted=False;self.telegram_connected=False
            self.states['KIM']['status']='대기'
        self.log('KIM',logging.WARNING,'텔레그램 토큰 미설정')

    def telegram_state(self,connected):
        with self.lock:
            changed=not self.telegram_attempted or self.telegram_connected!=bool(connected)
            self.telegram_attempted=True;self.telegram_connected=bool(connected)
            row=self.states['KIM']
            row['status']='정상' if connected else '오류·끊김'
            if connected:row['last']=self.clock()
        if changed:self.log('KIM',logging.INFO if connected else logging.WARNING,
                            '텔레그램 접속 %s','정상' if connected else '실패')

    def configure_specials(self,names):
        selected=tuple(dict.fromkeys(names))
        with self.lock:
            self.enabled_specials=selected
            for name in selected:
                self.buffers.setdefault(name,deque(maxlen=self.capacity))
                self.states.setdefault(name,dict(status='대기',last=None,errors=0,monitoring=False))
                self.states[name]['status']='정상'

    def begin(self,name):
        module=ALIASES.get(name,name)
        if module in self.states:
            with self.lock:self.begins[name]=self.states[module]['errors']

    def success(self,name,event,state=None):
        module=ALIASES.get(name,name)
        if module in self.states:
            with self.lock:self.components[name]=self.states[module]['errors']==self.begins.get(name,self.states[module]['errors'])
        self.touch(name)
        try:self._activity(name,state)
        except Exception:pass  # Diagnostics must never interrupt market dispatch.
        if self.detail_enabled:
            self.log(name,logging.INFO,'처리 완료 · %s · %s · seq=%s',event.kind.value,event.payload.get('symbol',''),event.engine_seq,detail=True)

    def _activity(self,name,state):
        if not isinstance(state,dict):return
        module=ALIASES.get(name,name)
        if name=='OZ_STATE':
            runtime=state.get('runtime')
            if runtime is None:return
            value=(len(runtime.ready_profiles),len(runtime.watch._watches))
            message='OZ 판정 실행 · 준비 프로필 %s개 · 등록 감시 %s건'
        elif name=='WATCH_CONDITIONS':
            runtime=state.get('runtime')
            if runtime is None:return
            value=(len(runtime.controller._watches),)
            message='WATCH 감시 %s건' if value[0] else 'WATCH 등록 감시 없음'
        elif name in ('SWEEP_STATE','FVG_STATE','INDICATOR'):
            if 'watches' not in state:return
            value=(len(state['watches']),)
            message=module+' 감시 %s건' if value[0] else module+' 등록 감시 없음'
        elif name in self.enabled_specials:
            value=('running',);message=name+' 조건 감시 시작'
        else:return
        with self.lock:
            changed=self.activity.get(name)!=value
            if changed:self.activity[name]=value
            self.states[module]['monitoring']=bool(any(value))
            if self.states[module]['status']!='오류·끊김':
                self.states[module]['status']='정상' if any(value) else '대기'
        if not changed:return
        self.log(module,logging.INFO,message,*(value if '%s' in message else ()))

    def engine_delay(self,seconds):
        """How long the input being processed waited (0 when the queue is empty).

        The engine's own warning is written once when a backlog starts; a long
        backlog is reported again every DELAY_REPORT_SECONDS and when it ends.
        """
        with self.lock:
            self.delay_seconds=max(0.,float(seconds));now=self.clock()
            if self.delay_seconds>=DELAY_ALERT_SECONDS:
                if self._delay_reported is not None and now-self._delay_reported<DELAY_REPORT_SECONDS:return
                self._delay_reported=now;message,args='엔진 처리 지연 · %d초 밀림 · 알림도 그만큼 늦습니다',(int(self.delay_seconds),)
            elif self._delay_reported is not None and self.delay_seconds<1:
                self._delay_reported=None;message,args='엔진 처리 지연 해소',()
            else:return
        self.log('ENGINE',logging.WARNING,message,*args)

    def failure(self,name,event,exc):
        with self.lock:self.components[name]=False
        self.log(name,logging.ERROR,'처리 오류 · seq=%s · %s',event.engine_seq,exc,exc_info=(type(exc),exc,exc.__traceback__))

    def log(self,module,level,message,*args,exc_info=None,detail=False):
        if level<logging.WARNING and (not self.trace_enabled or (detail and not self.detail_enabled)):return
        record=logging.LogRecord('modules',level,'',0,message,args,exc_info)
        record.trace_module=module;record.trace_detail=detail;self.handle(record)

    def emit(self,record):
        if record.levelno<logging.WARNING and not self.trace_enabled:return
        module=module_name(record,self.states)
        detail=getattr(record,'trace_detail',record.levelno<logging.WARNING)
        if detail and not self.detail_enabled:return
        try:
            with self.lock:
                row=self.states[module]
                if record.levelno>=logging.WARNING:row['status']='오류·끊김' if record.levelno>=logging.ERROR else '지연'
                if record.levelno>=logging.ERROR:row['errors']+=1
                line=self._clean(logging.Formatter('%(asctime)s [%(levelname)s] %(message)s').format(record))
                if self.trace_enabled or record.levelno>=logging.WARNING:
                    self.serial+=1;self.buffers[module].append((self.serial,line,detail))
                if record.levelno>=logging.WARNING:
                    handler=self.handlers.get(module)
                    if handler is None:
                        folder=self.root/'logs/module_errors';folder.mkdir(parents=True,exist_ok=True)
                        handler=RotatingFileHandler(folder/(module+'.log'),maxBytes=self.max_bytes,backupCount=self.backups,encoding='utf-8')
                        handler.setFormatter(logging.Formatter('%(message)s'));self.handlers[module]=handler
                    clean=logging.LogRecord('modules',record.levelno,'',0,'[%s] %s',(module,line),None)
                    handler.emit(clean)
        except Exception:
            # Diagnostics I/O must not stop market dispatch (e.g. full disk).
            with self.lock:self.states[module]['status']='오류·끊김';self.states[module]['errors']+=1

    def snapshot(self,module='ENGINE',*,modules=None):
        with self.lock:
            states={k:dict(v) for k,v in self.states.items()}
            if self.backlog and states['ENGINE']['status']!='오류·끊김':states['ENGINE']['status']='지연'
            states['ENGINE']['delay_seconds']=round(self.delay_seconds,1)
            row=states['STAFF'];age=self.clock()-self.last_staff_input
            if self.symbol_health and all(v=='CLOSED' for v in self.symbol_health.values()):age=0
            if age>self.disconnect:row['status']='오류·끊김'
            elif age>self.stale and row['status']!='오류·끊김':row['status']='지연'
            selected=tuple(n for n in (modules or self.states) if n in self.states) if module=='ALL' else (module,)
            lines=sorted(((seq,('[%s] '%name if module=='ALL' else '')+line)
                for name in selected for seq,line,detail in self.buffers.get(name,())
                if self.detail_enabled or not detail),key=lambda row:row[0])
            return {'modules':states,'engine_state':self.lifecycle_state() if callable(self.lifecycle_state) else None,
                    'pipe_connected':self.pipe_connected,'pipe_seen':self.pipe_seen,
                    'telegram_connected':self.telegram_connected,'telegram_attempted':self.telegram_attempted,
                    'enabled_specials':self.enabled_specials,
                    'lines':lines,'error_file':'logs/module_errors/'+module+'.log',
                    'trace_enabled':self.trace_enabled,'detail_enabled':self.detail_enabled}

    def close(self):
        with self.lock:
            for handler in self.handlers.values():handler.close()
            self.handlers.clear()
        super().close()

def configure(root,config=None,*,trace=None):
    logger=logging.getLogger()
    for handler in logger.handlers[:]:logger.removeHandler(handler);handler.close()
    logging.disable(logging.NOTSET)
    sink=Diagnostics(root,config,trace=trace)
    logger.setLevel(logging.WARNING);logger.addHandler(sink)
    return sink

def address(root):
    key=hashlib.sha256(str(Path(root).resolve()).casefold().encode()).hexdigest()[:24]
    return r'\\.\pipe\MOSES_DIAGNOSTICS_'+key

class DiagnosticServer:
    def __init__(self,sink):
        from multiprocessing.connection import Listener
        self.sink=sink;self.closed=False
        self.listener=Listener(address(sink.root),family='AF_PIPE')
        self.thread=Thread(target=self._serve,name='module-status-readonly',daemon=True);self.thread.start()
    def _serve(self):
        while not self.closed:
            try:
                with self.listener.accept() as conn:
                    if not conn.poll(1):continue
                    request=json.loads(conn.recv_bytes(512))
                    if isinstance(request,dict):
                        if 'detail' in request:self.sink.set_detail(request['detail'])
                        module=request.get('module','ENGINE');modules=request.get('modules')
                    else:module=request;modules=None
                    if module not in (*self.sink.states,'ALL'):module='ENGINE'
                    conn.send_bytes(json.dumps(self.sink.snapshot(module,modules=modules),ensure_ascii=False).encode())
            except (OSError,EOFError,ValueError):continue
    def close(self):self.closed=True;self.listener.close()

def read_snapshot(root,module,*,detail=None,modules=None):
    from multiprocessing.connection import Client
    with Client(address(root),family='AF_PIPE') as conn:
        request={'module':module}
        if detail is not None:request['detail']=bool(detail)
        if modules is not None:request['modules']=list(modules)
        conn.send_bytes(json.dumps(request).encode())
        if not conn.poll(2):raise TimeoutError('모듈 상태 응답 지연')
        return json.loads(conn.recv_bytes())
