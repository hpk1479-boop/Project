"""Revision48 requirements: four profiles, fail-closed settings, offline LIVE/replay parity.

The observed-state fixture explicitly supplies already tracked B0/HMA candidates;
existing test_oz_rewrite separately exercises actual OUT->IN/cross/timer tracking.
No broker, real Telegram transport, or wall-clock market observation is used.
"""
from __future__ import annotations
import ast,copy,json,socket,sys
from pathlib import Path
from types import SimpleNamespace
import numpy as np
import pytest
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'Part1/program'),str(ROOT/'Part2'),str(ROOT/'Part3')]
import oz_profiles as profiles
from oz_profile_loader import load_saved_oz, LegacyUnsupportedProfile
from test_oz_rewrite import view,edit,profile,seed,drive
from oz_engine.common import TF_MAP,PERCENTILES,WatchSpec
from oz_engine.controllers import OZWatchController,ExternalLiquidityController
from oz_engine.runtime import SourceClock,OZRuntime
from event_application import create_event_engine,load_strategy_inputs
from event_engine.domain_support import plain

ALL_TFS=tuple(sorted(set(TF_MAP)|{tf for pair in TF_MAP.values() for tf in pair}))
CONFIG={'TELEGRAM_TOKEN':'OFFLINE','TELEGRAM_CHAT_ID':'offline',
        'STAFF_ALLOWED_SYMBOLS':'XAUUSD+','TARGET_SYMBOLS':'XAUUSD+',
        'MAIN_ASIA':'0000-2400','MAIN_LONDON':'0000-2400','MAIN_NEWYORK':'0000-2400'}

@pytest.fixture(autouse=True)
def prohibit_network(monkeypatch):
    def forbidden(*args,**kwargs):raise AssertionError('REV48 network/Telegram transmission forbidden')
    for name in ('connect','connect_ex','sendto'):monkeypatch.setattr(socket.socket,name,forbidden)
    monkeypatch.setattr(socket,'create_connection',forbidden)


def test_exactly_four_labels_and_original_evaluation_order():
    assert [profiles.profile_label(*p) for p in profiles.PROFILE_KEYS]==[
        '올존','무지성 올존','브레이커 올존','무지성 브레이커 올존']
    assert profiles.TRIGGER_MODES=={'OZ','BREAKER'}
    assert profiles.EVALUATION_PROFILE_KEYS==(('NORMAL','OZ'),('NORMAL','BREAKER'),('BLIND','OZ'),('BLIND','BREAKER'))

@pytest.mark.parametrize('vm,tm',profiles.PROFILE_KEYS)
def test_labels_roundtrip(vm,tm):
    assert profiles.parse_profile_text(profiles.profile_label(vm,tm))==(vm,tm)
    assert profiles.normalize_watch_payload({'validation_mode':vm,'trigger_mode':tm})=={'validation_mode':vm,'trigger_mode':tm}

@pytest.mark.parametrize('mode',['REGIME','BREAKER_REGIME','SUPER','BREAKER_SUPER','REGIME_SUPER','BREAKER_REGIME_SUPER'])
@pytest.mark.parametrize('vm',['NORMAL','BLIND'])
def test_twelve_removed_profiles_never_default(vm,mode):
    with pytest.raises(ValueError):
        profiles.normalize_profile(vm,mode)
    for field in ('trigger_mode','oz_mode','trigger_type'):
        with pytest.raises(ValueError):
            profiles.normalize_watch_payload({field:mode})
    with pytest.raises(ValueError):
        create_event_engine(CONFIG,selection=['SPECIAL7'],trigger_overrides={'SPECIAL7':mode})

@pytest.mark.parametrize('text,expected',[
    ('브레이커 올존',('NORMAL','BREAKER')),('무지성 브레이커 올존',('BLIND','BREAKER')),
    ('BREAKER',('NORMAL','BREAKER')),('BLIND_BREAKER',('BLIND','BREAKER')),
    ('BREAKER_BLIND',('BLIND','BREAKER')),('브레이커 무지성 올존',('BLIND','BREAKER'))])
