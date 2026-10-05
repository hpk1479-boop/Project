"""Engine-owned memory port for the existing JSON state protocol.

No files are created in an event scope. This is neither Journal nor Outbox;
the supplied dictionary belongs to the engine checkpoint.
"""
from contextlib import contextmanager
from contextvars import ContextVar
import json
from pathlib import Path,PurePath

_store=ContextVar('moses_domain_memory',default=None)
_checkpoint_only=ContextVar('moses_skip_checkpoint_only',default=False)
# These are loaded at kernel/plugin construction only. Active logic reads the
# resident specs, watch links, children, chains and SPECIAL4 runtime state.
CHECKPOINT_ONLY=frozenset(('composer_private_watches.json','composer_timed_chains.json',
    'composer_active_oz_watches.json','composer_fvg_created_watches.json','special4_state.json'))


@contextmanager
def memory_scope(state,*,skip_checkpoint_only=False):
    token=_store.set(state)
    skip=_checkpoint_only.set(skip_checkpoint_only)
    try:yield
    finally:_store.reset(token);_checkpoint_only.reset(skip)


def active():return _store.get() is not None
def module_directory(module_file):
    # Event state uses logical keys, so resolving a host filesystem path is
    # unnecessary. The default polling path keeps its original resolution.
    return PurePath(module_file).parent if active() else Path(module_file).resolve().parent
def _key(path):return str(path).replace('\\','/').rsplit('/',1)[-1]
def exists(path,*,file_only=True):
    if not active():return path.is_file() if file_only else path.exists()
    return _key(path) in _store.get()
def read(path):
    key=_key(path)
    if _checkpoint_only.get() and key in CHECKPOINT_ONLY:
        raise RuntimeError('checkpoint-only state unexpectedly read during backtest dispatch: '+key)
    if key not in _store.get():raise FileNotFoundError(key)
    return json.loads(_store.get()[key])
def write(path,value,*,default=str,allow_nan=False,indent=None):
    if _checkpoint_only.get() and _key(path) in CHECKPOINT_ONLY:return
    _store.get()[_key(path)]=json.dumps(value,ensure_ascii=False,default=default,allow_nan=allow_nan,indent=indent)
