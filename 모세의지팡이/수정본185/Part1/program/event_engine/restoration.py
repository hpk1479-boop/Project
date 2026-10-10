"""Canonical polling restore functions applied to startup memory, once."""
from domain_memory import memory_scope
from .domain_support import plain


def stream_state(protocol,family,files):
    with memory_scope(dict(files)):
        stream=protocol.FactStream(family.lower()+'_stream.json',family)
        return {'generation':stream.generation,'sequence':stream.sequence}


def indicator_state(module,protocol,state,files):
    with memory_scope(dict(files)):
        registry=module.IndicatorWatchRegistry()
        state['watches']={wid:{'symbol':symbol,'source_tf':tf,'requested_fields':list(fields)}
                          for wid,(symbol,tf,fields) in registry._watches.items()}
        state['pending']=protocol.Records('trend_pending_events.json').all()
    state['stream']=stream_state(protocol,'TREND',files)


def fvg_state(module,protocol,state,files):
    with memory_scope(dict(files)):
        registry=module.FVGWatchRegistry();state['watches']=dict(registry._watches)
    state['stream']=stream_state(protocol,'FVG',files)


def sweep_state(module,protocol,state,files):
    with memory_scope(dict(files)):
        registry=module.SweepWatchRegistry()
        state['watches']={wid:plain(spec) for wid,spec in registry._watches.items()}
        core=module.SweepEngine(staff_client=False,manager_client=False,event_publisher=False,
                                event_state=state.setdefault('core',{}))
        core._load_detector_state()
    state['stream']=stream_state(protocol,'SWEEP',files)
    state['__board__']={'registry':state['watches']}
