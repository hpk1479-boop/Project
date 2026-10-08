"""Base frames (기준 프레임) of a virtual-entry backtest: the same strategy tested on another frame.

The virtual entry's base frame is the frame its test runs on. The recipe's own frame tests the
strategy as written; another frame moves the whole test, strategy conditions and entry, by the
ladder: each frame keeps its level (기준·중위·상위·최상위). SPECIAL9 (1m cross and touch, 15m
trend) on 2m is a 2m cross and touch with a 20m trend, entered on 2m candles. Only the replay's
copy moves: recipe files, alert-only runs and Part1 stay as written, and Part1 code is only called.
Middle and upper frames are the OZ monitor's own map (oz_engine.common.TF_MAP); top is added here.
"""
import functools
import sys
from types import SimpleNamespace
from .settings import PROGRAM

if str(PROGRAM) not in sys.path:
    sys.path.insert(0, str(PROGRAM))
# Part1's frame seconds as the command language reads them: the same values as indicator_facts.tf_seconds
# for every frame, without loading pandas when the backtest screen opens (수정본170).
from command_interpreter import tf_seconds
from oz_engine.common import TF_MAP

LEVELS = ('기준', '중위', '상위', '최상위')
TOP = {'1m': '15m', '2m': '20m', '3m': '30m', '4m': '1h', '5m': '1h', '6m': '1h', '10m': '2h',
       '12m': '2h', '15m': '4h', '20m': '4h', '30m': '8h', '1h': '12h', '2h': '1d', '3h': '1d',
       '4h': None}
LADDER = {base: (base, *TF_MAP[base], TOP[base]) for base in TF_MAP}
REFERENCES = ('SOURCE', 'FINAL')
# Waits written in seconds are times, not frames: they cannot follow a base frame.
WAITS = ('within_sec', 'final_window_sec', 'seconds')


def label(tf):
    from command_interpreter import WATCH_TF_MAP
    return WATCH_TF_MAP.get(tf, str(tf))


def sorted_frames(values):
    """Distinct real frames, smallest first; SOURCE/FINAL references are not frames."""
    return sorted({tf for tf in values if tf not in REFERENCES}, key=tf_seconds)


def unit_alert_frames(unit):
    """The frames one unit's strategy conditions complete on."""
    final = unit['final']
    steps = [*unit.get('steps', ()), *unit.get('after_conditions', ())]
    if final['kind'] == 'OZ':
        frames = set(final.get('tfs', ()))
        if 'SOURCE' in frames:
            # The OZ watch opens on the frame of the step it follows.
            ref = (final.get('level_gate') or {}).get('ref')
            sources = [s for s in steps if ref is None or s.get('capture') == ref]
            frames |= {tf for step in sources for tf in step.get('tfs', ())}
        return sorted_frames(frames)
    # A notification's frame is the smallest frame among its matched conditions.
    minima = {None}
    for step in steps:
        tfs = sorted_frames(step.get('tfs', ()))
        if not tfs:
            continue
        options = [tfs] if step.get('tf_combine') == 'ALL' else [[tf] for tf in tfs]
        minima = {min([*(() if low is None else (low,)), *option], key=tf_seconds)
                  for low in minima for option in options}
    return sorted_frames(tf for tf in minima if tf is not None)


def alert_frames(meaning):
    """The frames a recipe's strategy conditions complete on (each alert's own frame)."""
    from strategy_recipe.registry import units
    return sorted_frames(tf for unit in units(meaning) for tf in unit_alert_frames(unit))


def _written(value):
    """Every frame written in a recipe: tf/tfs/price_tf of its steps, final and lifecycle parts."""
    if isinstance(value, dict):
        for key, item in value.items():
            if key in ('tf', 'price_tf') and isinstance(item, str):
                yield item
            elif key == 'tfs' and isinstance(item, list):
                yield from (tf for tf in item if isinstance(tf, str))
            else:
                yield from _written(item)
    elif isinstance(value, list):
        for item in value:
            yield from _written(item)


def _waits(value):
    if isinstance(value, dict):
        return any(value.get(key) is not None for key in WAITS) or any(_waits(item) for item in value.values())
    return isinstance(value, list) and any(_waits(item) for item in value)


def written_frames(meaning):
    return sorted_frames(_written(meaning))


def _oz_frames(meaning):
    """Frames an OZ is watched on: an OZ final, or a step waiting for an OZ."""
    from strategy_recipe.registry import condition_steps, units
    items = [unit['final'] for unit in units(meaning) if unit['final']['kind'] == 'OZ']
    items += [step for step in condition_steps(meaning) if step['kind'] == 'OZ_ALERT']
    return sorted_frames(tf for item in items for tf in item.get('tfs', ()))


def base_frame(meaning):
    """The one frame a recipe's strategy completes on, when every frame it names is on that frame's ladder.

    None when it completes on several frames, names a frame off the ladder, waits a number of
    seconds, or borrows a preset (its frames are then not all written here).
    """
    frames = alert_frames(meaning)
    if len(frames) != 1 or frames[0] not in LADDER or 'preset' in meaning or _waits(meaning):
        return None
    base = frames[0]
    return base if set(written_frames(meaning)) <= set(LADDER[base]) else None


def base_choices(meaning, frames=()):
    """The base frames the recipe can be tested on, its own among them, in ladder order.

    `frames` are other frames that move with it (a virtual entry's fixed frames). A base frame
    is a choice when every level they use exists there (4h has no top frame) and an OZ stays
    on a frame the OZ monitor watches.
    """
    from command_interpreter import OZ_BASE_TFS
    base = base_frame(meaning)
    if base is None:
        return []
    used = set(written_frames(meaning)) | set(sorted_frames(frames))
    if not used <= set(LADDER[base]):
        return []
    levels = {LADDER[base].index(tf) for tf in used}
    watched = {LADDER[base].index(tf) for tf in _oz_frames(meaning)}
    return [target for target, row in LADDER.items()
            if all(row[level] for level in levels) and all(row[level] in OZ_BASE_TFS for level in watched)]


