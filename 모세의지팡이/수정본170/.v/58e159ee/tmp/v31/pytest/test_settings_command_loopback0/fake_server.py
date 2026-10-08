
import argparse, contextlib, http.server, json, sys, time
from pathlib import Path
p = argparse.ArgumentParser()
p.add_argument('--config', required=True)
p.add_argument('--port', type=int, required=True)
p.add_argument('--api-key', required=True)
p.add_argument('--host', required=True)
args, unknown = p.parse_known_args()
config = json.loads(Path(args.config).read_text('utf-8'))
if config.get('exit_code'):
    print('model load failed ' + str(config.get('diagnostic', '')), flush=True)
    sys.exit(config['exit_code'])
class Handler(http.server.BaseHTTPRequestHandler):
    count = 0
    chats = 0
    def log_message(self, *a): pass
    def reply(self, value, status=200, raw=None):
        body = json.dumps(value, ensure_ascii=False).encode('utf-8') if raw is None else raw
        if self.path == '/v1/chat/completions':
            time.sleep(config.get('header_delay', 0))
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        if self.path == '/v1/chat/completions':
            time.sleep(config.get('body_delay', 0))
        with contextlib.suppress(BrokenPipeError, ConnectionResetError):
            self.wfile.write(body)
    def note(self, payload=None):
        with Path(config['requests']).open('a', encoding='utf-8') as file:
            file.write(json.dumps({'path': self.path, 'host': self.headers['Host'],
                'auth': self.headers.get('Authorization'), 'payload': payload}, ensure_ascii=False) + '\n')
    def do_GET(self):
        self.note()
        if self.path == '/health':
            Handler.count += 1
            time.sleep(config.get('health_delay', 0))
            if Handler.count <= config.get('loading_count', 0):
                return self.reply({'error': {'message': 'Loading model'}}, 503)
            return self.reply(config.get('health', {'status': 'ok'}), config.get('health_status', 200))
        if self.headers.get('Authorization') != 'Bearer ' + args.api_key or config.get('deny_models'):
            return self.reply({'error': {'message': 'Invalid API Key'}}, 401)
        if self.path == '/v1/models':
            return self.reply(config.get('models', {'data': [{'id': 'fake-model'}]}))
        self.reply({}, 404)
    def do_POST(self):
        payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        self.note(payload)
        if self.headers.get('Authorization') != 'Bearer ' + args.api_key:
            return self.reply({'error': {'message': 'Invalid API Key'}}, 401)
        if self.path != '/v1/chat/completions':
            return self.reply({}, 404)
        Handler.chats += 1
        time.sleep(config.get('chat_delay', 0))
        if config.get('chat_error'):
            return self.reply({'error': config['chat_error']}, config.get('chat_status', 400))
        if config.get('invalid_json'):
            return self.reply({}, raw=b'not-json')
        if 'wire_reply' in config:
            return self.reply(config['wire_reply'])
        content = json.dumps(config.get('content', {'answer': '완료'}), ensure_ascii=False)
        self.reply({'choices': [{'finish_reason': config.get('finish_reason', 'stop'),
                    'message': {'content': content}}], 'truncated': config.get('truncated', False)})
http.server.ThreadingHTTPServer((args.host, args.port), Handler).serve_forever()
