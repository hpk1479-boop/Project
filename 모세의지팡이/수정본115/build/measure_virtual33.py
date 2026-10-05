from pathlib import Path
import sys, json, importlib.util, time, csv
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'검증결과/stop_virtual_parallel'
sys.path[:0]=[str(ROOT/'Part2'),str(ROOT/'Part1/program')]
from event_backtest.system import deny_network
deny_network()
from event_backtest.runner import runtime_config
from event_backtest.settings import scenario
warehouse=ROOT.parent.with_name(ROOT.parent.name+'_warehouse')
paths=list((warehouse/'captures/XAUUSD+/BAR/2025/09').glob('*/capture.delta2'))
assert len(paths)==1
captures=[{'start':'2025-09-01','end':'2025-10-01','path':paths[0].parent.relative_to(warehouse).as_posix()}]
source=ROOT.parent/'수정본32/검증결과/shared_oz_composer_input/final_SPECIAL1/alerts.csv'
s=scenario(symbol='XAUUSD+',start='2025-09-01',end='2025-10-01',strategies=['SPECIAL1'],overlap_trading_days=0)
which=sys.argv[1]
if which=='before':
    spec=importlib.util.spec_from_file_location('event_backtest.virtual_reference',OUT/'virtual_entry_before.py')
    mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
else:
    from event_backtest import virtual_entry as mod
# Keep output root portable and the CSV run_id equal across both trials.
mod_path=OUT/('virtual_'+which)/'same_run';mod_path.mkdir(parents=True,exist_ok=True)
import event_backtest.settings as settings
relative=settings.relative_path
settings.relative_path=lambda root,p:Path(p).resolve().relative_to(OUT).as_posix()
began=time.perf_counter()
result=mod.calculate(source,captures,warehouse,s,runtime_config(s),mod_path)
result['wall_seconds']=time.perf_counter()-began
(OUT/('virtual_'+which+'.json')).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'phase':which,'seconds':result['wall_seconds'],'signals':result['eligible_signals']},ensure_ascii=False))
