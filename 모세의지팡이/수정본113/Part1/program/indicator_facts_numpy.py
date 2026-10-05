"""NumPy Fact evaluation with a sealed closed prefix and one replaceable row.

The formula inventory is indicator_facts.FACTS; this is its array backend.
No strategy decisions or I/O. Recurrent hidden states are indexed by bar so a
forming-row correction always starts from the SAME closed state, not the last tick.
"""
import math
from domain_clock import datetime as dt
import numpy as np

NAN=float('nan')


def divide(a,b):
    return NAN if b==0 else a/b


def rolling(values,n,fn):
    return float(fn(values[-n:])) if len(values)>=n and np.isfinite(values[-n:]).all() else NAN


def ma_array(values,family,n):
    """Shared variable-period MA Fact; canonical OPEN except EMA=CLOSE."""
    n=int(n);v=np.asarray(values,dtype=float);out=np.full(len(v),np.nan)
    if family=='EMA':
        from indicator_facts import rma_array
        return rma_array(v,(n+1)/2) if n==1 else _ewm_array(v,2/(n+1),n)
    if family=='HMA':
        return ma_array(2*ma_array(v,'WMA',max(1,n//2))-ma_array(v,'WMA',n),'WMA',max(1,int(math.sqrt(n))))
    weights=np.arange(1,n+1,dtype=float)
    for i in range(n-1,len(v)):
        w=v[i+1-n:i+1]
        if np.isfinite(w).all():out[i]=np.mean(w) if family=='SMA' else float(np.dot(w,weights)/weights.sum())
    return out


def ewm_step(current,previous,alpha,min_periods):
    weighted,old_weight,count=previous
    observed=not np.isnan(current);count+=int(observed)
    if not np.isnan(weighted):
        old_weight*=1-alpha
        if observed:
            if weighted!=current:weighted=(old_weight*weighted+alpha*current)/(old_weight+alpha)
            old_weight=1.
    elif observed:weighted=current
    return (weighted,old_weight,count),weighted if count>=min_periods else NAN


def _ewm_array(values,alpha,min_periods):
    out=np.full(len(values),np.nan);state=(NAN,1.,0)
    for i,v in enumerate(values):state,out[i]=ewm_step(v,state,alpha,min_periods)
    return out


class ArrayFactFrame:
    """Lazy named Facts. Rebuild closed values once per bar/epoch, never per tick."""
    def __init__(self,view,tf,atr=None):
        self.tf=tf;self.key=None;self.rebuilds=0;self.updates=0;self.update(view,atr)
    def __len__(self):return len(self.view)
    def update(self,view,atr=None):
        key=view.close_key()
        corrected=(key==self.key and any(not np.array_equal(
            self.view.column(name)[:-1].view('<u8'),view.column(name)[:-1].view('<u8'))
            for name in ('open','high','low','close','volume','hma_50','open_band_4_mid','wonbi_upper')))
        self.view=view;self.provided_atr=atr
        if key!=self.key or corrected:
            self.key=key;self.values={};self.states={};self.rebuilds+=1
        self.ready=set();self.updates+=1
    def get(self,name):
        if name in self.ready:return self.values[name]
        if name=='ATR14_GENERAL' and self.provided_atr is not None:
            self.values[name]=self.provided_atr;self.ready.add(name);return self.provided_atr
        raw={'o':'open','h':'high','l':'low','c':'close','h50':'hma_50','vraw':'volume'}
        if name in raw:
            self.values[name]=self.view.column(raw[name]);self.ready.add(name);return self.values[name]
        start=len(self)-1 if name in self.values else 0
        out=self.values.setdefault(name,np.full(len(self),np.nan))
        self.states.setdefault(name,[None]*len(self))
        for i in range(start,len(self)):out[i]=self._step(name,i)
        self.ready.add(name);return out
    __getitem__=get
    def _ewm(self,name,i,value,n,alpha=None):
        if alpha is None:alpha=1./(1.+((1.-1./n)/(1./n)))
        previous=self.states[name][i-1] if i else (NAN,1.,0)
        state,value=ewm_step(value,previous,alpha,n)
        self.states[name][i]=state;return value
    def _step(self,name,i):
        def a(k):return self.get(k)
        def v(k,lag=0):return a(k)[i-lag] if i>=lag else NAN
        def w(k,n):return a(k)[max(0,i-n+1):i+1]
        def roll(k,n,fn=np.mean):return rolling(w(k,n),n,fn)
        if name=='candle_direction':
            from indicator_facts import candle_direction_values
            return candle_direction_values(v('o'),v('c'))
        if name=='v':return 0. if np.isnan(v('vraw')) else v('vraw')
        if name=='tr':return np.fmax(np.fmax(v('h')-v('l'),abs(v('h')-v('c',1))),abs(v('l')-v('c',1)))
        ewm={'e10':('o',10),'e50':('o',50),'e12':('c',12),'e26':('c',26),'e13':('c',13),
             'sig':('macd',9),'vol_ema':('v',20),'mid_ema':('mid',10)}
        if name in ewm:
            dep,n=ewm[name];return self._ewm(name,i,v(dep),n,2/(n+1))
        rmas={'ATR14_GENERAL':('tr',14),'atr10':('tr',10),'pdm_rma':('pdm',14),
              'mdm_rma':('mdm',14),'adx':('dx',14),'up_rma':('up',14),'dn_rma':('dn',14)}
        if name in rmas:
            dep,n=rmas[name];return self._ewm(name,i,v(dep),n)
        if name=='s20':return roll('o',20)
        if name=='w17':return roll('o',17,lambda x:np.dot(x,np.arange(1,18))/153.)
        if name=='std':
            from indicator_facts import wonbi_standard_deviation
            return wonbi_standard_deviation(self.view.column('open_band_4_mid')[i],self.view.column('wonbi_upper')[i])
        if name=='width':return 6*v('std')
        if name=='safe':return v('width')>roll('width',8)
        if name=='mid':return (v('h')+v('l'))/2
        if name in ('pdm','mdm'):
            up=v('h')-v('h',1);down=v('l',1)-v('l')
            return (up if up>down and up>0 else 0.) if name=='pdm' else (down if down>up and down>0 else 0.)
        if name in ('plus','minus'):return divide(100*v('pdm_rma' if name=='plus' else 'mdm_rma'),v('ATR14_GENERAL'))
        if name=='dx':return divide(100*abs(v('plus')-v('minus')),v('plus')+v('minus'))
        if name=='up':return max(v('c')-v('c',1),0.)
        if name=='dn':return max(v('c',1)-v('c'),0.)
        if name=='rv':
            up,dn=v('up_rma'),v('dn_rma')
            ratio=divide(up,dn) if dn else (np.inf if up>0 else NAN)
            return 100-100/(1+ratio)
        if name=='tp':return (v('h')+v('l')+v('c'))/3
        if name=='cv':return divide(v('tp')-roll('tp',20),.015*roll('tp',20,lambda x:np.mean(np.abs(x-np.mean(x)))))
        if name=='lr':return roll('c',20,lambda y:np.polyfit(np.arange(20,dtype=float),y,1)[0]*19+np.polyfit(np.arange(20,dtype=float),y,1)[1])
        if name=='macd':return v('e12')-v('e26')
        if name=='aup':return roll('h',14,lambda x:100*(13-np.argmax(x[::-1]))/14)
        if name=='adn':return roll('l',14,lambda x:100*(13-np.argmin(x[::-1]))/14)
        if name=='vmplus':return abs(v('h')-v('l',1))
        if name=='vmminus':return abs(v('l')-v('h',1))
        if name=='vmp':return roll('vmplus',14,np.sum)
        if name=='vmm':return roll('vmminus',14,np.sum)
        if name in ('trs','trsum'):return roll('tr',14,np.sum)
        if name in ('vip','vim'):return divide(v('vmp' if name=='vip' else 'vmm'),v('trs'))
        if name=='bop':return divide(v('c')-v('o'),v('h')-v('l'))
        if name=='bullp':return v('h')-v('e13')
        if name=='bearp':return v('l')-v('e13')
        if name=='ad':
            value=divide((v('c')-v('l'))-(v('h')-v('c')),v('h')-v('l'))*v('vraw')
            return 0. if np.isnan(value) else value
        if name=='cmfv':return divide(roll('ad',20,np.sum),roll('vraw',20,np.sum))
        if name in ('posflow','negflow'):
            diff=v('tp')-v('tp',1)
            return v('tp')*v('v') if (diff>0 if name=='posflow' else diff<0) else 0.
        if name=='mfiv':return 100-100/(1+divide(roll('posflow',14,np.sum),roll('negflow',14,np.sum)))
        if name=='vol_surge':return v('v')>v('vol_ema')*1.2
        if name=='hh':return roll('h',14,np.max)
        if name=='ll':return roll('l',14,np.min)
        if name=='chop':return 100*np.log10(divide(v('trsum'),max(v('hh')-v('ll'),1e-12)))/math.log10(14)
        if name=='logret':return np.log(divide(v('c'),v('c',1)))
        if name=='hv':return roll('logret',20,lambda x:np.std(x,ddof=0))*math.sqrt(252)*100
        if name=='hvma':return roll('hv',20)
        if name in ('ten','kij','sb'):
            n={'ten':5,'kij':13,'sb':26}[name];return (roll('l',n,np.min)+roll('h',n,np.max))/2
        if name=='sa':return (v('ten')+v('kij'))/2
        if name in ('cloud_top','cloud_bot'):return (np.fmax if name=='cloud_top' else np.fmin)(v('sa',13),v('sb',13))
        if name=='vw':
            from indicator_facts import tf_seconds
            t=dt.datetime.fromtimestamp(int(self.view.time[i]),dt.timezone.utc);secs=tf_seconds(self.tf)
            key=(t.year,t.month,t.day) if secs<900 else t.isocalendar()[:2] if secs<3600 else (t.year,t.month) if secs<14400 else (t.year,(t.month-1)//3)
            oldkey,pv,vol=self.states[name][i-1] if i else (None,0.,0.)
            if key!=oldkey:pv=vol=0.
            pv+=v('tp')*v('v');vol+=v('v');self.states[name][i]=(key,pv,vol)
            return divide(pv,vol)
        if name=='sar':
            if i==0:self.states[name][i]=(True,.02,v('h'),v('l'));return v('l')
            bull,af,ep,sar=self.states[name][i-1];sar+=af*(ep-sar)
            if bull:
                sar=min(sar,v('l',1),v('l',2)) if i>=2 else min(sar,v('l',1))
                if v('l')<sar:bull,sar,ep,af=False,ep,v('l'),.02
                elif v('h')>ep:ep,af=v('h'),min(af+.02,.2)
            else:
                sar=max(sar,v('h',1),v('h',2)) if i>=2 else max(sar,v('h',1))
                if v('h')>sar:bull,sar,ep,af=True,ep,v('h'),.02
                elif v('l')<ep:ep,af=v('l'),min(af+.02,.2)
            self.states[name][i]=(bull,af,ep,sar);return sar
        if name=='st':
            bu=v('mid')+3*v('atr10');bl=v('mid')-3*v('atr10');bull=True
            if i and np.isfinite(v('atr10',1)):
                fu,fl,previous=self.states[name][i-1]
                bu=bu if bu<fu or v('c',1)>fu else fu
                bl=bl if bl>fl or v('c',1)<fl else fl
                bull=True if v('c')>fu else False if v('c')<fl else previous
            self.states[name][i]=(bu,bl,bull);return bull
        if name=='vol_state':
            state,fu,fl=self.states[name][i-1] if i else (1,NAN,NAN)
            bu=v('mid_ema')+2*v('ATR14_GENERAL');bl=v('mid_ema')-2*v('ATR14_GENERAL')
            if np.isfinite((bu,bl)).all():
                if state==1:fl=max(bl,fl if np.isfinite(fl) else bl);fu=bu
                else:fu=min(bu,fu if np.isfinite(fu) else bu);fl=bl
                if state==-1 and v('c')>fu:state=1
                elif state==1 and v('c')<fl:state=-1
            self.states[name][i]=(state,fu,fl);return state
        if name=='mss':
            ph,pl,state=self.states[name][i-1] if i else (None,None,0)
            if i>=10:
                hw=w('h',11);lw=w('l',11)
                if not np.isnan(hw).any() and v('h',5)==hw.max():ph=v('h',5)
                if not np.isnan(lw).any() and v('l',5)==lw.min():pl=v('l',5)
            if np.isfinite(v('c')):
                if ph is not None and v('c')>ph:state=1
                elif pl is not None and v('c')<pl:state=-1
            self.states[name][i]=(ph,pl,state);return state
        raise KeyError('unregistered array Fact: '+name)
