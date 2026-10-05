"""Existing economic calendar host service, outside market strategy dispatch.
Only the host performs HTTP/time/file I/O. Notifications enter the sequencer.
"""
from __future__ import annotations
import datetime as dt
import logging,re,threading,time
from pathlib import Path
from typing import Optional
import requests
KST = dt.timezone(dt.timedelta(hours=9))

CALENDAR_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"

INDICATOR_NAMES_KO = {
    "CPI": "소비자물가지수(CPI)",
    "Core CPI": "근원 소비자물가지수(Core CPI)",
    "PPI": "생산자물가지수(PPI)",
    "Core PPI": "근원 생산자물가지수(Core PPI)",
    "PCE Price Index": "개인소비지출 물가지수(PCE)",
    "Core PCE Price Index": "근원 개인소비지출 물가지수(Core PCE)",
    "Non-Farm Employment Change": "비농업 고용(NFP)",
    "ADP Non-Farm Employment Change": "민간 비농업 고용(ADP)",
    "ADP Weekly Employment Change": "주간 민간 고용(ADP)",
    "Unemployment Rate": "실업률",
    "Average Hourly Earnings": "평균 시간당 임금",
    "Unemployment Claims": "신규 실업수당 청구건수",
    "Continuing Jobless Claims": "계속 실업수당 청구건수",
    "Employment Cost Index": "고용비용지수(ECI)",
    "JOLTS Job Openings": "구인건수(JOLTS)",
    "Federal Funds Rate": "연방기금금리",
    "FOMC Statement": "연방공개시장위원회 성명서(FOMC)",
    "FOMC Meeting Minutes": "연방공개시장위원회 회의록(FOMC)",
    "FOMC Press Conference": "연방공개시장위원회 기자회견(FOMC)",
    "GDP": "국내총생산(GDP)",
    "Advance GDP": "국내총생산(GDP) · 속보치",
    "Prelim GDP": "국내총생산(GDP) · 잠정치",
    "Final GDP": "국내총생산(GDP) · 확정치",
    "GDP Price Index": "국내총생산 물가지수(GDP)",
    "Advance GDP Price Index": "국내총생산 물가지수(GDP) · 속보치",
    "Prelim GDP Price Index": "국내총생산 물가지수(GDP) · 잠정치",
    "Final GDP Price Index": "국내총생산 물가지수(GDP) · 확정치",
    "Retail Sales": "소매판매",
    "Core Retail Sales": "근원 소매판매",
    "ISM Manufacturing PMI": "ISM 제조업 구매관리자지수(PMI)",
    "ISM Services PMI": "ISM 서비스업 구매관리자지수(PMI)",
    "ISM Manufacturing Prices": "ISM 제조업 구매물가지수",
    "Flash Manufacturing PMI": "제조업 구매관리자지수(PMI) · 속보치",
    "Flash Services PMI": "서비스업 구매관리자지수(PMI) · 속보치",
    "Final Manufacturing PMI": "제조업 구매관리자지수(PMI) · 확정치",
    "Final Services PMI": "서비스업 구매관리자지수(PMI) · 확정치",
    "CB Consumer Confidence": "컨퍼런스보드 소비자신뢰지수(CB)",
    "UoM Consumer Sentiment": "미시간대학교 소비자심리지수(UoM)",
    "Prelim UoM Consumer Sentiment": "미시간대학교 소비자심리지수(UoM) · 잠정치",
    "Revised UoM Consumer Sentiment": "미시간대학교 소비자심리지수(UoM) · 수정치",
    "UoM Inflation Expectations": "미시간대학교 기대인플레이션(UoM)",
    "Prelim UoM Inflation Expectations": "미시간대학교 기대인플레이션(UoM) · 잠정치",
    "Revised UoM Inflation Expectations": "미시간대학교 기대인플레이션(UoM) · 수정치",
    "Durable Goods Orders": "내구재 수주",
    "Core Durable Goods Orders": "근원 내구재 수주",
    "Building Permits": "건축허가 건수",
    "Housing Starts": "주택착공 건수",
    "Existing Home Sales": "기존주택 판매",
    "New Home Sales": "신규주택 판매",
    "Pending Home Sales": "미결주택 판매",
    "Personal Income": "개인소득",
    "Personal Spending": "개인소비",
    "Empire State Manufacturing Index": "뉴욕 연방은행 제조업지수",
    "Philly Fed Manufacturing Index": "필라델피아 연방은행 제조업지수",
}

