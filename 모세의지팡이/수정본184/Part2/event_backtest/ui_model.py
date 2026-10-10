"""GUI settings translated to the existing scenario/COMMAND input contract."""
import copy,json,sys
from .settings import ROOT,PROGRAM,scenario,settings

UI_PATH=ROOT/'Part2/backtest_ui.json'

def _presets():
    if str(PROGRAM) not in sys.path:sys.path.insert(0,str(PROGRAM))
    from strategy_recipe.registry import list_presets
    return list_presets('Part2')

def load(path=UI_PATH):
    available=_presets()
    if path.exists():
        value=json.loads(path.read_text('utf-8-sig'))
        value.get('watch',{}).pop('time_filters',None)
        if str(PROGRAM) not in sys.path:sys.path.insert(0,str(PROGRAM))
        from oz_profile_loader import load_saved_oz
        saved={name:row for name,row in value.get('specials',{}).items() if name in available}
        saved=load_saved_oz(saved, kind='specials', path='backtest_ui.specials')
        value['specials']={name:saved.get(name,{'enabled':False,'trigger':None,'time_filters':None})
                           for name in available}
        value['virtual_entry'],value['virtual_entry_target']=_saved_virtual(value)
        value['virtual_entry_strategy']=_saved_strategy(value)
        return value
    return {'target_mode':'SPECIAL','virtual_entry':None,'virtual_entry_target':None,'virtual_entry_strategy':None,
            'specials':{name:{'enabled':False,'trigger':None,'time_filters':None} for name in available},
            'watch':{'text':'','chat_id':'BACKTEST'}}

def _saved_virtual(value):
    """The last executed policy and its target, when it still holds for that target; else the strategy defaults."""
    from .virtual_defaults import restore_recipe_rows, strategy_profile, target_policy
    target,policy=value.get('virtual_entry_target'),value.get('virtual_entry')
    if policy is None or not isinstance(target,str):return None,None
    try:
        policy=restore_recipe_rows(strategy_profile(target),policy)
        return target_policy([target],policy=policy),target
    except (ValueError,KeyError,TypeError):return None,None

def _saved_strategy(value):
    """The last executed strategy-condition edits, for their own target and base frame; else None (the recipe's)."""
    policy,target,intent=value.get('virtual_entry'),value.get('virtual_entry_target'),value.get('virtual_entry_strategy')
    if policy is None or not isinstance(target,str) or intent is None:return None
    from .virtual_defaults import check_strategy,strategy_profile
    try:return check_strategy(strategy_profile(target),policy['tf'],intent)
    except (ValueError,KeyError,TypeError):return None

def _set_strategy(value,intent):
    if intent is None:value.pop('virtual_entry_strategy',None)
    else:value['virtual_entry_strategy']=intent

def save(value,path=UI_PATH):
    value=copy.deepcopy(value);value.get('watch',{}).pop('time_filters',None)
    if str(PROGRAM) not in sys.path:sys.path.insert(0,str(PROGRAM))
    import oz_profiles
    value['specials']=oz_profiles.normalize_special_settings(value.get('specials',{}))
    from .virtual_contract import normalize_virtual_entry
    value['virtual_entry']=normalize_virtual_entry(value.get('virtual_entry'))
    _set_strategy(value,_saved_strategy(value))
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')

def save_virtual_entry(policy,target,path=UI_PATH,*,strategy=None):
    """Persist only the executed virtual policy, its strategy-condition edits (None: the recipe's) and its
    one target, preserving the strategy settings."""
    from .virtual_contract import normalize_virtual_entry
    canonical=normalize_virtual_entry(policy)
    current=json.loads(path.read_text('utf-8-sig')) if path.exists() else load(path)
    current['virtual_entry']=canonical;current['virtual_entry_target']=target
    _set_strategy(current,strategy)
    path.write_text(json.dumps(current,ensure_ascii=False,indent=2),encoding='utf-8')
    return canonical

def make_scenario(ui,common):
    if str(PROGRAM) not in sys.path:sys.path.insert(0,str(PROGRAM))
    from event_selection import strategy_dependencies
    data=dict(common)
    data.update(result_mode=ui.get('result_mode','ALERT_ONLY'),spread_points=dict(ui.get('spread_points',{})))
    data['virtual_entry']=ui.get('virtual_entry')
    data['virtual_strategy']=ui.get('virtual_strategy')
    data['virtual_bases']=list(ui.get('virtual_bases') or [])
    data['trigger_variants']=list(ui.get('trigger_variants') or [])
    if ui['target_mode']=='SPECIAL':
        chosen={name:item for name,item in ui['specials'].items() if item.get('enabled')}
        import oz_profiles
        chosen=oz_profiles.normalize_special_settings(chosen)
        unsupported=set(chosen)-set(strategy_dependencies())
        if unsupported:raise ValueError('기존 실행 로더/의존 선언 미지원: '+', '.join(sorted(unsupported)))
        if not chosen:raise ValueError('전략 설정에서 실행할 전략을 선택하세요.')
        data.update(strategies=list(chosen),triggers={n:i['trigger'] for n,i in chosen.items() if i.get('trigger')},
                    special_time_filters={n:i['time_filters'] for n,i in chosen.items() if i.get('time_filters') is not None},commands=[])
    else:
        watch=ui['watch']
        if not watch.get('text','').strip():raise ValueError('WATCH 명령을 입력하세요.')
        data.update(strategies=['WATCH'],triggers={},special_time_filters={},
                    commands=[{'strategy':'WATCH','text':watch['text'].strip(),'chat_id':watch.get('chat_id') or 'BACKTEST'}])
    data['ui_target_mode']=ui['target_mode']
    config=settings()
    return scenario(defaults={k:config[k] for k in ('cores','capture_start','oz_evaluation','overlap_trading_days')},**data)
