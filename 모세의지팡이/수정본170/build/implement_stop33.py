from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def edit(name, changes):
    p=ROOT/name;raw=p.read_bytes();text=raw.decode('utf-8-sig').replace('\r\n','\n')
    for a,b in changes:
        if a not in text:raise ValueError((name,a[:90]))
        text=text.replace(a,b)
    p.write_bytes((b'\xef\xbb\xbf' if raw.startswith(b'\xef\xbb\xbf') else b'')+text.replace('\n','\r\n' if b'\r\n' in raw else '\n').encode())

edit('Part2/event_backtest/runner.py',[
('from .build_compat import', 'from .cancellation import requested, file_check, coverage\nfrom .build_compat import'),
("began=time.perf_counter();cpu=time.process_time();count=0;", "stop=file_check(Path(task['out']).parent/'stop.request')\n    interrupted=False;processed_start=None;processed_end=None\n    began=time.perf_counter();cpu=time.process_time();count=0;"),
('for item in select_inputs(inputs,subs,resolution):','for item in select_inputs(inputs,subs,resolution):\n            if requested(stop):interrupted=True;break'),
("if item.source_time<output_start_ms:warmup_bundles+=1", "if item.source_time<output_start_ms:warmup_bundles+=1\n                else:\n                    if processed_start is None:processed_start=item.source_time\n                    processed_end=item.source_time"),
("result={'bundles':count,", "write_progress(out/'progress.json',{'pid':os.getpid(),'bundles':count,'percent':100 if not interrupted else (100*(processed_end-start_ms)/(end_ms-start_ms) if processed_end else 0),'max_memory_bytes':peak_memory()})\n        result={'cancelled':interrupted,'processed_start_ms':processed_start,'processed_end_ms':processed_end,'bundles':count,"),
("transport='replay',emit=lambda *a:None):", "transport='replay',emit=lambda *a:None,cancel=lambda:None):"),
("periods=[(s['start'],s['end'])] if sequential else list(work_periods(s['start'],s['end'],s.get('work_size','MONTH')))", "from .partition import plan_periods\n    periods,partition=([(s['start'],s['end'])],'SEQUENTIAL') if sequential else plan_periods(s['start'],s['end'],workers,s.get('work_size','MONTH'),s.get('adaptive_chunks',True))\n    data['partition']=partition"),
('while pending:\n                done,pending=', "while pending:\n                if requested(cancel):(out/'stop.request').touch(exist_ok=True)\n                done,pending="),
("if s.get('result_mode')=='VIRTUAL_ENTRY':\n            from .virtual_entry import calculate", "interrupted=requested(cancel) or (out/'stop.request').exists() or any(r.get('cancelled') for r in results)\n        data['processed_periods']=coverage(results)\n        if s.get('result_mode')=='VIRTUAL_ENTRY' and not interrupted:\n            from .virtual_entry import calculate"),
("calculate(export,captures,root,s,config,out,emit=emit)", "calculate(export,captures,root,s,config,out,emit=emit,cancel=cancel)\n            interrupted=data['virtual_entry'].get('cancelled',False)"),
("catalog.run(run_id,'COMPLETE',data);catalog.close()", "data.update(status='CANCELLED' if interrupted else 'COMPLETE',status_label='중단됨(부분 결과)' if interrupted else '완료')\n        if interrupted and s.get('result_mode')=='VIRTUAL_ENTRY' and 'virtual_entry' not in data:\n            data['virtual_entry']={'cancelled':True,'not_started':True,'summary':[],'processed_signals':0}\n        catalog.run(run_id,data['status'],data);catalog.close()"),
])
edit('Part2/event_backtest/workflow.py',[
('import json,uuid','import json,uuid\nfrom .cancellation import Cancelled'),
("'status':'FAILED' if error else 'COMPLETE',", "'status':'CANCELLED' if isinstance(error,Cancelled) else 'FAILED' if error else 'COMPLETE',\n              'status_label':'중단됨(부분 결과)' if isinstance(error,Cancelled) else '실패' if error else '완료',\n              'processed_periods':[{'start':r['start'],'end':r['end']} for r in rows if r['status']!='실패'],"),
('except Exception as exc:\n        if s.get', "except Cancelled as exc:\n        return save_build([*plan.get('reuse',[]),*completed],exc)\n    except Exception as exc:\n        if s.get"),
('captures=captures,emit=emit)', 'captures=captures,emit=emit,cancel=cancel)')])
edit('Part2/event_backtest/__main__.py',[
("choices=('MONTH','FORTNIGHT')", "choices=('MONTH','FORTNIGHT','WEEK','DAY')"),
('from .workflow import execute', "from .workflow import execute\n            from .cancellation import file_check"),
('sequential=a.sequential,emit=emit)', "sequential=a.sequential,emit=emit,cancel=file_check(folder/'stop.request'))")])
edit('Part2/event_backtest/settings.py',[
("if size!='FORTNIGHT':raise ValueError('work_size: MONTH / FORTNIGHT')", "if size not in ('FORTNIGHT','WEEK','DAY'):raise ValueError('work_size: MONTH / FORTNIGHT / WEEK / DAY')"),
('cursor+dt.timedelta(days=14)', "cursor+dt.timedelta(days={'FORTNIGHT':14,'WEEK':7,'DAY':1}[size])"),
("if data['work_size'] not in ('MONTH','FORTNIGHT'):raise ValueError('work_size: MONTH / FORTNIGHT')", "if data['work_size'] not in ('MONTH','FORTNIGHT','WEEK','DAY'):raise ValueError('work_size: MONTH / FORTNIGHT / WEEK / DAY')")])
edit('Part2/event_backtest/keyframes.py',[
('def verify_indexed(path,expected,*,progress=None):','def verify_indexed(path,expected,*,progress=None,cancel=lambda:None):'),
('for stamp,raw in read_indexed(path,day_verified=progress):\n        count+=1;', 'for stamp,raw in read_indexed(path,day_verified=progress):\n        cancel()\n        count+=1;')])
edit('Part2/event_backtest/storage.py',[
('def convert(source,parent,key,*,', 'def _convert(source,parent,key,*,'),
('verify_indexed(dest/\'capture.delta2\',expected,','verify_indexed(dest/\'capture.delta2\',expected,cancel=cancel,')])
# Wrapper owns a unique staging parent, so cancellation can only delete this
# attempt. Finished pieces move to their usual location before catalog commit.
p=ROOT/'Part2/event_backtest/storage.py'
with p.open('a',encoding='utf-8',newline='\r\n') as f:f.write('''

def convert(source,parent,key,*,cancel=lambda:None,**kwargs):
    parent=Path(parent).resolve();parent.mkdir(parents=True,exist_ok=True)
    staging=parent/('.partial_'+uuid.uuid4().hex)
    staging.mkdir()
    try:
        dest,metadata=_convert(source,staging,key,cancel=cancel,**kwargs)
        cancel()
        final=parent/dest.name
        dest.rename(final)
        return final,metadata
    finally:
        resolved=staging.resolve()
        if resolved.parent!=parent or not resolved.name.startswith('.partial_'):
            raise ValueError('unsafe capture staging path')
        if resolved.exists():shutil.rmtree(resolved)
''')
edit('Part1/program/event_host.py',[
("token=self.config.get('TELEGRAM_TOKEN','')\n        if not token:\n            if self.diagnostics is not None:self.diagnostics.telegram_state(False)", "token=str(self.config.get('TELEGRAM_TOKEN','')).strip()\n        if not token:\n            if self.diagnostics is not None:self.diagnostics.telegram_unconfigured()\n            else:logging.warning('텔레그램 토큰 미설정',extra={'trace_module':'KIM'})")])
edit('Part1/program/module_diagnostics.py',[
('    def telegram_state(self,connected):', '''    def telegram_unconfigured(self):
        with self.lock:
            if getattr(self,'_token_missing_reported',False):return
            self._token_missing_reported=True
            self.telegram_attempted=False;self.telegram_connected=False
            self.states['KIM']['status']='대기'
        self.log('KIM',logging.WARNING,'텔레그램 토큰 미설정')

    def telegram_state(self,connected):''')])