def test_breaker_read_then_canonical_save(text,expected):
    loaded=load_saved_oz(text, kind='text')
    assert profiles.parse_profile_text(loaded)==expected
    output=profiles.normalize_special_settings({'SPECIAL2':{'enabled':True,'trigger':loaded}})
    assert output['SPECIAL2']['trigger']==profiles.profile_label(*expected)
    assert output['SPECIAL2']['trigger'].endswith('브레이커 올존')

@pytest.mark.parametrize('text',['레짐 올존','무지성 레짐 올존','슈퍼 올존','브레이커 레짐 올존','무지성 슈퍼 브레이커 올존','REGIME OZ','???'])
def test_ui_override_invalid_preserved_not_fallback(text):
    original={'SPECIAL2':{'enabled':False,'trigger':text}}
    before=copy.deepcopy(original)
    with pytest.raises(ValueError):profiles.normalize_special_settings(original)
    assert original==before
    errors=profiles.set_special_trigger_overrides({'SPECIAL2':text})
    assert 'SPECIAL2' in errors
    try:
        with pytest.raises(ValueError):profiles.special_final_profile('SPECIAL2','BLIND','OZ')
    finally:profiles.set_special_trigger_overrides({})

@pytest.mark.parametrize('text',['골드 1분 슈퍼 올존 알려줘','나스닥 3분 무지성 레짐 올존 계속','15분 추세 중 1분 브레이커 레짐 올존'])
def test_command_removed_modifier_rejected(text):
    with pytest.raises(ValueError):profiles.text_trigger_mode(text)


def test_other_regime_indicator_not_treated_as_removed_oz_profile():
    assert profiles.text_trigger_mode('레짐밴드 기울기 > 0 조건에서 1분 올존')=='OZ'
    assert profiles.text_trigger_mode('골드 1분 브레이커 올존')=='BREAKER'


def test_watch_restore_quarantines_only_bad_rows_and_saves_originals(caplog):
    clock=SourceClock({});sender=SimpleNamespace(send=lambda *a,**k:True)
    ext=ExternalLiquidityController(clock,sweep_registry={})
    def row(wid,**fields):return dict(watch_id=wid,timeframes=['1m'],symbol='XAUUSD+',**fields)
    items=[row('good',validation_mode='BLIND',trigger_mode='BREAKER'),
           row('bad',validation_mode='NORMAL',trigger_mode='BREAKER_REGIME'),
           row('v1',oz_mode='BLIND_BREAKER'),row('kind',trigger_type='SUPER'),row('plain',trigger_mode='OZ')]
    before=copy.deepcopy(items)
    watch=OZWatchController(sender,ext,clock,initial={'version':3,'watches':items});watch._load_state()
    assert set(watch._watches)=={'good','v1','plain'}
    assert watch._watches['good'].trigger_mode==watch._watches['v1'].trigger_mode=='BREAKER'
    payload=watch.export_payload();assert {x['watch_id'] for x in payload['rejected_watches']}=={'bad','kind'}
    assert payload['rejected_watches'][0]['original']==items[1]
    assert items==before and 'bad' in caplog.text and 'kind' in caplog.text
    again=OZWatchController(sender,ext,clock,initial=payload);again._load_state()
    assert set(again._watches)==set(watch._watches) and len(again.rejected_watches)==2


def data_for(direction='LONG',*,out=True,n=30):
    sign=1 if direction=='LONG' else -1
    v=view(n,hma_6=100+sign)
    if n!=30:
        from event_engine import FeedSnapshot
        from oz_engine.market import OZMarketView
        snap=v.snapshot
        v=OZMarketView(FeedSnapshot(snap.time-(n-30)*60,snap.volume,snap.values,snap.seq,snap.source_epoch,{}),atr_provider=v.atr)
    m=edit(v,open=100+3*sign,price_hma_6=100-2*sign,
           **{family+'_val':100-2*sign for family in ('RSI','STO','DI')}) if out else edit(v)
    # Deliberately adverse/flat regime: the four public profiles must ignore it.
    u=edit(v,price_regime_basis=99,RSI_basis=99,STO_basis=99,DI_basis=99)
    return v,m,u


