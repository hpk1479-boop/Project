"""Expected meanings from the user-approved #001–#050 answer keys.

Test fixtures, NOT a natural-language parser or a model training dataset.
The numbers are deliberately confined to tests; production handles common
conditions, independent branches, setup windows and inherited lifecycles.
XAUUSD in the historical keys resolves to the current broker's XAUUSD+.
"""
import copy

MINUTES = ['1m','2m','3m','4m','5m','6m','10m','12m','15m','20m','30m']
FAMILIES = ['RSI','STO','DI','PRICE']

def step(kind, tf='1m', **fields):
    return {'kind':kind, 'tfs':tf if isinstance(tf,list) else [tf], **fields}

def ma(tf='1m', family='EMA', fast=50, slow=200, **fields):
    return step('MA_STATE',tf,ma_family=family,fast_period=fast,slow_period=slow,**fields)

def cross(tf='1m', left='EMA50', right='EMA200', **fields):
    return step('MA_CROSS',tf,ma_left=left,ma_right=right,bar_state='CLOSED',**fields)

def price(tf, family, period):
    return step('MA_PRICE_STATE',tf,ma_family=family,slow_period=period)

def slope(tf, period):
    return step('MA_SLOPE_STATE',tf,ma_family='HMA',slow_period=period,lookback=2)

def fvg(tf, kind='FVG_STATE', **fields):
    return step(kind,tf,**({'state':'AREA'} if kind=='FVG_STATE' else {}),**fields)

def oz(tfs=('1m',), validation='NORMAL', trigger='OZ', **fields):
    return {'kind':'OZ','tfs':list(tfs),'validation_mode':validation,'trigger_mode':trigger,**fields}

def meaning(steps=(), direction='LONG', final=None, **fields):
    return {'direction':direction,'symbols':['XAUUSD+'],'steps':list(steps),
        'order_mode':'SIMULTANEOUS','global_combine':'ALL',
        'final':final or oz(),'persistent':False, **fields}

def regime(tfs, down=False):
    return step('REGIME_BAND',tfs,regime_families=FAMILIES,
        family_combine='MATCHING_FAMILY',tf_combine='ALL',relation='SLOPE_DOWN' if down else 'SLOPE_UP')

def branches(units, direction='BOTH', final=None, **fields):
    return meaning(direction=direction,final=final,branches=units,**fields)

def trend_wonbi(tf, direction='LONG', tfs=('5m',), validation='NORMAL', base=None):
    return meaning([step('TREND',tf),step('WONBI_TOUCH',tf)],direction,oz(tfs,validation,'BREAKER'),
        **({'base_special':base} if base else {}))

def setup(cross_tfs,new_tfs,touch_tfs,direction='BOTH',order='SEQUENTIAL',gap=1800,window=None):
    return meaning([cross(cross_tfs),fvg(new_tfs,'FVG_NEW',direction='SAME_AS_PREVIOUS_DIRECTION')],
        direction,oz(['1m','2m'],direction='SAME_AS_PREVIOUS_DIRECTION'),base_special='SPECIAL3',
        order_mode=order,within_sec=gap,final_window_sec=window,
        after_conditions=[fvg(touch_tfs,'FVG_TOUCH',direction='SAME_AS_PREVIOUS_DIRECTION')])

def inherited(base,direction='LONG'):
    params = {'cycle_tf':'30m','touch_tf':'15m','middle_tf':'3m','execution_tf':'1m',
        'active_window_sec':360,'atr_mult':1.0} if base=='SPECIAL4' else {'source_tfs':['15m'],
        'source_ema_fast':50,'source_ema_slow':200,'ema_filter':True}
    return meaning(direction=direction,final=oz(['1m'] if base=='SPECIAL4' else ['1m','2m','3m'],
        'NORMAL' if base=='SPECIAL4' else 'BLIND'),base_special=base,
        inherit_base_rules=True,special_parameters=params)

