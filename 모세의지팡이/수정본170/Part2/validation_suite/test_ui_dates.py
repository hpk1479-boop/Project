"""UI-only date contract; real Tk tests run with xvfb-run on Linux."""
from pathlib import Path
from types import SimpleNamespace
import calendar
import datetime as dt
import os
import sys
import time

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from generic_backtest import gui
from generic_backtest.ui_presentation import KST


@pytest.mark.parametrize('today,start', [
    ('2026-09-22', '2026-08-22'), ('2026-03-31', '2026-02-28'),
    ('2024-03-31', '2024-02-29'), ('2026-01-31', '2025-12-31'),
    ('2026-05-31', '2026-04-30'), ('2024-02-29', '2024-01-29'),
])
def test_default_calendar_month(today, start):
    assert gui.default_date_range(dt.date.fromisoformat(today)) == (start, today)


def test_default_every_day_across_leap_and_year_boundaries():
    today = dt.date(2023, 1, 1)
    while today <= dt.date(2027, 12, 31):
        start, end = map(dt.date.fromisoformat, gui.default_date_range(today))
        assert end == today
        assert (today.year * 12 + today.month) - (start.year * 12 + start.month) == 1
        assert start.day == min(today.day, calendar.monthrange(start.year, start.month)[1])
        today += dt.timedelta(days=1)