def observed_fixture(runtime,vm,tm,v,direction='LONG',tf='1m',watch_id='w',*,family_b0=None,hma_b0=None,source=None):
    p=source or runtime.profiles['XAUUSD+',vm,tm]
    t=int(v.time[-3]);b0=99. if direction=='LONG' else 101.
    for family in PERCENTILES:
        p._register_percentile_candidate(tf,direction,family,(family_b0 or {}).get(family,b0),t,t)
    c=p.candidates[tf,direction];c.hma_b0_price=b0 if hma_b0 is None else hma_b0;c.hma_b0_time=t;c.hma_cross_time=t
    p._refresh_true_b0(c)
    p.hma_cross_extremes[tf,direction]=(c.hma_b0_price,t,t)
    p.prev_states[tf]=v.percentile_states()
    p.environment_identities[tf,direction]=p.watch.allowed_environment_identities('XAUUSD+',tf,direction,vm,tm)
    return p


def event_signature(engine,*,notifications=False):
    result=[]
    for sig in engine.signals:
        content=plain(sig.payload).get('content',{})
        if notifications:
            if content.get('type')=='NOTIFICATION' and content.get('kind')=='FINAL_ALERT':result.append((sig.source_time,content))
        elif content.get('family')=='OZ' and content.get('event',{}).get('kind')=='FINAL_ALERT':
            result.append((sig.source_time,content['event']))
    return result

@pytest.mark.parametrize('vm,tm',profiles.PROFILE_KEYS)
@pytest.mark.parametrize('direction',['LONG','SHORT'])
@pytest.mark.parametrize('occurs',[True,False])
def test_four_profiles_positive_negative_live_backtest_same_input(vm,tm,direction,occurs):
    v,m,u=data_for(direction);sign=1 if direction=='LONG' else -1
    outputs=[];states=[]
    for bt in (False,True):
        e=create_event_engine(CONFIG,symbols=('XAUUSD+',),selection=['OZ'],backtest=bt,trigger_overrides={})
        feeds={tf:v.snapshot for tf in ALL_TFS};feeds.update({'3m':m.snapshot,'6m':u.snapshot})
        r=drive(e,feeds,1)
        r.watch._watches['w']=WatchSpec('w','MANUAL',('1m',),'XAUUSD+',direction,True,
                                      request_chat_id='offline',validation_mode=vm,trigger_mode=tm)
        p=observed_fixture(r,vm,tm,v,direction)
        if tm=='BREAKER':
            field='low' if direction=='LONG' else 'high';values=v.column(field).copy()
            values[-1]-=.1*sign if occurs else 0.
            final=edit(v,**{field:values})
        else:
            # Equality contact is sufficient for OZ/B0, but zero trigger hits is not.
            final=edit(v,close=100+.01*sign) if occurs else edit(v,low=99.4,high=100.6,hma_17=95 if sign==1 else 105)
        drive(e,{'1m':final.snapshot},2)
        assert not e.error_log,e.error_log
        events=event_signature(e);notices=event_signature(e,notifications=True)
        assert len(events)==len(notices)==int(occurs),(vm,tm,direction,occurs,events)
        if occurs:
            assert events[0][0]==1790381802000
            assert events[0][1]['event_time']==1790381802.
            assert profiles.profile_label(vm,tm) in notices[0][1]['message']
            assert events[0][1]['trigger_mode']==tm and events[0][1]['direction']==direction
        outputs.append((events,notices));states.append(p.export_event_state())
    assert outputs[0]==outputs[1]
    assert states[0]==states[1]

@pytest.mark.parametrize('tm',['OZ','BREAKER'])
@pytest.mark.parametrize('vm',['NORMAL','BLIND'])
def test_normal_requires_middle_out_while_blind_ignores_upper_frames(vm,tm):
    v,m,u=data_for(out=False);p=profile(vm,tm);seed(p,v)
    low=v.column('low').copy();low[-1]=98.9;v=edit(v,low=low)
    d=p._candidate_completion_decision({'1m':v,'3m':m,'6m':u},'1m','LONG',require_external=False,commit_validation=True)
    assert (d is not None)==(vm=='BLIND')


