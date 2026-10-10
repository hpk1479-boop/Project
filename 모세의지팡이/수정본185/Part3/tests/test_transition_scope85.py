"""Requested answer meanings: data-only fixtures, no AI or production edits."""
import sys, copy, importlib.util
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/'Part1/program'),str(ROOT/'Part3')]
spec=importlib.util.spec_from_file_location('runtime_helpers',ROOT/'Part3/tests/test_recipe_runtime84.py')
h=importlib.util.module_from_spec(spec);spec.loader.exec_module(h)
from strategy_recipe.contract import execution_plan
from strategy_recipe.runtime import IntentMachine
from lab.ai.schema import recipe_from_intent, output_schema
from lab.compiler import compile_recipe
import jsonschema

FAMILIES=['RSI','STO','DI','PRICE']
def s(kind,tf,**kw):return dict(kind=kind,tfs=kw.pop('tfs',[tf]),**kw)
def oz(tfs=['1m'],**kw):return dict(kind='OZ',tfs=tfs,validation_mode='NORMAL',trigger_mode='OZ',**kw)
def intent(steps,**kw):
    result=dict(symbols=['TEST'],direction='LONG',steps=steps,order_mode='SEQUENTIAL',persistent=True,final=oz())
    result.update(kw);return result
def percentile(kind,tf,families=None,side='LOWER',**kw):return s(kind,tf,families=families or FAMILIES,side=side,**kw)
def setup():return [s('MA_CROSS','1m',ma_left='EMA50',ma_right='EMA200',cross='GOLDEN'),s('FVG_NEW','5m',tfs=['5m','6m'],side='BULL',capture='zone')]

CASES={
61:intent([s('TREND_METRIC','1m',metric='rsi14',metric_operator='LTE',metric_value=30)],order_mode='SIMULTANEOUS'),
63:intent([s('FVG_NEW','5m',side='BULL',capture='zone'),s('FVG_TOUCH','5m',side='BULL',ref='zone')]),
66:intent([percentile('PERCENTILE_OUT','3m')],final=dict(kind='NOTIFY')),
67:intent([percentile('PERCENTILE_OUT','15m',['RSI'],'UPPER')],direction='SHORT',final=dict(kind='NOTIFY')),
68:intent([percentile('PERCENTILE_OUT_IN','1h',['DI'])],final=dict(kind='NOTIFY')),
71:intent([s('WONBI_TOUCH','30m',side='LOWER'),percentile('PERCENTILE_OUT_IN','3m',direction='SAME_AS_PREVIOUS_DIRECTION')],final=dict(kind='NOTIFY')),
72:intent([s('MA_CROSS','3m',ma_left='HMA50',ma_right='HMA168',cross='GOLDEN'),s('FVG_NEW','3m',side='BULL',capture='zone')],within_sec=1800,final_window_sec=3600,after_conditions=[s('FVG_TOUCH','3m',side='BULL',ref='zone')],final=dict(kind='NOTIFY')),
82:intent([s('WONBI_TOUCH','1h',side='LOWER'),percentile('PERCENTILE_OUT','15m'),percentile('PERCENTILE_OUT_IN','3m')],final=dict(kind='DEFINE')),
83:intent([s('TREND','1h',direction='SHORT'),s('FVG_TOUCH','1h',side='BEAR'),dict(kind='PERCENTILE_OUT_IN',tfs=['1m','2m','3m','4m','5m'],families=FAMILIES,side='UPPER')],direction='SHORT',final=dict(kind='DEFINE')),
88:intent([s('OZ_ALERT','1h',validation_mode='NORMAL',trigger_mode='BREAKER',capture='parent'),s('OZ_ALERT','15m',validation_mode='NORMAL',trigger_mode='BREAKER',capture='child',scope_ref='parent')],final=oz(['1m','2m','3m'],scope_ref='child'),lifecycle=dict(invalidate_refs=True)),
89:intent(setup(),order_mode='UNORDERED',final_window_sec=3600,after_conditions=[s('FVG_TOUCH','SOURCE',side='BULL',ref='zone')]),
90:intent(setup(),order_mode='UNORDERED',final_window_sec=1800,after_conditions=[s('FVG_TOUCH','SOURCE',side='BULL',ref='zone')],final=oz(scope_ref='zone'),lifecycle=dict(invalidate_refs=True)),
96:intent([s('BAR_CLOSE','30m')],direction='BOTH',final_window_sec=300,final=oz(['1m','2m','3m'],direction='BOTH')),
97:intent([s('BAR_CLOSE','30m')],direction='BOTH',final_window_sec=300,final=dict(kind='OZ',tfs=['1m'],direction='BOTH',validation_mode='BLIND',trigger_mode='OZ'),lifecycle=dict(restart_on=[s('BAR_CLOSE','30m')])),
99:intent([s('TREND','15m'),s('WONBI_TOUCH','15m',side='LOWER'),s('REGIME_BAND','6m',regime_families=FAMILIES,family_combine='MATCHING_FAMILY',relation='ABOVE'),percentile('PERCENTILE_OUT','3m',family_combine='MATCHING_FAMILY')],order_mode='SIMULTANEOUS',final=oz(regime_family='MATCHING_FAMILY')),
}
# Direction/family independence uses ordinary branches, never numbered runtime cases.
for number in (63,68,82,88,89,90):
    long=CASES[number];short=copy.deepcopy(long)
    short['direction']='SHORT'
    for step in short['steps']+short.get('after_conditions',[]):
        if step.get('side')=='LOWER':step['side']='UPPER'
        if step.get('side')=='BULL':step['side']='BEAR'
        if step.get('cross')=='GOLDEN':step['cross']='DEAD'
    CASES[number]=dict(long,steps=[],after_conditions=[],branches=[{k:v for k,v in x.items() if k!='symbols'} for x in (long,short)])
