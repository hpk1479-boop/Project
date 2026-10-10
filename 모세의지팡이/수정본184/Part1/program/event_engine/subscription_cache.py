"""Bounded reuse of complete immutable subscription descriptions.

Callers still compute their current selection on every request. No consumer,
selection object, market state, or acceptance decision is retained here.
"""
from functools import lru_cache
from .model import Kind, Resolution, Subscriptions


@lru_cache(maxsize=256)
def _cached(symbols, timeframes, facts, processor_states, kinds, resolution, boundaries):
    return Subscriptions(symbols=symbols, timeframes=timeframes, facts=facts,
                         processor_states=processor_states, kinds=kinds,
                         resolution=resolution, boundaries=boundaries)


def cached_subscriptions(*, symbols=(), timeframes=(), facts=(), processor_states=(),
                         kinds=(Kind.MARKET_BUNDLE, Kind.TIMER),
                         resolution=Resolution.TICK, boundaries=()):
    # Match Subscriptions' tuple/enum normalization, including reassigned lists.
    args = (tuple(symbols), tuple(timeframes), tuple(facts), tuple(processor_states),
            tuple(kinds), Resolution(resolution), tuple(boundaries))
    try:
        return _cached(*args)
    except TypeError:
        # A future unhashable descriptor must retain the original constructor
        # behavior rather than turn a cache limitation into a strategy error.
        return Subscriptions(symbols=args[0], timeframes=args[1], facts=args[2],
                             processor_states=args[3], kinds=args[4],
                             resolution=args[5], boundaries=args[6])