_PERIOD_NAMES_KO = {"m/m": "전월 대비", "y/y": "전년 대비", "q/q": "전분기 대비"}
_SPEAKER_NAMES_KO = {
    "Powell": "파월", "Bowman": "보먼", "Waller": "월러", "Williams": "윌리엄스",
    "Jefferson": "제퍼슨", "Cook": "쿡", "Barr": "바", "Bostic": "보스틱",
    "Collins": "콜린스", "Daly": "데일리", "Kashkari": "카시카리",
    "Barkin": "바킨", "Goolsbee": "굴스비", "Schmid": "슈미드",
    "Hammack": "해맥", "Musalem": "무살렘",
}
_SPEECH_TITLE = re.compile(r"(Fed Chair|FOMC Member) (.+) (Speaks|Testifies)")

def format_indicator_title(title: str) -> str:
    """Translate calendar display names without losing the comparison period."""
    base, separator, period = title.rpartition(" ")
    if separator and period in _PERIOD_NAMES_KO and base in INDICATOR_NAMES_KO:
        return f"{INDICATOR_NAMES_KO[base]} · {_PERIOD_NAMES_KO[period]}"
    if title in INDICATOR_NAMES_KO:
        return INDICATOR_NAMES_KO[title]
    speech = _SPEECH_TITLE.fullmatch(title)
    if speech:
        role, name, action = speech.groups()
        role_ko = "연방준비제도 의장(Fed)" if role == "Fed Chair" else "연방공개시장위원회 위원(FOMC)"
        return f"{role_ko} {_SPEAKER_NAMES_KO.get(name, name)} {'연설' if action == 'Speaks' else '증언'}"
    # New/unrecognized feed titles retain their meaning until a translation is added.
    return title

def get_futures_status(day: dt.date) -> list[str]:
    notices: list[str] = []
    year, month, dom = day.year, day.month, day.day

    if month in (3, 6, 9, 12):
        first_day = dt.date(year, month, 1)
        first_friday = 1 + (4 - first_day.weekday()) % 7
        third_friday_date = dt.date(year, month, first_friday + 14)
        expiry_monday = third_friday_date - dt.timedelta(days=4)
        if expiry_monday <= day <= third_friday_date:
            if day == third_friday_date:
                notices.append("• [나스닥] 선물·옵션 만기일 입니다 (네 마녀의 날)")
            else:
                notices.append("• [나스닥] 선물·옵션 만기 주간입니다")

    if month in (2, 4, 6, 8, 10, 12) and 20 <= dom <= 27 and day.weekday() < 5:
        notices.append("• [골드] 롤오버 주간입니다")
    return notices

