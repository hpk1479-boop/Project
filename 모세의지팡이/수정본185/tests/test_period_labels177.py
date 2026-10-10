"""수정본177: the backtest period shows its start and end days only, never labelled UTC.

The days are the recordings' (broker server) days, as the daily candles are; the user chose to show
them as plain start and end days.
"""
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / 'Part3/web'


def test_setup_date_fields_are_start_and_end_days():
    html = (WEB / 'index.html').read_text('utf-8')
    labels = dict((field, text) for text, field in re.findall(r'<label>([^<]*)<input id="mo-bt-(start|end)"', html))
    assert labels == {'start': '시작일', 'end': '종료일 (미포함)'}


def test_job_list_and_plan_period_carry_no_utc_label():
    jobs = (WEB / 'backtest_jobs.js').read_text('utf-8')
    assert "start + ' ~ ' + end + ' (종료일 미포함)'" in jobs and 'UTC (종료일' not in jobs
    editor = (WEB / 'ai_editor.js').read_text('utf-8')
    period = re.search(r"return `\$\{current\.symbol[^`]*`", editor).group(0)
    assert 'UTC' not in period and '(종료일 미포함)' in period