long=CASES[66];branches=[]
for direction,side in [('LONG','LOWER'),('SHORT','UPPER')]:
    for family in FAMILIES:branches.append(dict(direction=direction,steps=[percentile('PERCENTILE_OUT','3m',[family],side)],final=dict(kind='NOTIFY')))
CASES[66]=dict(long,steps=[],branches=branches)
def clean(value):
    if isinstance(value,dict):
        value.pop('cross',None)
        if 'symbols' in value:value['symbols']=['XAUUSD+']
        for child in value.values():clean(child)
    elif isinstance(value,list):
        for child in value:clean(child)
for value in CASES.values():clean(value)

@pytest.mark.parametrize('number',CASES)
def test_schema_recipe_compile_register(number):
    raw=dict(supported=True,intent='CREATE_STRATEGY',interpretation=CASES[number],needs_clarification=False,clarification_question=None,message_ko='')
    jsonschema.validate(raw,output_schema())
    recipe=recipe_from_intent(raw);plan=execution_plan(recipe['strategy_intent'])
    source=compile_recipe(recipe,'Test_SPECIAL901.py');namespace={}
    exec(compile(source,'<probe>','exec'),namespace)
    board=h.Board({'1m':h.rows([0,60,120])});api=h.API(board)
    api.market_context=lambda:(board,'XAUUSD+',120)
    api.request_watch=lambda *args,**kwargs:None
    installed=[];api.register_watch_handler=lambda owner,handler:installed.append(handler)
    namespace['register'](h.SimpleNamespace(special_api=api))
    assert installed and installed[0].machines
    assert plan['mode']=='CANONICAL'

def observe(step,fields,tf='1m',previous=None,direction='LONG'):
    board=h.Board({tf:h.rows([0,60,120],**(previous if previous is not None else fields))})
    port,api=h.make_port(h.meaning(),board,120)
    if previous is not None:
        port.observe(board,'TEST',step,direction,120)
        board.replace(tf,h.rows([0,60,120],**fields))
    return port.observe(board,'TEST',step,direction,121)
def bands(family='RSI',side=None):
    prefix='price' if family=='PRICE' else family
    return {prefix+'_lower_out' if side=='LOWER' else prefix+'_upper_out':10.} if side else {}

