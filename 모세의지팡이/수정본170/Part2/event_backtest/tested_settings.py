"""The settings a backtest tested, kept with its result (수정본169).

A run keeps the strategy conditions it replayed, the OZ trigger and alert times it applied and the
configuration values its decisions read, so a later change to a recipe or a setting never changes
what an older run shows. The scenario already keeps the rest of the request (period, spread). The
run folder holds a copy from the start, so a running run shows it too.

The conditions are the plan the engine runs (base_frames.engine_plugins): the recipe as written at
the time, or the conditions edited for a virtual entry test, on the base frame the test ran on and
prepared as Part1's loader prepares them (symbols, OZ trigger, trading time).
"""
from __future__ import annotations

from copy import deepcopy

from .settings import digest

FILE = 'tested_settings.json'
# config.txt values a replay's decisions read: the Wonbi band width and the session times (they name
# external-liquidity session levels, trading-time filters and session starts). POINT_<symbol> prices
# the virtual entry.
CONFIG_KEYS = ('WONBI_SIGMA', 'ASIA', 'LONDON', 'NEWYORK', 'MAIN_ASIA', 'MAIN_LONDON', 'MAIN_NEWYORK',
               'OPENING_ASIA', 'OPENING_LONDON', 'OPENING_NEWYORK')


def _folder(name, entry, sources):
    """The strategy file's folder, relative to the program root."""
    if not entry.get('generated'):
        return sources.get(name)
    from strategy_recipe.user_catalog import ROOT, test_directory
    return test_directory(ROOT).relative_to(ROOT).as_posix()


def tested_settings(s, config, applied):
    """{'strategies': {name: what it ran}, 'virtual_entry': policy, 'config': {key: value}} for a scenario.

    `applied` is runner.applied_strategy_settings: each strategy's OZ trigger and final alert times,
    its recipe defaults filled in.
    """
    from .base_frames import base_frame, engine_plugins, move, recipe_entry
    from .virtual_defaults import strategy_frames
    from strategy_recipe.registry import builtin_sources
    names = list(s.get('strategies') or [])
    edited_name = names[0] if s.get('virtual_strategy') is not None else None
    if names == ['ALL']:
        names = list(s.get('enabled_specials') or [])  # every registered strategy runs
    entries = {}
    for name in names:
        try:
            entries[name] = recipe_entry(name)
        except ValueError:
            continue  # commands (OZ, WATCH, ...): no recipe
    try:
        plugins = engine_plugins(config, s) if entries else {}
    except Exception:
        # The replay prepares the same plan, fails the same way and the run reports it; nothing ran to keep.
        # Keeping a record never changes how a run goes.
        plugins = {}
    sources = builtin_sources()
    strategies = {}
    for name, entry in entries.items():
        if name not in plugins:
            continue  # not loaded (no symbol to watch): not run
        written = entry['recipe']['strategy_intent']
        edited = name == edited_name
        target = (s.get('base_frames') or {}).get(name)
        # The conditions before preparation, as the virtual entry panel reads their frames.
        meaning = s['virtual_strategy'] if edited else move(written, base_frame(written), target) if target else written
        timing = applied.get(name) or {}
        strategies[name] = {'title': entry.get('name') or name, 'folder': _folder(name, entry, sources),
                            'recipe_sha256': digest(entry), 'edited': edited,
                            'conditions': deepcopy(plugins[name].recipe['strategy_intent']),
                            'base_frame': target or base_frame(written), 'frames': strategy_frames(meaning),
                            'trigger': timing.get('trigger'), 'time_filters': deepcopy(timing.get('final_alert_time_filters')),
                            'time_source': timing.get('time_source')}
    point = 'POINT_' + str(s.get('symbol', ''))
    # This run's own virtual entry: each base frame compared in one request runs its own (variant_scenarios).
    return {'version': 1, 'strategies': strategies, 'virtual_entry': deepcopy(s.get('virtual_entry')),
            'config': {key: config[key] for key in (*CONFIG_KEYS, point) if key in config}}
