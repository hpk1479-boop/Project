"""Standalone V1 SDK. No implicit indicators, trade policy or direction."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TIMEFRAMES = dict(zip(('1m','2m','3m','4m','5m','6m','10m','12m','15m','20m','30m',
    '1h','2h','3h','4h','6h','8h','12h','1d'),
    (60,120,180,240,300,360,600,720,900,1200,1800,3600,7200,10800,14400,21600,28800,43200,86400)))


class GenericError(ValueError):
    def __init__(self, code, detail=''):
        self.code = code
        super().__init__(code + (': ' + str(detail) if detail else ''))


