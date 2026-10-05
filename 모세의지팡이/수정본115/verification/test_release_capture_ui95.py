"""Logical symbols survive safe capture-directory encoding and relocation."""
import shutil
from verification.test_warehouse76 import project, unified_backtest, ui_model, unified_live
from event_backtest.capture_layout import capture_parent


def test_options_decode_symbols_after_warehouse_move(project, monkeypatch):
    store = project / '창고'
    symbols = ['#USNDAQ100', 'CON', '../logical', '한글종목', 'XAUUSD+']
    for symbol in symbols:
        capture_parent(store, symbol, 'BAR', '2026-09-01').mkdir(parents=True)
    moved = project / '이동한 창고'
    shutil.move(str(store), str(moved))
    monkeypatch.setattr(unified_backtest, 'warehouse', lambda: moved)
    monkeypatch.setattr(ui_model, 'load', lambda: {})
    monkeypatch.setattr(unified_live, 'control', lambda: {'special_code_default_trigger': lambda _: '올존'})
    before = sorted(p.relative_to(moved).as_posix() for p in moved.rglob('*'))
    result = unified_backtest.options()
    assert result['symbols'] == sorted(symbols)
    assert result['symbol'] == 'XAUUSD+'
    assert sorted(p.relative_to(moved).as_posix() for p in moved.rglob('*')) == before
