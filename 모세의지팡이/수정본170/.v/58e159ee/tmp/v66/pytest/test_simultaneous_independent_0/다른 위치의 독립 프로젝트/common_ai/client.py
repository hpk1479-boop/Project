"""Authenticated loopback client for the independent common model service."""
from __future__ import annotations

import atexit
import contextlib
import hashlib
import http.client
import json
import os
from pathlib import Path
import subprocess
import socket
import sys
import threading
import time
import uuid

from .process_identity import identity
from .transport import CancellableHTTPConnection

SHUTDOWN_CONFIRM_TIMEOUT = 3



def project_id(root):
    return hashlib.sha256(os.path.normcase(str(Path(root).resolve())).encode('utf-8')).hexdigest()


class Client:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.endpoint = self.root / 'runtime' / 'ai_service.json'
        self.client_id = uuid.uuid4().hex
        self.record = None
        self.registered_service = None
        self._closed = False
        self._cancelled = threading.Event()
        self._io_lock = threading.Lock()
        self._chat_connections = {}
        atexit.register(self.close)

    def _request(self, record, route, payload=None, *, timeout=3):
        chat = route == '/chat'
        connection = (CancellableHTTPConnection('127.0.0.1', int(record['port']), timeout=timeout,
                      cancelled=self._cancelled) if chat else
                      http.client.HTTPConnection('127.0.0.1', int(record['port']), timeout=timeout))
        response = None
        active = {'socket': None}
        if chat:
            with self._io_lock:
                if self._closed:
                    raise ValueError('AI 연결을 종료했습니다.')
                self._chat_connections[connection] = active
        try:
            body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode('utf-8')
            connection.request('GET' if payload is None else 'POST', route, body,
                {'Content-Type': 'application/json', 'X-AI-Token': record['token']})
            if chat:
                with self._io_lock:
                    if self._closed:
                        raise ValueError('AI 연결을 종료했습니다.')
                    # Preserve the original socket reference: a response's
                    # makefile still owns it after HTTPConnection drops .sock.
                    if connection.sock is not None:
                        active['socket'] = connection.sock
            response = connection.getresponse()
            raw = response.read(8 * 1024 * 1024 + 1)
            if chat:
                self._require_open()
            if len(raw) > 8 * 1024 * 1024:
                raise ValueError('공통 AI 응답이 너무 큽니다.')
            data = json.loads(raw.decode('utf-8'))
            if not isinstance(data, dict):
                raise ValueError('공통 AI 응답 형식이 올바르지 않습니다.')
            if response.status != 200:
                raise ValueError(data.get('error') or '공통 AI 요청에 실패했습니다.')
            if chat:
                self._require_open()
            return data
        finally:
            if chat:
                with self._io_lock:
                    self._chat_connections.pop(connection, None)
                if active['socket'] is not None:
                    active['socket'].close()
            if response is not None:
                response.close()
            connection.close()

    def _require_open(self):
        with self._io_lock:
            if self._closed:
                raise ValueError('AI 연결을 종료했습니다.')

    def _read_record(self):
        try:
            record = json.loads(self.endpoint.read_text(encoding='utf-8'))
            if (not isinstance(record, dict) or record.get('project') != project_id(self.root) or
                    identity(record['pid']) != record.get('created') or
                    not isinstance(record.get('token'), str) or len(record['token']) < 32 or
                    type(record.get('port')) is not int or not 1 <= record['port'] <= 65535):
                return None
            return record
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def _lookup(self):
        record = self._read_record()
        if record is None:
            return None
        try:
            status = self._request(record, '/status')
            if status.get('service_id') != record.get('service_id'):
                return None
            return record, status
        except (OSError, http.client.HTTPException):
            # A still-live owner may be starting or temporarily unavailable.
            # _start waits for it instead of publishing a second owner.
            return None

    def _start(self):
        from .model_runtime import MachineLease
        lease = MachineLease('moses_ai_start_' + project_id(self.root)[:24] + '.lock')
        deadline = time.monotonic() + 15
        while True:
            self._require_open()
            found = self._lookup()
            if found:
                return found
            try:
                lease.acquire()
                break
            except ValueError:
                if time.monotonic() >= deadline:
                    raise ValueError('공통 AI 연결 준비 시간이 초과되었습니다. 다시 요청하세요.') from None
                time.sleep(0.05)
        try:
            self._require_open()
            found = self._lookup()
            if found:
                return found
            existing = self._read_record()
            executable = Path(sys.executable)
            if executable.name.lower() == 'pythonw.exe':
                executable = executable.with_name('python.exe')
            flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
            process = None
            if existing is None:
                with self._io_lock:
                    if self._closed:
                        raise ValueError('AI 연결을 종료했습니다.')
                    process = subprocess.Popen([str(executable), '-B', '-u', '-m', 'common_ai.service',
                        '--root', str(self.root)], cwd=str(self.root), stdin=subprocess.DEVNULL,
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags)
            while time.monotonic() < deadline:
                self._require_open()
                found = self._lookup()
                if found:
                    return found
                if process is not None and process.poll() is not None:
                    raise ValueError('공통 AI 서비스를 시작하지 못했습니다. AI 설정과 실행 환경을 확인하세요.')
                time.sleep(0.05)
            raise ValueError('공통 AI 서비스가 응답하지 않습니다. 잠시 후 다시 요청하세요.')
        finally:
            lease.release()

    def _connect(self):
        self._require_open()
        record, status = self._lookup() or self._start()
        with self._io_lock:
            if self._closed:
                raise ValueError('AI 연결을 종료했습니다.')
            self.record = record
            register = self.registered_service != record['service_id']
        if register:
            try:
                status = self._request(record, '/register', {'client_id': self.client_id,
                    'pid': os.getpid(), 'created': identity(os.getpid())})
                with self._io_lock:
                    if not self._closed:
                        self.registered_service = record['service_id']
            finally:
                # close() may have unregistered before a delayed register
                # request reached the service. Remove that late registration.
                with self._io_lock:
                    closed = self._closed
                if closed:
                    self._unregister(record)
        self._require_open()
        return record, status

    def chat(self, messages, tools, *, response_schema=None, role='strategy', generation=None):
        from .settings import read_settings
        from .provider import require_ai_enabled
        self._require_open()
        settings = read_settings(self.root)
        require_ai_enabled(settings)
        record, status = self._connect()
        # A queued turn can wait one configured interval, then use another for
        # inference. Do not abandon a valid queued request halfway through it.
        timeout = 2 * float(settings.get('timeout', 90)) + 5
        payload = {'client_id': self.client_id, 'messages': messages, 'tools': tools,
            'response_schema': response_schema, 'role': role,
            'generation': status['generation'] if generation is None else generation}
        try:
            return self._request(record, '/chat', payload, timeout=timeout)
        except (OSError, http.client.HTTPException):
            # A retry after disconnection could execute a tool twice. Let the
            # existing caller report this request and choose when to retry.
            self._require_open()
            raise ValueError('공통 AI 연결이 끊어졌습니다. 다시 요청하세요.') from None

    def invalidate(self, settings=None):
        # Settings are already saved by the owner. Never spawn an idle model
        # service just because settings were opened or saved.
        found = self._lookup()
        if found:
            return self._request(found[0], '/invalidate', {})
        return None

    def shutdown(self, timeout=45):
        # /status reads the current settings, which may already be broken or
        # reports "closing" while the last-client watcher is shutting down.
        # A verified owner record is sufficient to send an explicit shutdown.
        record = self._read_record()
        if record is None:
            return
        disconnected = False
        try:
            self._request(record, '/shutdown', {'timeout': timeout}, timeout=timeout + 2)
        except (OSError, http.client.HTTPException):
            # A daemon HTTP handler can lose its acknowledgement when the
            # service process exits. This is success only after both the exact
            # owning process and its endpoint ownership are proven gone.
            disconnected = True
        deadline = time.monotonic() + SHUTDOWN_CONFIRM_TIMEOUT
        while time.monotonic() < deadline:
            released = False
            try:
                current = json.loads(self.endpoint.read_text(encoding='utf-8'))
                released = (isinstance(current, dict) and
                    isinstance(current.get('service_id'), str) and
                    current['service_id'] != record['service_id'])
            except FileNotFoundError:
                released = True
            except (OSError, ValueError):
                pass
            exited = identity(record['pid']) != record['created']
            if released and (not disconnected or exited):
                return
            time.sleep(0.05)
        raise RuntimeError('공통 AI 종료를 확인하지 못했습니다. 잠시 후 다시 닫아 주세요.')

    def _unregister(self, record):
        try:
            self._request(record, '/unregister', {'client_id': self.client_id}, timeout=1)
        except (OSError, ValueError, KeyError, http.client.HTTPException):
            pass

    def close(self):
        with self._io_lock:
            if self._closed:
                return
            self._closed = True
            self._cancelled.set()
            record = self.record
            active = list(self._chat_connections.items())
        # Interrupt only this client's chat sockets. /shutdown and settings
        # requests are management operations and must keep their acknowledgements.
        for connection, state in active:
            stream = state['socket'] or connection.sock
            if stream is not None:
                with contextlib.suppress(OSError):
                    stream.shutdown(socket.SHUT_RDWR)
            with contextlib.suppress(OSError):
                connection.close()
        if record:
            self._unregister(record)
        atexit.unregister(self.close)
