"""Economic calendar display names; never send a real Telegram notification."""
from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "Part1/program"))
import event_economy_host as economy


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def deny(*args, **kwargs):
        raise AssertionError("These tests cannot contact the calendar or Telegram")
    monkeypatch.setattr(economy.requests, "get", deny)
    monkeypatch.setattr(economy.requests, "post", deny)


@pytest.mark.parametrize("source, expected", [
    ("CPI m/m", "소비자물가지수(CPI) · 전월 대비"),
    ("CPI y/y", "소비자물가지수(CPI) · 전년 대비"),
    ("Core CPI m/m", "근원 소비자물가지수(Core CPI) · 전월 대비"),
    ("Core CPI y/y", "근원 소비자물가지수(Core CPI) · 전년 대비"),
    ("PPI m/m", "생산자물가지수(PPI) · 전월 대비"),
    ("Core PPI m/m", "근원 생산자물가지수(Core PPI) · 전월 대비"),
    ("Core PCE Price Index m/m", "근원 개인소비지출 물가지수(Core PCE) · 전월 대비"),
    ("PCE Price Index y/y", "개인소비지출 물가지수(PCE) · 전년 대비"),
    ("Non-Farm Employment Change", "비농업 고용(NFP)"),
    ("ADP Non-Farm Employment Change", "민간 비농업 고용(ADP)"),
    ("Unemployment Rate", "실업률"),
    ("Unemployment Claims", "신규 실업수당 청구건수"),
    ("Average Hourly Earnings m/m", "평균 시간당 임금 · 전월 대비"),
    ("Advance GDP q/q", "국내총생산(GDP) · 속보치 · 전분기 대비"),
    ("Prelim GDP q/q", "국내총생산(GDP) · 잠정치 · 전분기 대비"),
    ("Final GDP q/q", "국내총생산(GDP) · 확정치 · 전분기 대비"),
    ("GDP y/y", "국내총생산(GDP) · 전년 대비"),
    ("Final GDP Price Index q/q", "국내총생산 물가지수(GDP) · 확정치 · 전분기 대비"),
    ("Retail Sales m/m", "소매판매 · 전월 대비"),
    ("Core Retail Sales m/m", "근원 소매판매 · 전월 대비"),
    ("Employment Cost Index q/q", "고용비용지수(ECI) · 전분기 대비"),
    ("JOLTS Job Openings", "구인건수(JOLTS)"),
    ("ISM Manufacturing PMI", "ISM 제조업 구매관리자지수(PMI)"),
    ("ISM Services PMI", "ISM 서비스업 구매관리자지수(PMI)"),
    ("Flash Manufacturing PMI", "제조업 구매관리자지수(PMI) · 속보치"),
    ("Prelim UoM Consumer Sentiment", "미시간대학교 소비자심리지수(UoM) · 잠정치"),
    ("Revised UoM Inflation Expectations", "미시간대학교 기대인플레이션(UoM) · 수정치"),
    ("Federal Funds Rate", "연방기금금리"),
    ("FOMC Statement", "연방공개시장위원회 성명서(FOMC)"),
    ("FOMC Meeting Minutes", "연방공개시장위원회 회의록(FOMC)"),
    ("FOMC Press Conference", "연방공개시장위원회 기자회견(FOMC)"),
    ("Fed Chair Powell Speaks", "연방준비제도 의장(Fed) 파월 연설"),
    ("Fed Chair Powell Testifies", "연방준비제도 의장(Fed) 파월 증언"),
    ("FOMC Member Bowman Speaks", "연방공개시장위원회 위원(FOMC) 보먼 연설"),
    ("FOMC Member Musalem Speaks", "연방공개시장위원회 위원(FOMC) 무살렘 연설"),
    ("FOMC Member Newname Speaks", "연방공개시장위원회 위원(FOMC) Newname 연설"),
    ("New Calendar Indicator y/y", "New Calendar Indicator y/y"),
    ("CPI unexpected-period", "CPI unexpected-period"),
    ("FOMC Member Bowman Speaks Extra", "FOMC Member Bowman Speaks Extra"),
    ("", ""),
])
def test_calendar_titles_keep_indicator_period_and_release_stage(source, expected):
    assert economy.format_indicator_title(source) == expected


def event(title, date="2026-10-06T08:30:00-04:00", *, country="USD", impact="High"):
    return {"title": title, "date": date, "country": country, "impact": impact}


def worker(tmp_path, stop, send):
    return economy.EconomyWorker({}, stop, SimpleNamespace(send=send),
                                 state_directory=tmp_path / "state")


def test_daily_briefing_uses_korean_names_and_keeps_country_impact_and_kst(tmp_path):
    host = worker(tmp_path, None, lambda _: True)
    text = host.build_briefing(dt.date(2026, 10, 6), [
        event("CPI m/m"), event("CPI y/y"),
        event("Fed Chair Powell Speaks", "2026-10-06T09:00:00-04:00"),
        event("Not high", impact="Medium"), event("Not US", country="EUR"),
        event("Wrong date", "2026-10-07T08:30:00-04:00"),
        event("New Calendar Indicator y/y"),
    ])
    assert "• 21:30 · 소비자물가지수(CPI) · 전월 대비" in text
    assert "• 21:30 · 소비자물가지수(CPI) · 전년 대비" in text
    assert "• 22:00 · 연방준비제도 의장(Fed) 파월 연설" in text
    assert "New Calendar Indicator y/y" in text
    assert all(excluded not in text for excluded in ("Not high", "Not US", "Wrong date"))


class OnePollStop:
    def __init__(self):
        self.stopped = False

    def is_set(self):
        return self.stopped

    def wait(self, seconds):
        self.stopped = True


@pytest.mark.parametrize("delivered", [True, False])
def test_worker_prealert_keeps_raw_ids_delivery_acknowledgment_and_time_window(tmp_path, monkeypatch, delivered):
    class FixedDateTime(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 10, 6, 21, 5, tzinfo=economy.KST)

    monkeypatch.setattr(economy, "dt", SimpleNamespace(datetime=FixedDateTime, date=dt.date))
    messages = []
    stop = OnePollStop()
    def send(text):
        messages.append(text)
        return delivered
    host = worker(tmp_path, stop, send)
    day = dt.date(2026, 10, 6)
    host.save_last_briefing(day)
    already = event("Non-Farm Employment Change")
    original_id = f"{already['title']}_{already['date']}"
    host.save_alerted({original_id}, day)
    events = [event("CPI m/m"), event("CPI y/y"), event("CPI m/m"), already,
              event("Not high", impact="Medium"), event("Not US", country="EUR"),
              event("Too early", "2026-10-06T08:00:00-04:00"),
              event("Too late", "2026-10-06T09:00:00-04:00")]
    host.fetch_calendar = lambda: (True, events)
    host.run()
    assert messages == ["[주요 경제지표 및 연설 안내]\n\n• 21:30\n"
                        "  - 소비자물가지수(CPI) · 전월 대비\n"
                        "  - 소비자물가지수(CPI) · 전년 대비"]
    expected_ids = {original_id}
    if delivered:
        expected_ids.update(f"{item['title']}_{item['date']}" for item in events[:2])
    assert host.load_alerted(day) == expected_ids
    assert host.load_last_briefing() == day


def test_empty_calendar_briefing_stays_readable(tmp_path):
    host = worker(tmp_path, None, lambda _: True)
    assert host.build_briefing(dt.date(2026, 10, 6), []) == (
        "[오늘의 주요 일정 브리핑]\n\n오늘 예정된 주요 일정 및 지표 발표가 없습니다.")
