"""Atomic symbol publication and read-only event-time views."""
from types import MappingProxyType
from .model import FeedSnapshot, freeze


class BoardView:
    def __init__(self, feeds, observed, states, facts, requester, subscriptions, health=None, source_time=None, frame_cache=None, calculations=None):
        self.feeds = MappingProxyType(dict(feeds))
        self.observed = MappingProxyType(dict(observed))
        self.processor_states = freeze(states)
        self._facts = facts
        self._requester = requester
        self._subscriptions = subscriptions
        self.health = freeze(health or {})
        self.source_time = source_time
        self._frame_cache = {} if frame_cache is None else frame_cache
        self._calculations = {} if calculations is None else calculations

    def snapshot(self, symbol, timeframe):
        return self.feeds[(symbol, timeframe)]

    @property
    def publication_token(self):
        """Opaque identity of the publication shared by its read-only views."""
        return id(self._frame_cache)

    def fact(self, name, symbol, timeframe):
        if name not in self._subscriptions.facts:
            raise ValueError('Fact not declared in subscriptions: ' + name)
        return self._facts.get(symbol, timeframe, self.snapshot(symbol, timeframe), name, self._requester)

    def processor(self, name):
        if name not in self._subscriptions.processor_states:
            raise ValueError('Processor state not declared: ' + name)
        return self.processor_states[name]


class Board:
    def __init__(self, facts):
        self._feeds = {}
        self._observed = {}
        self.facts = facts
        self._health = {}
        self._frame_cache = {}
        self._calculations = {}
        self._projections = {}

    def set_health(self,event):
        symbol=event.payload.get('symbol')
        if not symbol:return
        status=event.payload.get('status')
        if status:
            self._health[symbol]={'status':status,'source_time':event.source_time}
            self._frame_cache = {}
            self._calculations = {}
        if status=='RECONNECT':
            changed=[key for key in self._feeds if key[0]==symbol]
            for key in changed:self._feeds.pop(key);self._observed.pop(key,None)
            self.facts.remove(changed)

    def commit(self, event):
        symbol = event.payload['symbol']
        updates = event.payload['feeds']
        if not isinstance(symbol, str) or not updates:
            raise ValueError('market bundle needs one symbol and feeds')
        if not all(isinstance(tf, str) and isinstance(value, FeedSnapshot) for tf, value in updates.items()):
            raise TypeError('market bundle contains invalid feed')
        changed = {(symbol, tf): value for tf, value in updates.items()}
        new_feeds = dict(self._feeds); new_feeds.update(changed)
        new_observed = dict(self._observed)
        new_observed.update({key: event.source_time for key in changed})
        self._feeds, self._observed = new_feeds, new_observed
        # Never carry a prepared frame across a MARKET_BUNDLE, including a
        # publication reusing the same Snapshot objects or another symbol.
        self._frame_cache = {}
        self._health[symbol]={'status':'FRESH','source_time':event.source_time}
        self.facts.invalidate(changed)

    def view(self, states, consumer, source_time=None):
        subscriptions = consumer.subscriptions()
        # Engine checkpoints own complete mutable internals. Consumers see only
        # declared processors' public projections, sealed for this event.
        visible = {}
        for name in subscriptions.processor_states:
            if name not in states:continue
            if name not in self._projections:
                self._projections[name]=freeze(states[name].get('__board__',states[name]))
            visible[name]=self._projections[name]
        return BoardView(self._feeds, self._observed, visible, self.facts,
                         consumer.__class__.__module__, subscriptions,self._health,source_time,
                         self._frame_cache,self._calculations)

    def processor_changed(self,name):
        self._projections.pop(name,None)

    def checkpoint(self):
        return {'feeds': dict(self._feeds), 'observed': dict(self._observed),'health':dict(self._health)}

    def restore(self, state):
        self._feeds = dict(state['feeds']); self._observed = dict(state['observed'])
        self._health = dict(state.get('health',{}))
        self._frame_cache = {}; self._calculations = {}
        self._projections = {}
        self.facts.clear(); self.facts.invalidate(self._feeds)
