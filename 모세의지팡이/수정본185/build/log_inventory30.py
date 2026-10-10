from pathlib import Path
import ast,json
R=Path(__file__).resolve().parents[1];P=R/'Part1/program'
paths={'STAFF':('THE STAFF OF MOSES.py','event_pipe_host.py'),'OZ':('monitor_OZ.py','oz_engine/profile.py'),'SWEEP':('strategy_SWEEP.py','event_engine/sweep_runtime.py'),'FVG':('strategy_FVG.py','event_engine/fvg_runtime.py'),'INDICATOR':('strategy_TREND.py','event_engine/indicator_runtime.py'),'WATCH':('monitor_OZ.py','event_engine/watch_runtime.py'),'KIM':('manager_KIM.py','manager_KIM.py')}
for i in range(1,8):paths['SPECIAL'+str(i)]=(f'SPECIAL/SPECIAL{i}.py',f'SPECIAL/SPECIAL{i}.py')
def templates(p):
 if not p.exists():return []
 result=[]
 for n in ast.walk(ast.parse(p.read_bytes().decode('utf-8-sig'))):
  if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute) and n.func.attr in ('info','debug','warning','error','exception') and isinstance(n.func.value,ast.Name) and n.func.value.id=='logging':
   result.append({'line':n.lineno,'level':n.func.attr,'template':ast.unparse(n.args[0]) if n.args else ''})
 return result
rows={k:{'legacy':a,'current':b,'legacy_logs':templates(R.parent/'레거시/Part1/program'/a),'current_logs':templates(P/b)} for k,(a,b) in paths.items()}
(R/'검증결과/log_restore/log_inventory.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding='utf8')
# Actual modules, same synthetic input via the established offline host; diagnostics
# are read after the run, not by the engine/UI callback.
s=(R/'build/run_engine_behavior.py').read_text('utf8')
s=s.replace("OUT=ROOT/'검증결과/engine_optimization'","OUT=ROOT/'검증결과/log_restore'")
s=s.replace("default='25',choices=('22','25')","default='30',choices=('22','30')").replace("a.revision=='25'","a.revision=='30'")
s=s.replace("    engine.retain_signals=False","    from module_diagnostics import configure\n    diagnostics=configure(out)\n    engine.diagnostics=diagnostics\n    engine.retain_signals=False")
s=s.replace("        (out/'result.json').write_text", "        result['module_logs']={k:len(v) for k,v in diagnostics.buffers.items()}\n        result['module_states']=diagnostics.snapshot()['modules']\n        diagnostics.close()\n        (out/'result.json').write_text")
(R/'build/log_behavior30.py').write_text(s,encoding='utf8')
