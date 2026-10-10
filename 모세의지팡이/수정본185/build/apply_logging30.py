from pathlib import Path
import ast,hashlib,json
R=Path(__file__).resolve().parents[1];P=R/'Part1/program'
def edit(rel,fn):
 p=R/rel;b=p.read_bytes();s=b.decode('utf-8-sig');n=fn(s);assert n!=s,rel;p.write_bytes(n.encode('utf-8'))
def rep(s,a,b):
 assert a in s,a[:80];return s.replace(a,b,1)
# Observe the previous revision without executing it.
prev=R.with_name('수정본29');manifest={}
for part in ('Part1/program','Part2/event_backtest','Part2/generic_backtest'):
 for p in (prev/part).rglob('*'):
  if p.is_file() and '__pycache__' not in p.parts and p.suffix in ('.py','.txt','.json','.mq5','.mqh'):
   manifest[p.relative_to(prev).as_posix()]=hashlib.sha256(p.read_bytes()).hexdigest()
(R/'검증결과/log_restore/source29_sha256.json').write_text(json.dumps(manifest,indent=2,ensure_ascii=False),encoding='utf-8')
def engine(s):
 s=s.replace('self.failures = {c.name: 0 for c in self.strategies}','self.failures = {c.name: 0 for c in self.strategies + self.processors}')
 s=s.replace('self.signal_sink = None','self.signal_sink = None\n        self.diagnostics = None')
 s=s.replace('self.failures[consumer.name] += 1','self.failures[consumer.name] = self.failures.get(consumer.name, 0) + 1\n        if self.diagnostics is not None:\n            self.diagnostics.failure(consumer.name, event, exc)')
 s=s.replace("if getattr(consumer,'stop_after_errors',True):","if consumer in self.strategies and getattr(consumer,'stop_after_errors',True):")
 start=s.index('        for processor in self.processors:');end=s.index('        for strategy in self.strategies:',start)
 s=s[:start]+'''        for processor in self.processors:
            try:
                if not self._accepts(processor,event):continue
                view = self.board.view(self.processor_state, processor,event.source_time)
                processor.on_event(event, view, self.processor_state[processor.name])
                self.board.processor_changed(processor.name)
            except Exception as exc:
                self._error(processor,event,exc)
            else:
                self.failures[processor.name]=0
                if self.diagnostics is not None:self.diagnostics.success(processor.name,event)
'''+s[end:]
 s=s.replace('                self.failures[strategy.name] = 0','                self.failures[strategy.name] = 0\n                if self.diagnostics is not None:self.diagnostics.success(strategy.name,event)')
 s=s.replace('        self.metrics.received(event)','        self.metrics.received(event)\n        if self.diagnostics is not None:self.diagnostics.touch("ENGINE")')
 return s
edit('Part1/program/event_engine/engine.py',engine)
def host(s):
 s=s.replace('            finally:self.outputs.task_done()','            except Exception:logging.exception("김매니저 출력 처리 오류",extra={"trace_module":"KIM"})\n            finally:self.outputs.task_done()')
 s=s.replace('            finally:self.external_requests.task_done()','            except Exception:logging.exception("외부 응답 처리 오류")\n            finally:self.external_requests.task_done()')
 a="    logging.basicConfig(level=logging.INFO,handlers=[logging.FileHandler(state/'event_host.log',encoding='utf-8'),logging.StreamHandler()],force=True)\n    engine._logger=lambda details:logging.error('STRATEGY_ERROR %s',dict(details))"
 b="    from module_diagnostics import configure,DiagnosticServer\n    diagnostics=configure(args.program.parent,config)\n    engine.diagnostics=diagnostics\n    status_server=DiagnosticServer(diagnostics)"
 s=rep(s,a,b)
 s=s.replace('host.inputs.symbols,host.stop)','host.inputs.symbols,host.stop,diagnostics=diagnostics)')
 s=s.replace('        receiver.close();host.close()','        receiver.close();host.close();status_server.close();diagnostics.close()')
 return s
edit('Part1/program/event_host.py',host)
def pipe(s):
 s=s.replace('def __init__(self,staff,cache,adapter,symbols,stop):','def __init__(self,staff,cache,adapter,symbols,stop,*,diagnostics=None):')
 s=s.replace('        self.last_status={}', '        self.diagnostics=diagnostics\n        self.last_status={}')
 s=s.replace('                if status!=previous:', '                if self.diagnostics is not None:self.diagnostics.health(symbol,status)\n                if status!=previous:')
 s=s.replace('        write(reply)','        write(reply)\n        if self.diagnostics is not None:\n            self.diagnostics.touch("STAFF")\n            self.diagnostics.log("STAFF",logging.INFO,"수신·검증·전광판 전달 완료 · source_time=%s",source_time)')
 s=s.replace("logging.warning('STAFF pipe reconnect: %s',exc)","logging.warning('STAFF pipe reconnect: %s',exc,exc_info=True)")
 return s
