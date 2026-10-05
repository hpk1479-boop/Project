from pathlib import Path
R=Path(__file__).resolve().parents[1]
def edit(rel,fn):
 p=R/rel;s=p.read_bytes().decode('utf8');n=fn(s);assert n!=s,rel;p.write_bytes(n.encode('utf8'))
def record(s):
 s=s.replace("            for piece in proposed['record']:","            from .terminal_lifecycle import launch_once_retry\n            for piece_index,piece in enumerate(proposed['record'],1):")
 s=s.replace("                launch=native.run_native_tester(","                emit('CAPTURE_START',{**piece,'index':piece_index,'total':len(proposed['record'])+len(proposed.get('convert',()))})\n                launch=launch_once_retry(lambda:native.run_native_tester(",1)
 s=s.replace("'STAFF_TIMER_MS':scenario['timer_ms']})", "'STAFF_TIMER_MS':scenario['timer_ms']}),profile,emit=emit,cancel=cancel)",1)
 s=s.replace("                source=Path(launch['export']);calendar=capture_calendar(source)","                source=Path(launch['export']);calendar=capture_calendar(source)\n                emit('CAPTURE_RECORDED',{**piece,'elapsed_seconds':launch['elapsed_seconds'],'raw_bytes':sum(p.stat().st_size for p in source.glob('pipe_*.bin'))})")
 s=s.replace("cancel=cancel)\r\n                ticks=", "cancel=cancel,emit=emit)\r\n                ticks=")
 s=s.replace("previous['capture_id'],cancel=cancel)","previous['capture_id'],cancel=cancel,emit=emit)")
 return s
edit('Part2/event_backtest/recording.py',record)
def storage(s):
 s=s.replace('def convert(source,parent,key,*,cancel=lambda:None):','def convert(source,parent,key,*,cancel=lambda:None,emit=lambda *a:None):')
 s=s.replace("    verify_indexed(dest/'capture.delta2',expected)","    emit('VERIFY_START',{})\n    verify_indexed(dest/'capture.delta2',expected)")
 return s
edit('Part2/event_backtest/storage.py',storage)
def runner(s):
 s=s.replace("run_id=uuid.uuid4().hex","run_id=s.get('_run_id') or uuid.uuid4().hex",1)
 s=s.replace("    out=root/'runs'/run_id;", "    if len(run_id)!=32 or any(c not in '0123456789abcdef' for c in run_id):raise ValueError('invalid run ID')\n    out=root/'runs'/run_id;",1)
 s=s.replace("    began=time.perf_counter();results=[];", "    emit('RUN_START',{'run_id':run_id})\n    began=time.perf_counter();results=[];",1)
 s=s.replace("data={'result_mode':", "data={'progress_log':relative_path(root,out/'progress.jsonl'),'result_path':relative_path(root,out/'result.json'),'result_mode':",1)
 return s
edit('Part2/event_backtest/runner.py',runner)
