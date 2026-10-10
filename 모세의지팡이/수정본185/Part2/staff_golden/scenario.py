"""Fixed inputs, explicit finite request domain, and synthetic capture provenance."""
import datetime as dt

from part1_host.synthetic import SyntheticMarket, TIMEFRAMES

SYMBOL = 'XAUUSD+'
START = int(dt.datetime(2026, 9, 22, tzinfo=dt.timezone.utc).timestamp())
WINDOW = 240
PATTERN = ((0, 0), (180, -0.5), (360, 3.0), (420, 3.2), (540, 1.0),
           (600, 0.8), (720, 3.1), (780, 2.5), (900, 1.0))
INDICATORS = ('EMA', 'HMA', 'PRICE', 'RSI', 'STO', 'DI', 'SUPERTREND', 'FVG')
MA_CASES = ((), ('SMA20',), ('WMA23',), ('EMA37',), ('HMA17',),
            ('SMA20', 'WMA23', 'EMA37', 'HMA17'))
WATCHES = (
    ('881001', '골드 1분 매도 올존 알려줘'),
    ('881002', '골드 1분 무지성 올존 알려줘'),
    ('881003', '골드 1분 브레이커 올존 알려줘'),
    ('881004', '골드 1분 무지성 레짐 브레이커 올존 알려줘'),
    ('881005', '골드 3분 슈퍼 레짐 올존 알려줘'),
    ('881006', '골드 2분 무지성 슈퍼 올존 알려줘'),
    ('881007', '골드 5분 FVG 터치일때 1분 매도 올존 알려줘'),
    ('881008', '골드 1분 FVG 생성 알려줘'),
    ('881009', '골드 1분 기울기(SMA17) > 0 알려줘'),
    ('881010', '골드 1분 상단 원비 터치 알려줘'),
    ('881011', '골드 1분 하단 원비 터치 알려줘'),
)


def market():
    return SyntheticMarket(SYMBOL, START, START + WINDOW, history_days=30, seed=0, pattern=PATTERN)


def indicator_combinations():
    for mask in range(1 << len(INDICATORS)):
        yield tuple(name for i, name in enumerate(INDICATORS) if mask & (1 << i))
