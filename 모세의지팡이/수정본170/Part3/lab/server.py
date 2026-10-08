"""외부 의존성이 없는 로컬 UI 서버(127.0.0.1 전용).

모든 API는 매 실행 새로 만든 토큰을 요구합니다. 다른 웹사이트가 로컬
생성/실행 API를 호출하지 못하게 Origin과 토큰을 함께 검사합니다.
원본 소스 읽기/편집은 Python 코드 실행과 분리되어 있습니다.
"""
from __future__ import annotations
from http.server import ThreadingHTTPServer,BaseHTTPRequestHandler
import json
import mimetypes
from pathlib import Path
import secrets
import threading
from urllib.parse import urlparse,parse_qs
from . import catalog,storage,integration
from .ai.provider import configured_settings

ROOT=catalog.ROOT
AI_SETTINGS=ROOT.parent/'settings/ai_settings.json'
AI_SESSIONS:dict={}
BACKTEST_COMMAND_SESSIONS:dict={}
RESEARCH_SESSIONS:dict={}
_AI_LOCK=threading.Lock()
_AI_CLIENT=None
_AI_SERVER_COUNT=0


def _shared_provider(settings, *, role='strategy'):
    """Caller holds _AI_LOCK; session reset preserves the UI's model cache."""
    global _AI_CLIENT
    from common_ai.client import Client
    from .ai.shared_provider import shared_from_settings
    root=ROOT.parent.resolve()
    if _AI_CLIENT is None or _AI_CLIENT.root!=root:
        if _AI_CLIENT is not None:_AI_CLIENT.close()
        _AI_CLIENT=Client(root)
    return shared_from_settings(settings,role=role,root=root,
                                client=_AI_CLIENT,allow_incomplete=True)


def _detach_ai_client():
    """Caller holds _AI_LOCK so a new session cannot borrow a closing client."""
    global _AI_CLIENT
    client,_AI_CLIENT=_AI_CLIENT,None
    AI_SESSIONS.clear();BACKTEST_COMMAND_SESSIONS.clear();RESEARCH_SESSIONS.clear()
    return client


def close_ai_client():
    with _AI_LOCK:
        client=_detach_ai_client()
    if client is not None:client.close()

def ai_settings()->dict:
    if AI_SETTINGS == ROOT.parent/'settings/ai_settings.json':
        from common_ai.settings import read_settings
        return read_settings(ROOT.parent)
    data=json.loads(AI_SETTINGS.read_text('utf-8')) if AI_SETTINGS.is_file() else {}
    return configured_settings(data)

def ai_agent(session:str):
    from .ai.agent import Agent
    with _AI_LOCK:
        if session not in AI_SESSIONS:AI_SESSIONS[session]=Agent(_shared_provider(ai_settings()))
        return AI_SESSIONS[session]

def backtest_command_session(session:str):
    from .ai.backtest_commands import Session
    if len(session)>128:raise ValueError('대화 ID가 너무 깁니다.')
    with _AI_LOCK:
        if session not in BACKTEST_COMMAND_SESSIONS:
            BACKTEST_COMMAND_SESSIONS[session]=Session(_shared_provider(ai_settings(),role='backtest'))
        return BACKTEST_COMMAND_SESSIONS[session]

def apply_strategy(session, revision):
    agent=ai_agent(session)
    with agent.lock:
        recipe=agent.apply(revision)
        preview=storage.preview(recipe)
        agent.cancel()
    return {'recipe':recipe,'filename':preview['filename']}

def research_session(session:str):
    from .ai.research import Session
    if len(session)>128:raise ValueError('대화 ID가 너무 깁니다.')
    with _AI_LOCK:
        if session not in RESEARCH_SESSIONS:
            RESEARCH_SESSIONS[session]=Session(
                _shared_provider(ai_settings()),
                lambda:ai_agent(session),
                lambda revision:apply_strategy(session,revision))
        return RESEARCH_SESSIONS[session]

