"""Lossless bar-observation deltas, not tick-based indicator calculations.

Retained bars are referenced only from this exact stream's previous invocation.
The ordered hash chain rejects missing, swapped, or independently spliced calls.
No OHLC aggregation or calculation rule is performed by this storage codec.
"""
from __future__ import annotations
from .state_codec import encode,decode,digest

VERSION='LIVE_BAR_OBSERVATION_DELTA_V1'
INITIAL=digest({'version':VERSION,'state':'EMPTY'})

class BarObservationEncoder:
    def __init__(self):self.bars=();self.encoded=[];self.chain=INITIAL
    def capture(self,bars):
        bars=tuple(bars);drop=0
        if self.bars and bars and self.bars[0].bar_id!=bars[0].bar_id:
            drop=next((i for i,b in enumerate(self.bars) if b.bar_id==bars[0].bar_id),len(self.bars))
        old_bars=self.bars[drop:];old_encoded=self.encoded[drop:]
        keep=0;encoded=[]
        for i,bar in enumerate(bars):
            # Object identity is safe for the frozen BarState retained by PIT.
            value=old_encoded[i] if i<len(old_bars) and old_bars[i] is bar else encode(bar)
            encoded.append(value)
            if i==keep and i<len(old_encoded) and value==old_encoded[i]:keep+=1
        body={'version':VERSION,'previous':self.chain,'drop':drop,'keep':keep,
              'tail':encoded[keep:],'count':len(encoded)}
        chain=digest(body);self.bars=bars;self.encoded=encoded;self.chain=chain
        return dict(body,chain=chain)

class BarObservationDecoder:
    def __init__(self):self.encoded=[];self.chain=INITIAL
    def restore(self,packet):
        if not isinstance(packet,dict) or set(packet)!={'version','previous','drop','keep','tail','count','chain'}:
            raise ValueError('BAR_OBSERVATION_LAYOUT')
        body={k:v for k,v in packet.items() if k!='chain'}
        if body['version']!=VERSION or body['previous']!=self.chain or digest(body)!=packet['chain']:
            raise ValueError('BAR_OBSERVATION_CHAIN_MISMATCH')
        drop,keep,count=body['drop'],body['keep'],body['count']
        if any(type(n) is not int or n<0 for n in (drop,keep,count)) or drop>len(self.encoded) or keep>len(self.encoded)-drop:
            raise ValueError('BAR_OBSERVATION_BOUNDS')
        if not isinstance(body['tail'],list) or keep+len(body['tail'])!=count:
            raise ValueError('BAR_OBSERVATION_COUNT')
        values=self.encoded[drop:drop+keep]+body['tail']
        bars=tuple(decode(v) for v in values)
        from pit.models import BarState
        if any(type(b) is not BarState for b in bars):raise ValueError('BAR_OBSERVATION_TYPE')
        self.encoded=values;self.chain=packet['chain']
        return bars