@pytest.mark.parametrize('value,expected',[(29,True),(30,True),(31,False)])
def test_061_rsi_boundary(value,expected):
    import pandas as pd
    from indicator_facts import rma
    prices=[1000.]
    for delta in [1.,-1.]*30:prices.append(prices[-1]+delta)
    changes=pd.Series(prices).diff();up=rma(changes.clip(lower=0),14).iloc[-1];down=rma((-changes).clip(lower=0),14).iloc[-1]
    fall=13*up*(100/value-1)-13*down
    prices += [prices[-1]-fall,prices[-1]-fall]
    board=h.Board({'1m':[dict(time=i*60,close=p) for i,p in enumerate(prices)]})
    port,_=h.make_port(h.meaning(),board)
    step=dict(CASES[61]['steps'][0],bar_state='CLOSED')
    assert port.observe(board,'TEST',step,'LONG',900)['matched']==expected

@pytest.mark.parametrize('number,tf,family,side,direction',[(66,'3m',f,side,d) for f in FAMILIES for side,d in [('LOWER','LONG'),('UPPER','SHORT')]]+[(67,'15m','RSI','UPPER','SHORT')])
def test_066_067_out_is_new_event_not_existing_state(number,tf,family,side,direction):
    step=execution_plan(intent([percentile('PERCENTILE_OUT',tf,[family],side)],direction=direction))['meaning']['steps'][0]
    observation=observe(step,bands(family,side),tf,previous={},direction=direction)
    assert observation['matched'] and observation['event'], 'OUT transition must be a discrete event'
    assert not observe(step,bands(family,side),tf,direction=direction)['matched'], 'pre-existing OUT must not invent a new event'

@pytest.mark.parametrize('family,expected',[('DI',True),('RSI',False),('STO',False),('PRICE',False)])
def test_068_di_only_return(family,expected):
    step=percentile('PERCENTILE_OUT_IN','1h',['DI'])
    assert observe(step,{},'1h',previous=bands(family,'LOWER'))['matched']==expected

def test_071_direction_inheritance():
    plan=execution_plan(CASES[71])['meaning']
    assert plan['steps'][1]['_resolved_direction']=='LONG'
    assert not observe(plan['steps'][1],{},'3m',previous=bands('RSI','UPPER'))['matched']
    assert observe(plan['steps'][1],{},'3m',previous=bands('RSI','LOWER'))['matched']

def test_072_independent_windows():
    plan=execution_plan(CASES[72])['meaning'];no=dict(ready=True,matched=False,event=True)
    m=IntentMachine(plan,'TEST','LONG')
    assert m.advance(100,[h.event('cross',100),no],after_observations=[no])==[]
    assert m.advance(1800,[h.event('cross',100),h.event('zone',1800)],after_observations=[no])==[]
    assert m.active_until==5400 and m.setup_time==1800
    assert m.advance(5300,[h.event('cross',100),h.event('zone',1800)],after_observations=[h.event('touch',5300)])==['NOTIFY']
    late=IntentMachine(plan,'TEST','LONG');late.advance(100,[h.event('cross',100),no],after_observations=[no])
    assert late.advance(1901,[no,h.event('zone',1901)],after_observations=[no])==[] and not late.active

def test_082_out_must_follow_touch_not_preexist():
    plan=execution_plan(CASES[82])['meaning']['branches'][0]
    board=h.Board({'15m':h.rows([0,60,120],**bands('RSI','LOWER'))})
    port,_=h.make_port(h.meaning(),board)
    out=port.observe(board,'TEST',plan['steps'][1],'LONG',120)
    no=dict(ready=True,matched=False,event=True)
    m=IntentMachine(plan,'TEST','LONG');m.advance(120,[h.event('touch',120),out,no])
    assert m.stage==1,'OUT that already existed must not satisfy the later OUT event'

def test_083_condition_chain_has_no_output():
    plan=execution_plan(CASES[83])['meaning'];m=IntentMachine(plan,'TEST','SHORT')
    no=dict(ready=True,matched=False,event=True)
    trend=dict(ready=True,matched=True,event=False)
    assert m.advance(100,[trend,h.event('fvg',100),no])==[]
    assert m.advance(101,[trend,h.event('fvg',100),h.event('return',101)])==['DEFINE']
    assert plan['final']['kind']=='DEFINE'

