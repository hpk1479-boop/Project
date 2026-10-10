"""Standalone V1 SDK. No implicit indicators, trade policy or direction."""
from dataclasses import dataclass, field
from pathlib import Path
from pit.models import AsOfToken

ROOT = Path(__file__).resolve().parents[1]
API = 'GENERIC_BACKTEST_PLUGIN_V1'
KIND = 'GENERIC_BACKTEST_V1'
TIMEFRAMES = dict(zip(('1m','2m','3m','4m','5m','6m','10m','12m','15m','20m','30m',
    '1h','2h','3h','4h','6h','8h','12h','1d'),
    (60,120,180,240,300,360,600,720,900,1200,1800,3600,7200,10800,14400,21600,28800,43200,86400)))


class GenericError(ValueError):
    def __init__(self, code, detail=''):
        self.code = code
        super().__init__(code + (': ' + str(detail) if detail else ''))


@dataclass(frozen=True)
class InstrumentSpec:
    broker_symbol: str
    server_fingerprint: str
    chart_mode: str = 'BID'
    digits: int = 2
    point: float = 0.01
    trade_tick_size: float | None = None
    description: str = ''
    metadata_revision: str = ''


@dataclass(frozen=True)
class StrategyRequirements:
    required_timeframes: tuple[str, ...] = ()
    completed_lookback_by_tf: dict = field(default_factory=dict)
    raw_tick_warmup_ns: int = 0
    features: tuple[dict, ...] = ()
    readiness_behavior: str = 'WAIT'


@dataclass(frozen=True)
class AlertIntent:
    event_key: str
    direction: str
    symbol: str
    signal_tf: str | None = None
    label: str = ''
    evidence_refs: tuple[str, ...] = ()
    diagnostics: dict = field(default_factory=dict)


@dataclass(frozen=True)
class GenericEntryIntent:
    entry_key: str
    symbol: str
    direction: str
    entry_price: float
    entry_time_ns: int
    decision_token: AsOfToken
    price_basis: str
    price_evidence: dict
    parent_alert_ids: tuple[str, ...] = ()
    entry_tf: str | None = None
    risk_anchors: dict = field(default_factory=dict)


EntryIntent = GenericEntryIntent


@dataclass(frozen=True)
class GenericRunConfig:
    mode: str
    plugin_id: str
    plugin_sha256: str
    parameters: dict
    instrument: dict
    start_ns: int
    end_ns: int
    calendar: dict
    archive: str
    runtime_approval: str = ''  # Old configs remain readable; ignored by runtime.
    plugin_approval: str = ''
    display_timezone: str = 'Asia/Seoul'
    stop_variants: tuple[dict, ...] | None = None
    target_variants: tuple[dict, ...] | None = None
    outcome_price_basis: str | None = None
    resources: dict = field(default_factory=lambda: {'max_history_bars': 100000,
        'max_occurrences': 1000000, 'worker_timeout_seconds': 30, 'max_ipc_bytes': 32*1024*1024})
    kind: str = KIND
    schema_version: int = 1
    evaluation_mode: str = 'TICK'  # Existing configs keep the original tick path.

    live_parity: dict | None = None
    session_filter: dict | None = None  # Legacy API omitted this field; UI defaults ON.