edit('Part1/program/event_pipe_host.py',pipe)
def runner(s):
 s=s.replace('logging.disable(logging.CRITICAL)','logging.disable(logging.NOTSET)')
 s=s.replace("    os.environ['MOSES_LOG_DIRECTORY']=str(out/'logs')", "    os.environ['MOSES_LOG_DIRECTORY']=str(out/'logs')\n    from module_diagnostics import configure\n    diagnostics=configure(out,trace=False)")
 s=s.replace('    engine.retain_signals=False','    engine.retain_signals=False\n    engine._logger=lambda details:diagnostics.log(details["strategy"],logging.ERROR,"처리 오류: %s",details,exc_info=sys.exc_info())')
 return s
edit('Part2/event_backtest/runner.py',runner)
def ui(s):
 needle='        self.refresh_btn.pack()'
 return rep(s,needle,needle+'''\n        def open_modules():
            program=BASE_DIR/'program'
            if str(program) not in sys.path:sys.path.insert(0,str(program))
            from module_status_ui import open_window
            open_window(root,BASE_DIR)
        tk.Button(root,text="모듈 상태 / 추적 로그",command=open_modules).pack(pady=8)''')
edit('Part1/OZ_SYSTEM CONTROL.pyw',ui)
def composer(s):
 needle='        if not passed:\r\n            return None'
 if needle not in s:needle=needle.replace('\r\n','\n')
 return rep(s,needle,'''        if logging.getLogger().isEnabledFor(logging.INFO):
            import re as _trace_re
            _trace_match=_trace_re.search(r'SPECIAL[1-7]',spec.spec_id.upper())
            logging.info("조건 판정 · %s %s · %s · 통과=%s · 조건별=%s",spec.spec_id,spec.symbol,direction,passed,
                         [(cond.kind,cond.tf,ok,token) for cond,(ok,token) in zip(spec.conditions,states)],
                         extra={'trace_module':_trace_match[0] if _trace_match else 'ENGINE'})
        if not passed:
            return None''')
edit('Part1/program/event_composer_domain.py',composer)
def fact(s):
 s=s.replace('import numpy as np','import numpy as np\nimport logging')
 s=s.replace('        self.stream.prepare(event)','        self.stream.prepare(event)\n        if logging.getLogger().isEnabledFor(logging.INFO):\n            logging.info("상태 이벤트 · %s",event,extra={"trace_module":self.stream.family})')
 return s
edit('Part1/program/event_engine/domain_support.py',fact)
def output(s):
 s=s.replace('        delivered = []','        if logging.getLogger().isEnabledFor(logging.INFO):logging.info("김매니저 수신 · %s · %s · 수신자=%s",signal_id,symbol,recipients)\n        delivered = []')
 s=s.replace('                delivered.append(self.receipts[key]); continue','                if logging.getLogger().isEnabledFor(logging.INFO):logging.info("김매니저 중복 차단 · %s · %s",signal_id,chat)\n                delivered.append(self.receipts[key]); continue')
 s=s.replace('                        break\r\n','                        if logging.getLogger().isEnabledFor(logging.INFO):logging.info("김매니저 전송 성공 · %s · %s",signal_id,chat)\n                        break\r\n')
 return s
edit('Part1/program/manager_KIM.py',output)
# Guard log-only argument construction, preserving every pre-existing byte outside
# the affected statements (including monitor_OZ mixed line endings).
changed=[]
for p in P.rglob('*.py'):
 if p.name in ('module_diagnostics.py','module_status_ui.py'):continue
 b=p.read_bytes();s=b.decode('utf-8-sig');lines=s.splitlines(keepends=True);nodes=[]
 for node in ast.walk(ast.parse(s)):
  if not isinstance(node,ast.Expr) or not isinstance(node.value,ast.Call):continue
  fn=node.value.func
  if isinstance(fn,ast.Attribute) and isinstance(fn.value,ast.Name) and fn.value.id=='logging' and fn.attr in ('info','debug'):
   # Only standalone statements; inline statements already have explicit guards.
   if lines[node.lineno-1][:node.col_offset].strip():continue
   nodes.append(node)
 for node in sorted(nodes,key=lambda n:n.lineno,reverse=True):
  a=node.lineno-1;z=node.end_lineno;indent=lines[a][:node.col_offset];nl='\r\n' if lines[a].endswith('\r\n') else '\n'
  level=node.value.func.attr.upper()
  lines[a:z]=[indent+f'if logging.getLogger().isEnabledFor(logging.{level}):'+nl]+['    '+line if line.strip() else line for line in lines[a:z]]
 if nodes:
  p.write_bytes(('\ufeff' if b.startswith(b'\xef\xbb\xbf') else '').encode('utf-8')+''.join(lines).encode('utf-8'));changed.append(p.relative_to(R).as_posix())
(R/'검증결과/log_restore/lazy_guards.json').write_text(json.dumps(changed,ensure_ascii=False,indent=2),encoding='utf-8')
print('A connected; lazy files',len(changed))