def test_088_parent_invalidation():
    plan=execution_plan(CASES[88])['meaning']['branches'][0];m=IntentMachine(plan,'TEST','LONG')
    no=dict(ready=True,matched=False,event=True)
    parent=h.event('p',10,resource=dict(kind='OZ',id='p',at=10));child=h.event('c',11,resource=dict(kind='OZ',id='c',at=11))
    m.advance(10,[parent,no]);assert m.advance(11,[parent,child])==['ARM']
    assert m.advance(12,[parent,child],lifecycle_context=dict(invalid_refs=['parent']))==['CANCEL']
    assert not m.captures and m.stage==0

@pytest.mark.parametrize('number',[89,90])
@pytest.mark.parametrize('order',[(0,1),(1,0)])
def test_089_090_unordered_without_artificial_deadline(number,order):
    plan=execution_plan(CASES[number])['meaning']['branches'][0];m=IntentMachine(plan,'TEST','LONG')
    no=dict(ready=True,matched=False,event=True);obs=[copy.deepcopy(no),copy.deepcopy(no)]
    obs[order[0]]=h.event(str(order[0]),10,resource=dict(kind='FVG',id='zone',tf='5m',at=10))
    assert m.advance(10,obs,after_observations=[no])==[]
    obs[order[1]]=h.event(str(order[1]),100000,resource=dict(kind='FVG',id='zone',tf='5m',at=100000))
    assert m.advance(100000,obs,after_observations=[no])==[]
    assert m.active and m.setup_time==100000
    assert m.advance(100001,obs,after_observations=[h.event('touch',100001)])==['ARM']

@pytest.mark.parametrize('number',[96,97])
def test_096_097_actual_bar_close_repeat_and_window(number):
    plan=execution_plan(CASES[number])['meaning'];plan['symbols']=['TEST'];board=h.Board({'30m':h.rows([0,1800,3600])})
    port,api=h.make_port(plan,board,3600);port.poll();assert not api.armed
    for now in (5400,7200):
        board.replace('30m',h.rows([now-3600,now-1800,now]));api.now=now;port.poll()
        assert api.armed and all(m.active_until==now+300 for m in port.machines)
        api.now=now+301;port.poll();assert not api.armed

def test_099_same_family_output_gate():
    plan=execution_plan(CASES[99])['meaning'];plan['symbols']=['TEST'];board=h.Board({'1m':h.rows([0,60,120])})
    port,api=h.make_port(plan,board,120);m=port.machines[0]
    m.active=True;m.after_ready=True;m.setup_time=100
    obs=[dict(ready=True,matched=True,event=False,families=None),dict(ready=True,matched=True,event=False,families=None),dict(ready=True,matched=True,event=False,families={'RSI'}),dict(ready=True,matched=True,event=False,families={'DI'})]
    port.observe=lambda *args,**kwargs:obs[plan['steps'].index(args[2])]
    event=dict(source_spec_id=port._sid(m),source_tf='1m',symbol='TEST',event_time=121,indicators_text='RSI')
    assert port.handle_oz_event(event).get('suppressed') and not api.deliveries
    obs[-1]['families']={'RSI'}
    assert not port.handle_oz_event(dict(event,indicators_text='DI')).get('delivered') or not api.deliveries
    port.handle_oz_event(event);assert len(api.deliveries)==1

def test_063_072_089_090_same_fvg_rejects_other_zone():
    h.test_fvg_reference_matches_captured_zone_not_newer_unrelated_zone()

@pytest.mark.parametrize('number,tf,side,direction',[(66,'3m','LOWER','LONG'),(67,'15m','UPPER','SHORT')])
def test_066_067_no_notification_for_out_existing_before_registration(number,tf,side,direction):
    plan=execution_plan(intent([percentile('PERCENTILE_OUT',tf,['RSI'],side)],direction=direction,final=dict(kind='NOTIFY')))['meaning']
    board=h.Board({tf:h.rows([0,60,120],**bands('RSI',side))});port,api=h.make_port(plan,board,120)
    notices=[];api.notify=lambda *args,**kwargs:notices.append((args,kwargs))
    port.poll()
    assert not notices,'already OUT at registration must not create a new OUT notification'

