"""Cancellable loopback HTTP I/O; no model or strategy dependencies."""
from __future__ import annotations

import errno
import http.client
import select
import socket
import time


class _ChatSocket(socket.socket):
    """SocketIO.makefile reads use this method, including on Windows."""
    def __init__(self, source, cancelled, deadline):
        super().__init__(source.family, source.type, source.proto, fileno=source.detach())
        self.setblocking(False)
        self.cancelled = cancelled
        self.deadline = deadline

    def _wait(self, *, writing=False):
        while True:
            if self.cancelled.is_set():
                raise OSError(errno.ECANCELED, 'AI 연결을 종료했습니다.')
            remaining = self.deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError('공통 AI 응답 대기 시간이 초과되었습니다.')
            try:
                ready = select.select([] if writing else [self], [self] if writing else [], [],
                                      min(0.1, remaining))
            except (OSError, ValueError):
                if self.cancelled.is_set():
                    raise OSError(errno.ECANCELED, 'AI 연결을 종료했습니다.') from None
                raise
            if ready[1 if writing else 0]:
                if self.cancelled.is_set():
                    raise OSError(errno.ECANCELED, 'AI 연결을 종료했습니다.')
                return

    def recv_into(self, buffer, nbytes=0, flags=0):
        while True:
            self._wait()
            try:
                return super().recv_into(buffer, nbytes, flags)
            except BlockingIOError:
                pass

    def sendall(self, data, flags=0):
        pending = memoryview(data)
        while pending:
            self._wait(writing=True)
            try:
                sent = super().send(pending[:65536], flags)
            except BlockingIOError:
                continue
            if not sent:
                raise ConnectionResetError('공통 AI 연결이 끊어졌습니다.')
            pending = pending[sent:]


class CancellableHTTPConnection(http.client.HTTPConnection):
    def __init__(self, *args, cancelled, **kwargs):
        super().__init__(*args, **kwargs)
        self.cancelled = cancelled
        self.deadline = time.monotonic() + self.timeout

    def connect(self):
        if self.cancelled.is_set():
            raise OSError(errno.ECANCELED, 'AI 연결을 종료했습니다.')
        previous_timeout = self.timeout
        try:
            # A closed/blocked loopback listener must not leave the caller
            # waiting for the much longer model-inference timeout.
            self.timeout = min(3, previous_timeout)
            super().connect()
        finally:
            self.timeout = previous_timeout
        self.sock = _ChatSocket(self.sock, self.cancelled, self.deadline)
