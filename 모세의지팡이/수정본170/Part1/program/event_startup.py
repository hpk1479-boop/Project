"""Host-only startup I/O. No strategy calls this module during dispatch.

Read the startup inputs and the event-owned state once, preserving original JSON
text for the canonical restore functions. Values and secrets are not logged.
"""
from dataclasses import dataclass
from pathlib import Path
import hashlib
import json
import os
import tempfile


@dataclass
class StartupInputs:
    config:dict
    aliases:dict
    triggers:dict
    enabled_specials:tuple
    state_files:dict
    evidence:list


def load_startup(program,modules,*,state_directory=None):
    program=Path(program).resolve();evidence=[]
    def note(path,role):
        exists=path.is_file()
        evidence.append({'path':str(path),'role':role,'exists':exists,
                         'sha256':hashlib.sha256(path.read_bytes()).hexdigest() if exists else None})
    config_path=program/'config.txt';note(config_path,'common config')
    config=modules['event_composer_domain'].load_config(str(config_path))
    settings_path=program.parent/'special_settings.json';note(settings_path,'SYSTEM CONTROL SPECIAL selection and triggers')
    # Exact SYSTEM CONTROL fallback: missing/malformed -> all/code defaults.
    try:
        raw=json.loads(settings_path.read_text('utf-8'))
        items=raw.get('specials') if isinstance(raw,dict) else None
        if not isinstance(items,dict):items={}
    except (OSError,ValueError):items={}
    settings={str(name).upper():{'enabled':bool(item.get('enabled',True)),
                'trigger':str(item['trigger']).strip() if isinstance(item.get('trigger'),str) and item['trigger'].strip() else None}
              for name,item in items.items() if isinstance(item,dict)}
    from strategy_recipe.registry import list_presets
    names=list_presets('Part1')
    from strategy_recipe.registry import builtin_entries,user_strategies
    builtins=set(builtin_entries())
    # The installation's own strategies (스페셜/내 전략) wait until the settings turn them on (수정본167).
    own=user_strategies()
    enabled=tuple(name for name in names if settings.get(name,{}).get('enabled',name in builtins and name not in own))
    triggers={name:item['trigger'] for name,item in settings.items() if name in names and item.get('trigger')}
    aliases_path=modules['command_interpreter'].resolve_command_language_path(config,program)
    note(aliases_path,'command language')
    aliases=modules['command_interpreter'].load_command_language(aliases_path)
    state={}
    if state_directory is not None:
        event_path=Path(state_directory).resolve()
        for path in sorted(event_path.glob('*.json')):
            note(path,'event-owned restore input')
            state[path.name]=path.read_text('utf-8')
    return StartupInputs(config,aliases,triggers,enabled,state,evidence)


def create_live_event_engine(program=None,*,collect_timings=False,state_directory=None):
    """Default LIVE entry; config may be read elsewhere, writes stay here."""
    from event_application import load_modules,create_event_engine
    modules=load_modules()
    event_path=Path(state_directory or Path(__file__).resolve().parent.parent/'event_state').resolve()
    source_program=Path(program or Path(__file__).parent).resolve()
    if event_path==source_program or source_program in event_path.parents or event_path in source_program.parents:
        raise ValueError('event state must be separate from input program')
    startup=load_startup(source_program,modules,state_directory=event_path)
    try:by_symbol=json.loads(startup.state_files.get('event_composer_memory.json','{}'))
    except ValueError:by_symbol={}
    engine=create_event_engine(startup.config,modules=modules,aliases=startup.aliases,
        trigger_overrides=startup.triggers,enabled_specials=startup.enabled_specials,
        state_files=startup.state_files,symbol_state_files=by_symbol,
        collect_timings=collect_timings)
    engine.startup_evidence=tuple(startup.evidence)
    engine.event_state_directory=str(event_path)
    engine.startup_config=dict(startup.config);engine.startup_aliases=startup.aliases
    engine.startup_enabled_specials=startup.enabled_specials
    return engine


