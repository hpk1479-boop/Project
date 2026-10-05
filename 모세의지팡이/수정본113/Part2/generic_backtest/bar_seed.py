"""Bounded broker candle initialization; never replay pre-test ticks."""
from .canonical import identity, read_json, write_json, finite
from .contracts import GenericError
from pit.models import BarState


def collect(provider, config, destination, plan, cancel=lambda: None):
    from .contracts import InstrumentSpec
    from .calendar import CalendarRegistry
    from .plugins import validate_requirements
    from .evaluation import ONE_MINUTE_CLOSE, close_gate_lookbacks, evaluation_base_tf
    instrument=InstrumentSpec(**config.instrument);calendar=CalendarRegistry(config.calendar)
    limit=config.resources.get('max_history_bars',100000)
    signal=validate_requirements(plan['signal'],limit)
    entry=validate_requirements(plan['entry'],limit) if config.mode=='TRADE' else None
    cutoff=config.start_ns
    if cutoff % (60*10**9):
        raise GenericError('E_BAR_SEED', 'bar initialization requires a minute-aligned start')
    lookbacks={}
    for req in (signal, entry):
        if req is None: continue
        sizes=req.completed_lookback_by_tf
        if config.evaluation_mode==ONE_MINUTE_CLOSE:
            sizes=close_gate_lookbacks(sizes,evaluation_base_tf(signal.required_timeframes))
        for tf,n in sizes.items():lookbacks[tf]=max(lookbacks.get(tf,0),n)
    if config.mode=='TRADE':
        for p in config.stop_variants:
            if p['kind']=='N_COMPLETED_EXTREME':lookbacks[p['tf']]=max(lookbacks.get(p['tf'],0),p['n'])
    feeds={}
    for tf,n in lookbacks.items():
        cancel()
        rows=provider.bars_before(instrument.broker_symbol,tf,cutoff,n+2)
        closed=[]
        for r in rows:
            start=int(r['time'])*10**9
            a,b=calendar.interval_for(instrument.broker_symbol,tf,start)
            if a!=start:raise GenericError('E_BAR_SEED','broker/calendar alignment '+tf)
            if b<=cutoff:
                closed.append([a,b,*[float(r[k]) for k in ('open','high','low','close')],int(r['tick_volume'])])
        # A broker can have fewer historical bars than requested (new symbols).
        # Preserve that absence; do not fabricate history or replay years of ticks.
        forming=[]
        a,b=calendar.interval_for(instrument.broker_symbol,tf,cutoff)
        if a<cutoff:
            minutes=provider.bars_before(instrument.broker_symbol,'1m',cutoff,int((cutoff-a)//(60*10**9))+1)
            prefix=[r for r in minutes if a<=int(r['time'])*10**9 and (int(r['time'])+60)*10**9<=cutoff]
            if prefix:
                forming=[[a,b,float(prefix[0]['open']),max(float(r['high']) for r in prefix),
                          min(float(r['low']) for r in prefix),float(prefix[-1]['close']),
                          sum(int(r['tick_volume']) for r in prefix)]]
        feeds[tf]={'closed':closed[-n:] if n else [],'forming':forming}
    payload={'kind':'MT5_PRESTART_BARS_V1','instrument':config.instrument,'calendar':calendar.definition,
             'cutoff_ns':cutoff,'feeds':feeds}
    payload['identity']=identity(payload)
    write_json(destination,payload)
    return {'path':str(destination),'identity':payload['identity']}


class BarSeed:
    def __init__(self,descriptor,config):
        from .paths import internal_path
        self.data=read_json(internal_path(descriptor['path']))
        body={k:v for k,v in self.data.items() if k!='identity'}
        self.identity=identity(body)
        if (self.identity!=descriptor['identity'] or self.data.get('identity')!=self.identity
                or self.data.get('kind')!='MT5_PRESTART_BARS_V1'
                or self.data['cutoff_ns']!=config.start_ns or self.data['instrument']!=config.instrument
                or self.data['calendar']!=config.calendar):
            raise GenericError('E_BAR_SEED','seed identity/config mismatch')

    def apply(self,core):
        cutoff=self.data['cutoff_ns']
        core.prefix=identity((core.prefix,self.identity))
        for tf,queue in core.book.closed.items():
            if tf not in self.data['feeds']:raise GenericError('E_BAR_SEED','missing timeframe '+tf)
            feed=self.data['feeds'][tf];previous=-1
            if len(feed['forming'])>1:raise GenericError('E_BAR_SEED','multiple forming bars')
            for state,rows in (('COMPLETED',feed['closed']),('FORMING',feed['forming'])):
                for row in rows:
                    a,b,o,h,l,c,volume=row
                    if (a<=previous or core.calendar.interval_for(core.instrument.broker_symbol,tf,a)!=(a,b)
                            or (state=='COMPLETED' and b>cutoff)
                            or (state=='FORMING' and not a<cutoff<b)
                            or not all(finite(v) and v>0 for v in (o,h,l,c))
                            or not l<=min(o,c)<=max(o,c)<=h or volume<0):
                        raise GenericError('E_BAR_SEED','invalid/future bar '+tf)
                    previous=a
                    bar=BarState(identity((self.identity,tf,a)),core.instrument.broker_symbol,tf,a,b,
                        o,h,l,c,volume,0,0,core.prefix,0,state,'COMPLETE_PREFIX',False,
                        0 if state=='COMPLETED' else None,'BROKER_PRESTART_BAR_SEED')
                    if state=='COMPLETED':queue.append(bar)
                    else:core.book.forming[tf]=bar
            core.book._closed_views[tf]=tuple(queue)