def test_088_child_must_occur_during_parent_valid_state():
    plan=execution_plan(CASES[88])['meaning']['branches'][0];plan['symbols']=['TEST']
    board=h.Board({'15m':h.rows([0,900,1800],close=500,high=501,low=499)})
    port,_=h.make_port(plan,board,1800);m=port.machines[0]
    m.captures['parent']=dict(kind='OZ',id='parent',tf='1h',at=1801)
    port._reference_alive=lambda *args:True
    port.oz_events[('TEST','15m','NORMAL','BREAKER','LONG')]=(1,1800,dict(kind='OZ',id='child',tf='15m',at=1800,b0_price=500))
    obs=port.observe(board,'TEST',plan['steps'][1],'LONG',1800,m)
    assert not obs['matched'],'child alert before parent activation must not satisfy INSIDE_PARENT'

def test_090_oz_origin_outside_same_fvg_is_rejected_even_if_price_returns():
    plan=execution_plan(CASES[90])['meaning']['branches'][0];plan['symbols']=['TEST']
    board=h.Board({'1m':h.rows([0,60,120],close=100)})
    port,api=h.make_port(plan,board,120);m=port.machines[0]
    m.active=True;m.after_ready=True;m.setup_time=100
    m.captures['zone']=dict(kind='FVG',id='z',tf='5m',lower=90,upper=110,at=90)
    port.poll=lambda *args:None
    port.observe=lambda *args,**kwargs:dict(ready=True,matched=True,event=True)
    port._reference_alive=lambda *args:True
    result=port.handle_oz_event(dict(source_spec_id=port._sid(m),symbol='TEST',source_tf='1m',event_time=121,b0_price=500,direction='LONG'))
    assert result.get('suppressed') and not api.deliveries,'current price inside FVG cannot replace OZ origin inside FVG'

def test_out_poll_emits_once_then_requires_a_new_transition():
    plan=execution_plan(intent([percentile('PERCENTILE_OUT','1m',['RSI'])],final=dict(kind='NOTIFY')))['meaning']
    plan['symbols']=['TEST'];board=h.Board({'1m':h.rows([0,60,120])})
    port,api=h.make_port(plan,board,120);notices=[]
    api.notify=lambda *args,**kwargs:notices.append((args,kwargs))
    port.poll();assert not notices
    board.replace('1m',h.rows([0,60,120],**bands('RSI','LOWER')));api.now=121;port.poll();assert len(notices)==1
    api.now=122;port.poll();assert len(notices)==1
    board.replace('1m',h.rows([0,60,120],**bands('RSI','LOWER')));api.now=123;port.poll();assert len(notices)==1
    board.replace('1m',h.rows([0,60,120]));api.now=124;port.poll()
    board.replace('1m',h.rows([0,60,120],**bands('RSI','LOWER')));api.now=125;port.poll();assert len(notices)==2

def test_closed_out_transition_ignores_forming_bar_changes():
    step=execution_plan(intent([percentile('PERCENTILE_OUT','1m',['RSI'],bar_state='CLOSED')]))['meaning']['steps'][0]
    board=h.Board({'1m':h.rows([0,60,120])});port,_=h.make_port(h.meaning(),board,120)
    assert not port.observe(board,'TEST',step,'LONG',120)['matched']
    current=h.rows([0,60,120]);current[-1].update(bands('RSI','LOWER'));board.replace('1m',current)
    assert not port.observe(board,'TEST',step,'LONG',121)['matched']
    current.append(dict(time=180));board.replace('1m',current)
    assert port.observe(board,'TEST',step,'LONG',180)['matched']

def test_old_retained_events_and_same_time_followups_are_not_reused():
    plan=execution_plan(intent([s('MA_CROSS','1m',ma_left='EMA50',ma_right='EMA200'),s('FVG_NEW','5m')]))['meaning']
    m=IntentMachine(plan,'TEST','LONG');no=dict(ready=True,matched=False,event=True)
    assert m.advance(100,[h.event('old',90),no])==[] and m.stage==0
    assert m.advance(101,[h.event('new',101),h.event('old-fvg',99)])==[] and m.stage==1
    assert m.advance(102,[no,h.event('same-instant',101)])==[] and m.stage==1
    assert m.advance(103,[no,h.event('fvg',103)])==['ARM']

