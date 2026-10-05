from pathlib import Path
p=Path('수정본20/build/run_event_e3_case.py');s=p.read_text('utf-8').replace("ROOT/'검증결과/event_e3'","ROOT/'검증결과/oz_rewrite'")
s=s.replace("    from event_host import EventHost",'''    processor_times={}
    if a.measure:
        for consumer in (*e.processors,*e.strategies):
            original=consumer.on_event
            def measured(*args,_original=original,_name=consumer.name,**kwargs):
                began=time.perf_counter_ns()
                try:return _original(*args,**kwargs)
                finally:
                    item=processor_times.setdefault(_name,{'ns':0,'calls':0})
                    item['ns']+=time.perf_counter_ns()-began;item['calls']+=1
            consumer.on_event=measured
    from event_host import EventHost''')
s=s.replace("    result.update(case=a.case",'''    if a.measure:
        result['processor_timings']={name:{**item,'ms_per_bundle':item['ns']/1e6/e.metrics.bundle_count}
                                     for name,item in processor_times.items()}
    runtime=e.processor_state['OZ_STATE'].get('runtime')
    result['oz_runtime']={'profiles':len(runtime.profiles),'views_created':runtime.views_created,'tf_evaluations':runtime.evaluations} if runtime else None
    result.update(case=a.case''')
Path('수정본20/build/run_oz_rewrite_case.py').write_text(s,encoding='utf-8')
