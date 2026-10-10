"""FVG-only Wilder seed, closed-bar structure and forming-bar touch Fact."""
import numpy as np
import strategy_FVG as rules

FACT_DEFINITIONS={'FVG_STRUCTURE': {'owner':__name__,'invalidation':'bar_close','inputs':('time','high','low','close')},
                  'FVG_TOUCH': {'owner':__name__,'invalidation':'forming_bar','inputs':('FVG_STRUCTURE','high','low','close')}}


def wilder_atr(high,low,close,period):
    # FVG uses the first-period arithmetic mean; deliberately not ATR14_GENERAL.
    if period<=0:raise ValueError('FVG_ATR_PERIOD는 1 이상이어야 합니다')
    previous=np.r_[np.nan,close[:-1]]
    tr=np.fmax(np.fmax(high-low,np.abs(high-previous)),np.abs(low-previous))
    out=np.full(len(tr),np.nan)
    if len(tr)<period or np.isnan(tr[:period]).any():return out
    out[period-1]=float(tr[:period].mean())
    for i in range(period,len(tr)):
        if np.isfinite(tr[i]) and np.isfinite(out[i-1]):out[i]=(out[i-1]*(period-1)+tr[i])/period
    return out


def structure(symbol,tf,view):
    size=len(view)-1
    if size<3:return None
    high=view.column('high')[:size];low=view.column('low')[:size];close=view.column('close')[:size]
    atr=wilder_atr(high,low,close,int(rules.FVG_ATR_PERIOD))
    first=max(2,size-int(rules.FVG_MAX_AGE_BARS));active=[];filled=[]
    for pos in range(first,size):
        for side,bot,top in (('BULL',high[pos-2],low[pos]),('BEAR',high[pos],low[pos-2])):
            gap=top-bot;base=atr[pos-2]
            if not (gap>0 and gap>=base*rules.FVG_MIN_ATR_RATIO and gap<=base*rules.FVG_MAX_ATR_RATIO):continue
            when=float(view.time[pos]);bot=float(bot);top=float(top)
            zone=dict(symbol=symbol,source_tf=tf,fvg_side=side,direction='LONG' if side=='BULL' else 'SHORT',
                fvg_time=when,zone_bot=bot,zone_top=top,gap=float(gap),age_bars=size-1-pos,
                zone_id=rules.make_zone_id(symbol,tf,side,when,bot,top))
            hits=np.flatnonzero(low[pos+1:]<=bot if side=='BULL' else high[pos+1:]>=top)
            if len(hits):zone['fill_time']=float(view.time[pos+1+int(hits[0])]);filled.append(zone)
            else:active.append(zone)
    filled.sort(key=lambda z:z['fvg_time'],reverse=True)
    return dict(latest_closed_time=float(view.time[-2]),oldest_allowed_time=float(view.time[first]),
                active_candidates=tuple(active),filled_zones=tuple(filled))


class StructureStore:
    def __init__(self):self.entries={};self.computations=0
    def evaluate(self,symbol,tf,view):
        if view is None or len(view)<4:return None
        signature=(view.close_key(),rules.FVG_ATR_PERIOD,rules.FVG_MIN_ATR_RATIO,rules.FVG_MAX_ATR_RATIO,rules.FVG_MAX_AGE_BARS)
        key=(symbol,tf);old=self.entries.get(key)
        if old is None or old[0]!=signature or not view.same_closed_inputs(old[2],('high','low','close')):
            old=(signature,structure(symbol,tf,view),view);self.computations+=1
        else:
            old=(signature,old[1],view)
        self.entries[key]=old
        base=old[1]
        if base is None:return None
        row=view.row(-1);lo,hi,close=(row.get(c) for c in ('low','high','close'));ok=np.isfinite((lo,hi,close)).all()
        tracked=[]
        for side in ('BULL','BEAR'):
            candidates=sorted((z for z in base['active_candidates'] if z['fvg_side']==side),key=lambda z:z['fvg_time'],reverse=True)
            for z in candidates[:int(rules.FVG_MAX_TRACKED_PER_SIDE)]:
                tracked.append(dict(z,touched_now=bool(ok and rules.candle_overlaps_zone(lo,hi,z['zone_bot'],z['zone_top']))))
        tracked.sort(key=lambda z:z['fvg_time'],reverse=True)
        return dict(symbol=symbol,source_tf=tf,latest_closed_time=base['latest_closed_time'],
            oldest_allowed_time=base['oldest_allowed_time'],live_price=float(close) if ok else None,
            live_time=float(view.time[-1]),zones=tracked,filled_zones=base['filled_zones'],
            eligible_zone_ids=[z['zone_id'] for z in base['active_candidates']])