def ai_post(path:str,data:dict):
    if path=='/api/ai/gemini-models':
        from common_ai.gemini import list_models
        if set(data)-{'gemini_api_key'}:
            raise ValueError('Gemini 모델 목록 요청 형식을 확인하세요.')
        return list_models({**ai_settings(),**data})
    session=str(data.get('session') or 'default')
    if path=='/api/ai/sequence/stop':
        from . import backtest_sequences
        return backtest_sequences.stop(data.get('sequence_id'))
    if data.get('research') is True:
        if path=='/api/ai/presets':return research_session(session).presets()
        if path=='/api/ai/preset/load':return research_session(session).load_preset(data.get('preset_id'),data.get('revision'))
        if path=='/api/ai/editor':return research_session(session).editor()
        if path=='/api/ai/edit':return research_session(session).edit(data.get('revision'),data.get('strategy'),data.get('operation'),data.get('plan'))
        if path=='/api/ai/chat':return research_session(session).send(str(data.get('message','')),mode=data.get('mode','strategy'))
        if path=='/api/ai/apply':return research_session(session).confirm(data.get('revision'))
        if path=='/api/ai/cancel':return research_session(session).cancel()
        if path=='/api/ai/reset':
            with _AI_LOCK:
                RESEARCH_SESSIONS.pop(session,None)
                AI_SESSIONS.pop(session,None)
                BACKTEST_COMMAND_SESSIONS.pop(session,None)
            return {'ok':True}
    if path=='/api/ai/backtest/chat':return backtest_command_session(session).send(str(data.get('message','')))
    if path=='/api/ai/backtest/confirm':return backtest_command_session(session).confirm(data.get('revision'))
    if path=='/api/ai/backtest/cancel':
        backtest_command_session(session).cancel()
        return {'ok':True}
    if path=='/api/ai/backtest/reset':
        with _AI_LOCK:BACKTEST_COMMAND_SESSIONS.pop(session,None)
        return {'ok':True}
    if path=='/api/ai/chat':return ai_agent(session).send(str(data.get('message','')))
    if path=='/api/ai/apply':
        return apply_strategy(session,data.get('revision'))
    if path=='/api/ai/cancel':
        ai_agent(session).cancel()
        return {'ok':True}
    if path=='/api/ai/reset':
        with _AI_LOCK:AI_SESSIONS.pop(session,None)
        return {'ok':True}
    if path=='/api/ai/settings':
        from .unified_settings import save_ai
        from .ai.provider import AI_SETTING_KEYS
        save_ai({key:data[key] for key in AI_SETTING_KEYS if key in data})
        from common_ai.settings import masked_settings
        return {'ok':True,'settings':masked_settings(ai_settings())}
    return None
DOCS=['README.md','GPT_START_HERE.md','docs/USER_GUIDE.md','docs/ARCHITECTURE.md',
      'docs/STRATEGY_SCHEMA.md','docs/SPECIAL_1_TO_7.md','docs/GPT_HANDOFF.md',
      'docs/PART1_PART2_API.md','docs/TEST_REPORT.md','CHANGELOG.md']

class LabServer(ThreadingHTTPServer):
    daemon_threads=True
    # Two PART3 servers must never share a port: the page would reach the other
    # server and every API call would be refused (403). A second launch moves to a free port.
    allow_reuse_address=False
    def server_bind(self):
        import socket
        if hasattr(socket,'SO_EXCLUSIVEADDRUSE'):
            self.socket.setsockopt(socket.SOL_SOCKET,socket.SO_EXCLUSIVEADDRUSE,1)
        super().server_bind()
    def __init__(self,port=8763):
        global _AI_SERVER_COUNT
        self.token=secrets.token_urlsafe(32)
        self.last_seen=None  # monotonic time of the last authorized page request
        self.closed_at=None  # set when a page reports it was closed (console-less mode only)
        super().__init__(('127.0.0.1',port),Handler)
        with _AI_LOCK:
            _AI_SERVER_COUNT+=1
            self._ai_server_registered=True

    def server_close(self):
        global _AI_SERVER_COUNT
        super().server_close()
        with _AI_LOCK:
            if not getattr(self,'_ai_server_registered',False):return
            self._ai_server_registered=False
            _AI_SERVER_COUNT-=1
            client=_detach_ai_client() if _AI_SERVER_COUNT==0 else None
        if client is not None:client.close()