class EconomyWorker(threading.Thread):
    def __init__(self, config, stop_event, notifier, *, program, state_directory):
        super().__init__(name="EconomyHostInput", daemon=True)
        import shutil
        self.config=dict(config);self.stop_event=stop_event;self.notifier=notifier
        self.token=config.get('TELEGRAM_TOKEN','');self.chat_id=config.get('TELEGRAM_CHAT_ID','')
        self.fetch_interval=int(config.get('ECONOMY_FETCH_SEC','1800'))
        self.poll_interval=int(config.get('ECONOMY_POLL_SEC','30'))
        state=Path(state_directory);state.mkdir(parents=True,exist_ok=True)
        self.alerted_file=state/'economy_alerted.txt';self.briefing_file=state/'economy_briefing.txt'
        program=Path(program)
        alerted=Path(config.get('ECONOMY_ALERTED_FILE','alerted_events.txt'))
        if not alerted.is_absolute():alerted=program/alerted
        briefing=Path(config.get('ECONOMY_BRIEFING_FILE','last_briefing.txt'))
        choices=[briefing] if briefing.is_absolute() else [(program if briefing.parts[0]=='logs' else program/'logs')/briefing,program/briefing]
        for target,origins in [(self.alerted_file,[alerted]),(self.briefing_file,choices)]:
            if not target.exists():
                for origin in origins:
                    if origin.is_file():shutil.copyfile(origin,target);break
        self.next_fetch_monotonic=0.;self.calendar_status_logged=False

    def send_telegram(self, text: str) -> bool:
        return self.notifier.send(text)

    def fetch_calendar(self) -> tuple[bool, list]:
        try:
            r = requests.get(
                CALENDAR_URL,
                headers={
                    "User-Agent": "Mozilla/5.0",
                    "Accept": "application/json",
                    "Cache-Control": "no-cache",
                },
                timeout=15,
            )

            if r.status_code == 200:
                data = r.json()
                if isinstance(data, list):
                    if not self.calendar_status_logged:
                        if logging.getLogger().isEnabledFor(logging.INFO):
                            logging.info("✅ [경제 캘린더 연결 정상] 이번 주 일정 %d건 수신", len(data))
                        self.calendar_status_logged = True
                    return True, data

                logging.warning("⚠️ [경제] 캘린더 응답 형식 이상")
                return False, []

            if r.status_code == 429:
                retry_after = r.headers.get("Retry-After")
                try:
                    wait_sec = max(self.fetch_interval, int(retry_after)) if retry_after else self.fetch_interval
                except Exception:
                    wait_sec = self.fetch_interval

                self.next_fetch_monotonic = time.monotonic() + wait_sec
                logging.warning(
                    "⚠️ [경제] 캘린더 요청 제한 HTTP=429 - %d초 후 재시도",
                    wait_sec,
                )
                return False, []

            logging.warning("[경제] 캘린더 수신 실패 HTTP=%s", r.status_code)

        except Exception as e:
            logging.warning("[경제] 캘린더 수신 오류: %s", e)

        return False, []

    def load_alerted(self, day: dt.date) -> set[str]:
        try:
            lines = [x.strip() for x in self.alerted_file.read_text(encoding="utf-8").splitlines() if x.strip()]
            if lines and lines[0] == day.isoformat():
                return set(lines[1:])
        except Exception:
            pass
        return set()

    def save_alerted(self, ids: set[str], day: dt.date) -> None:
        try:
            self.alerted_file.write_text(day.isoformat() + "\n" + "\n".join(sorted(ids)) + "\n", encoding="utf-8")
        except Exception as e:
            logging.warning("[경제] alerted 저장 오류: %s", e)

    def load_last_briefing(self) -> Optional[dt.date]:
        try:
            return dt.date.fromisoformat(self.briefing_file.read_text(encoding="utf-8").strip())
        except Exception:
            return None

    def save_last_briefing(self, day: dt.date) -> None:
        try:
            self.briefing_file.write_text(day.isoformat(), encoding="utf-8")
        except Exception as e:
            logging.warning("[경제] briefing 저장 오류: %s", e)

    def build_briefing(self, day: dt.date, events: list) -> str:
        futures = get_futures_status(day)
        econ: list[str] = []
        holidays: list[str] = []

        for item in events:
            try:
                event_dt = dt.datetime.fromisoformat(item["date"]).astimezone(KST)
                if event_dt.date() != day:
                    continue
                impact = item.get("impact", "")
                country = item.get("country", "")
                title = item.get("title", "")
                if impact.lower() == "holiday" and country == "USD":
                    holidays.append(f"• 미국 증시 휴장 ({title})")
                elif country == "USD" and impact == "High":
                    econ.append(f"• {event_dt:%H:%M} · {format_indicator_title(title)}")
            except Exception:
                continue

        if not futures and not econ and not holidays:
            return "[오늘의 주요 일정 브리핑]\n\n오늘 예정된 주요 일정 및 지표 발표가 없습니다."

        sections = ["[오늘의 주요 일정 브리핑]"]
        if futures:
            sections.append("[선물 만기 및 롤오버 안내]\n" + "\n".join(futures))
        if econ:
            sections.append("[주요 경제지표 및 연설 안내]\n" + "\n".join(econ))
        if holidays:
            sections.append("[선물 시장 일정 안내]\n" + "\n".join(holidays))
        return "\n\n".join(sections)

    def run(self) -> None:
        if logging.getLogger().isEnabledFor(logging.INFO):
            logging.info("🟢 [김비서/경제] worker 시작")
        now = dt.datetime.now(KST)
        current_day = now.date()
        alerted = self.load_alerted(current_day)
        last_briefing = self.load_last_briefing()
        cached_events: list = []
        last_fetch = 0.0
        calendar_ok = False

        while not self.stop_event.is_set():
            try:
                now = dt.datetime.now(KST)
                day = now.date()

                if day != current_day:
                    alerted.clear()
                    self.save_alerted(alerted, day)
                    current_day = day

                if now.weekday() >= 5:
                    self.stop_event.wait(60)
                    continue

                mono = time.monotonic()

                # 외부 캘린더 API는 성공/실패와 관계없이 기본 30분 간격으로만 호출합니다.
                # 내부 알림 시간 비교는 아래 루프가 poll_interval(기본 30초)마다 수행합니다.
                if mono >= self.next_fetch_monotonic:
                    # 먼저 다음 호출 시각을 예약해 실패 시 재요청 폭주를 원천 차단합니다.
                    self.next_fetch_monotonic = mono + self.fetch_interval

                    ok, events = self.fetch_calendar()
                    last_fetch = mono

                    if ok:
                        cached_events = events
                        calendar_ok = True

                # API 성공 데이터가 있을 때만 아침 브리핑 발송.
                if calendar_ok and now.hour >= 8 and last_briefing != day:
                    if self.send_telegram(self.build_briefing(day, cached_events)):
                        last_briefing = day
                        self.save_last_briefing(day)

                # 같은 발표 시각의 High USD 지표는 한 메시지로 묶어서 사전 알림합니다.
                due_groups: dict[dt.datetime, list[tuple[str, str]]] = {}
                for item in cached_events:
                    try:
                        if item.get("country") != "USD" or item.get("impact") != "High":
                            continue

                        event_dt = dt.datetime.fromisoformat(item["date"]).astimezone(KST)
                        if event_dt.date() != day:
                            continue

                        event_id = f"{item.get('title')}_{item.get('date')}"
                        diff = (event_dt - now).total_seconds()
                        if not (0 < diff <= 1800) or event_id in alerted:
                            continue

                        group_time = event_dt.replace(second=0, microsecond=0)
                        due_groups.setdefault(group_time, []).append(
                            (event_id, format_indicator_title(item.get("title", "")))
                        )
                    except Exception:
                        continue

                for group_time in sorted(due_groups):
                    rows = due_groups[group_time]
                    titles: list[str] = []
                    seen_titles: set[str] = set()
                    for _, title in rows:
                        if title and title not in seen_titles:
                            titles.append(title)
                            seen_titles.add(title)

                    if not titles:
                        continue

                    text = (
                        "[주요 경제지표 및 연설 안내]\n\n"
                        f"• {group_time:%H:%M}\n"
                        + "\n".join(f"  - {title}" for title in titles)
                    )

                    if self.send_telegram(text):
                        for event_id, _ in rows:
                            alerted.add(event_id)
                        self.save_alerted(alerted, day)

            except Exception:
                logging.exception("[김비서/경제] worker 오류 - 데이터 서버는 계속 실행됩니다.")

            self.stop_event.wait(self.poll_interval)

        if logging.getLogger().isEnabledFor(logging.INFO):
            logging.info("[김비서/경제] worker 종료")

