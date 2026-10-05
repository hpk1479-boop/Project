"""Explicit clock/identity port: polling defaults, source-time-only event scope.

This boundary is the only wall-clock fallback. Event consumers must enter a
scope; context-local bindings cannot alter a concurrent polling thread.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from types import ModuleType
import datetime as _datetime
import time as _time
import uuid as _uuid
import hashlib

_scope=ContextVar('moses_event_domain_clock',default=None)


@contextmanager
def event_scope(source_time_ms,identity_scope,identity_state):
    token=_scope.set((int(source_time_ms),str(identity_scope),identity_state))
    try:yield
    finally:_scope.reset(token)


def _seconds():
    bound=_scope.get()
    return None if bound is None else bound[0]/1000


class TimePort(ModuleType):
    def __init__(self):super().__init__('time')
    def time(self):
        value=_seconds();return _time.time() if value is None else value
    def time_ns(self):
        value=_scope.get()
        if value is None:return _time.time_ns()
        # Canonical callers use time_ns for ids/issued_at, never for decisions.
        # Several commands can share one source millisecond. Keep their ids
        # unique and deterministic, including after checkpoint and old timers.
        stamp=max(value[0]*1000000,value[2].get('logical_identity_ns',-1)+1)
        value[2]['logical_identity_ns']=stamp
        return stamp
    def monotonic(self):
        value=_seconds();return _time.monotonic() if value is None else value
    def perf_counter(self):
        value=_seconds();return _time.perf_counter() if value is None else value
    def sleep(self,seconds):
        if _scope.get() is not None:raise RuntimeError('event decisions cannot sleep')
        return _time.sleep(seconds)
    def __getattr__(self,name):return getattr(_time,name)


class EventDateTime(_datetime.datetime):
    @classmethod
    def now(cls,tz=None):
        value=_seconds()
        if value is None:return _datetime.datetime.now(tz)
        zone=tz or _datetime.timezone(_datetime.timedelta(hours=9))
        result=_datetime.datetime.fromtimestamp(value,zone)
        return result if tz is not None else result.replace(tzinfo=None)
    @classmethod
    def utcnow(cls):
        value=_seconds()
        return _datetime.datetime.utcnow() if value is None else _datetime.datetime.fromtimestamp(value,_datetime.timezone.utc).replace(tzinfo=None)


class DateTimePort(ModuleType):
    def __init__(self):super().__init__('datetime')
    datetime=EventDateTime
    def __getattr__(self,name):return getattr(_datetime,name)


class IdentityPort(ModuleType):
    def __init__(self):super().__init__('uuid')
    def uuid4(self):
        bound=_scope.get()
        if bound is None:return _uuid.uuid4()
        timestamp,owner,state=bound
        order=state.get('identity_sequence',0)+1;state['identity_sequence']=order
        raw=f'{owner}:{timestamp}:{order}'.encode('utf-8')
        return _uuid.UUID(hex=hashlib.sha256(raw).hexdigest()[:32])
    def __getattr__(self,name):return getattr(_uuid,name)


time=TimePort()
datetime=DateTimePort()
uuid=IdentityPort()
