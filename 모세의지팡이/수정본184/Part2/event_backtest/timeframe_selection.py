"""Which timeframes a replay's strategies can read: the others are not published (수정본131).

Built once, after the strategies are loaded and the WATCH commands registered. Every timeframe
written in a selected recipe, a configured chain, a registered command or a composer watch is
included, with OZ's own feeds, the level timeframes when levels or SWEEP watches are used, and
1m (the latest quote of alerts).
None means every timeframe: unrestricted strategy sets, OZ without a declared plan, or anything
this function cannot read. A strategy that still asks for a missing timeframe is caught by the
board (LimitedFeeds) and the period is replayed with every feed, so this list only decides speed.
"""
from collections.abc import Mapping

import numpy as np

SWEEP_LEVELS = ('1d', '4h', '8h', '5m')
# Words that bring the level timeframes: external-liquidity steps and gates, price/liquidity levels,
# and SWEEP watches (commands, or OZ external liquidity). Without them SWEEP reads no feed.
LEVEL_WORDS = {'EXTERNAL_LIQUIDITY', 'EXTERNAL_LIQUIDITY_TOUCH', 'PRICE_LEVEL', 'LIQUIDITY_LEVEL',
               'SWEEP_WATCH', 'sweep', 'level_gate'}


def _strings(value, seen, depth=0):
    if isinstance(value, str):
        yield value
        return
    if depth > 6 or value is None or isinstance(value, (bool, int, float, bytes, np.ndarray, np.generic)) or id(value) in seen:
        return
    seen.add(id(value))
    if isinstance(value, Mapping):
        for key, item in value.items():
            yield from _strings(key, seen, depth + 1)
            yield from _strings(item, seen, depth + 1)
    elif isinstance(value, (list, tuple, set, frozenset)):
        for item in value:
            yield from _strings(item, seen, depth + 1)
    elif hasattr(value, '__dict__'):
        yield from _strings(vars(value), seen, depth + 1)
    elif hasattr(value, '__slots__'):
        for name in value.__slots__:
            yield from _strings(getattr(value, name, None), seen, depth + 1)


def required_timeframes(engine, registrations=()):
    from command_interpreter import MT5_TIMEFRAMES
    from strategy_recipe.registry import preset_entry
    plan = engine.selection
    if plan is None or plan.unrestricted:
        return None
    names = set(MT5_TIMEFRAMES)
    found = {'1m'}
    processors = {p.name: p for p in engine.processors}
    if 'OZ_STATE' in processors:
        oz = getattr(processors['OZ_STATE'], 'selection', None)
        if oz is None:
            return None                      # OZ then evaluates every feed it is given
        found.update(oz.feeds)
    try:
        sources = [preset_entry(name)['recipe']['strategy_intent'] for name in plan.specials]
    except ValueError:
        return None                          # loaded outside the presets (Part3 generated): every feed
    sources += [definition for _, definition in plan.official]
    sources += list(registrations)
    for kernel in engine.strategy_state.get('COMPOSER', {}).get('kernels', {}).values():
        manager = kernel.manager
        sources += [getattr(manager, 'manual_specs', {}), getattr(manager, 'timed_chains', {}),
                    getattr(manager, 'fvg_created_watches', {})]
        if hasattr(manager, '_desired_subscriptions_locked'):
            sources.append(manager._desired_subscriptions_locked())
    seen = set()
    for text in _strings(sources, seen):
        if text.lower() in names:
            found.add(text.lower())
        elif text == 'PREV_DAY_TOUCH':
            found.add('1d')
        elif text in LEVEL_WORDS:
            found.update(SWEEP_LEVELS)
    for consumer in (*engine.strategies, *engine.processors):
        found.update(tf for tf in consumer.subscriptions().timeframes if tf in names)
    return frozenset(found)
