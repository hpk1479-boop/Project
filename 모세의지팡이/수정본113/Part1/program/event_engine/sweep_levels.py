"""SWEEP external-liquidity Fact, keyed by closed source bars/session inputs."""
from domain_clock import datetime as dt
import numpy as np
from strategy_SWEEP import _hhmm_start, _session_end_hm

FACT_DEFINITIONS={'SWEEP_EXTERNAL_LEVELS': {'owner':__name__, 'invalidation':'bar_close',
                  'inputs':('1d:high/low/time','4h:high/low/time','8h:high/low/time','5m:high/low/time','sessions')}}
datetime=dt.datetime;timezone=dt.timezone
KST=timezone(dt.timedelta(hours=9))


def external_levels(views,london='',newyork=''):
    result=[]
    def pair(view,high_code,low_code,high_name,low_name):
        if view is None or len(view)<2:return
        high,low=view.row(-2).get('high'),view.row(-2).get('low')
        if not np.isfinite((high,low)).all():return
        stamp=datetime.fromtimestamp(int(view.time[-2]),timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
        for code,direction,name,price in ((high_code,'SHORT',high_name,high),(low_code,'LONG',low_name,low)):
            result.append(dict(id=f'{code}:{stamp}',direction=direction,level_code=code,level_name=name,price=price))
    daily=views.get('1d')
    pair(daily,'PDH','PDL','전일고가(PDH)','전일저가(PDL)')
    for tf,label in (('4h','4시간봉'),('8h','8시간봉')):
        pair(views.get(tf),'PREV_'+tf.upper()+'_HIGH','PREV_'+tf.upper()+'_LOW',label+' 이전봉 고가',label+' 이전봉 저가')
    if daily is not None and len(daily)>=3:
        day=int(daily.time[-1])//86400*86400
        weekday=(day//86400+3)%7
        week=day-weekday*86400;previous=week-7*86400
        mask=(daily.time[:-1]>=previous)&(daily.time[:-1]<week)
        if mask.any():
            hi=float(np.nanmax(daily.column('high')[:-1][mask]));lo=float(np.nanmin(daily.column('low')[:-1][mask]))
            if np.isfinite((hi,lo)).all():
                stamp=datetime.fromtimestamp(previous,timezone.utc).isoformat()
                for code,direction,name,price in (('PWH','SHORT','주봉고가',hi),('PWL','LONG','주봉저가',lo)):
                    result.append(dict(id=f'{code}:{stamp}',direction=direction,level_code=code,level_name=name,price=price))
    session=views.get('5m');lh=_hhmm_start(london);nh=_hhmm_start(newyork)
    if session is None or len(session)<3 or lh is None or nh is None:return result
    latest=int(session.time[-2]);local=latest+9*3600;day=local//86400*86400-9*3600
    end=_session_end_hm(newyork);hm=(local%86400//3600,local%3600//60)
    if end and nh>end and hm<end:day-=86400;anchor=day+nh[0]*3600+nh[1]*60;code='NY'
    elif latest>=day+nh[0]*3600+nh[1]*60:anchor=day+nh[0]*3600+nh[1]*60;code='NY'
    elif latest>=day+lh[0]*3600+lh[1]*60:anchor=day+lh[0]*3600+lh[1]*60;code='LONDON'
    else:return result
    mask=(session.time[:-1]>=day)&(session.time[:-1]<anchor)
    if mask.any():
        hi=float(np.nanmax(session.column('high')[:-1][mask]));lo=float(np.nanmin(session.column('low')[:-1][mask]))
        if np.isfinite((hi,lo)).all():
            stamp=datetime.fromtimestamp(anchor,KST).isoformat()
            for side,direction,name,price in (('HIGH','SHORT','고가',hi),('LOW','LONG','저가',lo)):
                result.append(dict(id=f'PREV_SESSION_{side}:{code}:{stamp}',direction=direction,
                                   level_code=f'PREV_SESSION_{side}',level_name='이전 세션 '+name,price=price))
    return result


class LevelStore:
    def __init__(self):self.entries={};self.computations=0
    def get(self,views,london='',newyork='',symbol=''):
        key=(symbol,london,newyork)
        signature=tuple(views[tf].close_key() if views.get(tf) is not None else None for tf in ('1d','4h','8h','5m'))
        old=self.entries.get(key)
        same = old is not None and old[0]==signature and all(
            views[tf].same_closed_inputs(old[2][tf],('high','low'),include_forming_time=tf=='1d')
            for tf in ('1d','4h','8h','5m') if views.get(tf) is not None)
        if not same:
            old=(signature,external_levels(views,london,newyork),dict(views));self.computations+=1
        else:
            old=(signature,old[1],dict(views))
        self.entries[key]=old
        return old[1]
