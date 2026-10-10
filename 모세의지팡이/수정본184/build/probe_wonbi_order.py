"""Explain the unchanged set iteration used by legacy Wonbi touch identities."""
from pathlib import Path
import argparse,json,logging,os,sys,threading
from types import SimpleNamespace
ROOT=Path(__file__).resolve().parents[1]
def main():
    p=argparse.ArgumentParser();p.add_argument('--before',action='store_true');a=p.parse_args()
    host=ROOT/'검증결과/engine_optimization/before_runtime' if a.before else ROOT
    sys.path.insert(0,str(host/'Part1/program'))
    os.environ['MOSES_LOG_DIRECTORY']=str(ROOT/'검증결과/engine_optimization/order_probe_logs')
    logging.disable(logging.CRITICAL)
    from event_composer_domain import ComposerManager
    import pandas as pd
    frames={tf:pd.DataFrame({'time':[pd.Timestamp('2025-09-01')],'low':[5.], 'high':[25.],
        'wonbi_lower':[10.],'wonbi_upper':[20.]}) for tf in ('1m','2m','3m','5m','1h','2h','4h')}
    request=lambda *a,**k:frames
    state=SimpleNamespace(_lock=threading.RLock(),_wonbi_requirements_locked=lambda:{'XAUUSD+':set(frames)},
        staff=SimpleNamespace(request=request),_condition_market=request,_condition_row=lambda f,i:f.iloc[i],
        _observe_local_source=lambda *a:None,_evaluate_symbol_locked=lambda *a:None,wonbi_facts={},_wonbi_touch_seq=0)
    ComposerManager._update_wonbi(state)
    print(json.dumps({tf:state.wonbi_facts['XAUUSD+',tf,'LOWER']['touch_id'] for tf in frames},sort_keys=True))
if __name__=='__main__':main()
