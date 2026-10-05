"""Explicit opt-in construction; importing this module starts no services.

The caller supplies startup configuration. The event host is the default; it
imports or invokes this factory. No Telegram transport is attached.
"""
import importlib
import importlib.util
import hashlib
import os
import sys
from pathlib import Path
from event_engine import EventEngine, IngressSequencer
from event_engine.fvg_state import FVGProcessor,FVGConsumer
from event_engine.sweep_state import SweepProcessor,SweepConsumer
from event_engine.oz_processor import OZProcessor,OZConsumer
from event_engine.indicator_consumer import IndicatorConsumer
from event_engine.watch_consumer import WatchConditionConsumer
from event_engine.composition_consumer import CompositionConsumer
from event_composition import CompositionKernel


_strategy_loaders = {}


def register_strategy_loader(name, loader, *, dependencies):
    """Register a process-local external plugin loader without replacing host methods.

    A loader is called only when its declared strategy is selected.
    """
    key = str(name or '').strip()
    from strategy_recipe.registry import list_presets
    if not key or key in list_presets() or not callable(loader):
        raise ValueError('invalid external strategy loader')
    declared = tuple(dependencies)
    if not declared or any(not isinstance(item, str) or not item for item in declared):
        raise ValueError('strategy dependencies must be declared')
    from event_selection import SPECIAL_DEPENDENCIES
    _strategy_loaders[key] = loader
    SPECIAL_DEPENDENCIES[key] = declared


def unregister_strategy_loader(name):
    """Remove only a registered external provider and its dependency declaration."""
    key = str(name or '').strip()
    if key not in _strategy_loaders:
        return False
    _strategy_loaders.pop(key)
    from event_selection import SPECIAL_DEPENDENCIES
    SPECIAL_DEPENDENCIES.pop(key, None)
    return True


def load_modules():
    return {name:importlib.import_module(name) for name in (
        'monitor_OZ','staff_compat','durable_protocol','strategy_FVG',
        'strategy_SWEEP','strategy_INDICATOR','event_composer_domain','command_interpreter')}


def load_strategy_inputs(trigger_overrides, selected=None, time_overrides=None, config=None, part=None):
    """Load canonical profile logic in an isolated startup namespace.

    A polling process's global profile settings are never temporarily changed.
    Recipe presets and external generated plugins share the same public port.
    """
    import oz_profiles
    mapping=(oz_profiles.parse_special_trigger_json(os.environ.get('OZ_SPECIAL_TRIGGERS',''))
             if trigger_overrides is None else dict(trigger_overrides))
    base=Path(__file__).resolve().parent
    import json
    import special_time_slot
    time_mapping=(special_time_slot.parse_special_time_json(os.environ.get('OZ_SPECIAL_TIME_FILTERS',''))
                  if time_overrides is None else dict(time_overrides))
    digest=hashlib.sha256(json.dumps([mapping,time_mapping],sort_keys=True).encode()).hexdigest()[:16]
    profile_name='_event_profiles_'+digest
    spec=importlib.util.spec_from_file_location(profile_name,base/'oz_profiles.py')
    profile=importlib.util.module_from_spec(spec);sys.modules[profile_name]=profile
    spec.loader.exec_module(profile)
    errors=profile.set_special_trigger_overrides(mapping)
    if errors:raise ValueError('invalid SPECIAL trigger inputs: '+str(errors))
    slot_spec=importlib.util.spec_from_file_location('_event_time_'+digest,base/'special_time_slot.py')
    slot=importlib.util.module_from_spec(slot_spec);slot_spec.loader.exec_module(slot)
    errors=slot.set_special_time_overrides(time_mapping)
    if errors:raise ValueError('invalid SPECIAL time inputs: '+str(errors))
    from strategy_recipe.registry import load_plugins
    plugins=load_plugins(config or {}, selected, mapping, time_mapping, part=part)
    for name, loader in tuple(_strategy_loaders.items()):
        if selected is not None and name not in selected:
            continue
        module = loader()
        if not callable(getattr(module, 'register', None)):
            raise ValueError('external strategy requires register(manager): ' + name)
        plugins[name] = module
    return profile,mapping,plugins


