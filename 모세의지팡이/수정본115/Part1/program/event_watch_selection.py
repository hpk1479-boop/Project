"""Dependency closure from canonical WATCH registrations, before market input.

The command parser remains the only parser. Delayed chain steps declare future
dependencies here as well; a future unknown type retains the broad closure.
LIVE never calls this replay-startup specialization.
"""
from dataclasses import replace
from event_selection import CONSUMER_DEPENDENCIES, PROCESSOR_DEPENDENCIES, command_family

TRIGGER_CAPS={
    'OZ_ALERT':{'OZ'}, 'FVG_NEW':{'FVG'},
    'WONBI_TOUCH':{'WATCH','WONBI'}, 'WONBI_TOUCH_CLOSE':{'WATCH','WONBI'},
    'EMA_CROSS':{'WATCH','MA'},'HMA_CROSS':{'WATCH','MA'},
    'MA_EXPRESSION': {'WATCH', 'MA'},
    'PERCENTILE_OUT':{'WATCH','PERCENTILE'},'PERCENTILE_OUT_IN':{'WATCH','PERCENTILE'},
    'PREV_DAY_TOUCH':{'WATCH'},'BAR_CLOSE':{'WATCH'},
}
CONDITION_CAPS={'TREND':{'INDICATOR'},'TREND_METRIC':{'INDICATOR'},'FVG':{'FVG'},
    'SWEEP':{'SWEEP'},'WONBI':{'WONBI'},'PERCENTILE':{'PERCENTILE'},
    'MA_STATE':{'MA'},'MA_PRICE_STATE':{'MA'},'MA_SLOPE_STATE':{'MA'}}


def specialize(engine, registrations):
    plan=engine.selection
    if plan.names!=('WATCH',):return {'specialized':False}
    caps=set();unknown=[]
    for payload in registrations:
        family=command_family(payload)
        if family:caps.add('INDICATOR' if family=='TREND' else family)
    kernels=engine.strategy_state.get('COMPOSER',{}).get('kernels',{})
    for kernel in kernels.values():
        manager=kernel.manager
        def conditions(spec):
            for c in spec.conditions:
                if c.kind not in CONDITION_CAPS:unknown.append('condition:'+c.kind)
                else:caps.update(CONDITION_CAPS[c.kind])
        for spec in manager._all_specs_locked():
            conditions(spec)
            if spec.final_action=='OZ':caps.add('OZ')
        for chain in manager.timed_chains.values():
            caps.add('CHAINS')
            if chain.final_action=='OZ':caps.add('OZ')
            for trigger in (*chain.triggers,*chain.invalidation_triggers):
                kind=trigger.watch_type
                if kind=='COMPOUND_CONDITION':
                    spec=manager._compound_spec_for_trigger(chain,trigger)
                    if spec is None:unknown.append(kind)
                    else:conditions(spec)
                elif kind in TRIGGER_CAPS:caps.update(TRIGGER_CAPS[kind])
                else:unknown.append(kind)
        if manager.fvg_created_watches:
            caps.add('FVG')
            if any(w.final_action=='OZ' for w in manager.fvg_created_watches.values()):caps.add('OZ')
        for family,items in manager._desired_subscriptions_locked().items():
            if items:caps.add('INDICATOR' if family=='TREND' else family)
    if unknown:return {'specialized':False,'conservative_dependencies':sorted(set(unknown))}
    consumers=set(caps)&set(CONSUMER_DEPENDENCIES);processors=set()
    def require(name):
        if name in processors:return
        processors.add(name)
        for dep in PROCESSOR_DEPENDENCIES[name]:require(dep)
    for name in sorted(consumers):
        for dep in CONSUMER_DEPENDENCIES[name]:require(dep)
    if 'SWEEP_STATE' in processors:consumers.add('SWEEP')
    narrow=replace(plan,consumers=frozenset(consumers),processors=frozenset(processors),capabilities=frozenset(caps))
    engine.processors=tuple(p for p in engine.processors if p.name in processors)
    names={'WATCH':'WATCH_CONDITIONS', 'OZ':'OZ', 'SWEEP':'SWEEP','FVG':'FVG','INDICATOR':'INDICATOR'}
    allowed={'COMPOSER'}|{names[c] for c in consumers}
    engine.strategies=tuple(c for c in engine.strategies if c.name in allowed)
    for kernel in kernels.values():kernel.selection=narrow
    for c in engine.strategies:
        if c.name=='COMPOSER':c.selection=narrow
    engine.selection=narrow
    return {'specialized':True,'capabilities':sorted(caps),'processors':sorted(processors),'consumers':sorted(allowed)}
