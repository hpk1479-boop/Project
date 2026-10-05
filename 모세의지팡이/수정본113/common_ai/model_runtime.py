"""One resident model per MOSES runtime, including Ollama, PEFT and GGUF."""
from __future__ import annotations

import atexit
import contextlib
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import urllib.error
import urllib.request


class MachineLease:
    """An OS file lock survives threads and is released if the owning UI dies."""
    def __init__(self, name='moses_part3_ai_model.lock'):
        self.handle = None
        self.name = name

    def acquire(self):
        if self.handle is not None:
            return
        handle = open(Path(tempfile.gettempdir()) / self.name, 'a+b')
        try:
            handle.seek(0, 2)
            if not handle.tell():
                handle.write(b'0'); handle.flush()
            handle.seek(0)
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            raise ValueError('다른 MOSES 창에서 AI 모델을 사용 중입니다. 그 창을 닫거나 AI 설정을 변경한 뒤 다시 요청하세요.') from None
        self.handle = handle

    def release(self):
        if self.handle is not None:
            self.handle.close()
            self.handle = None


class ModelRuntime:
    def __init__(self):
        self.lock = threading.Lock()
        self.config_lock = threading.Lock()
        self.generation = 0
        self.lease = MachineLease()
        self.worker = None
        self.worker_key = None
        self.ollama_model = None
        self.pending_ollama = None
        self._closing = False

    def _remaining(self, deadline):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ValueError('AI 모델 전환·응답 대기 시간이 초과되었습니다. 다시 요청하세요.')
        return remaining

    def _http(self, route, deadline, data=None):
        from .provider import BASE_URL
        request = urllib.request.Request(BASE_URL + route,
            data=None if data is None else json.dumps(data).encode('utf-8'),
            headers={'Content-Type': 'application/json'}, method='GET' if data is None else 'POST')
        with urllib.request.urlopen(request, timeout=min(3, self._remaining(deadline))) as response:
            return json.loads(response.read().decode('utf-8'))

    def _loaded(self, deadline, *, offline_ok=False):
        self._remaining(deadline)
        try:
            data = self._http('/api/ps', deadline)
        except urllib.error.URLError as exc:
            # A refused local listener cannot have resident Ollama models. Timeout
            # or any HTTP error cannot establish that, so fail closed in those cases.
            import errno
            reason = exc.reason
            if offline_ok and isinstance(reason, OSError) and getattr(reason, 'errno', None) in (errno.ECONNREFUSED, 10061):
                return []
            raise ValueError('Ollama 모델의 메모리 상태를 확인하지 못했습니다. 연결 상태를 확인하세요.') from exc
        except (TimeoutError, OSError, ValueError):
            raise ValueError('Ollama 모델의 메모리 상태를 확인하지 못했습니다.') from None
        rows = data.get('models') if isinstance(data, dict) else None
        if not isinstance(rows, list):
            raise ValueError('Ollama 실행 모델 목록 응답이 올바르지 않습니다.')
        names = []
        for row in rows:
            name = (row.get('name') or row.get('model')) if isinstance(row, dict) else None
            if not isinstance(name, str) or not name:
                raise ValueError('Ollama 실행 모델 목록 응답이 올바르지 않습니다.')
            names.append(name)
        return names

    def _ensure_ollama(self, deadline, keep=None, *, offline_ok=False):
        def same(name):
            return keep is not None and (name == keep or name == keep + ':latest')
        names = self._loaded(deadline, offline_ok=offline_ok)
        unwanted = [name for name in names if not same(name)]
        if not unwanted:
            return
        for name in unwanted:
            try:
                self._http('/api/generate', deadline, {'model': name, 'keep_alive': 0, 'stream': False})
            except (OSError, ValueError):
                raise ValueError('기존 Ollama 모델을 메모리에서 내리지 못했습니다. 새 모델을 로드하지 않았습니다.') from None
        # The unload request is only a request. Confirm before loading anything.
        while any(not same(name) for name in self._loaded(deadline, offline_ok=offline_ok)):
            time.sleep(min(0.05, self._remaining(deadline)))

    def _close_worker(self):
        if self.worker is not None:
            self.worker.close()
            self.worker = None
            self.worker_key = None

    def _check_orphan_worker(self):
        # The child also holds its own lease until it actually exits. If its
        # parent crashed, a new UI cannot load a model during that shutdown gap.
        lease = MachineLease('moses_part3_ai_worker.lock')
        lease.acquire()
        lease.release()

    def _finish_pending_ollama(self, deadline):
        if self.pending_ollama is None:
            return
        try:
            self._http('/api/generate', deadline, {'model': self.pending_ollama,
                'keep_alive': 0, 'stream': False})
            self._ensure_ollama(deadline)
        except (OSError, ValueError):
            raise ValueError('이전 Ollama 요청의 종료를 확인하지 못했습니다. Ollama를 재시작한 뒤 다시 요청하세요.') from None
        self.pending_ollama = None

    @contextlib.contextmanager
    def _turn(self, provider):
        deadline = time.monotonic() + provider.timeout
        if not self.lock.acquire(timeout=self._remaining(deadline)):
            raise ValueError('다른 AI 요청이 진행 중입니다. 잠시 후 다시 요청하세요.')
        try:
            if self._closing:
                raise ValueError('프로그램 종료 중입니다. 새 AI 요청을 시작할 수 없습니다.')
            if provider.generation != self.generation:
                raise ValueError('AI 설정이 변경되었습니다. 대화를 다시 시작해 주세요.')
            self.lease.acquire()
            self._finish_pending_ollama(deadline)
            yield deadline
        finally:
            try:
                if provider.generation != self.generation:
                    self._release_invalidated_model()
            finally:
                # Failed cleanup must remain retryable while its model lease
                # stays held until the worker's exit is actually confirmed.
                self.lock.release()

    def local_chat(self, provider, messages, tools, schema):
        from .local_lora import WorkerClient, check_adapter, resolve_base_model
        with self._turn(provider) as deadline:
            try:
                check_adapter(provider.settings, provider.root)
                resolve_base_model(provider.settings['base_model'], provider.root)
                self._ensure_ollama(deadline, offline_ok=True)
                self.ollama_model = None
                key = (str(provider.root.resolve()), json.dumps(provider.settings, sort_keys=True))
                if self.worker_key != key or self.worker is None or self.worker.process.poll() is not None:
                    self._close_worker()
                    self._check_orphan_worker()
                    self.worker = WorkerClient(provider.settings, provider.root)
                    self.worker.start(deadline)
                    self.worker_key = key
                result = self.worker.request({'op': 'chat', 'messages': messages, 'tools': tools,
                    'response_schema': schema}, deadline)
                if provider.generation != self.generation:
                    raise ValueError('AI 설정이 변경되었습니다. 대화를 다시 시작해 주세요.')
                return result
            except Exception:
                self._close_worker()
                if self.pending_ollama is None:
                    self.lease.release()
                raise

    def ollama_chat(self, provider, messages, tools, schema):
        with self._turn(provider) as deadline:
            self._close_worker()
            try:
                self._check_orphan_worker()
                self._ensure_ollama(deadline, keep=provider.model)
                self.ollama_model = provider.model
                # Keep the existing inference implementation and request contract.
                from .provider import OpenAICompatible
                delegate = OpenAICompatible(self._remaining(deadline), model=provider.model)
                result = delegate.chat(messages, tools, response_schema=schema)
                if provider.generation != self.generation:
                    raise ValueError('AI 설정이 변경되었습니다. 대화를 다시 시작해 주세요.')
                return result
            except (TimeoutError, urllib.error.URLError):
                self.pending_ollama = provider.model
                raise
            except Exception:
                # The old provider maps network failures to ValueError. A failed
                # chat may still be loading remotely; explicitly unload it next
                # time even if /api/ps momentarily reports an empty inventory.
                if self.ollama_model == provider.model and provider.generation == self.generation:
                    self.pending_ollama = provider.model
                if self.pending_ollama is None:
                    self.lease.release()
                raise

    def gguf_chat(self, provider, messages, tools, schema):
        from .gguf_engine import GGUFServer
        from .local_gguf import check_files
        with self._turn(provider) as deadline:
            try:
                check_files(provider.settings, provider.root)
                self._ensure_ollama(deadline, offline_ok=True)
                self.ollama_model = None
                key = ('local_gguf', str(provider.root.resolve()), json.dumps(provider.settings, sort_keys=True))
                if self.worker_key != key or self.worker is None or self.worker.process.poll() is not None:
                    self._close_worker()
                    self._check_orphan_worker()
                    self.worker = GGUFServer(provider.settings, provider.root)
                    self.worker.start(deadline)
                    self.worker_key = key
                result = self.worker.chat(messages, tools, schema, deadline)
                if provider.generation != self.generation:
                    raise ValueError('AI 설정이 변경되었습니다. 대화를 다시 시작해 주세요.')
                return result
            except Exception:
                self._close_worker()
                if self.pending_ollama is None:
                    self.lease.release()
                raise

    def remote_chat(self, provider, operation):
        """Explicit remote provider uses the same serialized model boundary."""
        with self._turn(provider) as deadline:
            try:
                self._close_worker()
                self._check_orphan_worker()
                self._ensure_ollama(deadline, offline_ok=True)
                self.ollama_model = None
                result = operation(deadline)
                if provider.generation != self.generation:
                    raise ValueError('AI 설정이 변경되었습니다. 대화를 다시 시작해 주세요.')
                return result
            finally:
                if self.worker is None and self.pending_ollama is None:
                    self.lease.release()

    def invalidate(self):
        with self.config_lock:
            self.generation += 1
        # Do not hold up a settings save behind a long inference. Its finally
        # unloads the old worker before permitting the next model to load.
        if self.lock.acquire(blocking=False):
            try:
                self._release_invalidated_model()
            finally:
                self.lock.release()

    def _release_invalidated_model(self):
        """Called with the request lock held, also after an in-flight turn."""
        if self.ollama_model or self.pending_ollama:
            # Do not unload a model if another runtime now owns its lease.
            self.lease.acquire()
        self._close_worker()
        self._unload_owned_ollama(time.monotonic() + 2)
        self.lease.release()

    def close(self):
        self.invalidate()

    def _unload_owned_ollama(self, deadline):
        """Unload only models this runtime requested; preserve other users' models."""
        names = [name for name in (self.ollama_model, self.pending_ollama) if name]
        if not names:
            return

        def canonical(name):
            # A registry address can contain ':' without specifying a tag.
            return name if ':' in name.rsplit('/', 1)[-1] else name + ':latest'

        owned = {canonical(name) for name in names}
        try:
            loaded = self._loaded(deadline, offline_ok=True)
            unload = {canonical(name): name for name in loaded if canonical(name) in owned}
            if self.pending_ollama is not None:
                # A timed-out request may still be loading while /api/ps is
                # temporarily empty. Its unload must be sent explicitly.
                unload.setdefault(canonical(self.pending_ollama), self.pending_ollama)
            for name in unload.values():
                try:
                    self._http('/api/generate', deadline,
                        {'model': name, 'keep_alive': 0, 'stream': False})
                except urllib.error.URLError as exc:
                    import errno
                    reason = exc.reason
                    if not (isinstance(reason, OSError) and
                            getattr(reason, 'errno', None) in (errno.ECONNREFUSED, 10061)):
                        raise
            # The unload response only acknowledges a request. Confirm that
            # every owned name is absent before dropping the machine lease.
            while any(canonical(name) in owned for name in self._loaded(deadline, offline_ok=True)):
                time.sleep(min(0.05, self._remaining(deadline)))
        except (OSError, ValueError):
            raise ValueError('Ollama AI 모델 종료를 확인하지 못했습니다. 연결 상태를 확인한 뒤 다시 닫아 주세요.') from None
        self.ollama_model = None
        self.pending_ollama = None

    def shutdown(self, timeout=45):
        """Wait for inference and close only this runtime's owned models."""
        deadline = time.monotonic() + timeout
        with self.config_lock:
            self._closing = True
        if not self.lock.acquire(timeout=max(0, deadline - time.monotonic())):
            with self.config_lock:
                self._closing = False
            raise RuntimeError('AI 요청 종료를 기다리고 있습니다. 잠시 후 X를 눌러 다시 시도하세요.')
        try:
            if self.ollama_model or self.pending_ollama:
                # invalidate() can previously have released an idle lease.
                # Never unload a matching model now owned by another runtime.
                self.lease.acquire()
            self._close_worker()
            self._unload_owned_ollama(deadline)
            self.lease.release()
        except Exception:
            with self.config_lock:
                self._closing = False
            raise
        finally:
            self.lock.release()


class GuardedOllama:
    permission_scope = 'local'
    def __init__(self, delegate):
        self.delegate = delegate
        self.generation = RUNTIME.generation

    def __getattr__(self, name):
        return getattr(self.delegate, name)

    def chat(self, messages, tools, *, response_schema=None):
        return RUNTIME.ollama_chat(self, messages, tools, response_schema)


RUNTIME = ModelRuntime()
atexit.register(RUNTIME.close)
