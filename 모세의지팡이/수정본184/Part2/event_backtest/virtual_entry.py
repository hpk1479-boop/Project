"""Common post-alert virtual entries, backed by read-only shared Facts.

Prices are bid OHLC. Buy spread increases entry; sell spread increases the
observed M1 high/low. A simultaneous stop/target touch always loses.
"""
from collections import defaultdict
from pathlib import Path
import csv
import math
from decimal import Decimal, localcontext
from .virtual_contract import normalize_virtual_entry,signal_is_oz,required_timeframes,max_bars,open_condition
from .virtual_facts import VirtualFacts
from .virtual_rules import confirm,environment_holds,filters_pass,open_on_average,stop_price,selected_tf,track_touches

RATIOS=tuple(1+i*.5 for i in range(9))

def finite(value,label):
    try:value=float(value)
    except (TypeError,ValueError):raise ValueError(label+' 값이 없습니다.') from None
    if not math.isfinite(value) or abs(value)>1e100:raise ValueError(label+' 값이 준비되지 않았습니다.')
    return value

def pricing(s,captures,config):
    symbol=s['symbol'];points={}
    for c in captures:
        raw=c.get('point',c.get('symbol_point'))
        if raw is not None:points[finite(raw,'녹화 point')]=True
    if len(points)>1:raise ValueError('녹화 조각의 종목 point 값이 서로 다릅니다.')
    spreads=s.get('spread_points',{})
    spread=finite(spreads.get(symbol,0) if isinstance(spreads,dict) else spreads,'스프레드 포인트')
    if spread<0:raise ValueError('스프레드는 0 이상이어야 합니다.')
    if points:point=next(iter(points));source='capture'
    elif spread==0 and config.get('POINT_'+symbol) in (None,''):
        # point only converts the spread to a price; a zero spread needs none.
        return {'symbol':symbol,'point':None,'point_source':None,'spread_points':spread,'spread_price':0.}
    else:
        point=finite(config.get('POINT_'+symbol),'종목 point ('+symbol+')');source='config'
    if point<=0:raise ValueError('종목 point는 양수여야 합니다.')
    with localcontext() as context:
        context.prec=50
        spread_price=float(Decimal(str(spread))*Decimal(str(point)))
    return {'symbol':symbol,'point':point,'point_source':source,'spread_points':spread,'spread_price':spread_price}

def load_alerts(path):
    rows=[];ignored=0;seen=set()
    with Path(path).open(encoding='utf-8-sig',newline='') as f:
        for row in csv.DictReader(f):
            # Registration/status notices are not market entry instructions.
            if str(row.get('signal_source') or '').upper()=='NOTICE' or not row.get('tf') or row.get('direction') not in ('LONG','SHORT'):
                ignored+=1;continue
            key=(row['signal_id'],row['strategy'],row['symbol'],row['tf'])
            if key in seen:continue
            seen.add(key)
            row=dict(row,time_ms=int(row['time_ms']))
            rows.append(row)
    return sorted(rows,key=lambda r:r['time_ms']),ignored

