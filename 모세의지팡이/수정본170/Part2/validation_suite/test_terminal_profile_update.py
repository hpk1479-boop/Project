from pathlib import Path
import sys
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from generic_backtest import canonical, cli, native_mt5


def test_native_cli_updates_existing_profile_and_returns_result(monkeypatch, tmp_path):
    profile = tmp_path/'terminal_selection.json'
    canonical.write_json(profile, {'pid':10, 'created_at':20, 'name':'한글'})
    refreshed = {'pid':30, 'created_at':40, 'name':'한글'}
    events = []
    class Protocol:
        committed = False
        def __init__(self, *a): pass
        def emit(self, kind, payload): events.append((kind,payload))
        def listen(self): pass
        def check(self): pass
        def commit(self, fn):
            result=fn(); self.committed=True; return result
    def gate(old, *a, on_profile_restarted, **kw):
        assert old['pid']==10
        on_profile_restarted(refreshed)
        return {'status':'PASS', 'runtime':'MT5_STRATEGY_TESTER', 'restarted_profile':refreshed}
    monkeypatch.setattr(cli, 'JobProtocol', Protocol)
    monkeypatch.setattr(cli, 'plain_path', Path)
    monkeypatch.setattr(native_mt5, 'run_native_backtest_gate', gate)
    monkeypatch.setattr(sys, 'argv', ['test','native-backtest','--profile',str(profile),
        '--symbol','TEST','--start-ns','1','--end-ns','2','--work-dir',str(tmp_path)])
    assert cli.main()==0
    assert canonical.read_json(profile)==refreshed
    assert events[-1]==('RESULT', {'status':'PASS','runtime':'MT5_STRATEGY_TESTER'})
    assert not list(tmp_path.glob('*.tmp'))


def test_failed_replace_preserves_existing_profile(monkeypatch,tmp_path):
    profile=tmp_path/'profile.json'
    canonical.write_json(profile, {'pid':10})
    def fail(*a): raise PermissionError('controlled file lock')
    monkeypatch.setattr(canonical.os,'replace',fail)
    with pytest.raises(PermissionError): canonical.replace_json(profile, {'pid':30})
    assert canonical.read_json(profile)=={'pid':10}
    assert not list(tmp_path.glob('*.tmp'))
    with pytest.raises(FileExistsError): canonical.write_json(profile, {'pid':30})
