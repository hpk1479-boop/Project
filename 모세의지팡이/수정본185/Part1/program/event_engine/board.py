"""Atomic symbol publication and read-only event-time views."""
from collections.abc import Mapping
from types import MappingProxyType
from .model import FeedSnapshot, freeze


class LimitedFeeds(Mapping):
    """Feeds of a replay that publishes only some timeframes.

    Asking for any other timeframe, or listing the feeds of a symbol without its 1m feed
    (the latest-quote choice then depends on the others), is recorded in ``misses``;
    the replay then repeats that period with every feed, so results never depend on the limit.
    """
    __slots__ = ('_data', '_limit', '_misses')
    def __init__(self, data, limit, misses):
        self._data = data; self._limit = limit; self._misses = misses
    def _check(self, key):
        if isinstance(key, tuple) and len(key) == 2 and key[1] not in self._limit:
            self._misses.add(str(key[1]))
    def __getitem__(self, key):
        self._check(key); return self._data[key]
    def __contains__(self, key):
        self._check(key); return key in self._data
    def get(self, key, default=None):
        self._check(key); return self._data.get(key, default)
    def __iter__(self):
        for symbol in {key[0] for key in self._data}:
            if (symbol, '1m') not in self._data:
                self._misses.add('1m missing while listing feeds')
        return iter(self._data)
    def __len__(self):
        return len(self._data)


class BoardView:
    def __init__(self, feeds, observed, states, facts, requester, subscriptions, health=None, source_time=None, frame_cache=None, calculations=None,
                 timeframe_limit=None, timeframe_misses=None):
        self.feeds = MappingProxyType(dict(feeds))
        self.observed = MappingProxyType(dict(observed))
        if timeframe_limit is not None:
            self.feeds = LimitedFeeds(self.feeds, timeframe_limit, timeframe_misses)
            self.observed = LimitedFeeds(self.observed, timeframe_limit, timeframe_misses)
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
        # Set by a replay that publishes only the timeframes its strategies read (None: all).
        self.timeframe_limit = None
        self.timeframe_misses = set()

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
                         self._frame_cache,self._calculations,self.timeframe_limit,self.timeframe_misses)

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
