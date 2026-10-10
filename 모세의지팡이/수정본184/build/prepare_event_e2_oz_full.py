from pathlib import Path
p=Path(__file__).resolve().parents[1]/'Part1/program/monitor_OZ.py';s=p.read_text('utf-8')
a='''    def __init__(self):
        self._lock = threading.RLock()
        self._specs: dict[str, ExternalLiquiditySpec] = {}
        self._states: dict[str, ExternalLiquidityState] = {}
        self._invalidated: dict[str, float] = {}'''
b='''    def __init__(self, *, event_state=None, sweep_registry=None):
        self._lock = threading.RLock()
        state = {} if event_state is None else event_state
        self._specs = state.setdefault('specs', {})
        self._states = state.setdefault('states', {})
        self._invalidated = state.setdefault('invalidated', {})
        self._event_registry = sweep_registry'''
assert s.count(a)==1;s=s.replace(a,b)
s=s.replace('''        self._load_state()
        self._bootstrap_sweep_registry()''','''        if event_state is None:
            self._load_state()
            self._bootstrap_sweep_registry()''',1)
key='''        """현재 strategy_SWEEP registry만 읽어 SWEEP watch 존재 여부의 기준으로 사용합니다."""
        path = self._sweep_registry_path'''
assert key in s;s=s.replace(key,'''        """현재 strategy_SWEEP registry만 읽어 SWEEP watch 존재 여부의 기준으로 사용합니다."""
        if getattr(self, '_event_registry', None) is not None:
            return dict(self._event_registry)
        path = self._sweep_registry_path''',1)
a='''    def __init__(self, telegram: "TelegramSender", external: ExternalLiquidityController):
        self.telegram = telegram
        self.external = external
        self._lock = threading.RLock()
        self._watches: dict[str, WatchSpec] = {}
        self._revision = {_profile_key(*profile): 0 for profile in PROFILE_KEYS}
        self._state_path = LOG_DIR / "oz_manual_watch_state.json"
        self._load_state()'''
b='''    def __init__(self, telegram: "TelegramSender", external: ExternalLiquidityController, *, event_state=None):
        self.telegram = telegram
        self.external = external
        self._lock = threading.RLock()
        state = {} if event_state is None else event_state
        self._watches = state.setdefault('watches', {})
        self._revision = state.setdefault('revision', {_profile_key(*profile): 0 for profile in PROFILE_KEYS})
        self._state_path = LOG_DIR / "oz_manual_watch_state.json"
        if event_state is None:self._load_state()'''
assert s.count(a)==1;s=s.replace(a,b)
a='''    def __init__(self, telegram: "TelegramSender"):
        self.telegram = telegram
        self._lock = threading.RLock()
        self._watches: dict[str, GenericWatchSpec] = {}
        self._revision = 0
        self._state_path = LOG_DIR / "oz_generic_watch_state.json"
        self._load_state()'''
b='''    def __init__(self, telegram: "TelegramSender", *, event_state=None):
        self.telegram = telegram
        self._lock = threading.RLock()
        state = {} if event_state is None else event_state
        self._watches = state.setdefault('watches', {})
        self._revision = state.get('revision', 0)
        self._state_path = LOG_DIR / "oz_generic_watch_state.json"
        if event_state is None:self._load_state()'''
assert s.count(a)==1;s=s.replace(a,b)
a='''    def __init__(self, config: dict[str, str]):
        self.endpoint = config.get("MANAGER_ALERT_ENDPOINT", "tcp://127.0.0.1:5556")'''
b='''    def __init__(self, config: dict[str, str], *, transport=None):
        self._transport = transport
        self.endpoint = config.get("MANAGER_ALERT_ENDPOINT", "tcp://127.0.0.1:5556")'''
assert s.count(a)==1;s=s.replace(a,b)
a='''    def _request(self, event: dict) -> dict:
        sock = self._socket()'''
b='''    def _request(self, event: dict) -> dict:
        if getattr(self, '_transport', None) is not None:
            return self._transport(event)
        sock = self._socket()'''
assert s.count(a)==1;s=s.replace(a,b)
p.write_text(s,encoding='utf-8')
print('OZ controllers and message transport explicit injection added')
