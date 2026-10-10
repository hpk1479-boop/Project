"""Synthetic local UI preview: never calls AI, engines, user settings or warehouse."""
import json

from web_only65_preview import Preview, LabServer, ID


class JobPreview(Preview):
    def do_GET(self):
        route = self.path.split('?')[0]
        if route == '/api/mo/backtest/recent':
            return self.send(200, {'items': [
                {'job_id': ID, 'kind': 'normal', 'phase': 'complete', 'symbol': 'XAUUSD+',
                 'strategies': ['SPECIAL1'], 'start': '2026-09-01', 'end': '2026-10-01'},
                {'job_id': 'b' * 32, 'kind': 'generated', 'phase': 'confirm', 'filename': 'Test_SPECIAL123.py',
                 'symbol': 'NAS100', 'start': '2026-09-01', 'end': '2026-10-01'}], 'warnings': []})
        return super().do_GET()

    def do_POST(self):
        route = self.path.split('?')[0]
        data = json.loads(self.rfile.read(int(self.headers.get('Content-Length', '0'))) or b'{}')
        if route == '/api/ai/backtest/chat':
            return self.send(200, {'action': 'START', 'can_confirm': True, 'revision': 'preview:1',
                'message_ko': '실행 전에 해석한 조건을 확인해 주세요.',
                'preview': '목적: 백테스트\n종목: XAUUSD+\n기간: 2026-09-01 ~ 2026-09-30 UTC\n종료 경계: 2026-10-01 (미포함)\n전략: SPECIAL1\n데이터 모드: 틱\n결과: 알림만\n스프레드: 0 포인트\n재구축: 사용 안 함'})
        if route == '/api/mo/backtest/reconnect':
            return self.send(200, {'job_id': data['job_id'], 'phase': 'confirm'})
        return self.send(400, {'error': '합성 화면입니다. 실행·중지·AI 호출을 하지 않습니다.'})


if __name__ == '__main__':
    host = LabServer(0)
    host.RequestHandlerClass = JobPreview
    print(f'http://127.0.0.1:{host.server_port}/#token={host.token}', flush=True)
    try:
        host.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        host.server_close()