def test_special7_final_profile_and_trend_contract_no_regime():
    _,_,plugins=load_strategy_inputs({});s=plugins['SPECIAL7']
    assert (s.SETUP_TF,s.FINAL_OZ_TFS if hasattr(s,'FINAL_OZ_TFS') else s.FINAL_TFS)==('15m',('1m',))
    assert (s.FINAL_VALIDATION_MODE,s.FINAL_TRIGGER_MODE)==('NORMAL','BREAKER')
    specs=s._main_7_specs()
    spec=specs[0] if isinstance(specs,list) else next(iter(specs))
    assert '브레이커' in s._main_7_alert_template() and '레짐' not in s._main_7_alert_template()
    # Final completion remains allowed when an old regime filter would reject it.
    v,m,u=data_for();p=profile('NORMAL','BREAKER');seed(p,v)
    low=v.column('low').copy();low[-1]=98.9
    assert float(u.live.get('RSI_regime_slope', 0)) <= 0
    assert p._candidate_completion_decision({'1m':edit(v,low=low),'3m':m,'6m':u},'1m','LONG',require_external=False,commit_validation=True)
    assert str(spec).count('15m')>=1 and 'TREND' in str(spec)


def test_part2_saved_alias_and_removed_setting(tmp_path):
    from event_backtest.settings import scenario
    path=tmp_path/'scenario.json'
    path.write_text(json.dumps({'triggers':{'SPECIAL2':'무지성 브레이커 올존'}}),encoding='utf-8')
    assert scenario(path)['triggers']=={'SPECIAL2':'무지성 브레이커 올존'}
    for value in ('레짐 올존','슈퍼 올존','BREAKER_REGIME'):
        with pytest.raises(ValueError,match='SPECIAL2'):
            scenario(defaults={'triggers':{'SPECIAL2':value}})


def test_part3_current_defaults_generated_code_and_legacy_recipe():
    from lab import catalog
    from lab.compiler import compile_recipe
    for i in range(1,8):
        r=catalog.load(i);catalog.validate(r)
        code=compile_recipe(r,f'Test_SPECIAL{100+i:03}.py');ast.parse(code)
        assert r['config']['FINAL_TRIGGER_MODE'] in ('OZ','BREAKER')
        if i==5:
            assert 'BREAKER_REGIME_SOURCE_TRIGGER_MODE' not in r['config']
            assert r['slots'][0]['params']=={'ema_filter':True}
    r=catalog.new();r['config']['FINAL_TRIGGER_MODE']='BREAKER';r=load_saved_oz(r,kind='recipe');catalog.normalize_profiles(r)
    assert r['config']['FINAL_TRIGGER_MODE']=='BREAKER'
    r['config']['FINAL_TRIGGER_MODE']='BREAKER_REGIME'
    with pytest.raises(ValueError,match='FINAL_TRIGGER_MODE'):catalog.normalize_profiles(r)


def test_breaker_observed_checkpoint_roundtrips_without_losing_candidate_state():
    from durable_protocol import identity
    import monitor_OZ
    p=profile('NORMAL','BREAKER');v=view();seed(p,v)
    state=p.export_event_state()
    legacy='oz_observed_'+identity('XAUUSD+','NORMAL','BREAKER')[:24]+'.json'
    r=OZRuntime(monitor_OZ,CONFIG,{legacy:json.dumps({'version':1,'profile':['NORMAL','BREAKER'],'observed':state})})
    q=r._profile('XAUUSD+','NORMAL','BREAKER')
    assert q.candidates['1m','LONG'].true_b0_price==99.
    assert q.candidates['1m','LONG'].hma_cross_time==int(v.time[-3])
    files=r.export_files();assert q.checkpoint_name == legacy and legacy in files
    assert 'oz_profile_checkpoint_migrations.json' not in files
    assert json.loads(files[q.checkpoint_name])['profile']==['NORMAL','BREAKER']
    clone=copy.deepcopy(r);assert clone.watch is not r.watch
    from oz_engine.checkpoint import decode
    actual=clone._profile('XAUUSD+','NORMAL','BREAKER').export_event_state()
    assert {k:decode(v) for k,v in actual.items()}=={k:decode(v) for k,v in q.export_event_state().items()}


