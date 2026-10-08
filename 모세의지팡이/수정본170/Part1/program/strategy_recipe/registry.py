"""Data-only presets. Loading and lowering happen at startup, never per tick.

The registry path is relative to the project; preset identifiers carry no
strategy behaviour. A preset uses exactly the same contract as generated code.
"""
from copy import deepcopy
from functools import lru_cache
import json
import re
from pathlib import Path
from types import SimpleNamespace

REGISTRY = Path(__file__).resolve().parents[3] / 'settings' / 'strategy_registry.json'


@lru_cache(maxsize=4)
def _read(path, signature):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('중복된 registry 필드: ' + key)
            result[key] = value
        return result
    data = json.loads(Path(path).read_text('utf-8-sig'), object_pairs_hook=unique)
    if data.get('schema_version') != 2 or not isinstance(data.get('presets'), list):
        raise ValueError('Strategy registry는 Recipe v2 preset 목록이어야 합니다.')
    result = {}
    for entry in data['presets']:
        name = entry.get('id')
        if (not isinstance(name, str) or not re.fullmatch(r'[A-Z][A-Z0-9_]*', name)
            or name in result or name in {'ALL','STAFF','ENGINE','OZ','FVG','SWEEP','INDICATOR','WATCH','KIM'}):
            raise ValueError('비어 있거나 중복된 preset ID입니다: ' + str(name))
        recipe = entry.get('recipe', {})
        if recipe.get('schema_version') != 2 or recipe.get('base') != 'AI' or not isinstance(recipe.get('strategy_intent'), dict):
            raise ValueError(name + ': Recipe v2 strategy_intent가 필요합니다.')
        result[name] = entry
    return result


def _cached_builtin_entries():
    """The cached entries themselves (SPECIAL files, else the registry): never handed out uncopied."""
    from .special_files import _folder_entries
    found = _folder_entries(REGISTRY.parent.parent)
    if found is not None:
        return found[0]
    stat = REGISTRY.stat()
    return _read(str(REGISTRY), (stat.st_mtime_ns, stat.st_size))


def builtin_entries():
    # One copy (수정본170: it was copied twice).
    return deepcopy(_cached_builtin_entries())


def skipped_builtins():
    """SPECIAL files left out of builtin_entries, each with a one-line notice."""
    from .special_files import skipped_special_files
    return skipped_special_files(REGISTRY.parent.parent)


def builtin_sources():
    """{ID: folder of its SPECIAL file, relative to the project}; empty when the old registry is read."""
    from .special_files import special_sources
    return special_sources(REGISTRY.parent.parent) or {}


def user_strategies():
    """IDs read from the installed 스페셜/내 전략 folder: made or received after installation.

    Until the saved live settings name one, its live alerts wait to be turned on (수정본167);
    the shipped strategies keep starting on.
    """
    from .special_files import USER_FOLDER
    return {name for name, folder in builtin_sources().items() if folder == USER_FOLDER}


def entries(part=None):
    from .user_catalog import generated_entries, hidden, registrations
    root = REGISTRY.parent.parent
    builtin = builtin_entries()
    invisible = hidden(root, part)
    result = {name: row for name, row in builtin.items() if name not in invisible}
    generated = generated_entries(root)
    if set(builtin) & set(generated):
        raise ValueError('추가 전략 ID가 기본 전략과 중복됩니다.')
    promoted = registrations(root)
    return {**result, **{name: row for name, row in generated.items()
                        if promoted.get(name) and (part is None or part in promoted[name])}}


def list_presets(part=None):
    return tuple(entries(part))


def preset_entry(name):
    from .user_catalog import generated_entries
    definitions = _cached_builtin_entries()
    generated = generated_entries(REGISTRY.parent.parent)
    if set(definitions) & set(generated):
        raise ValueError('테스트 전략 ID가 기본 스페셜과 중복됩니다.')
    if name in generated:
        return generated[name]
    if name in definitions:
        # A copy of the one entry asked for, not of every entry (수정본170).
        return deepcopy(definitions[name])
    raise ValueError('등록되지 않은 preset: ' + str(name))


def preset_meaning(name, symbols=None):
    entry=preset_entry(name)
    item = deepcopy(entry['recipe']['strategy_intent'])
    item['time_filters']=deepcopy(entry.get('time_filters', []))
    item['final_time_filters']=deepcopy(entry.get('final_time_filters', 0))
    if symbols is not None:
        item['symbols'] = list(symbols)
    return item


def units(meaning):
    if meaning.get('branches'):
        for branch in meaning['branches']:
            yield {**{k:v for k,v in meaning.items() if k != 'branches'}, **branch}
    else:
        yield meaning