def test_parent_scope_is_cycle_lifetime_not_price_range():
    plan=execution_plan(CASES[88])['meaning']['branches'][0];plan['symbols']=['TEST']
    board=h.Board({'1h':h.rows([0,3600,7200]),'15m':h.rows([0,900,1800],close=500)})
    status={('TEST','1h','NORMAL','BREAKER','LONG'):1,('TEST','15m','NORMAL','BREAKER','LONG'):2}
    board.processor=lambda name:dict(resources=status)
    port,_=h.make_port(plan,board,1800);m=port.machines[0]
    parent=dict(kind='OZ',tf='1h',validation_mode='NORMAL',trigger_mode='BREAKER',direction='LONG',b0_time=1,at=1700)
    m.captures['parent']=parent
    child=dict(kind='OZ',tf='15m',validation_mode='NORMAL',trigger_mode='BREAKER',direction='LONG',b0_time=2,b0_price=500,at=1800)
    port.oz_events[('TEST','15m','NORMAL','BREAKER','LONG')]=(1,1800,child)
    assert port.observe(board,'TEST',plan['steps'][1],'LONG',1800,m)['matched']
    status[('TEST','1h','NORMAL','BREAKER','LONG')]=None
    assert not port.observe(board,'TEST',plan['steps'][1],'LONG',1801,m)['matched']
    # A replacement cycle cannot keep a captured parent alive.
    status[('TEST','1h','NORMAL','BREAKER','LONG')]=99
    assert not port.observe(board,'TEST',plan['steps'][1],'LONG',1802,m)['matched']

@pytest.mark.parametrize('origin,allowed',[(89,False),(90,True),(100,True),(110,True),(111,False),(None,False),(float('nan'),False)])
def test_fvg_origin_gate_is_frozen_and_ignores_current_price(origin,allowed):
    board=h.Board({'1m':h.rows([0,60,120],close=500)})
    port,_=h.make_port(h.meaning(),board,120)
    facts=[dict(zone_id='chosen')];port._fact_snapshot=lambda *args:facts
    zone=dict(kind='FVG',id='chosen',tf='5m',lower=90,upper=110,at=100)
    assert port._scope_matches(board,'TEST',zone,dict(b0_price=origin),121)==allowed
    facts[:]=[dict(zone_id='different')]
    assert not port._scope_matches(board,'TEST',zone,dict(b0_price=100),121)

def test_same_fvg_source_tf_comes_from_reference_not_other_setup_step():
    board=h.Board({'5m':h.rows([0,300,600]),'1m':h.rows([0,60,120])})
    port,_=h.make_port(h.meaning(),board,600);m=port.machines[0];m.source_tf='1m'
    m.captures['zone']=dict(kind='FVG',id='chosen',tf='5m',lower=90,upper=110,at=500)
    seen=[]
    def facts(board,family,symbol,tf):
        seen.append(tf);return [dict(zone_id='chosen',fvg_side='BULL',zone_bot=90,zone_top=110,touched_now=True)]
    port._fact_snapshot=facts
    assert port.observe(board,'TEST',s('FVG_TOUCH','SOURCE',side='BULL',ref='zone'),'LONG',600,m)['matched']
    assert seen==['5m']

