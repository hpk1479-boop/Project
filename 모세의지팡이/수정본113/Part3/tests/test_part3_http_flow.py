"""Real local HTTP/UI route; scripted JSON, no Qwen or external network."""
import ast
import http.client
import json
import socket
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from lab import server,storage
from lab.ai.agent import Agent
from lab.ai.provider import ScriptedProvider
from confirmed_answers import cases

class LocalUIFlow(unittest.TestCase):
    def test_manual_creation_routes_are_not_available(self):
        app=server.LabServer(0)
        port=app.server_address[1]
        original_connect=socket.socket.connect
        def local_only(sock,address):
            if address != ('127.0.0.1',port): raise AssertionError('External network forbidden')
            return original_connect(sock,address)
        worker=threading.Thread(target=app.serve_forever,daemon=True)
        worker.start()
        try:
            with tempfile.TemporaryDirectory() as folder, patch.object(storage,'ROOT',Path(folder)), patch.object(socket.socket,'connect',local_only):
                from lab.ai.intent import recipe_from_intent
                approved=recipe_from_intent(next(cases())['intent'])
                for path,data,status in (
                    ('/api/load',{'number':0},404),
                    ('/api/preview',{'recipe':approved,'manual_code':'print(1)'},400),
                    ('/api/generate',{'recipe':approved,'manual':True,'manual_code':'print(1)'},400),
                    ('/api/draft',{'recipe':approved,'manual_code':'print(1)'},400),
                ):
                    with self.subTest(path=path):
                        connection=http.client.HTTPConnection('127.0.0.1',port,timeout=5)
                        try:
                            connection.request('POST',path,json.dumps(data),
                                {'X-Lab-Token':app.token,'Content-Type':'application/json'})
                            response=connection.getresponse();body=response.read().decode('utf-8')
                            self.assertEqual(response.status,status,body)
                        finally: connection.close()
                self.assertEqual(list(Path(folder).iterdir()),[])
        finally:
            app.shutdown();app.server_close();worker.join(timeout=5)

    def test_ai_json_confirm_preview_and_generate_over_actual_http(self):
        answer=next(cases())['intent']
        agent=Agent(ScriptedProvider([{'content':json.dumps(answer,ensure_ascii=False),'tool_calls':[]}]))
        session='interface-check'
        previous=server.AI_SESSIONS.get(session)
        server.AI_SESSIONS[session]=agent
        app=server.LabServer(0)
        port=app.server_address[1]
        original_connect=socket.socket.connect
        def local_only(sock,address):
            if address != ('127.0.0.1',port): raise AssertionError('External network forbidden')
            return original_connect(sock,address)
        worker=threading.Thread(target=app.serve_forever,daemon=True)
        worker.start()
        def request(path,data=None):
            connection=http.client.HTTPConnection('127.0.0.1',port,timeout=5)
            try:
                headers={'X-Lab-Token':app.token,'Content-Type':'application/json'}
                connection.request('GET' if data is None else 'POST',path,
                    None if data is None else json.dumps(data),headers)
                response=connection.getresponse(); body=response.read().decode('utf-8')
                self.assertEqual(response.status,200,body)
                return json.loads(body) if path.startswith('/api/') else body
            finally: connection.close()
        try:
            with tempfile.TemporaryDirectory() as folder, patch.object(storage,'ROOT',Path(folder)), patch.object(socket.socket,'connect',local_only):
                page=request('/')
                self.assertIn('ai-text',page)
                response=request('/api/ai/chat',{'session':session,'message':'1분 EMA50이 EMA200 위에 있으면 1분 올존 전략으로 만들어줘'})
                self.assertTrue(response['can_apply'],(response,agent.provider.seen))
                self.assertFalse((Path(folder)/'generated').exists())
                applied=request('/api/ai/apply',{'session':session})
                self.assertEqual(applied['recipe']['schema_version'],2)
                self.assertFalse((Path(folder)/'generated').exists())
                preview=request('/api/preview',{'recipe':applied['recipe']})
                ast.parse(preview['code'])
                generated=request('/api/generate',{'recipe':applied['recipe']})
                path=Path(generated['path'])
                self.assertTrue(path.is_relative_to(Path(folder)))
                self.assertTrue(path.is_file())
                ast.parse(path.read_text('utf-8'))
                self.assertEqual(len(list((Path(folder)/'generated').glob('*.py'))),1)
                self.assertFalse((Path(folder)/'ai_training').exists())
        finally:
            app.shutdown(); app.server_close(); worker.join(timeout=5)
            if previous is None: server.AI_SESSIONS.pop(session,None)
            else: server.AI_SESSIONS[session]=previous

if __name__=='__main__': unittest.main()
