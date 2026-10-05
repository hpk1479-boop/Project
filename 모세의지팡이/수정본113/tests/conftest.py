"""E2 tests must not inherit Part2 replay's process-global module replacements."""
import sys
from pathlib import Path
import pytest


@pytest.fixture(autouse=True)
def event_e2_canonical_import_scope(request,monkeypatch):
    if not request.node.path.name.startswith('test_event_e2_'):return
    program=Path(__file__).resolve().parents[1]/'Part1/program'
    monkeypatch.syspath_prepend(str(program))
    # Preserve the E1 engine and ContextVar port identities imported at collection.
    # Replace only operational modules that the Part2 tests monkeypatch globally.
    for name in ('durable_protocol','monitor_OZ','manager_KIM','strategy_INDICATOR',
                 'strategy_FVG','strategy_SWEEP','staff_compat','staff_snapshot',
                 'command_interpreter','watch_orchestrator','oz_profiles'):
        monkeypatch.delitem(sys.modules,name,raising=False)
