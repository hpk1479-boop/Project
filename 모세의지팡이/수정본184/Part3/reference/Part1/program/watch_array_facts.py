"""Variable-period WATCH MA Facts, resident history and replaceable forming row."""
import numpy as np
from watch_ma import canonical_ma, parse_ma_name, feature_source_rows
from indicator_facts import ma_array
from indicator_facts_numpy import ewm_step
from event_engine.market import COLUMNS

FACT_DEFINITIONS={'WATCH_MA': {'owner':__name__, 'inputs':('open','close','native_ma','period'),
                             'invalidation':'bar_close/open or forming_bar/close'}}


class WatchMAStore:
    def __init__(self):self.histories={};self.entries={};self.computations=0;self.recurrences={};self.publications={}
    def get(self,symbol,tf,view,names):
        names=tuple(dict.fromkeys(canonical_ma(name) for name in names));key=(symbol,tf)
        needed=max(feature_source_rows(name) for name in names)
        old=self.histories.get(key)
        signature=view.close_key()
        previous=self.publications.get(key)
        corrected=(previous is not None and signature==previous.close_key() and any(not np.array_equal(
            previous.column(name)[:-1].view('<u8'),view.column(name)[:-1].view('<u8')) for name in ('open','close')))
        self.publications[key]=view
        if old is None or old[0]!=signature or old[1]<needed or corrected:
            times=view.time;opens=view.column('open');closes=view.column('close')
            if old is not None and old[0][0]==signature[0] and int(times[0])<=int(old[2][-1]) and int(times[-1])>=int(old[2][-1]):
                take=int(np.searchsorted(old[2],int(times[0])))
                if take:
                    times=np.concatenate((old[2][:take],times));opens=np.concatenate((old[3][:take],opens));closes=np.concatenate((old[4][:take],closes))
            limit=max(needed,old[1] if old else 0)
            old=(signature,limit,times[-limit:],opens[-limit:],closes[-limit:]);self.histories[key]=old
        result={}
        for name in names:
            family,n=parse_ma_name(name);native=f'{family.lower()}_{n}'
            if native in COLUMNS:
                result[name]=view.column(native);continue
            entry_key=(symbol,tf,name);entry=self.entries.get(entry_key)
            # OPEN values do not change during a normal forming bar. Corrections
            # to its OPEN are still detected. EMA uses CLOSE, including every tick.
            source=old[4] if family=='EMA' else old[3]
            source=source[-feature_source_rows(name):]
            marker=(signature,len(source),float(view.column('open')[-1]) if family!='EMA' else None)
            if family=='EMA':
                from event_engine.recurrence import EWMPrefix
                cache=self.recurrences.setdefault(entry_key,EWMPrefix())
                live=np.r_[source[:-1],view.column('close')[-1]] if len(source) else source
                values=cache.evaluate(old[2][-len(live):],live,view.snapshot.source_epoch,2/(n+1),n)
                if entry is None or entry[0]!=marker or corrected:self.computations+=1
                self.entries[entry_key]=(marker,values,None);result[name]=values
                continue
            if entry is None or entry[0]!=marker or corrected:
                if family!='EMA' and len(source):
                    # Only allocate at a new bar/requested period/open correction.
                    source=np.r_[source[:-1],view.column('open')[-1]]
                values=ma_array(source,family,n)
                entry=(marker,values,None);self.entries[entry_key]=entry;self.computations+=1
            values=entry[1]
            result[name]=values
        return result
