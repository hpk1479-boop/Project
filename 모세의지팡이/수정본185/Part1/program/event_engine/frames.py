"""Publication-scoped preparation; every caller receives an isolated frame."""
from copy import deepcopy
from staff_schema import legacy_frame
from .history import required_rows,ma_source_rows
from watch_ma_features import WatchMAFeatures
from indicator_facts import add_atr14_feature


def available(board,symbol,tf,stale_seconds=30.):
    if (symbol,tf) not in board.feeds:return False
    if hasattr(board,'health') and board.health.get(symbol,{}).get('status') in ('STALE','UNAVAILABLE','RECONNECT'):
        return False
    if hasattr(board,'source_time') and board.source_time is not None:
        return board.source_time-board.observed[(symbol,tf)]<=stale_seconds*1000
    return True


def isolated(frame):
    result=frame.copy(deep=True);result.attrs=deepcopy(frame.attrs)
    return result


def _raw_prototype(board,symbol,tf):
    if not available(board,symbol,tf):return None
    cache=getattr(board,'_frame_cache',None)
    if cache is None:return legacy_frame(symbol,tf,board.snapshot(symbol,tf))
    key=('raw',symbol,tf)
    if key not in cache:cache[key]=legacy_frame(symbol,tf,board.snapshot(symbol,tf))
    return cache[key]


def raw_frame(board,symbol,tf):
    raw=_raw_prototype(board,symbol,tf)
    return None if raw is None else isolated(raw)


class BoardFrames:
    def __init__(self,board,compat,oz,*,sigma=3.0,history=None,role='default'):
        self.board=board;self.compat_module=compat;self.oz=oz;self.sigma=sigma
        self.facts=oz.OZFactMemo();self.compat=compat.StaffCompat(None)
        self.history=history;self._local_cache={};self._local_calculations={}
        self.role=role
        if history is not None:self.compat._watch_ma_features=history.get('features')
        if self.compat._watch_ma_features is None:
            self.compat._watch_ma_features=WatchMAFeatures(source_rows=ma_source_rows)

    def raw(self,symbol,tf):return raw_frame(self.board,symbol,tf)

    def select(self,symbol,timeframes,indicators=None,**extra):
        if indicators is None:raise ValueError('OZ reads Board snapshots; other consumers declare indicators explicitly')
        cache=getattr(self.board,'_frame_cache',self._local_cache)
        calculations=getattr(self.board,'_calculations',self._local_calculations)
        prepared={}
        normalized=None if indicators is None else tuple(sorted(set(str(x).strip().upper() for x in indicators)))
        for tf in timeframes:
            raw=_raw_prototype(self.board,symbol,tf)
            if raw is None:return None
            prep_key=('window',symbol,tf,normalized,self.role,extra.get('watch_ma_history_rows',0))
            if prep_key not in cache:
                self.compat_module.validate_mt5_snapshot(raw,symbol,tf,normalized or ())
                rows=required_rows(raw,tf,normalized,role=self.role)
                rows=min(len(raw),max(rows,int(extra.get('watch_ma_history_rows',0) or 0)+16))
                cache[prep_key]=raw.iloc[-rows:].reset_index(drop=True)
            prepared[tf]=cache[prep_key]
        result={}
        for tf,raw in prepared.items():
            # A stateful Watch's accumulated MA history is an additional input.
            ma=normalized and any(x.startswith(('SMA','WMA','EMA','HMA','SMMA')) and x not in ('EMA','HMA') for x in normalized)
            history_key=id(self.history) if ma and self.history is not None else None
            key=('features',symbol,tf,len(raw),normalized,self.sigma,repr(sorted(extra.items())),history_key)
            if key not in cache:
                atr_key=('atr',symbol,tf,len(raw))
                if atr_key not in cache:cache[atr_key]=add_atr14_feature(raw.copy(deep=True))['atr_14']
                req=dict(symbol=symbol,timeframes=[tf],indicators=list(normalized),**extra)
                bands={name:values[-len(raw):] for name,values in self.board.fact('WONBI_BANDS',symbol,tf).items()}
                value=self.compat._compose(req,symbol,list(normalized),{tf:raw},self.sigma,prepared_atr={tf:cache[atr_key]},prepared_wonbi={tf:bands})[tf]
                if self.history is not None:self.history['features']=self.compat._watch_ma_features
                cache[key]=isolated(value)
            result[tf]=isolated(cache[key])
        return result


class SelectionPort:
    def __init__(self,frames,*,share_within_owner=False):
        self.frames=frames;self.facts=frames.facts
        self.share_within_owner=share_within_owner;self._publication=None;self._owned={}
    def request(self,symbol,timeframes,indicators=None,**extra):
        try:
            if not self.share_within_owner:return self.frames.select(symbol,timeframes,indicators,**extra)
            # The canonical OZ profiles are readers of one processor-owned
            # request. This copy is never the shared publication prototype.
            publication=getattr(self.frames.board,'_frame_cache',None)
            if self._publication is not publication:
                self._owned={};self._publication=publication
            key=(symbol,tuple(timeframes),None if indicators is None else tuple(indicators),repr(sorted(extra.items())))
            if key not in self._owned:self._owned[key]=self.frames.select(symbol,timeframes,indicators,**extra)
            return self._owned[key]
        except (ValueError,RuntimeError):return None


class RawSelectionPort:
    def __init__(self,board):self.board=board
    def request(self,symbol,timeframes,indicators=None,**extra):
        if any(not available(self.board,symbol,tf) for tf in timeframes):return None
        result={}
        for tf in timeframes:
            raw=raw_frame(self.board,symbol,tf)
            result[tf]=raw.tail(required_rows(raw,tf,(),role='fvg')).reset_index(drop=True)
        return result
