"""M1 history omissions block replay; generated ticks only produce warnings."""
import datetime as dt
import re

DATE=re.compile(r'\b(20\d\d)[.-](\d\d)[.-](\d\d)(?:\s+(\d\d):(\d\d)(?::(\d\d))?)?')
MISSING=re.compile(r'(?:history|data|bars?).*(?:missing|not available|unavailable|absent|failed)|no (?:history|data|bars?)|not enough (?:history|data|bars?)|이력.*(?:없|누락)',re.I)
TICKS=re.compile(r'\bticks?\b|틱',re.I)
MINUTE_MISSING=re.compile(r'(?:\bM1\b|minute bars?|1[- ]minute|1분봉)\s*:?\s*(?:(?:history|data)\s+){0,2}(?:missing|not available|unavailable|absent|no data|없|누락)|(?:missing|no|absent)\s+(?:M1|minute bars?|1[- ]minute|1분봉)',re.I)
OTHER_TF=re.compile(r',\s*(?:M(?:[2-9]|[1-9]\d)|H\d+|D1|W1|MN1)\s*[: ]',re.I)

def is_m1_omission(line):
    if MINUTE_MISSING.search(line):return True
    # "real ticks absent ... total minute bars" proves bars exist. A failed
    # tick download is not a failed M1 history download either.
    if TICKS.search(line) or OTHER_TF.search(line):return False
    return bool(MISSING.search(line))

def tick_warning(capture,mode):
    actual=capture.get('tick_evidence',{}).get('actual')
    if actual=='REAL_TICKS':return None
    label='생성 틱' if actual=='MIXED_OR_GENERATED' else '틱 방식 미확인'
    return f"{capture.get('start','')}~{capture.get('end','')}: {label} ({mode}); 1분봉 이력 누락이 아니므로 실행합니다."

def omissions(lines,start,end,symbol):
    result=[]
    for line in lines:
        if not is_m1_omission(line):continue
        # Journals may contain parallel tester agents; only this symbol matters.
        if symbol not in line:continue
        dates=[]
        for m in DATE.finditer(line):
            try:dates.append(dt.datetime(*map(int,m.groups()[:3]),*(int(v or 0) for v in m.groups()[3:]),tzinfo=dt.timezone.utc))
            except ValueError:continue
        lo=dt.datetime.fromisoformat(start).replace(tzinfo=dt.timezone.utc)
        hi=dt.datetime.fromisoformat(end).replace(tzinfo=dt.timezone.utc)
        if len(dates)>=2:
            lo=max(lo,dates[-2]);hi=min(hi,dates[-1])
        elif dates:
            day=dates[-1].replace(hour=0,minute=0,second=0)
            lo=max(lo,day);hi=min(hi,day+dt.timedelta(days=1))
        if lo<hi:result.append({'start':lo.isoformat(),'end':hi.isoformat(),'reason':line.strip()})
    return result

def require_complete(captures):
    missing=[{'capture_id':c.get('capture_id'),**gap} for c in captures for gap in c.get('history_missing',())]
    if missing:raise HistoryMissing(missing)

class HistoryMissing(RuntimeError):
    def __init__(self,gaps):self.gaps=gaps;super().__init__('브로커 1분봉 이력 누락: 백테스트 시작 중단')
