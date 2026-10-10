import sys
from pathlib import Path
from types import SimpleNamespace
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'Part1/program'))
from event_engine import EventEngine,IngressSequencer,Kind,Subscriptions
from event_engine.frames import available
from event_engine.domain_support import subscription_command
from test_event_e2_domains import snap


def test_health_stale_reconnect_preserve_checkpoint():
    class Consumer:
        name='check'
        def subscriptions(self):return Subscriptions(kinds=(Kind.MARKET_BUNDLE,Kind.TIMER,Kind.FEED_HEALTH,Kind.COMMAND))
        def on_event(self,event,board,state,emit):state['available']=available(board,'XAUUSD+','1m')
    def make():return EventEngine(IngressSequencer(),[Consumer()])
    e=make()
    def post(kind,when,**payload):
        e.ingress.post(kind,source='test',source_seq=None,source_time=when,payload={'symbol':'XAUUSD+',**payload});e.run()
    post(Kind.MARKET_BUNDLE,1000,feeds={'1m':snap()})
    post(Kind.COMMAND,31000);assert e.strategy_state['check']['available']
    post(Kind.COMMAND,31001);assert not e.strategy_state['check']['available']
    post(Kind.FEED_HEALTH,32000,status='RECONNECT')
    assert not e.board._feeds
    r=make();r.restore(e.checkpoint());assert not r.board._feeds
    post(Kind.MARKET_BUNDLE,33000,feeds={'1m':snap(seq=1)})
    assert e.strategy_state['check']['available']


def test_shared_subscription_cancel_keeps_other_owner():
    state={}
    def apply(owner,action,fields=()):
        event=SimpleNamespace(kind=Kind.SIGNAL,source=owner,payload={'strategy':owner,
            'content':{'type':'WATCH_COMMAND','command':{'action':action,'watch_id':'common',
             'symbol':'XAUUSD+','source_tf':'1m','requested_fields':fields}}})
        return subscription_command(event,state,'TREND')
    apply('A','TREND_WATCH',('EMA',))
    assert apply('B','TREND_WATCH',('HMA',))['requested_fields']==['EMA','HMA']
    assert apply('A','CANCEL_TREND')['requested_fields']==['HMA']
    assert apply('B','CANCEL_TREND')['action']=='CANCEL_TREND'


def test_composer_five_errors_alert_once_without_stopping_and_resume():
    from event_engine.composition_consumer import CompositionConsumer
    class BrokenKernel:
        def step(self,*args):raise ValueError('injected composition failure')
    consumer=CompositionConsumer('COMPOSER',lambda *args:BrokenKernel(),lambda raw:'COMPOSER')
    class Healthy:
        name='other'
        def subscriptions(self):return Subscriptions(kinds=(Kind.COMMAND,))
        def on_event(self,event,board,state,emit):state['calls']=state.get('calls',0)+1
    def make():return EventEngine(IngressSequencer(),[consumer,Healthy()])
    e=make()
    def post(engine,i):
        engine.ingress.post(Kind.COMMAND,source='commands',source_seq=i,source_time=i*1000,
            payload={'symbol':'XAUUSD+','text':'test','chat_id':'offline'})
        engine.run()
    for i in range(1,7):post(e,i)
    assert e.failures['COMPOSER']==6 and e.status=='DEGRADED'
    assert not e.disabled and len(e.signals)==1
    assert e.signals[0].payload['content']['status']=='DEGRADED'
    r=make();r.restore(e.checkpoint())
    for i in range(7,12):post(e,i);post(r,i)
    assert e.failures['COMPOSER']==r.failures['COMPOSER']==11
    assert len(e.error_log)==11 and len(e.signals)==1 and not r.signals
    assert e.strategy_state['other']['calls']==r.strategy_state['other']['calls']==11
    assert r.status=='DEGRADED' and not r.disabled


def test_retained_chain_deadline_fires_once_not_on_every_later_bundle():
    from event_engine.composition_consumer import CompositionConsumer
    class RetainedDeadline:
        def __init__(self):self.timer_calls=0;self.timestamp=0
        def step(self,event,*args):
            self.timestamp=event.source_time
            if event.kind==Kind.TIMER:self.timer_calls+=1
            return [],[],None
        def deadlines(self):return [2000] if self.timestamp<=2000 else []
    c=CompositionConsumer('COMPOSER',lambda *args:RetainedDeadline(),lambda raw:'COMPOSER')
    e=EventEngine(IngressSequencer(),[c])
    for i,when in enumerate((1000,2000,3000,4000)):
        e.ingress.post(Kind.MARKET_BUNDLE,source='staff',source_seq=i+1,source_time=when,
            payload={'symbol':'XAUUSD+','feeds':{'1m':snap(seq=i+1)}})
        e.run()
    assert not e.error_log,e.error_log
    assert e.strategy_state['COMPOSER']['kernels']['XAUUSD+'].timer_calls==1


def test_fact_emission_preserves_the_fact_symbol():
    from event_engine.domain_support import emit_facts
    emitted=[]
    emit_facts(emitted.append,'XAUUSD+','SWEEP',[{'symbol':'BTCUSD','event_id':'btc-fact','kind':'SWEEP_INVALIDATED'}])
    assert emitted[0].symbol=='BTCUSD'
