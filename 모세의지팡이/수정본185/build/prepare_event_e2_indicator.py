from pathlib import Path
p=Path(__file__).resolve().parents[1]/'Part1/program/strategy_INDICATOR.py'
s=p.read_text('utf-8')
before='''        manager_timeout_ms: int = MANAGER_TIMEOUT_MS,
    ):
        self.staff = StaffClient(staff_endpoint, staff_timeout_ms)
        self.manager = ManagerClient(manager_endpoint, manager_timeout_ms)
        self.threshold = float(threshold)
        self.facts = FactStore()
        self._last_watch_state: dict[tuple[str, str], str] = {}
        self._pending_states = Records(Path(__file__).resolve().parent / 'logs' / 'trend_pending_events.json')
        self._last_metric_push: dict[tuple[str, str], tuple[tuple[str, ...], float]] = {}'''
after='''        manager_timeout_ms: int = MANAGER_TIMEOUT_MS,
        staff_client=None, manager_client=None, event_state=None, pending_records=None,
    ):
        self.staff = StaffClient(staff_endpoint, staff_timeout_ms) if staff_client is None else staff_client
        self.manager = ManagerClient(manager_endpoint, manager_timeout_ms) if manager_client is None else manager_client
        self.threshold = float(threshold)
        self.facts = FactStore()
        state = {} if event_state is None else event_state
        self._last_watch_state = state.setdefault('last_watch_state', {})
        self._pending_states = Records(Path(__file__).resolve().parent / 'logs' / 'trend_pending_events.json') if pending_records is None else pending_records
        self._last_metric_push = state.setdefault('last_metric_push', {})'''
assert s.count(before)==1
p.write_text(s.replace(before,after),encoding='utf-8')
print('INDICATOR explicit state/client injection added')
