from dataclasses import dataclass
from .contracts import CalcEvent, KernelWriteSet, PercentileError

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

class SourceCallScheduler:
    def __init__(self):self.previous_return=0;self.sequence=0;self.history_epoch=None
    def event(self,R,history_epoch='0',source_ordinal=0,market_asof_token=None,point=0.01,reset_reason=None):
        if self.history_epoch is not None and history_epoch!=self.history_epoch:self.previous_return=0
        if reset_reason:self.previous_return=0
        self.sequence+=1;self.history_epoch=history_epoch
        return CalcEvent(str(self.sequence),R,self.previous_return,history_epoch,source_ordinal,market_asof_token,point,reset_reason)
    def commit(self,result):self.previous_return=result.returned

class SourceReferenceStepper:
    def __init__(self,kernel):self.kernel=kernel;self.scheduler=SourceCallScheduler()
    def advance(self,bars,**kwargs):
        event=self.scheduler.event(len(bars),**kwargs);result=self.kernel.invoke(bars,event);self.scheduler.commit(result);return result
