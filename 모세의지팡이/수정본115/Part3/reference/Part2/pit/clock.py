from .contracts import PitError

class PitClock:
    def __init__(self,now_ns=0):self._now=0;self.advance_to(now_ns)
    @property
    def now_ns(self):return self._now
    def advance_to(self,now_ns):
        if type(now_ns)!=int or now_ns<self._now or now_ns>2**63-1:
            raise PitError('E_FUTURE_READ','backward/non-int/out-of-range clock')
        self._now=now_ns
    def time(self):return self._now/1e9
    def monotonic(self):return 1+self._now/1e9
    def datetime(self,tz=None):
        import datetime as dt
        seconds,nanos=divmod(self._now,10**9)
        value=dt.datetime.fromtimestamp(seconds,dt.timezone.utc)+dt.timedelta(microseconds=nanos//1000)
        return value.astimezone(tz) if tz else value.replace(tzinfo=None)
