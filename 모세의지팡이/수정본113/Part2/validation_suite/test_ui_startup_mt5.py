"""Launch contract and MT5 UI consent tests, with fail-closed fake terminal APIs."""
from pathlib import Path
from types import SimpleNamespace
import importlib.machinery
import importlib.util
import json
import sys

import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bootstrap
from generic_backtest import gui
from generic_backtest.history import selection
from generic_backtest.history.provider import ReadOnlyMT5Provider
from generic_backtest.contracts import GenericError


def load_launcher():
    p = Path(__file__).resolve().parents[1] / 'BACKTEST CONTROL.pyw'
    loader = importlib.machinery.SourceFileLoader('backtest_control_test', str(p))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec); loader.exec_module(module)
    return module


def test_relaunch_uses_existing_probe_and_windowed_child(monkeypatch, tmp_path):
    app = load_launcher(); monkeypatch.setattr(app, 'ROOT', tmp_path)
    calls = []
    monkeypatch.setattr(bootstrap, 'probe', lambda env: (calls.append(('probe', env)) or (True, 'ok')))
    def executable(env, windowed=False):
        calls.append(('executable', env, windowed))
        return env / 'Scripts' / ('pythonw.exe' if windowed else 'python.exe')
    monkeypatch.setattr(bootstrap, 'executable', executable)
    monkeypatch.setattr(app.subprocess, 'call', lambda *a, **k: (calls.append(('call', a, k)) or 7))
    assert app.main() == 7  # Parent waits for and propagates child exit; no detached helper.
    assert calls[0] == ('probe', tmp_path / '.venv-generic')
    assert calls[1] == ('executable', tmp_path / '.venv-generic', True)
    command = calls[2][1][0]; options = calls[2][2]
    assert command[0].endswith('pythonw.exe') and command[1].endswith('BACKTEST CONTROL.pyw')
    assert options['cwd'] == tmp_path
    assert options['env']['PYTHONDONTWRITEBYTECODE'] == '1'
    assert options['env']['PYTHONUTF8'] == '1'
    assert not options.get('shell')


def test_bad_environment_still_blocks_launch(monkeypatch, tmp_path):
    app = load_launcher(); monkeypatch.setattr(app, 'ROOT', tmp_path)
    monkeypatch.setattr(bootstrap, 'probe', lambda env: (False, 'controlled invalid prefix'))
    monkeypatch.setattr(app.subprocess, 'call', lambda *a, **k: pytest.fail('Bad venv launched'))
    with pytest.raises(RuntimeError, match='START_BACKTEST.cmd'):
        app.main()


def test_in_venv_launch_keeps_gui_order(monkeypatch, tmp_path):
    import tkinter as tk
    app = load_launcher(); monkeypatch.setattr(app, 'ROOT', tmp_path)
    monkeypatch.setattr(sys, 'prefix', str(tmp_path / '.venv-generic'))
    order = []
    root = SimpleNamespace(mainloop=lambda: order.append('mainloop'))
    monkeypatch.setattr(bootstrap, 'probe', lambda env: pytest.fail('Unexpected reprobe'))
    monkeypatch.setattr(tk, 'Tk', lambda: (order.append('Tk') or root))
    monkeypatch.setattr(app, 'App', lambda actual: order.append(('App', actual is root)))
    assert app.main() == 0 and order == ['Tk', ('App', True), 'mainloop']


def test_app_close_keeps_existing_cancel_and_destroy_sequence():
    app = load_launcher(); order = []
    panel = SimpleNamespace(busy=True, cancel=lambda: order.append('cancel'))
    root = SimpleNamespace(after=lambda ms, cb: order.append(('after', ms)), destroy=lambda: order.append('destroy'))
    obj = SimpleNamespace(generic=panel, root=root, closing=False)
    obj.close = lambda: app.App.close(obj)
    obj.close(); obj.close(); panel.busy = False; obj.close()
    assert order == ['cancel', ('after', 100), ('after', 100), 'destroy']


def test_bootstrap_windows_executable_contract():
    # Execute the tiny pure helper with a Windows os facade, not global os.name.
    from pathlib import PureWindowsPath
    import inspect
    namespace = {'Path': PureWindowsPath, 'os': SimpleNamespace(name='nt')}
    exec(inspect.getsource(bootstrap.executable), namespace)
    env = PureWindowsPath(r'C:\Backtest\.venv-generic')
    assert str(namespace['executable'](env, windowed=True)).endswith(r'Scripts\pythonw.exe')
    assert str(namespace['executable'](env)).endswith(r'Scripts\python.exe')


