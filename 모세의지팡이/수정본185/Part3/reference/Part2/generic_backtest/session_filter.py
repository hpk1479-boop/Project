"""Half-open editable KST sessions, compiled once into a 1440-minute mask."""
import re
from time import perf_counter
from .contracts import GenericError

VERSION='KST_TRADING_SESSIONS_V1'
MINUTE_NS=60_000_000_000


def default_definition():
    return {'enabled':True,'timezone':'Asia/Seoul','sessions':[
        {'name':'아시아','enabled':True,'start':'09:00','end':'12:00'},
        {'name':'런던','enabled':True,'start':'15:00','end':'18:00'},
        {'name':'뉴욕','enabled':True,'start':'21:00','end':'24:00'}]}


def minute(value):
    if not isinstance(value,str) or re.fullmatch(r'\d{2}:\d{2}',value) is None:
        raise GenericError('E_SESSION_TIME','HH:MM 형식으로 입력하세요: '+str(value))
    h,m=map(int,value.split(':'))
    if not (0<=h<=24 and 0<=m<60 and (h<24 or m==0)):
        raise GenericError('E_SESSION_TIME','00:00 ~ 24:00 범위: '+value)
    return h*60+m


class TradingSessionFilter:
    def __init__(self,definition=None):
        # Old V1 API/config files had no session field: absence means their
        # original 24h replay. New UI jobs always serialize default_definition().
        self.definition={'enabled':False,'timezone':'Asia/Seoul','sessions':[]} if definition is None else definition
        if not isinstance(self.definition,dict) or type(self.definition.get('enabled')) is not bool:
            raise GenericError('E_SESSION_TIME','거래시간 필터 설정')
        if self.definition.get('timezone','Asia/Seoul')!='Asia/Seoul':
            raise GenericError('E_SESSION_TIME','KST 시간만 지원합니다.')
        self.enabled=self.definition['enabled'];mask=[False]*1440
        sessions=self.definition.get('sessions',[])
        if not isinstance(sessions,(tuple,list)):raise GenericError('E_SESSION_TIME','sessions')
        for session in sessions:
            if not isinstance(session,dict) or type(session.get('enabled')) is not bool:
                raise GenericError('E_SESSION_TIME','세션별 ON/OFF')
            if not session['enabled']:continue
            start=minute(session.get('start'));end=minute(session.get('end'))
            if start==1440:start=0
            ranges=[(start,end)] if end>=start else [(start,1440),(0,end)]
            for a,b in ranges:
                for i in range(a,b):mask[i]=True
        self.mask=tuple(mask)
        self.merged=[];begin=None
        for i,allowed in enumerate((*self.mask,False)):
            if allowed and begin is None:begin=i
            elif not allowed and begin is not None:self.merged.append((begin,i));begin=None
        self._minute=None;self._answer=None
        self.stats={'calls':0,'minute_cache_hits':0,'seconds':0.0,'allowed':0,'blocked':0}

    def allows(self,utc_ns):
        start=perf_counter();self.stats['calls']+=1
        try:
            if not self.enabled:return True
            absolute=utc_ns//MINUTE_NS+9*60
            if absolute==self._minute:self.stats['minute_cache_hits']+=1
            else:self._minute=absolute;self._answer=self.mask[absolute%1440]
            self.stats['allowed' if self._answer else 'blocked']+=1
            return self._answer
        finally:self.stats['seconds']+=perf_counter()-start

    def metadata(self):
        return {'version':VERSION,'definition':self.definition,'merged_minute_ranges':self.merged,
            'interval_semantics':'START_INCLUSIVE_END_EXCLUSIVE','overlap_policy':'UNION_ONCE',
            'outside_policy':'ALL_RAW_MARKET_FEATURE_STATE_AND_EXISTING_BARRIERS_CONTINUE',
            'stats':dict(self.stats)}
