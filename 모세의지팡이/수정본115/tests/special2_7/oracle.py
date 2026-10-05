"""Current Part1 method-AST differential. Shared IO adapters are not a LIVE oracle."""
from __future__ import annotations
import ast,copy
from types import SimpleNamespace
from pathlib import Path
from live_replay import runtime as R,clock,storage
from live_replay.reference import composer as C,oz as O,trend as T,sweep as W,fvg as F
ROOT=Path(__file__).resolve().parents[2]
def source_class(filename,name,module,adapter):
    tree=ast.parse((ROOT/'Part1/program'/filename).read_text(encoding='utf-8-sig'))
    node=copy.deepcopy(next(x for x in tree.body if isinstance(x,ast.ClassDef) and x.name==name));members=set(vars(getattr(module,name)))-{'__init__','__new__'}-(set(vars(adapter)) if adapter is not getattr(module,name) else set())
    node.bases=[]
    node.body=[x for x in node.body if (isinstance(x,ast.FunctionDef) and x.name in members) or (isinstance(x,ast.Assign) and any(isinstance(t,ast.Name) and t.id in members for t in x.targets))]
    for x in ast.walk(node):
        if isinstance(x,ast.ImportFrom) and x.module in {'watch_ma','watch_ma_features'}: x.level=1
    ns=dict(vars(module));exec(compile(ast.fix_missing_locations(ast.Module(body=[node],type_ignores=[])),str(ROOT/'Part1/program'/filename),'exec'),ns)
    return type('Original_'+name,(ns[name],adapter),{})
def original_special(number):
    """Execute the current Part1 definitions with only existing clock/disk IO bound."""
    module=getattr(R,f'special{number}')
    source=ast.parse((ROOT/f'Part1/program/SPECIAL/SPECIAL{number}.py').read_text(encoding='utf-8-sig'))
    definitions=[n for n in source.body if isinstance(n,(ast.FunctionDef,ast.ClassDef))]
    ns=dict(vars(module))
    exec(compile(ast.fix_missing_locations(ast.Module(body=definitions,type_ignores=[])),f'Part1/SPECIAL{number}.py','exec'),ns)
    return SimpleNamespace(**ns)

def original_replay(*,specials=(1,),mode='REALTIME'):
    r=R.Replay(specials=specials,mode=mode,synthetic=True)
    with clock.at(0),storage.at(r.state_files):
        original_modules={n:getattr(R,f'special{n}') for n in specials if n>=4}
        try:
            for n in original_modules:setattr(R,f'special{n}',original_special(n))
            r.composer=source_class('manager_KIM.py','ComposerManager',C,R.Composer)(r)
        finally:
            for n,module in original_modules.items():setattr(R,f'special{n}',module)
        r.watch=source_class('monitor_OZ.py','OZWatchController',O,R.Watch)(r.sender)
        r.watch.external=source_class('monitor_OZ.py','ExternalLiquidityController',O,R.External)(r)
        r.oz_worker.controller=r.watch;r.sweep=source_class('strategy_SWEEP.py','SweepEngine',W,R.Sweep)(r)
        mc=source_class('monitor_OZ.py','OZMonitor',O,R.Monitor)
        r.monitors={symbol:mc(r,symbol) for symbol in r.monitors}
        r.profile_monitors={(s,vm,tm):(r.monitors[s] if tm=='BREAKER' and vm=='NORMAL' else mc(r,s,vm,tm)) for s,vm,tm in r.profile_monitors}
        r.generic=source_class('monitor_OZ.py','GenericWatchController',O,R.GenericWatch)(r.sender)
        r.oz_worker.generic=r.generic
        gm=source_class('monitor_OZ.py','GenericConditionMonitor',O,R.GenericMonitor)
        r.generic_monitors={s:gm(r,s) for s in r.monitors}
        r.fvg=source_class('strategy_FVG.py','FVGEngine',F,R.FVG)(r)
        trend_cls=source_class('strategy_INDICATOR.py','IndicatorEngine',T,T.IndicatorEngine);trend=trend_cls.__new__(trend_cls);trend.__dict__.update(r.trend.__dict__);r.trend=trend
    return r
def assert_decision_state_equal(a,e):
    for k in ('_active_children','_last_signatures','trend_facts','wonbi_facts','sweep_touches','fvg_zones','fvg_touches','ma_state_facts','ma_price_state_facts','ma_slope_state_facts','_source_bindings','source_status','_config_chain_state','_config_chain_active'):
        assert getattr(a.composer,k)==getattr(e.composer,k),k
    for name,handler in a.composer._special_watch_handlers.items():
        other=e.composer._special_watch_handlers[name]
        for field in ('active','last_30m_open','cycles','states','_states','last_5m_open'):
            if hasattr(handler,field):assert getattr(handler,field)==getattr(other,field),(name,field)
    assert a.watch._watches==e.watch._watches
    assert a.watch.external._states==e.watch.external._states
    assert a.watch.external._invalidated==e.watch.external._invalidated
    for symbol,m in a.monitors.items():
        for field in O.OZMonitor._checkpoint_fields: assert getattr(m,field)==getattr(e.monitors[symbol],field),(symbol,field)
    for key,m in a.profile_monitors.items():
        for field in O.OZMonitor._checkpoint_fields: assert getattr(m,field)==getattr(e.profile_monitors[key],field),(key,field)
    assert a.delivered==e.delivered;assert a.alerts==e.alerts
