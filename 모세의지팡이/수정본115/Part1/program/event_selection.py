"""Declarative dependency closure shared by the LIVE and replay hosts.

None/ALL is the unrestricted LIVE application. Selection changes construction
and dispatch only; canonical strategy decision functions remain untouched.
"""
from dataclasses import dataclass
import json

from strategy_recipe.registry import entries, dependencies

# External, process-local generated plugins declare their requirements here.
# Preset requirements are derived from the data registry, never their IDs.
SPECIAL_DEPENDENCIES={}

def strategy_dependencies(part=None):
    return {**{name:dependencies(row['recipe']['strategy_intent']) for name,row in entries(part).items()},
            **SPECIAL_DEPENDENCIES}
# OZ can dynamically register external-liquidity watches; those share SWEEP.
CONSUMER_DEPENDENCIES={'OZ':('OZ_STATE',),'FVG':('FVG_STATE',),
    'SWEEP':('SWEEP_STATE',),'INDICATOR':(),'WATCH':('WATCH_CONDITIONS',)}
PROCESSOR_DEPENDENCIES={'OZ_STATE':('SWEEP_STATE',),'SWEEP_STATE':(),'FVG_STATE':(),
    'WATCH_CONDITIONS':()}
COMMAND_FAMILIES={'OZ':'OZ','INDICATOR':'TREND','WATCH':'WATCH','FVG':'FVG','SWEEP':'SWEEP'}

def config_definitions(config):
    result={}
    for key,prefix in (('OFFICIAL_SPECS','OFFICIAL:'),('OFFICIAL_CHAIN_SPECS','CHAIN:')):
        raw=config.get(key,())
        values=json.loads(raw) if isinstance(raw,str) and raw.strip() else raw or ()
        if isinstance(values,dict):values=list(values.values())
        for value in values:
            name=prefix+str(value['spec_id'])
            if name in result:raise ValueError('duplicate configured strategy: '+name)
            result[name]=dict(value)
    return result

def available(config,part=None):return tuple(strategy_dependencies(part))+tuple(COMMAND_FAMILIES)+tuple(config_definitions(config))

def definition_dependencies(name,value):
    deps={'OZ'}
    if name.startswith('CHAIN:'):return deps|{'WATCH','FVG','CHAINS'}
    for condition in value.get('conditions',()):
        kind=condition.split('@')[0] if isinstance(condition,str) else condition['kind']
        family={'TREND':'INDICATOR','TREND_METRIC':'INDICATOR','FVG':'FVG','SWEEP':'SWEEP',
                'WONBI':'WONBI','PERCENTILE':'PERCENTILE','MA_STATE':'MA','MA_PRICE_STATE':'MA','MA_SLOPE_STATE':'MA'}.get(kind)
        if family is None:raise ValueError('undeclared configured condition dependency: '+kind)
        deps.add(family)
    return deps

@dataclass(frozen=True)
class Selection:
    names:tuple
    unrestricted:bool
    specials:tuple
    consumers:frozenset
    processors:frozenset
    capabilities:frozenset
    official:tuple
    @property
    def families(self):
        return frozenset(COMMAND_FAMILIES[x] for x in self.consumers if x in COMMAND_FAMILIES)
    def permits_command(self,declared):return self.unrestricted or declared in self.names

def resolve(names,config,part=None):
    definitions=config_definitions(config)
    presets=strategy_dependencies(part)
    unrestricted=names is None or list(names)==['ALL']
    names=tuple(available(config,part) if unrestricted else dict.fromkeys(names))
    if not names:raise ValueError('전략을 하나 이상 선택하세요. 전체 감시는 ALL을 명시하세요.')
    unknown=set(names)-set(available(config,part))
    if unknown:raise ValueError('unknown selected strategies: '+','.join(sorted(unknown)))
    caps=set()
    for name in names:
        if name in presets:caps.update(presets[name])
        elif name in definitions:caps.update(definition_dependencies(name,definitions[name]))
        else:
            caps.add(name)
            # A command Watch can contain timed/compound chains which arm any
            # of these families after an earlier condition succeeds.
            if name=='WATCH':caps.update(('OZ','INDICATOR','FVG','SWEEP','CHAINS','MA','WONBI','PERCENTILE'))
    if unrestricted:caps.update(('WONBI','PERCENTILE','MA','CHAINS'))
    consumers=set(caps)&set(CONSUMER_DEPENDENCIES);processors=set()
    def require(name):
        if name in processors:return
        processors.add(name)
        for dep in PROCESSOR_DEPENDENCIES[name]:require(dep)
    for name in sorted(consumers):
        for dep in CONSUMER_DEPENDENCIES[name]:require(dep)
    # External-liquidity replies must also flow back through the SWEEP Consumer.
    if 'SWEEP_STATE' in processors:consumers.add('SWEEP')
    return Selection(names,unrestricted,tuple(n for n in names if n in presets),
        frozenset(consumers),frozenset(processors),frozenset(caps),
        tuple((name,definitions[name]) for name in names if name in definitions))

def command_family(payload):
    action=str(payload.get('action',''))
    if action in ('MANUAL_WATCH','CANCEL_MANUAL'):return 'OZ'
    if action in ('GENERIC_WATCH','CANCEL_GENERIC'):return 'WATCH'
    for prefix,family in (('TREND','TREND'),('FVG','FVG'),('SWEEP','SWEEP')):
        if prefix in action:return family
    return payload.get('family') if action=='SYNC_SUBSCRIPTIONS' else None
