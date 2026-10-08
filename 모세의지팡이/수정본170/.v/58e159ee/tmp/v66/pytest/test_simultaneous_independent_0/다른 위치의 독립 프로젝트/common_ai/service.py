"""One project model owner used by standalone WATCH and the unified web UI."""
from __future__ import annotations

import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import threading
import time
import uuid

from .client import project_id
from .process_identity import identity


class Service:
    def __init__(self, root, *, provider_factory=None, runtime=None):
        from .model_runtime import RUNTIME
        from .provider import from_settings
        from .settings import read_settings
        self.root = Path(root).resolve()
        self.runtime = RUNTIME if runtime is None else runtime
        self.factory = from_settings if provider_factory is None else provider_factory
        self.settings = read_settings(self.root)
        from .reference_pack import load_pack
        load_pack()  # Immutable language data, once per AI service process.
        self.fingerprint = self._fingerprint(self.settings)
        self.provider = None
        self.provider_generation = None
        self.clients = {}
        self.client_lock = threading.Lock()
        self.settings_lock = threading.Lock()
        self.turn_lock = threading.Lock()
        self.close_lock = threading.Lock()
        self.closed = threading.Event()
        self.closing = False
        self.started = time.monotonic()
        self.last_client = self.started
        self.had_clients = False
        self.token = secrets.token_hex(32)
        self.service_id = uuid.uuid4().hex
        self.endpoint = self.root / 'runtime' / 'ai_service.json'
        owner = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'

            def log_message(self, *args):
                pass

            def handle_request(self):
                if not hmac.compare_digest(self.headers.get('X-AI-Token', ''), owner.token):
                    self.reply(403, {'error': '공통 AI 연결 인증에 실패했습니다.'})
                    return
                try:
                    payload = None
                    if self.command == 'POST':
                        size = int(self.headers.get('Content-Length', '0'))
                        if not 0 < size <= 8 * 1024 * 1024:
                            raise ValueError('AI 요청 크기를 확인하세요.')
                        payload = json.loads(self.rfile.read(size).decode('utf-8'))
                        if not isinstance(payload, dict):
                            raise ValueError('AI 요청 형식이 올바르지 않습니다.')
                    result = owner.dispatch(self.path, payload)
                    self.reply(200, result)
                except (ValueError, RuntimeError) as exc:
                    from .security import redact, secret_values
                    self.reply(400, {'error': redact(str(exc), secret_values(owner.settings) + (owner.token,))[:1000]})
                except Exception:
                    self.reply(500, {'error': '공통 AI 요청 처리에 실패했습니다. 설정과 모델 파일을 확인하세요.'})

            def reply(self, status, data):
                encoded = json.dumps(data, ensure_ascii=False).encode('utf-8')
                try:
                    self.send_response(status)
                    self.send_header('Content-Type', 'application/json; charset=utf-8')
                    self.send_header('Content-Length', str(len(encoded)))
                    self.send_header('Connection', 'close')
                    self.end_headers()
                    self.wfile.write(encoded)
                except (OSError, ConnectionError):
                    pass
                self.close_connection = True

            do_POST = do_GET = handle_request

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.daemon_threads = True
        self.server.timeout = 2

    @staticmethod
    def _fingerprint(data):
        return json.dumps(data, sort_keys=True, ensure_ascii=False)

    @property
    def settings_generation(self):
        return self.runtime.generation

    def _sync_settings(self, force=False):
        from .settings import read_settings
        with self.settings_lock:
            settings = read_settings(self.root)
            fingerprint = self._fingerprint(settings)
            if force or fingerprint != self.fingerprint:
                self.runtime.invalidate()
                self.settings = settings
                self.fingerprint = fingerprint
                self.provider = None
                self.provider_generation = None
            return dict(self.settings), self.settings_generation

    def status(self):
        _, generation = self._sync_settings()
        with self.client_lock:
            count = len(self.clients)
        return {'service_id': self.service_id, 'pid': os.getpid(),
                'generation': generation, 'clients': count}

    def dispatch(self, route, payload):
        if route == '/status' and payload is None:
            if self.closing:
                raise ValueError('공통 AI 서비스를 종료 중입니다.')
            return self.status()
        if payload is None:
            raise ValueError('공통 AI 요청 경로가 올바르지 않습니다.')
        if route == '/unregister':
            with self.client_lock:
                self.clients.pop(payload.get('client_id'), None)
                self.last_client = time.monotonic()
            return {'ok': True}
        if route == '/shutdown':
            timeout = payload.get('timeout', 45)
            if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or not 0 < timeout <= 300:
                raise ValueError('AI 종료 대기 시간을 확인하세요.')
            self.close(timeout=timeout)
            return {'ok': True}
        if self.closing:
            raise ValueError('공통 AI 서비스를 종료 중입니다.')
        if route == '/register':
            client_id = payload.get('client_id')
            pid = payload.get('pid')
            created = payload.get('created')
            if not isinstance(client_id, str) or not 1 <= len(client_id) <= 128 or not created or identity(pid) != created:
                raise ValueError('AI 클라이언트 프로세스를 확인하지 못했습니다.')
            with self.client_lock:
                if self.closing:
                    raise ValueError('공통 AI 서비스를 종료 중입니다.')
                self.clients[client_id] = {'pid': int(pid), 'created': created}
                self.had_clients = True
                self.last_client = time.monotonic()
            return self.status()
        if route == '/invalidate':
            self._sync_settings(force=True)
            return self.status()
        if route == '/chat':
            return self.chat(payload)
        raise ValueError('공통 AI 요청 경로가 올바르지 않습니다.')

    def chat(self, payload):
        from .provider import require_ai_enabled
        with self.client_lock:
            registered = self.clients.get(payload.get('client_id'))
        if not registered or identity(registered['pid']) != registered['created']:
            raise ValueError('AI 연결을 다시 시작해 주세요.')
        settings, generation = self._sync_settings()
        if payload.get('generation') != generation:
            raise ValueError('AI 설정이 변경되었습니다. 다시 요청하세요.')
        if (payload.get('role') not in ('watch', 'strategy', 'backtest') or
                not isinstance(payload.get('messages'), list) or
                not isinstance(payload.get('tools'), list) or
                payload.get('response_schema') is not None and not isinstance(payload['response_schema'], dict)):
            raise ValueError('AI 요청 형식이 올바르지 않습니다.')
        require_ai_enabled(settings)
        timeout = float(settings.get('timeout', 90))
        if not self.turn_lock.acquire(timeout=timeout):
            raise ValueError('다른 AI 요청이 진행 중입니다. 잠시 후 다시 요청하세요.')
        try:
            current_settings, current_generation = self._sync_settings()
            if generation != current_generation or self.closing:
                raise ValueError('AI 설정이 변경되었거나 종료 중입니다. 다시 요청하세요.')
            require_ai_enabled(current_settings)
            with self.client_lock:
                current_client = self.clients.get(payload.get('client_id'))
            if not current_client or identity(current_client['pid']) != current_client['created']:
                raise ValueError('AI 연결을 종료했습니다.')
            with self.settings_lock:
                if generation != self.settings_generation or self.closing:
                    raise ValueError('AI 설정이 변경되었거나 종료 중입니다. 다시 요청하세요.')
                if self.provider is None or self.provider_generation != generation:
                    self.provider = self.factory(current_settings)
                    self.provider_generation = generation
                selected_provider = self.provider
            from .security import external_payload, provider_scope, redact, secret_values
            messages, tools, schema = payload['messages'], payload['tools'], payload.get('response_schema')
            messages, tools, schema = external_payload(messages, tools, schema,
                role=payload['role'], settings=current_settings)
            # Gemini attaches the same public dictionary in its transport
            # preparation. Local transports retain their existing reference.
            if provider_scope(current_settings) == 'local':
                from .external_prompt import attach_language_reference
                messages = attach_language_reference(messages, current_settings)
            try:
                result = selected_provider.chat(messages, tools, response_schema=schema)
            except (ValueError, RuntimeError) as exc:
                raise ValueError(redact(str(exc), secret_values(current_settings))) from None
            self._sync_settings()
            if generation != self.settings_generation:
                raise ValueError('AI 설정이 변경되었습니다. 이전 응답을 적용하지 않았습니다.')
            if self.closing:
                raise ValueError('프로그램 종료 중입니다. 이전 AI 응답을 적용하지 않았습니다.')
            with self.client_lock:
                current_client = self.clients.get(payload.get('client_id'))
            if not current_client or identity(current_client['pid']) != current_client['created']:
                raise ValueError('AI 연결을 종료했습니다. 이전 응답을 적용하지 않았습니다.')
            if not isinstance(result, dict) or not isinstance(result.get('content'), str) or not isinstance(result.get('tool_calls'), list):
                raise ValueError('AI 모델 응답 형식이 올바르지 않습니다.')
            return redact(result, secret_values(current_settings))
        finally:
            self.turn_lock.release()

    def start(self):
        self.endpoint.parent.mkdir(parents=True, exist_ok=True)
        record = {'port': self.server.server_port, 'pid': os.getpid(),
            'created': identity(os.getpid()), 'token': self.token,
            'project': project_id(self.root), 'service_id': self.service_id,
            'generation': self.settings_generation}
        temporary = self.endpoint.with_name('ai_service.' + self.service_id + '.tmp')
        temporary.write_text(json.dumps(record), encoding='utf-8')
        os.replace(temporary, self.endpoint)
        self.thread = threading.Thread(target=self.server.serve_forever,
            kwargs={'poll_interval': 0.1}, daemon=True)
        self.thread.start()
        self.watcher = threading.Thread(target=self._watch, daemon=True)
        self.watcher.start()
        return self

    def _watch(self):
        while not self.closed.wait(0.2):
            with self.client_lock:
                for client_id, client in list(self.clients.items()):
                    if identity(client['pid']) != client['created']:
                        self.clients.pop(client_id, None)
                        self.last_client = time.monotonic()
                idle = not self.clients and time.monotonic() - self.last_client > (1 if self.had_clients else 60)
            if idle:
                try:
                    self.close(only_if_idle=True)
                except (RuntimeError, ValueError, OSError):
                    # Still-running inference retains ownership. Retry cleanup
                    # before publishing another service/model owner.
                    continue

    def close(self, timeout=45, *, only_if_idle=False):
        with self.close_lock:
            if self.closed.is_set():
                return
            with self.client_lock:
                # The watcher must recheck after its idle observation: a new
                # client may have registered before this close acquired locks.
                if only_if_idle and self.clients:
                    return
                self.closing = True
            deadline = time.monotonic() + timeout
            if not self.turn_lock.acquire(timeout=max(0, deadline - time.monotonic())):
                self.closing = False
                raise RuntimeError('AI 요청 종료를 기다리고 있습니다. 잠시 후 다시 닫아 주세요.')
            try:
                self.runtime.shutdown(timeout=max(0, deadline - time.monotonic()))
            except Exception:
                self.closing = False
                raise
            finally:
                self.turn_lock.release()
            self.server.shutdown()
            self.server.server_close()
            try:
                record = json.loads(self.endpoint.read_text(encoding='utf-8'))
                if record.get('service_id') == self.service_id:
                    self.endpoint.unlink()
            except (OSError, ValueError):
                pass
            self.closed.set()


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', required=True)
    args = parser.parse_args()
    service = Service(args.root).start()
    try:
        while not service.closed.wait(0.5):
            pass
    finally:
        service.close()


if __name__ == '__main__':
    main()
