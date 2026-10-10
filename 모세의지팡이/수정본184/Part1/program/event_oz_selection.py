"""Static OZ upper bound declared by common Recipe plans and external plugins."""
from dataclasses import dataclass
from functools import cached_property
from oz_profiles import PROFILE_KEYS,normalize_profile
from oz_engine.common import TF_MAP

# Attribute names, not duplicated strategy timeframes/settings. The isolated
# plugin namespace has already applied the configured final-trigger overrides.

@dataclass(frozen=True)
class OZSelection:
    triples:frozenset
    @cached_property
    def profiles(self):return tuple(p for p in PROFILE_KEYS if any((tf,*p) in self.triples for tf in TF_MAP))
    def timeframes(self,vm,tm):return tuple(tf for tf in TF_MAP if (tf,vm,tm) in self.triples)
    @cached_property
    def feeds(self):
        result=set()
        for tf,vm,tm in self.triples:
            result.add(tf)
            if vm=='NORMAL':result.update(TF_MAP[tf])
        return frozenset(result)
    def require(self,tf,vm,tm):
        if (tf,vm,tm) not in self.triples:
            raise ValueError(f'undeclared OZ request: {tf}/{vm}/{tm}')

def declare(plan,plugins):
    # Commands can register arbitrary watches later. That explicit strategy
    # family therefore declares the complete upper bound.
    if plan.unrestricted or any(n in ('OZ','WATCH') for n in plan.names):return None
    triples=set()
    def add(tfs,vm,tm):
        vm,tm=normalize_profile(vm,tm)
        for tf in tfs:
            if tf not in TF_MAP:raise ValueError('undeclared OZ timeframe: '+str(tf))
            triples.add((tf,vm,tm))
    for name in plan.specials:
        plugin=plugins.get(name)
        if plugin is None: continue
        declarations=getattr(plugin,'OZ_DECLARATIONS',None)
        if declarations is None and getattr(plugin,'recipe',None):
            from strategy_recipe.registry import oz_declarations
            declarations=oz_declarations(plugin.recipe['strategy_intent'])
        if declarations is None:
            # A generated external module must declare its bounded plan.
            raise ValueError('strategy OZ declarations missing: '+name)
        for tf,vm,tm in declarations: add((tf,),vm,tm)
    for name,value in plan.official:
        vm,tm=normalize_profile(value.get('validation_mode'),value.get('trigger_mode'),value.get('oz_mode'))
        add(value.get('oz_tfs',()),vm,tm)
    return OZSelection(frozenset(triples))
