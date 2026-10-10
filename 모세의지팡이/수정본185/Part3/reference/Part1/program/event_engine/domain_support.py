"""Pure in-memory protocol ports shared by the E2 domain adapters."""
from copy import deepcopy
from collections.abc import Mapping
from dataclasses import asdict,is_dataclass
import numpy as np
from .model import Kind, Signal


def plain(value):
    """Primitive event data; no lossy repr fallback for mutable domain values."""
    if isinstance(value,Mapping):return {k:plain(v) for k,v in value.items()}
    if isinstance(value,(list,tuple)):return [plain(v) for v in value]
    if isinstance(value,(set,frozenset)):return [plain(v) for v in sorted(value,key=str)]
    if isinstance(value,np.generic):return value.item()
    if is_dataclass(value):return plain(asdict(value))
    if value is None or isinstance(value,(str,int,float,bool,bytes)):return value
    # pd.Timestamp is a source-time value, not a clock read.
    if type(value).__name__=='Timestamp':return value.isoformat()
    raise TypeError('unsupported domain event value: '+type(value).__name__)


class MemoryRecords:
    def __init__(self,state):self.state=state
    def get(self,key,default=None):return deepcopy(self.state.get(key,default))
    def put(self,key,value):self.state[key]=deepcopy(value)
    def all(self):return deepcopy(self.state)
    def remove(self,key):self.state.pop(key,None)


class FactPort:
    """Canonical FactStream prepare/snapshot, with engine-owned sequence."""
    def __init__(self,protocol,family,state):
        self.stream=protocol.FactStream.__new__(protocol.FactStream)
        self.stream.family=family
        self.stream.generation=state.get('generation',1)
        self.stream.sequence=state.get('sequence',0)
        self.state=state;self.events=[]
    def send(self,event):
        self.stream.prepare(event)
        self.events.append(plain(event))
        self.state.update(generation=self.stream.generation,sequence=self.stream.sequence)
        return {'ok':True,'delivered':True}


def command_of(event):
    if event.kind != Kind.SIGNAL:return None
    content=event.payload.get('content',{})
    return content.get('command') if content.get('type')=='WATCH_COMMAND' else None


def accepts_command(event, actions):
    if event.kind == Kind.MARKET_BUNDLE:return True
    command = command_of(event)
    return command is not None and command.get('action') in actions


def subscription_command(event,state,family):
    """Keep a shared legacy watch until every consumer releases it.

    Repeated registrations by one owner replace that owner's requirements.
    A cancellation by another consumer must never revoke an active owner.
    """
    command=command_of(event)
    if command is None:return None
    action=command.get('action');wid=command.get('watch_id')
    owner=event.payload.get('strategy',event.source)
    owners=state.setdefault('subscription_owners',{})
    if action==family+'_WATCH':
        owners.setdefault(wid,{})[owner]=plain(command)
    elif action=='CANCEL_'+family:
        current=owners.get(wid,{})
        current.pop(owner,None)
        if not current:
            owners.pop(wid,None)
            return plain(command)
    else:return plain(command)
    current=owners.get(wid,{})
    if not current:return None
    merged=deepcopy(next(iter(current.values())))
    for field in ('requested_fields','levels'):
        if any(field in value for value in current.values()):
            merged[field]=list(dict.fromkeys(item for value in current.values() for item in value.get(field,())))
    return merged


def emit_facts(emit,symbol,family,events):
    for index,item in enumerate(events):
        key=str(item.get('event_id') or f"{family}:{item.get('kind')}:{item.get('source_tf')}:{index}")
        emit(Signal(item.get('symbol') or symbol,key,{'type':'DOMAIN_FACT','family':family,'event':plain(item)}))
