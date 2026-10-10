"""Static upper bound of OZ profiles, including SPECIAL's deferred children."""
from dataclasses import dataclass
from functools import cached_property
from oz_profiles import PROFILE_KEYS,normalize_profile,has_flag
from oz_engine.common import TF_MAP

# Attribute names, not duplicated strategy timeframes/settings. The isolated
# plugin namespace has already applied the configured final-trigger overrides.
SPECIAL_OZ_DECLARATIONS={
    'SPECIAL1':('OZ_TO_1H',), 'SPECIAL2':('OZ_TO_1H',),
    'SPECIAL3':('FINAL_OZ_TFS',), 'SPECIAL4':('OZ_TFS',),
    'SPECIAL5':('FINAL_TFS','SOURCE_TFS'),
    'SPECIAL6':('FINAL_OZ_TFS',), 'SPECIAL7':('FINAL_OZ_TFS',),
}

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
            elif has_flag(tm,'REGIME'):result.add(TF_MAP[tf][1])
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
        plugin=plugins[name];attrs=SPECIAL_OZ_DECLARATIONS[name]
        add(getattr(plugin,attrs[0]),plugin.FINAL_VALIDATION_MODE,plugin.FINAL_TRIGGER_MODE)
        if name=='SPECIAL5':
            for trigger in (plugin.SOURCE_TRIGGER_MODE,plugin.BREAKER_REGIME_SOURCE_TRIGGER_MODE):
                add(plugin.SOURCE_TFS,plugin.SOURCE_VALIDATION_MODE,trigger)
    for name,value in plan.official:
        vm,tm=normalize_profile(value.get('validation_mode'),value.get('trigger_mode'),value.get('oz_mode'))
        add(value.get('oz_tfs',()),vm,tm)
    return OZSelection(frozenset(triples))
