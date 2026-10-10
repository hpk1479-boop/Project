"""Exercise the four calendar presets and the real, hidden release window."""
from datetime import date
import tkinter as tk
from tkinter import ttk

import pytest

from releasekit import ui


def freeze_today(monkeypatch, today):
    class Calendar(date):
        @classmethod
        def today(cls):
            return today

    monkeypatch.setattr(ui, 'date', Calendar)


@pytest.mark.parametrize('today,months,expected', [
    (date(2026, 1, 31), 1, '2026-02-28'),
    (date(2028, 1, 31), 1, '2028-02-29'),
    (date(2026, 11, 30), 3, '2027-02-28'),
    (date(2027, 11, 30), 3, '2028-02-29'),
    (date(2026, 8, 31), 6, '2027-02-28'),
    (date(2027, 8, 31), 6, '2028-02-29'),
    (date(2028, 2, 29), 12, '2029-02-28'),
    (date(2026, 12, 31), 1, '2027-01-31'),
    (date(2026, 12, 31), 3, '2027-03-31'),
    (date(2026, 12, 31), 6, '2027-06-30'),
    (date(2026, 12, 31), 12, '2027-12-31'),
])
def test_calendar_presets_keep_valid_dates(monkeypatch, today, months, expected):
    freeze_today(monkeypatch, today)
    assert ui.months_later(months) == expected


def test_native_release_window_preset_commands_and_names(monkeypatch):
    """Click the actual Tk controls without starting a build or showing a window."""
    freeze_today(monkeypatch, date(2026, 11, 30))
    native_tk = tk.Tk
    windows = []
    checked = []

    def children(widget):
        for child in widget.winfo_children():
            yield child
            yield from children(child)

    def create_hidden_window():
        try:
            window = native_tk()
        except tk.TclError as error:
            pytest.skip(f'Tk window unavailable: {error}')
        window.withdraw()
        windows.append(window)

        def inspect_controls():
            assert window.title() == '모세의지팡이 배포'
            buttons = [item for item in children(window) if isinstance(item, ttk.Button)]
            assert [item.cget('text') for item in buttons] == [
                '1개월', '3개월', '6개월', '12개월', '모세의지팡이 통합설치 만들기',
            ]
            fields = [item for item in children(window) if isinstance(item, ttk.Entry)]
            version, expiry, minutes = fields
            assert version.get() == 'v1.0'
            assert minutes.get() == '10'
            assert expiry.get() == '2027-11-30'
            for button, expected in zip(buttons[:4], [
                '2026-12-30', '2027-02-28', '2027-05-30', '2027-11-30',
            ]):
                button.invoke()
                assert expiry.get() == expected
                checked.append(button.cget('text'))
            window.update_idletasks()
            shortcuts = buttons[:4]
            assert all(item.winfo_reqwidth() > 0 for item in shortcuts)
            assert sum(item.winfo_reqwidth() + 5 for item in shortcuts) <= 350

        window.mainloop = inspect_controls
        return window

    monkeypatch.setattr(ui.tk, 'Tk', create_hidden_window)
    monkeypatch.delenv('MOSES_RELEASE_LAUNCH_TOKEN', raising=False)
    try:
        ui.main()
    finally:
        for window in windows:
            window.destroy()
    assert checked == ['1개월', '3개월', '6개월', '12개월']
