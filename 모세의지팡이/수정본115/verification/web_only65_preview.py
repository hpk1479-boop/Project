"""Actual unified UI with synthetic APIs; never starts/stops engines or writes settings."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'Part3'))
from lab.server import Handler, LabServer

ID = 'a' * 32
MODULES = {key: {'name': name, 'state': '연결중', 'monitoring': False}
           for key, name in zip(('STAFF', 'ENGINE', 'OZ', 'SWEEP', 'FVG', 'INDICATOR', 'WATCH', 'KIM'),
                                ('시장 데이터', '이벤트 엔진', '올존', '스윕', 'FVG', '지표', '개인 감시', '알림 담당'))}


class Preview(Handler):
    def do_GET(self):
        route = self.path.split('?')[0]
        payload = {
            '/api/init': {'connections': {}, 'recent': []},
            '/api/mo/live/status': {'modules': MODULES, 'lines': ['[STAFF] 시장 데이터 연결 확인', '[ENGINE] 합성 로그 · 실제 엔진 실행 없음'], 'error': ''},
            '/api/mo/settings': {'live': [], 'part2': {'cores': None},
                                 'connections': {'warehouse': '', 'live_records': '', 'python_executable': ''},
                                 'ai': {'model': 'qwen3.5:4b', 'timeout': 90, 'base_url': 'http://127.0.0.1:11434'}},
            '/api/ai/settings': {'settings': {'model': 'qwen3.5:4b', 'timeout': 90}, 'base_url': 'http://127.0.0.1:11434'},
            '/api/ai/models': {'available': True, 'models': ['qwen3.5:4b', 'qwen3:8b']},
            '/api/mo/backtest/options': {'symbol': 'XAUUSD+', 'symbols': ['XAUUSD+'], 'start': '2026-09-01',
                                         'end': '2026-10-01', 'mode': 'BAR', 'warehouse_set': True,
                                         'specials': [], 'special_settings': {}, 'trigger_choices': [],
                                         'watch_text': '15분 상승추세 계속 감시', 'watch_chat_id': 'BACKTEST', 'spread_points': {}},
            '/api/mo/backtest/status': {'phase': 'complete', 'result_ready': False, 'progress': {
                'phase': '완료 · 합성 화면', 'build': 100, 'replay': 100, 'virtual': 0,
                'remaining': '', 'warnings': [], 'lines': ['현재 상세 기록을 펼쳐 원본 진단 로그를 볼 수 있습니다.']}},
            '/api/mo/backtest/log': {'available': True, 'text': '[PLAN] 데이터 계획 확인\n[RUN] 재생 완료\n합성 데이터 · 원본 로그 끝 40KB 표시'},
        }
        if route in payload:
            return self.send(200, payload[route])
        if route.startswith('/api/'):
            return self.send(200, {})
        return super().do_GET()

    def do_POST(self):
        route = self.path.split('?')[0]
        if route == '/api/mo/backtest/start':
            return self.send(200, {'job_id': ID, 'phase': 'planning'})
        return self.send(400, {'error': '검증용 화면입니다. 엔진과 실제 설정은 변경하지 않습니다.'})


if __name__ == '__main__':
    host = LabServer(0)
    host.RequestHandlerClass = Preview
    print(f'http://127.0.0.1:{host.server_port}/#token={host.token}', flush=True)
    try:
        host.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        host.server_close()
