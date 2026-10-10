"""Current web assets and real preset/editor APIs; no AI, jobs, settings or generation writes."""
from datetime import date
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'Part3'))
from lab import catalog, server
from lab.ai.agent import Agent
from lab.ai.research import Session

MODULES = {key: {'name': name, 'state': '연결중', 'monitoring': False}
           for key, name in zip(('STAFF', 'ENGINE', 'OZ', 'SWEEP', 'FVG', 'INDICATOR', 'WATCH', 'KIM'),
                                ('시장 데이터', '이벤트 엔진', '올존', '스윕', 'FVG', '지표', '개인 감시', '알림 담당'))}


class OfflineProvider:
    permission_scope = 'local'
    settings = {'provider': 'ollama'}

    def chat(self, *args, **kwargs):
        raise AssertionError('화면 확인 중에는 외부 AI를 호출하지 않습니다.')


def context():
    return {'today': date.today().isoformat(),
            'options': {'symbols': list(catalog.current_symbols()),
                        'specials': list(catalog.preset_names()), 'mode': 'BAR',
                        'result_mode': 'VIRTUAL_ENTRY', 'special_settings': {}, 'spread_points': {}},
            'jobs': [], 'generated': [], 'execution_defaults': {}}


def research_session(name):
    if name not in server.RESEARCH_SESSIONS:
        provider = OfflineProvider()
        agent = Agent(provider)
        def no_apply(*args):
            raise AssertionError('화면 확인 중에는 적용·생성·실행하지 않습니다.')
        server.RESEARCH_SESSIONS[name] = Session(provider, lambda: agent, no_apply, context_loader=context)
    return server.RESEARCH_SESSIONS[name]


class Preview(server.Handler):
    def do_GET(self):
        route = self.path.split('?')[0]
        payload = {
            '/api/init': {'connections': {}, 'recent': []},
            '/api/ai/settings': {'settings': {'provider': 'ollama', 'model': '검증용 화면 · AI 호출 없음'},
                                 'base_url': 'http://127.0.0.1:11434'},
            '/api/mo/live/status': {'modules': MODULES, 'lines': [], 'error': ''},
            '/api/mo/backtest/recent': {'items': []},
        }
        if route in payload:
            return self.send(200, payload[route])
        if route.startswith('/api/mo/'):
            return self.send(200, {})
        return super().do_GET()

    def do_POST(self):
        route = self.path.split('?')[0]
        if route not in ('/api/ai/presets', '/api/ai/preset/load', '/api/ai/editor',
                         '/api/ai/edit', '/api/ai/cancel', '/api/ai/reset', '/api/bye'):
            return self.send(400, {'error': '검증용 화면에서는 실제 실행·저장·AI 호출을 하지 않습니다.'})
        return super().do_POST()


if __name__ == '__main__':
    server.research_session = research_session
    host = server.LabServer(0)
    host.RequestHandlerClass = Preview
    print(f'http://127.0.0.1:{host.server_port}/#token={host.token}', flush=True)
    try:
        host.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        host.server_close()