def export_engine_state(engine):
    """Explicit host save at an event boundary, in the established JSON shapes.

    No queue, input journal or pending output is persisted. Symbol-specific
    Composer stores remain separate; otherwise one symbol could erase another.
    """
    from durable_protocol import identity
    from event_engine.domain_support import plain
    if engine._running or engine._internal or len(engine.ingress):
        raise RuntimeError('save requires an event boundary with drained ingress and internal events')
    files={}
    for state in (*engine.processor_state.values(),*engine.strategy_state.values()):
        files.update(state.get('startup_files',{}));files.update(state.get('memory',{}))
    kernels=engine.strategy_state.get('COMPOSER',{}).get('kernels',{})
    stores={symbol:kernel.export_memory() for symbol,kernel in kernels.items()}
    # Logical confirmation sequence and semantic output receipts survive restart.
    for symbol,kernel in kernels.items():
        stores[symbol]['event_notification_port.json']=json.dumps({'sequence':kernel.notifier.sequence,
            'deliveries':[[list(key),value] for key,value in kernel.notifier.deliveries.items()],
            'delivery_times':[[list(key),value] for key,value in kernel.notifier.delivery_times.items()],
            'clock':kernel.notifier.clock})
    if stores:files['event_composer_memory.json']=json.dumps(stores,ensure_ascii=False)
    oz_runtime=engine.processor_state.get('OZ_STATE',{}).get('runtime')
    if oz_runtime is not None:
        # OZ inherited read-only startup inputs from every domain. Export only
        # its own files so it cannot replace another consumer's latest state.
        files.update({name:value for name,value in oz_runtime.export_files().items() if name.startswith('oz_')})
    def save(name,value):files[name]=json.dumps(value,ensure_ascii=False,default=str)
    watch_runtime=engine.strategy_state.get('WATCH_CONDITIONS',{}).get('runtime')
    if watch_runtime is not None:files.update(watch_runtime.export_files())
    trend=engine.strategy_state.get('INDICATOR',{})
    if 'watches' in trend:
        save('trend_watch_state.json',{'version':3,'watches':{k:[v['symbol'],v['source_tf'],v.get('requested_fields',[])] for k,v in trend['watches'].items()}})
    fvg=engine.processor_state.get('FVG_STATE',{})
    if 'watches' in fvg:save('fvg_watch_state.json',{'version':2,'watches':plain(fvg['watches'])})
    sweep=engine.processor_state.get('SWEEP_STATE',{})
    if 'watches' in sweep:save('sweep_watch_state.json',{'version':2,'watches':plain(sweep['watches'])})
    for family,state in [('trend',trend),('fvg',fvg),('sweep',sweep)]:
        if 'stream' in state:save(family+'_stream.json',{'version':1,'records':{'generation':state['stream']['generation']}})
    if 'pending' in trend:save('trend_pending_events.json',{'version':1,'records':plain(trend['pending'])})
    # SWEEP detector checkpoint uses its existing serializer, through memory I/O.
    if sweep.get('core'):
        import strategy_SWEEP
        from domain_memory import memory_scope
        with memory_scope(files):
            core=strategy_SWEEP.SweepEngine(staff_client=False,manager_client=False,event_publisher=False,event_state=sweep['core'])
            core._state_dirty=True;core._save_detector_state()
    return files


def write_event_state_files(files,*,output_directory,polling_program):
    """Host export of an engine memory store to an event-only directory.

    This is explicit state output, not a journal, service loop or outbox. The
    caller chooses the engine-owned memory store; dispatch never calls this.
    """
    target=Path(output_directory).resolve();program=Path(polling_program).resolve()
    if target==program or program in target.parents or target in program.parents:
        raise ValueError('event state output must be separate from the polling program tree')
    names=[]
    for name,value in files.items():
        if Path(name).name!=name or not name.endswith('.json'):
            raise ValueError('event state filename must be a JSON basename')
        json.loads(value)  # Validate all payloads before starting any writes.
        destination=(target/name).resolve()
        if destination.parent!=target:raise ValueError('event state output path escape')
        names.append((destination,value))
    target.mkdir(parents=True,exist_ok=True)
    for path,value in names:
        # A fresh exclusive file avoids following pre-existing temporary links.
        with tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',dir=target,
                                         prefix='.event-',suffix='.tmp',delete=False) as handle:
            temporary=Path(handle.name);handle.write(value)
        try:os.replace(temporary,path)
        finally:
            if temporary.exists():temporary.unlink()
    return tuple(str(path) for path,_ in names)