ANSWERS = {
    1:meaning([ma(side='ABOVE')]),
    2:meaning([ma(side='BELOW')],'SHORT'),
    3:meaning([cross()],final={'kind':'NOTIFY'}),
    4:meaning([fvg('5m','FVG_NEW',side='BULL')],final={'kind':'NOTIFY'}),
    5:meaning([fvg('5m',side='BULL')]),
    6:meaning([cross(left='HMA50',right='HMA168')],final={'kind':'NOTIFY'}),
    7:meaning([cross(left='HMA17',right='HMA50')],'SHORT',{'kind':'NOTIFY'}),
    8:meaning([cross(left='WMA17',right='SMA20')],final={'kind':'NOTIFY'}),
    9:meaning([step('OZ_ALERT','15m',validation_mode='NORMAL',trigger_mode='OZ')],
        'BOTH',oz(MINUTES[:3],direction='SAME_AS_PREVIOUS_DIRECTION'),order_mode='SEQUENTIAL'),
    10:meaning([step('WONBI_TOUCH','1h')],'BOTH',oz(MINUTES[:5],direction='SAME_AS_PREVIOUS_DIRECTION'),order_mode='SEQUENTIAL'),
    11:branches([{'steps':[fvg(tf,side='BEAR')]} for tf in ('15m','30m')],
        'SHORT',oz(MINUTES[:3])),
    12:meaning([cross()],final_window_sec=3600,order_mode='SEQUENTIAL'),
    13:meaning([step('SESSION_START',session='LONDON')],'BOTH',{'kind':'NOTIFY'}),
    14:meaning([step('LIQUIDITY_LEVEL',level='PDL',relation='BREAK_DOWN')],'SHORT',{'kind':'NOTIFY'}),
    15:meaning([step('LIQUIDITY_LEVEL',level='PDL',relation='TOUCH')],final=oz(MINUTES[:5],'BLIND','BREAKER')),
    16:trend_wonbi('1h',validation='BLIND'),
    17:meaning([step('LIQUIDITY_LEVEL',level='PREV_4H_LOW',relation='TOUCH')],final=oz(MINUTES[2:5])),
    18:meaning([step('LIQUIDITY_LEVEL',level='PREV_8H_HIGH',relation='TOUCH')],'SHORT'),
    19:meaning([price('1m','HMA',168)],final=oz(trigger='BREAKER')),
    20:meaning([ma(family='HMA',fast=90,slow=270)]),
    21:branches([{'steps':[step('TREND',tf),step('WONBI_TOUCH',tf)]} for tf in ('1h','2h','3h','4h')],
        final=oz(MINUTES+['1h'],trigger='BREAKER'),base_special='SPECIAL1'),
    22:branches([{'steps':[step('EXTERNAL_LIQUIDITY_TOUCH',tf)],'final':oz([tf],trigger='BREAKER',direction='SAME_AS_PREVIOUS_DIRECTION')}
        for tf in MINUTES],base_special='SPECIAL2'),
    23:setup(['1m','2m'],['5m','6m'],MINUTES[4:9],window=3600),
    24:branches([{'direction':d,'steps':[step('TREND','15m'),regime(['6m'],d=='SHORT')],
        'final':oz(trigger='BREAKER',regime_family='MATCHING_FAMILY')} for d in ('LONG','SHORT')],base_special='SPECIAL7'),
    25:branches([{'steps':[ma(tf),slope(tf,168),fvg(tf,'FVG_TOUCH',side='BULL')]} for tf in ('15m','30m')],
        'LONG',oz(MINUTES[:6],trigger='BREAKER'),base_special='SPECIAL6'),
    26:trend_wonbi('2h',validation='BLIND',base='SPECIAL1'),
    27:meaning([step('EXTERNAL_LIQUIDITY_TOUCH')],'BOTH',oz(MINUTES[:5],direction='SAME_AS_PREVIOUS_DIRECTION'),order_mode='SEQUENTIAL'),
    28:meaning([slope('3m',168)],'BOTH',oz(direction='SAME_AS_PREVIOUS_DIRECTION')),
    29:meaning(direction='BOTH',final=oz(validation='BLIND')),
    30:meaning([regime(['5m','15m'])],final=oz(MINUTES[:3],'BLIND',regime_family='MATCHING_FAMILY')),
    31:inherited('SPECIAL4'),
    32:inherited('SPECIAL5'),
    33:meaning([step('EXTERNAL_LIQUIDITY_TOUCH','10m',side='HIGH')],'SHORT',oz(['10m'],trigger='BREAKER'),base_special='SPECIAL2'),
    34:meaning([ma('30m'),slope('30m',168),fvg('30m','FVG_TOUCH',side='BEAR')],'SHORT',oz(MINUTES[:6],trigger='BREAKER'),base_special='SPECIAL6'),
    35:setup('2m','6m','10m','SHORT','UNORDERED'),
    36:meaning([price('5m','EMA',37)],'SHORT',oz(['3m'],trigger='BREAKER')),
    37:meaning([cross('3m','WMA17','SMA20')],final=oz(MINUTES[:2]),order_mode='SEQUENTIAL',final_window_sec=2700),
    38:trend_wonbi('1h','SHORT',MINUTES[:9],'BLIND','SPECIAL1'),
    39:meaning(direction='BOTH',final=oz(MINUTES[:3],'BLIND','BREAKER')),
    40:meaning([regime(['6m','12m'],True)],'SHORT',oz(MINUTES[:3],'BLIND',regime_family='MATCHING_FAMILY')),
    41:trend_wonbi('3h','LONG',MINUTES,base='SPECIAL1'),
    42:meaning([step('EXTERNAL_LIQUIDITY_TOUCH','20m',side='LOW')],final=oz(['20m'],trigger='BREAKER'),base_special='SPECIAL2'),
    43:setup('2m','5m','12m','SHORT',gap=1200,window=2700),
    44:inherited('SPECIAL4','SHORT'),
    45:inherited('SPECIAL5','SHORT'),
    46:meaning([price('6m','HMA',270),slope('6m',90)],final=oz(['3m'],trigger='BREAKER')),
    47:meaning([cross('12m','WMA23','SMA37')],final=oz(MINUTES[:3]),order_mode='SEQUENTIAL',final_window_sec=1200),
    48:meaning([step('SESSION_START',session='NEWYORK')],'BOTH',{'kind':'NOTIFY'}),
    49:meaning([regime(['5m','15m'])],final=oz(['2m'],trigger='BREAKER',regime_family='MATCHING_FAMILY')),
    50:meaning([fvg('2h',side='BEAR')],'SHORT',oz(MINUTES[:6],'BLIND','BREAKER')),
}

def cases():
    assert sorted(ANSWERS) == list(range(1,51))
    for number, interpretation in ANSWERS.items():
        start = (number-1)//10*10+1
        yield {'id':f'{number:03}', 'source':f'MOSES_TRAINING_{start:03}_{start+9:03}.md',
            'intent':{'supported':True,'intent':'CREATE_STRATEGY','interpretation':copy.deepcopy(interpretation),
                'needs_clarification':False,'clarification_question':None,
                'message_ko':f'사용자 확정 정답 #{number:03}의 전략 의미'}}
