"""Explicit opt-in construction; importing this module starts no services.

The caller supplies startup configuration. The event host is the default; it
imports or invokes this factory. No Telegram transport is attached.
"""
import importlib
import importlib.util
import builtins
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


def load_modules():
    return {name:importlib.import_module(name) for name in (
        'monitor_OZ','staff_compat','durable_protocol','strategy_FVG',
        'strategy_SWEEP','strategy_INDICATOR','event_composer_domain','command_interpreter')}


def load_strategy_inputs(trigger_overrides, selected=None):
    """Load canonical profile logic in an isolated startup namespace.

    A polling process's global profile settings are never temporarily changed.
    The SPECIAL source bytes and register functions are loaded unmodified.
    """
    import oz_profiles
    mapping=(oz_profiles.parse_special_trigger_json(os.environ.get('OZ_SPECIAL_TRIGGERS',''))
             if trigger_overrides is None else dict(trigger_overrides))
    base=Path(__file__).resolve().parent
    digest=hashlib.sha256(repr(sorted(mapping.items())).encode()).hexdigest()[:16]
    profile_name='_event_profiles_'+digest
    spec=importlib.util.spec_from_file_location(profile_name,base/'oz_profiles.py')
    profile=importlib.util.module_from_spec(spec);sys.modules[profile_name]=profile
    spec.loader.exec_module(profile)
    errors=profile.set_special_trigger_overrides(mapping)
    if errors:raise ValueError('invalid SPECIAL trigger inputs: '+str(errors))
    def isolated_import(name,globals=None,locals=None,fromlist=(),level=0):
        if name=='oz_profiles' and level==0:return profile
        return builtins.__import__(name,globals,locals,fromlist,level)
    plugins={}
    for i in range(1,8):
        name=f'SPECIAL{i}'
        if selected is not None and name not in selected:continue
        module_name=f'_event_{name}_{digest}'
        spec=importlib.util.spec_from_file_location(module_name,base/'SPECIAL'/(name+'.py'))
        module=importlib.util.module_from_spec(spec)
        module.__dict__['__builtins__']={**vars(builtins),'__import__':isolated_import}
        sys.modules[module_name]=module;spec.loader.exec_module(module);plugins[name]=module
    return profile,mapping,plugins


def create_event_engine(config, *, aliases=None, symbols=None, modules=None,
                        plugins=None, trigger_overrides=None, enabled_specials=None,
                        state_files=None, symbol_state_files=None, sweep_events=(), collect_timings=False, selection=None, backtest=False, oz_evaluation='selected'):
    from event_selection import resolve
    plan=resolve(selection,config)
    modules=dict(modules or load_modules())
    profile,trigger_map,loaded=load_strategy_inputs(trigger_overrides, plan.specials if enabled_specials is None else enabled_specials)
    modules['event_profiles']=profile
    if plugins is None:plugins=loaded
    if enabled_specials is not None:plugins={name:module for name,module in plugins.items() if name in enabled_specials}
    if oz_evaluation not in ('selected','all'):raise ValueError('oz_evaluation: selected / all')
    from event_oz_selection import declare
    oz_plan=declare(plan,plugins) if backtest and oz_evaluation=='selected' else None
    symbols=tuple(symbols or (s.strip() for s in config.get('STAFF_ALLOWED_SYMBOLS',
                           'XAUUSD+,NAS100,BTCUSD').split(',') if s.strip()))
    if aliases is None:
        aliases=modules['command_interpreter'].load_command_language(Path(__file__).resolve().parent/'command_aliases.json')
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
        'OZ_STATE':lambda:OZProcessor(oz,compat,config,symbols=symbols,selection=oz_plan)}
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
