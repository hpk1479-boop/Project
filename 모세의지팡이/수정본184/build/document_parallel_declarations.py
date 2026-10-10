"""Record effective declarations from canonical strategy constants and config."""
from pathlib import Path
import json,sys
ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'검증결과/parallel_oz'
sys.path[:0]=[str(ROOT/'Part1/program'),str(ROOT/'Part2')]
from event_backtest.system import deny_network
deny_network()
from event_backtest.runner import runtime_config
from event_selection import resolve,SPECIAL_DEPENDENCIES
from event_application import load_strategy_inputs
from event_oz_selection import declare
from indicator_facts import tf_seconds
config=runtime_config({'symbol':'XAUUSD+'});_,_,plugins=load_strategy_inputs({})
rows=[]
for name in SPECIAL_DEPENDENCIES:
    dependencies=resolve([name],config);oz=declare(dependencies,plugins)
    rows.append({'strategy':name,'direct':SPECIAL_DEPENDENCIES[name],
        'capabilities':sorted(dependencies.capabilities),'consumers':sorted(dependencies.consumers),'processors':sorted(dependencies.processors),
        'oz_profiles':[{'validation':vm,'trigger':tm,'timeframes':oz.timeframes(vm,tm)} for vm,tm in oz.profiles],
        'oz_input_timeframes':sorted(oz.feeds,key=tf_seconds), 'oz_combinations':len(oz.triples)})
(OUT/'dependency_declarations.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding='utf-8')
audit={
 'sorted': {
   'event_composer_domain.py':['wonbi/percentile symbol and timeframe sets allocate touch sequences','MA symbol/pair/price/slope request sets allocate state IDs','removed subscription IDs cause command emission'],
   'event_selection.py':['dependency closure traversal'],
   'oz_engine/controllers.py':['stale registry IDs','external source watch IDs','profile reset groups','manual/external removal IDs'],
   'monitor_OZ.py':['same controller reference functions, exact byte substitutions preserve mixed CRLF/LF'],
   'SPECIAL/SPECIAL5.py':['child cancellation ID set affects commands']},
 'retained': {
   'event_composer_domain._update_ma_state tf_names':'set comprehension result only; the returned request requirements are sorted before allocating IDs',
   'event_composer_domain._parse_private_strategy_local directional_pairs':'set used to validate conflicting direction count; no emitted event ordering',
   'staff_compat.validate_mt5_snapshot requested':'read-only validation of required column presence; no decision or ID allocation',
   'STAFF.snapshot_reply requested':'requested indicator readiness check; no strategy decision/ID allocation',
   'strategy registration and priority dictionaries':'insertion order is explicitly meaningful and comes from canonical ordered strategy definitions',
   'Fact dependency sets':'independent pure inputs/reductions; no state-changing emission; canonical signal dictionaries are content-normalized'},
 'verification':'first-week ALL in fresh processes with PYTHONHASHSEED 0, 1 and random; compare full normalized signal trace and all alert fields'}
(OUT/'ordering_audit.json').write_text(json.dumps(audit,ensure_ascii=False,indent=2),encoding='utf-8')
print([(r['strategy'],r['oz_combinations']) for r in rows])