class Handler(BaseHTTPRequestHandler):
    server_version='PART3/1.0'
    def log_message(self,*args):pass
    def send(self,status,body,ctype='application/json; charset=utf-8'):
        if not isinstance(body,bytes):
            body=json.dumps(body,ensure_ascii=False).encode('utf-8') if ctype.startswith('application/json') else body.encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type',ctype);self.send_header('Content-Length',str(len(body)))
        self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Referrer-Policy','no-referrer')
        self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'")
        self.end_headers();self.wfile.write(body)
    def authorized(self):
        supplied=self.headers.get('X-Lab-Token','')
        if not secrets.compare_digest(supplied,self.server.token):return False
        origin=self.headers.get('Origin')
        ok=not origin or origin==f'http://127.0.0.1:{self.server.server_port}'
        if ok:
            import time
            self.server.last_seen=time.monotonic()
        return ok
    def send_file(self,path):
        # Stream original bytes; large CSVs do not become a JSON/UI allocation.
        with path.open('rb') as handle:
            self.send_response(200)
            self.send_header('Content-Type','application/octet-stream')
            self.send_header('Content-Length',str(path.stat().st_size))
            self.send_header('Content-Disposition','attachment; filename="'+path.name+'"')
            self.send_header('Cache-Control','no-store')
            self.send_header('X-Content-Type-Options','nosniff')
            self.end_headers()
            while chunk:=handle.read(64*1024):
                self.wfile.write(chunk)
    def do_GET(self):
        parsed=urlparse(self.path);path=parsed.path
        try:
            if path.startswith('/api/'):
                if not self.authorized():return self.send(403,{'error':'UI 세션을 다시 여세요.'})
                if path=='/api/init':
                    conn=storage.connections();project=storage.project_path(conn)
                    draft=ROOT/'projects/last_draft.json'
                    saved=json.loads(draft.read_text('utf-8')) if draft.is_file() else None
                    draft_warning=None
                    recipe=(saved or {}).get('recipe') or {}
                    if recipe:
                        try:
                            saved=storage.load_saved_draft(saved)
                            recipe=saved['recipe']
                        except ValueError as exc:
                            draft_warning='저장 초안 실행 차단: '+str(exc)+' 초안 파일은 보존했습니다.'
                            saved=None
                    return self.send(200,{'specials':[{'id':i,'number':i,'name':n} for i,n in catalog.preset_names().items()],
                       'timeframes':catalog.TF_LABELS,'oz_timeframes':catalog.OZ_TFS,'conditions':catalog.CONDITIONS,
                       'profiles':sorted(catalog.PROFILES.TRIGGER_MODES),'profile_choices':[{'validation_mode':vm,'trigger_mode':tm,'label':catalog.PROFILES.profile_label(vm,tm)} for vm,tm in catalog.PROFILES.PROFILE_KEYS],'connections':conn,'project_resolved':project,
                       'recent':storage.recent(),'next_filename':storage.next_filename(),'draft':saved,
                       'draft_warning':draft_warning,'docs':DOCS})
                if path=='/api/ai/settings':
                    from common_ai.settings import masked_settings
                    return self.send(200,{'settings':masked_settings(ai_settings()),'base_url':'http://127.0.0.1:11434'})
                if path=='/api/ai/models':
                    from .ollama_models import list_models
                    return self.send(200,list_models())
                if path=='/api/ai/gemini-models':
                    from common_ai.gemini import list_models
                    return self.send(200,list_models(ai_settings()))
                if path=='/api/ai/gguf-models':
                    from .ai.gguf_catalog import models
                    listing=models()
                    listing['selected']=ai_settings().get('gguf_model_path')
                    return self.send(200,listing)
                if path=='/api/ai/sequence':
                    from . import backtest_sequences
                    return self.send(200,backtest_sequences.status(parse_qs(parsed.query).get('id',[''])[0]))
                if path.startswith('/api/mo/'):
                    from . import unified_live,unified_backtest,unified_settings
                    query=parse_qs(parsed.query)
                    if path=='/api/mo/live/status':
                        detail=query.get('detail',[None])[0]
                        return self.send(200,unified_live.status(query.get('module',['ALL'])[0],
                            detail=None if detail is None else detail=='true',
                            lines=query.get('lines',['true'])[0]!='false'))
                    if path=='/api/mo/live/specials':return self.send(200,unified_live.specials())
                    if path=='/api/mo/live/engines':return self.send(200,unified_live.engines())
                    if path=='/api/mo/backtest/options':return self.send(200,unified_backtest.options())
                    if path=='/api/mo/backtest/recent':return self.send(200,unified_backtest.recent())
                    if path=='/api/mo/backtest/status':return self.send(200,unified_backtest.status(query.get('id',[''])[0]))
                    if path=='/api/mo/backtest/log':return self.send(200,unified_backtest.log(query.get('id',[''])[0]))
                    if path=='/api/mo/backtest/result':return self.send(200,unified_backtest.result(query.get('id',[''])[0]))
                    if path=='/api/mo/backtest/tested':return self.send(200,unified_backtest.tested(query.get('id',[''])[0]))
                    if path=='/api/mo/backtest/trades':return self.send(200,unified_backtest.trades(
                        query.get('id',[''])[0],query.get('rr',[None])[0],
                        query.get('offset',[0])[0],query.get('limit',[100])[0],query.get('period',[None])[0]))
                    if path=='/api/mo/backtest/analysis':return self.send(200,unified_backtest.analysis(
                        query.get('id',[''])[0],query.get('period',['all'])[0],query.get('rr',[None])[0],
                        table=query.get('table',[''])[0]=='1'))
                    if path=='/api/mo/backtest/tune':
                        from . import backtest_tuning
                        return self.send(200,backtest_tuning.status())
                    if path=='/api/mo/backtest/download':return self.send_file(unified_backtest.download(
                        query.get('id',[''])[0],query.get('kind',[''])[0]))
                    if path=='/api/mo/settings':return self.send(200,unified_settings.read())
                    return self.send(404,{'error':'API를 찾지 못했습니다.'})
                if path=='/api/ping':return self.send(200,{'ok':True})
                if path=='/api/recent':return self.send(200,{'items':storage.recent(),'next_filename':storage.next_filename()})
                if path=='/api/strategies':
                    from . import strategy_library
                    return self.send(200,strategy_library.listing())
                if path=='/api/job':return self.send(200,integration.job_status(parse_qs(parsed.query).get('id',[''])[0]))
                if path=='/api/doc':
                    name=parse_qs(parsed.query).get('name',['README.md'])[0]
                    if name not in DOCS:raise ValueError('문서 이름을 확인하세요.')
                    return self.send(200,{'name':name,'text':(ROOT/name).read_text('utf-8')})
                return self.send(404,{'error':'API를 찾지 못했습니다.'})
            files={'/':'index.html','/index.html':'index.html','/style.css':'style.css','/app.js':'app.js','/icon.svg':'icon.svg',
                   '/ai_chat.js':'ai_chat.js','/ai_display.js':'ai_display.js','/unified.js':'unified.js',
                   '/strategy_library.js':'strategy_library.js',
                   '/ai_editor.js':'ai_editor.js','/ai_editor.css':'ai_editor.css',
                   '/backtest_dashboard.js':'backtest_dashboard.js','/backtest_jobs.js':'backtest_jobs.js'}
            name=files.get(path)
            if path in ('/moses.ico', '/moses.png'):name=path[1:]
            if not name:return self.send(404,'Not found','text/plain')
            data=(ROOT/'web'/name).read_bytes()
            ctype=mimetypes.guess_type(name)[0] or 'text/plain'
            return self.send(200,data,ctype+'; charset=utf-8')
        except Exception as exc:return self.send(400,{'error':str(exc)})
    def do_POST(self):
        if not self.authorized():return self.send(403,{'error':'UI 세션을 다시 여세요.'})
        try:
            size=int(self.headers.get('Content-Length','0'))
            if not 0<size<=4_000_000:raise ValueError('요청 크기가 올바르지 않습니다.')
            data=json.loads(self.rfile.read(size))
            path=urlparse(self.path).path
            if path in ('/api/preview','/api/generate') and set(data) != {'recipe'}:
                raise ValueError('AI Recipe만 받습니다. 수동 코드 입력은 지원하지 않습니다.')
            if path=='/api/bye':
                import time
                # Remember this request's own time: any later request (a reload) cancels the stop.
                self.server.close_seen=self.server.last_seen
                self.server.closed_at=time.monotonic();return self.send(200,{'ok':True})
            if path=='/api/preview':result=storage.preview(data['recipe'])
            elif path=='/api/generate':result=storage.generate(data['recipe'])
            elif path=='/api/strategies':
                from . import strategy_library
                result=strategy_library.change(data)
            elif path=='/api/reopen':result=storage.reopen(data['filename'])
            elif path=='/api/import':result={'recipe':storage.load_saved_recipe(data['recipe'])}
            elif path=='/api/save_project':result={'filename':storage.save_project(data['recipe'])}
            elif path=='/api/draft':
                storage.save_draft(data);result={'ok':True}
            elif path=='/api/connections':result=storage.save_connections(data)
            elif path=='/api/folder':integration.open_folder();result={'ok':True}
            elif path=='/api/backtest':result=integration.backtest(data)
            elif path.startswith('/api/ai/'):
                result=ai_post(path,data)
                if result is None:return self.send(404,{'error':'API를 찾지 못했습니다.'})
            elif path.startswith('/api/mo/'):
                from . import unified_live,unified_backtest,unified_settings
                if path=='/api/mo/live/start':result=unified_live.start(data)
                elif path=='/api/mo/live/stop':result=unified_live.stop()
                elif path=='/api/mo/live/specials':result=unified_live.save_specials(data.get('items'),recover=data.get('recover',False))
                elif path=='/api/mo/strategies':result=unified_live.change_strategy_list(data)
                elif path=='/api/mo/backtest/start':result=unified_backtest.start(data)
                elif path=='/api/mo/backtest/confirm':result=unified_backtest.confirm(data.get('job_id'),data.get('plan_revision'))
                elif path=='/api/mo/backtest/stop':result=unified_backtest.stop(data.get('job_id'),force=data.get('force',False))
                elif path=='/api/mo/backtest/reconnect':result=unified_backtest.reconnect(data.get('job_id'))
                elif path=='/api/mo/backtest/delete':result=unified_backtest.delete_selected(data.get('job_ids'))
                elif path=='/api/mo/backtest/leftovers':result=unified_backtest.delete_leftovers(data.get('job_ids'))
                elif path=='/api/mo/backtest/tune':
                    from . import backtest_tuning
                    result=backtest_tuning.start()
                elif path=='/api/mo/settings/telegram':result=unified_settings.confirm_telegram(data)
                elif path=='/api/mo/settings/telegram_chats':result=unified_settings.telegram_chats(data)
                elif path=='/api/mo/settings':
                    group=data.get('group');changes=data.get('changes')
                    handlers={'live':unified_settings.save_live,'part2':unified_settings.save_part2,
                              'connections':unified_settings.save_connections,'ai':unified_settings.save_ai}
                    if group not in handlers:raise ValueError('설정 그룹을 확인하세요.')
                    result=handlers[group](changes)
                else:return self.send(404,{'error':'API를 찾지 못했습니다.'})
            else:return self.send(404,{'error':'API를 찾지 못했습니다.'})
            return self.send(200,result)
        except Exception as exc:
            failure={'error':str(exc)}
            if getattr(exc,'pending_preserved',False) is True:failure['pending_preserved']=True
            self.send(400,failure)