def terminal(tmp_path, number=1, **overrides):
    install = tmp_path / f'mt5_{number}'; install.mkdir(exist_ok=True)
    row = dict(pid=number, created_at=1234 + number, executable=str(install / 'terminal64.exe'),
               data_root=str(install / 'data'), account_server='TEST-SERVER', account_login=12345,
               portable=False, live=False, live_unknown=True)
    row.update(overrides)
    return row


def consent_panel(monkeypatch, tmp_path, chosen=None):
    monkeypatch.setattr(gui, 'ROOT', tmp_path)
    panel = gui.GenericPanel.__new__(gui.GenericPanel)
    panel.frame = None; panel.live_consents = set(); panel.profile = None
    panel.running = True; panel.ready = 0; panel.picked = []
    panel.status = SimpleNamespace(set=lambda value: None)
    def choose(rows):
        panel.picked.append(rows)
        return chosen
    panel._choose_mt5 = choose
    panel.terminal_ready = lambda: setattr(panel, 'ready', panel.ready + 1)
    return panel


def test_unknown_live_matches_old_yes_profile_without_popup(monkeypatch, tmp_path):
    from tkinter import messagebox
    row = terminal(tmp_path)
    panel = consent_panel(monkeypatch, tmp_path)
    monkeypatch.setattr(messagebox, 'askyesno', lambda *a, **k: pytest.fail('Unknown LIVE popup shown'))
    expected = selection.select_terminal([row], lambda rows: pytest.fail('Unexpected picker'), lambda text: True)
    panel._terminal_discovered({'terminals': [row]})
    assert json.loads(panel.profile.read_text()) == expected
    assert expected['unknown_live_confirmed'] is True
    assert panel.ready == 1 and not panel.picked and len(panel.live_consents) == 1


def test_multiple_terminal_selection_rule_is_unchanged(monkeypatch, tmp_path):
    from tkinter import messagebox
    first = terminal(tmp_path, 1); second = terminal(tmp_path, 2)
    panel = consent_panel(monkeypatch, tmp_path, chosen=second)
    monkeypatch.setattr(messagebox, 'askyesno', lambda *a, **k: pytest.fail('Unknown LIVE popup shown'))
    panel._terminal_discovered({'terminals': [first, second]})
    assert panel.picked == [[first, second]]
    assert json.loads(panel.profile.read_text()) == dict(second, unknown_live_confirmed=True)


@pytest.mark.parametrize('accepted', [True, False])
def test_known_live_confirmation_is_not_silenced(monkeypatch, tmp_path, accepted):
    from tkinter import messagebox
    row = terminal(tmp_path, live=True, live_unknown=False)
    panel = consent_panel(monkeypatch, tmp_path); asked = []
    monkeypatch.setattr(messagebox, 'askyesno', lambda *a, **k: (asked.append(a) or accepted))
    panel._terminal_discovered({'terminals': [row]})
    assert len(asked) == 1 and asked[0][1] == selection.LIVE_CONFIRM
    if accepted:
        assert json.loads(panel.profile.read_text())['live_confirmed'] is True and panel.ready == 1
    else:
        assert panel.profile is None and panel.ready == 0 and not panel.running


def test_cancel_picker_still_aborts(monkeypatch, tmp_path):
    panel = consent_panel(monkeypatch, tmp_path)
    panel._terminal_discovered({'terminals': [terminal(tmp_path, 1), terminal(tmp_path, 2)]})
    assert panel.profile is None and panel.ready == 0 and not panel.running


def test_no_mt5_still_errors(monkeypatch, tmp_path):
    panel = consent_panel(monkeypatch, tmp_path)
    with pytest.raises(GenericError, match='E_MT5_NOT_RUNNING'):
        panel._terminal_discovered({'terminals': []})
    assert panel.profile is None and panel.ready == 0


def test_inventory_filters_sorts_and_keeps_saved_identity(monkeypatch, tmp_path):
    a = terminal(tmp_path, 1); b = terminal(tmp_path, 2)
    rows = [b, dict(a, executable=str(tmp_path / 'other.exe')), a]
    monkeypatch.setattr(selection, 'running_processes', lambda: rows)
    monkeypatch.setattr(selection, '_saved_identity', lambda *a: {'account_server': 'SAVED', 'account_login': 3, 'data_root': 'saved-root'})
    result = selection.discover_terminals()
    assert [r['pid'] for r in result['terminals']] == [1, 2]
    assert all(r['account_server'] == 'SAVED' and r['live_unknown'] for r in result['terminals'])