def move_frame(tf, base, target):
    """The frame at tf's level of base's ladder, under target. References stay references."""
    if tf in REFERENCES or base == target:
        return tf
    if target not in LADDER:
        raise ValueError('기준 프레임은 ' + label(next(iter(LADDER))) + '~' + label(list(LADDER)[-1]) + ' 중 하나입니다.')
    if tf not in LADDER.get(base, ()):
        raise ValueError(label(tf) + '은 ' + label(base) + ' 기준 표(기준·중위·상위·최상위)에 없는 시간봉입니다.')
    level = LADDER[base].index(tf)
    moved = LADDER[target][level]
    if moved is None:
        raise ValueError(label(target) + ' 기준에는 ' + LEVELS[level] + ' 시간봉이 없습니다.')
    return moved


def _moved(value, change):
    if isinstance(value, dict):
        return {key: change(item) if key in ('tf', 'price_tf') and isinstance(item, str)
                else [change(tf) if isinstance(tf, str) else tf for tf in item] if key == 'tfs' and isinstance(item, list)
                else _moved(item, change) for key, item in value.items()}
    if isinstance(value, list):
        return [_moved(item, change) for item in value]
    return value


def move(meaning, base, target):
    """A new recipe meaning (or loaded plan) with every frame moved from base's ladder to target's."""
    return _moved(meaning, lambda tf: move_frame(tf, base, target))


def recipe_entry(name):
    """The recipe entry a strategy name runs (수정본163).

    A preset by its ID, or a Part3 generated strategy run under its file name (Test_SPECIAL001):
    the library lists that file by its ID (TEST_SPECIAL001), a name an external loader cannot take.
    """
    from strategy_recipe.registry import REGISTRY, preset_entry
    from strategy_recipe.user_catalog import generated_entries
    try:
        return preset_entry(name)
    except ValueError:
        for entry in generated_entries(REGISTRY.parent.parent).values():
            if entry['filename'] == str(name) + '.py':
                return entry
        raise


def moved_plugins(plugins, bases):
    """The loaded strategies, each one in `bases` replaced by its copy moved to that base frame.

    The copy is the loaded plan with its frames moved, installed by the strategy's own register.
    """
    from strategy_recipe.registry import oz_declarations
    result = dict(plugins)
    for name, target in bases.items():
        meaning = recipe_entry(name)['recipe']['strategy_intent']
        if name not in result or target not in base_choices(meaning):
            raise ValueError(name + ': ' + label(target) + ' 기준 프레임으로 바꿀 수 없습니다.')
        plugin = result[name]
        plan = move(plugin.recipe['strategy_intent'], base_frame(meaning), target)
        result[name] = SimpleNamespace(__name__=name, register=functools.partial(plugin.register, plan=plan),
                                       recipe={**plugin.recipe, 'strategy_intent': plan},
                                       OZ_DECLARATIONS=oz_declarations(plan))
    return result


def edited_plugin(name, plugin, meaning, config, triggers=None, times=None):
    """The loaded strategy running `meaning` (its conditions as edited for a test, already on its base frame).

    The meaning is prepared exactly as Part1's loader prepares the recipe (strategy_recipe.registry.
    load_plugins: symbols, OZ trigger, trading time) and installed by the strategy's own register.
    """
    from copy import deepcopy
    from oz_profiles import parse_profile_text
    from strategy_recipe.contract import execution_plan
    from strategy_recipe.registry import oz_declarations, units
    from symbol_settings import configured_symbols
    entry = recipe_entry(name)
    meaning = deepcopy(meaning)
    if entry.get('symbol_source') == 'CONFIG':
        meaning['symbols'] = list(configured_symbols(config))
    if (triggers or {}).get(name):
        vm, tm = parse_profile_text(triggers[name])
        for unit in units(meaning):
            if unit['final']['kind'] != 'OZ':
                raise ValueError(name + ': OZ 전략에만 트리거를 설정할 수 있습니다.')
            unit['final'].update(validation_mode=vm, trigger_mode=tm)
    override = (times or {}).get(name)
    meaning['time_filters'] = [] if override is not None else deepcopy(entry.get('time_filters', []))
    meaning['final_time_filters'] = deepcopy(entry.get('final_time_filters', 0) if override is None else override)
    plan = execution_plan(meaning)['meaning']
    return SimpleNamespace(__name__=name, register=functools.partial(plugin.register, plan=plan),
                           recipe={**plugin.recipe, 'strategy_intent': plan}, OZ_DECLARATIONS=oz_declarations(plan))


def engine_plugins(config, s):
    """The replay's strategies as Part1 loads them, with the scenario's base frame and strategy edits applied."""
    from event_application import load_strategy_inputs
    from event_selection import resolve
    plan = resolve(s['strategies'], config, part='Part2')
    plugins = load_strategy_inputs(s['triggers'], plan.specials, s.get('special_time_filters', {}), config,
                                   part='Part2')[2]
    if s.get('virtual_strategy') is not None:
        name = s['strategies'][0]
        return {**plugins, name: edited_plugin(name, plugins[name], s['virtual_strategy'], config,
                                               s['triggers'], s.get('special_time_filters', {}))}
    return moved_plugins(plugins, s['base_frames'])