def create_event_engine(config, *, aliases=None, symbols=None, modules=None,
                        plugins=None, trigger_overrides=None, enabled_specials=None, time_overrides=None,
                        state_files=None, symbol_state_files=None, sweep_events=(), collect_timings=False, selection=None, backtest=False, oz_evaluation='selected'):
    from event_selection import resolve
    part='Part2' if backtest else 'Part1'
    plan=resolve(selection,config,part=part)
    modules=dict(modules or load_modules())
    profile,trigger_map,loaded=load_strategy_inputs(trigger_overrides, plan.specials if enabled_specials is None else enabled_specials,time_overrides,config,part=part)
    modules['event_profiles']=profile
    if plugins is None:plugins=loaded
    if enabled_specials is not None:plugins={name:module for name,module in plugins.items() if name in enabled_specials}
    if oz_evaluation not in ('selected','all'):raise ValueError('oz_evaluation: selected / all')
    from event_oz_selection import declare
    oz_plan=declare(plan,plugins) if backtest and oz_evaluation=='selected' else None
    resource_keys=set()
    if 'OZ_RESOURCES' in plan.capabilities:
        from strategy_recipe.registry import condition_steps
        for plugin in plugins.values():
            recipe=getattr(plugin,'recipe',None) or getattr(plugin,'PART3_RECIPE',None)
            if not recipe:continue
            for step in condition_steps(recipe['strategy_intent']):
                if step['kind']=='OZ_ALERT' and step.get('capture'):
                    resource_keys.update((tf,step['validation_mode'],step['trigger_mode']) for tf in step['tfs'])
    # Empty = every symbol the EA sends (Subscriptions wildcard). Replays pass their symbol.
    symbols=tuple(symbols or ())
    if aliases is None:
        interpreter=modules['command_interpreter']
        aliases=interpreter.load_command_language(interpreter.resolve_command_language_path(config))
    sigma=float(config.get('WONBI_SIGMA',3))
    oz=modules['monitor_OZ'];compat=modules['staff_compat'];protocol=modules['durable_protocol']
    def factory(owner,symbol,timestamp):
        return CompositionKernel(modules,tuple(plugins.values()),
                                 config,aliases,owner,symbol,timestamp,trigger_map=trigger_map,
                                 state_files=(symbol_state_files or {}).get(symbol,state_files),selection=plan,backtest=backtest)
    def route(raw):
        return 'COMPOSER'
    processor_builders={
        'SWEEP_STATE':lambda:SweepProcessor(modules['strategy_SWEEP'],protocol,compat,oz,symbols=symbols,sigma=sigma),
        'FVG_STATE':lambda:FVGProcessor(modules['strategy_FVG'],protocol,symbols=symbols),
        'OZ_STATE':lambda:OZProcessor(oz,compat,config,symbols=symbols,selection=oz_plan,resource_keys=resource_keys)}
    consumer_builders={
        'INDICATOR':lambda:IndicatorConsumer(modules['strategy_INDICATOR'],protocol,compat,oz,symbols=symbols,sigma=sigma),
        'FVG':FVGConsumer,'SWEEP':SweepConsumer,'OZ':OZConsumer,
        'WATCH':lambda:WatchConditionConsumer(oz,compat,config,symbols=symbols)}
    processors=[make() for name,make in processor_builders.items() if name in plan.processors]
    consumers=[make() for name,make in consumer_builders.items() if name in plan.consumers]
    # Preserve the canonical shared arbitration of mixed SPECIAL/manual alert
    # groups. Splitting KIM is E3; E2 changes the input and state ownership only.
    consumers.append(CompositionConsumer('COMPOSER',factory,route,symbols=symbols,selection=plan))
    from indicator_facts import wonbi_bands
    wonbi_bands(0.,0.,0.,sigma)  # Validate the immutable startup parameter before any input.
    engine=EventEngine(IngressSequencer(),consumers,processors,collect_timings=collect_timings,
                       fact_parameters={'WONBI_SIGMA':sigma})
    # Replay callers pass no historical state. The opt-in LIVE startup loader
    # supplies the exact existing file text for canonical restore functions.
    if state_files:
        for name in ('OZ_STATE','SWEEP_STATE','FVG_STATE'):
            if name in engine.processor_state:engine.processor_state[name]['startup_files']=dict(state_files)
        for name in ('WATCH_CONDITIONS','INDICATOR'):
            if name in engine.strategy_state:engine.strategy_state[name]['startup_files']=dict(state_files)
    if sweep_events and 'OZ_STATE' in engine.processor_state:
        engine.processor_state['OZ_STATE']['startup_sweep_events']=tuple(sweep_events)
    engine.selection=plan
    engine.oz_selection=oz_plan
    return engine
