"""Composer's demanded Snapshot views; no DataFrame preparation or indicators here."""
import numpy as np
import pandas as pd
from event_engine.market import select
from watch_array_facts import WatchMAStore
from watch_ma import parse_ma_name


class ComposerRow:
    native_out = True
    def __init__(self, view, index):self.view=view;self.index=index
    def get(self, name, default=None):
        if name=='time':
            # Preserve the existing timestamp/identity envelope, without making
            # a pandas frame or converting every historical row.
            return pd.Timestamp(int(self.view.time[self.index]),unit='s')
        try:return self.view.column(name)[self.index].item()
        except (KeyError,IndexError):return default


class ComposerView:
    def __init__(self, view, features=None):
        self.view=view;self.time=view.time;self.snapshot=view.snapshot;self.features=features or {}
        self.attrs={'source_epoch':view.snapshot.source_epoch,'indicator_validity':view.snapshot.indicator_validity}
    def __len__(self):return len(self.view)
    @property
    def empty(self):return not len(self)
    def column(self, name):return self.features[name] if name in self.features else self.view.column(name)
    def row(self, index):return ComposerRow(self,index)


def same_storage(a,b):
    return (a is b or a.source_epoch==b.source_epoch and a.indicator_validity==b.indicator_validity
            and all(x.shape==y.shape and np.shares_memory(x,y)
                    for x,y in ((a.time,b.time),(a.volume,b.volume),(a.values,b.values))))


class ComposerInputs:
    def __init__(self):self.previous={};self.ma=WatchMAStore()
    def read(self,kernel,symbol,tfs,indicators,*,ma_names=None):
        if symbol!=kernel.symbol or kernel.board is None:return None
        views={tf:select(kernel.board,symbol,tf,indicators) for tf in tfs}
        if any(view is None for view in views.values()):return None
        result={};group=tuple(sorted(indicators))
        for tf,view in views.items():
            names=tuple(sorted((ma_names or {}).get(tf,())))
            key=(symbol,tf,group,names);prior=self.previous.get(key)
            if prior is not None and same_storage(prior,view.snapshot) and indicators:
                if ma_names is not None:
                    # Heartbeats refresh source liveness, not the MA calculation
                    # or its transition identity. Preserve the existing TTL rule.
                    for store in (kernel.manager.ma_state_facts,kernel.manager.ma_price_state_facts,kernel.manager.ma_slope_state_facts):
                        for fact_key,fact in store.items():
                            if fact_key[:2]==(symbol,tf):fact['observed_at']=kernel.timestamp/1000
                continue
            self.previous[key]=view.snapshot
            features=self.ma.get(symbol,tf,view,names) if names else None
            if features:
                features=dict(features)
                for name in names:
                    family,period=parse_ma_name(name)
                    features[f'{family.lower()}_{period}']=features[name]
            result[tf]=ComposerView(view,features)
        return result
