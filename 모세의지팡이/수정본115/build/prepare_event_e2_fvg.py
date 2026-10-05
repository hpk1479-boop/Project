from pathlib import Path
p=Path(__file__).resolve().parents[1]/'Part1/program/strategy_FVG.py'
s=p.read_text('utf-8')
before='''        manager_timeout_ms: int = MANAGER_TIMEOUT_MS,
    ):
        self.staff = StaffClient(staff_endpoint, staff_timeout_ms)
        self.manager = ManagerClient(manager_endpoint, manager_timeout_ms)
        self._last_closed_time: dict[tuple[str, str], float] = {}
        self._touch_state: dict[str, bool] = {}
        self._active_zones: dict[tuple[str, str], dict[str, dict]] = {}
        self._seen_created: set[str] = set()
        self._initialized_keys: set[tuple[str, str]] = set()'''
after='''        manager_timeout_ms: int = MANAGER_TIMEOUT_MS,
        staff_client=None, manager_client=None, event_state=None,
    ):
        self.staff = StaffClient(staff_endpoint, staff_timeout_ms) if staff_client is None else staff_client
        self.manager = ManagerClient(manager_endpoint, manager_timeout_ms) if manager_client is None else manager_client
        state = {} if event_state is None else event_state
        self._last_closed_time = state.setdefault('last_closed_time', {})
        self._touch_state = state.setdefault('touch_state', {})
        self._active_zones = state.setdefault('active_zones', {})
        self._seen_created = state.setdefault('seen_created', set())
        self._initialized_keys = state.setdefault('initialized_keys', set())'''
assert s.count(before)==1
p.write_text(s.replace(before,after),encoding='utf-8')
print('FVG explicit state/client injection added')
