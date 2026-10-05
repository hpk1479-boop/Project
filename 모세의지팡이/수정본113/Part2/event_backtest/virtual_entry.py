"""Common post-alert virtual entries, backed by read-only shared Facts.

Prices are bid OHLC. Buy spread increases entry; sell spread increases the
observed M1 high/low. A simultaneous stop/target touch always loses.
"""
from collections import defaultdict
from pathlib import Path
import csv
import json
import math
from .virtual_contract import normalize_virtual_entry,resolve_virtual_entry,signal_is_oz,required_timeframes
from .virtual_facts import VirtualFacts
from .virtual_rules import confirm,filters_pass,stop_price,selected_tf

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
    if points:point=next(iter(points));source='capture'
    else:
        point=finite(config.get('POINT_'+symbol),'종목 point ('+symbol+')');source='config'
    if point<=0:raise ValueError('종목 point는 양수여야 합니다.')
    spreads=s.get('spread_points',{})
    spread=finite(spreads.get(symbol,0) if isinstance(spreads,dict) else spreads,'스프레드 포인트')
    if spread<0:raise ValueError('스프레드는 0 이상이어야 합니다.')
    return {'symbol':symbol,'point':point,'point_source':source,'spread_points':spread,'spread_price':spread*point}

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

    def _enter(self,trade,stamp,price):
        a=trade['alert'];config=trade['policy']
        if not filters_pass(config,a,stamp,price,self.facts):
            trade['blocked_checks']+=1
            if config['mode']=='IMMEDIATE':trade['status']='BLOCKED'
            return False
        long=a['direction']=='LONG';entry=price+(self.spread if long else 0)
        stop=stop_price(config,a,stamp,entry,self.facts)
        risk=entry-stop if long else stop-entry
        trade['stop_price']=stop
        if risk<=0:
            trade['status']='PASS_RISK'
            return False
        targets={rr:entry+(1 if long else -1)*rr*risk for rr in RATIOS}
        trade.update(status='ENTERED',entry_time=stamp,entry_price=entry,risk=risk,targets=targets)
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

    def _oz_expired(self,trade,stamp):
        validity=trade['validity']
        if validity is None:return False
        import numpy as np
        view=self.facts.view(validity['tf'])
        anchor=validity['bar_time_ms']//1000
        if int(view.time[0])>anchor:
            raise ValueError('올존 원본 유효기간의 기준봉이 녹화에 없습니다.')
        origin=int(np.searchsorted(view.time,anchor,side='left'))
        if origin>=len(view.time) or int(view.time[origin])!=anchor:
            raise ValueError('올존 원본 유효기간의 기준봉이 녹화에 없습니다.')
        first=origin+1
        end=int(np.searchsorted(view.time,stamp//1000,side='right'))
        return max(0,end-first)>validity['remaining_bars']

    def observe(self,stamp,feeds,*,end_ms):
        import numpy as np
        self.views.update(feeds)
        symbol=self.alerts[0]['symbol'] if self.alerts else ''
        self.facts.update(self.views,symbol,stamp)
        while self.next<len(self.alerts) and self.alerts[self.next]['time_ms']<=stamp:
            alert=self.alerts[self.next];self.next+=1
            policy=resolve_virtual_entry(self.config,alert)
            if any(c['kind']=='NECKLINE_BREAK' for c in policy['conditions']):
                if not signal_is_oz(alert):raise ValueError('넥라인 확인 진입은 올존 신호만 지원합니다.')
                finite(alert.get('neckline_price'),'해당 올존 넥라인')
            validity=alert.get('oz_validity')
            if signal_is_oz(alert):
                if isinstance(validity,str):
                    try:validity=json.loads(validity)
                    except ValueError:raise ValueError('올존 원본 유효기간 형식 오류') from None
                if not isinstance(validity,dict) or set(validity)!={'tf','bar_time_ms','remaining_bars'}:
                    raise ValueError('올존 신호의 원본 유효기간 정보가 없습니다: '+alert['signal_id'])
                if type(validity['remaining_bars']) is not int or validity['remaining_bars']<0:
                    raise ValueError('올존 원본 남은 봉 수 형식 오류')
                validity=dict(validity,bar_time_ms=int(finite(validity['bar_time_ms'],'올존 유효기간 기준봉')))
                if validity['bar_time_ms']>alert['time_ms']:
                    raise ValueError('올존 유효기간 기준봉이 알림 시각 이후입니다.')
            else:validity=None
            trade={'alert':alert,'policy':policy,'status':'WAITING','last_bar':alert['time_ms']//1000,
                   'entry_time':None,'entry_price':None,'stop_price':None,'exits':{},'state':{},
                   'blocked_checks':0,'validity':validity}
            self.trades.append(trade)
            if policy['mode']=='IMMEDIATE':
                if self._oz_expired(trade,alert['time_ms']):trade['status']='EXPIRED'
                else:self._enter(trade,alert['time_ms'],self._signal_price(alert))
            else:self.waiting.append(trade)
        for trade in self.waiting[:]:
            a=trade['alert'];policy=trade['policy'];view=self.facts.view(selected_tf(policy['tf'],a))
            first=int(np.searchsorted(view.time,trade['last_bar'],side='right'))
            from staff_schema import PIPE_VALUE_COLUMNS
            columns=getattr(view,'columns',PIPE_VALUE_COLUMNS)
            for i in range(first,len(view.time)):
                bar_ms=int(view.time[i])*1000
                if bar_ms>stamp or bar_ms>=end_ms:break
                trade['last_bar']=int(view.time[i])
                if self._oz_expired(trade,bar_ms):
                    trade['status']='EXPIRED';self.waiting.remove(trade);break
                price=finite(view.values[i,columns.index('open')],'확인봉 다음 시가')
                if not confirm(policy,a,bar_ms,price,self.facts,trade['state']):continue
                if self._enter(trade,bar_ms,price) or trade['status']=='PASS_RISK':
                    self.waiting.remove(trade);break
            if trade in self.waiting and self._oz_expired(trade,stamp):
                trade['status']='EXPIRED';self.waiting.remove(trade)
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
            hi=finite(m1.values[i,high],'1분봉 고가');lo=finite(m1.values[i,low],'1분봉 저가')
            for trade in self.open[:]:
                if bar_ms+60000<=trade['entry_time']:continue
                long=trade['alert']['direction']=='LONG'
                h=hi if long else hi+self.spread;l=lo if long else lo+self.spread
                stop=l<=trade['stop_price'] if long else h>=trade['stop_price']
                for rr in RATIOS:
                    if rr in trade['exits']:continue
                    target=trade['targets'][rr]
                    won=h>=target if long else l<=target
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
            waiting=sum(t['status']=='WAITING' for t in trades)
            for rr in RATIOS:
                resolved=[t['exits'][rr] for t in entries if rr in t['exits']]
                closed=[x for x in resolved if x['result'] in ('WIN','LOSS')]
                uncertain=sum(x['result']=='UNCERTAIN' for x in resolved)
                wins=sum(x['result']=='WIN' for x in closed);losses=len(closed)-wins;total=sum(x['r'] for x in closed)
                summaries.append(dict(strategy=strategy,rr=rr,alerts=len(trades),entries=len(entries),passes=blocked+risk+expired,
                    pass_blocked=blocked,pass_risk=risk,expired=expired,waiting=waiting,
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

def calculate(alerts_csv,captures,warehouse,s,config,out,*,emit=lambda *a:None,cancel=lambda:None):
    from .settings import warehouse_path,milliseconds,relative_path
    from .virtual_source import shared_observations
    from .cancellation import Cancelled,requested
    import time
    inputs,ignored=load_alerts(alerts_csv);price=pricing(s,captures,config)
    policy=normalize_virtual_entry(s.get('virtual_entry'))
    calculator=VirtualEntry(inputs,price['spread_price'],policy)
    emit('VIRTUAL_ENTRY_START',{'alerts':len(inputs)})
    end=milliseconds(s['end']);began=time.perf_counter();last_progress=0.
    interrupted=False;finished=0;read_count=0;periods=[];metrics={}
    currents={};active={};last_stamps={};pending=0
    bounds=[(milliseconds(p['start']),milliseconds(p['end'])) for p in s.get('_available_periods',())]
    def alert_end(ordinal):
        stamp=inputs[ordinal]['time_ms']
        return next((hi for lo,hi in bounds if lo<=stamp<hi),end) if bounds else end
    selected={(a['symbol'],tf) for a in inputs
              for tf in required_timeframes(resolve_virtual_entry(policy,a),a.get('signal_tf') or a['tf'])}
    for a in inputs:
        if signal_is_oz(a):
            validity=a.get('oz_validity')
            if isinstance(validity,str):
                try:validity=json.loads(validity)
                except ValueError:raise ValueError('올존 원본 유효기간 형식 오류') from None
            if isinstance(validity,dict) and validity.get('tf'):selected.add((a['symbol'],validity['tf']))
    facts=VirtualFacts()
    def check():
        if requested(cancel):raise Cancelled('중단됨(부분 결과)')
    def needed_from():
        if active:return inputs[0]['time_ms']
        return inputs[pending]['time_ms'] if pending<len(inputs) else None
    stream=None
    try:
        check()
        if inputs:
            stream=shared_observations(captures,warehouse,selected,inputs[0]['time_ms'],end,
                                       check=check,needed_from=needed_from,metrics=metrics)
            for stamp,feeds in stream:
                check();read_count+=1
                while pending<len(inputs) and inputs[pending]['time_ms']<=stamp:
                    current=VirtualEntry([inputs[pending]],price['spread_price'],policy,facts=facts)
                    currents[pending]=current;active[pending]=current;pending+=1
                # Scoped to this observation, including repeated timestamps/ROW updates.
                symbol_feeds={}
                resolved_now=False
                for ordinal,current in list(active.items()):
                    check();symbol=inputs[ordinal]['symbol']
                    owned_end=alert_end(ordinal)
                    if stamp>=owned_end:
                        del active[ordinal];finished+=1
                        continue
                    if symbol not in symbol_feeds:
                        symbol_feeds[symbol]={tf:v for (sym,tf),v in feeds.items() if sym==symbol}
                    current.observe(stamp,symbol_feeds[symbol],end_ms=owned_end)
                    last_stamps[ordinal]=stamp
                    if current.next==1 and not current.waiting and not current.open:
                        del active[ordinal];finished+=1;resolved_now=True
                now=time.perf_counter()
                if now-last_progress>=.25 or resolved_now:
                    fraction=sum(min(1.,max(0.,(stamp-inputs[i]['time_ms'])/max(1,end-inputs[i]['time_ms']))) for i in active)
                    emit('VIRTUAL_ENTRY_PROGRESS',{'processed_alerts':finished,'total_alerts':len(inputs),
                        'active_alert':pending,'time_ms':stamp,'percent':100*(finished+fraction)/len(inputs),
                        'elapsed_seconds':now-began})
                    last_progress=now
                if needed_from() is None:break
    except Cancelled:interrupted=True
    finally:
        if stream is not None:stream.close()
    # Preserve stable alert ordering, independently of completion order.
    for ordinal,current in sorted(currents.items()):
        calculator.trades.extend(current.trades);calculator.next+=current.next
        if current.last_m1 is not None:calculator.last_m1=max(calculator.last_m1 or 0,current.last_m1)
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
                expired=0,waiting=0,wins=0,losses=0,uncertain=0,unclosed=0,win_rate=None,average_r=None,total_r=0.))
    summary.sort(key=lambda row:(row['strategy'],row['rr']))
    def save(name,rows,empty_fields):
        path=Path(out)/name
        with path.open('w',encoding='utf-8-sig',newline='') as f:
            fields=['run_id',*(list(rows[0]) if rows else empty_fields)]
            writer=csv.DictWriter(f,fieldnames=fields);writer.writeheader()
            for row in rows:writer.writerow({'run_id':Path(out).name,**row})
        return relative_path(warehouse,path)
    result={'cancelled':interrupted,'processed_signals':finished,'observed_signals':calculator.next,
            'processed_periods':periods,'read_bundles':read_count,'reader_metrics':metrics,'elapsed_seconds':time.perf_counter()-began,
            'status_label':'중단됨(부분 결과)' if interrupted else '완료','pricing':price,'policy':policy,'summary':summary,'eligible_signals':len(inputs),'non_entry_notices':ignored,
            'last_closed_m1_ms':calculator.last_m1*1000 if calculator.last_m1 is not None else None,
            'summary_csv':save('virtual_summary.csv',summary,['strategy','rr','alerts','entries','passes','wins','losses','unclosed','win_rate','average_r','total_r']),
            'trades_csv':save('virtual_trades.csv',details,['signal_id','strategy','alert_time','direction','entry_time','entry_price','stop_price','rr','result','exit_time','r'])}
    emit('VIRTUAL_ENTRY_COMPLETE',{'eligible_signals':len(inputs),'rows':len(summary),'cancelled':interrupted,'processed_alerts':finished})
    return result