def test_native_live_and_replay_out_notifications_match():
    import numpy as np
    from event_application import create_event_engine, register_strategy_loader, unregister_strategy_loader
    from event_engine.model import Kind, FeedSnapshot
    from staff_schema import PIPE_VALUE_COLUMNS
    from strategy_recipe.registry import dependencies
    from strategy_recipe.port import IntentPort
    name='TRANSITION_PROBE'
    plan=execution_plan(intent([percentile('PERCENTILE_OUT','1m',['RSI'])],final=dict(kind='NOTIFY')))['meaning']
    plan['symbols']=['TEST']
    def register(manager):IntentPort(manager,plan,'Transition',name).install()
    plugin=h.SimpleNamespace(__name__=name,register=register,OZ_DECLARATIONS=())
    register_strategy_loader(name,lambda:plugin,dependencies=dependencies(plan))
    try:
        engines=[create_event_engine(dict(SYMBOLS='TEST',TELEGRAM_CHAT_ID='TEST'),symbols=['TEST'],selection=[name],backtest=mode) for mode in (False,True)]
        for seq,is_out in enumerate((True,True,False,True,True,False,True),1):
            values=np.full((240,len(PIPE_VALUE_COLUMNS)),100.,dtype='<f8')
            for i,column in enumerate(PIPE_VALUE_COLUMNS):
                if column.endswith(('_lower_out','_upper_out')):values[:,i]=np.nan
            if is_out:values[:,PIPE_VALUE_COLUMNS.index('RSI_lower_out')]=10.
            times=np.arange(240,dtype='<i8')*60+1790812800
            feed=FeedSnapshot(times,np.ones(240,dtype='<i8'),values,seq,'transition',{})
            for engine in engines:
                engine.ingress.post(Kind.MARKET_BUNDLE,source='staff',source_seq=seq,source_time=(1790827200+seq)*1000,payload=dict(symbol='TEST',feeds={'1m':feed}))
                engine.run();assert not engine.error_log
            assert [signal.payload for signal in engines[0].signals]==[signal.payload for signal in engines[1].signals]
        for engine in engines:
            assert len([signal for signal in engine.signals if signal.payload.get('content',{}).get('type')=='NOTIFICATION'])==2
    finally:unregister_strategy_loader(name)

def test_fvg_origin_inside_is_delivered_even_with_current_price_outside():
    plan=execution_plan(CASES[90])['meaning']['branches'][0];plan['symbols']=['TEST']
    board=h.Board({'1m':h.rows([0,60,120],close=500)})
    port,api=h.make_port(plan,board,120);m=port.machines[0]
    m.active=True;m.after_ready=True;m.setup_time=100
    m.captures['zone']=dict(kind='FVG',id='z',tf='5m',lower=90,upper=110,at=90)
    port.poll=lambda *args:None
    port.observe=lambda *args,**kwargs:dict(ready=True,matched=True,event=True)
    port._reference_alive=lambda *args:True
    event=dict(source_spec_id=port._sid(m),symbol='TEST',source_tf='1m',event_time=121,b0_price=100,direction='LONG')
    assert port.handle_oz_event(event)['delivered'] and len(api.deliveries)==1

def test_chain_only_consumes_out_after_wonbi_step_activation():
    plan=execution_plan(CASES[82])['meaning']['branches'][0];plan['symbols']=['TEST']
    board=h.Board({'1h':h.rows([0,60,120],low=101,wonbi_lower=100),
        '15m':h.rows([0,60,120]),'3m':h.rows([0,60,120])})
    import numpy as np
    original_fact=board.fact
    board.fact=lambda name,symbol,tf:({field:np.full(len(board.feeds[symbol,tf].time),100.) for field in ('wonbi_lower','wonbi_upper')}
        if name=='WONBI_BANDS' else original_fact(name,symbol,tf))
    port,api=h.make_port(plan,board,120);port.poll();m=port.machines[0];assert m.stage==0
    board.replace('15m',h.rows([0,60,120],**bands('RSI','LOWER')));api.now=121;port.poll();assert m.stage==0
    board.replace('1h',h.rows([0,60,120],low=99,wonbi_lower=100));api.now=122;port.poll();assert m.stage==1
    board.replace('15m',h.rows([0,60,120]));api.now=123;port.poll();assert m.stage==1
    board.replace('15m',h.rows([0,60,120],**bands('RSI','LOWER')));api.now=124;port.poll();assert m.stage==2
    board.replace('3m',h.rows([0,60,120],**bands('RSI','LOWER')));api.now=125;port.poll();assert not m.active
    board.replace('3m',h.rows([0,60,120]));api.now=126;port.poll();assert m.active and m.after_ready
    assert not api.armed and not api.deliveries
