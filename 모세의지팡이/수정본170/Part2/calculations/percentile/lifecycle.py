from dataclasses import dataclass
from .contracts import KernelWriteSet, PercentileError

@dataclass(frozen=True)
class SourceCallPlan:
    family:str
    ready:bool
    limit:int
    raw_limit:int|None=None
    ehlers_limit:int|None=None
    hma_limit:int|None=None

def source_call_plan(family,R,P,params):
    if family=='PRICE':
        b=params['InpDomCycle'];limit=R-P
        if limit<=0:limit=1
        if P==0:limit=R-(b+11)
        if limit<0:limit=0
        e=limit+b+2
        if e>=R-5:e=R-6
        return SourceCallPlan(family,R>=b+12,limit,ehlers_limit=e,hma_limit=min(limit+8,R-8))
    b=params['BandPeriod'];lag=int((params['Vibration']-1)/2);length=params[family+'Length']
    limit=R-b-lag-(2 if family=='RSI' else length) if P==0 else R-P+1
    return SourceCallPlan(family,R>b+lag+length,limit,raw_limit=limit+lag+1)


