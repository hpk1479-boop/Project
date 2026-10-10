"""Single ingress: raw arrival FIFO -> sequence assignment -> ready FIFO.

Numbering is deferred until the head input can be expanded with due timers.
Thus queued future market inputs cannot jump ahead of timers created by the
preceding strategy invocation. No numbered event is reordered or discarded.
"""
from collections import deque
from threading import Lock, Condition
from .model import Input, Event
from .metrics import measured_time


class IngressSequencer:
    def __init__(self):
        self._arrivals = deque()
        self._ready = deque()
        self._lock = Lock()
        self._available = Condition(self._lock)
        self._last_seq = 0
        self._owner = None

    def bind(self, owner):
        if self._owner is not None:
            raise RuntimeError('Ingress already has an engine')
        self._owner = owner

    def post(self, kind, *, source, source_seq, source_time, payload, engine_time=None):
        item = Input(source, source_seq, source_time, kind, payload,
                     measured_time() if engine_time is None else engine_time)
        with self._lock:
            self._arrivals.append(item)
            self._available.notify()

    def wait(self, timeout=None):
        with self._available:
            return self._available.wait_for(lambda: bool(self._arrivals), timeout)

    def __len__(self):
        with self._lock:
            return len(self._arrivals) + len(self._ready)

    def take_input(self, owner):
        self._check(owner)
        with self._lock:
            return self._arrivals.popleft() if self._arrivals else None

    def number(self, owner, item):
        self._check(owner)
        if not isinstance(item, Input):
            raise TypeError('Only immutable ingress Inputs may be sequenced')
        with self._lock:
            self._last_seq += 1
            self._ready.append(Event(self._last_seq, item.engine_time, item.source,
                                     item.source_seq, item.source_time, item.kind, item.payload))

    def take_ready(self, owner):
        self._check(owner)
        with self._lock:
            return self._ready.popleft()

    def _check(self, owner):
        if owner is not self._owner or owner is None:
            raise RuntimeError('Only the bound engine drains ingress')

    def checkpoint(self, owner):
        self._check(owner)
        with self._lock:
            if self._arrivals or self._ready:
                raise RuntimeError('Checkpoint requires drained ingress')
            return self._last_seq

    def restore(self, owner, seq):
        self.checkpoint(owner)
        self._last_seq = seq
