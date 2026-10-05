from pathlib import Path
import ast
P=Path(__file__).resolve().parents[1]/'Part1/program'
for name in ('THE STAFF OF MOSES.py','monitor_OZ.py'):
 p=P/name;s=p.read_bytes().decode('utf8');lines=s.splitlines(True);tree=ast.parse(s)
 spans=[]
 for node in tree.body:
  if isinstance(node,ast.Expr) and isinstance(node.value,ast.Call):
   call=node.value
   if isinstance(call.func,ast.Attribute) and isinstance(call.func.value,ast.Name) and ((call.func.value.id=='logging' and call.func.attr=='basicConfig') or (call.func.value.id=='LOG_DIR' and call.func.attr=='mkdir')):
    spans.append((node.lineno-1,node.end_lineno))
 for a,b in reversed(spans):lines[a:b]=[]
 s=''.join(lines)
 if name.startswith('THE '):s=s.replace('    stop_event = threading.Event()','    from module_diagnostics import configure\n    configure(Path(__file__).resolve().parent.parent,config)\n    stop_event = threading.Event()',1)
 p.write_bytes(s.encode('utf8'))
p=P/'event_host.py';s=p.read_text('utf8');s=s.replace('    engine=create_live_event_engine(args.program,state_directory=args.state_directory)','    from module_diagnostics import configure,DiagnosticServer\n    from event_composer_domain import load_config\n    diagnostics=configure(args.program.parent,load_config(str(args.program/"config.txt")))\n    engine=create_live_event_engine(args.program,state_directory=args.state_directory)');s=s.replace('    from module_diagnostics import configure,DiagnosticServer\n    diagnostics=configure(args.program.parent,config)\n','');p.write_bytes(s.encode('utf8'))