class VirtualEntry:
    """One signal -> one entry, nine independent exit experiments."""
    def __init__(self,alerts,spread_price=0,config=None,*,facts=None):
        self.alerts=alerts;self.spread=spread_price;self.trades=[];self.waiting=[];self.open=[]
        self.next=0;self.views={};self.last_m1=None
        self.config=normalize_virtual_entry(config);self.facts=facts or VirtualFacts()
        if self.config is None:raise ValueError('가상진입 설정이 없습니다.')

    def _enter(self,trade,stamp,price):
        a=trade['alert'];config=trade['policy']
        if not filters_pass(config,a,stamp,price,self.facts):
            trade['blocked_checks']+=1
            # Only a conditional entry without 이평 시가 위·아래 tries a later candle; the others judge their one candle.
            if config['mode']!='CONFIRM' or open_condition(config) is not None:trade['status']='BLOCKED'
            return False
        long=a['direction']=='LONG'
        # Decimal source prices define exact RR touches (수정본178), without a price tolerance.
        # Compute once per entry, including bid thresholds for short exits; the observation loop
        # still compares floats and never repeatedly adds spread or evaluates Decimal arithmetic.
        with localcontext() as context:
            context.prec=50
            decimal_spread=Decimal(str(self.spread))
            decimal_entry=Decimal(str(price))+(decimal_spread if long else 0)
            entry=float(decimal_entry)
            stop=stop_price(config,a,stamp,entry,self.facts)
            decimal_stop=Decimal(str(stop))
            decimal_risk=decimal_entry-decimal_stop if long else decimal_stop-decimal_entry
            risk=float(decimal_risk)
            trade['stop_price']=stop
            if decimal_risk<=0:
                trade['status']='PASS_RISK'
                return False
            exact={rr:decimal_entry+(1 if long else -1)*Decimal(str(rr))*decimal_risk for rr in RATIOS}
            targets={rr:float(target) for rr,target in exact.items()}
            bid_targets=targets if long else {rr:float(target-decimal_spread) for rr,target in exact.items()}
            bid_stop=stop if long else float(decimal_stop-decimal_spread)
        trade.update(status='ENTERED',entry_time=stamp,entry_price=entry,risk=risk,targets=targets,
                     bid_targets=bid_targets,bid_stop=bid_stop)
        self.open.append(trade)
        return True

    def _signal_price(self,alert):
        value=alert.get('signal_price')
        if value is not None and value!='':return finite(value,'알림 시점 가격')
        # An exact M1 open is observable without inventing an intrabar fill.
        import numpy as np
        from staff_schema import PIPE_VALUE_COLUMNS
        view=self.views.get('1m')
        if view is not None and alert['time_ms']%60000==0:
            index=int(np.searchsorted(view.time,alert['time_ms']//1000,side='left'))
            if index<len(view.time) and int(view.time[index])*1000==alert['time_ms']:
                columns=getattr(view,'columns',PIPE_VALUE_COLUMNS)
                return finite(view.values[index,columns.index('open')],'알림 시점 1분봉 시가')
        raise ValueError('즉시 진입에 필요한 알림 시점 가격이 없습니다: '+alert['signal_id'])

    def observe(self,stamp,feeds,*,end_ms,finalizing=False):
        import numpy as np
        self.views.update(feeds)
        symbol=self.alerts[0]['symbol'] if self.alerts else ''
        self.facts.update(self.views,symbol,stamp)
        while not finalizing and self.next<len(self.alerts) and self.alerts[self.next]['time_ms']<=stamp:
            alert=self.alerts[self.next];self.next+=1
            policy=self.config
            if any(c['kind']=='NECKLINE_BREAK' for c in policy['conditions']):
                if not signal_is_oz(alert):raise ValueError('넥라인 종가 돌파는 올존 신호에만 쓸 수 있습니다.')
                finite(alert.get('neckline_price'),'해당 올존 넥라인')
            trade={'alert':alert,'policy':policy,'status':'WAITING','last_bar':alert['time_ms']//1000,
                   'entry_time':None,'entry_price':None,'stop_price':None,'exits':{},'state':{},
                   'blocked_checks':0,'bars':0}
            self.trades.append(trade)
            if policy['mode']=='IMMEDIATE':
                price=self._signal_price(alert)
                # An environment already broken at the alert passes it.
                if not environment_holds(policy,alert,alert['time_ms'],price,self.facts):trade['status']='PASS_ENV'
                else:self._enter(trade,alert['time_ms'],price)
            else:self.waiting.append(trade)
        for trade in ([] if finalizing else self.waiting[:]):
            a=trade['alert'];policy=trade['policy'];view=self.facts.view(selected_tf(policy['tf'],a))
            first=int(np.searchsorted(view.time,trade['last_bar'],side='right'))
            from staff_schema import PIPE_VALUE_COLUMNS
            columns=getattr(view,'columns',PIPE_VALUE_COLUMNS)
            limit=max_bars(policy);opening=open_condition(policy)
            for i in range(first,len(view.time)):
                bar_ms=int(view.time[i])*1000
                if bar_ms>stamp or bar_ms>=end_ms:break
                trade['last_bar']=int(view.time[i]);trade['bars']+=1
                # The candle closed at bar_ms is the n-th confirmation candle after the alert.
                if limit is not None and trade['bars']>limit:
                    trade['status']='EXPIRED';self.waiting.remove(trade);break
                price=finite(view.values[i,columns.index('open')],'확인봉 다음 시가')
                # A broken environment passes the alert, even when this candle confirms.
                if not environment_holds(policy,a,bar_ms,price,self.facts):
                    trade['status']='PASS_ENV';self.waiting.remove(trade);break
                if opening is not None:
                    # 이평 시가 위·아래: the first candle opening on its average is judged once; before it, the alert waits.
                    track_touches(policy,a,bar_ms,self.facts,trade['state'])
                    within=open_on_average(opening,policy,a,bar_ms,price,self.facts)
                    if within is None:continue
                    self.waiting.remove(trade)
                    if not confirm(policy,a,bar_ms,price,self.facts,trade['state']):trade['status']='PASS_CONDITION'
                    elif not within:trade['status']='PASS_DISTANCE'
                    else:self._enter(trade,bar_ms,price)
                    break
                if not confirm(policy,a,bar_ms,price,self.facts,trade['state']):continue
                if self._enter(trade,bar_ms,price) or trade['status']=='PASS_RISK':
                    self.waiting.remove(trade);break
        m1=self.views.get('1m')
        if m1 is None and self.trades:raise ValueError('손절·익절 판정용 1분봉이 없습니다.')
        if m1 is None:return
        from staff_schema import PIPE_VALUE_COLUMNS
        columns=getattr(m1,'columns',PIPE_VALUE_COLUMNS)
        high=columns.index('high');low=columns.index('low')
        first=0 if self.last_m1 is None else int(np.searchsorted(m1.time,self.last_m1,side='right'))
        for i in range(first,len(m1.time)):
            bar=int(m1.time[i]);bar_ms=bar*1000
            # An unfinished M1 candle never supplies its eventual high/low.
            if bar_ms+60000>stamp or bar_ms>=end_ms:break
            if stamp>=end_ms and (bar_ms+60000>end_ms or i+1>=len(m1.time)
                                  or int(m1.time[i+1])*1000>stamp):break
            hi=finite(m1.values[i,high],'1분봉 고가');lo=finite(m1.values[i,low],'1분봉 저가')
            for trade in self.open[:]:
                if bar_ms+60000<=trade['entry_time']:continue
                long=trade['alert']['direction']=='LONG'
                stop=lo<=trade['bid_stop'] if long else hi>=trade['bid_stop']
                for rr in RATIOS:
                    if rr in trade['exits']:continue
                    target=trade['targets'][rr]
                    won=hi>=trade['bid_targets'][rr] if long else lo<=trade['bid_targets'][rr]
                    if stop or won:
                        if bar_ms<trade['entry_time']:
                            # The first partial minute may contain pre-entry
                            # touches. Each ambiguous ratio is excluded rather
                            # than inventing a win/loss from a later candle.
                            trade['exits'][rr]={'result':'UNCERTAIN','r':None,
                                               'exit_time':bar_ms+60000,'target':target}
                        else:
                            trade['exits'][rr]={'result':'LOSS' if stop else 'WIN','r':-1. if stop else rr,'exit_time':bar_ms,'target':target}
                if len(trade['exits'])==len(RATIOS):
                    self.open.remove(trade)
                    del trade['targets']  # Completed trades no longer need the extra cache.
                    del trade['bid_targets']
            self.last_m1=bar

    def results(self,*,partial=False):
        if not partial and self.next<len(self.alerts):raise ValueError('기간 끝의 알림에 대응하는 녹화 관측이 없습니다.')
        summaries=[];details=[];groups=defaultdict(list)
        for t in self.trades:groups[t['alert']['strategy']].append(t)
        for strategy,trades in sorted(groups.items()):
            # These counts do not depend on the exit ratio; refresh on each results() call.
            entries=[t for t in trades if t['entry_time'] is not None]
            blocked=sum(t['status']=='BLOCKED' for t in trades);risk=sum(t['status']=='PASS_RISK' for t in trades)
            expired=sum(t['status']=='EXPIRED' for t in trades)
            environment=sum(t['status']=='PASS_ENV' for t in trades)
            condition=sum(t['status']=='PASS_CONDITION' for t in trades)
            distance=sum(t['status']=='PASS_DISTANCE' for t in trades)
            waiting=sum(t['status']=='WAITING' for t in trades)
            for rr in RATIOS:
                resolved=[t['exits'][rr] for t in entries if rr in t['exits']]
                closed=[x for x in resolved if x['result'] in ('WIN','LOSS')]
                uncertain=sum(x['result']=='UNCERTAIN' for x in resolved)
                wins=sum(x['result']=='WIN' for x in closed);losses=len(closed)-wins;total=sum(x['r'] for x in closed)
                summaries.append(dict(strategy=strategy,rr=rr,alerts=len(trades),entries=len(entries),
                    passes=blocked+risk+expired+environment+condition+distance,
                    pass_blocked=blocked,pass_risk=risk,expired=expired,pass_env=environment,
                    pass_condition=condition,pass_distance=distance,waiting=waiting,
                    wins=wins,losses=losses,uncertain=uncertain,unclosed=len(entries)-len(resolved),
                    win_rate=wins/len(closed) if closed else None,average_r=total/len(closed) if closed else None,total_r=total))
        for t in self.trades:
            a=t['alert']
            for rr in RATIOS:
                exit=t['exits'].get(rr,{})
                details.append(dict(signal_id=a['signal_id'],strategy=a['strategy'],symbol=a['symbol'],tf=a['tf'],
                    alert_time=a['time_ms'],direction=a['direction'],entry_time=t['entry_time'],entry_price=t['entry_price'],
                    stop_price=t['stop_price'],rr=rr,result=exit.get('result','UNCLOSED' if t['entry_time'] is not None else t['status']),
                    exit_time=exit.get('exit_time'),r=exit.get('r'),target=exit.get('target'),
                    entry_mode=t['policy']['mode'],blocked_checks=t['blocked_checks']))
        return summaries,details

def follow(inputs,captures,warehouse,selected,end,bounds,spread,policy,check,progress,server_time=None):
    """The fills of `inputs` (consecutive alerts) on one shared recording stream.

    Each alert has its own VirtualEntry; the Facts are a read-only cache. Alerts never
    share state, so any consecutive part of the alert list gives its alerts the same fills.
    Returns {position: (trades, observed, last closed M1)} and the reader's counts.
    server_time: the recordings' broker clock; alerts, end and bounds are real time (수정본172).
    """
    from .virtual_source import shared_observations
    from .cancellation import Cancelled
    interrupted=False;finished=0;read_count=0;metrics={}
    currents={};active={};last_stamps={};pending=0;tail_days={}
    def alert_end(ordinal):
        stamp=inputs[ordinal]['time_ms']
        return next((hi for lo,hi in bounds if lo<=stamp<hi),end) if bounds else end
    facts=VirtualFacts()
    def needed_from():
        if active:return inputs[0]['time_ms']
        return inputs[pending]['time_ms'] if pending<len(inputs) else None
    stream=None
    try:
        check()
        if inputs:
            stream=shared_observations(captures,warehouse,selected,inputs[0]['time_ms'],end,
                                       check=check,needed_from=needed_from,metrics=metrics,server_time=server_time,
                                       past_end=True)
            for stamp,feeds in stream:
                check();read_count+=1
                while pending<len(inputs) and inputs[pending]['time_ms']<=stamp:
                    current=VirtualEntry([inputs[pending]],spread,policy,facts=facts)
                    currents[pending]=current;active[pending]=current;pending+=1
                # Scoped to this observation, including repeated timestamps/ROW updates.
                symbol_feeds={}
                resolved_now=False
                for ordinal,current in list(active.items()):
                    check();symbol=inputs[ordinal]['symbol']
                    owned_end=alert_end(ordinal)
                    finalizing=stamp>=owned_end
                    if finalizing:
                        server_stamp=server_time.to_server_ms(stamp) if server_time is not None else stamp
                        day=server_stamp//86_400_000
                        if day!=tail_days.setdefault(ordinal,day):
                            del active[ordinal];finished+=1;resolved_now=True
                            continue
                    if symbol not in symbol_feeds:
                        symbol_feeds[symbol]={tf:v for (sym,tf),v in feeds.items() if sym==symbol}
                    current.observe(stamp,symbol_feeds[symbol],end_ms=owned_end,finalizing=finalizing)
                    last_stamps[ordinal]=stamp
                    # A later timestamp alone cannot finalize stale M1 data (수정본178). Wait for a
                    # successor M1 within the first post-end recorded server day, evaluating exits
                    # only. The next session's prices and confirmation entries remain out of range.
                    m1=current.views.get('1m')
                    finalized=finalizing and m1 is not None and len(m1.time) and owned_end<=int(m1.time[-1])*1000<=stamp
                    if finalized or (current.next==1 and not current.open and (finalizing or not current.waiting)):
                        del active[ordinal];finished+=1;resolved_now=True
                progress(stamp,finished,pending,active,resolved_now)
                if needed_from() is None:break
    except Cancelled:interrupted=True
    finally:
        if stream is not None:stream.close()
    return {'entries':{i:(c.trades,c.next,c.last_m1) for i,c in currents.items()},'last_stamps':last_stamps,
            'finished':finished,'read_count':read_count,'metrics':metrics,'interrupted':interrupted}


def alert_groups(inputs,workers):
    """Consecutive alerts in whole UTC days (the recording's restore unit), about two groups per worker."""
    import bisect
    from .keyframes import DAY_MS
    if int(workers)<=1:return [(0,len(inputs))]
    days=[a['time_ms']//DAY_MS for a in inputs]
    distinct=sorted(set(days))
    count=min(len(distinct),2*int(workers))
    if count<=1:return [(0,len(inputs))]
    cuts=[bisect.bisect_left(days,distinct[i*len(distinct)//count]) for i in range(1,count)]
    edges=[0,*cuts,len(inputs)]
    return [(a,b) for a,b in zip(edges,edges[1:]) if b>a]


_STOP_CHECK_SECONDS=.5
_PROGRESS_SECONDS=.5


def _fraction(inputs,active,stamp,end):
    return sum(min(1.,max(0.,(stamp-inputs[i]['time_ms'])/max(1,end-inputs[i]['time_ms']))) for i in active)


def follow_group(task):
    """One worker's alert group, read from its own first day. Writes only its progress file."""
    import os,sys,time
    from .system import deny_network,restrict_writes,write_progress
    from .cancellation import file_check
    deny_network();sys.dont_write_bytecode=True
    folder=Path(task['out']);restrict_writes(folder)
    stop=file_check(task['stop']);inputs=task['alerts'];end=task['end'];due=[0.,0.]
    def check():
        # A stat per record would cost more than the fills; the stop file is polled.
        now=time.perf_counter()
        if now>=due[0]:due[0]=now+_STOP_CHECK_SECONDS;stop()
    def progress(stamp,finished,pending,active,resolved_now):
        now=time.perf_counter()
        if now<due[1]:return
        due[1]=now+_PROGRESS_SECONDS
        write_progress(folder/'progress.json',{'pid':os.getpid(),'finished':finished,'reached':pending,
                                               'fraction':_fraction(inputs,active,stamp,end)})
    from .recording_time import _rule
    broker=_rule().ServerTime.from_record(task['server_time']) if task.get('server_time') else None
    result=follow(inputs,task['captures'],task['warehouse'],task['selected'],end,task['bounds'],
                  task['spread'],task['policy'],check,progress,server_time=broker)
    return {'first':task['first'],**result}


def _parallel(inputs,groups,workers,out,emit,cancel,began,common):
    """Groups on worker processes; a group's error is raised after all groups end (lowest group first)."""
    import concurrent.futures,json,time
    from .cancellation import requested
    from .runner import _worker_started
    from .worker_schedule import worker_pool,worker_pids,priority_keeper
    folder=Path(out)/'virtual_entry';stop=Path(out)/'stop.request'
    tasks=[]
    for index,(first,last) in enumerate(groups):
        path=folder/f'group_{index:03d}';path.mkdir(parents=True,exist_ok=True)
        tasks.append({**common,'alerts':inputs[first:last],'first':first,'out':str(path),'stop':str(stop)})
    results={};errors={};stopped=False;last_emit=0.
    def poll():
        nonlocal stopped
        if not stopped and (requested(cancel) or stop.exists()):
            stopped=True;stop.touch(exist_ok=True)
            for future in list(pending):
                if future.cancel():pending.pop(future)
    def report(force):
        nonlocal last_emit
        now=time.perf_counter()
        if not force and now-last_emit<.25:return
        finished=reached=0;fraction=0.
        for index,(first,last) in enumerate(groups):
            if index in results:
                finished+=results[index]['finished'];reached+=last-first;continue
            try:row=json.loads((folder/f'group_{index:03d}'/'progress.json').read_text('utf-8'))
            except (OSError,ValueError):continue
            if isinstance(row,dict):
                finished+=row.get('finished',0);reached+=row.get('reached',0);fraction+=row.get('fraction',0.)
        emit('VIRTUAL_ENTRY_PROGRESS',{'processed_alerts':finished,'total_alerts':len(inputs),'active_alert':reached,
            'percent':100*(finished+fraction)/len(inputs),'elapsed_seconds':now-began})
        last_emit=now
    factory=lambda **k:concurrent.futures.ProcessPoolExecutor(initializer=_worker_started,**k)
    keeper=priority_keeper()
    with worker_pool(min(workers,len(tasks)),factory=factory) as pool:
        pending={pool.submit(follow_group,task):index for index,task in enumerate(tasks)}
        poll()
        while pending:
            done,_=concurrent.futures.wait(list(pending),timeout=.5,return_when=concurrent.futures.FIRST_COMPLETED)
            for future in done:
                index=pending.pop(future,None)
                if index is None:continue
                try:results[index]=future.result()
                except Exception as exc:errors[index]=exc
            poll();report(bool(done))
            keeper.update(worker_pids(pool))
    if errors:raise errors[min(errors)]
    # A stop after every group ended interrupts nothing; an unstarted or stopped group does.
    merged={'entries':{},'last_stamps':{},'finished':0,'read_count':0,'metrics':{},'interrupted':False}
    for index in sorted(results):
        row=results[index];first=row['first']
        merged['entries'].update({first+i:v for i,v in row['entries'].items()})
        merged['last_stamps'].update({first+i:v for i,v in row['last_stamps'].items()})
        merged['finished']+=row['finished'];merged['read_count']+=row['read_count']
        merged['interrupted']|=row['interrupted']
        for key,value in row['metrics'].items():merged['metrics'][key]=merged['metrics'].get(key,0)+value
    merged['interrupted']|=len(results)<len(groups)
    return merged


def calculate(alerts_csv,captures,warehouse,s,config,out,*,emit=lambda *a:None,cancel=lambda:None,workers=1,following=()):
    """Fills of every alert. With several workers, groups of consecutive alert days run on worker
    processes; the results are the same as one stream's, merged in alert order.

    following: a recording after the period's end, on the same broker clock. Its first recorded server
    day can finalize the last in-period M1; no later date or new entry is used (수정본178)."""
    from .settings import warehouse_path,milliseconds,relative_path
    from .cancellation import Cancelled,requested
    import time
    inputs,ignored=load_alerts(alerts_csv);price=pricing(s,captures,config)
    policy=normalize_virtual_entry(s.get('virtual_entry'))
    calculator=VirtualEntry(inputs,price['spread_price'],policy)
    emit('VIRTUAL_ENTRY_START',{'alerts':len(inputs)})
    # Alerts are real time (수정본172); the period names the recorded (server) days of the recordings.
    from .recording_time import recording_clock
    broker=recording_clock(captures);real=broker.to_utc_ms
    captures=[*captures,*(c for c in following if recording_clock([c])==broker)]
    end=real(milliseconds(s['end']));began=time.perf_counter();periods=[]
    bounds=[(real(milliseconds(p['start'])),real(milliseconds(p['end']))) for p in s.get('_available_periods',())]
    selected={(a['symbol'],tf) for a in inputs for tf in required_timeframes(policy,a)}
    groups=alert_groups(inputs,workers) if inputs else [(0,0)]
    if len(groups)>1:
        common={'captures':captures,'warehouse':str(warehouse),'selected':selected,'end':end,'bounds':bounds,
                'spread':price['spread_price'],'policy':policy,'server_time':broker.record()}
        run=_parallel(inputs,groups,workers,out,emit,cancel,began,common)
    else:
        last_progress=[0.]
        def check():
            if requested(cancel):raise Cancelled('중단됨(부분 결과)')
        def progress(stamp,finished,pending,active,resolved_now):
            now=time.perf_counter()
            if now-last_progress[0]>=.25 or resolved_now:
                emit('VIRTUAL_ENTRY_PROGRESS',{'processed_alerts':finished,'total_alerts':len(inputs),
                    'active_alert':pending,'time_ms':stamp,'percent':100*(finished+_fraction(inputs,active,stamp,end))/len(inputs),
                    'elapsed_seconds':now-began})
                last_progress[0]=now
        run=follow(inputs,captures,warehouse,selected,end,bounds,price['spread_price'],policy,check,progress,server_time=broker)
    interrupted=run['interrupted'];finished=run['finished'];read_count=run['read_count'];metrics=run['metrics']
    last_stamps=run['last_stamps']
    # Preserve stable alert ordering, independently of completion order.
    for ordinal,(trades,observed,last_m1) in sorted(run['entries'].items()):
        calculator.trades.extend(trades);calculator.next+=observed
        if last_m1 is not None:calculator.last_m1=max(calculator.last_m1 or 0,last_m1)
        if ordinal in last_stamps:
            alert=inputs[ordinal]
            periods.append({'signal_id':alert['signal_id'],'start_ms':alert['time_ms'],'last_observation_ms':last_stamps[ordinal]})
    if not interrupted:
        if calculator.next!=len(inputs):raise ValueError('기간 끝의 알림에 대응하는 녹화 관측이 없습니다.')
        finished=calculator.next
    summary,details=calculator.results(partial=interrupted)
    present={row['strategy'] for row in summary}
    if s['strategies']==['ALL']:
        import sys
        from .settings import PROGRAM
        if str(PROGRAM) not in sys.path:sys.path.insert(0,str(PROGRAM))
        from event_selection import strategy_dependencies
        selected=list(strategy_dependencies())
    else:selected=s['strategies']
    for name in selected:
        if name in present:continue
        for rr in RATIOS:
            summary.append(dict(strategy=name,rr=rr,alerts=0,entries=0,passes=0,pass_blocked=0,pass_risk=0,
                expired=0,pass_env=0,pass_condition=0,pass_distance=0,waiting=0,wins=0,losses=0,uncertain=0,unclosed=0,
                win_rate=None,average_r=None,total_r=0.))
    summary.sort(key=lambda row:(row['strategy'],row['rr']))
    def save(name,rows,empty_fields):
        path=Path(out)/name
        with path.open('w',encoding='utf-8-sig',newline='') as f:
            fields=['run_id',*(list(rows[0]) if rows else empty_fields)]
            writer=csv.DictWriter(f,fieldnames=fields);writer.writeheader()
            for row in rows:writer.writerow({'run_id':Path(out).name,**row})
        return relative_path(warehouse,path)
    result={'cancelled':interrupted,'processed_signals':finished,'observed_signals':calculator.next,
            'processed_periods':periods,'read_bundles':read_count,'reader_metrics':metrics,'alert_groups':len(groups),
            'elapsed_seconds':time.perf_counter()-began,
            'status_label':'중단됨(부분 결과)' if interrupted else '완료','pricing':price,'policy':policy,'summary':summary,'eligible_signals':len(inputs),'non_entry_notices':ignored,
            'last_closed_m1_ms':calculator.last_m1*1000 if calculator.last_m1 is not None else None,
            'summary_csv':save('virtual_summary.csv',summary,['strategy','rr','alerts','entries','passes','wins','losses','unclosed','win_rate','average_r','total_r']),
            'trades_csv':save('virtual_trades.csv',details,['signal_id','strategy','alert_time','direction','entry_time','entry_price','stop_price','rr','result','exit_time','r'])}
    emit('VIRTUAL_ENTRY_COMPLETE',{'eligible_signals':len(inputs),'rows':len(summary),'cancelled':interrupted,'processed_alerts':finished})
    return result
