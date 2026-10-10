"""Reconstructible closed-bar SWEEP inputs shared across independent specs."""
from copy import deepcopy


class ExternalLevelCache:
    def __init__(self):
        self.entries={};self.computations=0;self.hits=0

    def build(self,compute,symbol,daily,session,h4,h8,*,session_london='',session_newyork=''):
        key=(symbol,session_london,session_newyork)
        signature=[]
        for frame in (daily,session,h4,h8):
            if frame is None:signature.append(None);continue
            signature.append((frame.attrs.get('source_epoch'),
                frame['time'].to_numpy().tobytes(),
                frame[['high','low']].iloc[:-1].to_numpy().tobytes()))
        signature=tuple(signature)
        old=self.entries.get(key)
        if old is None or old[0]!=signature:
            values=compute(daily,session,h4,h8,session_london=session_london,session_newyork=session_newyork)
            self.entries[key]=(signature,deepcopy(values));self.computations+=1
        else:self.hits+=1
        return deepcopy(self.entries[key][1])
