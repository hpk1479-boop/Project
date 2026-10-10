from pathlib import Path
p=Path(__file__).resolve().parents[1]/'Part1/program/strategy_SWEEP.py'
s=p.read_text('utf-8')
before='''        manager_timeout_ms: int = MANAGER_TIMEOUT_MS,
    ):
        self.staff = StaffClient(staff_endpoint, staff_timeout_ms)
        self.manager = ManagerClient(manager_endpoint, manager_timeout_ms)
        self.events = SweepEventPublisher()'''
after='''        manager_timeout_ms: int = MANAGER_TIMEOUT_MS,
        staff_client=None, manager_client=None, event_publisher=None,
        event_state=None, source_time=None,
    ):
        self.staff = StaffClient(staff_endpoint, staff_timeout_ms) if staff_client is None else staff_client
        self.manager = ManagerClient(manager_endpoint, manager_timeout_ms) if manager_client is None else manager_client
        self.events = SweepEventPublisher() if event_publisher is None else event_publisher
        self._source_time = source_time'''
assert s.count(before)==1;s=s.replace(before,after)
before='''        self.detectors: dict[str, ExternalLiquidityDetector] = {}
        self._fingerprints: dict[str, tuple] = {}
        self._restored_pending_sync: set[str] = set()
        self._state_dirty = False
        self._source_health = {}
        self._load_detector_state()'''
after='''        state = {} if event_state is None else event_state
        self.detectors = state.setdefault('detectors', {})
        self._fingerprints = state.setdefault('fingerprints', {})
        self._restored_pending_sync = state.setdefault('restored_pending_sync', set())
        self._state_dirty = False
        self._source_health = state.setdefault('source_health', {})
        if event_state is None:
            self._load_detector_state()'''
assert s.count(before)==1;s=s.replace(before,after)
s=s.replace('for event in detector.invalidate_all():',"for event in detector.invalidate_all(event_time=getattr(self, '_source_time', None)):")
p.write_text(s,encoding='utf-8')
print('SWEEP explicit state/client injection added')
