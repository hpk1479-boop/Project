"""Wire v2 publisher for synthetic/replay inputs; no indicator arithmetic."""
import numpy as np
from .capture import wire_schema

_C={name:index for index,name in enumerate(wire_schema().PIPE_VALUE_COLUMNS)}
GROUPS=tuple(tuple(_C[name] for name in names) for names in (
    ('ema_20','ema_50','ema_200'),
    ('price_hma_6','price_band_lower','price_band_upper','hma_6','hma_17','price_regime_basis','price_regime_upper','price_regime_lower'),
    ('hma_6','hma_17','hma_50','hma_168'),
    ('RSI_val','RSI_db','RSI_ub','RSI_basis','RSI_regime_upper','RSI_regime_lower'),
    ('STO_val','STO_db','STO_ub','STO_basis','STO_regime_upper','STO_regime_lower'),
    ('DI_val','DI_db','DI_ub','DI_basis','DI_regime_upper','DI_regime_lower'),
    ('open_band_4_mid','wonbi_upper','wonbi_lower')))

def validity(values):
    row=values[-1]
    return tuple(bool(np.isfinite(row[list(group)]).all() and (np.abs(row[list(group)])<=1e300).all()) for group in GROUPS)

class Publisher:
    def __init__(self,symbol):
        self.symbol=symbol;self.previous={};self.sequences={};self.kinds={1:0,2:0,3:0};self.bytes=0;self.bundles=0

    def frame(self,tf,data):
        w=wire_schema();data=tuple(np.asarray(x) for x in data);old=self.previous.get(tf)
        seq=self.sequences.get(tf,0)+1;self.sequences[tf]=seq
        kind=w.WIRE_FULL
        if old is not None and validity(old[2])==validity(data[2]) and old[0].tobytes()==data[0].tobytes():
            if all(x.tobytes()==y.tobytes() for x,y in zip(old,data)):kind=w.WIRE_HEARTBEAT
            elif all(x[:-1].tobytes()==y[:-1].tobytes() for x,y in zip(old[1:],data[1:])):kind=w.WIRE_ROW
        if kind==w.WIRE_HEARTBEAT:raw=w.pack_v2(self.symbol,tf,seq=seq,kind=kind)
        else:raw=w.pack_v2(self.symbol,tf,*(data if kind==w.WIRE_FULL else tuple(x[-1:] for x in data)),seq=seq,kind=kind)
        self.previous[tf]=tuple(x.copy() for x in data);self.kinds[kind]+=1
        return raw

    def bundle(self,feeds):
        frames=[self.frame(tf,data) for tf,data in feeds]
        raw=wire_schema().pack_bundle(self.symbol,frames)
        self.bytes+=len(raw);self.bundles+=1
        return raw

    def reconnect(self):
        self.previous.clear();self.sequences.clear()
