"""Offline diagnosis of the September 12 six-minute shared OZ case."""
from pathlib import Path
import json, runpy, sys

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / '검증결과/shared_oz_composer_input'
strategy = sys.argv[1]
sys.path[:0] = [str(ROOT/'Part1/program'), str(ROOT/'Part2')]
from oz_engine.controllers import ExternalLiquidityController, OZWatchController

handle = (OUT / ('case6m_' + strategy + '.jsonl')).open('w', encoding='utf-8')
last = {}
def write(kind, clock, payload):
    handle.write(json.dumps({'kind': kind, 'time_ms': clock.milliseconds, **payload}, ensure_ascii=False, default=str)+'\n')
    handle.flush()

save = ExternalLiquidityController._save_locked
def trace_save(self):
    result = save(self)
    for key, state in self._states.items():
        spec = self._specs.get(state.watch_id)
        if spec is None or spec.source_tf != '6m':
            continue
        payload = {'spec': vars(spec), 'state': vars(state)}
        signature = json.dumps(payload, sort_keys=True, default=str)
        if last.get(key) != signature:
            write('external_state', self.clock, payload)
            last[key] = signature
    return result
ExternalLiquidityController._save_locked = trace_save

validate = OZWatchController.validate_external_true_b0
def trace_validate(self, symbol, tf, direction, vm, tm, price):
    result = validate(self, symbol, tf, direction, vm, tm, price)
    if tf == '6m' and vm == 'NORMAL' and tm == 'BREAKER':
        write('qualify', self.clock, {'tf': tf, 'direction': direction, 'true_b0': price, 'allowed': result})
    return result
OZWatchController.validate_external_true_b0 = trace_validate

fire = OZWatchController.try_fire
def trace_fire(self, symbol, tf, direction, grade, *args, **kwargs):
    if tf == '6m' and 1757680000000 <= self.clock.milliseconds <= 1757690000000:
        watches=[]
        for watch in self._watches.values():
            if tf not in watch.timeframes:
                continue
            ext_id = self._external_id_for_watch_locked(watch)
            state = self.external.state(ext_id, direction) if ext_id else None
            watches.append({'watch': vars(watch), 'external_state': vars(state) if state else None})
        write('try_fire', self.clock, {'direction': direction, 'grade': grade, 'watches': watches})
    return fire(self, symbol, tf, direction, grade, *args, **kwargs)
OZWatchController.try_fire = trace_fire
try:
    sys.argv = ['validate_shared32.py', 'case_trace', strategy, '--end', '2025-09-13']
    runpy.run_path(str(ROOT/'build/validate_shared32.py'), run_name='__main__')
finally:
    handle.close()