def test_network_guard_is_active():
    with pytest.raises(AssertionError,match='forbidden'):socket.create_connection(('127.0.0.1',9))

@pytest.mark.parametrize('which',['private','fvg','chain_trigger','chain'])
@pytest.mark.parametrize('field',['trigger_mode','oz_mode','trigger_type'])
def test_every_profile_from_json_entry_rejects_removed_before_default(which,field):
    from event_composer_domain import StrategySpec,FVGCreatedWatchSpec
    from watch_orchestrator import ChainTriggerSpec,TimedChainSpec
    parser={'private':StrategySpec,'fvg':FVGCreatedWatchSpec,'chain_trigger':ChainTriggerSpec,'chain':TimedChainSpec}[which]
    with pytest.raises(ValueError):
        parser.from_json({field:'BREAKER_REGIME'})

@pytest.mark.parametrize('vm,tm',profiles.PROFILE_KEYS)
def test_special7_alert_and_name_follow_effective_four_profile_override(vm,tm):
    label=profiles.profile_label(vm,tm)
    _,_,plugins=load_strategy_inputs({'SPECIAL7':label});s=plugins['SPECIAL7']
    assert label in s.STRATEGY_NAME and label in s._main_7_alert_template()
    assert (s.FINAL_VALIDATION_MODE,s.FINAL_TRIGGER_MODE)==(vm,tm)


def test_removed_observed_profile_is_not_silently_restored_as_breaker():
    from durable_protocol import identity
    import monitor_OZ
    key='oz_observed_'+identity('XAUUSD+','NORMAL','BREAKER')[:24]+'.json'
    r=OZRuntime(monitor_OZ,CONFIG,{key:json.dumps({'version':1,'profile':['NORMAL','BREAKER_REGIME'],'observed':{}})})
    assert key not in r.memory
    rejected=json.loads(r.export_files()['oz_rejected_profile_checkpoints.json'])
    assert json.loads(rejected[key]['original'])['profile']==['NORMAL','BREAKER_REGIME']
    p=r._profile('XAUUSD+','NORMAL','BREAKER')
    assert not any(p.candidates.values())


def test_identified_generated_removed_recipes_fail_closed_without_file_writes():
    from lab import storage
    for filename in ('Test_SPECIAL012.py','Test_SPECIAL014.py'):
        path=storage.generated_path(filename);side=path.with_suffix('.recipe.json')
        before=(path.read_bytes(),side.read_bytes())
        with pytest.raises(ValueError):storage.reopen(filename)
        assert before==(path.read_bytes(),side.read_bytes())

@pytest.mark.parametrize('text',['골드 1분 슈퍼 올존 알려줘','골드 1분 브레이커 레짐 올존 알려줘'])
def test_actual_command_engine_rejects_removed_modifier_without_registering(text):
    from event_engine import Kind
    e=create_event_engine(CONFIG,symbols=('XAUUSD+',),selection=['WATCH'],enabled_specials=(),trigger_overrides={})
    e.ingress.post(Kind.COMMAND,source='test',source_seq=1,source_time=1790381800000,
                  payload={'symbol':'XAUUSD+','strategy':'WATCH','chat_id':'offline','text':text})
    e.run()
    states=e.processor_state.get('OZ_STATE',{});r=states.get('runtime')
    assert r is None or not r.watch._watches
    assert not event_signature(e,notifications=True)
    assert any('제거된' in str(s.payload) or '오류' in str(s.payload) for s in e.signals) or e.error_log


def test_manual_generated_breaker_literal_is_saved_canonically():
    from lab.compiler import prepare_manual
    code="FINAL_VALIDATION_MODE='NORMAL'\nFINAL_TRIGGER_MODE='BREAKER'\ndef register(manager):pass\n"
    assert load_saved_oz(code,kind='source') == code
    result=prepare_manual(code,'Test_SPECIAL888.py')
    constants={n.targets[0].id:ast.literal_eval(n.value) for n in ast.parse(result).body if isinstance(n,ast.Assign)}
    assert constants['FINAL_TRIGGER_MODE']=='BREAKER'
    with pytest.raises(ValueError):prepare_manual(code.replace('BREAKER','BREAKER_REGIME'),'Test_SPECIAL888.py')