def test_default_uses_one_kst_clock_read(monkeypatch):
    calls = []
    class Clock(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            calls.append(tz)
            return dt.datetime(2026, 9, 21, 15, 1, tzinfo=dt.timezone.utc).astimezone(tz)
    monkeypatch.setattr(gui, 'dt', SimpleNamespace(datetime=Clock, date=dt.date))
    assert gui.default_date_range() == ('2026-08-22', '2026-09-22')
    assert calls == [KST]


@pytest.mark.parametrize('start,end,exclusive,days', [
    ('2026-09-01', '2026-09-22', '2026-09-23', 22),
    ('2026-09-22', '2026-09-22', '2026-09-23', 1),
    ('2026-02-28', '2026-02-28', '2026-03-01', 1),
    ('2026-04-30', '2026-04-30', '2026-05-01', 1),
    ('2026-12-31', '2026-12-31', '2027-01-01', 1),
    ('2024-02-29', '2024-02-29', '2024-03-01', 1),
])
def test_inclusive_end_once(start, end, exclusive, days):
    a, b = gui.parse_ui_date_range(start, end)
    assert a == gui.parse_kst_ns(start + 'T00:00:00+09:00')
    assert b == gui.parse_kst_ns(exclusive + 'T00:00:00+09:00')
    assert b - a == days * 86400 * 10**9


@pytest.mark.parametrize('value', [
    '2026-02-30', '2026-09-31', '2026-13-01', '2026/09/22',
    '22-09-2026', '2026-02-29', '2026-00-01', '2026-01-00',
    '2026-1-01', '2026-01-1', '20260922', '2026-W39-2',
    '2026-09-22 00:00:00', '2026-09-22T00:00:00+09:00',
    ' 2026-09-22', '2026-09-22\n', '', '２０２６-０９-２２', '0000-01-01',
])
@pytest.mark.parametrize('field', ['start', 'end'])
def test_invalid_dates_block_both_fields(value, field):
    args = (value, '2026-09-22') if field == 'start' else ('2026-09-01', value)
    with pytest.raises(ValueError, match='YYYY-MM-DD|존재하지 않는 날짜'):
        gui.parse_ui_date_range(*args)


def test_reversed_range_rejected():
    with pytest.raises(ValueError, match='종료일은 시작일보다 이전'):
        gui.parse_ui_date_range('2026-09-22', '2026-09-21')


def test_max_end_overflow_is_friendly():
    with pytest.raises(ValueError, match='종료일 다음 날'):
        gui.parse_ui_date_range('9999-12-31', '9999-12-31')


@pytest.mark.parametrize('local_zone', ['UTC', 'America/Los_Angeles', 'Pacific/Kiritimati'])
def test_host_timezone_never_changes_boundaries(local_zone, monkeypatch):
    if not hasattr(time, 'tzset'):
        pytest.skip('tzset is not available; explicit-offset tests still apply on Windows')
    old = os.environ.get('TZ')
    try:
        monkeypatch.setenv('TZ', local_zone); time.tzset()
        a, b = gui.parse_ui_date_range('2026-09-22', '2026-09-22')
        assert a == gui.parse_utc_ns('2026-09-21T15:00:00+00:00')
        assert b == gui.parse_utc_ns('2026-09-22T15:00:00+00:00')
    finally:
        if old is None: os.environ.pop('TZ', None)
        else: os.environ['TZ'] = old
        time.tzset()


@pytest.fixture
def tk_root():
    import tkinter as tk
    try: root = tk.Tk()
    except tk.TclError as exc: pytest.skip(f'No Tk display: {exc}; use xvfb-run')
    root.geometry('1180x900+0+0')
    yield root
    root.destroy()


@pytest.fixture
def panel(tk_root, tmp_path):
    panel = gui.GenericPanel(tk_root, auto_connect=False, start_poll=False, log_directory=tmp_path / 'ui-log')
    panel.frame.pack(fill='both', expand=True)
    tk_root.update()
    yield panel


def day_button(picker, day):
    from tkinter import ttk
    return next(w for w in picker.days.winfo_children()
                if isinstance(w, ttk.Button) and w.cget('text') == str(day))


def test_calendar_navigation_headers_and_selection(panel):
    from tkinter import ttk
    panel.start.set('2026-12-31'); panel.end.set('2027-01-31')
    picker = panel.open_date_picker(panel.start)
    assert (picker.year, picker.month) == (2026, 12)
    labels = [w.cget('text') for w in picker.days.winfo_children()
              if isinstance(w, ttk.Label) and int(w.grid_info()['row']) == 0]
    assert labels == ['일', '월', '화', '수', '목', '금', '토']
    picker.next.invoke(); assert (picker.year, picker.month) == (2027, 1)
    picker.previous.invoke(); assert (picker.year, picker.month) == (2026, 12)
    picker.previous.invoke(); assert (picker.year, picker.month) == (2026, 11)
    day_button(picker, 30).invoke()
    assert panel.start.get() == '2026-11-30'
    assert panel.end.get() == '2027-01-31'
    assert not picker.window.winfo_exists()


def test_calendar_end_only_and_manual_input_same_config(panel):
    panel.start.set('2026-09-01'); panel.end.set('2026-09-01')
    picker = panel.open_date_picker(panel.end)
    day_button(picker, 22).invoke()
    picked = panel._config_from_controls()
    assert panel.start.get() == '2026-09-01'
    assert panel.end.get() == '2026-09-22'
    entry = panel.entries[1]
    assert str(entry.cget('state')) == 'normal'
    entry.delete(0, 'end'); entry.insert(0, '2026-09-22')
    assert panel._config_from_controls() == picked
    assert (picked['start_ns'], picked['end_ns']) == gui.parse_ui_date_range('2026-09-01', '2026-09-22')
    entry.delete(0, 'end'); entry.insert(0, '2026-09-23')
    assert panel._config_from_controls()['end_ns'] == gui.parse_kst_ns('2026-09-24')


@pytest.mark.parametrize('invalid', ['', 'bad', '2026-02-30'])
def test_calendar_invalid_entry_falls_back_to_kst_month(panel, invalid):
    panel.start.set(invalid)
    before = dt.datetime.now(KST).date()
    picker = panel.open_date_picker(panel.start)
    after = dt.datetime.now(KST).date()
    assert (picker.year, picker.month) in {(before.year, before.month), (after.year, after.month)}
    assert panel.start.get() == invalid  # Merely opening the calendar never edits text.
    picker.window.destroy()


@pytest.mark.parametrize('value,count', [('2024-02-29', 29), ('2026-02-28', 28)])
def test_calendar_leap_month(panel, value, count):
    from tkinter import ttk
    panel.start.set(value); picker = panel.open_date_picker(panel.start)
    dates = [int(w.cget('text')) for w in picker.days.winfo_children() if isinstance(w, ttk.Button)]
    assert dates == list(range(1, count + 1))
    day_button(picker, count).invoke(); assert panel.start.get() == value


def test_calendar_extreme_year_navigation(panel):
    panel.start.set('0001-01-01'); picker = panel.open_date_picker(panel.start)
    assert str(picker.previous.cget('state')) == 'disabled'
    picker._move_month(-1); assert (picker.year, picker.month) == (1, 1)
    panel.end.set('9999-12-30'); end_picker = panel.open_date_picker(panel.end)
    assert not picker.window.winfo_exists()
    assert str(end_picker.next.cget('state')) == 'disabled'
    end_picker._move_month(1); assert (end_picker.year, end_picker.month) == (9999, 12)
    end_picker.window.destroy()


def test_ui_labels_editable_entries_busy_buttons(panel):
    from tkinter import ttk
    def descendants(w):
        for child in w.winfo_children():
            yield child
            yield from descendants(child)
    widgets = list(descendants(panel.frame))
    labels = [w.cget('text') for w in widgets if isinstance(w, ttk.Label)]
    assert '시작일(KST)' in labels and '종료일(KST)' in labels
    assert not any('미포함' in str(text) for text in labels)
    buttons = [w for w in widgets if isinstance(w, ttk.Button) and w.cget('text') == '달력']
    assert len(buttons) == 2 and all(b in panel.controls for b in buttons)
    assert all(str(e.cget('state')) == 'normal' for e in panel.entries)
    panel.busy = True; panel.refresh()
    assert all(str(w.cget('state')) == 'disabled' for w in [*buttons, *panel.entries])
    assert panel.open_date_picker(panel.start) is None
    panel.busy = False; panel.refresh()
    assert all(str(w.cget('state')) == 'normal' for w in [*buttons, *panel.entries])


@pytest.mark.parametrize('start,end', [('2026-02-30', '2026-09-22'), ('2026-09-22', '2026-09-21')])
def test_invalid_input_cannot_start_backtest(panel, monkeypatch, start, end):
    from tkinter import messagebox
    panel.start.set(start); panel.end.set(end)
    errors = []
    monkeypatch.setattr(messagebox, 'showerror', lambda *a, **k: errors.append(a))
    monkeypatch.setattr(panel, 'request_market_watch', lambda *a, **k: pytest.fail('Invalid dates started acquisition'))
    panel.run()
    assert len(errors) == 1 and not panel.running
    assert panel.config is None


def test_gui_config_plan_fetch_run_no_second_end_increment(panel, tmp_path, monkeypatch):
    from generic_backtest.runner import prepare_plan
    from generic_backtest.contracts import GenericRunConfig
    from integration_fixtures import INSTRUMENT
    panel.start.set('2026-09-01'); panel.end.set('2026-09-22')
    config = panel._config_from_controls(); config['instrument'] = INSTRUMENT
    # The unchanged real planner may extend ONLY warm-up start.
    plan = prepare_plan(GenericRunConfig(**config))
    expected = gui.parse_kst_ns('2026-09-23')
    assert config['end_ns'] == plan['end_ns'] == expected
    assert plan['start_ns'] <= config['start_ns']
    panel.job = tmp_path; panel.config = config
    panel.profile = tmp_path / 'profile.json'
    calls = []
    monkeypatch.setattr(panel, 'launch', lambda *a, **k: calls.append((a, k)))
    panel.plan_done(plan)
    args = calls[-1][0][1]
    assert args[args.index('--end-ns') + 1] == expected
    # No real acquisition or output mutation is needed to exercise the UI hand-off.
    panel.prepared({'archive': str(tmp_path / 'raw')})
    import json
    saved = json.loads((tmp_path / 'run_config.json').read_text())
    assert saved['start_ns'] == config['start_ns'] and saved['end_ns'] == expected
