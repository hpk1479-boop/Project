"""Owned, loopback-only llama.cpp server for a configured local GGUF file.

No model is downloaded and no optional GPU/Python ML package is imported here.
The existing Agent receives its original content/tool_calls contract. Tool
requests share one JSON grammar with the final response, avoiding template-
specific native tool syntax competing with the canonical response schema.
"""
from __future__ import annotations

import copy
import http.client
import json
import os
from pathlib import Path
import re
import secrets
import socket
import subprocess
import threading
import time

MAX_REPLY_BYTES = 8 * 1024 * 1024
MAX_DIAGNOSTIC_CHARS = 16384


class _HTTPFailure(ValueError):
    def __init__(self, status, data=None):
        self.status = status
        self.data = data
        super().__init__('GGUF 실행기 HTTP 응답 오류: ' + str(status))


class _DeadlineExceeded(ValueError):
    pass


def _context_overflow(error):
    """Only explicit context-limit codes or prompt-token overflow diagnostics."""
    codes = {'exceed_context_size_error', 'context_length_exceeded', 'context_size_exceeded'}
    if isinstance(error, dict):
        if any(isinstance(error.get(key), str) and error[key].lower() in codes for key in ('type', 'code')):
            return True
        message = error.get('message')
    else:
        message = error
    if not isinstance(message, str):
        return False
    patterns = (
        r'\b(?:the\s+)?(?:request(?:ed)?\s+)?prompt\s+(?:\(\d+\s+tokens?\)\s+)?'
        r'(?:is\s+)?(?:too\s+(?:long|large)|exceeds?|exceeded)\s+(?:for\s+)?(?:the\s+)?'
        r'(?:available\s+|maximum\s+|max\s+|model\s+)?context\b',
        r'\b(?:prompt\s+tokens?|n_prompt_tokens|prompt_tokens)\b.{0,80}'
        r'(?:exceeds?|exceeded|greater\s+than|>)\s*.{0,40}\b(?:n_ctx|context(?:\s+(?:size|length|window))?)\b',
        r'\b(?:maximum|max)\s+context\s+length\s+is\s+\d+\s+tokens?\b.{0,100}'
        r'\b(?:requested|resulted\s+in)\s+\d+\s+tokens?\b',
    )
    return any(re.search(pattern, message.lower()) for pattern in patterns)


