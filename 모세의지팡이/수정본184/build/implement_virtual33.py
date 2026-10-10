exec((__import__('pathlib').Path(__file__).with_name('implement_stop33.py')).read_text(encoding='utf-8').split("edit('Part2/event_backtest/runner.py'")[0])
edit('Part2/event_backtest/virtual_entry.py',[
('columns=PIPE_VALUE_COLUMNS','columns=getattr(view,\'columns\',PIPE_VALUE_COLUMNS)'),
("high=PIPE_VALUE_COLUMNS.index('high');low=PIPE_VALUE_COLUMNS.index('low')", "columns=getattr(m1,'columns',PIPE_VALUE_COLUMNS)\n        high=columns.index('high');low=columns.index('low')"),
('def results(self):\n        if self.next<len(self.alerts):','def results(self,*,partial=False):\n        if not partial and self.next<len(self.alerts):'),
('emit=lambda *a:None):','emit=lambda *a:None,cancel=lambda:None):')])
p=ROOT/'Part2/event_backtest/virtual_entry.py';text=p.read_text('utf-8')
start=text.index('    from .bridge import CaptureInputs',text.index('def calculate'))
end=text.index('    present={row',start)
text=text[:start]+'''    from .virtual_source import observations
    from .cancellation import Cancelled,requested
    import time
    inputs,ignored=load_alerts(alerts_csv);price=pricing(s,captures,config)
    calculator=VirtualEntry(inputs,price['spread_price'])
    emit('VIRTUAL_ENTRY_START',{'alerts':len(inputs)})
    end=milliseconds(s['end']);began=time.perf_counter();last_progress=0.
    interrupted=False;finished=0;read_count=0;periods=[]
    for ordinal,alert in enumerate(inputs):
        if requested(cancel):interrupted=True;break
        current=VirtualEntry([alert],price['spread_price']);last_stamp=None
        stream=observations(captures,warehouse,alert['symbol'],alert['tf'],alert['time_ms'],end,check=cancel)
        try:
            for stamp,feeds in stream:
                current.observe(stamp,feeds,end_ms=end);last_stamp=stamp;read_count+=1
                now=time.perf_counter()
                resolved=current.next==1 and not current.waiting and not current.open
                if now-last_progress>=.25 or resolved:
                    fraction=1. if resolved else min(1.,max(0.,(stamp-alert['time_ms'])/max(1,end-alert['time_ms'])))
                    emit('VIRTUAL_ENTRY_PROGRESS',{'processed_alerts':finished,'total_alerts':len(inputs),
                        'active_alert':ordinal+1,'time_ms':stamp,'percent':100*(finished+fraction)/len(inputs),
                        'elapsed_seconds':now-began})
                    last_progress=now
                if resolved:break
        except Cancelled:interrupted=True
        finally:stream.close()
        if not interrupted and current.next!=1:
            raise ValueError('기간 끝의 알림에 대응하는 녹화 관측이 없습니다.')
        calculator.trades.extend(current.trades);calculator.next+=current.next
        if current.last_m1 is not None:calculator.last_m1=max(calculator.last_m1 or 0,current.last_m1)
        if last_stamp is not None:periods.append({'signal_id':alert['signal_id'],'start_ms':alert['time_ms'],'last_observation_ms':last_stamp})
        if interrupted:break
        finished+=1
    summary,details=calculator.results(partial=interrupted)
''' +text[end:]
text=text.replace("result={'pricing':price", "result={'cancelled':interrupted,'processed_signals':finished,'observed_signals':calculator.next,\n            'processed_periods':periods,'read_bundles':read_count,'elapsed_seconds':time.perf_counter()-began,\n            'status_label':'중단됨(부분 결과)' if interrupted else '완료','pricing':price")
text=text.replace("emit('VIRTUAL_ENTRY_COMPLETE',{'eligible_signals':len(inputs),'rows':len(summary)})", "emit('VIRTUAL_ENTRY_COMPLETE',{'eligible_signals':len(inputs),'rows':len(summary),'cancelled':interrupted,'processed_alerts':finished})")
p.write_bytes(text.replace('\n','\r\n').encode())
