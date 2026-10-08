"""Variable-period WATCH MA Facts, resident history and replaceable forming row."""
import numpy as np
from watch_ma import canonical_ma, parse_ma_name, feature_source_rows
from indicator_facts import ma_array
from indicator_facts_numpy import ewm_step
from event_engine.market import COLUMNS
from event_engine.model import same_immutable_array

FACT_DEFINITIONS={'WATCH_MA': {'owner':__name__, 'inputs':('open','close','native_ma','period'),
                             'invalidation':'bar_close/open or forming_bar/close'}}


def _same_bits(left,right):
    # Views of one sealed Snapshot share immutable bytes, so they are equal
    # without scanning; any other pair is compared bit for bit as before.
    return same_immutable_array(left,right) or np.array_equal(left.view('<u8'),right.view('<u8'))


class WatchMAStore:
    def __init__(self):
        self.histories={};self.entries={};self.computations=0;self.recurrences={};self.publications={}
        # A history rebuild (e.g. a corrected closed bar) is seen by the request that
        # detects it; every MA name of that feed must then recompute, even one asked later.
        self.versions={}
        self.closed_entries={};self.native_pending={}
    def get(self,symbol,tf,view,names,*,closed=False):
        """MA arrays; a CLOSED request uses only the prefix before the forming row.

        Variable CLOSED arrays keep the usual source-window length, with an unused
        NaN forming slot. The window/seed is the same as a full live calculation.
        """
        names=tuple(dict.fromkeys(canonical_ma(name) for name in names));key=(symbol,tf)
        needed=max(feature_source_rows(name) for name in names)
        old=self.histories.get(key)
        signature=view.close_key()
        native={name:f'{family.lower()}_{period}' for name in names for family,period in (parse_ma_name(name),)}
        if old is not None and old[0]==signature and old[1]>=needed and all(col in COLUMNS for col in native.values()):
            # Native arrays already contain any FULL correction. Retain both the
            # resident prefix and its publication: a later variable/mixed request
            # must still detect corrections skipped by this numerical fast path.
            self.native_pending[key]=view
            return {name:view.column(col) for name,col in native.items()}
        pending=self.native_pending.pop(key,None)
        if pending is not None:
            # Incorporate a deferred FULL before a later window discards its
            # corrected rows. This preserves resident seeds for variable periods.
            self._history(key,pending,old[1])
        old,signature,corrected=self._history(key,view,needed)
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
            if closed:
                result[name]=self._closed(entry_key,old[2][-len(source):],source,view.snapshot.source_epoch,family,n)
                continue
            marker=(signature,self.versions.get(key,0),len(source),float(view.column('open')[-1]) if family!='EMA' else None)
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

    def _history(self,key,view,needed):
        old=self.histories.get(key);signature=view.close_key()
        previous=self.publications.get(key)
        # The same view (one board publication) cannot correct itself.
        corrected=(previous is not None and previous is not view and signature==previous.close_key() and any(
            not _same_bits(previous.column(name)[:-1],view.column(name)[:-1]) for name in ('open','close')))
        self.publications[key]=view
        if old is None or old[0]!=signature or old[1]<needed or corrected:
            times=view.time;opens=view.column('open');closes=view.column('close')
            if old is not None and old[0][0]==signature[0] and int(times[0])<=int(old[2][-1]) and int(times[-1])>=int(old[2][-1]):
                take=int(np.searchsorted(old[2],int(times[0])))
                if take:
                    times=np.concatenate((old[2][:take],times));opens=np.concatenate((old[3][:take],opens));closes=np.concatenate((old[4][:take],closes))
            limit=max(needed,old[1] if old else 0)
            old=(signature,limit,times[-limit:],opens[-limit:],closes[-limit:]);self.histories[key]=old
            self.versions[key]=self.versions.get(key,0)+1
        return old,signature,corrected

    def _closed(self,key,times,source,epoch,family,period):
        times,source=times[:-1],source[:-1]
        previous=self.closed_entries.get(key)
        if previous is not None and previous[0]==epoch and np.array_equal(previous[1],times) and _same_bits(previous[2],source):
            return previous[3]
        # Existing MA formulas are causal. Dropping only the forming input keeps
        # every judged value and the original first-value/window seed unchanged.
        values=np.r_[ma_array(np.array(source),family,period),np.nan]
        values.setflags(write=False)
        self.closed_entries[key]=(epoch,times.copy(),source.copy(),values)
        self.computations+=1
        return values

