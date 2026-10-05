"""Calendar shortcuts must keep a valid expiry date at month-end boundaries."""
from datetime import date
import pytest
from releasekit import ui


@pytest.mark.parametrize('today,months,expected', [
    (date(2026, 12, 31), 6, '2027-06-30'),
    (date(2028, 2, 29), 12, '2029-02-28'),
])
def test_expiry_shortcut_clamps_to_last_valid_day(monkeypatch, today, months, expected):
    class Calendar(date):
        @classmethod
        def today(cls):
            return today
    monkeypatch.setattr(ui, 'date', Calendar)
    assert ui.months_later(months) == expected
