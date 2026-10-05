"""The single public backtest entry: propose, approve, build, verify, replay."""
from .warehouse import Warehouse
from .build_plan import make_plan,require_approval
from .history_check import require_complete

def proposal(s,warehouse,*,rebuild=False):
    catalog=Warehouse(warehouse)
    try:return make_plan(s,catalog,rebuild=rebuild)
    finally:catalog.close()

def execute(s,warehouse,*,yes=False,approved_token=None,rebuild=False,profile=None,
            cores=None,sequential=False,emit=lambda *a:None,cancel=lambda:None):
    from .runner import runtime_config,run
    from .recording import prepare
    config=runtime_config(s)
    from event_selection import resolve
    resolve(s['strategies'],config)
    plan=proposal(s,warehouse,rebuild=rebuild);emit('BUILD_PLAN',plan)
    token=plan['approval_token'] if yes else approved_token
    require_approval(plan,token)
    captures=prepare(s,warehouse,profile,plan=plan,approved_token=token,rebuild=rebuild,emit=emit,cancel=cancel)
    require_complete(captures)
    emit('BUILD_VERIFIED',{'pieces':len(captures),'strategies':s['strategies']})
    return run(s,warehouse,cores=cores,sequential=sequential,captures=captures,emit=emit)
