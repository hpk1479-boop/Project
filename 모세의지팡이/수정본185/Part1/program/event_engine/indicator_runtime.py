"""Resident TREND consumer computation. Whole history, cached closed prefix."""
import logging, math
import numpy as np
from domain_clock import uuid
from durable_protocol import identity
from indicator_facts import TREND_BASIS, normalize_tf, ArrayFactFrame
from indicator_array_score import score_from_facts
from indicator_score import DEFAULT_TREND_THRESHOLD
from strategy_INDICATOR import METRIC_FACTS, TREND_METRIC_FIELDS, SCORE_FIELDS, REQUIRED_INDS, TREND_METRIC_PUSH_SEC
from .market import select,health,MarketView,timestamp
PROTOCOL_STRATEGY='TREND'

def bar_stamp(view):return timestamp(view.time[-1]).replace(tzinfo=None).isoformat()

class IndicatorRuntime:
    def __init__(self,state,pending):
        self._last_watch_state=state.setdefault('last_watch_state',{})
        self._last_metric_push=state.setdefault('last_metric_push',{})
        self.pending=pending;self.frames={};self.threshold=DEFAULT_TREND_THRESHOLD
    def __deepcopy__(self,memo):
        from copy import deepcopy
        result=type(self).__new__(type(self));memo[id(self)]=result
        for key,value in vars(self).items():
            if key not in ('board','manager'):setattr(result,key,deepcopy(value,memo))
        return result
    def bind(self,board,manager,source_time):self.board=board;self.manager=manager;self.source_time=source_time
    def frame(self,symbol,tf,view):
        key=(symbol,tf);frame=self.frames.get(key)
        atr=self.board.fact('ATR14_GENERAL',symbol,tf) if hasattr(self,'board') else None
        if frame is None:frame=ArrayFactFrame(view,tf,atr);self.frames[key]=frame
        else:frame.update(view,atr)
        return frame
    def trend(self,symbol,tf,view,f):
        if len(view)<3 or not np.isfinite(view.column('close')[-1]):return None
        s20=f['s20'];h50=f['h50'];slope=s20[-1]-s20[-2];hull=h50[-1]-h50[-3]
        if not np.isfinite((slope,hull)).all():return None
        trend,direction=('UP','LONG') if slope>0 and hull>0 else ('DOWN','SHORT') if slope<0 and hull<0 else ('NEUTRAL','NEUTRAL')
        return dict(symbol=symbol,source_tf=tf,trend=trend,direction=direction,trend_basis=TREND_BASIS,
            sma20_slope=float(slope),hma50_slope=float(hull),price=float(view.column('close')[-1]),bar_time=bar_stamp(view))
    def metrics(self,view,f,requested):
        if len(view)<60:return None
        invalid=[name for name in requested if name not in TREND_METRIC_FIELDS]
        if invalid:raise ValueError('unsupported metric fields: '+','.join(invalid))
        values={}
        if set(requested)&SCORE_FIELDS:
            if not np.isfinite(view.column('close')[-1]):return None
            long=score_from_facts(f,'LONG');short=score_from_facts(f,'SHORT')
            values.update(long_score=long,short_score=short,trend_score=max(long,short))
        for name in requested:
            if name in METRIC_FACTS:values[name]=f[METRIC_FACTS[name]][-1]
        if 'hma50_slope' in requested:values['hma50_slope']=f['h50'][-1]-f['h50'][-3]
        return dict(bar_time=bar_stamp(view),metrics={name:float(values[name]) for name in requested
                        if name in values and values[name] is not None and np.isfinite(values[name])})
    def watch(self,symbol,active):
        views={tf:select(self.board,symbol,tf,REQUIRED_INDS) for tf in active}
        if any(view is None for view in views.values()):return
        for tf,fields in active.items():
            view=views[tf];f=self.frame(symbol,tf,view);result=self.trend(symbol,tf,view,f)
            if result is not None:
                result['source_health']=health({tf:view},REQUIRED_INDS)
                self._send_watch_state_if_changed(result)
                if not self.pending.get(identity(symbol,tf),[]):
                    self.manager.send(self.manager.stream.snapshot(symbol,tf,[dict(kind='TREND_STATE',strategy='TREND',**result)],source_health=result['source_health']))
            fields=tuple(fields)
            if not fields:continue
            previous,last=self._last_metric_push.get((symbol,tf),((),0.))
            if fields==previous and self.source_time-last<TREND_METRIC_PUSH_SEC:continue
            snapshot=self.metrics(view,f,fields)
            if snapshot is not None:
                self.manager.send(dict(kind='TREND_METRIC_STATE',strategy='TREND',symbol=symbol,source_tf=tf,requested_fields=list(fields),**snapshot))
                self._last_metric_push[symbol,tf]=(fields,self.source_time)
    def query(self,payload):
        symbol=str(payload.get('symbol') or '').strip();tf=normalize_tf(payload.get('source_tf'))
        if not symbol or not tf:return
        mode=str(payload.get('bar_mode') or 'LIVE').strip().upper()
        view=select(self.board,symbol,tf,REQUIRED_INDS)
        if mode=='CLOSED' and view is not None:view=MarketView(view.snapshot,end=-1) if len(view)>=2 else None
        fields=tuple(dict.fromkeys(str(x or '').strip().lower() for x in payload.get('requested_fields',()) if str(x or '').strip()))
        event=dict(kind='TREND_QUERY_RESULT',strategy='TREND',request_id=payload.get('request_id'),request_chat_id=payload.get('request_chat_id'),purpose=payload.get('purpose'),symbol=symbol,source_tf=tf,bar_mode=mode)
        if fields:event['requested_fields']=list(fields)
        try:
            atr=self.board.fact('ATR14_GENERAL',symbol,tf)[:len(view)] if view is not None else None
            f=ArrayFactFrame(view,tf,atr) if view is not None else None
            value=None if view is None else self.metrics(view,f,fields) if fields else self.trend(symbol,tf,view,f)
            event.update(dict(ok=False,error='trend_data_unavailable') if value is None else dict(ok=True,**value))
        except ValueError as exc:event.update(ok=False,error=str(exc))
        self.manager.send(event)
    def _send_watch_state_if_changed(self, result: dict) -> None:
        key = (result["symbol"], result["source_tf"])
        trend = result["trend"]
        scope = identity(*key)
        pending = self.pending.get(scope, [])
        observed = pending[-1]['trend'] if pending else self._last_watch_state.get(key)
        if observed != trend:
            pending.append({'kind': 'TREND_STATE', 'strategy': PROTOCOL_STRATEGY, **result,
                            'event_id': uuid.uuid4().hex})
            self.manager.stream.prepare(pending[-1])
            self.pending[scope] = pending
        if not pending:
            return
        while pending:
            event = pending[0]
            reply = self.manager.send(event)
            if not reply.get('ok'):
                return
            self._last_watch_state[key] = event['trend']
            pending.pop(0)
            self.pending[scope] = pending
        if logging.getLogger().isEnabledFor(logging.INFO):
            logging.info(
                "📈 [INDICATOR 추세] %s %s | %s | SMA20(시가) 기울기 %+.6g / HMA50 기울기 %+.6g",
                result["symbol"], result["source_tf"], result["trend"],
                result.get("sma20_slope", float("nan")), result.get("hma50_slope", float("nan")),
            )