def condition_steps(meaning):
    for unit in units(meaning):
        for key in ('steps', 'after_conditions', 'final_conditions', 'cancel_conditions'):
            yield from unit.get(key, ())
        yield from unit.get('lifecycle', {}).get('restart_on', ())


def dependencies(meaning):
    deps = set()
    for unit in units(meaning):
        if unit['final']['kind'] == 'OZ': deps.add('OZ')
        if unit.get('lifecycle', {}).get('invalidate_refs') or unit['final'].get('scope_ref'):
            if any(s['kind'] == 'OZ_ALERT' and s.get('capture') for s in condition_steps(unit)):
                deps.add('OZ_RESOURCES')
        if any(value.get('field') == 'ATR14' for value in unit.get('lifecycle', {}).get('snapshots', {}).values()):
            deps.add('ATR')
    for step in condition_steps(meaning):
        kind = step['kind']
        if step.get('scope_ref'): deps.add('OZ_RESOURCES')
        if kind == 'OZ_ALERT': deps.add('OZ')
        elif kind.startswith('FVG'): deps.add('FVG')
        elif kind in ('EXTERNAL_LIQUIDITY_TOUCH', 'LIQUIDITY_LEVEL'): deps.add('SWEEP')
        elif kind.startswith('MA_') or kind == 'TREND': deps.add('MA')
        elif kind == 'WONBI_TOUCH': deps.add('WONBI')
        elif kind.startswith('PERCENTILE_'): deps.add('PERCENTILE')
        elif kind == 'TREND_METRIC': deps.add('INDICATOR')
        elif kind == 'PRICE_LEVEL' and isinstance(step.get('level'), str): deps.add('SWEEP')
    return tuple(sorted(deps))


def oz_declarations(meaning):
    """Static dependency upper bound; SOURCE/FINAL resolve from declared Tfs."""
    triples = set()
    for unit in units(meaning):
        source_tfs = [tf for s in unit['steps'] for tf in s.get('tfs', ()) if tf not in ('SOURCE','FINAL')]
        final_tfs = unit['final'].get('tfs', ())
        candidates = [unit['final']] if unit['final']['kind'] == 'OZ' else []
        candidates += [s for s in condition_steps(unit) if s['kind'] == 'OZ_ALERT']
        for item in candidates:
            for tf in item.get('tfs', ()):
                expanded = source_tfs if tf == 'SOURCE' else final_tfs if tf == 'FINAL' else (tf,)
                for selected in expanded:
                    if selected in ('SOURCE','FINAL'):continue
                    triples.add((selected, item['validation_mode'], item['trigger_mode']))
    return frozenset(triples)


def default_settings(name):
    from oz_profiles import profile_label
    entry = preset_entry(name)
    final = next(units(entry['recipe']['strategy_intent']))['final']
    if final['kind'] != 'OZ':
        return (None, deepcopy(entry.get('final_time_filters', 0)))
    return (profile_label(final.get('validation_mode', 'NORMAL'), final.get('trigger_mode', 'OZ')),
            deepcopy(entry.get('final_time_filters', 0)))


def load_plugins(config, selected=None, triggers=None, times=None, part=None):
    from .contract import execution_plan
    from symbol_settings import configured_symbols
    from oz_profiles import parse_profile_text
    symbols = configured_symbols(config)
    output = {}
    for name, entry in entries(part).items():
        if selected is not None and name not in selected: continue
        recipe = deepcopy(entry['recipe'])
        meaning = recipe['strategy_intent']
        if entry.get('symbol_source') == 'CONFIG':
            meaning['symbols'] = list(symbols)
        if not meaning['symbols']:
            # An empty configured list is not permission to invent symbols.
            continue
        if (triggers or {}).get(name):
            vm, tm = parse_profile_text(triggers[name])
            for unit in units(meaning):
                if unit['final']['kind'] != 'OZ':
                    raise ValueError(name + ': OZ 전략에만 트리거를 설정할 수 있습니다.')
                unit['final'].update(validation_mode=vm, trigger_mode=tm)
        # A user-set trading time replaces the recipe's own; trading time applies only at the final alert
        # (special_time_slot.strategy_trading_time).
        override = (times or {}).get(name)
        meaning['time_filters'] = [] if override is not None else deepcopy(entry.get('time_filters', []))
        meaning['final_time_filters'] = deepcopy(entry.get('final_time_filters', 0) if override is None else override)
        plan = execution_plan(meaning)['meaning']
        recipe['strategy_intent'] = plan
        def register(manager, plan=plan, title=entry['name'], namespace=name):
            from .port import IntentPort
            port = IntentPort(manager, plan, title, namespace)
            port.install()
        output[name] = SimpleNamespace(__name__=name, register=register, recipe=recipe,
                                       OZ_DECLARATIONS=oz_declarations(plan))
    return output