class FakeMT5:
    COPY_TICKS_ALL = 0
    def __init__(self, profile):
        self.calls = []
        self.info = SimpleNamespace(connected=True, path=str(Path(profile['executable']).parent), data_path=profile['data_root'])
        self.account = SimpleNamespace(server=profile['account_server'], login=profile['account_login'])
        self.initialized = True
        self.symbol_rows = [SimpleNamespace(name='VISIBLE', description='visible', visible=True),
                            SimpleNamespace(name='HIDDEN', description='hidden', visible=False)]
    def initialize(self, path, **kw): self.calls.append(('initialize', path, kw)); return self.initialized
    def terminal_info(self): self.calls.append(('terminal_info',)); return self.info
    def account_info(self): self.calls.append(('account_info',)); return self.account
    def version(self): return (500, 1, 'fake-test-only')
    def shutdown(self): self.calls.append(('shutdown',))
    def symbols_get(self): self.calls.append(('symbols_get',)); return self.symbol_rows
    def last_error(self): return (1, 'Success')


def fake_connection(monkeypatch, tmp_path):
    row = terminal(tmp_path); fake = FakeMT5(row)
    monkeypatch.setitem(sys.modules, 'MetaTrader5', fake)
    monkeypatch.setattr(selection, 'running_processes', lambda: [row])
    return row, fake


def test_auto_approved_profile_initializes_validates_and_reads_visible_only(monkeypatch, tmp_path):
    from tkinter import messagebox
    row, fake = fake_connection(monkeypatch, tmp_path)
    panel = consent_panel(monkeypatch, tmp_path)
    monkeypatch.setattr(messagebox, 'askyesno', lambda *a, **k: pytest.fail('Popup shown'))
    panel._terminal_discovered({'terminals': [row]})
    with_profile = json.loads(panel.profile.read_text())
    provider = ReadOnlyMT5Provider(with_profile)
    assert fake.calls[0] == ('initialize', row['executable'], {'portable': False})
    assert provider.symbols() == [{'symbol': 'VISIBLE', 'description': 'visible'}]
    assert fake.calls.count(('symbols_get',)) == 1
    assert fake.calls.count(('terminal_info',)) >= 3
    provider.close(); assert fake.calls[-1] == ('shutdown',)


@pytest.mark.parametrize('fault', ['initialize', 'terminal_path', 'data_path', 'server', 'login', 'connected', 'no_account', 'no_terminal'])
def test_connection_errors_still_fail_closed(monkeypatch, tmp_path, fault):
    row, fake = fake_connection(monkeypatch, tmp_path)
    if fault == 'initialize': fake.initialized = False
    elif fault == 'terminal_path': fake.info.path = str(tmp_path / 'wrong')
    elif fault == 'data_path': fake.info.data_path = str(tmp_path / 'wrong')
    elif fault == 'server': fake.account.server = 'WRONG'
    elif fault == 'login': fake.account.login += 1
    elif fault == 'connected': fake.info.connected = False
    elif fault == 'no_account': fake.account = None
    elif fault == 'no_terminal': fake.info = None
    with pytest.raises(GenericError, match='E_MT5_UNAVAILABLE|E_MT5_IDENTITY'):
        ReadOnlyMT5Provider(dict(row, unknown_live_confirmed=True))
    assert ('shutdown',) in fake.calls and ('symbols_get',) not in fake.calls


@pytest.mark.parametrize('fault', ['missing_process', 'duplicate_process', 'missing_identity'])
def test_preflight_blocks_initialize(monkeypatch, tmp_path, fault):
    row, fake = fake_connection(monkeypatch, tmp_path)
    if fault == 'missing_process': monkeypatch.setattr(selection, 'running_processes', lambda: [])
    elif fault == 'duplicate_process': monkeypatch.setattr(selection, 'running_processes', lambda: [row, dict(row, pid=42)])
    else: row.pop('account_server')
    with pytest.raises(GenericError, match='E_MT5_NOT_RUNNING|E_MT5_IDENTITY'):
        ReadOnlyMT5Provider(row)
    assert not any(c[0] == 'initialize' for c in fake.calls)


def test_market_watch_error_and_changed_identity_not_ignored(monkeypatch, tmp_path):
    row, fake = fake_connection(monkeypatch, tmp_path)
    provider = ReadOnlyMT5Provider(row)
    fake.symbol_rows = None
    with pytest.raises(GenericError, match='E_MT5_UNAVAILABLE'): provider.symbols()
    fake.account.server = 'CHANGED'
    with pytest.raises(GenericError, match='E_MT5_IDENTITY'): provider.symbols()