class _WindowsJob:
    """Closing this non-inheritable handle kills only the owned process tree."""
    def __init__(self):
        self.handle = None
        if os.name != 'nt':
            return
        import ctypes
        from ctypes import wintypes

        class BasicLimits(ctypes.Structure):
            _fields_ = [('PerProcessUserTimeLimit', ctypes.c_longlong),
                        ('PerJobUserTimeLimit', ctypes.c_longlong),
                        ('LimitFlags', wintypes.DWORD),
                        ('MinimumWorkingSetSize', ctypes.c_size_t),
                        ('MaximumWorkingSetSize', ctypes.c_size_t),
                        ('ActiveProcessLimit', wintypes.DWORD),
                        ('Affinity', ctypes.c_size_t),
                        ('PriorityClass', wintypes.DWORD),
                        ('SchedulingClass', wintypes.DWORD)]

        class IOCounters(ctypes.Structure):
            _fields_ = [(name, ctypes.c_ulonglong) for name in
                        ('ReadOperationCount', 'WriteOperationCount', 'OtherOperationCount',
                         'ReadTransferCount', 'WriteTransferCount', 'OtherTransferCount')]

        class ExtendedLimits(ctypes.Structure):
            _fields_ = [('BasicLimitInformation', BasicLimits), ('IoInfo', IOCounters),
                        ('ProcessMemoryLimit', ctypes.c_size_t), ('JobMemoryLimit', ctypes.c_size_t),
                        ('PeakProcessMemoryUsed', ctypes.c_size_t), ('PeakJobMemoryUsed', ctypes.c_size_t)]

        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        kernel.CreateJobObjectW.restype = wintypes.HANDLE
        kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int,
                                                   ctypes.c_void_p, wintypes.DWORD]
        kernel.SetInformationJobObject.restype = wintypes.BOOL
        kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel.AssignProcessToJobObject.restype = wintypes.BOOL
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.restype = wintypes.BOOL
        handle = kernel.CreateJobObjectW(None, None)
        if not handle:
            raise ValueError('GGUF 실행기의 종료 보호 장치를 만들지 못했습니다.')
        self.kernel = kernel
        self.handle = handle
        limits = ExtendedLimits()
        limits.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not kernel.SetInformationJobObject(handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            self.close()
            raise ValueError('GGUF 실행기의 종료 보호 설정에 실패했습니다.')

    def assign(self, process):
        if self.handle is not None:
            # Popen owns this original native process handle; no PID search or
            # name-based termination can accidentally select another program.
            if not self.kernel.AssignProcessToJobObject(self.handle, int(process._handle)):
                raise ValueError('GGUF 실행기를 종료 보호 장치에 연결하지 못했습니다.')

    def close(self):
        if self.handle is not None:
            self.kernel.CloseHandle(self.handle)
            self.handle = None


def _free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(('127.0.0.1', 0))
        return listener.getsockname()[1]


def _remaining(deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise _DeadlineExceeded('GGUF 모델 로딩·응답 대기 시간이 초과되었습니다. 대기 시간을 확인하세요.')
    return remaining


def _history(messages):
    """Preserve all Agent history using plain JSON instead of native tool tags."""
    result = []
    for original in copy.deepcopy(messages):
        role = original.get('role')
        content = original.get('content') or ''
        if not isinstance(content, str) or role not in ('system', 'user', 'assistant', 'tool'):
            raise ValueError('GGUF에 전달할 대화 형식이 올바르지 않습니다.')
        if original.get('tool_calls'):
            calls = []
            for call in original['tool_calls']:
                function = call.get('function', call)
                arguments = function.get('arguments') or {}
                if isinstance(arguments, str):
                    try:
                        arguments = json.loads(arguments)
                    except ValueError:
                        raise ValueError('GGUF에 전달할 조회 도구 인수가 올바르지 않습니다.') from None
                if not isinstance(arguments, dict):
                    raise ValueError('GGUF에 전달할 조회 도구 인수는 JSON 객체여야 합니다.')
                calls.append({'id': call.get('id'), 'name': function['name'], 'arguments': arguments})
            encoded = json.dumps({'tool_calls': calls}, ensure_ascii=False)
            content = content + '\n' + encoded if content else encoded
        if role == 'tool':
            # The template sees an ordinary user turn but the trusted provider
            # keeps the complete tool response and its correlation ID intact.
            content = '[읽기 전용 조회 도구 결과]\n' + json.dumps({
                'tool_call_id': original.get('tool_call_id'), 'name': original.get('name'),
                'content': content}, ensure_ascii=False)
            role = 'user'
        result.append({'role': role, 'content': content})
    return result


def _parse_reply(text, schema, tools):
    # This pure Python validator is shared with local LoRA. Importing the
    # helper does not import torch/transformers or load a model.
    from .lora_worker import parse_reply
    try:
        return parse_reply(text, schema, tools)
    except ImportError:
        raise ValueError('GGUF 응답 검증 패키지가 없습니다. Part3/requirements-local-gguf.txt를 설치하세요.') from None
    except ValueError as exc:
        raise ValueError(str(exc).replace('로컬 LoRA', 'GGUF')) from None


class GGUFServer:
    def __init__(self, settings, root):
        from .local_gguf import validate_gguf_settings
        self.settings = validate_gguf_settings(settings)
        self.root = Path(root).resolve()
        self.process = None
        self.job = None
        self.port = None
        self.api_key = None
        self.model = None
        self.ready = False
        self.diagnostic = ''
        self._reader = None
        self._diagnostic_lock = threading.Lock()
        self._connection = None

    def _command(self, files):
        model, server, template = files
        command = [str(server), '--model', str(model), '--ctx-size', str(self.settings.get('gguf_context_size', 16384)),
                   '--n-gpu-layers', str(self.settings.get('gguf_gpu_layers', 0)),
                   '--host', '127.0.0.1', '--port', str(self.port), '--parallel', '1',
                   '--jinja', '--no-context-shift', '--no-webui', '--offline',
                   '--api-key', self.api_key]
        if self.settings.get('gguf_threads') is not None:
            command.extend(['--threads', str(self.settings['gguf_threads'])])
        if template is not None:
            command.extend(['--chat-template-file', str(template)])
        return command

    def _drain(self, pipe):
        import codecs
        decoder = codecs.getincrementaldecoder('utf-8')('replace')
        try:
            while True:
                chunk = pipe.read1(4096)
                if not chunk:
                    break
                text = decoder.decode(chunk)
                # Diagnostics are bounded in memory only: no absolute machine
                # paths or generated secret are persisted in a log file.
                for value in (str(self.root), str(self.root).replace('\\', '/'), self.api_key):
                    if value:
                        text = text.replace(value, '[로컬]')
                with self._diagnostic_lock:
                    self.diagnostic = (self.diagnostic + text)[-MAX_DIAGNOSTIC_CHARS:]
        except (OSError, ValueError):
            pass
        finally:
            pipe.close()

    def _request(self, route, deadline, payload=None):
        # http.client ignores proxy environment variables and never follows
        # redirects. Both target address and routes are owned by this class.
        connection = http.client.HTTPConnection('127.0.0.1', self.port, timeout=_remaining(deadline))
        self._connection = connection
        expired = threading.Event()
        wire_socket = [None]
        def expire():
            expired.set()
            # HTTP/1.0 responses can detach connection.sock while their file
            # still owns the socket. Keep its original handle so a delayed
            # body cannot outlive the total deadline after headers arrive.
            sock = wire_socket[0] or connection.sock
            if sock is not None:
                try:
                    sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
            # Windows can keep an already-blocking socket read pending even
            # after shutdown. An inference deadline also cancels its exact
            # owned server, which closes the peer and cannot affect another
            # model process. Short health probes leave loading uninterrupted.
            if route != '/health':
                process = self.process
                if process is not None and process.poll() is None:
                    try:
                        process.kill()
                    except OSError:
                        pass
            connection.close()
        timer = threading.Timer(_remaining(deadline), expire)
        timer.daemon = True
        timer.start()
        try:
            body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode('utf-8')
            headers = {'Authorization': 'Bearer ' + self.api_key, 'Content-Type': 'application/json'}
            connection.request('GET' if payload is None else 'POST', route, body=body, headers=headers)
            wire_socket[0] = connection.sock
            response = connection.getresponse()
            limit = MAX_REPLY_BYTES if response.status == 200 else 8192
            chunks = []
            size = 0
            while True:
                if expired.is_set():
                    _remaining(deadline)
                if response.isclosed():
                    break
                if wire_socket[0] is not None:
                    wire_socket[0].settimeout(_remaining(deadline))
                chunk = response.read1(min(65536, limit + 1 - size))
                if not chunk:
                    break
                chunks.append(chunk)
                size += len(chunk)
                if size > limit:
                    raise ValueError('GGUF 실행기 응답이 너무 큽니다. 응답 길이 설정을 확인하세요.')
            _remaining(deadline)
            try:
                data = json.loads(b''.join(chunks).decode('utf-8'))
            except (UnicodeError, ValueError):
                if response.status != 200:
                    raise _HTTPFailure(response.status) from None
                raise ValueError('GGUF 실행기 응답이 올바른 JSON이 아닙니다.') from None
            if response.status != 200:
                raise _HTTPFailure(response.status, data)
            if not isinstance(data, dict):
                raise ValueError('GGUF 실행기 응답은 JSON 객체여야 합니다.')
            return data
        finally:
            timer.cancel()
            connection.close()
            if self._connection is connection:
                self._connection = None

    def start(self, deadline):
        if self.ready and self.process is not None and self.process.poll() is None:
            _remaining(deadline)
            return
        if self.process is not None:
            self.close()
        from .local_gguf import check_files
        files = check_files(self.settings, self.root)
        _remaining(deadline)
        self.port = _free_port()
        # Hex cannot begin with '-' and be mistaken for a CLI option value.
        self.api_key = secrets.token_hex(32)
        self.diagnostic = ''
        self.job = _WindowsJob()
        environment = {key: value for key, value in os.environ.items()
                       if not key.upper().startswith(('LLAMA_ARG_', 'LLAMA_API_KEY'))}
        kwargs = {'stdin': subprocess.DEVNULL, 'stdout': subprocess.PIPE, 'stderr': subprocess.STDOUT,
                  'cwd': str(self.root), 'env': environment}
        if os.name == 'nt':
            kwargs['creationflags'] = subprocess.CREATE_NO_WINDOW
        try:
            try:
                self.process = subprocess.Popen(self._command(files), **kwargs)
            except OSError:
                raise ValueError('GGUF 실행기를 시작하지 못했습니다. 실행 파일과 동봉 DLL·실행 환경을 확인하세요.') from None
            self.job.assign(self.process)
            self._reader = threading.Thread(target=self._drain, args=(self.process.stdout,), daemon=True)
            self._reader.start()
            while True:
                _remaining(deadline)
                code = self.process.poll()
                if code is not None:
                    raise ValueError('GGUF 실행기가 모델 준비 전에 종료되었습니다 (종료 코드 ' + str(code) +
                                     '). 모델 호환성·메모리·llama.cpp 버전·DLL을 확인하세요.')
                try:
                    health = self._request('/health', min(deadline, time.monotonic() + 1))
                    if health.get('status') != 'ok':
                        raise ValueError('GGUF 실행기의 준비 상태 응답이 올바르지 않습니다.')
                    # Health is public in llama.cpp. Prove this listener knows
                    # our fresh secret before sending any strategy content.
                    models = self._request('/v1/models', deadline).get('data')
                    if not isinstance(models, list) or len(models) != 1 or not isinstance(models[0], dict):
                        raise ValueError('GGUF 실행기의 모델 목록 응답이 올바르지 않습니다.')
                    identifier = models[0].get('id')
                    if not isinstance(identifier, str) or not identifier:
                        raise ValueError('GGUF 실행기의 모델 식별자가 없습니다.')
                    if self.process.poll() is not None:
                        raise ValueError('GGUF 실행기가 모델 준비 중 종료되었습니다.')
                    self.model = identifier
                    self.ready = True
                    return
                except _HTTPFailure as exc:
                    if exc.status != 503:
                        raise ValueError('GGUF 실행기 준비 확인에 실패했습니다 (HTTP ' + str(exc.status) + ').') from None
                except _DeadlineExceeded:
                    # A single slow health probe is retried until the overall
                    # configured model-load deadline, rather than cutting a
                    # 90 second first load down to one second.
                    _remaining(deadline)
                except (ConnectionError, TimeoutError, OSError, http.client.HTTPException):
                    pass
                time.sleep(min(0.05, _remaining(deadline)))
        except Exception:
            self.close()
            raise

    def chat(self, messages, tools, schema, deadline):
        if not self.ready or self.process is None or self.process.poll() is not None:
            raise ValueError('GGUF 모델이 아직 준비되지 않았거나 실행기가 종료되었습니다. 다시 요청하세요.')
        from .lora_worker import reply_schema
        from .schema_prompt import schema_prompt
        expected = reply_schema(schema, tools)
        history = _history(messages)
        instruction = ('응답은 지정된 JSON schema를 만족하는 JSON 객체 하나만 반환하세요. '
            '조회가 필요하면 tool_calls에 도구명과 arguments를 반환하고, 최종 해석은 원래 응답 형식으로 반환하세요. '
            '설명, Markdown, XML 도구 태그를 쓰지 마세요.\n' + schema_prompt(schema))
        if tools:
            instruction += '\n사용할 수 있는 읽기 전용 도구:\n' + json.dumps(tools, ensure_ascii=False)
        if history and history[0]['role'] == 'system':
            history[0]['content'] += '\n' + instruction
        else:
            history.insert(0, {'role': 'system', 'content': instruction})
        body = {'model': self.model, 'messages': history, 'stream': False, 'temperature': 0.1,
                'response_format': {'type': 'json_object', 'schema': expected},
                'chat_template_kwargs': {'enable_thinking': False}, 'reasoning_effort': 'none',
                'parse_tool_calls': False, 'max_tokens': self.settings.get('max_new_tokens', 4096)}
        try:
            result = self._request('/v1/chat/completions', deadline, body)
            choices = result.get('choices')
            if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], dict):
                raise ValueError('GGUF 실행기의 AI 응답 목록이 올바르지 않습니다.')
            choice = choices[0]
            if choice.get('finish_reason') == 'length' or result.get('truncated'):
                raise ValueError('GGUF 응답이 처리 길이 제한에 도달했습니다. 새 대화를 시작하거나 대화·응답 길이를 확인하세요.')
            message = choice.get('message')
            if not isinstance(message, dict) or not isinstance(message.get('content'), str) or message.get('tool_calls'):
                raise ValueError('GGUF 실행기의 AI 응답 형식이 올바르지 않습니다.')
            return _parse_reply(message['content'], schema, tools)
        except _HTTPFailure as exc:
            error = exc.data.get('error') if isinstance(exc.data, dict) else None
            text = json.dumps(error, ensure_ascii=False).lower()
            self.close()
            if any(word in text for word in ('out of memory', 'outofmemory', 'allocation failed')):
                raise ValueError('GGUF 실행 메모리가 부족합니다. 문맥 길이·GPU 사용 설정을 확인하세요.') from None
            if _context_overflow(error):
                raise ValueError('대화가 GGUF 모델의 처리 길이를 초과합니다. 새 대화를 시작하거나 문맥 길이를 늘려 주세요.') from None
            raise ValueError('GGUF 추론 요청에 실패했습니다 (HTTP ' + str(exc.status) +
                             '). 실행기 버전·대화 템플릿·모델 호환성을 확인하세요.') from None
        except (TimeoutError, OSError, http.client.HTTPException):
            self.close()
            if deadline <= time.monotonic():
                raise ValueError('GGUF 모델 응답 대기 시간이 초과되었습니다. 대기 시간을 확인하세요.') from None
            raise ValueError('GGUF 실행기와 연결이 끊겼습니다. 다시 요청하세요.') from None
        except Exception:
            self.close()
            raise

    def close(self):
        connection = self._connection
        if connection is not None:
            connection.close()
        process = self.process
        try:
            if process is not None and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=2)
            elif process is not None:
                process.wait(timeout=2)
        finally:
            # Includes any descendants owned by this exact Windows job.
            if self.job is not None:
                self.job.close()
                self.job = None
        if process is not None and process.poll() is None:
            raise ValueError('GGUF 실행기 종료를 확인하지 못했습니다. 잠시 후 다시 종료하세요.')
        self.process = None
        self.ready = False
        self.model = None
        self.api_key = None
        if self._reader is not None:
            self._reader.join(timeout=1)
            self._reader = None
